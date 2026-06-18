"""Personal vs shared-team goals over the HTTP API: who can see, edit, and delete which goals."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db       # noqa: E402
import goals    # noqa: E402
import app      # noqa: E402


def _mk(client, uid):
    with client.session_transaction() as s:
        s["uid"] = uid


def _ids(client):
    return {g["id"] for g in client.get("/api/goals").get_json()["goals"]}


def test_personal_vs_shared_goals(tmp_path, monkeypatch):
    db.DB_PATH = str(tmp_path / "g.sqlite")
    db.migrate()
    monkeypatch.setattr(goals, "_matches", lambda *a, **k: [])     # no cache I/O; grading -> no_data
    u1 = db.upsert_user("1", "Owner")
    u2 = db.upsert_user("2", "Member")
    u3 = db.upsert_user("3", "Outsider")
    t = db.create_team("Squad", u1)
    tid = t["id"]
    db.join_team(t["invite_code"], u2)                              # u1 owner, u2 member; u3 outside
    c = app.app.test_client()

    _mk(c, u1)
    personal = c.post("/api/goals", json={"metric": "adr", "target": 85}).get_json()["goal"]
    shared = c.post("/api/goals", json={"metric": "adr", "target": 85, "team_id": tid}).get_json()["goal"]
    assert personal["owner_user_id"] == u1 and personal["team_id"] is None
    assert shared["team_id"] == tid                                # accepted: u1 is in the team
    # u1's "share with a team I'm not in" attempt is ignored -> personal
    sneaky = c.post("/api/goals", json={"metric": "kast", "target": 70, "team_id": 999}).get_json()["goal"]
    assert sneaky["team_id"] is None

    assert _ids(c) == {personal["id"], shared["id"], sneaky["id"]}  # owner sees all of his
    _mk(c, u2)
    assert _ids(c) == {shared["id"]}                               # member sees ONLY the shared team goal
    _mk(c, u3)
    assert _ids(c) == set()                                        # outsider sees nothing

    # permissions: member may edit the shared goal's status, but not delete it; outsider can't touch it
    _mk(c, u2)
    assert c.put("/api/goals/%s" % shared["id"], json={"status": "drilling"}).status_code == 200
    assert c.delete("/api/goals/%s" % shared["id"]).status_code == 403       # member isn't owner/team-owner
    assert c.put("/api/goals/%s" % personal["id"], json={"status": "fixed"}).status_code == 403  # not visible to u2
    _mk(c, u3)
    assert c.delete("/api/goals/%s" % shared["id"]).status_code == 403       # outsider
    _mk(c, u1)
    assert c.delete("/api/goals/%s" % shared["id"]).status_code == 200       # creator (and team owner) can delete
    assert _ids(c) == {personal["id"], sneaky["id"]}
