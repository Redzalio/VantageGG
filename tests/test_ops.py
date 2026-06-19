"""19A/19B admin ops: the /api/admin/ops payload carries an upload vs queue vs parse timing
breakdown and a failed-job drilldown list (filename, who, when, error). Temp DB; admin session."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db    # noqa: E402
import jobs  # noqa: E402
import app   # noqa: E402


def _admin_client(tmp_path, monkeypatch):
    db.DB_PATH = str(tmp_path / "ops.sqlite")
    db.migrate()
    sid = "76561190000000099"
    monkeypatch.setenv("ADMIN_STEAM_IDS", sid)        # this steamid is an admin
    uid = db.upsert_user(sid, "AdminGuy")
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid
    app._rl_hits.clear()
    return c, uid


def test_ops_blocks_non_admin(tmp_path, monkeypatch):
    db.DB_PATH = str(tmp_path / "ops0.sqlite")
    db.migrate()
    monkeypatch.setenv("ADMIN_STEAM_IDS", "76561190000000001")   # someone else
    uid = db.upsert_user("76561190000000002", "Rando")
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid
    app._rl_hits.clear()
    assert c.get("/api/admin/ops").status_code == 403


def test_ops_timing_breakdown_and_failures(tmp_path, monkeypatch):
    c, uid = _admin_client(tmp_path, monkeypatch)
    # a DONE job: created -> started (10s queue) -> finished (30s parse), 9 MB, 2s upload
    ok = jobs.create_job("good.dem", "/g", owner_user_id=uid, upload_ms=2000, size_bytes=9_000_000)
    jobs._update(ok, status="done", created_at="2026-01-01T00:00:00",
                 started_at="2026-01-01T00:00:10", finished_at="2026-01-01T00:00:40")
    # a FAILED job with a multi-line error
    bad = jobs.create_job("bad.dem", "/b", owner_user_id=uid, upload_ms=500, size_bytes=1000)
    jobs._update(bad, status="failed", finished_at="2026-01-01T00:01:00",
                 error="corrupt demo header\nTraceback: ...")

    r = c.get("/api/admin/ops")
    assert r.status_code == 200
    t = r.get_json()["timing"]

    # upload / queue / parse aggregates are all present and populated (19A)
    assert t["upload"]["max"] is not None and t["upload"]["max"] >= 2.0
    assert t["queue"]["max"] is not None and t["queue"]["max"] >= 10.0
    assert t["parse"]["max"] is not None and t["parse"]["max"] >= 30.0
    assert t["parsed"] == 1 and t["failed"] == 1

    # failed-job drilldown payload: who/when/error/size (19B)
    fail = next(f for f in t["failures"] if f["filename"] == "bad.dem")
    assert "corrupt demo header" in fail["error"]
    assert fail["who"] == "AdminGuy"
    assert fail["status"] == "failed" and fail["finished_at"]

    # recent rows carry the per-stage split
    row = next(r2 for r2 in t["recent"] if r2["filename"] == "good.dem")
    assert row["upload_s"] == 2.0 and row["queue_s"] == 10.0 and row["parse_s"] == 30.0
    assert row["bytes"] == 9_000_000
