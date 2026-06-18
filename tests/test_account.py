"""Account self-service: custom display name (locks against Steam re-login), the gated
/api/account/name + DELETE /api/account (wipes the user's demos + account, ends session),
and support_contact in /api/me."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db   # noqa: E402


def _tmpdb(tmp_path, name="acc.sqlite"):
    db.DB_PATH = str(tmp_path / name)
    db.migrate()


def _demo(sha, owner_uid):
    p = {"steamid": "1", "name": "A", "kills": 10, "deaths": 10, "kd": 1.0, "adr": 75,
         "kast": 70, "hltv": 1.0, "open_wr": 50, "traded_pct": 20, "udr": 20}
    return {"source_sha1": sha, "map": "de_x", "version": 14, "rounds": [{"score_ct": 13, "score_t": 7}],
            "analytics": {"version": 9, "n_rounds": 20, "players": [p]}}


# ---- db: custom name survives Steam re-login --------------------------------
def test_display_name_locks_against_login(tmp_path):
    _tmpdb(tmp_path)
    uid = db.upsert_user("111", "SteamName")
    assert db.get_user(uid)["name"] == "SteamName"
    assert db.set_display_name(uid, "  Custom  ") == "Custom"          # trimmed
    assert db.get_user(uid)["name"] == "Custom"
    db.upsert_user("111", "NewSteamName")                              # a later login fetched a name
    assert db.get_user(uid)["name"] == "Custom"                        # ...must NOT overwrite the custom one
    assert db.set_display_name(uid, "   ") is None                     # blank rejected
    assert db.get_user(uid)["name"] == "Custom"


def test_owned_demo_ids(tmp_path):
    _tmpdb(tmp_path, "own.sqlite")
    uid = db.upsert_user("111", "Bob")
    db.index_demo(_demo("a" * 40, uid), "a" * 16, owner_user_id=uid)
    db.index_demo(_demo("b" * 40, uid), "b" * 16, owner_user_id=uid)
    assert sorted(db.owned_demo_ids(uid)) == ["a" * 40, "b" * 40]
    assert db.owned_demo_ids(None) == []


# ---- endpoints --------------------------------------------------------------
def test_account_name_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    import app
    db.DB_PATH = str(tmp_path / "n.sqlite")
    db.migrate()
    uid = db.upsert_user("111", "Bob")
    c = app.app.test_client()
    assert c.post("/api/account/name", json={"name": "X"}).status_code == 401   # anonymous
    with c.session_transaction() as s:
        s["uid"] = uid
    assert c.post("/api/account/name", json={"name": "  "}).status_code == 400   # blank
    r = c.post("/api/account/name", json={"name": "  NewName "})
    assert r.status_code == 200 and r.get_json()["name"] == "NewName"
    assert db.get_user(uid)["name"] == "NewName"


def test_account_delete_wipes_user_and_demos(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    import app
    db.DB_PATH = str(tmp_path / "d.sqlite")
    db.migrate()
    cache = tmp_path / "cache"
    uploads = tmp_path / "uploads"
    cache.mkdir()
    uploads.mkdir()
    monkeypatch.setattr(app, "CACHE", str(cache))      # keep delete_demo off the real dirs
    monkeypatch.setattr(app, "UPLOADS", str(uploads))
    uid = db.upsert_user("111", "Bob")
    db.index_demo(_demo("a" * 40, uid), "a" * 16, owner_user_id=uid)
    assert db.user_demo_count(uid) == 1
    c = app.app.test_client()
    assert c.delete("/api/account").status_code == 401             # anonymous
    with c.session_transaction() as s:
        s["uid"] = uid
    r = c.delete("/api/account")
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert db.get_user(uid) is None                                # account gone
    con = db.connect()
    n = con.execute("SELECT COUNT(*) n FROM demos WHERE sha1=?", ("a" * 40,)).fetchone()["n"]
    con.close()
    assert n == 0                                                  # their demo row wiped (not orphaned)
    assert c.get("/api/me").get_json()["authenticated"] is False   # session ended


def test_api_me_support_contact(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPPORT_CONTACT", "help@example.com")
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    import app
    db.DB_PATH = str(tmp_path / "s.sqlite")
    db.migrate()
    c = app.app.test_client()
    assert c.get("/api/me").get_json()["support_contact"] == "help@example.com"
