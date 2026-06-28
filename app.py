"""
app.py -- local web server for the CS2 demo player.

  GET  /                -> the player UI
  GET  /api/sample      -> sample demo, validated vs SCHEMA/ANALYTICS_VERSION
                           (regenerates a current-schema mock if stale/missing)
  POST /api/upload      -> upload a .dem, parse it, return normalized JSON
                           (results cached by content hash in cache/, with a
                            small <key>.meta.json sidecar for cheap version checks)

Run:  python app.py    (or use start.bat)  ->  http://127.0.0.1:8770
"""
import collections
import datetime
import gzip
import hashlib
import json
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
import traceback
import urllib.parse

from flask import Flask, Response, jsonify, redirect, request, send_from_directory, session

import callouts                                         # callout seed loader + geometry (stdlib only)
import callout_store                                    # effective callouts: seed+overrides+learned (stdlib only)
import goals                                            # persistent match-aware Practice Goals (stdlib only)
import library                                          # saved-demo library (stdlib only)
import mapstatus                                        # 3D-asset status (stdlib only)
import matchindex                                       # cross-match trends (stdlib only)
import db                                               # SQLite metadata index (stdlib only)
import jobs                                             # background parse-job queue (stdlib only)
import legal                                            # Terms/Privacy/Cookie/Refund pages (stdlib only)
import nades                                            # local nade-lineup library (stdlib only)
import appconfig                                        # admin-editable site settings (Free upload cap)
import billing                                          # Stripe subscription billing (opt-in via env)
import practiceplan                                     # practice-plan done-state (stdlib only)
import pricing                                           # editable Pro subscription prices (stdlib only)
import reviews                                          # review bookmarks + auto-queues (stdlib only)
import samplemgr                                        # admin-managed replacement of the bundled sample demo
import statsfile                                        # compact .txt stats retained when a replay is deleted
import steamauth                                        # Steam OpenID 2.0 login (stdlib only, optional)
import teams                                            # local team config (stdlib only)
import tendencies                                       # cross-match tendency detection (stdlib only)
import playbook                                         # team playbook + adherence (stdlib only)
import nadeclusters                                     # auto-detect consistent utility (stdlib only)
import coaching_summary                                 # heuristic NL coaching summary (+ optional env-gated AI)
import compare                                          # player-vs-player comparison (presentation over trends)
import export_report                                    # rich match-report export: text / JSON / printable HTML
import analytics_migrations                             # in-place, .dem-free upgrade of stale cached analytics
import sidestats                                        # own/team CT-T round winrate by map (from cached analytics)
import benchmarks                                       # source-agnostic skill-bucket benchmark layer (no fake data)
from schema import ANALYTICS_VERSION, SCHEMA_VERSION   # dep-free; safe at import time


def _load_dotenv():
    """Load KEY=VALUE lines from a local .env into os.environ (stdlib only -- no python-dotenv).
    Real environment variables always win, so Docker/`set X=...` overrides .env. Runs at import so
    auth/config below see it. Skipped under pytest so tests stay hermetic."""
    import sys
    if "pytest" in sys.modules:
        return
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except FileNotFoundError:
        pass
    except Exception as e:                                # never let a bad .env stop startup
        print(f"[.env] could not load .env: {e}")


_load_dotenv()

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
# data dirs are env-overridable so a hosted/Docker deploy can mount them as volumes
CACHE = os.environ.get("CACHE_DIR") or os.path.join(HERE, "cache")
UPLOADS = os.environ.get("UPLOAD_DIR") or os.path.join(HERE, "uploads")
IMPORT_DIR = os.environ.get("IMPORT_DIR") or os.path.join(HERE, "incoming")   # #69 drop .dem files here
# Compact retained stats (.txt per deleted match) live on the data volume next to the DB so they persist.
STATS = os.environ.get("STATS_DIR") or os.path.join(os.path.dirname(db.DB_PATH) or HERE, "stats")
os.makedirs(CACHE, exist_ok=True)
os.makedirs(UPLOADS, exist_ok=True)
# KEEP_DEM=0 (DEFAULT) discards the raw .dem after parsing -- the parsed cache JSON is all the app needs
# to replay/analyze, so we don't hoard ~hundreds of MB per demo on a shared host (the orphaned-.dem
# issue). Trade-off: a parser/schema upgrade can't re-process old demos from disk; users re-upload to
# refresh. Set KEEP_DEM=1 only if you explicitly want raw demos retained for re-parsing.
KEEP_DEM = os.environ.get("KEEP_DEM", "0").strip().lower() not in ("0", "false", "no", "off")
# Persistent data volume (mirrors db.py): the admin-managed sample + its metadata + retained raw .dem
# live under DATA_DIR/sample so they survive across deploys (cache/ and data/ are mounted volumes).
DATA_DIR = os.environ.get("DATA_DIR") or HERE
db.migrate()                                            # ensure the SQLite index schema exists


def clean_nan(o):
    """Replace NaN/Inf with None so the output is always valid JSON."""
    import math
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: clean_nan(v) for k, v in o.items()}
    if isinstance(o, list):
        return [clean_nan(v) for v in o]
    return o

app = Flask(__name__, static_folder=STATIC, static_url_path="/static")
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "2048"))     # demos are big (default 2 GB)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0  # don't let the browser cache JS/CSS/data
# separate, smaller cap for lineup video clips (a clip should be a few seconds, not a movie)
MAX_VIDEO_UPLOAD_MB = int(os.environ.get("MAX_VIDEO_UPLOAD_MB", "100"))


# ---- auth / sessions (Stage 4) ----------------------------------------------
# Steam OpenID login is OPT-IN: set PUBLIC_BASE_URL (and optionally AUTH_REQUIRED) to enable it.
# A pure-local install leaves both unset and runs as a single 'local' user -- no login wall, no
# behavior change. Login itself needs only PUBLIC_BASE_URL + SECRET_KEY; STEAM_API_KEY is optional
# (display name/avatar only). See DEPLOY.md / .env.example.
def _truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on") if v is not None else False


app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",        # Lax lets the post-login redirect carry the session cookie
    SESSION_COOKIE_SECURE=_truthy(os.environ.get("SESSION_COOKIE_SECURE")),  # set true behind HTTPS
)
if steamauth.auth_enabled() and not os.environ.get("SECRET_KEY"):
    print("  [auth] WARNING: SECRET_KEY not set -- using an ephemeral key (logins reset on restart "
          "and won't work across processes). Set SECRET_KEY for production.")


def current_user():
    """The logged-in user dict, or a synthetic 'local' user ONLY in pure-local mode. Returns None for
    an unauthenticated visitor on ANY auth-enabled deployment.

    SECURITY: the fallback is gated on auth_enabled() (AUTH_REQUIRED *or* PUBLIC_BASE_URL set), NOT
    auth_required() (AUTH_REQUIRED only). A site with PUBLIC_BASE_URL set but AUTH_REQUIRED unset is a
    real public deployment -- handing anonymous visitors the synthetic 'local' user there gave them
    is_admin=True + tier=pro and exposed /api/admin/* and private dashboards to the internet. Pure-local
    mode (neither env set) is unchanged: a single 'local' owner that is admin/Pro and sees everything.
    Returning None here is what makes _scope_or_block()/require_auth_when_locked()/is_admin()/tier_of()
    fail closed for anonymous users (they already treat None as 'no user')."""
    uid = session.get("uid")
    if uid is not None:
        u = db.get_user(uid)
        if u:
            return u
        session.pop("uid", None)                       # stale session: user row no longer exists
    if steamauth.auth_enabled():
        return None                                    # auth-enabled + not logged in -> anonymous
    return {"id": None, "name": "Local", "local": True}   # pure-local install: single owner = admin/Pro


def anon_user():
    """A read-only, no-privilege user struct for an unauthenticated visitor on an auth-enabled site.
    Never 'local' (so is_admin/tier_of stay False/free) and has no id (so it owns/sees nothing). Used
    by /api/me to give the frontend an explicit anonymous identity instead of a bare null."""
    return {"id": None, "name": None, "local": False, "anonymous": True}


def current_user_id():
    u = current_user()
    return u["id"] if u else None


# ---- admin + subscription tiers --------------------------------------------
# ADMIN_STEAM_IDS = comma-separated SteamID64s with admin rights. A pure-local install treats its
# single user as admin (their own machine). TIERS_ENABLED gates Pro-only features; while it's off
# (default) everyone gets full access, so turning it on later is a flip -- nothing changes today.
TIERS_ENABLED = _truthy(os.environ.get("TIERS_ENABLED"))
# What $5 Pro unlocks. Free keeps 2D replay + BASIC analytics (K/D, ADR, KAST, openings, trades,
# util counts, dmg/round, side splits) -- "Free tells you what happened; Pro tells you why + how to
# fix it." Pro = 3D, utility/nade tools, advanced analytics (trends/mistakes/role/spacing/swing),
# practice goals, and team workspaces/playbook.
PRO_FEATURES = ("threeD", "utility", "advancedAnalytics", "goals", "teams")


def _admin_ids():
    return {s.strip() for s in (os.environ.get("ADMIN_STEAM_IDS", "")).split(",") if s.strip()}


def is_admin(user):
    if not user:
        return False
    if user.get("local"):
        return True                                    # single-user local install = full control
    return bool(user.get("steam_id_64") and user["steam_id_64"] in _admin_ids())


def is_helper(user):
    """Helpers can view the admin panel and grant/revoke Pro, but NOT delete users or assign roles
    (those stay admin-only). Every admin is implicitly a helper."""
    if not user:
        return False
    return is_admin(user) or (user.get("role") == "helper")


def _pro_expired(user):
    """True if this user has a Pro expiry that's now in the past. No expiry set -> never expired."""
    pu = user.get("pro_until")
    return bool(pu) and pu < datetime.datetime.now().isoformat(timespec="seconds")


def tier_of(user):
    if not user:
        return "free"
    if user.get("local") or is_admin(user):
        return "pro"                                   # local owner + admins always get full access
    if user.get("tier") == "pro" and not _pro_expired(user):
        return "pro"
    return "free"                                      # never-Pro or lapsed subscription


def _add_months(dt, months):
    """Add whole calendar months to a datetime, clamping the day (Jan 31 + 1mo -> Feb 28/29)."""
    import calendar
    m = dt.month - 1 + int(months)
    y = dt.year + m // 12
    m = m % 12 + 1
    return dt.replace(year=y, month=m, day=min(dt.day, calendar.monthrange(y, m)[1]))


# Pro durations the admin/helper can grant. 0 == indefinite (no expiry).
PRO_DURATIONS = {1, 3, 6, 12}


def entitlements(user):
    """Which Pro features this user may use. Tiers OFF -> everyone gets everything (no gating yet)."""
    unlocked = (not TIERS_ENABLED) or (tier_of(user) == "pro")
    return {f: unlocked for f in PRO_FEATURES}


def require_feature(feat):
    """Server-side Pro gate (defense in depth: the frontend already hides Pro tools, this stops a
    direct API call from a free account). Returns None when allowed (tiers off / local / admin / Pro),
    else a (402 JSON, status) tuple to return. Login is gated separately by _scope_or_block."""
    if not TIERS_ENABLED:
        return None
    if entitlements(current_user()).get(feat):
        return None
    return _nostore({"error": "Pro feature", "feature": feat, "upgrade": True}), 402


FREE_UPLOAD_LIMIT = int(os.environ.get("FREE_UPLOAD_LIMIT", "10"))   # #22: Free plan stores 10 demos
# abuse caps: most in-flight jobs one user may queue, and the free-disk floor below which uploads are
# refused (leave headroom for the upload + decompress temp + parsed cache).
MAX_ACTIVE_JOBS = int(os.environ.get("MAX_ACTIVE_JOBS_PER_USER", "10") or 10)
MIN_FREE_DISK_BYTES = int(os.environ.get("MIN_FREE_DISK_GB", "2") or 2) * (1 << 30)


def upload_allowance(user):
    """How many demos this user may store. unlimited for Pro/admin/local or when tiers are off;
    otherwise Free is capped at FREE_UPLOAD_LIMIT owned demos."""
    if (not TIERS_ENABLED) or tier_of(user) == "pro":
        return {"unlimited": True, "used": None, "limit": None}
    uid = user.get("id") if user else None
    used = db.user_demo_count(uid) if uid else 0
    limit = appconfig.free_upload_limit()                # admin-settable at runtime (#22)
    return {"unlimited": False, "used": used, "limit": limit,
            "remaining": max(0, limit - used)}


