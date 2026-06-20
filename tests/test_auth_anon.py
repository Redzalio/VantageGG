"""Regression tests for the anonymous-admin exposure bug.

THE BUG: current_user() fell back to the synthetic 'local' user (is_admin=True, tier=pro, sees
everything) whenever steamauth.auth_required() was false. But auth_required() is true ONLY for
AUTH_REQUIRED=1 -- so a real public deployment with PUBLIC_BASE_URL set but AUTH_REQUIRED unset handed
every anonymous internet visitor the privileged local owner: /api/admin/* returned 200, /api/me
reported is_admin=True + tier=pro, and private dashboard/library data was world-readable.

THE FIX: the synthetic local user is returned ONLY in PURE-LOCAL mode (neither PUBLIC_BASE_URL nor
AUTH_REQUIRED set, i.e. steamauth.auth_enabled() is false). On ANY auth-enabled deployment an
unauthenticated visitor is anonymous: is_admin=False, is_helper=False, tier=free, authenticated=False,
and admin/data endpoints fail closed (401/403).

These tests run under pytest, so app._load_dotenv() is skipped (hermetic) and the only env that matters
is what each test sets via monkeypatch. Temp DB, no real demo parsing.
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db    # noqa: E402
import app   # noqa: E402


def _tmpdb(tmp_path, name="anon.sqlite"):
    db.DB_PATH = str(tmp_path / name)
    db.migrate()


def _auth_enabled_env(monkeypatch, required=False):
    """Configure an auth-ENABLED deployment. By default PUBLIC_BASE_URL is set but AUTH_REQUIRED is NOT
    -- the exact misconfiguration that exposed the admin panel. required=True also sets AUTH_REQUIRED."""
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://vantagegg.com")
    monkeypatch.setenv("ADMIN_STEAM_IDS", "76561198106326204")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    if required:
        monkeypatch.setenv("AUTH_REQUIRED", "1")
    else:
        monkeypatch.delenv("AUTH_REQUIRED", raising=False)


def _pure_local_env(monkeypatch):
    """Pure-local install: neither auth env var set. The single user is the admin/Pro owner."""
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("ADMIN_STEAM_IDS", raising=False)


# ---------------------------------------------------------------------------
# 1. Pure local mode is unchanged: the single owner is admin + Pro, dashboard works.
# ---------------------------------------------------------------------------
def test_pure_local_mode_owner_is_admin_pro(tmp_path, monkeypatch):
    _pure_local_env(monkeypatch)
    _tmpdb(tmp_path, "local.sqlite")
    assert app.steamauth.auth_enabled() is False        # neither env set -> pure local
    c = app.app.test_client()

    me = c.get("/api/me").get_json()
    assert me["auth_enabled"] is False
    assert me["is_admin"] is True                        # local owner runs their own machine
    assert me["tier"] == "pro"
    assert me["user"] and me["user"].get("local") is True

    # the synthetic local user owns nothing -> scope is 'open', everything visible
    assert c.get("/api/dashboard").status_code == 200
    assert c.get("/api/library").status_code == 200
    assert c.get("/api/admin/overview").status_code == 200   # local = full control


def test_pure_local_current_user_helpers(tmp_path, monkeypatch):
    """Unit-level: in pure-local mode current_user() is the local owner and the gating helpers agree."""
    _pure_local_env(monkeypatch)
    _tmpdb(tmp_path, "local2.sqlite")
    with app.app.test_request_context("/"):
        u = app.current_user()
        assert u is not None and u.get("local") is True
        assert app.is_admin(u) is True
        assert app.is_helper(u) is True
        assert app.tier_of(u) == "pro"


# ---------------------------------------------------------------------------
# 2 & 3. Auth-enabled (PUBLIC_BASE_URL set, AUTH_REQUIRED unset) + anonymous:
#        NOT admin, NOT pro, admin endpoints 401/403.
# ---------------------------------------------------------------------------
def test_auth_enabled_anon_me_is_not_admin_not_pro(tmp_path, monkeypatch):
    _auth_enabled_env(monkeypatch)                       # PUBLIC_BASE_URL set, AUTH_REQUIRED unset
    _tmpdb(tmp_path, "anon_me.sqlite")
    assert app.steamauth.auth_enabled() is True
    assert app.steamauth.auth_required() is False        # the exact exposed configuration
    c = app.app.test_client()

    me = c.get("/api/me").get_json()
    assert me["auth_enabled"] is True
    assert me["authenticated"] is False
    assert me["is_admin"] is False                       # <-- was True (the bug)
    assert me["is_helper"] is False
    assert me["tier"] != "pro" and me["tier"] == "free"  # <-- was "pro" (the bug)
    # explicit anonymous identity, never the privileged local owner
    assert not (me["user"] or {}).get("local")
    assert (me["user"] or {}).get("anonymous") is True
    # NOTE: entitlements are NOT asserted here. They gate Pro *UI features*, and with TIERS_ENABLED
    # off (the default) "everyone gets everything" by design -- that's a feature flag, not an
    # authz control. The real security gates (is_admin / tier / 401-403 on admin+data endpoints) are
    # what this bug was about, and they're covered above and in the sibling tests.


def test_auth_enabled_anon_admin_overview_blocked(tmp_path, monkeypatch):
    _auth_enabled_env(monkeypatch)
    _tmpdb(tmp_path, "anon_ov.sqlite")
    c = app.app.test_client()
    assert c.get("/api/admin/overview").status_code in (401, 403)   # <-- was 200 (the bug)


def test_auth_enabled_anon_admin_users_blocked(tmp_path, monkeypatch):
    _auth_enabled_env(monkeypatch)
    _tmpdb(tmp_path, "anon_users.sqlite")
    # seed a real user so /api/admin/users would otherwise leak the roster (SteamID, tier, role)
    db.upsert_user("76561198106326204", "Redzalio")
    c = app.app.test_client()
    assert c.get("/api/admin/users").status_code in (401, 403)      # <-- was 200 (the bug)


def test_auth_enabled_anon_all_admin_endpoints_blocked(tmp_path, monkeypatch):
    """Belt-and-suspenders: every admin route fails closed for an anonymous visitor."""
    _auth_enabled_env(monkeypatch)
    _tmpdb(tmp_path, "anon_all.sqlite")
    uid = db.upsert_user("76561198106326204", "Redzalio")
    c = app.app.test_client()
    gets = ["/api/admin/overview", "/api/admin/users", "/api/admin/ops",
            "/api/admin/recent", "/api/admin/orphans"]
    for path in gets:
        assert c.get(path).status_code in (401, 403), path
    assert c.post("/api/admin/orphans/clean").status_code in (401, 403)
    assert c.post("/api/admin/config", json={"free_upload_limit": 99}).status_code in (401, 403)
    assert c.post("/api/admin/users/%d/tier" % uid, json={"tier": "pro"}).status_code in (401, 403)
    assert c.post("/api/admin/users/%d/role" % uid, json={"role": "helper"}).status_code in (401, 403)
    assert c.delete("/api/admin/users/%d" % uid).status_code in (401, 403)
    # the anon escalation didn't actually grant anything
    assert db.get_user(uid)["tier"] == "free" and db.get_user(uid)["role"] == "user"


# ---------------------------------------------------------------------------
# 4. Auth-enabled + anonymous: /api/dashboard (and friends) return 401.
# ---------------------------------------------------------------------------
def test_auth_enabled_anon_dashboard_401(tmp_path, monkeypatch):
    _auth_enabled_env(monkeypatch)
    _tmpdb(tmp_path, "anon_dash.sqlite")
    c = app.app.test_client()
    assert c.get("/api/dashboard").status_code == 401      # <-- was 200, leaking instance data


def test_auth_enabled_anon_data_endpoints_401(tmp_path, monkeypatch):
    """The dashboard isn't the only one: library/matches/players must also block anon here."""
    _auth_enabled_env(monkeypatch)
    _tmpdb(tmp_path, "anon_data.sqlite")
    c = app.app.test_client()
    for path in ("/api/dashboard", "/api/library", "/api/matches", "/api/players"):
        assert c.get(path).status_code == 401, path
    # resource-creating endpoints also fail closed (no anon disk/CPU/queue burn on a public site)
    up = c.post("/api/upload", data={"files": (io.BytesIO(b"x"), "m.dem")},
                content_type="multipart/form-data")
    assert up.status_code == 401
    assert c.get("/api/jobs").status_code == 401


