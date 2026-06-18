"""Admin panel + subscription-tier tests: is_admin/tier_of/entitlements logic, the db tier helpers,
and the gated /api/admin/* endpoints (anon/non-admin -> 403, admin -> 200) via the Flask client."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db   # noqa: E402


def _tmpdb(tmp_path):
    db.DB_PATH = str(tmp_path / "admin.sqlite")
    db.migrate()


def _demo(sha, owner_uid):
    p = {"steamid": "1", "name": "A", "kills": 10, "deaths": 10, "kd": 1.0, "adr": 75,
         "kast": 70, "hltv": 1.0, "open_wr": 50, "traded_pct": 20, "udr": 20}
    return {"source_sha1": sha, "map": "de_x", "version": 14, "rounds": [{"score_ct": 13, "score_t": 7}],
            "analytics": {"version": 9, "n_rounds": 20, "players": [p]}}


# ---- db tier helpers --------------------------------------------------------
def test_tier_default_set_and_overview(tmp_path):
    _tmpdb(tmp_path)
    uid = db.upsert_user("76561198106326204", "Redzalio")
    assert db.get_user(uid)["tier"] == "free"
    assert db.set_user_tier(uid, "pro") is True
    assert db.get_user(uid)["tier"] == "pro"
    assert db.set_user_tier(uid, "garbage") is True and db.get_user(uid)["tier"] == "free"  # normalized
    ov = db.admin_overview()
    assert ov["users"] == 1 and "jobs" in ov and "players_indexed" in ov
    assert any(u["id"] == uid for u in db.list_users())


def test_role_default_set_and_growth_metrics(tmp_path):
    _tmpdb(tmp_path)
    uid = db.upsert_user("76561198106326204", "Redzalio")
    assert db.get_user(uid)["role"] == "user"                       # default
    assert db.set_user_role(uid, "helper") is True
    assert db.get_user(uid)["role"] == "helper"
    assert db.set_user_role(uid, "garbage") is True and db.get_user(uid)["role"] == "user"  # normalized
    db.set_user_role(uid, "helper")
    ov = db.admin_overview()
    # pro-vs-free + helpers + growth windows are all present
    for k in ("free_users", "pro_users", "helpers", "new_users_7d", "new_users_30d",
              "demos_7d", "demos_30d", "signups_14d", "uploads_14d"):
        assert k in ov, k
    assert ov["users"] == ov["pro_users"] + ov["free_users"]
    assert ov["helpers"] == 1
    assert ov["new_users_7d"] == 1                                   # just-created user counts
    assert len(ov["signups_14d"]) == 14 and len(ov["uploads_14d"]) == 14
    assert all(set(pt.keys()) == {"date", "count"} for pt in ov["signups_14d"])
    assert ov["signups_14d"][-1]["count"] == 1                       # today's bucket


# ---- helper role gating -----------------------------------------------------
def test_helper_can_view_and_grant_but_not_manage(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_STEAM_IDS", "76561198106326204")
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    import app
    db.DB_PATH = str(tmp_path / "h.sqlite")
    db.migrate()
    admin_uid = db.upsert_user("76561198106326204", "Redzalio")
    helper_uid = db.upsert_user("111", "Helen")
    bob = db.upsert_user("222", "Bob")
    db.set_user_role(helper_uid, "helper")
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = helper_uid
    assert c.get("/api/admin/overview").status_code == 200          # helper can view
    assert c.get("/api/admin/users").status_code == 200
    assert c.post("/api/admin/users/%d/tier" % bob, json={"tier": "pro"}).status_code == 200  # + grant Pro
    assert db.get_user(bob)["tier"] == "pro"
    assert c.delete("/api/admin/users/%d" % bob).status_code == 403  # but NOT delete
    assert c.post("/api/admin/users/%d/role" % bob, json={"role": "helper"}).status_code == 403  # nor promote
    me = c.get("/api/me").get_json()
    assert me["is_helper"] is True and me["is_admin"] is False
    # admin CAN assign roles, but not to themselves
    with c.session_transaction() as s:
        s["uid"] = admin_uid
    assert c.post("/api/admin/users/%d/role" % bob, json={"role": "helper"}).status_code == 200
    assert db.get_user(bob)["role"] == "helper"
    assert c.post("/api/admin/users/%d/role" % admin_uid, json={"role": "helper"}).status_code == 400  # not self
    assert c.get("/api/me").get_json()["is_helper"] is True          # admin is implicitly a helper


# ---- time-limited Pro -------------------------------------------------------
def test_pro_expiry_affects_tier_and_count(tmp_path, monkeypatch):
    import datetime as dt
    monkeypatch.setenv("ADMIN_STEAM_IDS", "999")                     # uid below is NOT an admin
    import app
    db.DB_PATH = str(tmp_path / "exp.sqlite")
    db.migrate()
    uid = db.upsert_user("111", "Bob")
    past = (dt.datetime.now() - dt.timedelta(days=1)).isoformat(timespec="seconds")
    future = (dt.datetime.now() + dt.timedelta(days=30)).isoformat(timespec="seconds")
    db.set_user_tier(uid, "pro", None)                              # indefinite
    assert app.tier_of(db.get_user(uid)) == "pro" and db.admin_overview()["pro_users"] == 1
    db.set_user_tier(uid, "pro", future)                           # still active
    assert app.tier_of(db.get_user(uid)) == "pro" and db.admin_overview()["pro_users"] == 1
    db.set_user_tier(uid, "pro", past)                             # lapsed -> effectively free, uncounted
    assert app.tier_of(db.get_user(uid)) == "free"
    ov = db.admin_overview()
    assert ov["pro_users"] == 0 and ov["free_users"] == 1
    db.set_user_tier(uid, "free", future)                          # going free always clears the expiry
    assert db.get_user(uid)["pro_until"] is None and db.get_user(uid)["tier"] == "free"


def test_add_months_and_grant_duration(tmp_path, monkeypatch):
    import datetime as dt
    monkeypatch.setenv("ADMIN_STEAM_IDS", "76561198106326204")
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    import app
    assert app._add_months(dt.datetime(2026, 1, 31), 1).date() == dt.date(2026, 2, 28)   # clamp short month
    assert app._add_months(dt.datetime(2026, 12, 15), 1).date() == dt.date(2027, 1, 15)  # year rollover
    assert app._add_months(dt.datetime(2026, 6, 17), 12).date() == dt.date(2027, 6, 17)  # 1 year
    db.DB_PATH = str(tmp_path / "dur.sqlite")
    db.migrate()
    admin = db.upsert_user("76561198106326204", "Redzalio")
    bob = db.upsert_user("111", "Bob")
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = admin
    r = c.post("/api/admin/users/%d/tier" % bob, json={"tier": "pro", "months": 3}).get_json()
    assert r["tier"] == "pro" and r["pro_until"] > dt.datetime.now().isoformat()          # ~3 months out
    c.post("/api/admin/users/%d/tier" % bob, json={"tier": "pro", "months": 0})            # indefinite
    assert db.get_user(bob)["pro_until"] is None and db.get_user(bob)["tier"] == "pro"
    c.post("/api/admin/users/%d/tier" % bob, json={"tier": "pro", "months": 999})          # bogus -> indefinite, no crash
    assert db.get_user(bob)["pro_until"] is None and db.get_user(bob)["tier"] == "pro"


# ---- pure helpers (is_admin / tier_of / entitlements) -----------------------
def test_is_admin(monkeypatch):
    monkeypatch.setenv("ADMIN_STEAM_IDS", "76561198106326204, 999")
    import app
    assert app.is_admin({"steam_id_64": "76561198106326204"}) is True
    assert app.is_admin({"steam_id_64": "111"}) is False
    assert app.is_admin({"id": None, "local": True}) is True      # local owner = admin
    assert app.is_admin(None) is False


def test_tier_and_entitlements(monkeypatch):
    monkeypatch.setenv("ADMIN_STEAM_IDS", "76561198106326204")
    import app
    admin = {"steam_id_64": "76561198106326204", "tier": "free"}
    free = {"steam_id_64": "111", "tier": "free"}
    pro = {"steam_id_64": "222", "tier": "pro"}
    assert app.tier_of(admin) == "pro" and app.tier_of(free) == "free" and app.tier_of(pro) == "pro"
    monkeypatch.setattr(app, "TIERS_ENABLED", False)             # off -> everyone unlocked
    assert all(app.entitlements(free).values())
    monkeypatch.setattr(app, "TIERS_ENABLED", True)              # on -> free locked, pro/admin unlocked
    assert not any(app.entitlements(free).values())
    assert all(app.entitlements(pro).values()) and all(app.entitlements(admin).values())


# ---- gated endpoints --------------------------------------------------------
def test_admin_endpoints_gated(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_STEAM_IDS", "76561198106326204")
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    import app
    db.DB_PATH = str(tmp_path / "a.sqlite")
    db.migrate()
    admin_uid = db.upsert_user("76561198106326204", "Redzalio")
    other_uid = db.upsert_user("111", "Bob")
    c = app.app.test_client()
    assert c.get("/api/admin/overview").status_code in (401, 403)   # anonymous
    with c.session_transaction() as s:
        s["uid"] = other_uid
    assert c.get("/api/admin/overview").status_code == 403          # non-admin user
    assert c.post("/api/admin/users/%d/tier" % admin_uid, json={"tier": "pro"}).status_code == 403
    with c.session_transaction() as s:
        s["uid"] = admin_uid
    assert c.get("/api/admin/overview").status_code == 200          # admin
    assert c.get("/api/admin/users").status_code == 200
    r = c.post("/api/admin/users/%d/tier" % other_uid, json={"tier": "pro"})
    assert r.status_code == 200 and db.get_user(other_uid)["tier"] == "pro"


def test_api_me_includes_admin_and_tier(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_STEAM_IDS", "76561198106326204")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://demos.example.com")
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    import app
    db.DB_PATH = str(tmp_path / "m.sqlite")
    db.migrate()
    uid = db.upsert_user("76561198106326204", "Redzalio")
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid
    me = c.get("/api/me").get_json()
    assert me["is_admin"] is True and me["tier"] == "pro"
    assert set(me["entitlements"].keys()) == {"threeD", "utility", "advancedAnalytics", "goals", "teams"}


def test_admin_overview_config_and_maps(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_STEAM_IDS", "76561198106326204")
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    import app
    db.DB_PATH = str(tmp_path / "ov.sqlite")
    db.migrate()
    uid = db.upsert_user("76561198106326204", "Redzalio")
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid
    ov = c.get("/api/admin/overview").get_json()
    assert "config" in ov and "maps3d" in ov
    for k in ("tiers_enabled", "free_upload_limit", "schema_version", "analytics_version", "public_base_url"):
        assert k in ov["config"]


def test_admin_recent_and_delete_user(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_STEAM_IDS", "76561198106326204")
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    import app
    db.DB_PATH = str(tmp_path / "ad.sqlite")
    db.migrate()
    admin_uid = db.upsert_user("76561198106326204", "Redzalio")
    bob = db.upsert_user("111", "Bob")
    db.index_demo(_demo("a" * 40, bob), "a" * 16, owner_user_id=bob, created_at="2026-06-01T00:00:00")
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = admin_uid
    rec = c.get("/api/admin/recent").get_json()
    assert len(rec["demos"]) == 1 and rec["demos"][0]["owner"] == "Bob" and "jobs" in rec
    assert c.delete("/api/admin/users/%d" % admin_uid).status_code == 400      # can't delete self
    assert c.delete("/api/admin/users/%d" % bob).status_code == 200
    assert db.get_user(bob) is None
    con = db.connect()
    n = con.execute("SELECT COUNT(*) n FROM demos WHERE sha1=?", ("a" * 40,)).fetchone()["n"]
    con.close()
    assert n == 0                            # Bob was the only member -> his demo is refcount-wiped
