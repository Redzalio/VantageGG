"""Extended /api/dashboard/analytics contract: utility/flash/perf aggregates on map_stats[], the
top-level utility{} block, and per-player maps/utility/perf -- all roster/scope-respecting and
honest about missing data (None/omitted, never a fabricated 0). Temp DB; matches seeded straight into
the SQLite index (the dashboard reads from the index, not the cache)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db        # noqa: E402
import app       # noqa: E402

SID = "76561190000000900"            # "you"
SID2 = "76561190000000901"           # a squadmate (shares >= 2 matches)
OPP = "76561190000000999"            # an opponent -- must NOT enter roster aggregates

_CORE = ("kills", "deaths", "kd", "adr", "kast", "hltv", "open_wr", "traded_pct", "udr")
_EXTRA = ("smokes", "flashes_thrown", "hes", "molotovs", "enemy_flashed", "team_flashed",
          "blind_time", "he_dmg", "headshot_accuracy", "he_dmg_per_he", "accuracy",
          "flashes_hit_foe_per_game", "flashes_hit_friend_per_game",
          "total_flash_blind_duration_per_game")


def _setup(tmp_path, name):
    db.DB_PATH = str(tmp_path / name)
    db.migrate()


def _client(uid):
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid
    app._rl_hits.clear()
    return c


def _seed(con, sha, owner_uid, mp, rows, created="2026-06-18T00:00:00", team_id=None):
    """rows: list of dicts with at least steamid/name; missing core->defaults, missing extra->NULL."""
    con.execute("INSERT OR IGNORE INTO demos(sha1,key,map,rounds,created_at,score,schema_version,"
                "analytics_version,owner_user_id) VALUES(?,?,?,?,?,?,?,?,?)",
                (sha, sha[:16], mp, 24, created, "13-11", 1, 11, owner_uid))
    con.execute("INSERT OR IGNORE INTO user_demos(user_id,sha1,team_id,created_at,archived) "
                "VALUES(?,?,?,?,0)", (owner_uid, sha, team_id, created))
    coredefault = {"kills": 18, "deaths": 15, "kd": 1.2, "adr": 80, "kast": 70,
                   "hltv": 1.1, "open_wr": 50, "traded_pct": 20, "udr": 25}
    for r in rows:
        vals = {**coredefault, **{k: r[k] for k in _CORE if k in r}}
        extra = {k: r.get(k) for k in _EXTRA}
        cols = ["sha1", "steamid", "name", *_CORE, *_EXTRA]
        data = [sha, r["steamid"], r.get("name", "P"), *[vals[k] for k in _CORE],
                *[extra[k] for k in _EXTRA]]
        con.execute("INSERT OR REPLACE INTO demo_players(" + ",".join(cols) + ") VALUES("
                    + ",".join(["?"] * len(cols)) + ")", data)


def _full(sid, mp, name="Zed", **kw):
    base = dict(steamid=sid, name=name, smokes=5, flashes_thrown=4, hes=2, molotovs=1,
                enemy_flashed=3, team_flashed=1, blind_time=6.0, he_dmg=40.0,
                headshot_accuracy=22.0, he_dmg_per_he=20.0, accuracy=18.0,
                flashes_hit_foe_per_game=3, flashes_hit_friend_per_game=1,
                total_flash_blind_duration_per_game=6.0)
    base.update(kw)
    base["map"] = mp
    return base


# ---------------------------------------------------------------------------
def test_map_stats_and_utility_block_populated(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    _setup(tmp_path, "ext1.sqlite")
    uid = db.upsert_user(SID, "Zed")
    db.upsert_user(SID2, "Mate")
    con = db.connect()
    # SID + squadmate SID2 share TWO dust2 matches (so SID2 auto-joins the personal squad, shared>=2);
    # an opponent row (OPP, only in one match) must NOT enter roster aggregates.
    _seed(con, "a" * 40, uid, "de_dust2",
          [_full(SID, "de_dust2"), _full(SID2, "de_dust2", name="Mate"),
           _full(OPP, "de_dust2", name="Enemy", smokes=99, he_dmg=999.0)],
          created="2026-06-18T00:00:00")
    _seed(con, "b" * 40, uid, "de_dust2",
          [_full(SID, "de_dust2"), _full(SID2, "de_dust2", name="Mate")],
          created="2026-06-19T00:00:00")
    _seed(con, "c" * 40, uid, "de_mirage",
          [_full(SID, "de_mirage", smokes=3)], created="2026-06-20T00:00:00")
    con.commit(); con.close()
    r = _client(uid).get("/api/dashboard/analytics?workspace=personal")
    assert r.status_code == 200
    j = r.get_json()
    assert SID2 in {m["steamid"] for m in j["roster_members"]}   # squadmate auto-detected
    assert OPP not in {m["steamid"] for m in j["roster_members"]}  # opponent excluded

    # map_stats rows carry the new utility fields
    ms = {m["map"]: m for m in j["overview"]["map_stats"]}
    assert "de_dust2" in ms and "de_mirage" in ms
    d2 = ms["de_dust2"]
    assert d2["util_per_round"] is not None
    assert d2["he_dmg"] is not None
    assert "flash" in d2 and d2["flash"]["enemies"] is not None
    # flash.enemies = roster players on dust2: you(3)+mate(3) x2 matches = 12; opponent's excluded
    assert d2["flash"]["enemies"] == 12.0
    assert d2["flash"]["thrown"] == 16.0          # 4 flashes x (2 players x 2 matches)

    # top-level utility block, roster-scoped, sorted by volume
    u = j["utility"]
    assert set(u["by_type"]) == {"smoke", "flash", "he", "molotov"}
    # smoke = you+mate on dust2 (5x4=20) + you on mirage (3) = 23 (opponent's 99 excluded)
    assert u["by_type"]["smoke"] == 23.0
    maps_in_bymap = [row["map"] for row in u["by_map"]]
    assert maps_in_bymap and maps_in_bymap[0] == "de_dust2"     # higher volume first
    assert any(row["map"] == "de_dust2" for row in u["flash_by_map"])
    assert any(row["map"] == "de_dust2" for row in u["dmg_by_map"])


def test_players_have_maps_utility_perf(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    _setup(tmp_path, "ext2.sqlite")
    uid = db.upsert_user(SID, "Zed")
    con = db.connect()
    _seed(con, "a" * 40, uid, "de_dust2", [_full(SID, "de_dust2")], created="2026-06-18T00:00:00")
    _seed(con, "b" * 40, uid, "de_dust2", [_full(SID, "de_dust2", hltv=1.5)], created="2026-06-19T00:00:00")
    _seed(con, "c" * 40, uid, "de_nuke", [_full(SID, "de_nuke")], created="2026-06-20T00:00:00")
    con.commit(); con.close()
    j = _client(uid).get("/api/dashboard/analytics?workspace=personal").get_json()
    me = next(p for p in j["players"] if p["steamid"] == SID)

    # per-map split: dust2 has 2 matches, nuke 1
    by = {x["map"]: x for x in me["maps"]}
    assert by["de_dust2"]["n"] == 2 and by["de_nuke"]["n"] == 1
    assert by["de_dust2"]["hltv"] is not None

    # per-game utility averages present
    assert me["utility"]["smokes"] == 5.0 and me["utility"]["enemy_flashed"] == 3.0
    assert "avg_blind" in me["utility"]
    # perf averages present + None-aware
    assert me["perf"]["headshot_accuracy"] == 22.0
    assert me["perf"]["accuracy"] == 18.0


def test_honest_empty_when_no_utility_data(tmp_path, monkeypatch):
    """No-fake-data: when utility/flash/perf columns are all NULL (e.g. demo predates them or no
    player_blind), the flash block is omitted, the utility arrays are empty, and perf keys are omitted
    -- never a guessed 0."""
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    _setup(tmp_path, "ext3.sqlite")
    uid = db.upsert_user(SID, "Zed")
    con = db.connect()
    # only core stats, all extra columns NULL
    _seed(con, "a" * 40, uid, "de_dust2", [{"steamid": SID, "name": "Zed"}])
    con.commit(); con.close()
    j = _client(uid).get("/api/dashboard/analytics?workspace=personal").get_json()

    d2 = next(m for m in j["overview"]["map_stats"] if m["map"] == "de_dust2")
    # udr is a core stat (seeded 25 by default) so util_per_round IS present; the genuinely-missing
    # he_dmg and the whole flash block must be omitted/None -- never a fabricated 0.
    assert d2["util_per_round"] == 25.0
    assert d2.get("he_dmg") is None
    assert "flash" not in d2                        # no flash data -> omitted, not a 0-block

    u = j["utility"]
    assert u["by_type"] == {"smoke": 0, "flash": 0, "he": 0, "molotov": 0}
    assert u["by_map"] == [] and u["flash_by_map"] == [] and u["dmg_by_map"] == []

    me = next(p for p in j["players"] if p["steamid"] == SID)
    assert me["utility"] == {} or "smokes" not in me["utility"]
    assert me["perf"] == {}                         # no perf samples -> omitted entirely


def test_existing_keys_preserved(tmp_path, monkeypatch):
    """Additive contract: all original keys still present and unchanged in shape."""
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    _setup(tmp_path, "ext4.sqlite")
    uid = db.upsert_user(SID, "Zed")
    con = db.connect()
    _seed(con, "a" * 40, uid, "de_dust2", [_full(SID, "de_dust2")])
    con.commit(); con.close()
    j = _client(uid).get("/api/dashboard/analytics?workspace=personal").get_json()
    for k in ("match_count", "matches", "players", "overview", "recurring",
              "roster_mode", "roster_members"):
        assert k in j, "lost top-level key " + k
    ov = j["overview"]
    for k in ("top_maps", "averages", "form", "map_stats", "next_focus"):
        assert k in ov, "lost overview key " + k
    assert set(j["overview"]["form"]) == {"last3", "last5", "all", "delta3"}
    # original map_stats fields still there
    d2 = next(m for m in ov["map_stats"] if m["map"] == "de_dust2")
    for k in ("map", "count", "adr", "kast", "hltv", "open_wr", "traded_pct", "udr"):
        assert k in d2


def test_scope_excludes_other_workspace(tmp_path, monkeypatch):
    """Team-shared utility must not bleed into the personal utility block (scope correctness)."""
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    _setup(tmp_path, "ext5.sqlite")
    uid = db.upsert_user(SID, "Zed")
    tid = db.create_team("Alpha", uid)["id"]
    con = db.connect()
    _seed(con, "a" * 40, uid, "de_dust2", [_full(SID, "de_dust2", smokes=7)], team_id=None)   # personal
    _seed(con, "b" * 40, uid, "de_mirage", [_full(SID, "de_mirage", smokes=11)], team_id=tid)  # team
    con.commit(); con.close()
    c = _client(uid)
    pj = c.get("/api/dashboard/analytics?workspace=personal").get_json()
    tj = c.get("/api/dashboard/analytics?workspace=team:%d" % tid).get_json()
    assert pj["utility"]["by_type"]["smoke"] == 7.0          # personal only
    assert tj["utility"]["by_type"]["smoke"] == 11.0         # team only
    assert [m["map"] for m in pj["overview"]["map_stats"]] == ["de_dust2"]
    assert [m["map"] for m in tj["overview"]["map_stats"]] == ["de_mirage"]
