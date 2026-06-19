"""#23: db.library_membership classifies each visible demo as personal vs team-shared, from a
given user's point of view, so the library can split into Personal / per-team tabs. Temp DBs only."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db   # noqa: E402


def _seed(con, sha, uid, team_id=None):
    con.execute("INSERT OR IGNORE INTO demos(sha1,key,map,rounds,created_at,score,schema_version,"
                "analytics_version,owner_user_id) VALUES(?,?,?,?,?,?,?,?,?)",
                (sha, sha[:16], "de_dust2", 24, "2026-06-18T00:00:00", "13-11", 1, 1, uid))
    con.execute("INSERT INTO user_demos(user_id,sha1,team_id,created_at,archived) VALUES(?,?,?,?,0)",
                (uid, sha, team_id, "2026-06-18T00:00:00"))


def _scope(uid):
    return {"uid": uid, "team_ids": db.team_ids_for_user(uid), "ownerless": False}


def test_membership_personal_team_and_shared(tmp_path):
    db.DB_PATH = str(tmp_path / "lib.sqlite")
    db.migrate()
    a = db.upsert_user("76561190000000500", "A")
    b = db.upsert_user("76561190000000501", "B")
    team = db.create_team("Squad", a)
    tid = team["id"]
    db.join_team(team["invite_code"], b)
    X, Y, Z = "a" * 40, "b" * 40, "c" * 40        # X=A private, Y=A shared to team, Z=B's, shared to team
    con = db.connect()
    _seed(con, X, a, None)
    _seed(con, Y, a, tid)
    _seed(con, Z, b, tid)
    con.commit()
    con.close()

    m = db.library_membership(_scope(a))
    assert m[X] == {"personal": True, "team_ids": []}     # A's own upload, not in a team
    assert m[Y] == {"personal": False, "team_ids": [tid]}  # A moved it into the team
    assert m[Z] == {"personal": False, "team_ids": [tid]}  # B's, visible to A via the team

    mb = db.library_membership(_scope(b))
    assert mb[Z]["team_ids"] == [tid]
    assert mb[Y]["team_ids"] == [tid] and mb[Y]["personal"] is False
    assert mb[X]["personal"] is False and mb[X]["team_ids"] == []   # A's private demo isn't B's/team's


def test_archived_copy_not_personal(tmp_path):
    db.DB_PATH = str(tmp_path / "lib2.sqlite")
    db.migrate()
    a = db.upsert_user("76561190000000510", "A")
    X = "d" * 40
    con = db.connect()
    _seed(con, X, a, None)
    con.commit()
    con.close()
    assert db.library_membership(_scope(a))[X]["personal"] is True
    db.set_archived(a, X, 1)                               # delete the replay (stats-only)
    assert X not in db.library_membership(_scope(a))       # dropped from the user's library views


def test_open_mode_returns_empty_map():
    assert db.library_membership(None) == {}               # local/no-auth -> caller treats all as personal


def test_api_library_tags_each_row_and_lists_teams(tmp_path, monkeypatch):
    """/api/library returns each demo tagged personal/team_ids + the user's teams (id+name)."""
    import app
    import library
    db.DB_PATH = str(tmp_path / "api.sqlite")
    db.migrate()
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    a = db.upsert_user("76561190000000520", "A")
    team = db.create_team("Alpha", a)
    tid = team["id"]
    P, T = "e" * 40, "f" * 40                               # P=personal, T=shared with Alpha
    con = db.connect()
    _seed(con, P, a, None)
    _seed(con, T, a, tid)
    con.commit()
    con.close()
    # the library list is cache-backed; stub it so we don't need real parsed files on disk
    monkeypatch.setattr(library, "list_demos",
                        lambda *a, **k: [{"id": P, "map": "de_dust2"}, {"id": T, "map": "de_nuke"}])
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = a
    app._rl_hits.clear()
    j = c.get("/api/library").get_json()
    assert {t["id"]: t["name"] for t in j["teams"]} == {tid: "Alpha"}
    by = {d["id"]: d for d in j["demos"]}
    assert by[P]["personal"] is True and by[P]["team_ids"] == []
    assert by[T]["personal"] is False and by[T]["team_ids"] == [tid]
