"""#22 archive flow: removing a demo's heavy replay frees a Free-plan slot but KEEPS the compact
stats (so trends/profile survive), and the demo still surfaces in the library as 'archived'."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db    # noqa: E402
import app   # noqa: E402


def _seed_demo(sha, owner_uid, steamid, map_="de_dust2"):
    """Insert a parsed-demo index row + a per-player stat row + the user's library membership."""
    c = db.connect()
    try:
        c.execute("INSERT INTO demos(sha1,key,map,rounds,created_at,score,schema_version,"
                  "analytics_version,owner_user_id) VALUES(?,?,?,?,?,?,?,?,?)",
                  (sha, sha[:16], map_, 24, "2026-06-18T00:00:00", "13-11", 1, 1, owner_uid))
        c.execute("INSERT INTO demo_players(sha1,steamid,name,kills,deaths,kd,adr,kast,hltv,"
                  "open_wr,traded_pct,udr) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (sha, steamid, "Zed", 20, 15, 1.33, 85.0, 72.0, 1.15, 0.5, 0.2, 0.6))
        c.execute("INSERT INTO user_demos(user_id,sha1,created_at,archived) VALUES(?,?,?,0)",
                  (owner_uid, sha, "2026-06-18T00:00:00"))
        c.commit()
    finally:
        c.close()


def test_archive_frees_slot_and_keeps_stats(tmp_path):
    db.DB_PATH = str(tmp_path / "arch.sqlite")
    db.migrate()
    uid = db.upsert_user("76561190000000200", "Zed")
    sha = "abcdef0123456789abcdef0123456789abcdef01"
    _seed_demo(sha, uid, "76561190000000200")

    assert db.user_demo_count(uid) == 1                 # counts toward the Free cap before archiving
    remaining_full = db.set_archived(uid, sha, 1)
    assert remaining_full == 0                          # no other member holds a full copy -> caller may wipe files
    assert db.user_demo_count(uid) == 0                 # archived -> slot freed

    # stats survive: the per-player index row is untouched, so trends still count the match
    t = db.player_trends("76561190000000200", scope=None)
    assert t["n_matches"] == 1

    # and it still shows in the library as archived
    rows = db.archived_library_rows({"uid": uid, "team_ids": []})
    assert len(rows) == 1
    assert rows[0]["id"] == sha and rows[0]["map"] == "de_dust2"
    assert rows[0]["archived"] is True and rows[0]["score"] == {"ct": 13, "t": 11}


def test_archive_keeps_shared_cache_until_last_member(tmp_path):
    db.DB_PATH = str(tmp_path / "arch2.sqlite")
    db.migrate()
    a = db.upsert_user("76561190000000201", "A")
    b = db.upsert_user("76561190000000202", "B")
    sha = "0011223344556677889900112233445566778899"
    _seed_demo(sha, a, "76561190000000201")
    con = db.connect()
    con.execute("INSERT INTO user_demos(user_id,sha1,created_at,archived) VALUES(?,?,?,0)",
                (b, sha, "2026-06-18T00:00:00"))
    con.commit(); con.close()

    # A archives -> B still holds a full copy, so the shared cache must NOT be wiped
    assert db.set_archived(a, sha, 1) == 1              # 1 member (B) still full
    # B archives -> now nobody holds it full -> safe to wipe
    assert db.set_archived(b, sha, 1) == 0


def test_archive_endpoint(tmp_path, monkeypatch):
    db.DB_PATH = str(tmp_path / "arche.sqlite")
    db.migrate()
    monkeypatch.setattr(app, "CACHE", str(tmp_path))
    monkeypatch.setattr(app, "UPLOADS", str(tmp_path))
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    uid = db.upsert_user("76561190000000203", "Zed3")
    sha = "feedface00feedface00feedface00feedface00"
    _seed_demo(sha, uid, "76561190000000203")
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid
    app._rl_hits.clear()
    r = c.post("/api/demo/" + sha + "/archive")
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] and j["archived"] is True
    assert db.user_demo_count(uid) == 0                 # slot freed live
