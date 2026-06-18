"""Team membership management (DB teams, not the local config): leave (members), remove member +
disband (owner), and the gated endpoints. Demos shared to a team revert to private on leave/disband."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db   # noqa: E402


def _p(sid, name):
    return {"steamid": sid, "name": name, "kills": 10, "deaths": 10, "kd": 1.0, "adr": 75,
            "kast": 70, "hltv": 1.0, "open_wr": 50, "traded_pct": 20, "udr": 20}


def _match(sha, players):
    return {"source_sha1": sha, "map": "de_x", "version": 14, "duration": 1800.0,
            "rounds": [{"score_ct": 13, "score_t": 7}],
            "analytics": {"version": 9, "n_rounds": 20, "players": players}}


def _shared_team(uid, sha, con):
    return con.execute("SELECT team_id FROM user_demos WHERE user_id=? AND sha1=?", (uid, sha)).fetchone()["team_id"]


def test_leave_remove_disband(tmp_path):
    db.DB_PATH = str(tmp_path / "team.sqlite")
    db.migrate()
    u1 = db.upsert_user("1", "Owner")
    u2 = db.upsert_user("2", "Member")
    u3 = db.upsert_user("3", "Member2")
    t = db.create_team("Squad", u1)
    tid = t["id"]
    db.join_team(t["invite_code"], u2)
    db.join_team(t["invite_code"], u3)
    # u2 shares a demo to the team; leaving should unshare it
    db.index_demo(_match("a" * 40, [_p("2", "Member")]), "a" * 16, owner_user_id=u2)
    assert db.set_demo_team("a" * 40, tid, u2) is True
    tv = db.teams_for_user(u1)[0]
    assert tv["member_count"] == 3 and len(tv["members"]) == 3        # roster exposed for the owner UI

    assert db.leave_team(u1, tid) is False                           # owner can't leave (disbands instead)
    assert db.leave_team(u2, tid) is True                            # member leaves
    assert tid not in db.team_ids_for_user(u2)
    con = db.connect()
    assert _shared_team(u2, "a" * 40, con) is None                   # their shared demo reverted to private
    con.close()

    db.join_team(t["invite_code"], u2)                               # rejoin to test remove/disband perms
    assert db.remove_member(tid, u1, u2) is False                    # non-owner can't remove
    assert db.remove_member(tid, u1, u1) is False                    # owner can't remove self
    assert db.remove_member(tid, u3, u1) is True                     # owner removes u3
    assert tid not in db.team_ids_for_user(u3)

    assert db.disband_team(tid, u2) is False                         # non-owner can't disband
    assert db.disband_team(tid, u1) is True                          # owner disbands
    con = db.connect()
    assert con.execute("SELECT COUNT(*) n FROM teams WHERE id=?", (tid,)).fetchone()["n"] == 0
    assert con.execute("SELECT COUNT(*) n FROM team_members WHERE team_id=?", (tid,)).fetchone()["n"] == 0
    con.close()
    assert db.team_ids_for_user(u1) == []


def test_team_management_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    import app
    db.DB_PATH = str(tmp_path / "te.sqlite")
    db.migrate()
    u1 = db.upsert_user("1", "Owner")
    u2 = db.upsert_user("2", "Member")
    t = db.create_team("Sq", u1)
    tid = t["id"]
    db.join_team(t["invite_code"], u2)
    c = app.app.test_client()
    assert c.post("/api/teams/%d/leave" % tid).status_code == 401    # anonymous
    with c.session_transaction() as s:
        s["uid"] = u2
    assert c.post("/api/teams/%d/leave" % tid).status_code == 200    # member leaves
    assert tid not in db.team_ids_for_user(u2)
    db.join_team(t["invite_code"], u2)
    assert c.delete("/api/teams/%d" % tid).status_code == 403        # u2 (non-owner) can't disband
    with c.session_transaction() as s:
        s["uid"] = u1
    assert c.post("/api/teams/%d/remove" % tid, json={"user_id": u2}).status_code == 200   # owner removes
    assert tid not in db.team_ids_for_user(u2)
    assert c.delete("/api/teams/%d" % tid).status_code == 200        # owner disbands
    assert db.team_ids_for_user(u1) == []
