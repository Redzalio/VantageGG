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

import goals                                            # persistent match-aware Practice Goals (stdlib only)
import library                                          # saved-demo library (stdlib only)
import mapstatus                                        # 3D-asset status (stdlib only)
import matchindex                                       # cross-match trends (stdlib only)
import db                                               # SQLite metadata index (stdlib only)
import jobs                                             # background parse-job queue (stdlib only)
import nades                                            # local nade-lineup library (stdlib only)
import billing                                          # Stripe subscription billing (opt-in via env)
import practiceplan                                     # practice-plan done-state (stdlib only)
import pricing                                           # editable Pro subscription prices (stdlib only)
import reviews                                          # review bookmarks + auto-queues (stdlib only)
import statsfile                                        # compact .txt stats retained when a replay is deleted
import steamauth                                        # Steam OpenID 2.0 login (stdlib only, optional)
import teams                                            # local team config (stdlib only)
import tendencies                                       # cross-match tendency detection (stdlib only)
import playbook                                         # team playbook + adherence (stdlib only)
import nadeclusters                                     # auto-detect consistent utility (stdlib only)
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
    """The logged-in user dict, or a synthetic 'local' user when auth isn't enabled. Returns None
    only when auth is REQUIRED and nobody is logged in (data access is gated on this in Stage 5)."""
    uid = session.get("uid")
    if uid is not None:
        u = db.get_user(uid)
        if u:
            return u
        session.pop("uid", None)                       # stale session: user row no longer exists
    if steamauth.auth_required():
        return None
    return {"id": None, "name": "Local", "local": True}


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
    return {"unlimited": False, "used": used, "limit": FREE_UPLOAD_LIMIT,
            "remaining": max(0, FREE_UPLOAD_LIMIT - used)}


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


@app.route("/api/sample")
def sample():
    """Serve the sample demo, validating it against the current schema/analytics versions.

    - replay missing or schema-stale  -> regenerate a fresh mock at the current SCHEMA_VERSION
    - replay current but analytics stale (and no .dem to recompute from) -> drop the stale
      analytics so the UI shows the honest empty state instead of wrong numbers
    """
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
        print("[sample] analytics version stale and no .dem to recompute -> dropping it")
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


def _save_to_library(name, data, owner_user_id=None):
    """Save a parsed demo to the library; return its frontend result row.
    `owner_user_id` stamps the uploader in the index (NULL in local mode)."""
    demo_id = library.demo_id_for(data)
    library.upsert(CACHE, demo_id, name, data, atomic_write_json)
    try:
        db.index_demo(data, str(demo_id)[:16], owner_user_id=owner_user_id)   # keep the fast index current
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
        _save_to_library(name, data, owner_user_id=job.get("owner_user_id"))
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


def _process_one_dem(tmp, name, results, owner_user_id=None):
    """Parse a single .dem temp file and append its result (ok or error)."""
    try:
        data = _parse_or_load_dem(tmp, name)
        results.append(_save_to_library(name, data, owner_user_id=owner_user_id))
    except Exception as e:
        traceback.print_exc()
        results.append({"name": name, "ok": False, "error": f"parse failed: {e}"})


