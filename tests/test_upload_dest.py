"""Upload destination (Personal vs Team): the uploader can send a demo straight to a team's library.
The form's team_id is validated (must be a team the user belongs to), threaded through the parse job,
and applied to that uploader's library-membership row. Temp dirs; no real parsing."""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db        # noqa: E402
import app       # noqa: E402
import jobs as jobs_mod   # noqa: E402


def _udrow(uid, sha):
    con = db.connect()
    try:
        return con.execute("SELECT * FROM user_demos WHERE user_id=? AND sha1=?", (uid, sha)).fetchone()
    finally:
        con.close()


def _data(sha):
    return {"source_sha1": sha, "map": "de_dust2", "duration": 100, "version": 1,
            "rounds": [{"score_ct": 13, "score_t": 11}],
            "analytics": {"version": 1, "n_rounds": 24,
                          "players": [{"steamid": "76561190000000799", "name": "Zed", "kills": 20}]}}


def test_upload_to_team_threads_team_id(tmp_path, monkeypatch):
    db.DB_PATH = str(tmp_path / "u.sqlite")
    db.migrate()
    monkeypatch.setattr(app, "UPLOADS", str(tmp_path))
    uid = db.upsert_user("76561190000000700", "Zed")
    tid = db.create_team("Alpha", uid)["id"]
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid
    app._rl_hits.clear()
    r = c.post("/api/upload", data={"files": (io.BytesIO(b"FAKE" * 100), "m.dem"), "team_id": str(tid)},
               content_type="multipart/form-data")
    assert r.status_code == 200
    jid = r.get_json()["jobs"][0]["id"]
    assert jobs_mod.get_job(jid)["team_id"] == tid            # destination rode through to the job


def test_upload_personal_has_no_team(tmp_path, monkeypatch):
    db.DB_PATH = str(tmp_path / "u2.sqlite")
    db.migrate()
    monkeypatch.setattr(app, "UPLOADS", str(tmp_path))
    uid = db.upsert_user("76561190000000701", "Zed")
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid
    app._rl_hits.clear()
    r = c.post("/api/upload", data={"files": (io.BytesIO(b"FAKE" * 100), "m.dem")},
               content_type="multipart/form-data")
    jid = r.get_json()["jobs"][0]["id"]
    assert jobs_mod.get_job(jid)["team_id"] is None           # no team field -> personal


def test_upload_ignores_team_user_not_in(tmp_path, monkeypatch):
    """Security: a team_id the uploader doesn't belong to is ignored (falls back to personal)."""
    db.DB_PATH = str(tmp_path / "u3.sqlite")
    db.migrate()
    monkeypatch.setattr(app, "UPLOADS", str(tmp_path))
    owner = db.upsert_user("76561190000000702", "Owner")
    other = db.upsert_user("76561190000000703", "Other")
    foreign_tid = db.create_team("Secret", other)["id"]       # owner is NOT a member
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = owner
    app._rl_hits.clear()
    r = c.post("/api/upload", data={"files": (io.BytesIO(b"FAKE" * 100), "m.dem"),
                                    "team_id": str(foreign_tid)}, content_type="multipart/form-data")
    jid = r.get_json()["jobs"][0]["id"]
    assert jobs_mod.get_job(jid)["team_id"] is None           # ignored: not their team


def test_index_demo_applies_team_and_preserves_on_revalueless_reindex(tmp_path):
    db.DB_PATH = str(tmp_path / "i.sqlite")
    db.migrate()
    uid = db.upsert_user("76561190000000710", "Zed")
    tid = db.create_team("Alpha", uid)["id"]
    tid2 = db.create_team("Bravo", uid)["id"]
    sha = "aa" * 20
    data = _data(sha)
    db.index_demo(data, "k1", owner_user_id=uid, team_id=tid)
    assert _udrow(uid, sha)["team_id"] == tid                 # upload-to-team set it
    db.index_demo(data, "k1", owner_user_id=uid, team_id=None)
    assert _udrow(uid, sha)["team_id"] == tid                 # value-less re-index keeps it (COALESCE)
    db.index_demo(data, "k1", owner_user_id=uid, team_id=tid2)
    assert _udrow(uid, sha)["team_id"] == tid2                # an explicit team still moves it
    # and it shows under that team in the library split
    scope = {"uid": uid, "team_ids": db.team_ids_for_user(uid), "ownerless": False}
    assert db.library_membership(scope)[sha]["team_ids"] == [tid2]
