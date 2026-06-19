"""Dashboard workspace switch: /api/dashboard?workspace=personal|team:<id> shows ONE context --
personal-only matches/goals/stats, or one team's. Cross-context bleed must not happen, and a team a
user isn't on is a 403. Temp DBs; matches seeded directly (dashboard reads the SQLite index)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db        # noqa: E402
import app       # noqa: E402


def _seed(con, sha, owner_uid, steamid, team_id=None, kills=20):
    con.execute("INSERT OR IGNORE INTO demos(sha1,key,map,rounds,created_at,score,schema_version,"
                "analytics_version,owner_user_id) VALUES(?,?,?,?,?,?,?,?,?)",
                (sha, sha[:16], "de_dust2", 24, "2026-06-18T00:00:00", "13-11", 1, 1, owner_uid))
    con.execute("INSERT INTO demo_players(sha1,steamid,name,kills,deaths,kd,adr,kast,hltv,"
                "open_wr,traded_pct,udr) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (sha, steamid, "Zed", kills, 15, 1.33, 85.0, 72.0, 1.15, 0.5, 0.2, 0.6))
    con.execute("INSERT INTO user_demos(user_id,sha1,team_id,created_at,archived) VALUES(?,?,?,?,0)",
                (owner_uid, sha, team_id, "2026-06-18T00:00:00"))


def _client(uid):
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid
    app._rl_hits.clear()
    return c


def _dash(c, workspace):
    return c.get("/api/dashboard?workspace=" + workspace)


SID = "76561190000000900"


def _setup(tmp_path, name):
    db.DB_PATH = str(tmp_path / name)
    db.migrate()


def test_personal_workspace_shows_only_personal_matches(tmp_path, monkeypatch):
    _setup(tmp_path, "p.sqlite")
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    uid = db.upsert_user(SID, "Zed")
    tid = db.create_team("Alpha", uid)["id"]
    con = db.connect()
    _seed(con, "a" * 40, uid, SID, team_id=None)           # personal
    _seed(con, "b" * 40, uid, SID, team_id=tid)            # shared to Alpha
    con.commit(); con.close()
    c = _client(uid)
    pj = _dash(c, "personal").get_json()
    assert [m["id"] for m in pj["matches"]] == ["a" * 40]   # personal only
    assert pj["workspace"] == "personal"
    assert pj["me"]["n_matches"] == 1                       # stats scoped to the personal match


def test_team_workspace_shows_only_team_matches(tmp_path, monkeypatch):
    _setup(tmp_path, "t.sqlite")
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    uid = db.upsert_user(SID, "Zed")
    tid = db.create_team("Alpha", uid)["id"]
    con = db.connect()
    _seed(con, "a" * 40, uid, SID, team_id=None)
    _seed(con, "b" * 40, uid, SID, team_id=tid)
    con.commit(); con.close()
    c = _client(uid)
    tj = _dash(c, "team:%d" % tid).get_json()
    assert [m["id"] for m in tj["matches"]] == ["b" * 40]   # team only
    assert tj["workspace"] == "team:%d" % tid


def test_teammate_sees_shared_team_match(tmp_path, monkeypatch):
    _setup(tmp_path, "tm.sqlite")
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    a = db.upsert_user(SID, "A")
    b = db.upsert_user("76561190000000901", "B")
    team = db.create_team("Alpha", a)
    tid = team["id"]
    db.join_team(team["invite_code"], b)
    con = db.connect()
    _seed(con, "c" * 40, a, SID, team_id=tid)              # A shared with the team
    con.commit(); con.close()
    bc = _client(b)
    tj = _dash(bc, "team:%d" % tid).get_json()
    assert [m["id"] for m in tj["matches"]] == ["c" * 40]   # B sees the team-shared match
    assert _dash(bc, "personal").get_json()["matches"] == []  # but nothing in B's personal


def test_foreign_team_is_403(tmp_path, monkeypatch):
    _setup(tmp_path, "f.sqlite")
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    owner = db.upsert_user(SID, "Owner")
    other = db.upsert_user("76561190000000902", "Other")
    foreign = db.create_team("Secret", other)["id"]        # owner is NOT a member
    c = _client(owner)
    assert _dash(c, "team:%d" % foreign).status_code == 403
    assert _dash(c, "team:999999").status_code == 403       # unknown team too


def test_goals_do_not_bleed_across_workspaces(tmp_path, monkeypatch):
    _setup(tmp_path, "g.sqlite")
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    uid = db.upsert_user(SID, "Zed")
    tid = db.create_team("Alpha", uid)["id"]
    c = _client(uid)
    # one personal goal, one team goal (created through the real endpoint)
    assert c.post("/api/goals", json={"metric": "adr", "target": 85}).status_code == 200
    assert c.post("/api/goals", json={"metric": "kd", "target": 1.2, "team_id": tid}).status_code == 200
    pmetrics = {g["metric"] for g in _dash(c, "personal").get_json()["open_goals"]}
    tmetrics = {g["metric"] for g in _dash(c, "team:%d" % tid).get_json()["open_goals"]}
    assert pmetrics == {"adr"} and tmetrics == {"kd"}       # each side sees only its own


def test_local_mode_unsplit(tmp_path, monkeypatch):
    """No-auth/local mode has no workspace split -- the dashboard still loads and shows everything."""
    _setup(tmp_path, "l.sqlite")
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    c = app.app.test_client()
    app._rl_hits.clear()
    r = c.get("/api/dashboard")
    assert r.status_code == 200 and "matches" in r.get_json()