def _process_zip(tmp, name, results, owner_user_id=None):
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
            _process_one_dem(dem_tmp, member_name, results, owner_user_id=owner_user_id)
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
    """In AUTH_REQUIRED mode, reject anonymous requests up front. Returns a (response, 401) tuple to
    return immediately, or None when the request may proceed. Gates resource-creating / data endpoints
    (upload, jobs, nade media) so anonymous traffic can't burn disk/CPU/queue or read others' data on
    the locked-down hosted site. Local/open mode (auth not required) is unaffected."""
    if steamauth.auth_required() and current_user() is None:
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
                (_process_zip if is_zip else _process_one_dem)(tmp, name, results, owner)
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
                created.append({"id": jobs.create_job(dispname, btmp, owner_user_id=owner, upload_ms=ums, size_bytes=bts),
                                "filename": dispname, "status": "queued", "ok": True})
            elif is_gz:
                # client-gzipped demo (CompressionStream): save the .gz as-is + enqueue; the worker
                # gunzips before parsing (keeps this web tier light -- it does no decompression/parse).
                dispname = library.strip_gz(name)
                fd, gtmp = tempfile.mkstemp(prefix="_jobup_", suffix=".dem.gz", dir=UPLOADS)
                os.close(fd)
                ums, bts = _timed_save(f, gtmp)
                created.append({"id": jobs.create_job(dispname, gtmp, owner_user_id=owner, upload_ms=ums, size_bytes=bts),
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
                        created.append({"id": jobs.create_job(mname, dtmp, owner_user_id=owner, upload_ms=per_ms, size_bytes=len(payload)),
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
                created.append({"id": jobs.create_job(name, dtmp, owner_user_id=owner, upload_ms=ums, size_bytes=bts),
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
    if sc is not None:
        ok = db.visible_predicate(sc)
        demos = [d for d in demos if ok(d.get("id"))]    # deleted (stats-only) demos have no cache -> excluded
    resp = jsonify({"demos": demos})
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
        return d
    if re.fullmatch(r"[A-Za-z0-9]+", sid):               # content-hash cache (<sha[:16]>.json)
        d = load_cache(os.path.join(CACHE, f"{sid[:16]}.json"))
        if d is not None:
            return d
    s = load_cache(os.path.join(CACHE, "sample.json"))   # the sample
    if s is not None and s.get("source_sha1") == sid:
        return s
    return None


@app.route("/api/reviews/<demo_id>/bookmarks", methods=["GET", "POST"])
def api_bookmarks(demo_id):
    """List (GET) or add/replace (POST) review bookmarks for a demo (keyed by source_sha1)."""
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
    return jsonify({"deleted": reviews.delete_bookmark(demo_id, bm_id)})


@app.route("/api/reviews/<demo_id>/queues")
def api_review_queues(demo_id):
    """Auto-seeded review queues (untraded deaths, dry opens, good rounds, team losses,
    round-by-round, ...) computed from the demo's analytics. Empty list if not found."""
    data = _load_demo_by_sha(demo_id)
    if data is None:
        return jsonify({"queues": []})
    return jsonify({"queues": reviews.auto_queues(data)})


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
    matches = db.list_matches(scope=sc)
    g_uid = current_user_id()
    g_team_ids = set(db.team_ids_for_user(g_uid)) if g_uid else set()
    try:
        open_goals = [g for g in db.goals_visible(g_uid, g_team_ids)
                      if g.get("status") in ("open", "drilling")]
    except Exception:
        open_goals = []
    active = [jobs._public(j) for j in jobs.list_jobs(owner_user_id=g_uid, active_only=True)]  # only your own uploads
    # the signed-in user's own form (rating/K-D/ADR/KAST + trend), if they appear in their demos
    me = None
    u = current_user()
    if u and u.get("steam_id_64"):
        try:
            t = db.player_trends(u["steam_id_64"], scope=sc)
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


@app.route("/api/tendencies/<steamid>")
def api_tendencies(steamid):
    """Cross-match tendencies / repeated patterns for one player (anti-strat scouting)."""
    mode, sc = _scope_or_block()
    if mode == "blocked":
        return _nostore({"error": "login required"}), 401
    matches = goals._matches(CACHE)
    if sc is not None:                                  # only count matches this user may see
        ok = db.visible_predicate(sc)
        matches = [m for m in matches if ok(m.get("sha"))]
    return _nostore(tendencies.cross_tendencies(matches, steamid))


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
    this map and one-click add it, not wait for the same lineup to recur across multiple demos."""
    player = request.args.get("player") or None
    mp = request.args.get("map") or None
    sha = request.args.get("sha") or None      # restrict to the demo being watched (not other demos on the map)
    cl = nadeclusters.find_consistent(CACHE, steamid=player, map_filter=mp, min_matches=1, only_sha=sha)
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
    """Current auth state for the frontend. In local mode: auth_enabled=False + a synthetic user."""
    u = current_user()
    teams = db.teams_for_user(u["id"]) if (u and u.get("id")) else []
    return _nostore({"authenticated": bool(u and u.get("id")),
                     "auth_enabled": steamauth.auth_enabled(),
                     "auth_required": steamauth.auth_required(),
                     "user": u, "teams": teams,
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
        "tiers_enabled": TIERS_ENABLED, "free_upload_limit": FREE_UPLOAD_LIMIT,
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
    return _nostore({"config": pricing.get_config(), "plans": pricing.public_plans(), "periods": pricing.PERIODS})


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