# ---------------------------------------------------------------------------
# AUTH_REQUIRED=1 anonymous: also fails closed (the previously-"safe" path stays safe).
# ---------------------------------------------------------------------------
def test_auth_required_anon_blocked(tmp_path, monkeypatch):
    _auth_enabled_env(monkeypatch, required=True)        # AUTH_REQUIRED=1 too
    _tmpdb(tmp_path, "req_anon.sqlite")
    assert app.steamauth.auth_required() is True
    c = app.app.test_client()
    me = c.get("/api/me").get_json()
    assert me["is_admin"] is False and me["tier"] == "free" and me["authenticated"] is False
    assert c.get("/api/admin/overview").status_code in (401, 403)
    assert c.get("/api/dashboard").status_code == 401


# ---------------------------------------------------------------------------
# 5. Signed-in admin still gets full admin access (behaviour preserved).
# ---------------------------------------------------------------------------
def test_signed_in_admin_keeps_access(tmp_path, monkeypatch):
    _auth_enabled_env(monkeypatch)                       # auth-enabled deployment
    _tmpdb(tmp_path, "signed_admin.sqlite")
    admin_uid = db.upsert_user("76561198106326204", "Redzalio")   # in ADMIN_STEAM_IDS
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = admin_uid

    me = c.get("/api/me").get_json()
    assert me["authenticated"] is True
    assert me["is_admin"] is True
    assert me["tier"] == "pro"

    assert c.get("/api/admin/overview").status_code == 200
    assert c.get("/api/admin/users").status_code == 200
    # admin can actually act (grant Pro to another user). PUBLIC_BASE_URL is set, so the CSRF origin
    # guard requires a same-origin header on this state-changing POST (same as a real browser fetch).
    same_origin = {"Origin": "https://vantagegg.com"}
    other = db.upsert_user("111", "Bob")
    r = c.post("/api/admin/users/%d/tier" % other, json={"tier": "pro"}, headers=same_origin)
    assert r.status_code == 200 and db.get_user(other)["tier"] == "pro"
    # and the admin sees their dashboard
    assert c.get("/api/dashboard").status_code == 200


def test_signed_in_nonadmin_user_not_admin(tmp_path, monkeypatch):
    """A signed-in NON-admin on the same deployment is not admin and can't reach the admin panel,
    but their own dashboard works (regular authenticated user)."""
    _auth_enabled_env(monkeypatch)
    _tmpdb(tmp_path, "signed_user.sqlite")
    uid = db.upsert_user("111", "Bob")                  # not in ADMIN_STEAM_IDS
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid
    me = c.get("/api/me").get_json()
    assert me["authenticated"] is True and me["is_admin"] is False
    assert c.get("/api/admin/overview").status_code == 403
    assert c.get("/api/dashboard").status_code == 200
