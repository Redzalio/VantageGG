"""Re-uploading a demo already in your library is skipped (no redundant parse job) and reported as a
duplicate, so the UI can say 'already uploaded' instead of erroring. Covers db.user_has_demo + the
/api/upload raw-.dem dedupe path."""
import hashlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db   # noqa: E402


def _index_demo_with_sha(sha):
    """Put one indexed demo with content-hash `sha` into the DB (local/ownerless)."""
    data = {"source_sha1": sha, "map": "de_dust2", "duration": 1, "rounds": [{}],
            "analytics": {"version": 11, "n_rounds": 24,
                          "players": [{"steamid": "76561190000000001", "name": "Me",
                                       "kills": 1, "deaths": 1, "kd": 1.0}]}}
    db.index_demo(data, sha[:16])


def test_user_has_demo_lookup(tmp_path):
    db.DB_PATH = str(tmp_path / "d.sqlite"); db.migrate()
    sha = "a" * 40
    assert db.user_has_demo(None, sha) is None        # not present yet
    _index_demo_with_sha(sha)
    assert db.user_has_demo(None, sha) is not None     # now present -> returns its loader key


def _upload(c, content, name="match.dem"):
    return c.post("/api/upload", data={"files": (io.BytesIO(content), name)},
                  content_type="multipart/form-data").get_json()


def test_reupload_existing_demo_is_skipped_as_duplicate(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    import app
    monkeypatch.setattr(app, "UPLOADS", str(tmp_path / "up")); os.makedirs(str(tmp_path / "up"), exist_ok=True)
    db.DB_PATH = str(tmp_path / "d.sqlite"); db.migrate()

    content = b"FAKE-CS2-DEMO-BYTES-" + b"x" * 500
    sha = hashlib.sha1(content).hexdigest()
    _index_demo_with_sha(sha)                          # pretend it's already in the library

    j = _upload(app.app.test_client(), content)
    jobs = j.get("jobs") or []
    assert len(jobs) == 1
    assert jobs[0].get("duplicate") is True            # flagged, not enqueued
    assert "id" in jobs[0] and "status" not in jobs[0]  # resolves to the existing demo, no parse job
    # and no temp .dem was left behind in UPLOADS (we removed it)
    assert not [f for f in os.listdir(str(tmp_path / "up")) if f.endswith(".dem")]


def test_new_demo_still_enqueues_a_job(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    import app
    monkeypatch.setattr(app, "UPLOADS", str(tmp_path / "up")); os.makedirs(str(tmp_path / "up"), exist_ok=True)
    db.DB_PATH = str(tmp_path / "d.sqlite"); db.migrate()

    j = _upload(app.app.test_client(), b"BRAND-NEW-DEMO-" + b"y" * 500)
    jobs = j.get("jobs") or []
    assert len(jobs) == 1
    assert jobs[0].get("duplicate") is not True        # a real, never-seen demo -> queued
    assert jobs[0].get("status") == "queued" and jobs[0].get("id")
