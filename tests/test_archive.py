"""Delete = remove the replay (raw .dem + parsed cache) and drop it from the library to free storage,
but retain a tiny compact .txt stats record so trends/profile/goals keep the match. Also covers the
admin orphan scanner. Temp dirs; no real parsing."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db          # noqa: E402
import app         # noqa: E402
import statsfile   # noqa: E402


def _seed_demo(sha, owner_uid, steamid, map_="de_dust2"):
    """Index row + per-player stat row + the user's library membership (full replay)."""
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


def test_statsfile_roundtrip_is_tiny_and_has_no_replay(tmp_path):
    base = str(tmp_path)
    match = {"map": "de_dust2", "rounds": 24, "score": "13-11", "created_at": "2026-06-18T00:00:00",
             "players": [{"steamid": "765", "name": "Zed", "kills": 20, "deaths": 15, "kd": 1.33,
                          "adr": 85.0, "kast": 72.0, "hltv": 1.15, "open_wr": 0.5, "traded_pct": 0.2, "udr": 0.6}]}
    path = statsfile.write(base, "abc", match)
    assert path.endswith(".txt") and os.path.getsize(path) < 1024     # tiny
    txt = open(path, encoding="utf-8").read()
    for banned in ("frame", "tick", "grenade", "positions", "x_", "trajectory"):
        assert banned not in txt.lower()
    back = statsfile.read(base, "abc")
    assert back["map"] == "de_dust2" and back["players"][0]["kills"] == 20.0 and back["players"][0]["name"] == "Zed"


def test_delete_removes_replay_keeps_stats(tmp_path, monkeypatch):
    db.DB_PATH = str(tmp_path / "del.sqlite")
    db.migrate()
    monkeypatch.setattr(app, "CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(app, "UPLOADS", str(tmp_path / "uploads"))
    monkeypatch.setattr(app, "STATS", str(tmp_path / "stats"))
    os.makedirs(app.CACHE); os.makedirs(app.UPLOADS)
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    uid = db.upsert_user("76561190000000300", "Zed")
    sha = "feedface00feedface00feedface00feedface00"
    _seed_demo(sha, uid, "76561190000000300")
    # the heavy files that delete should remove
    key = sha[:16]
    open(os.path.join(app.CACHE, key + ".json"), "w").write('{"replay":"big"}')
    open(os.path.join(app.UPLOADS, key + ".dem"), "wb").write(b"X" * 5000)

    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid
    app._rl_hits.clear()
    r = c.delete("/api/demo/" + sha)
    assert r.status_code == 200 and r.get_json()["ok"]
    assert r.get_json()["freed_bytes"] >= 5000                       # large files freed

    # heavy files gone
    assert not os.path.exists(os.path.join(app.CACHE, key + ".json"))
    assert not os.path.exists(os.path.join(app.UPLOADS, key + ".dem"))
    # tiny .txt retained, stats survive
    assert statsfile.exists(app.STATS, sha)
    assert os.path.getsize(statsfile.path_for(app.STATS, sha)) < 1024
    t = db.player_trends("76561190000000300", scope=None)
    assert t["n_matches"] == 1                                        # trend still counts it
    assert db.user_demo_count(uid) == 0                              # slot freed (flagged stats-only)


def test_delete_keeps_shared_replay_until_last_member(tmp_path):
    db.DB_PATH = str(tmp_path / "shared.sqlite")
    db.migrate()
    a = db.upsert_user("76561190000000301", "A")
    b = db.upsert_user("76561190000000302", "B")
    sha = "0011223344556677889900112233445566778899"
    _seed_demo(sha, a, "76561190000000301")
    con = db.connect()
    con.execute("INSERT INTO user_demos(user_id,sha1,created_at,archived) VALUES(?,?,?,0)",
                (b, sha, "2026-06-18T00:00:00"))
    con.commit(); con.close()
    assert db.set_archived(a, sha, 1) == 1     # B still holds a full replay -> shared cache stays
    assert db.set_archived(b, sha, 1) == 0     # last full member gone -> safe to free


def test_orphan_scan_and_clean(tmp_path, monkeypatch):
    db.DB_PATH = str(tmp_path / "orph.sqlite")
    db.migrate()
    monkeypatch.setattr(app, "CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(app, "UPLOADS", str(tmp_path / "uploads"))
    os.makedirs(app.CACHE); os.makedirs(app.UPLOADS)
    # a kept raw .dem (reclaimable), a stale temp, and a cache file that must NOT be touched
    open(os.path.join(app.UPLOADS, "abc123def456abcd.dem"), "wb").write(b"D" * 4000)
    open(os.path.join(app.UPLOADS, "_jobup_stale.dem.gz"), "wb").write(b"T" * 2000)
    open(os.path.join(app.CACHE, "abc123def456abcd.json"), "w").write("{}")
    import time as _t                                          # age them past the 30-min in-flight guard
    old = _t.time() - 7200
    os.utime(os.path.join(app.UPLOADS, "abc123def456abcd.dem"), (old, old))
    os.utime(os.path.join(app.UPLOADS, "_jobup_stale.dem.gz"), (old, old))

    scan = app._scan_orphans()
    assert scan["n_dems"] == 1 and scan["n_temps"] == 1
    assert scan["total_bytes"] >= 6000

    # admin clean
    monkeypatch.setenv("ADMIN_STEAM_IDS", "76561190000000303")
    uid = db.upsert_user("76561190000000303", "Admin")
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid
    app._rl_hits.clear()
    r = c.post("/api/admin/orphans/clean")
    assert r.status_code == 200 and r.get_json()["removed"] == 2
    assert not os.path.exists(os.path.join(app.UPLOADS, "abc123def456abcd.dem"))
    assert not os.path.exists(os.path.join(app.UPLOADS, "_jobup_stale.dem.gz"))
    assert os.path.exists(os.path.join(app.CACHE, "abc123def456abcd.json"))   # cache untouched