def _dir_bytes(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _scope_or_block():
    """Resolve data visibility for this request (Stage 5 isolation):
      ('open',    None)             -- local/unenforced mode: see everything (current behavior)
      ('scoped',  {uid,team_ids})   -- a user is logged in: own + team-shared + ownerless demos
      ('blocked', None)             -- auth is REQUIRED but nobody is logged in
    Endpoints pass the returned scope (None for 'open') straight into the db.* read helpers."""
    u = current_user()
    if u is None:
        return "blocked", None                         # AUTH_REQUIRED + anonymous
    if u.get("id") is None:
        return "open", None                            # synthetic local user
    # ownerless (legacy/pre-auth) demos are shared only in local mode; on a locked-down site
    # (AUTH_REQUIRED) they're hidden until claimed, so unclaimed demos never leak between users.
    return "scoped", {"uid": u["id"], "team_ids": db.team_ids_for_user(u["id"]),
                      "ownerless": not steamauth.auth_required()}


def json_file_response(path):
    """Serve a JSON file as a normal (gzip-able) response, not a passthrough stream."""
    with open(path, "rb") as f:
        resp = Response(f.read(), mimetype="application/json")
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ---- cache validation (real JSON + schema/analytics versioning) -------------
def _sha1_file(path):
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_cache(path):
    """Load a cached demo JSON; return the dict, or None if missing/corrupt."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def replay_valid(data):
    return bool(data) and data.get("version") == SCHEMA_VERSION and "frames" in data


def has_duck(data):
    """True if the cached replay carries per-frame crouch data. Parses from before `duck_amount`
    was added lack the 'duck' key entirely (the key is all-or-nothing per parse), so re-uploading
    such a demo should force a full re-parse to pick up first-person crouch. Cheap: checks the first
    player dict it finds."""
    for f in (data.get("frames") or []) if isinstance(data, dict) else []:
        for pl in (f.get("players") or []):
            if isinstance(pl, dict):
                return "duck" in pl
    return True   # no players at all -> nothing to gain from a re-parse


def analytics_valid(data):
    a = data.get("analytics") if data else None
    return isinstance(a, dict) and a.get("version") == ANALYTICS_VERSION


def atomic_write_json(path, data):
    """Write JSON to a temp file in the same dir, then atomically replace -- no partial caches."""
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(data, out)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


# ---- lightweight cache sidecar metadata -------------------------------------
# A tiny <key>.meta.json next to each full cache file. Cheap to read for listings,
# version checks, and a future job queue / admin page -- no need to parse the huge blob.
def meta_path_for(cache_path):
    base = cache_path[:-5] if cache_path.endswith(".json") else cache_path
    return base + ".meta.json"


def build_meta(data, source_sha1=None, status="ok"):
    a = data.get("analytics") if isinstance(data, dict) else None
    av = a.get("version") if isinstance(a, dict) else None
    return {
        "source_sha1": source_sha1 or (data.get("source_sha1") if isinstance(data, dict) else None),
        "map": data.get("map") if isinstance(data, dict) else None,
        "schema_version": data.get("version") if isinstance(data, dict) else None,
        "analytics_version": av,
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "duration": data.get("duration") if isinstance(data, dict) else None,
        "rounds": len(data.get("rounds") or []) if isinstance(data, dict) else 0,
        "players": len(data.get("players") or []) if isinstance(data, dict) else 0,
        "frames": len(data.get("frames") or []) if isinstance(data, dict) else 0,
        "has_analytics": isinstance(a, dict),
        "mock": bool(data.get("mock")) if isinstance(data, dict) else False,
        "parse_status": status,
    }


def write_meta(cache_path, data, source_sha1=None, status="ok"):
    """Write the sidecar; advisory only -- never fail a request because metadata didn't save."""
    try:
        atomic_write_json(meta_path_for(cache_path), build_meta(data, source_sha1, status))
    except Exception:
        pass


@app.after_request
def gzip_response(resp):
    """Transparently gzip large JSON/text responses (positions compress ~6x)."""
    try:
        if resp.direct_passthrough or "gzip" not in request.headers.get("Accept-Encoding", ""):
            return resp
        ct = resp.content_type or ""
        if not (ct.startswith("application/json") or ct.startswith("text/")):
            return resp
        data = resp.get_data()
        if len(data) < 1024:
            return resp
        resp.set_data(gzip.compress(data, 5))
        resp.headers["Content-Encoding"] = "gzip"
        resp.headers["Vary"] = "Accept-Encoding"
        resp.headers["Content-Length"] = str(len(resp.get_data()))
    except Exception:
        pass
    return resp


# ---- lightweight per-IP rate limiting (stdlib, in-process sliding window) ----------------------
_RATE_LIMITS = {                       # path -> (max requests, window seconds) per client IP
    "/api/upload": (20, 60),
    "/login/steam": (30, 60),
    "/auth/steam/callback": (30, 60),
    "/api/sample": (30, 60),
}
_rl_hits = collections.defaultdict(collections.deque)
_rl_lock = threading.Lock()


def _client_ip():
    """Real client IP behind Caddy (X-Forwarded-For); falls back to the socket peer. Without this all
    traffic buckets under the proxy's 127.0.0.1."""
    xff = request.headers.get("X-Forwarded-For", "")
    return xff.split(",")[0].strip() if xff else (request.remote_addr or "?")


def _rate_ok(key, limit, window):
    now = time.monotonic()
    with _rl_lock:
        dq = _rl_hits[key]
        while dq and dq[0] <= now - window:
            dq.popleft()
        if len(dq) >= limit:
            return False
        dq.append(now)
        return True


@app.before_request
def rate_limit():
    """Throttle the abusable/expensive endpoints per client IP. In-process (per worker) -- a first
    line, not a substitute for edge protection; thresholds are generous so a real team isn't hit."""
    rule = _RATE_LIMITS.get(request.path)
    if rule and not _rate_ok("%s|%s" % (request.path, _client_ip()), rule[0], rule[1]):
        return _nostore({"error": "Too many requests -- slow down and try again shortly."}), 429
    return None


@app.before_request
def csrf_origin_guard():
    """CSRF defense: a logged-in (cookie-session) state-changing request must carry an Origin/Referer
    matching our own site. Requests with NO authenticated session, and local/open mode (PUBLIC_BASE_URL
    unset), are unaffected. The Stripe webhook is exempt (signature-authed, server-to-server, no Origin/
    cookie); Steam login/callback are GET so naturally exempt. Same-origin fetch() sends Origin
    automatically, so the frontend needs no change."""
    if request.method not in ("POST", "PUT", "DELETE", "PATCH"):
        return None
    if request.path == "/api/stripe/webhook":
        return None
    if not session.get("uid"):                 # no authenticated session -> nothing to forge against
        return None
    base = steamauth.public_base_url()
    if not base:                               # local/open mode -> not enforced
        return None
    src = request.headers.get("Origin") or request.headers.get("Referer") or ""
    if not src or urllib.parse.urlparse(src).netloc != urllib.parse.urlparse(base).netloc:
        return _nostore({"error": "cross-site request blocked"}), 403
    return None


@app.before_request
def feature_and_store_gates():
    """Defense-in-depth for things the frontend already gates (a direct API/asset call must not bypass):
      - 3D map geometry (/static/maps3d/*.glb) is a Pro (threeD) asset -> 402 for free/anon when tiers
        are on, except for the current public sample's own map mesh.
      - the shared nade/playbook/team/practice JSON stores are writable only by a logged-in user on a
        locked site, so an anonymous request can't clobber them.
    All no-ops in local/open mode (tiers off, not auth-locked), so dev/preview behavior is unchanged."""
    p = request.path
    if p.startswith("/static/maps3d/") and p.endswith(".glb"):
        if _is_current_sample_glb_request(p):
            return None
        return require_feature("threeD")
    if request.method in ("POST", "PUT", "DELETE", "PATCH") and (
            p.startswith("/api/nades") or p == "/api/playbook" or p.startswith("/api/playbook/")
            or p == "/api/team" or p == "/api/practice"):
        return require_auth_when_locked()
    return None


def _effective_sample_data():
    if samplemgr.has_valid_admin_sample(DATA_DIR, replay_valid, analytics_valid):
        return load_cache(samplemgr.current_json_path(DATA_DIR))
    return load_cache(os.path.join(CACHE, "sample.json"))


def _current_sample_glb_name():
    data = _effective_sample_data()
    map_name = data.get("map") if isinstance(data, dict) else None
    if not map_name:
        return None
    transforms = load_cache(os.path.join(STATIC, "maps3d", "transforms.json")) or {}
    cfg = transforms.get(map_name) if isinstance(transforms, dict) else None
    if not isinstance(cfg, dict) or not cfg.get("verified"):
        return None
    return os.path.basename(cfg.get("glb") or (map_name + "_full.glb"))


def _is_current_sample_glb_request(path):
    """Allow the public sample to preview exactly its own 3D mesh without opening all 3D assets."""
    if request.args.get("sample") != "1":
        return False
    wanted = _current_sample_glb_name()
    return bool(wanted) and os.path.basename(path) == wanted


@app.after_request
def security_headers(resp):
    """Baseline security headers (safe phase: no CSP yet -- strict CSP needs the inline handlers +
    importmap migrated first). HSTS is set at Caddy where TLS terminates, not here."""
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("X-Frame-Options", "DENY")        # we embed others (YouTube); nobody frames us
    resp.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    return resp


@app.route("/")
def index():
    """Serve the SPA, choosing the first-paint view server-side (no flash): a logged-out visitor on an
    auth-enabled site lands on the public marketing page; everyone else lands on the dashboard."""
    try:
        with open(os.path.join(STATIC, "index.html"), "r", encoding="utf-8") as f:
            html = f.read()
    except OSError:
        return send_from_directory(STATIC, "index.html")
    u = current_user()
    show_landing = steamauth.auth_enabled() and not (u and u.get("id"))
    html = html.replace('<body class="on-dashboard">',
                        '<body class="{}">'.format("on-landing" if show_landing else "on-dashboard"), 1)
    resp = Response(html, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/<any(terms, privacy, cookies, refunds):slug>")
def legal_page(slug):
    """Public, logged-out-accessible policy pages (Terms/Privacy/Cookies/Refunds)."""
    page = legal.render(slug)
    if page is None:
        return jsonify({"error": "not found"}), 404
    resp = Response(page, mimetype="text/html")
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@app.route("/api/sample")
def sample():
    """Serve the sample demo, validating it against the current schema/analytics versions.

    - replay missing or schema-stale  -> regenerate a fresh mock at the current SCHEMA_VERSION
    - replay current but analytics stale (and no .dem to recompute from) -> drop the stale
      analytics so the UI shows the honest empty state instead of wrong numbers

    An admin-managed sample (DATA_DIR/sample/current.json) takes precedence WHEN it is present AND
    valid (replay + analytics). Otherwise we fall back to the bundled sample exactly as before.
    """
    if samplemgr.has_valid_admin_sample(DATA_DIR, replay_valid, analytics_valid):
        return json_file_response(samplemgr.current_json_path(DATA_DIR))
    path = os.path.join(CACHE, "sample.json")
    data = load_cache(path)
    if not replay_valid(data):
        try:
            import mockgen
            mockgen.build()
        except Exception as e:
            return jsonify({"error": f"could not generate sample: {e}"}), 500
        data = load_cache(path)
        write_meta(path, data, status="regenerated-mock")
    elif not analytics_valid(data) and data.get("analytics") is not None:
        analytics_migrations.migrate(data)            # try a .dem-free in-place upgrade first
        if analytics_valid(data):
            atomic_write_json(path, data)
            write_meta(path, data, status="analytics-migrated")
        else:                                          # truly unmigratable -> drop wrong numbers
            print("[sample] analytics version stale and unmigratable -> dropping it")
            data["analytics"] = None
            atomic_write_json(path, data)
            write_meta(path, data, status="analytics-dropped")
    elif not os.path.exists(meta_path_for(path)) and data is not None:
        write_meta(path, data)   # backfill sidecar for an already-valid sample
    if not os.path.exists(path):
        return jsonify({"error": "sample unavailable"}), 500
    return json_file_response(path)


def _parse_or_load_dem(tmp, original_filename, progress=None):
    """Turn a .dem at `tmp` into the normalized + analytics-tagged data dict.

    Reuses the existing content-hash cache (cache/<key>.json + <key>.meta.json
    and the persisted uploads/<key>.dem) so re-uploading a demo we've already
    parsed is cheap. Mirrors the original single-file upload flow:
      1) fully-valid cache -> load it as-is
      2) replay fine but analytics stale -> recompute analytics only (no re-parse)
      3) otherwise -> full parse (+ analytics, which is allowed to fail)
    `data["source_sha1"]` is always set to the full content digest on return.
    Raises on a genuinely unparseable demo (caller records a per-file error).
    """
    key_full = _sha1_file(tmp)
    key = key_full[:16]                              # short key = filename (keeps existing caches)
    cached = os.path.join(CACHE, f"{key}.json")
    dem_path = os.path.join(UPLOADS, f"{key}.dem")

    data = load_cache(cached)
    # 1) fully valid cache (incl. crouch data) -> reuse as-is. Re-uploading a demo cached before
    #    duck_amount existed re-parses it so first-person crouch works (otherwise we'd serve stale).
    if replay_valid(data) and analytics_valid(data) and has_duck(data):
        if KEEP_DEM and not os.path.exists(dem_path):
            try:
                os.replace(tmp, dem_path)            # keep the .dem for future re-runs
            except OSError:
                pass
        if not os.path.exists(meta_path_for(cached)):
            write_meta(cached, data, key_full)       # backfill sidecar for old caches
        print(f"[upload] cache hit {key}")
        data.setdefault("source_sha1", key_full)
        return data

    # We need to (re)parse. Parse from an existing .dem if present, else the upload.
    src = dem_path if os.path.exists(dem_path) else tmp
    import parser as demo_parser
    import analytics as an
    from demoparser2 import DemoParser
    pr = DemoParser(src)

    if replay_valid(data) and has_duck(data):
        # 2) replay schema fine (and has crouch data) but analytics stale/missing -> recompute analytics ONLY
        print(f"[upload] analytics v-mismatch for {key} -> recomputing analytics (no re-parse)")
        if progress:
            progress("analyzing")
        data["analytics"] = an.analyze(pr, replay=data)
    else:
        # 3) full parse
        if data is not None:
            print(f"[upload] stale replay cache {key} -> full re-parse")
        print(f"[upload] parsing {original_filename} ({os.path.getsize(src) // (1 << 20)} MB)...")
        if progress:
            progress("parsing")
        data = demo_parser.parse_demo(pr)
        try:
            print("[upload] computing analytics...")
            if progress:
                progress("analyzing")
            data["analytics"] = an.analyze(pr, replay=data)
        except Exception as ae:
            print(f"[upload] analytics failed: {ae}")
            traceback.print_exc()
            data["analytics"] = None

    data["source_sha1"] = key_full                   # full digest in metadata (key is truncated)
    data = clean_nan(data)
    atomic_write_json(cached, data)
    write_meta(cached, data, key_full,
               status="ok" if data.get("analytics") else "replay-only")
    if KEEP_DEM and src is tmp and not os.path.exists(dem_path):
        try:
            os.replace(tmp, dem_path)                # persist the .dem on first successful parse
        except OSError:
            pass
    # KEEP_DEM=0: never hoard raw demos. The temp is cleaned by the caller's finally; if an older
    # run persisted a copy, drop it (it's not src here -- src is the temp -- so no open-handle clash).
    if not KEEP_DEM and src is tmp and os.path.exists(dem_path):
        try:
            os.remove(dem_path)
        except OSError:
            pass
    return data


def _save_to_library(name, data, owner_user_id=None, team_id=None):
    """Save a parsed demo to the library; return its frontend result row.
    `owner_user_id` stamps the uploader in the index (NULL in local mode); `team_id` is the chosen
    upload destination for that uploader's library copy (NULL = personal)."""
    demo_id = library.demo_id_for(data)
    library.upsert(CACHE, demo_id, name, data, atomic_write_json)
    try:
        db.index_demo(data, str(demo_id)[:16], owner_user_id=owner_user_id, team_id=team_id)   # keep the fast index current
    except Exception as e:
        print(f"[index] index_demo failed for {demo_id}: {e}")
    return {"id": demo_id, "name": name, "map": data.get("map"),
            "score": library.final_score(data), "ok": True}


def _wipe_orphaned(shas):
    """Delete the shared parse + cache + raw .dem for any sha that now has NO library members left
    (refcount). Used after removing memberships (demo/account/admin delete) so co-owned matches
    survive until the LAST member is gone. Returns the count wiped."""
    wiped = 0
    for sha in set(shas or []):
        try:
            if db.demo_member_count(sha) == 0:
                library.delete_demo(CACHE, UPLOADS, sha)
                db.remove_demo(sha)
                wiped += 1
        except Exception as e:
            print(f"[wipe] orphan cleanup failed for {sha}: {e}")
    return wiped


def _process_upload_job(job):
    """Worker entrypoint (injected into jobs.py): parse the uploaded .dem outside the request,
    save it to the library, and return the demo's source_sha1. Raises on failure (-> job 'failed')."""
    path, name, jid = job["upload_path"], job["filename"], job["id"]
    parse_path = path
    try:
        if library.is_gz_name(path):
            # client-gzipped upload: decompress to a byte-identical .dem here (in the WORKER process,
            # not the web tier) before parsing. gzip is lossless, so the .dem's content-hash cache key
            # is the same as a raw upload of the same demo.
            jobs.set_progress(jid, status="parsing", progress="decompressing")
            import gzip as _gzip
            import shutil as _shutil
            fd, parse_path = tempfile.mkstemp(prefix="_jobgz_", suffix=".dem", dir=UPLOADS)
            with _gzip.open(path, "rb") as fin, os.fdopen(fd, "wb") as fout:
                _shutil.copyfileobj(fin, fout, length=1 << 20)
        elif library.is_bz2_name(path):
            # bzip2-compressed upload (Valve MM): decompress to a byte-identical .dem in the WORKER
            # before parsing -- same lossless-cache-key reasoning as the .gz path above.
            jobs.set_progress(jid, status="parsing", progress="decompressing")
            import bz2 as _bz2
            import shutil as _shutil
            fd, parse_path = tempfile.mkstemp(prefix="_jobbz_", suffix=".dem", dir=UPLOADS)
            with _bz2.open(path, "rb") as fin, os.fdopen(fd, "wb") as fout:
                _shutil.copyfileobj(fin, fout, length=1 << 20)
        data = _parse_or_load_dem(parse_path, name,
                                  progress=lambda stage: jobs.set_progress(jid, status=stage, progress=stage))
        jobs.set_progress(jid, status="analyzing", progress="saving to library")
        _save_to_library(name, data, owner_user_id=job.get("owner_user_id"), team_id=job.get("team_id"))
        return data.get("source_sha1")
    finally:
        # _parse_or_load_dem moves the temp to uploads/<key>.dem on success (KEEP_DEM); clean any
        # leftover -- both the uploaded file (.dem or .gz) and the decompressed temp.
        for p in {path, parse_path}:
            try:
                if p and os.path.exists(p) and os.path.basename(p).startswith(("_jobup_", "_jobgz_", "_jobbz_")):
                    os.remove(p)
            except OSError:
                pass


def _ensure_sample():
    """Restore the bundled sample demo into the cache if it's missing. The cache dir is a fresh
    mounted volume on a server (and cache/ is git/docker-ignored), so the sample -- shipped gzipped
    in sample/ -- has to be unpacked into CACHE on first boot or /api/sample 404s ('sample not found')."""
    target = os.path.join(CACHE, "sample.json")
    src = os.path.join(HERE, "sample", "sample.json.gz")
    if os.path.exists(target) or not os.path.exists(src):
        return
    try:
        import gzip
        import shutil
        os.makedirs(CACHE, exist_ok=True)
        with gzip.open(src, "rb") as f_in, open(target, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        meta = os.path.join(HERE, "sample", "sample.meta.json")
        if os.path.exists(meta):
            shutil.copyfile(meta, os.path.join(CACHE, "sample.meta.json"))
        print("[sample] restored bundled sample into cache")
    except Exception as e:
        print(f"[sample] could not restore bundled sample: {e}")


def _validate_prod_config():
    """Fail-closed: refuse to boot a MISCONFIGURED production deploy. Gated on auth_required() (true
    only in production), and called from start_workers() -- NOT at import -- so pytest + local single-
    user mode are completely unaffected. A correctly-configured prod (the current one) passes silently."""
    if not steamauth.auth_required():
        return
    missing = []
    if not os.environ.get("SECRET_KEY"):
        missing.append("SECRET_KEY")
    if not steamauth._truthy(os.environ.get("SESSION_COOKIE_SECURE")):
        missing.append("SESSION_COOKIE_SECURE=1")
    if not os.environ.get("PUBLIC_BASE_URL"):
        missing.append("PUBLIC_BASE_URL")
    if not os.environ.get("ADMIN_STEAM_IDS"):
        missing.append("ADMIN_STEAM_IDS")
    if missing:
        raise SystemExit("FATAL: AUTH_REQUIRED is set (production) but missing/insecure config: "
                         + ", ".join(missing) + " -- refusing to start.")


def start_workers():
    """Start the background parse worker. Called by the server entrypoints (__main__ / wsgi.py),
    NOT at import time -- so `import app` in tests doesn't spawn the worker thread."""
    _validate_prod_config()               # fail-closed before doing anything in production
    goals.migrate_legacy_json()           # one-time import of the old goals.json -> SQLite (server start only)
    _ensure_sample()                      # unpack the bundled sample into the cache volume if absent
    jobs.start_worker(_process_upload_job)


def _process_one_dem(tmp, name, results, owner_user_id=None, team_id=None):
    """Parse a single .dem temp file and append its result (ok or error)."""
    try:
        data = _parse_or_load_dem(tmp, name)
        results.append(_save_to_library(name, data, owner_user_id=owner_user_id, team_id=team_id))
    except Exception as e:
        traceback.print_exc()
        results.append({"name": name, "ok": False, "error": f"parse failed: {e}"})


def _process_zip(tmp, name, results, owner_user_id=None, team_id=None):
    """Extract every .dem from a .zip and process each; per-file errors collected."""
    try:
        members = list(library.iter_zip_dems(tmp))
    except Exception as e:                            # corrupt / not-a-zip
        traceback.print_exc()
        results.append({"name": name, "ok": False, "error": f"bad zip: {e}"})
        return
    if not members:
        results.append({"name": name, "ok": False,
                        "error": "no .dem entries found in zip"})
        return
    for member_name, payload in members:
        fd, dem_tmp = tempfile.mkstemp(prefix="_incoming_", suffix=".dem", dir=UPLOADS)
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(payload)
            _process_one_dem(dem_tmp, member_name, results, owner_user_id=owner_user_id, team_id=team_id)
        finally:
            if os.path.exists(dem_tmp):
                try:
                    os.remove(dem_tmp)
                except OSError:
                    pass


def _gather_files():
    files = []
    for key in request.files:
        files.extend(request.files.getlist(key))
    return [f for f in files if f and f.filename]


def require_auth_when_locked():
    """On any AUTH-ENABLED deployment, reject anonymous requests up front. Returns a (response, 401)
    tuple to return immediately, or None when the request may proceed. Gates resource-creating / data
    endpoints (upload, jobs, nade media) so anonymous traffic can't burn disk/CPU/queue or read others'
    data on a hosted site. Pure-local mode (neither AUTH_REQUIRED nor PUBLIC_BASE_URL set) is unaffected.

    SECURITY: gated on auth_enabled() (AUTH_REQUIRED *or* PUBLIC_BASE_URL), not auth_required() alone --
    a PUBLIC_BASE_URL-only deployment is public-facing and must also block anonymous resource use. In
    auth-enabled mode current_user() is None for an unauthenticated visitor, so this fails closed."""
    if steamauth.auth_enabled() and current_user() is None:
        return _nostore({"error": "login required"}), 401
    return None


def _timed_save(f, path):
    """Save an uploaded file, returning (elapsed_ms, size_bytes) for the admin timing breakdown (19A).
    Pure network-receive happens in waitress/Caddy before this handler runs, so this is the server-side
    receive+save cost -- still enough to tell a slow-upload case apart from a slow-parse case."""
    t0 = time.time()
    f.save(path)
    ms = int((time.time() - t0) * 1000)
    try:
        return ms, os.path.getsize(path)
    except OSError:
        return ms, None


@app.route("/api/upload", methods=["POST"])
def upload():
    """Accept 1+ .dem/.zip files. Default (website mode): enqueue a background parse JOB per demo
    and return {jobs:[{id,filename,status}]} immediately -- poll /api/jobs/<id>. Legacy synchronous
    behavior (parse in-request, return {demos:[...]}) is kept under ?sync=1 for tests/simple clients.
    One bad file never fails the whole batch."""
    blocked = require_auth_when_locked()      # SECURITY: no anon uploads on a locked site (DDoS/disk abuse)
    if blocked:
        return blocked
    files = _gather_files()
    if not files:
        return jsonify({"error": "no files uploaded",
                        "hint": "send a multipart form with one or more .dem or .zip files"}), 400
    u = current_user()
    owner = u["id"] if u else None                        # NULL in local mode; the uploader otherwise
    # upload destination (Personal vs a team the user is on); ignore a team they're not a member of
    try:
        _raw_tid = request.form.get("team_id")
        dest_team = int(_raw_tid) if _raw_tid not in (None, "", "null") else None
    except (TypeError, ValueError):
        dest_team = None
    if dest_team is not None and (owner is None or dest_team not in db.team_ids_for_user(owner)):
        dest_team = None
    al = upload_allowance(u)                              # enforce the Free demo cap (no-op when unlimited)
    if not al["unlimited"] and al["used"] >= al["limit"]:
        return _nostore({"error": "Free plan holds %d replays. Upgrade to Pro, or archive an old demo "
                                  "(frees space, keeps its stats) to make room." % al["limit"],
                         "upsell": True, "quota": al}), 403
    # don't let one user flood the parse queue
    if owner is not None and jobs.count_active(owner) >= MAX_ACTIVE_JOBS:
        return _nostore({"error": "You have %d demos still processing -- wait for those to finish first."
                                  % MAX_ACTIVE_JOBS}), 429
    # never accept an upload we can't safely store (keep headroom for parse temp + cache)
    try:
        if shutil.disk_usage(UPLOADS).free < MIN_FREE_DISK_BYTES:
            return _nostore({"error": "Server storage is temporarily full. Please try again later."}), 507
    except OSError:
        pass

    # --- legacy synchronous path (?sync=1) -----------------------------------
    if request.args.get("sync"):
        results = []
        for f in files:
            name = f.filename
            is_zip, is_dem = library.is_zip_name(name), library.is_dem_name(name)
            if not (is_zip or is_dem):
                results.append({"name": name, "ok": False, "error": "expected a .dem or .zip file"})
                continue
            fd, tmp = tempfile.mkstemp(prefix="_incoming_", suffix=".zip" if is_zip else ".dem", dir=UPLOADS)
            os.close(fd)
            try:
                f.save(tmp)
                (_process_zip if is_zip else _process_one_dem)(tmp, name, results, owner, dest_team)
            except Exception as e:
                traceback.print_exc()
                results.append({"name": name, "ok": False, "error": f"upload failed: {e}"})
            finally:
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
        return _nostore({"demos": results})

    # --- async path (default): persist upload + enqueue a parse job ----------
    created = []
    for f in files:
        name = f.filename
        is_zip, is_dem, is_gz = library.is_zip_name(name), library.is_dem_name(name), library.is_gz_name(name)
        is_bz2 = library.is_bz2_name(name)
        if not (is_zip or is_dem or is_gz or is_bz2):
            created.append({"filename": name, "ok": False, "error": "expected a .dem, .dem.gz, .dem.bz2 or .zip file"})
            continue
        try:
            if is_bz2:
                # bzip2-compressed demo (Valve MM download). Save as-is + enqueue; the worker bz2-
                # decompresses to a byte-identical .dem before parsing (same as the .gz path).
                dispname = library.strip_bz2(name)
                fd, btmp = tempfile.mkstemp(prefix="_jobup_", suffix=".dem.bz2", dir=UPLOADS)
                os.close(fd)
                ums, bts = _timed_save(f, btmp)
                created.append({"id": jobs.create_job(dispname, btmp, owner_user_id=owner, upload_ms=ums, size_bytes=bts, team_id=dest_team),
                                "filename": dispname, "status": "queued", "ok": True})
            elif is_gz:
                # client-gzipped demo (CompressionStream): save the .gz as-is + enqueue; the worker
                # gunzips before parsing (keeps this web tier light -- it does no decompression/parse).
                dispname = library.strip_gz(name)
                fd, gtmp = tempfile.mkstemp(prefix="_jobup_", suffix=".dem.gz", dir=UPLOADS)
                os.close(fd)
                ums, bts = _timed_save(f, gtmp)
                created.append({"id": jobs.create_job(dispname, gtmp, owner_user_id=owner, upload_ms=ums, size_bytes=bts, team_id=dest_team),
                                "filename": dispname, "status": "queued", "ok": True})
            elif is_zip:
                fd, ztmp = tempfile.mkstemp(prefix="_zip_", suffix=".zip", dir=UPLOADS)
                os.close(fd)
                try:
                    zms, _zb = _timed_save(f, ztmp)
                    members = list(library.iter_zip_dems(ztmp))
                    if not members:
                        created.append({"filename": name, "ok": False, "error": "no .dem entries in zip"})
                    per_ms = int(zms / len(members)) if members else zms   # split the archive receive time across its demos
                    for mname, payload in members:
                        fd, dtmp = tempfile.mkstemp(prefix="_jobup_", suffix=".dem", dir=UPLOADS)
                        with os.fdopen(fd, "wb") as out:
                            out.write(payload)
                        created.append({"id": jobs.create_job(mname, dtmp, owner_user_id=owner, upload_ms=per_ms, size_bytes=len(payload), team_id=dest_team),
                                        "filename": mname, "status": "queued", "ok": True})
                finally:
                    if os.path.exists(ztmp):
                        try:
                            os.remove(ztmp)
                        except OSError:
                            pass
            else:
                fd, dtmp = tempfile.mkstemp(prefix="_jobup_", suffix=".dem", dir=UPLOADS)
                os.close(fd)
                ums, bts = _timed_save(f, dtmp)
                # Already in this user's library (same content sha1)? Don't re-queue it -- drop the temp
                # and tell the client it's a duplicate so the UI says "already uploaded" instead of
                # spinning up a redundant parse job. (Raw .dem: the temp IS the content, so the hash is
                # exact + cheap. .gz/.bz2/zip stay idempotent via the worker's content-hash cache.)
                dup_key = db.user_has_demo(owner, _sha1_file(dtmp))
                if dup_key:
                    try:
                        os.remove(dtmp)
                    except OSError:
                        pass
                    created.append({"filename": name, "ok": True, "duplicate": True, "id": dup_key})
                else:
                    created.append({"id": jobs.create_job(name, dtmp, owner_user_id=owner, upload_ms=ums, size_bytes=bts, team_id=dest_team),
                                    "filename": name, "status": "queued", "ok": True})
        except Exception as e:
            traceback.print_exc()
            created.append({"filename": name, "ok": False, "error": f"upload failed: {e}"})
    return _nostore({"jobs": created})


@app.route("/api/jobs/<job_id>")
def api_job(job_id):
    blocked = require_auth_when_locked()      # SECURITY: anon can't probe job ids/status on a locked site
    if blocked:
        return blocked
    j = jobs.get_job(job_id)
    if not j:
        return jsonify({"error": "no such job"}), 404
    uid = current_user_id()                                # only your own jobs (legacy ownerless = visible)
    if j.get("owner_user_id") is not None and uid != j["owner_user_id"] and not is_admin(current_user()):
        return jsonify({"error": "no such job"}), 404      # don't reveal another user's job exists
    return _nostore(jobs._public(j))


@app.route("/api/jobs")
def api_jobs():
    blocked = require_auth_when_locked()      # SECURITY: don't list jobs to anon on a locked site
    if blocked:
        return blocked
    active = bool(request.args.get("active"))
    uid = current_user_id()                                # scope to the uploader (was leaking everyone's)
    return _nostore({"jobs": [jobs._public(j) for j in jobs.list_jobs(owner_user_id=uid, active_only=active)]})


@app.route("/api/library")
def api_library():
    """Saved library, newest-first; each row tagged stale vs current SCHEMA_VERSION.
    Scoped to the current user's visible demos when auth is on (Stage 5)."""
    mode, sc = _scope_or_block()
    if mode == "blocked":
        return _nostore({"error": "login required"}), 401
    demos = library.list_demos(CACHE, SCHEMA_VERSION)    # cache-backed -> only watchable replays appear
    teams = []
    if sc is not None:
        ok = db.visible_predicate(sc)
        demos = [d for d in demos if ok(d.get("id"))]    # deleted (stats-only) demos have no cache -> excluded
        # #23: tag each row personal vs team-shared so the library can split into Personal / per-team tabs.
        mem = db.library_membership(sc)
        for d in demos:
            m = mem.get(d.get("id"))
            d["team_ids"] = m["team_ids"] if m else []
            d["personal"] = (m["personal"] or not m["team_ids"]) if m else True   # no team -> personal
            d["tag"] = (m.get("tag") if m else None) or ""                        # viewer's own demo tag
        teams = [{"id": t["id"], "name": t["name"]} for t in db.teams_for_user(sc.get("uid"))]
    else:
        for d in demos:                                  # open/local mode: everything is personal
            d["team_ids"], d["personal"], d["tag"] = [], True, ""
    resp = jsonify({"demos": demos, "teams": teams})
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _retain_compact_stats(sha):
    """Write the tiny compact .txt stats for a match BEFORE its replay/cache is deleted, so the match
    keeps contributing to trends/profile/goals. Best-effort -- never block a delete on a stats write."""
    try:
        m = db.match_for_stats(sha)
        if m:
            statsfile.write(STATS, sha, m)
    except Exception as e:
        print(f"[stats] retain failed for {sha}: {e}")


@app.route("/api/demo/<demo_id>", methods=["GET", "DELETE"])
def api_demo(demo_id):
    """GET: the saved parsed JSON for a library id (same shape as /api/sample), or 404.
    DELETE: remove the REPLAY (parsed cache + raw .dem) and drop it from the library to free storage,
    but KEEP compact stats (index rows + a tiny .txt) so trends/profile/goals retain the match."""
    mode, sc = _scope_or_block()
    if mode == "blocked":
        return _nostore({"error": "login required"}), 401
    if request.method == "DELETE":
        if not db.can_delete(demo_id, sc):             # team viewers can't delete others' demos
            return _nostore({"error": "not allowed"}), 403
        sha = db.resolve_sha(demo_id)
        uid = sc.get("uid") if isinstance(sc, dict) else None
        # Flag THIS user's copy stats-only (keeps it in their trend scope) instead of removing it.
        # still_full = members who still hold a FULL replay; the shared cache survives until that's 0.
        still_full = db.set_archived(uid, sha, 1) if uid is not None else 0
        freed = 0
        if still_full == 0:                            # nobody holds a full replay -> free the heavy files
            _retain_compact_stats(sha)                 # tiny .txt first (cache about to go)
            res = library.delete_demo(CACHE, UPLOADS, sha)   # parsed cache + raw .dem; KEEPS the index rows
            freed = res.get("bytes", 0) if isinstance(res, dict) else 0
        return _nostore({"ok": True, "removed_from_library": True, "freed_bytes": freed,
                         "shared": still_full > 0})
    if sc is not None and not db.accessible(demo_id, sc):
        return jsonify({"error": "no demo with that id"}), 404   # 404 (not 403): don't leak existence
    data = library.load_demo(CACHE, demo_id)
    if data is None:
        return jsonify({"error": "no demo with that id"}), 404
    analytics_migrations.migrate(data)              # upgrade stale-but-derivable analytics in place (.dem-free)
    resp = jsonify(data)
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _load_demo_by_sha(sid):
    """Find a parsed demo's JSON by its source_sha1, wherever it lives: a library copy,
    a content-hash upload cache (16-char key), or the sample. Used for review auto-queues."""
    if not sid:
        return None
    d = library.load_demo(CACHE, sid)                    # library copy (lib_<sha>.json)
    if d is not None:
        analytics_migrations.migrate(d)                  # .dem-free upgrade of stale-but-derivable analytics
        return d
    if re.fullmatch(r"[A-Za-z0-9]+", sid):               # content-hash cache (<sha[:16]>.json)
        d = load_cache(os.path.join(CACHE, f"{sid[:16]}.json"))
        if d is not None:
            analytics_migrations.migrate(d)
            return d
    s = load_cache(os.path.join(CACHE, "sample.json"))   # the sample
    if s is not None and s.get("source_sha1") == sid:
        analytics_migrations.migrate(s)
        return s
    return None


@app.route("/api/reviews/<demo_id>/bookmarks", methods=["GET", "POST"])
def api_bookmarks(demo_id):
    """List (GET) or add/replace (POST) review bookmarks for a demo (keyed by source_sha1).
    SECURITY: scoped -- you can only read/write bookmarks for a demo you can access."""
    mode, sc = _scope_or_block()
    if mode == "blocked":
        return _nostore({"error": "login required"}), 401
    if mode == "scoped" and not db.accessible(demo_id, sc):
        return _nostore({"error": "not found"}), 404
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        try:
            bm = reviews.add_bookmark(demo_id, body)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify(bm)
    return jsonify({"bookmarks": reviews.bookmarks(demo_id)})


@app.route("/api/reviews/<demo_id>/bookmarks/<bm_id>", methods=["DELETE"])
def api_bookmark_delete(demo_id, bm_id):
    mode, sc = _scope_or_block()
    if mode == "blocked":
        return _nostore({"error": "login required"}), 401
    if mode == "scoped" and not db.accessible(demo_id, sc):
        return _nostore({"error": "not found"}), 404
    return jsonify({"deleted": reviews.delete_bookmark(demo_id, bm_id)})


@app.route("/api/reviews/<demo_id>/queues")
def api_review_queues(demo_id):
    """Auto-seeded review queues (untraded deaths, dry opens, good rounds, team losses,
    round-by-round, ...) computed from the demo's analytics. Empty list if not found.
    SECURITY: scoped to demos the caller can access."""
    mode, sc = _scope_or_block()
    if mode == "blocked":
        return _nostore({"error": "login required"}), 401
    if mode == "scoped" and not db.accessible(demo_id, sc):
        return jsonify({"queues": []})
    data = _load_demo_by_sha(demo_id)
    if data is None:
        return jsonify({"queues": []})
    return jsonify({"queues": reviews.auto_queues(data)})


@app.route("/api/reviews/<demo_id>/summary")
def api_coaching_summary(demo_id):
    """P3: a short natural-language 'Coaching summary' for a match -- what happened, why rounds were
    lost, what to review first, what util to learn. Deterministic heuristic by default; optionally
    AI-rewritten when ANTHROPIC_API_KEY + AI_SUMMARY_ENABLED are set (server-side only). Scoped."""
    mode, sc = _scope_or_block()
    if mode == "blocked":
        return _nostore({"error": "login required"}), 401
    if mode == "scoped" and not db.accessible(demo_id, sc):
        return _nostore({"error": "not found"}), 404
    data = _load_demo_by_sha(demo_id)
    if data is None or not isinstance(data.get("analytics"), dict):
        return _nostore({"error": "no analytics for this demo"}), 404
    side = request.args.get("side") or None
    stid = request.args.get("player") or None
    recurring = None
    if stid:                                            # cross-match "what keeps happening" for this player
        try:
            recurring = goals.recurring_mistakes(CACHE, stid, min_matches=2)
        except Exception:
            recurring = None
    out = coaching_summary.coaching_summary(data["analytics"], my_side=side,
                                            player_steamid=stid, recurring=recurring)
    return _nostore(out)


@app.route("/api/reviews/<demo_id>/export")
def api_export_report(demo_id):
    """Rich match-report export: ?fmt=text|json|html (html = open + Print-to-PDF). Scoped. Much fuller
    than the Discord text share -- full scoreboard, key rounds, economy, utility, coaching prose."""
    mode, sc = _scope_or_block()
    if mode == "blocked":
        return _nostore({"error": "login required"}), 401
    if mode == "scoped" and not db.accessible(demo_id, sc):
        return _nostore({"error": "not found"}), 404
    data = _load_demo_by_sha(demo_id)
    if data is None or not isinstance(data.get("analytics"), dict):
        return _nostore({"error": "no analytics for this demo"}), 404
    fmt = (request.args.get("fmt") or "text").lower()
    side = request.args.get("side") or None
    stid = request.args.get("player") or None
    recurring = None
    if stid:
        try:
            recurring = goals.recurring_mistakes(CACHE, stid, min_matches=2)
        except Exception:
            recurring = None
    body = export_report.render(data["analytics"], fmt=fmt, my_side=side, recurring=recurring,
                                map_name=data.get("map"), score=data.get("score"),
                                date=data.get("parsed_at"),
                                title=f"{data.get('map', 'CS2')} match report")
    ctype = {"json": "application/json; charset=utf-8",
             "html": "text/html; charset=utf-8"}.get(fmt, "text/plain; charset=utf-8")
    resp = Response(body, mimetype=ctype.split(";")[0])
    resp.headers["Content-Type"] = ctype
    resp.headers["Cache-Control"] = "no-store"
    if fmt in ("text", "txt", "json"):                  # html opens in a tab to print; these download
        ext = "json" if fmt == "json" else "txt"
        mapn = (data.get("map") or "match").replace("/", "_")
        resp.headers["Content-Disposition"] = f'attachment; filename="{mapn}_report.{ext}"'
    return resp


@app.route("/api/goals/metrics")
def api_goal_metrics():
    """The trackable metrics for the goal-create UI (key/label/kind/better/unit/scopes) +
    the side/buy/role option lists for scope dropdowns."""
    return jsonify({"metrics": goals.METRICS, "sides": goals.SIDES,
                    "buys": goals.BUYS, "roles": goals.ROLES})


@app.route("/api/goals", methods=["GET", "POST"])
def api_goals():
    """List all Practice Goals WITH cross-match grading (GET), or create one (POST).
    GET also returns the distinct maps across cached matches (for the scope dropdown),
    derived from goals._matches -- which is sidecar-cached, so this stays cheap (no full
    demo reload, unlike /api/matches)."""
    gate = require_feature("goals")                       # Practice Goals is a Pro feature
    if gate:
        return gate
    uid = current_user_id()
    team_ids = set(db.team_ids_for_user(uid)) if uid else set()
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        body["owner_user_id"] = uid                       # creator (from the session, not the client)
        try:                                              # share only with a team you actually belong to
            tid = int(body.get("team_id"))
        except (TypeError, ValueError):
            tid = None
        body["team_id"] = tid if tid in team_ids else None
        return jsonify({"goal": goals.add_goal(body, CACHE)})
    maps = sorted({m["map"] for m in goals._matches(CACHE) if m.get("map")})
    return jsonify({"goals": goals.visible_progress(CACHE, uid, team_ids), "maps": maps})


@app.route("/api/recurring")
def api_recurring():
    """Repeated mistakes across cached matches -- for ?player=<steamid> (only matches they
    played) or the whole team. Feeds the 'recurring mistakes' panel + one-click goal creation."""
    player = request.args.get("player") or None
    return jsonify(goals.recurring_mistakes(CACHE, player=player))


@app.route("/api/goals/<goal_id>", methods=["PUT", "DELETE"])
def api_goal(goal_id):
    """Update a goal's status/notes/etc. (PUT) or delete it (DELETE). A shared goal can be edited by
    any member of its team and deleted by its creator or the team's owner; a personal goal only by its
    owner. (Legacy/local ownerless goals stay editable/deletable by anyone -- preserves single-user.)"""
    gate = require_feature("goals")
    if gate:
        return gate
    uid = current_user_id()
    g = goals.get_goal(goal_id)
    if g is None:
        return jsonify({"error": "no goal with that id"}), 404
    owner, team_id = g.get("owner_user_id"), g.get("team_id")
    my_teams = db.teams_for_user(uid) if uid else []
    member_tids = {t["id"] for t in my_teams}
    owner_tids = {t["id"] for t in my_teams if t["role"] == "owner"}
    can_edit = owner is None or owner == uid or (team_id in member_tids)
    can_delete = owner is None or owner == uid or (team_id in owner_tids)
    if request.method == "DELETE":
        if not can_delete:
            return jsonify({"error": "not allowed"}), 403
        return jsonify({"deleted": goals.delete_goal(goal_id)})
    if not can_edit:
        return jsonify({"error": "not allowed"}), 403
    return jsonify({"goal": goals.update_goal(goal_id, request.get_json(silent=True) or {})})


# ---- 3D asset status (per-map geometry availability/verification) -----------
@app.route("/api/maps3d/status")
def maps3d_status():
    resp = jsonify(mapstatus.map_status())
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ---- multi-demo trends + team config ----------------------------------------
def _nostore(obj):
    resp = jsonify(obj)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/matches")
def api_matches():
    # served from the SQLite index (was a 10-20s full-JSON scan via matchindex)
    mode, sc = _scope_or_block()
    if mode == "blocked":
        return _nostore({"error": "login required"}), 401
    return _nostore(db.list_matches(scope=sc))


@app.route("/api/dashboard")
def api_dashboard():
    """Landing-page summary built from the SQLite index (fast -- no replay JSON loads). Scoped to the
    current user when auth is on. Powers the dashboard the app opens on instead of the replay UI."""
    mode, sc = _scope_or_block()
    if mode == "blocked":
        return _nostore({"error": "login required"}), 401
    g_uid = current_user_id()
    g_team_ids = set(db.team_ids_for_user(g_uid)) if g_uid else set()
    # Workspace: which dashboard context to show. 'personal' (default) or 'team:<id>'. A team is only
    # valid if the user belongs to it -- a foreign/unknown id is a clear 403 (the frontend then falls
    # back to Personal). In pure local mode (no auth) there's no workspace split: show everything.
    ws_raw = (request.args.get("workspace") or "personal").strip()
    ws_team = None
    if ws_raw.startswith("team:"):
        try:
            ws_team = int(ws_raw.split(":", 1)[1])
        except (TypeError, ValueError):
            ws_team = None
        if ws_team is None or ws_team not in g_team_ids:
            return _nostore({"error": "you are not on that team", "workspace": ws_raw}), 403
    ws_scope = sc
    if sc is not None:                                    # scoped (auth on): restrict to the one workspace
        ws_scope = dict(sc)
        ws_scope["workspace"] = ("team", ws_team) if ws_team is not None else "personal"
        ws_label = "team:%d" % ws_team if ws_team is not None else "personal"
    else:
        ws_label = "personal"                            # local mode -> no real split
    matches = db.list_matches(scope=ws_scope)
    try:
        # personal workspace -> only the user's own (non-team) goals; team workspace -> that team's goals
        cand = db.goals_visible(g_uid, g_team_ids if ws_team is not None else set())
        if ws_team is not None:
            cand = [g for g in cand if g.get("team_id") == ws_team]
        else:
            cand = [g for g in cand if not g.get("team_id")]
        open_goals = [g for g in cand if g.get("status") in ("open", "drilling")]
    except Exception:
        open_goals = []
    # active parse jobs for THIS workspace (personal: untargeted; team: targeting that team)
    active = []
    for j in jobs.list_jobs(owner_user_id=g_uid, active_only=True):
        jt = j.get("team_id")
        if (ws_team is None and not jt) or (ws_team is not None and jt == ws_team):
            active.append(jobs._public(j))
    # the signed-in user's own form (rating/K-D/ADR/KAST + trend) WITHIN this workspace's matches
    me = None
    u = current_user()
    if u and u.get("steam_id_64"):
        try:
            t = db.player_trends(u["steam_id_64"], scope=ws_scope)
            if t.get("n_matches"):
                me = t
        except Exception:
            me = None
    return _nostore({
        "matches": matches[:12],
        "match_count": len(matches),
        "active_jobs": active,
        "open_goals": open_goals[:6],
        "open_goal_count": len(open_goals),
        "me": me,
        "workspace": ws_label,
    })


def _build_roster(uid, ws_team, ws_scope, mode):
    """Return (roster_mode, roster_sids, roster_members).
    roster_sids: set of steamid strings to keep; empty set = keep all (local_all).
    """
    if mode == "open" or uid is None:
        return "local_all", set(), []
    if ws_team is not None:
        teams = db.teams_for_user(uid)
        team = next((t for t in teams if t["id"] == ws_team), None)
        if team:
            members = [{"steamid": m["steamid"], "name": m["name"]}
                       for m in (team.get("members") or []) if m.get("steamid")]
            return "team", {m["steamid"] for m in members}, members
        return "team", set(), []
    you, co = db.squad_for(uid, scope=ws_scope)
    overrides = db.roster_overrides(uid)
    members, sids = [], set()
    if you:
        members.append({"steamid": you["steamid"], "name": you["name"]})
        sids.add(you["steamid"])
    for p in co:
        status = overrides.get(p["steamid"])
        if status == "out":
            continue
        if status == "in" or p["shared"] >= 2:
            members.append({"steamid": p["steamid"], "name": p["name"]})
            sids.add(p["steamid"])
    return "personal_squad", sids, members


@app.route("/api/dashboard/analytics")
def api_dashboard_analytics():
    """Cross-demo analytics: roster-scoped player stats, form windows, map breakdown,
    next-focus recommendations. Scoped to the active dashboard workspace."""
    mode, sc = _scope_or_block()
    if mode == "blocked":
        return _nostore({"error": "login required"}), 401
    g_uid = current_user_id()
    g_team_ids = set(db.team_ids_for_user(g_uid)) if g_uid else set()
    ws_raw = (request.args.get("workspace") or "personal").strip()
    ws_team = None
    if ws_raw.startswith("team:"):
        try:
            ws_team = int(ws_raw.split(":", 1)[1])
        except (TypeError, ValueError):
            ws_team = None
        if ws_team is None or ws_team not in g_team_ids:
            return _nostore({"error": "you are not on that team"}), 403
    ws_scope = sc
    if sc is not None:
        ws_scope = dict(sc)
        ws_scope["workspace"] = ("team", ws_team) if ws_team is not None else "personal"

    roster_mode, roster_sids, roster_members = _build_roster(g_uid, ws_team, ws_scope, mode)
    matches = db.list_matches(scope=ws_scope)   # newest-first
    # Per-(sha,steamid) utility/flash/perf columns for the visible matches -- one bounded SQL query
    # against the index (no cache JSON / frames). Honest None where a metric was unavailable a match.
    try:
        dash_metrics = db.dashboard_player_metrics(scope=ws_scope)
    except Exception:
        dash_metrics = {}

    STAT_FIELDS = ["kills", "deaths", "kd", "adr", "kast", "hltv", "open_wr", "traded_pct", "udr"]
    AVG_FIELDS = ["adr", "kast", "hltv", "open_wr", "traded_pct", "udr"]
    # raw util counts (per-game), gated flash, and the Leetify-comparable perf metrics surfaced from
    # demo_players. UTIL_COUNT_FIELDS are always-present counts; FLASH_FIELDS/he_dmg are None when the
    # demo lacked player_blind / HE data (omitted, never a fake 0).
    UTIL_COUNT_FIELDS = ["smokes", "flashes_thrown", "hes", "molotovs"]
    FLASH_FIELDS = ["enemy_flashed", "team_flashed", "blind_time"]
    PERF_FIELDS = ["headshot_accuracy", "he_dmg_per_he", "accuracy",
                   "flashes_hit_foe_per_game", "flashes_hit_friend_per_game",
                   "total_flash_blind_duration_per_game"]

    def _in_roster(sid):
        return not roster_sids or str(sid or "") in roster_sids

    def _metric(sha, sid, col):
        """A per-match metric value for (sha,steamid) from the index, or None if absent/unavailable."""
        v = (dash_metrics.get(sha) or {}).get(str(sid or ""), {}).get(col)
        return v if isinstance(v, (int, float)) else None

    def _avg_none(vals):
        """Mean of the non-None values, or None when there are none (no fabricated 0)."""
        xs = [v for v in vals if isinstance(v, (int, float))]
        return sum(xs) / len(xs) if xs else None

    # Per-player aggregate stats (roster-filtered)
    player_agg = {}
    for m in matches:
        for p in (m.get("players") or []):
            sid = str(p.get("steamid") or "")
            if not sid or not _in_roster(sid):
                continue
            if sid not in player_agg:
                player_agg[sid] = {"steamid": sid, "name": p.get("name"), "n": 0,
                                   **{f: 0.0 for f in STAT_FIELDS}}
            rec = player_agg[sid]
            rec["n"] += 1
            if p.get("name"):
                rec["name"] = p["name"]
            for f in STAT_FIELDS:
                v = p.get(f)
                if isinstance(v, (int, float)):
                    rec[f] += v

    players_out = []
    for rec in player_agg.values():
        n = rec["n"]
        row = {"steamid": rec["steamid"], "name": rec["name"], "n_matches": n}
        for f in STAT_FIELDS:
            row[f] = round(rec[f] / n, 2) if n else 0.0
        players_out.append(row)
    players_out.sort(key=lambda r: (-r["n_matches"], -(r.get("hltv") or 0)))

    # Per-player enrichment: per-map split + per-game utility averages + None-aware perf averages.
    # Built from the same scope/roster-filtered matches and the indexed metrics (no extra cache load).
    pmap = {}          # sid -> {map -> {"n":int, "hltv":[], "adr":[], "kast":[]}}
    putil = {}         # sid -> {col -> [vals]}  (UTIL_COUNT_FIELDS + enemy_flashed/team_flashed/avg_blind)
    pperf = {}         # sid -> {col -> [vals]}  (PERF_FIELDS)
    for m in matches:
        mp = m.get("map") or "unknown"
        sha = m.get("id")
        for p in (m.get("players") or []):
            sid = str(p.get("steamid") or "")
            if not sid or not _in_roster(sid):
                continue
            mm = pmap.setdefault(sid, {}).setdefault(mp, {"n": 0, "hltv": [], "adr": [], "kast": []})
            mm["n"] += 1
            for f in ("hltv", "adr", "kast"):
                v = p.get(f)
                if isinstance(v, (int, float)) and v > 0:
                    mm[f].append(v)
            u = putil.setdefault(sid, {})
            for f in UTIL_COUNT_FIELDS:
                mv = _metric(sha, sid, f)
                if mv is not None:
                    u.setdefault(f, []).append(mv)
            # per-game flash: enemy/team flashed counts + avg blind (derived per match)
            ef, tf, bt = (_metric(sha, sid, "enemy_flashed"), _metric(sha, sid, "team_flashed"),
                          _metric(sha, sid, "blind_time"))
            if ef is not None:
                u.setdefault("enemy_flashed", []).append(ef)
                if isinstance(bt, (int, float)):
                    u.setdefault("avg_blind", []).append(round(bt / ef, 2) if ef else 0.0)
            if tf is not None:
                u.setdefault("team_flashed", []).append(tf)
            pf = pperf.setdefault(sid, {})
            for f in PERF_FIELDS:
                mv = _metric(sha, sid, f)
                if mv is not None:
                    pf.setdefault(f, []).append(mv)

    for row in players_out:
        sid = row["steamid"]
        # per-map split (n>=1), sorted by matches then rating
        maps = []
        for mp, mm in (pmap.get(sid) or {}).items():
            maps.append({"map": mp, "n": mm["n"],
                         "hltv": round(_avg_none(mm["hltv"]), 2) if _avg_none(mm["hltv"]) is not None else None,
                         "adr": round(_avg_none(mm["adr"]), 1) if _avg_none(mm["adr"]) is not None else None,
                         "kast": round(_avg_none(mm["kast"]), 1) if _avg_none(mm["kast"]) is not None else None})
        maps.sort(key=lambda r: (-r["n"], -(r.get("hltv") or 0)))
        row["maps"] = maps
        # per-game utility averages (omit a key entirely when no samples -> never a fake 0)
        u, util_out = putil.get(sid) or {}, {}
        for f in UTIL_COUNT_FIELDS + ["enemy_flashed", "team_flashed", "avg_blind"]:
            av = _avg_none(u.get(f) or [])
            if av is not None:
                util_out[f] = round(av, 2)
        row["utility"] = util_out
        # None-aware perf averages (skip None; omit a key with no samples)
        pf, perf_out = pperf.get(sid) or {}, {}
        for f in PERF_FIELDS:
            av = _avg_none(pf.get(f) or [])
            if av is not None:
                perf_out[f] = round(av, 2)
        row["perf"] = perf_out

    # Form windows: average each stat across roster players per-match, then average per window
    def _match_avg(m, field):
        vals = [p.get(field) for p in (m.get("players") or [])
                if _in_roster(str(p.get("steamid") or ""))
                and isinstance(p.get(field), (int, float)) and p[field] > 0]
        return sum(vals) / len(vals) if vals else None

    def _window(match_list):
        result = {}
        for f in AVG_FIELDS:
            per_match = [_match_avg(m, f) for m in match_list]
            vals = [v for v in per_match if v is not None]
            result[f] = round(sum(vals) / len(vals), 1) if vals else None
        return result

    all_avg = _window(matches)
    last5 = _window(matches[:5])
    last3 = _window(matches[:3])

    def _delta(recent, base):
        if recent is None or base is None:
            return None
        return round(recent - base, 1)

    delta3 = {f: _delta(last3.get(f), all_avg.get(f)) for f in AVG_FIELDS}

    # Per-map stats (roster-filtered). Alongside the existing AVG_FIELDS we now also accumulate the
    # per-game utility counts, gated flash, total HE damage, and per-round util damage -- all from the
    # index (dash_metrics), None-aware so an unavailable metric never drags an average toward 0.
    UTIL_AGG_FIELDS = UTIL_COUNT_FIELDS + FLASH_FIELDS + ["he_dmg", "udr"]
    map_agg = {}
    for m in matches:
        mp = m.get("map") or "unknown"
        sha = m.get("id")
        if mp not in map_agg:
            map_agg[mp] = {"map": mp, "count": 0,
                           **{f: [] for f in AVG_FIELDS}, **{f: [] for f in UTIL_AGG_FIELDS}}
        map_agg[mp]["count"] += 1
        for p in (m.get("players") or []):
            sid = str(p.get("steamid") or "")
            if not _in_roster(sid):
                continue
            for f in AVG_FIELDS:
                v = p.get(f)
                if isinstance(v, (int, float)) and v > 0:
                    map_agg[mp][f].append(v)
            # util/flash/he_dmg/udr come from the index per (sha,steamid); keep None out of the list
            for f in UTIL_COUNT_FIELDS + FLASH_FIELDS + ["he_dmg"]:
                mv = _metric(sha, sid, f)
                if mv is not None:
                    map_agg[mp][f].append(mv)
            uv = p.get("udr")                          # udr is in the match row already
            if isinstance(uv, (int, float)):
                map_agg[mp]["udr"].append(uv)

    def _flash_block(d):
        """Roster flash aggregate for a per-map/overall accumulator dict, or None when no player_blind
        data contributed (all flash lists empty) -- honest 'unavailable' instead of a fake 0 block."""
        ef, tf, bt = d.get("enemy_flashed") or [], d.get("team_flashed") or [], d.get("blind_time") or []
        if not ef and not tf and not bt:
            return None
        enemies = round(sum(ef), 1) if ef else 0.0
        teammates = round(sum(tf), 1) if tf else 0.0
        blind_total = round(sum(bt), 1) if bt else 0.0
        thrown = d.get("flashes_thrown") or []
        thrown_sum = sum(thrown) if thrown else 0
        return {
            "thrown": round(thrown_sum, 1) if thrown else None,
            "enemies": enemies, "teammates": teammates, "blind_total": blind_total,
            "avg_blind": round(blind_total / enemies, 2) if enemies else None,
            "enemies_per_flash": round(enemies / thrown_sum, 2) if thrown_sum else None,
            "teammates_per_flash": round(teammates / thrown_sum, 2) if thrown_sum else None,
        }

    # per-map CT/T round-winrate (reuse sidestats; cheap analytics-only sidecars, scope-filtered).
    side_by_map = {}
    try:
        srecs = goals._matches(CACHE)
        if ws_scope is not None:
            okp = db.visible_predicate(ws_scope)
            srecs = [r for r in srecs if okp(r.get("sha"))]
        spairs = [(r["analytics"], r.get("map")) for r in srecs if isinstance(r.get("analytics"), dict)]
        side_sid = next(iter(roster_sids)) if roster_sids else None
        side_by_map = sidestats.aggregate_side_winrates(spairs, steamid=side_sid)
    except Exception:
        side_by_map = {}

    map_stats = []
    for mp, d in sorted(map_agg.items(), key=lambda x: -x[1]["count"]):
        row = {"map": mp, "count": d["count"]}
        for f in AVG_FIELDS:
            vals = d[f]
            row[f] = round(sum(vals) / len(vals), 1) if vals else None
        # new per-map utility fields (avg over roster players on that map; None when no data)
        upr = _avg_none(d["udr"])
        row["util_per_round"] = round(upr, 1) if upr is not None else None
        hed = _avg_none(d["he_dmg"])
        row["he_dmg"] = round(hed, 1) if hed is not None else None
        fb = _flash_block(d)
        if fb is not None:
            row["flash"] = fb
        sw = side_by_map.get(mp)
        if sw is not None:
            row["ct_win_rate"] = sw.get("ct_wr")
            row["t_win_rate"] = sw.get("t_wr")
        map_stats.append(row)

    # ---- top-level utility breakdown (roster/scope-respecting) ----------------
    util_by_type = {"smoke": 0, "flash": 0, "he": 0, "molotov": 0}
    util_by_map_acc, flash_by_map_acc, dmg_by_map_acc = {}, {}, {}
    _TYPE_COL = {"smoke": "smokes", "flash": "flashes_thrown", "he": "hes", "molotov": "molotovs"}
    for m in matches:
        mp = m.get("map") or "unknown"
        sha = m.get("id")
        ub = util_by_map_acc.setdefault(mp, {"map": mp, "smoke": 0, "flash": 0, "he": 0, "molotov": 0})
        fb = flash_by_map_acc.setdefault(mp, {f: [] for f in FLASH_FIELDS + ["flashes_thrown"]})
        dm = dmg_by_map_acc.setdefault(mp, [])
        for p in (m.get("players") or []):
            sid = str(p.get("steamid") or "")
            if not _in_roster(sid):
                continue
            for t, col in _TYPE_COL.items():
                mv = _metric(sha, sid, col)
                if mv is not None:
                    util_by_type[t] += mv
                    ub[t] += mv
            for f in FLASH_FIELDS + ["flashes_thrown"]:
                mv = _metric(sha, sid, f)
                if mv is not None:
                    fb[f].append(mv)
            hed = _metric(sha, sid, "he_dmg")
            if hed is not None:
                dm.append(hed)

    util_by_map = sorted(
        ([{"map": mp, **{t: round(v[t], 1) for t in ("smoke", "flash", "he", "molotov")},
           "total": round(v["smoke"] + v["flash"] + v["he"] + v["molotov"], 1)}
          for mp, v in util_by_map_acc.items()]),
        key=lambda r: -r["total"])
    util_by_map = [r for r in util_by_map if r["total"] > 0]
    flash_by_map = []
    for mp, d in flash_by_map_acc.items():
        blk = _flash_block(d)
        if blk is not None:
            flash_by_map.append({
                "map": mp, "thrown": blk["thrown"],
                "enemies_flashed": blk["enemies"], "teammates_flashed": blk["teammates"],
                "blind_total": blk["blind_total"], "avg_blind": blk["avg_blind"],
                "enemies_per_flash": blk["enemies_per_flash"],
                "teammates_per_flash": blk["teammates_per_flash"]})
    flash_by_map.sort(key=lambda r: -((r.get("enemies_flashed") or 0) + (r.get("teammates_flashed") or 0)))
    dmg_by_map = sorted(
        [{"map": mp, "he_dmg": round(sum(xs) / len(xs), 1)} for mp, xs in dmg_by_map_acc.items() if xs],
        key=lambda r: -r["he_dmg"])
    utility = {
        "by_type": {t: round(util_by_type[t], 1) for t in util_by_type},
        "by_map": util_by_map, "flash_by_map": flash_by_map, "dmg_by_map": dmg_by_map,
    }

    # Recurring issues
    try:
        recurring = goals.recurring_mistakes(CACHE).get("recurring", [])[:8]
    except Exception:
        recurring = []

    # Next review focus: top issue + weak map + form drop (up to 3 items)
    next_focus = []
    if recurring:
        top = recurring[0]
        next_focus.append({
            "type": "recurring_issue",
            "label": top["label"],
            "detail": f"recurs in {top['matches_present']} of {top['matches_total']} matches",
            "suggest_metric": top.get("suggest_metric"),
            "suggested_target": top.get("suggested_target"),
        })
    weak = sorted([r for r in map_stats if r["count"] >= 2 and r.get("hltv") is not None],
                  key=lambda x: x["hltv"])
    if weak:
        wm = weak[0]
        next_focus.append({
            "type": "weak_map",
            "label": wm["map"],
            "detail": f"avg rating {wm['hltv']} over {wm['count']} matches",
        })
    for f, label in [("hltv", "Rating"), ("adr", "ADR"), ("kast", "KAST")]:
        d3 = delta3.get(f)
        if d3 is not None and d3 <= -2.0 and last3.get(f) is not None:
            next_focus.append({
                "type": "form_drop",
                "label": f"{label} dropped",
                "detail": f"{all_avg.get(f)} → {last3.get(f)} over last 3",
            })
            break

    return _nostore({
        "match_count": len(matches),
        "matches": matches,
        "players": players_out,
        "overview": {
            "top_maps": [{"map": r["map"], "count": r["count"]} for r in map_stats[:6]],
            "averages": all_avg,
            "form": {
                "last3": last3, "last5": last5, "all": all_avg, "delta3": delta3,
            },
            "map_stats": map_stats,
            "next_focus": next_focus[:3],
        },
        "utility": utility,
        "recurring": recurring,
        "roster_mode": roster_mode,
        "roster_members": roster_members,
    })


@app.route("/api/dashboard/analytics/match/<demo_id>")
def api_dashboard_analytics_match(demo_id):
    """Analytics-only view of one demo — no frames, no replay state. Cheaper than /api/demo/<id>."""
    mode, sc = _scope_or_block()
    if mode == "blocked":
        return _nostore({"error": "login required"}), 401
    if sc is not None and not db.accessible(demo_id, sc):
        return jsonify({"error": "no demo with that id"}), 404
    data = library.load_demo(CACHE, demo_id)
    if data is None:
        return jsonify({"error": "no demo with that id"}), 404
    analytics = data.get("analytics") or {}
    rounds_list = data.get("rounds") or []
    total_rounds = data.get("total_rounds") or len(rounds_list)
    return _nostore({
        "id": data.get("source_sha1") or demo_id,
        "key": demo_id,
        "map": data.get("map"),
        "score": data.get("score"),
        "rounds": total_rounds,
        "created_at": data.get("parsed_at") or "",
        "analytics": analytics,
    })


@app.route("/api/players")
def api_players():
    mode, sc = _scope_or_block()
    if mode == "blocked":
        return _nostore({"error": "login required"}), 401
    return _nostore(db.all_players(scope=sc))


@app.route("/api/squad", methods=["GET", "POST"])
def api_squad():
    """The user's auto-detected squad (teammates from 2+ shared matches) + manual add/remove.
    GET -> {available, you, squad:[{steamid,name,shared,pinned}], candidates:[{steamid,name,shared}]}.
    POST {steamid, name?, action:'add'|'remove'} curates it, then returns the refreshed view.
    Powers the Goals + Trends player pickers so they show your squad, not every player."""
    mode, sc = _scope_or_block()
    if mode == "blocked":
        return _nostore({"error": "login required"}), 401
    uid = sc.get("uid") if isinstance(sc, dict) else None
    if uid is None:                                    # local/open mode -> no account; pickers show all
        return _nostore({"available": False, "you": None, "squad": [], "candidates": []})
    if request.method == "POST":
        d = request.get_json(silent=True) or {}
        st = "in" if d.get("action") == "add" else ("out" if d.get("action") == "remove" else None)
        db.set_roster_entry(uid, d.get("steamid"), st, d.get("name"))
    you, detected = db.squad_for(uid, sc)
    ov = db.roster_overrides(uid)
    squad, candidates = [], []
    for p in detected:
        st = ov.get(p["steamid"])
        if st == "out":
            candidates.append(p)                       # auto-suggested but removed -> re-addable
        elif p["shared"] >= 2 or st == "in":
            squad.append({**p, "pinned": st == "in"})  # in the squad (auto >=2, or manually pinned)
        else:
            candidates.append(p)                       # played <2 together, not pinned -> suggestion
    return _nostore({"available": True, "you": you, "squad": squad, "candidates": candidates})


@app.route("/api/trends/<steamid>")
def api_trends(steamid):
    mode, sc = _scope_or_block()
    if mode == "blocked":
        return _nostore({"error": "login required"}), 401
    return _nostore(db.player_trends(steamid, scope=sc))


@app.route("/api/compare")
def api_compare():
    """Player-vs-player comparison over their cross-match trend averages (scoped). Pass ?a=<steamid>
    &b=<steamid>."""
    mode, sc = _scope_or_block()
    if mode == "blocked":
        return _nostore({"error": "login required"}), 401
    gate = require_feature("advancedAnalytics")             # comparison is a Pro analytics tool
    if gate:
        return gate
    sid_a = request.args.get("a") or None
    sid_b = request.args.get("b") or None
    if not sid_a or not sid_b:
        return _nostore({"error": "need a and b steamids"}), 400
    con = db.connect()
    try:
        return _nostore(compare.compare_from_trends(con, sid_a, sid_b, scope=sc))
    finally:
        con.close()


@app.route("/api/tendencies/<steamid>")
def api_tendencies(steamid):
    """Cross-match tendencies / repeated patterns for one player (anti-strat scouting)."""
    mode, sc = _scope_or_block()
    if mode == "blocked":
        return _nostore({"error": "login required"}), 401
    gate = require_feature("advancedAnalytics")             # anti-strat scouting is a Pro tool
    if gate:
        return gate
    matches = goals._matches(CACHE)
    if sc is not None:                                  # only count matches this user may see
        ok = db.visible_predicate(sc)
        matches = [m for m in matches if ok(m.get("sha"))]
    return _nostore(tendencies.cross_tendencies(matches, steamid))


@app.route("/api/sidestats")
def api_sidestats():
    """Own/team CT & T round-winrate per map across the caller's visible matches (map-pool / veto view).
    ?player=<steamid> scopes to that player's team; omit for the started-CT team. Pro analytics."""
    mode, sc = _scope_or_block()
    if mode == "blocked":
        return _nostore({"error": "login required"}), 401
    gate = require_feature("advancedAnalytics")
    if gate:
        return gate
    steamid = request.args.get("player") or None
    matches = goals._matches(CACHE)
    if sc is not None:
        ok = db.visible_predicate(sc)
        matches = [m for m in matches if ok(m.get("sha"))]
    pairs = [(m["analytics"], m.get("map")) for m in matches if isinstance(m.get("analytics"), dict)]
    out = sidestats.aggregate_side_winrates(pairs, steamid=steamid)
    ds = benchmarks.load_datasets()
    # tell the UI whether to even show the "vs Premier" overlay (hide-until-data)
    resp = {"maps": out,
            "benchmark_available": any(r.get("bucket_type") == "premier_ct_t_side_winrates" for r in ds)}
    bucket = request.args.get("bucket") or None              # public CT/T overlay (only when sourced)
    if bucket:
        region = request.args.get("region") or "all"
        pub, src = {}, None
        for mp in out:                                       # per-map overlay (needs a verified map-id mapping)
            b = benchmarks.ct_t_benchmark(mp, bucket, region=region, datasets=ds)
            if b:
                pub[mp] = {"ct_wr": b.get("ct_win_rate"), "t_wr": b.get("t_win_rate"), "games": b.get("sample_size")}
                src = src or b
        overall = benchmarks.ct_t_benchmark("all", bucket, region=region, datasets=ds)  # all-maps avg (always reliable)
        if pub or overall:                                   # honest: omit entirely if nothing sourced
            src = src or overall
            resp["benchmark"] = {"bucket": bucket, "maps": pub,
                                 "overall": ({"ct_wr": overall.get("ct_win_rate"), "t_wr": overall.get("t_win_rate"),
                                              "games": overall.get("sample_size")} if overall else None),
                                 "source_name": (src or {}).get("source_name"),
                                 "source_date": (src or {}).get("source_date"),
                                 "source_url": getattr(benchmarks, "LEETIFY_URL", None),
                                 "attribution": getattr(benchmarks, "ATTRIBUTION_TEXT", None)}
    return _nostore(resp)


@app.route("/api/benchmarks/compare")
def api_benchmarks_compare():
    """Compare a player's cross-match averages against a sourced skill-bucket benchmark dataset.
    Returns honest 'unavailable' (never a guessed number) when no sourced dataset covers a metric.
    ?player=<steamid>&type=premier_rating|faceit_level&bucket=<label> (or &rating= / &elo= to derive)."""
    mode, sc = _scope_or_block()
    if mode == "blocked":
        return _nostore({"error": "login required"}), 401
    gate = require_feature("advancedAnalytics")
    if gate:
        return gate
    steamid = request.args.get("player") or None
    btype = request.args.get("type") or "premier_rating"
    bucket = request.args.get("bucket") or None
    def _int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    if not bucket:                                       # derive the bucket from a raw rating/elo if given
        if btype == "faceit_level" and request.args.get("elo"):
            lvl = benchmarks.faceit_level(_int(request.args.get("elo")))
            bucket = str(lvl) if lvl else None
        elif request.args.get("rating"):
            bucket = benchmarks.premier_bucket(_int(request.args.get("rating")))
    stats = {}
    if steamid:
        tr = db.player_trends(steamid, scope=sc) or {}
        stats = tr.get("averages") or {}
    region = request.args.get("region") or "all"
    map_filter = request.args.get("map") or "all"
    if not bucket:
        return _nostore({"available": False, "reason": "no bucket selected", "metrics": []})
    # Bridge the player's analytics perf fields (he_dmg_per_he, headshot_accuracy, utility throws,
    # flash metrics) into Leetify metric keys via the CURATED-SAFE map, so they compare against the
    # premier_rating/faceit_level perf datasets. Only definitionally-compatible pairs are bridged
    # (benchmarks.PERF_COMPARE_MAP); unsafe metrics stay unavailable. Merge keeps any direct-key
    # comparisons (adr/kast/...) working too.
    stats = {**stats, **benchmarks.perf_player_vals(stats)}
    return _nostore(benchmarks.compare(stats, btype, bucket, region=region, map_filter=map_filter))


def _perf_keep_safe(res):
    """Keep only metrics that are actually bridgeable (in PERF_COMPARE_MAP) in a perf-compare result,
    in place. Unsafe metrics (counter-strafe, spotted/spray/preaim/reaction) would otherwise show a
    benchmark with a permanent player '—' -- noise that reads as broken. The CT/T + admin compare
    endpoints are unaffected; this only trims the dedicated perf panel feed."""
    safe = set(benchmarks.PERF_COMPARE_MAP.values())
    if isinstance(res, dict) and isinstance(res.get("metrics"), list):
        res["metrics"] = [m for m in res["metrics"] if m.get("metric") in safe]


def _resolve_bucket(btype):
    """Bucket label from ?bucket=, else derived from ?rating= / ?elo=. Shared by perf endpoints."""
    bucket = request.args.get("bucket") or None
    if bucket:
        return bucket

    def _int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    if btype == "faceit_level" and request.args.get("elo"):
        lvl = benchmarks.faceit_level(_int(request.args.get("elo")))
        return str(lvl) if lvl else None
    if request.args.get("rating"):
        return benchmarks.premier_bucket(_int(request.args.get("rating")))
    return None


@app.route("/api/benchmarks/perf")
def api_benchmarks_perf():
    """Performance-metric comparison vs a skill bucket, two windows:
      * ?key=<demo>&player=<sid>      -> THIS match's perf for that player (Player tab)
      * ?recent=<N>&player=<sid>      -> average of the player's last N matches (Dashboard; default 15)
    Bridges only the curated-safe player->Leetify keys (benchmarks.PERF_COMPARE_MAP), so unsafe metrics
    (counter-strafe, spotted/spray/preaim/reaction, hs-kill%) surface as 'unavailable', never a wrong
    delta. ?type=premier_rating|faceit_level + ?bucket= (or ?rating=/?elo=). NO fake data: a metric with
    no sourced benchmark OR no player sample is 'unavailable'."""
    mode, sc = _scope_or_block()
    if mode == "blocked":
        return _nostore({"error": "login required"}), 401
    gate = require_feature("advancedAnalytics")
    if gate:
        return gate
    btype = request.args.get("type") or "premier_rating"
    bucket = _resolve_bucket(btype)
    region = request.args.get("region") or "all"
    steamid = request.args.get("player") or None
    if not steamid:
        return _nostore({"available": False, "reason": "no player selected", "metrics": []})
    if not bucket:
        return _nostore({"available": False, "reason": "no bucket selected", "metrics": []})

    key = request.args.get("key") or None
    if key:                                              # single-match window (Player tab)
        if sc is not None and not db.accessible(key, sc):
            return _nostore({"error": "no demo with that id"}), 404
        data = library.load_demo(CACHE, key)
        if data is None:
            return _nostore({"error": "no demo with that id"}), 404
        players = (data.get("analytics") or {}).get("players") or []
        p = next((pp for pp in players if str(pp.get("steamid")) == str(steamid)), None)
        if p is None:
            return _nostore({"available": False, "reason": "player not in this match", "metrics": []})
        res = benchmarks.perf_compare(p, btype, bucket, region=region)
        _perf_keep_safe(res)                             # only show bridgeable metrics (no permanent "—" rows)
        q = p.get("perf_quality") or {}                  # quality keys are the Leetify field minus "avg_"
        for row in res.get("metrics", []):
            qk = row["metric"][4:] if row["metric"].startswith("avg_") else row["metric"]
            row["sample_size"] = (q.get(qk) or {}).get("sample_size")
        res["window"] = {"mode": "match", "n": 1}
        return _nostore(res)

    # recent-average window (Dashboard): average the player's last N matches, no-fake-data (NULL-skipping)
    try:
        recent = max(1, min(int(request.args.get("recent") or 15), 100))
    except (TypeError, ValueError):
        recent = 15
    avg = db.player_perf_averages(steamid, scope=sc, limit=recent)
    res = benchmarks.perf_compare(avg.get("averages") or {}, btype, bucket, region=region)
    _perf_keep_safe(res)                                 # only show bridgeable metrics (no permanent "—" rows)
    counts = avg.get("counts") or {}                     # per-metric # of matches that carried the value
    rev = {v: k for k, v in benchmarks.PERF_COMPARE_MAP.items()}
    for row in res.get("metrics", []):
        local = rev.get(row["metric"])
        row["sample_size"] = counts.get(local) if local else None
    res["window"] = {"mode": "recent", "requested": recent, "n": avg.get("window", 0)}
    return _nostore(res)


@app.route("/api/benchmarks/status")
def api_benchmarks_status():
    """Whether a sourced benchmark dataset is loaded (drives hide-until-data in the UI) + the provenance
    to render the required visible attribution. Cheap; safe for any signed-in user."""
    mode, _ = _scope_or_block()
    if mode == "blocked":
        return _nostore({"error": "login required"}), 401
    ds = benchmarks.load_datasets()
    types = sorted({r.get("bucket_type") for r in ds if r.get("bucket_type")})
    src = next((r for r in ds if r.get("source_name")), {})
    return _nostore({"available": bool(ds), "bucket_types": types,
                     "source_name": src.get("source_name"), "source_date": src.get("source_date"),
                     "source_url": getattr(benchmarks, "LEETIFY_URL", None) or src.get("source_url"),
                     "attribution": getattr(benchmarks, "ATTRIBUTION_TEXT", None) or src.get("attribution")})


def _parse_benchmark_rows(rows, kind, body):
    """Dispatch sourced rows to the right benchmarks parser by kind. Raises ValueError on empty result."""
    common = dict(source_url=body.get("source_url"), source_date=body.get("source_date"),
                  source_name=body.get("source_name", "Leetify"))
    if kind == "premier-ct-t-side-winrates":
        emap = body.get("map_ids") if isinstance(body.get("map_ids"), dict) else None
        records = benchmarks.parse_leetify_ct_t(rows, extra_map_ids=emap, **common)
    else:
        records = benchmarks.parse_leetify_pdl(rows, kind=kind,
                                               aggregate_premier=bool(body.get("aggregate_premier", True)), **common)
    if not records:
        raise ValueError("no usable benchmark rows found (check the source/kind)")
    return records


def _save_benchmark_records(u, records, body):
    fname = (str(body.get("source_name", "leetify")) + "_" + str(body.get("kind", "")) + "_"
             + str(body.get("source_date") or "import")).lower().replace(" ", "_").replace("/", "-") + ".json"
    path = benchmarks.save_datasets(records, fname)
    db.log_admin_action(u.get("id"), "benchmarks_import", "benchmark",
                        os.path.basename(path), {"records": len(records), "kind": body.get("kind")})
    return _nostore({"ok": True, "records": len(records), "file": os.path.basename(path)})


@app.route("/api/admin/benchmarks/fetch", methods=["POST"])
def api_admin_benchmarks_fetch():
    """Admin-triggered ONE-SHOT pull of a dated public snapshot, then store + attribute it. Not a
    background job -- the admin clicks it. Body: {date: YYYY-MM-DD, kind, platform}."""
    u = _admin_or_none()
    if u is None:
        return _nostore({"error": "admin only"}), 403
    body = request.get_json(silent=True) or {}
    date = (body.get("date") or "").strip()
    kind = body.get("kind", "performance-metric-tool")
    platform = body.get("platform", "premier")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        return _nostore({"error": "date must be YYYY-MM-DD (e.g. 2026-03-01)"}), 400
    try:
        rows = benchmarks.fetch_leetify(date, kind=kind, platform=platform)
    except Exception as e:
        return _nostore({"error": f"fetch failed: {e}"}), 502
    body.setdefault("source_date", date)
    body.setdefault("source_url", benchmarks.LEETIFY_URL)
    try:
        return _save_benchmark_records(u, _parse_benchmark_rows(rows, kind, body), body)
    except ValueError as e:
        return _nostore({"error": str(e)}), 400


@app.route("/api/admin/benchmarks", methods=["GET", "POST"])
def api_admin_benchmarks():
    """Admin: list loaded benchmark datasets, or import a sourced one. Import accepts the raw rows from a
    sourced provider (e.g. a Leetify public-data-library response) + provenance; values are normalized,
    attributed, and stored. No fake data: unsupported metrics are simply never emitted."""
    u = _admin_or_none()
    if u is None:
        return _nostore({"error": "admin only"}), 403
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        rows = body.get("rows")
        if not isinstance(rows, list) or not rows:
            return _nostore({"error": "need a non-empty 'rows' array from a sourced dataset"}), 400
        try:
            records = _parse_benchmark_rows(rows, body.get("kind", "performance-metric-tool"), body)
            return _save_benchmark_records(u, records, body)
        except ValueError as e:
            return _nostore({"error": str(e)}), 400
        except Exception as e:
            return _nostore({"error": f"import failed: {e}"}), 400
    # GET: a compact provenance summary of what's loaded (for the admin status panel)
    ds = benchmarks.load_datasets()
    summary = {}
    for r in ds:
        k = (r.get("source_name"), r.get("source_date"), r.get("bucket_type"))
        summary.setdefault(str(k), {"source_name": r.get("source_name"), "source_date": r.get("source_date"),
                                    "source_url": r.get("source_url"), "bucket_type": r.get("bucket_type"),
                                    "buckets": 0})
        summary[str(k)]["buckets"] += 1
    return _nostore({"datasets": list(summary.values()), "total_records": len(ds)})


@app.route("/api/admin/benchmarks/manual", methods=["GET", "POST"])
def api_admin_benchmarks_manual():
    """Admin: hand-enter Premier CT/T side win rates for one rating bucket across maps — no provider
    fetch needed (CT/T data barely moves, so typing it once is fine). GET ?bucket=&region= returns the
    values already stored for that bucket so the form can prefill for editing. POST
    {source_name, source_date, region, bucket, rows:[{map,ct,t,games}]} builds the SAME records
    parse_leetify_ct_t emits (map names pass straight through; "all" = all-maps average) and stores them
    WITH attribution, exactly like an import. Each (bucket,region) is its own file, so re-saving a bucket
    cleanly replaces it and different buckets coexist. Values are transcribed by the admin from a real,
    named source and attributed wherever they appear — this is a friendlier import path, not fake data."""
    u = _admin_or_none()
    if u is None:
        return _nostore({"error": "admin only"}), 403

    def _f(v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if (f == f and f not in (float("inf"), float("-inf"))) else None

    if request.method == "GET":
        bucket = (request.args.get("bucket") or "").strip()
        region = (request.args.get("region") or "all").strip() or "all"
        rows = {}
        if bucket:
            for r in benchmarks.load_datasets():
                if (r.get("bucket_type") == "premier_ct_t_side_winrates"
                        and str(r.get("bucket")) == bucket
                        and str(r.get("region") or "all").lower() == region.lower()):
                    m = r.get("metrics") or {}
                    rows[str(r.get("map_filter"))] = {"ct": m.get("ct_win_rate"), "t": m.get("t_win_rate"),
                                                      "games": r.get("sample_size")}
        return _nostore({"bucket": bucket, "region": region, "rows": rows,
                         "source_name": "Leetify", "source_url": benchmarks.LEETIFY_URL})

    body = request.get_json(silent=True) or {}
    bucket = str(body.get("bucket") or "").strip()
    if not bucket:
        return _nostore({"error": "pick a rating bucket (e.g. 15k-20k)"}), 400
    region = str(body.get("region") or "all").strip() or "all"
    rows_in = body.get("rows")
    if not isinstance(rows_in, list) or not rows_in:
        return _nostore({"error": "need a non-empty 'rows' array"}), 400
    # Re-shape the hand-typed rows into the canonical Leetify CT/T row shape, then let the tested parser
    # normalize/validate them. A row with neither CT nor T entered is skipped (never a fabricated 0).
    leetify_rows = []
    for r in rows_in:
        if not isinstance(r, dict):
            continue
        mp = str(r.get("map") or "").strip().lower()
        if not mp:
            continue
        ct, t = _f(r.get("ct")), _f(r.get("t"))
        if ct is None and t is None:
            continue
        row = {"game_map_id": mp, "rating_bucket": bucket, "region": region}
        if ct is not None:
            row["avg_ct_win_rate"] = ct
        if t is not None:
            row["avg_t_win_rate"] = t
        g = _f(r.get("games"))
        if g is not None and g > 0:
            row["total_games"] = int(g)
        leetify_rows.append(row)
    if not leetify_rows:
        return _nostore({"error": "enter at least one map's CT or T win %"}), 400
    records = benchmarks.parse_leetify_ct_t(
        leetify_rows,
        source_url=(str(body.get("source_url") or "").strip() or benchmarks.LEETIFY_URL),
        source_date=(str(body.get("source_date") or "").strip() or None),
        source_name=(str(body.get("source_name") or "").strip() or "Leetify"))
    if not records:
        return _nostore({"error": "no usable rows (check map names and values)"}), 400
    safe = lambda s: (re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "x")
    fname = "manual_premier_ct_t_%s_%s.json" % (safe(bucket), safe(region))
    path = benchmarks.save_datasets(records, fname)
    db.log_admin_action(u.get("id"), "benchmarks_manual", "benchmark",
                        os.path.basename(path), {"records": len(records), "bucket": bucket, "region": region})
    return _nostore({"ok": True, "records": len(records), "file": os.path.basename(path)})


@app.route("/api/admin/benchmarks/perf-manual", methods=["GET", "POST"])
def api_admin_benchmarks_perf_manual():
    """Admin: hand-enter Performance-Metric benchmark values for one skill bucket — the fallback for when
    the Leetify fetch isn't working. GET ?platform=&bucket=&region= returns the values already stored so
    the form can prefill for editing. POST {platform, bucket, region, source_name, source_date, metrics}
    keeps ONLY the documented Leetify metric keys (blank/garbage dropped, never a fabricated 0), builds
    the SAME record parse_leetify_pdl emits, and stores it WITH attribution. ``platform`` premier ->
    bucket_type premier_rating (bucket like "15k-20k"); faceit -> faceit_level (bucket = level 1-10).
    One file per (bucket_type,bucket,region) so re-saving replaces cleanly and buckets coexist."""
    u = _admin_or_none()
    if u is None:
        return _nostore({"error": "admin only"}), 403

    def _btype(platform):
        return "faceit_level" if str(platform or "").lower() == "faceit" else "premier_rating"

    if request.method == "GET":
        bucket = str(request.args.get("bucket") or "").strip()
        region = (request.args.get("region") or "all").strip() or "all"
        btype = _btype(request.args.get("platform"))
        metrics, sample_size = {}, None
        if bucket:
            for r in benchmarks.load_datasets():
                if (r.get("bucket_type") == btype and str(r.get("bucket")) == bucket
                        and str(r.get("region") or "all").lower() == region.lower()):
                    metrics = r.get("metrics") or {}
                    sample_size = r.get("sample_size")
                    break
        return _nostore({"platform": request.args.get("platform") or "premier", "bucket": bucket,
                         "region": region, "metrics": metrics, "sample_size": sample_size,
                         "fields": list(benchmarks.LEETIFY_METRIC_FIELDS),
                         "source_name": "Leetify", "source_url": benchmarks.LEETIFY_URL})

    body = request.get_json(silent=True) or {}
    bucket = str(body.get("bucket") or "").strip()
    if not bucket:
        return _nostore({"error": "pick a bucket (Premier band like 15k-20k, or FACEIT level 1-10)"}), 400
    region = str(body.get("region") or "all").strip() or "all"
    btype = _btype(body.get("platform"))
    metrics_in = body.get("metrics")
    if not isinstance(metrics_in, dict) or not metrics_in:
        return _nostore({"error": "need a 'metrics' object"}), 400
    rec = benchmarks.manual_perf_record(
        btype, bucket, metrics_in, region=region,
        source_name=(str(body.get("source_name") or "").strip() or "Leetify"),
        source_date=(str(body.get("source_date") or "").strip() or None),
        source_url=(str(body.get("source_url") or "").strip() or benchmarks.LEETIFY_URL),
        sample_size=body.get("sample_size"))
    if rec is None:
        return _nostore({"error": "enter at least one metric value"}), 400
    safe = lambda s: (re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "x")
    fname = "manual_perf_%s_%s_%s.json" % (safe(btype), safe(bucket), safe(region))
    path = benchmarks.save_datasets([rec], fname)
    db.log_admin_action(u.get("id"), "benchmarks_perf_manual", "benchmark", os.path.basename(path),
                        {"metrics": len(rec.get("metrics") or {}), "bucket_type": btype, "bucket": bucket})
    return _nostore({"ok": True, "metrics": len(rec.get("metrics") or {}), "file": os.path.basename(path)})


@app.route("/api/playbook", methods=["GET"])
def playbook_list():
    """Team plays, optionally filtered to ?map=de_xxx. Adherence is checked client-side."""
    mp = request.args.get("map") or ""
    return _nostore({"plays": playbook.plays_for(mp) if mp else playbook.load_all()["plays"]})


@app.route("/api/playbook", methods=["POST"])
def playbook_add():
    data = request.get_json(silent=True) or {}
    if not data.get("map"):
        return jsonify({"error": "map required"}), 400
    return _nostore(playbook.add_play(data))


@app.route("/api/playbook/<pid>", methods=["DELETE"])
def playbook_delete(pid):
    return jsonify({"deleted": playbook.delete_play(pid)})


@app.route("/api/team", methods=["GET"])
def api_team_get():
    return _nostore(teams.load_team())


@app.route("/api/team", methods=["POST"])
def api_team_post():
    return _nostore(teams.save_team(request.get_json(silent=True) or {}))


@app.route("/api/practice", methods=["GET"])
def api_practice_get():
    return _nostore(practiceplan.load_done())


@app.route("/api/practice", methods=["POST"])
def api_practice_post():
    d = request.get_json(silent=True) or {}
    if not d.get("id"):
        return jsonify({"error": "need an item id"}), 400
    return _nostore(practiceplan.set_done(d["id"], bool(d.get("done"))))


# ---- nade library -----------------------------------------------------------
@app.route("/api/nades", methods=["GET"])
def nades_list():
    resp = jsonify(nades.load_library())
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/nades", methods=["POST"])
def nades_add():
    data = request.get_json(silent=True) or {}
    if not data.get("map") or not data.get("type"):
        return jsonify({"error": "a lineup needs at least 'map' and 'type'"}), 400
    return jsonify(nades.add_nade(data))


@app.route("/api/nades/<nid>", methods=["PUT"])
def nades_update(nid):
    data = request.get_json(silent=True) or {}
    if not data.get("map") or not data.get("type"):
        return jsonify({"error": "a lineup needs at least 'map' and 'type'"}), 400
    n = nades.update_nade(nid, data)
    if n is None:
        return jsonify({"error": "no lineup with that id"}), 404
    return jsonify(n)


@app.route("/api/nades/<nid>/favorite", methods=["POST"])
def nades_favorite(nid):
    data = request.get_json(silent=True) or {}
    ok = nades.set_favorite(nid, bool(data.get("favorite")))
    return (jsonify({"id": nid, "favorite": bool(data.get("favorite"))})
            if ok else (jsonify({"error": "no lineup with that id"}), 404))


@app.route("/api/nades/<nid>", methods=["DELETE"])
def nades_delete(nid):
    return jsonify({"deleted": nades.delete_nade(nid)})


@app.route("/api/nades/suggest")
def nades_suggest():
    """#61: repeatedly-thrown utility -> promote candidates. A spot thrown 3+ times (min_matches=1,
    so a SINGLE demo qualifies) is enough to surface -- the user just wants to SEE what was thrown on
    this map and one-click add it, not wait for the same lineup to recur across multiple demos.
    SECURITY: scoped to the caller's own/team demos so one user's grenade positions never leak into
    another's suggestions (the global cache scan used to ignore ownership)."""
    blocked = require_auth_when_locked()
    if blocked:
        return blocked
    mode, sc = _scope_or_block()
    if mode == "blocked":
        return _nostore({"error": "login required"}), 401
    player = request.args.get("player") or None
    mp = request.args.get("map") or None
    sha = request.args.get("sha") or None      # restrict to the demo being watched (not other demos on the map)
    allow = None
    if mode == "scoped":                        # logged-in user: never scan beyond what they can see
        if sha:
            if not db.accessible(sha, sc):      # asked for a demo they don't own/share -> nothing
                return _nostore({"total": 0, "map": mp, "sha": sha, "suggestions": []})
        else:
            allow = set(db.library_membership(sc).keys())   # only this user's/team's demos
    # min_throws=2 for the single-demo view: a spot thrown TWICE in one match is already a repeatable
    # setup worth one-click-adding. (3+ across a single demo almost never happens, so suggestions used
    # to come back empty on real demos -- the sample only filled up because it's a synthetic match.)
    cl = nadeclusters.find_consistent(CACHE, steamid=player, map_filter=mp,
                                      min_throws=2, min_matches=1, only_sha=sha, allow_shas=allow)
    return _nostore({"total": len(cl), "map": mp, "sha": sha,
                     "suggestions": [dict(c, nade=nadeclusters.to_nade(c)) for c in cl[:40]]})


@app.route("/nades/videos/<path:fn>")
def nade_video(fn):
    blocked = require_auth_when_locked()      # SECURITY: user-uploaded clips aren't public on a locked site
    if blocked:
        return blocked
    return send_from_directory(nades.VIDEOS_DIR, fn)


@app.route("/api/nades/video", methods=["POST"])
def nade_video_upload():
    blocked = require_auth_when_locked()
    if blocked:
        return blocked
    f = request.files.get("video")
    if not f or not f.filename:
        return jsonify({"error": "no video uploaded (field 'video')"}), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in nades.VIDEO_EXTS:
        return jsonify({"error": "use an mp4/webm/mov/m4v clip",
                        "hint": "record the throw + landing as a short clip"}), 400
    os.makedirs(nades.VIDEOS_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="_vid_", suffix=ext, dir=nades.VIDEOS_DIR)
    os.close(fd)
    try:
        f.save(tmp)
        mb = os.path.getsize(tmp) / (1024 * 1024)
        if mb > MAX_VIDEO_UPLOAD_MB:
            return jsonify({"error": f"clip is {mb:.0f} MB; limit is {MAX_VIDEO_UPLOAD_MB} MB",
                            "hint": "trim it to just the throw + landing, or lower the quality"}), 413
        name = _sha1_file(tmp)[:16] + ext          # content-addressed -> identical clips dedupe
        os.replace(tmp, os.path.join(nades.VIDEOS_DIR, name))
        return jsonify({"url": "/nades/videos/" + name})
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


@app.route("/api/nades/import", methods=["POST"])
def nades_import():
    data = request.get_json(silent=True)
    items = data if isinstance(data, list) else (data or {}).get("nades", [])
    if not isinstance(items, list) or not items:
        return jsonify({"error": "expected a JSON array of lineups, or {\"nades\": [...]}",
                        "hint": "CSNADES-style fields are accepted (map, type, side, from/to, "
                                "throw_pos/land_pos, technique, video, ...)"}), 400
    src = data.get("source") if isinstance(data, dict) else None
    added, total = nades.import_nades(items, source=src or "csnades-import")
    return jsonify({"added": added, "total": total})


# ---- auth routes (Stage 4: Steam OpenID 2.0) --------------------------------
@app.route("/login/steam")
def login_steam():
    """Kick off Steam login -- redirect the browser to Steam's OpenID endpoint."""
    base = steamauth.public_base_url(request.url_root)
    return redirect(steamauth.login_url(base))


@app.route("/auth/steam/callback")
def steam_callback():
    """Steam redirects here after login; verify the assertion and start a session."""
    base = steamauth.public_base_url(request.url_root)
    steamid = steamauth.verify(request.args,
                               expected_return_prefix=base + steamauth.CALLBACK_PATH)
    if not steamid:
        return _nostore({"error": "Steam login failed or was cancelled"}), 400
    prof = steamauth.fetch_profile(steamid)            # {} without STEAM_API_KEY -- login still works
    uid = db.upsert_user(steamid, prof.get("name"), prof.get("avatar"))
    session["uid"] = uid
    session.permanent = True
    return redirect("/")


@app.route("/api/me")
def api_me():
    """Current auth state for the frontend. Pure-local mode: auth_enabled=False + a synthetic 'local'
    user (admin/Pro). Auth-enabled + signed out: an explicit ANONYMOUS user struct (is_admin=False,
    is_helper=False, tier=free, authenticated=False) -- never the privileged local user."""
    u = current_user()
    # auth-enabled visitor with no session -> expose an explicit anon identity, not the local owner.
    view = u if u is not None else (anon_user() if steamauth.auth_enabled() else None)
    teams = db.teams_for_user(u["id"]) if (u and u.get("id")) else []
    return _nostore({"authenticated": bool(u and u.get("id")),
                     "auth_enabled": steamauth.auth_enabled(),
                     "auth_required": steamauth.auth_required(),
                     "user": view, "teams": teams,
                     "is_admin": is_admin(u), "is_helper": is_helper(u), "tier": tier_of(u),
                     "entitlements": entitlements(u), "tiers_enabled": TIERS_ENABLED,
                     "upload_quota": upload_allowance(u), "pricing": pricing.public_plans(),
                     "billing_enabled": billing.enabled(),  # frontend: real Checkout vs "not live yet"
                     "support_contact": os.environ.get("SUPPORT_CONTACT", "")})


# ---- billing (Stripe Checkout + Customer Portal + webhook) -----------------------------------
@app.route("/api/billing/checkout", methods=["POST"])
def api_billing_checkout():
    """Start a Stripe Checkout (subscription) for the signed-in user on the chosen period -> {url} to
    redirect to. 503 if billing isn't configured, 401 if not signed in."""
    if not billing.enabled():
        return _nostore({"error": "Billing isn't enabled yet."}), 503
    u = current_user()
    if not u or not u.get("id"):
        return _nostore({"error": "Sign in to subscribe."}), 401
    period = (request.get_json(silent=True) or {}).get("period", "monthly")
    if period not in billing.LOOKUP:
        return _nostore({"error": "Unknown plan."}), 400
    try:
        url = billing.create_checkout_session(u, period, steamauth.public_base_url(request.url_root))
    except Exception:
        traceback.print_exc()
        return _nostore({"error": "Could not start checkout."}), 502
    if not url:
        return _nostore({"error": "That plan isn't available right now."}), 502
    return _nostore({"url": url})


@app.route("/api/billing/portal", methods=["POST"])
def api_billing_portal():
    """Open the Stripe Customer Portal (self-serve cancel/switch) for the signed-in user -> {url}.
    400 if they have no Stripe customer yet (never subscribed)."""
    if not billing.enabled():
        return _nostore({"error": "Billing isn't enabled yet."}), 503
    u = current_user()
    if not u or not u.get("id"):
        return _nostore({"error": "Sign in first."}), 401
    try:
        url = billing.create_portal_session(u, steamauth.public_base_url(request.url_root))
    except Exception:
        traceback.print_exc()
        return _nostore({"error": "Could not open the billing portal."}), 502
    if not url:
        return _nostore({"error": "No subscription to manage yet."}), 400
    return _nostore({"url": url})


@app.route("/api/stripe/webhook", methods=["POST"])
def api_stripe_webhook():
    """Stripe -> us. Verify the signature (that IS the auth -- server-to-server, no login/scope), then
    apply the event (grant/revoke Pro). 200 on handled/ignored so Stripe stops retrying; 400 only on a
    bad signature; 500 lets Stripe retry a transient handler failure."""
    payload = request.get_data()
    sig = request.headers.get("Stripe-Signature", "")
    try:
        event = billing.verify_event(payload, sig)
    except Exception as e:
        print(f"[stripe] rejected webhook: {e}")
        return _nostore({"error": "bad signature"}), 400
    try:
        billing.apply_event(event)
    except Exception:
        traceback.print_exc()
        return _nostore({"error": "handler error"}), 500
    return _nostore({"received": True})


# ---- admin panel (gated to ADMIN_STEAM_IDS / local owner; helpers get read + grant-Pro) ------
def _admin_or_none():
    u = current_user()
    return u if is_admin(u) else None


def _helper_or_none():
    u = current_user()
    return u if is_helper(u) else None


@app.route("/api/admin/overview")
def api_admin_overview():
    """Instance-wide counts + storage for the admin panel."""
    if not _helper_or_none():
        return _nostore({"error": "admin only"}), 403
    ov = db.admin_overview()
    ov["storage"] = {"cache_bytes": _dir_bytes(CACHE), "uploads_bytes": _dir_bytes(UPLOADS),
                     "nades_bytes": _dir_bytes(nades.LIB_DIR) if hasattr(nades, "LIB_DIR") else 0}
    ov["tiers_enabled"] = TIERS_ENABLED
    try:
        ov["maps3d"] = sum(1 for m in mapstatus.map_status() if m.get("glb_present"))
    except Exception:
        ov["maps3d"] = None
    # live config readout so the admin can confirm the deployment without SSHing in
    ov["config"] = {
        "tiers_enabled": TIERS_ENABLED, "free_upload_limit": appconfig.free_upload_limit(),
        "auth_required": steamauth.auth_required(), "auth_enabled": steamauth.auth_enabled(),
        "keep_dem": KEEP_DEM, "session_cookie_secure": bool(app.config.get("SESSION_COOKIE_SECURE")),
        "public_base_url": os.environ.get("PUBLIC_BASE_URL") or "(inferred from request)",
        "steam_api_key": bool(os.environ.get("STEAM_API_KEY")), "admins": len(_admin_ids()),
        "schema_version": SCHEMA_VERSION, "analytics_version": ANALYTICS_VERSION,
    }
    return _nostore(ov)


@app.route("/api/admin/ops")
def api_admin_ops():
    """Ops view: where storage is going + upload/parse timing (derived from the jobs table -- the
    created/started/finished timestamps are already recorded, so no extra logging is needed)."""
    import datetime as _dt
    import shutil
    if not _helper_or_none():
        return _nostore({"error": "admin only"}), 403
    cats = [
        ("Parsed demo cache", CACHE),
        ("Raw uploads (.dem)", UPLOADS),
        ("Nade library + clips", getattr(nades, "LIB_DIR", os.path.join(HERE, "nades"))),
        ("3D map geometry", os.path.join(HERE, "static", "maps3d")),
        ("Radars + images", os.path.join(HERE, "static", "maps")),
    ]
    storage = [{"label": lbl, "bytes": _dir_bytes(p)} for lbl, p in cats]
    dbb = 0
    for ext in ("", "-wal", "-shm"):
        try:
            dbb += os.path.getsize(db.DB_PATH + ext)
        except OSError:
            pass
    storage.append({"label": "Database (SQLite)", "bytes": dbb})
    storage.sort(key=lambda s: -s["bytes"])
    disk = {}
    try:
        du = shutil.disk_usage(os.path.dirname(db.DB_PATH) or HERE)   # the data-volume filesystem
        disk = {"total": du.total, "used": du.used, "free": du.free}
    except Exception:
        pass

    alljobs = jobs.list_jobs(limit=500)

    def _span(j, a, b):
        try:
            return round((_dt.datetime.fromisoformat(j[b]) - _dt.datetime.fromisoformat(j[a])).total_seconds(), 1)
        except Exception:
            return None
    _parse_s = lambda j: _span(j, "started_at", "finished_at")     # noqa: E731  (parse: claim -> done/fail)
    _queue_s = lambda j: _span(j, "created_at", "started_at")      # noqa: E731  (wait: enqueue -> claim)
    _upload_s = lambda j: round(j["upload_ms"] / 1000.0, 1) if j.get("upload_ms") is not None else None

    # owner display names for the "by whom" column on the failed-job drilldown (19B)
    owner_ids = {j["owner_user_id"] for j in alljobs if j.get("owner_user_id")}
    who = {}
    if owner_ids:
        con = db.connect()
        try:
            qs = ",".join("?" * len(owner_ids))
            for r in con.execute("SELECT id, display_name FROM users WHERE id IN (%s)" % qs, tuple(owner_ids)):
                who[r["id"]] = r["display_name"]
        finally:
            con.close()

    def _who(j):
        oid = j.get("owner_user_id")
        return (who.get(oid) or ("user #%s" % oid)) if oid else "local/legacy"

    def _agg(xs):
        xs = sorted(x for x in xs if x is not None and x >= 0)
        if not xs:
            return {"avg": None, "median": None, "min": None, "max": None, "n": 0}
        return {"avg": round(sum(xs) / len(xs), 1), "median": xs[len(xs) // 2],
                "min": xs[0], "max": xs[-1], "n": len(xs)}

    def _row(j):
        return {"id": j.get("id"), "filename": j.get("filename"), "status": j.get("status"),
                "who": _who(j), "created_at": j.get("created_at"), "bytes": j.get("bytes"),
                "upload_s": _upload_s(j), "queue_s": _queue_s(j), "parse_s": _parse_s(j)}
    done = [j for j in alljobs if j.get("status") == "done"]
    parse_agg = _agg([_parse_s(j) for j in done])
    failures = [{**_row(j), "finished_at": j.get("finished_at"),
                 "error": (j.get("error") or "").strip()[:2000]}
                for j in alljobs if j.get("status") == "failed"][:30]
    timing = {
        "parsed": parse_agg["n"],
        "failed": len(failures),
        "active": sum(1 for j in alljobs if j.get("status") in ("queued", "parsing", "analyzing")),
        "workers": jobs.WORKERS,
        "parse": parse_agg,
        "queue": _agg([_queue_s(j) for j in alljobs]),
        "upload": _agg([_upload_s(j) for j in alljobs]),
        # flat back-compat keys (parse duration) still read by the summary tiles
        "avg_s": parse_agg["avg"], "median_s": parse_agg["median"],
        "min_s": parse_agg["min"], "max_s": parse_agg["max"],
        "recent": [_row(j) for j in alljobs[:15]],
        "failures": failures,
    }
    return _nostore({"storage": storage, "storage_total": sum(s["bytes"] for s in storage),
                     "disk": disk, "timing": timing})


# ---- admin storage drilldown (read-only; never deletes) ----------------------
def _storage_categories():
    """Allowlisted storage categories -> (label, root_abs, kind). kind: 'dir' = walk it,
    'dbfiles' = the sqlite trio, 'volume_root' = the data volume top level (top-level entries only).
    Roots are resolved fresh each call so a test/env override of CACHE/db.DB_PATH/etc. is honored.
    NOTHING outside this map is ever scannable (unknown id -> 400)."""
    volume_root = os.path.dirname(db.DB_PATH) or HERE
    return {
        "parsed_cache": ("Parsed demo cache", CACHE, "dir"),
        "raw_uploads":  ("Raw uploads (.dem)", UPLOADS, "dir"),
        "maps3d":       ("3D map geometry", os.path.join(STATIC, "maps3d"), "dir"),
        "radars":       ("Radars + images", os.path.join(STATIC, "maps"), "dir"),
        "database":     ("Database (SQLite)", db.DB_PATH, "dbfiles"),
        "nades":        ("Nade library + clips", getattr(nades, "LIB_DIR", os.path.join(HERE, "nades")), "dir"),
        "app_root_other": ("App data volume (top level)", volume_root, "volume_root"),
    }


_STORAGE_MAX_ENTRIES = 50          # top-N largest entries returned
_STORAGE_MAX_DEPTH = 12            # hard recursion-depth cap when sizing a directory
_STORAGE_MAX_SCAN = 200_000        # hard cap on files visited while sizing one category (DoS guard)


def _within(root_real, path):
    """True iff `path` (after realpath) is inside `root_real` -- blocks symlink/.. escapes."""
    try:
        pr = os.path.realpath(path)
    except OSError:
        return False
    root_real = root_real.rstrip(os.sep)
    return pr == root_real or pr.startswith(root_real + os.sep)


def _safe_dir_size(path, root_real, notes, counter):
    """Recursively sum file sizes under `path`, staying within `root_real`. Skips symlinks (and any
    entry that resolves outside the root), bounds depth + total files visited, and records the first
    permission/oversize problem in `notes` instead of raising. Returns total bytes."""
    total = 0
    stack = [(path, 0)]
    while stack:
        cur, depth = stack.pop()
        if depth > _STORAGE_MAX_DEPTH:
            notes.append("Stopped at depth %d (too deep): %s" % (_STORAGE_MAX_DEPTH, os.path.basename(cur)))
            continue
        try:
            with os.scandir(cur) as it:
                for e in it:
                    if counter[0] >= _STORAGE_MAX_SCAN:
                        notes.append("Stopped after %d files (too many to scan)." % _STORAGE_MAX_SCAN)
                        return total
                    counter[0] += 1
                    try:
                        if e.is_symlink():
                            continue                    # never follow links (escape/loop guard)
                        if not _within(root_real, e.path):
                            continue                    # resolved outside the root -> skip
                        if e.is_dir(follow_symlinks=False):
                            stack.append((e.path, depth + 1))
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue                        # unreadable single entry -> skip
        except PermissionError:
            notes.append("Permission denied reading a folder; its size may be undercounted.")
        except OSError:
            pass
    return total


def _storage_overview():
    """Category sizes + the whole-volume disk usage, with the unexplained (non-app) remainder."""
    import shutil
    cats_def = _storage_categories()
    cats, app_total = [], 0
    for cid, (label, root, kind) in cats_def.items():
        notes, counter = [], [0]
        if kind == "dbfiles":
            b = 0
            for ext in ("", "-wal", "-shm"):
                try:
                    b += os.path.getsize(root + ext)
                except OSError:
                    pass
        else:
            rr = os.path.realpath(root)
            b = _safe_dir_size(root, rr, notes, counter) if os.path.isdir(root) else 0
        # app_root_other is the volume root that already CONTAINS cache/uploads/db etc., so counting it
        # into app_total would double-count; it's reported as a category but excluded from the sum.
        if cid != "app_root_other":
            app_total += b
        cats.append({"id": cid, "label": label, "bytes": b})
    cats.sort(key=lambda c: -c["bytes"])
    volume = {}
    try:
        du = shutil.disk_usage(os.path.dirname(db.DB_PATH) or HERE)
        volume = {"used": du.used, "free": du.free, "total": du.total}
    except Exception:
        volume = {"used": None, "free": None, "total": None}
    used = volume.get("used")
    unexplained = (used - app_total) if isinstance(used, int) and used >= app_total else None
    return {
        "volume": volume, "app_data_total": app_total, "categories": cats,
        "unexplained_bytes": unexplained,
        "note": ("App data total counts known CS2DemoPlayer folders. Disk used is the whole HOST "
                 "volume, most of which lives OUTSIDE this app container and can't be itemized from "
                 "here. On this Docker deploy the unexplained portion is almost always Docker BUILD "
                 "CACHE (it grows on every `--build` deploy) plus images/OS. Reclaim it on the host "
                 "with `docker builder prune -af` (safe — build cache regenerates), then "
                 "`docker image prune -f`."),
    }


def _storage_detail(category):
    """Top-N largest entries within one allowlisted category (files + immediate dirs), read-only.
    Realpath-guarded so a resolved entry can never escape the category root; symlinks are skipped;
    depth + entry counts are bounded; permission/size errors land in `notes`, never an exception.
    Absolute paths are NOT exposed for non-root categories (only relative names + a root_label)."""
    cats_def = _storage_categories()
    label, root, kind = cats_def[category]
    notes, entries, total, file_count, truncated = [], [], 0, 0, False
    root_label = label

    if kind == "dbfiles":
        for ext in ("", "-wal", "-shm"):
            fp = root + ext
            try:
                st = os.stat(fp)
            except OSError:
                continue
            entries.append({"name": os.path.basename(fp), "type": "file", "bytes": st.st_size,
                            "modified": datetime.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")})
            total += st.st_size
            file_count += 1
        entries.sort(key=lambda e: -e["bytes"])
        return {"category": category, "label": label, "root_label": "cs2dp.sqlite (+ -wal / -shm)",
                "total_bytes": total, "file_count": file_count, "entries": entries,
                "notes": notes, "truncated": False}

    rr = os.path.realpath(root)
    if not os.path.isdir(root):
        notes.append("Folder does not exist yet (no data stored here).")
        return {"category": category, "label": label, "root_label": root_label,
                "total_bytes": 0, "file_count": 0, "entries": [], "notes": notes, "truncated": False}

    raw = []
    counter = [0]
    try:
        with os.scandir(root) as it:
            for e in it:
                try:
                    if e.is_symlink() or not _within(rr, e.path):
                        continue                        # skip links / anything resolving outside root
                    if e.is_dir(follow_symlinks=False):
                        sub_notes = []
                        b = _safe_dir_size(e.path, rr, sub_notes, counter)
                        notes.extend(sub_notes[:1])     # surface at most one nested note per dir
                        try:
                            mt = e.stat(follow_symlinks=False).st_mtime
                        except OSError:
                            mt = None
                        raw.append({"name": e.name, "type": "dir", "bytes": b,
                                    "modified": (datetime.datetime.fromtimestamp(mt).isoformat(timespec="seconds")
                                                 if mt else None)})
                        total += b
                    elif e.is_file(follow_symlinks=False):
                        st = e.stat(follow_symlinks=False)
                        raw.append({"name": e.name, "type": "file", "bytes": st.st_size,
                                    "modified": datetime.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")})
                        total += st.st_size
                        file_count += 1
                except OSError:
                    continue
    except PermissionError:
        notes.append("Permission denied reading this folder.")
    except OSError:
        notes.append("Could not read this folder.")

    raw.sort(key=lambda e: -e["bytes"])
    if len(raw) > _STORAGE_MAX_ENTRIES:
        truncated = True
        entries = raw[:_STORAGE_MAX_ENTRIES]
    else:
        entries = raw
    # de-duplicate notes while preserving order
    seen, uniq = set(), []
    for nx in notes:
        if nx not in seen:
            seen.add(nx); uniq.append(nx)
    return {"category": category, "label": label, "root_label": root_label,
            "total_bytes": total, "file_count": file_count, "entries": entries,
            "notes": uniq, "truncated": truncated}


@app.route("/api/admin/ops/storage-detail")
def api_admin_ops_storage_detail():
    """Admin/helper: drill into ONE allowlisted storage category (largest entries) or, with no
    ?category, an overview that reconciles known app-data folders against total disk usage. Strictly
    read-only and realpath-sandboxed -- it can never traverse outside an allowlisted root or delete."""
    if not _helper_or_none():
        return _nostore({"error": "admin only"}), 403
    category = (request.args.get("category") or "").strip()
    if not category:
        return _nostore(_storage_overview())
    if category not in _storage_categories():
        return _nostore({"error": "unknown category"}), 400
    return _nostore(_storage_detail(category))


@app.route("/api/admin/recent")
def api_admin_recent():
    """Recent demos + recent parse jobs (incl. failures). Kept for ops/debugging; the admin UI no
    longer surfaces per-row data, but helpers/admins can still query it."""
    if not _helper_or_none():
        return _nostore({"error": "admin only"}), 403
    return _nostore({"demos": db.recent_demos(12),
                     "jobs": [jobs._public(j) for j in jobs.list_jobs(limit=12)]})


def _scan_orphans(min_age_s=1800):
    """Reclaimable files in the upload dir: kept raw .dem (the parsed cache is the real watch source,
    so the raw demo is reclaimable) + stale temp upload files left by failed/old jobs. NEVER includes
    parsed cache JSON or the retained .txt stats. Files belonging to an ACTIVE job are skipped, and
    so is anything modified within min_age_s (default 30 min) -- a decompress temp of an IN-FLIGHT
    parse isn't the job's upload_path, so the age guard keeps a running parse safe."""
    import glob as _glob
    now = time.time()
    active = {os.path.basename(j.get("upload_path") or "") for j in jobs.list_jobs(active_only=True)}
    dems, temps, total = [], [], 0
    for path in _glob.glob(os.path.join(UPLOADS, "*")):
        name = os.path.basename(path)
        if not os.path.isfile(path) or name in active:
            continue
        try:
            sz = os.path.getsize(path)
            if now - os.path.getmtime(path) < min_age_s:   # too fresh -> may be an in-flight parse
                continue
        except OSError:
            continue
        if name.lower().endswith(".dem") and not name.startswith("_"):
            has_cache = os.path.exists(os.path.join(CACHE, name[:-4] + ".json"))
            dems.append({"name": name, "bytes": sz, "has_cache": has_cache})
            total += sz
        elif name.startswith(("_jobup_", "_jobgz_", "_jobbz_", "_zip_", "_incoming_")):
            temps.append({"name": name, "bytes": sz})       # temp from a failed/interrupted upload
            total += sz
    return {"dems": dems, "temps": temps, "n_dems": len(dems), "n_temps": len(temps),
            "total_bytes": total, "active_jobs": len(active - {""})}


@app.route("/api/admin/orphans")
def api_admin_orphans():
    """Admin: list reclaimable raw .dem + stale upload temps (storage that can be safely freed)."""
    if not _helper_or_none():
        return _nostore({"error": "admin only"}), 403
    return _nostore(_scan_orphans())


@app.route("/api/admin/orphans/clean", methods=["POST"])
def api_admin_orphans_clean():
    """Admin: delete the reclaimable raw .dem + stale temps from _scan_orphans (never cache or stats)."""
    if not _helper_or_none():
        return _nostore({"error": "admin only"}), 403
    scan = _scan_orphans()
    freed, removed = 0, 0
    for f in scan["dems"] + scan["temps"]:
        p = os.path.join(UPLOADS, f["name"])
        try:
            if os.path.isfile(p):
                freed += os.path.getsize(p)
                os.remove(p)
                removed += 1
        except OSError:
            pass
    return _nostore({"ok": True, "removed": removed, "freed_bytes": freed})


@app.route("/api/admin/config", methods=["POST"])
def api_admin_config():
    """Admin: update editable site settings (currently the Free-plan upload limit). Takes effect
    immediately for new uploads + the shown quota -- no env edit, no redeploy."""
    if not _admin_or_none():
        return _nostore({"error": "admin only"}), 403
    body = request.get_json(silent=True) or {}
    out = {"ok": True}
    if "free_upload_limit" in body:
        try:
            out["free_upload_limit"] = appconfig.set_free_upload_limit(int(body["free_upload_limit"]))
        except (TypeError, ValueError):
            return _nostore({"error": "free_upload_limit must be a whole number"}), 400
        actor = _admin_or_none()
        db.log_admin_action(actor.get("id") if actor else None, "set_config",
                            "config", "free_upload_limit", {"value": out.get("free_upload_limit")})
    return _nostore(out)


@app.route("/api/admin/users/<int:uid>", methods=["DELETE"])
def api_admin_delete_user(uid):
    """Remove a user. Demos shared with other members survive; demos only they had are wiped (refcount).
    Admin only; can't delete self."""
    admin = _admin_or_none()
    if not admin:
        return _nostore({"error": "admin only"}), 403
    if admin.get("id") == uid:
        return _nostore({"error": "you can't delete your own admin account"}), 400
    shas = db.owned_demo_ids(uid)
    ok = db.delete_user(uid)
    if ok:
        _wipe_orphaned(shas)
        db.log_admin_action(admin.get("id"), "delete_user", "user", uid, {})
    return _nostore({"ok": ok}), (200 if ok else 404)


@app.route("/api/admin/users")
def api_admin_users():
    """All users + their demo counts + tier + role, for the admin panel (helpers may view)."""
    if not _helper_or_none():
        return _nostore({"error": "admin only"}), 403
    return _nostore({"users": db.list_users()})


@app.route("/api/admin/users/<int:uid>/tier", methods=["POST"])
def api_admin_set_tier(uid):
    """Grant/revoke Pro for a user (manual until billing exists). Helpers may do this.
    Body: {tier: 'pro'|'free', months?: 1|3|6|12}. months omitted/0/unknown + tier=pro -> indefinite."""
    if not _helper_or_none():
        return _nostore({"error": "admin only"}), 403
    data = request.get_json(silent=True) or {}
    tier = str(data.get("tier") or "free").lower()
    pro_until = None
    if tier == "pro":
        try:
            months = int(data.get("months") or 0)
        except (TypeError, ValueError):
            months = 0
        if months in PRO_DURATIONS:
            pro_until = _add_months(datetime.datetime.now(), months).isoformat(timespec="seconds")
        # months 0 / unknown -> indefinite (pro_until stays None)
    ok = db.set_user_tier(uid, tier, pro_until)
    if ok:
        actor = _helper_or_none()
        db.log_admin_action(actor.get("id") if actor else None, "set_tier",
                            "user", uid, {"tier": tier, "pro_until": pro_until})
    return _nostore({"ok": ok, "tier": "pro" if tier == "pro" else "free", "pro_until": pro_until}), (200 if ok else 404)


@app.route("/api/admin/users/<int:uid>/role", methods=["POST"])
def api_admin_set_role(uid):
    """Promote/demote a user to/from Helper. Admin only (helpers can't make other helpers).
    Body: {role: 'helper'|'user'}. You can't change your own role."""
    admin = _admin_or_none()
    if not admin:
        return _nostore({"error": "admin only"}), 403
    if admin.get("id") == uid:
        return _nostore({"error": "you can't change your own role"}), 400
    role = (request.get_json(silent=True) or {}).get("role") or "user"
    ok = db.set_user_role(uid, role)
    if ok:
        db.log_admin_action(admin.get("id"), "set_role", "user", uid, {"role": role})
    return _nostore({"ok": ok, "role": "helper" if str(role).lower() == "helper" else "user"}), (200 if ok else 404)


@app.route("/api/admin/pricing", methods=["GET", "POST"])
def api_admin_pricing():
    """Read/edit Pro subscription prices. Admin only. GET -> {config, plans, periods}.
    POST {currency?, prices:{periodKey: total}} -> persists + returns the recomputed plans."""
    if not _admin_or_none():
        return _nostore({"error": "admin only"}), 403
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        pricing.save_config(prices=data.get("prices"), currency=data.get("currency"))
        actor = _admin_or_none()
        db.log_admin_action(actor.get("id") if actor else None, "set_pricing",
                            "config", "pricing", {"prices": data.get("prices"), "currency": data.get("currency")})
    return _nostore({"config": pricing.get_config(), "plans": pricing.public_plans(), "periods": pricing.PERIODS})


@app.route("/api/admin/audit")
def api_admin_audit():
    if not _helper_or_none():
        return _nostore({"error": "admin only"}), 403
    return _nostore({"entries": db.get_admin_audit(100)})


@app.route("/api/admin/users/<int:uid>/detail")
def api_admin_user_detail(uid):
    if not _helper_or_none():
        return _nostore({"error": "admin only"}), 403
    detail = db.get_user_detail(uid)
    if not detail:
        return _nostore({"error": "user not found"}), 404
    return _nostore(detail)


@app.route("/api/admin/teams")
def api_admin_teams():
    if not _helper_or_none():
        return _nostore({"error": "admin only"}), 403
    return _nostore({"teams": db.list_all_teams_admin()})


@app.route("/api/admin/jobs/<job_id>/retry", methods=["POST"])
def api_admin_retry_job(job_id):
    if not _admin_or_none():
        return _nostore({"error": "admin only"}), 403
    job = jobs.get_job(job_id)
    if not job:
        return _nostore({"error": "job not found"}), 404
    if job.get("status") not in ("failed",):
        return _nostore({"error": "only failed jobs can be retried"}), 400
    upload_path = job.get("upload_path")
    if not upload_path or not os.path.exists(upload_path):
        return _nostore({"error": "original upload file not found — cannot retry"}), 400
    jobs._update(job_id, status="queued", progress="queued",
                 started_at=None, finished_at=None, error=None)
    try:
        jobs._wake.set()   # wake local worker thread if any (no-op in separate-worker mode)
    except Exception:
        pass
    actor = _admin_or_none()
    db.log_admin_action(actor.get("id") if actor else None, "retry_job", "job", job_id,
                        {"filename": job.get("filename")})
    return _nostore({"ok": True})


# ---- admin: sample-demo replacement -----------------------------------------
# The bundled sample goes stale when SCHEMA/ANALYTICS_VERSION bump (it then shows the empty-analytics
# fallback). These admin-only routes let an admin upload a fresh .dem -> parse via the trusted path ->
# validate real analytics -> atomically swap it in as the runtime sample. The admin sample is site
# furniture: never a user/team library member, never counted against any quota, stored under DATA_DIR.
def _parse_sample_dem(dem_path):
    """Parse a .dem (or .dem.gz / .dem.bz2 / .zip) at `dem_path` into the normalized + analytics-tagged
    dict using the SAME trusted parser/analytics as user uploads -- but WITHOUT the content-hash cache,
    library, or quota side effects (the admin sample is not a user demo). The admin route saves the
    picked file as-is, so compression is detected by MAGIC BYTES (not filename) and decompressed to a
    real .dem first -- this also makes rebuild work on a retained compressed raw. Raises on an
    unparseable demo. Analytics that fail to compute leave analytics=None, which validation then
    rejects (no fake analytics)."""
    import parser as demo_parser
    import analytics as an
    from demoparser2 import DemoParser

    parse_path, tmp_dec = dem_path, None
    try:
        with open(dem_path, "rb") as fh:
            magic = fh.read(4)
        if magic[:2] == b"\x1f\x8b" or magic[:3] == b"BZh" or magic[:4] == b"PK\x03\x04":
            fd, tmp_dec = tempfile.mkstemp(prefix="_sampledec_", suffix=".dem", dir=CACHE)
            os.close(fd)
            if magic[:2] == b"\x1f\x8b":
                import gzip as _gz
                with _gz.open(dem_path, "rb") as fin, open(tmp_dec, "wb") as fout:
                    shutil.copyfileobj(fin, fout, length=1 << 20)
            elif magic[:3] == b"BZh":
                import bz2 as _bz2
                with _bz2.open(dem_path, "rb") as fin, open(tmp_dec, "wb") as fout:
                    shutil.copyfileobj(fin, fout, length=1 << 20)
            else:                                          # zip -> first .dem entry
                import zipfile
                with zipfile.ZipFile(dem_path) as zf:
                    names = [n for n in zf.namelist() if n.lower().endswith(".dem")]
                    if not names:
                        raise ValueError("zip archive contains no .dem file")
                    with zf.open(names[0]) as fin, open(tmp_dec, "wb") as fout:
                        shutil.copyfileobj(fin, fout, length=1 << 20)
            parse_path = tmp_dec
        pr = DemoParser(parse_path)
        data = demo_parser.parse_demo(pr)
        try:
            data["analytics"] = an.analyze(pr, replay=data)
        except Exception as ae:
            print(f"[sample] analytics failed: {ae}")
            traceback.print_exc()
            data["analytics"] = None
        data["source_sha1"] = _sha1_file(parse_path)
        return clean_nan(data)
    finally:
        if tmp_dec and os.path.exists(tmp_dec):
            try:
                os.remove(tmp_dec)
            except OSError:
                pass


def _sample_status():
    return samplemgr.status(DATA_DIR, replay_valid, analytics_valid)


@app.route("/api/admin/sample", methods=["GET", "POST"])
def api_admin_sample():
    """GET -> status of the effective sample (admin or bundled fallback).
    POST (multipart, file field `demo`) -> parse via the trusted path, validate, atomically replace
    the runtime sample. On any failure the PREVIOUS sample is untouched."""
    admin = _admin_or_none()
    if not admin:
        # 401 if nobody is logged in, 403 if logged in but not an admin -- matching admin routes.
        return (_nostore({"error": "login required"}), 401) if current_user() is None \
            else (_nostore({"error": "admin only"}), 403)

    if request.method == "GET":
        return _nostore(_sample_status())

    f = request.files.get("demo")
    if f is None or not getattr(f, "filename", ""):
        return _nostore({"ok": False, "error": "No demo file uploaded (form field 'demo')."}), 400
    original_filename = os.path.basename(f.filename)

    fd, tmp = tempfile.mkstemp(prefix="_sampleup_", suffix=".dem", dir=CACHE)
    os.close(fd)
    try:
        f.save(tmp)
        try:
            data = _parse_sample_dem(tmp)
        except Exception as e:
            traceback.print_exc()
            db.log_admin_action(admin.get("id"), "sample_upload_failed", "sample", original_filename,
                                {"reason": "parse error: %s" % e, "original_filename": original_filename})
            return _nostore({"ok": False, "error": "Could not parse demo: %s" % e}), 400

        ok, reason = samplemgr.install_parsed(
            DATA_DIR, data, replay_valid, analytics_valid,
            source="admin", original_filename=original_filename, raw_src=tmp)
        if not ok:
            db.log_admin_action(admin.get("id"), "sample_upload_failed", "sample", original_filename,
                                {"reason": reason, "original_filename": original_filename,
                                 "map": data.get("map") if isinstance(data, dict) else None})
            return _nostore({"ok": False, "error": reason}), 400

        st = _sample_status()
        db.log_admin_action(admin.get("id"), "sample_uploaded", "sample", original_filename,
                            {"original_filename": original_filename, "map": st.get("map"),
                             "rounds": st.get("rounds"), "schema_version": st.get("schema_version"),
                             "analytics_version": st.get("analytics_version"),
                             "replay_valid": st.get("replay_valid"),
                             "analytics_valid": st.get("analytics_valid")})
        return _nostore({"ok": True,
                         "message": "Sample replaced: %s (%s rounds) on %s." %
                                    (st.get("map") or "?", st.get("rounds"), original_filename),
                         "status": st})
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)        # the raw .dem was COPIED into the sample dir by install_parsed
        except OSError:
            pass


@app.route("/api/admin/sample/rebuild", methods=["POST"])
def api_admin_sample_rebuild():
    """Re-parse the RETAINED raw .dem at the latest parser/schema/analytics version and reinstall."""
    admin = _admin_or_none()
    if not admin:
        return (_nostore({"error": "login required"}), 401) if current_user() is None \
            else (_nostore({"error": "admin only"}), 403)
    ok, reason = samplemgr.rebuild(DATA_DIR, _parse_sample_dem, replay_valid, analytics_valid)
    if not ok:
        db.log_admin_action(admin.get("id"), "sample_upload_failed", "sample", "rebuild",
                            {"reason": reason, "op": "rebuild"})
        return _nostore({"ok": False, "error": reason}), 400
    st = _sample_status()
    db.log_admin_action(admin.get("id"), "sample_rebuild", "sample", st.get("original_filename"),
                        {"map": st.get("map"), "rounds": st.get("rounds"),
                         "schema_version": st.get("schema_version"),
                         "analytics_version": st.get("analytics_version"),
                         "replay_valid": st.get("replay_valid"),
                         "analytics_valid": st.get("analytics_valid")})
    return _nostore({"ok": True, "status": st})


@app.route("/api/admin/sample/revert", methods=["POST"])
def api_admin_sample_revert():
    """Drop the admin sample so /api/sample falls back to the bundled sample."""
    admin = _admin_or_none()
    if not admin:
        return (_nostore({"error": "login required"}), 401) if current_user() is None \
            else (_nostore({"error": "admin only"}), 403)
    ok, reason = samplemgr.revert(DATA_DIR)
    if not ok:
        return _nostore({"ok": False, "error": reason}), 400
    st = _sample_status()
    db.log_admin_action(admin.get("id"), "sample_revert", "sample", None,
                        {"source": st.get("source")})
    return _nostore({"ok": True, "status": st})


# ---- callouts / location knowledge ------------------------------------------
@app.route("/api/callouts/<map_name>")
def api_callouts_map(map_name):
    """Effective callouts for a map (JSON seed + admin overrides + demo-learned fill). Powers the 2D
    overlay, utility-by-callout matching, analytics handoffs, and location labeling."""
    return _nostore({"map": map_name, "callouts": callout_store.effective_callouts(map_name)})


@app.route("/api/callouts")
def api_callouts_list():
    """Per-map callout coverage (count / world-coords / boundaries / admin-managed / learned samples)."""
    return _nostore({"coverage": callout_store.coverage()})


@app.route("/api/label/<map_name>")
def api_label(map_name):
    """Label a world position with the nearest/inside callout. Query: ?x=..&y=..[&t=threshold]."""
    try:
        x = float(request.args.get("x")); y = float(request.args.get("y"))
    except (TypeError, ValueError):
        return _nostore({"error": "x and y query params required"}), 400
    t = request.args.get("t", type=float) or 500
    return _nostore(callout_store.label(map_name, x, y, threshold=t))


# ---- admin: callout editor --------------------------------------------------
@app.route("/api/admin/callouts/<map_name>")
def api_admin_callouts_get(map_name):
    """Editor payload: effective callouts + learned suggestions + unmapped learned zones + calibration."""
    if not _admin_or_none():
        return _nostore({"error": "admin only"}), 403
    return _nostore(callout_store.editor_data(map_name))


@app.route("/api/admin/callouts/<map_name>", methods=["POST"])
def api_admin_callouts_save(map_name):
    """Save the editor's full callout list as this map's override set (admin owns the map thereafter)."""
    admin = _admin_or_none()
    if not admin:
        return _nostore({"error": "admin only"}), 403
    body = request.get_json(silent=True) or {}
    cos = body.get("callouts")
    if not isinstance(cos, list):
        return _nostore({"error": "callouts (list) required"}), 400
    n = callout_store.save_map(map_name, cos, admin_uid=admin.get("id"))
    db.log_admin_action(admin.get("id"), "save_callouts", "map", map_name, {"count": n})
    return _nostore({"ok": True, "saved": n})


@app.route("/api/admin/callouts/<map_name>/revert", methods=["POST"])
def api_admin_callouts_revert(map_name):
    """Drop admin overrides -> revert this map to its JSON seed (+ learned fill)."""
    admin = _admin_or_none()
    if not admin:
        return _nostore({"error": "admin only"}), 403
    n = callout_store.revert_map(map_name)
    db.log_admin_action(admin.get("id"), "revert_callouts", "map", map_name, {"removed": n})
    return _nostore({"ok": True, "removed": n})


@app.route("/api/admin/callouts/<map_name>/ingest", methods=["POST"])
def api_admin_callouts_ingest(map_name):
    """Backfill demo-learned position samples for this map from parsed caches. New uploads fold
    automatically at index time; this re-folds caches that carry analytics.position_samples but
    predate that wiring. Only loads caches for THIS map (via the index), so it stays bounded."""
    admin = _admin_or_none()
    if not admin:
        return _nostore({"error": "admin only"}), 403
    scanned = had = folded = 0
    for sha, key in db.demo_keys_for_map(map_name):
        data = load_cache(os.path.join(CACHE, (key or sha) + ".json"))
        if not data:
            continue
        scanned += 1
        samples = (data.get("analytics") or {}).get("position_samples")
        if isinstance(samples, dict) and samples:
            had += 1
            if db.fold_position_samples(data.get("source_sha1") or sha, map_name, samples):
                folded += 1
    db.log_admin_action(admin.get("id"), "ingest_callouts", "map", map_name,
                        {"scanned": scanned, "folded": folded})
    return _nostore({"ok": True, "scanned": scanned, "with_samples": had, "folded": folded,
                     "learned_zones": len(db.callout_learned(map_name))})


# ---- teams / workspaces (Stage 5) -------------------------------------------
@app.route("/api/teams", methods=["GET", "POST"])
def api_teams():
    """GET: the current user's teams. POST {name}: create a team (creator becomes owner)."""
    uid = current_user_id()
    if uid is None:
        return _nostore({"error": "login required"}), 401
    if request.method == "POST":
        name = (request.get_json(silent=True) or {}).get("name") or "Team"
        return _nostore(db.create_team(name, uid))
    return _nostore({"teams": db.teams_for_user(uid)})


@app.route("/api/teams/join", methods=["POST"])
def api_team_join():
    """POST {invite_code}: join a team by its invite code."""
    uid = current_user_id()
    if uid is None:
        return _nostore({"error": "login required"}), 401
    code = (request.get_json(silent=True) or {}).get("invite_code") or ""
    t = db.join_team(code, uid)
    if not t:
        return _nostore({"error": "invalid invite code"}), 404
    return _nostore(t)


@app.route("/api/teams/<int:tid>/leave", methods=["POST"])
def api_team_leave(tid):
    """Current (non-owner) member leaves the team. Owners must disband instead."""
    uid = current_user_id()
    if uid is None:
        return _nostore({"error": "login required"}), 401
    if not db.leave_team(uid, tid):
        return _nostore({"error": "can't leave (owners disband instead)"}), 400
    return _nostore({"ok": True})


@app.route("/api/teams/<int:tid>/remove", methods=["POST"])
def api_team_remove_member(tid):
    """Owner removes a member. Body: {user_id}."""
    uid = current_user_id()
    if uid is None:
        return _nostore({"error": "login required"}), 401
    target = (request.get_json(silent=True) or {}).get("user_id")
    if not db.remove_member(tid, target, uid):
        return _nostore({"error": "not allowed"}), 403
    return _nostore({"ok": True})


@app.route("/api/teams/<int:tid>", methods=["DELETE"])
def api_team_disband(tid):
    """Owner disbands (deletes) the team; demos shared to it revert to private."""
    uid = current_user_id()
    if uid is None:
        return _nostore({"error": "login required"}), 401
    if not db.disband_team(tid, uid):
        return _nostore({"error": "owner only"}), 403
    return _nostore({"ok": True})


@app.route("/api/demo/<demo_id>/team", methods=["POST"])
def api_demo_team(demo_id):
    """POST {team_id}: share a demo with a team (team_id=null to unshare). Owner-only."""
    uid = current_user_id()
    if uid is None:
        return _nostore({"error": "login required"}), 401
    team_id = (request.get_json(silent=True) or {}).get("team_id")
    ok = db.set_demo_team(demo_id, team_id, uid)
    return _nostore({"ok": ok}), (200 if ok else 400)


@app.route("/api/demo/<demo_id>/tag", methods=["POST"])
def api_demo_tag(demo_id):
    """Set/clear the current user's per-demo tag (scrim/matchmaking/faceit/...) for filtering.
    Per-user: only affects the caller's own library copy."""
    uid = current_user_id()
    if uid is None:
        return _nostore({"error": "login required"}), 401
    sha = db.resolve_sha(demo_id) or demo_id
    tag = (request.get_json(silent=True) or {}).get("tag", "")
    ok = db.set_demo_tag(uid, sha, tag)
    return _nostore({"ok": ok, "tag": (tag or "").strip()[:32]}), (200 if ok else 400)


@app.route("/logout", methods=["POST"])
def logout():
    # POST-only: a GET /logout let a cross-site <img src="/logout"> force a logout (logout-CSRF).
    # The frontend already calls this with fetch(POST).
    session.pop("uid", None)
    return _nostore({"ok": True})


# ---- account self-service (display name / delete) ---------------------------
@app.route("/api/account/name", methods=["POST"])
def api_account_name():
    """Set your own display name (persists past Steam re-login). Body: {name}."""
    u = current_user()
    if not u or not u.get("id"):
        return _nostore({"error": "login required"}), 401
    name = (request.get_json(silent=True) or {}).get("name")
    saved = db.set_display_name(u["id"], name)
    if not saved:
        return _nostore({"error": "name can't be empty"}), 400
    return _nostore({"ok": True, "name": saved})


@app.route("/api/account", methods=["DELETE"])
def api_account_delete():
    """Delete your own account AND your data: your library + account row + team memberships. Demos
    you co-own with someone else (same match uploaded by both) survive for them; demos only you had
    are fully wiped (parsed JSON, cache, raw .dem). Irreversible. Ends the session."""
    u = current_user()
    if not u or not u.get("id"):
        return _nostore({"error": "login required"}), 401
    uid = u["id"]
    shas = db.owned_demo_ids(uid)          # the user's library (memberships) -- capture before deleting
    db.delete_user(uid)                    # drops their memberships + account + team memberships
    wiped = _wipe_orphaned(shas)           # wipe only demos that now have no members left
    session.pop("uid", None)
    return _nostore({"ok": True, "demos_deleted": wiped})


@app.errorhandler(413)
def too_large(_e):
    return jsonify({"error": "file too large"}), 413


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")     # set 0.0.0.0 to expose on the LAN
    port = int(os.environ.get("PORT", "8770"))
    print(f"\n  CS2 Demo Player  ->  http://{host}:{port}\n")
    start_workers()                                 # background parse-job worker
    # Flask's dev server is fine for local use; for hosting use waitress/gunicorn (see DEPLOY.md)
    app.run(host=host, port=port, threaded=True, debug=False)
