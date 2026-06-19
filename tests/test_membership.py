"""Per-user library membership model: two players who upload the SAME match each get their own copy
automatically (sharing one cached parse), quota counts memberships, and deletion is refcounted (the
shared parse/cache is only wiped when the LAST member is gone)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db   # noqa: E402


def _p(sid, name):
    return {"steamid": sid, "name": name, "kills": 10, "deaths": 10, "kd": 1.0,
            "adr": 75, "kast": 70, "hltv": 1.0, "open_wr": 50, "traded_pct": 20, "udr": 20}


def _match(sha, mp, players):
    return {"source_sha1": sha, "map": mp, "version": 14, "duration": 1800.0,
            "rounds": [{"score_ct": 13, "score_t": 7}],
            "analytics": {"version": 9, "n_rounds": 20, "players": players}}


def _tmpdb(tmp_path, name="mem.sqlite"):
    db.DB_PATH = str(tmp_path / name)
    db.migrate()


def _scope(uid, ownerless=False):
    return {"uid": uid, "team_ids": db.team_ids_for_user(uid), "ownerless": ownerless}


def test_coupload_both_get_own_copy(tmp_path):
    _tmpdb(tmp_path)
    u1 = db.upsert_user("1", "U1")
    u2 = db.upsert_user("2", "U2")
    # the SAME match file (same sha1) uploaded by two players on opposing teams
    db.index_demo(_match("a" * 40, "de_a", [_p("1", "U1")]), "a" * 16, owner_user_id=u1)
    db.index_demo(_match("a" * 40, "de_a", [_p("1", "U1")]), "a" * 16, owner_user_id=u2)
    db.index_demo(_match("b" * 40, "de_b", [_p("1", "U1")]), "b" * 16, owner_user_id=u1)   # only u1's
    assert db.demo_member_count("a" * 40) == 2                 # both have the shared match
    # each sees the shared match in their own library...
    assert "de_a" in {m["map"] for m in db.list_matches(scope=_scope(u1))}
    assert "de_a" in {m["map"] for m in db.list_matches(scope=_scope(u2))}
    # ...but u2 does NOT see u1's solo demo
    assert "de_b" not in {m["map"] for m in db.list_matches(scope=_scope(u2))}
    # quota counts each user's own memberships (not the global demo count)
    assert db.user_demo_count(u1) == 2 and db.user_demo_count(u2) == 1
    # accessibility follows membership
    assert db.accessible("a" * 40, _scope(u2)) and not db.accessible("b" * 40, _scope(u2))


def test_refcount_delete_keeps_for_others(tmp_path):
    _tmpdb(tmp_path, "rc.sqlite")
    u1 = db.upsert_user("1", "U1")
    u2 = db.upsert_user("2", "U2")
    db.index_demo(_match("a" * 40, "de_a", [_p("1", "U1")]), "a" * 16, owner_user_id=u1)
    db.index_demo(_match("a" * 40, "de_a", [_p("1", "U1")]), "a" * 16, owner_user_id=u2)
    assert db.remove_membership(u1, "a" * 40) == 1             # 1 member (u2) remains
    assert db.demo_member_count("a" * 40) == 1
    assert "de_a" not in {m["map"] for m in db.list_matches(scope=_scope(u1))}   # u1 dropped it
    assert "de_a" in {m["map"] for m in db.list_matches(scope=_scope(u2))}       # u2 still has it
    assert db.remove_membership(u2, "a" * 40) == 0             # last member gone -> caller wipes


def test_delete_endpoint_refcounts(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    import app
    db.DB_PATH = str(tmp_path / "de.sqlite")
    db.migrate()
    cache = tmp_path / "cache"
    uploads = tmp_path / "uploads"
    cache.mkdir()
    uploads.mkdir()
    monkeypatch.setattr(app, "CACHE", str(cache))
    monkeypatch.setattr(app, "UPLOADS", str(uploads))
    u1 = db.upsert_user("1", "U1")
    u2 = db.upsert_user("2", "U2")
    db.index_demo(_match("a" * 40, "de_a", [_p("1", "U1")]), "a" * 16, owner_user_id=u1)
    db.index_demo(_match("a" * 40, "de_a", [_p("1", "U1")]), "a" * 16, owner_user_id=u2)
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = u1
    r = c.delete("/api/demo/" + "a" * 40)
    assert r.status_code == 200 and r.get_json().get("shared") is True   # u2 still has a full replay
    # u1 no longer sees it in their library; u2 still does (delete flags u1's copy, keeps u2's)
    assert db.visible_predicate(_scope(u1))("a" * 40) is False
    assert db.visible_predicate(_scope(u2))("a" * 40) is True
    con = db.connect()
    assert con.execute("SELECT COUNT(*) n FROM demos WHERE sha1=?", ("a" * 40,)).fetchone()["n"] == 1
    con.close()
    with c.session_transaction() as s:
        s["uid"] = u2
    assert c.delete("/api/demo/" + "a" * 40).status_code == 200          # last full member -> free heavy files
    # stats are KEPT (the demos index row survives for trends/profile), but neither user sees it now
    con = db.connect()
    assert con.execute("SELECT COUNT(*) n FROM demos WHERE sha1=?", ("a" * 40,)).fetchone()["n"] == 1
    con.close()
    assert db.visible_predicate(_scope(u2))("a" * 40) is False


def test_account_delete_keeps_coowned_demo(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    import app
    db.DB_PATH = str(tmp_path / "ad2.sqlite")
    db.migrate()
    cache = tmp_path / "cache"
    uploads = tmp_path / "uploads"
    cache.mkdir()
    uploads.mkdir()
    monkeypatch.setattr(app, "CACHE", str(cache))
    monkeypatch.setattr(app, "UPLOADS", str(uploads))
    u1 = db.upsert_user("1", "U1")
    u2 = db.upsert_user("2", "U2")
    db.index_demo(_match("a" * 40, "de_a", [_p("1", "U1")]), "a" * 16, owner_user_id=u1)
    db.index_demo(_match("a" * 40, "de_a", [_p("1", "U1")]), "a" * 16, owner_user_id=u2)
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = u1
    assert c.delete("/api/account").status_code == 200
    assert db.get_user(u1) is None
    assert db.demo_member_count("a" * 40) == 1                 # u2 still has the match
    con = db.connect()
    assert con.execute("SELECT COUNT(*) n FROM demos WHERE sha1=?", ("a" * 40,)).fetchone()["n"] == 1
    con.close()
