"""Tests for the background parse-job queue (jobs.py). Temp DB, synchronous process_next (no thread),
injected fake process_fn -- so status transitions are deterministic and no demo parsing happens."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db    # noqa: E402
import jobs  # noqa: E402


def _tmp(tmp_path):
    db.DB_PATH = str(tmp_path / "jobs.sqlite")
    db.migrate()
    jobs._process_fn = None


def test_create_and_get_queued(tmp_path):
    _tmp(tmp_path)
    jid = jobs.create_job("m.dem", "/up/m.dem")
    j = jobs.get_job(jid)
    assert j["status"] == "queued" and j["filename"] == "m.dem" and j["created_at"]


def test_process_next_empty_queue(tmp_path):
    _tmp(tmp_path)
    assert jobs.process_next() is None


def test_claim_sets_parsing_before_processing(tmp_path):
    _tmp(tmp_path)
    captured = {}
    jobs._process_fn = lambda job: captured.setdefault("status", job["status"]) or "s"
    jid = jobs.create_job("m.dem", "/up/m.dem")
    assert jobs.process_next() == jid
    assert captured["status"] == "parsing"          # claimed -> parsing before process_fn runs


def test_success_transitions_to_done(tmp_path):
    _tmp(tmp_path)
    jobs._process_fn = lambda job: "sha_abc"
    jid = jobs.create_job("m.dem", "/up/m.dem")
    jobs.process_next()
    j = jobs.get_job(jid)
    assert j["status"] == "done" and j["demo_sha1"] == "sha_abc" and j["finished_at"]


def test_failure_transitions_to_failed_with_error(tmp_path):
    _tmp(tmp_path)
    def boom(job):
        raise RuntimeError("corrupt demo")
    jobs._process_fn = boom
    jid = jobs.create_job("b.dem", "/up/b.dem")
    jobs.process_next()
    j = jobs.get_job(jid)
    assert j["status"] == "failed" and "corrupt demo" in (j["error"] or "") and j["finished_at"]


def test_set_progress_updates_status(tmp_path):
    _tmp(tmp_path)
    jid = jobs.create_job("m.dem", "/up/m.dem")
    jobs.set_progress(jid, status="analyzing", progress="computing")
    j = jobs.get_job(jid)
    assert j["status"] == "analyzing" and j["progress"] == "computing"


def test_list_jobs_active_filter(tmp_path):
    _tmp(tmp_path)
    jobs._process_fn = lambda job: "s"
    j1 = jobs.create_job("a.dem", "/a")
    jobs.process_next()                              # j1 -> done
    j2 = jobs.create_job("b.dem", "/b")             # stays queued
    assert [j["id"] for j in jobs.list_jobs(active_only=True)] == [j2]
    assert len(jobs.list_jobs()) == 2


def test_requeue_stale_recovers_orphaned_jobs(tmp_path):
    """A restart leaves in-flight jobs at parsing/analyzing; startup re-queues them (the queue is
    in-memory). Finished/queued jobs are left alone."""
    _tmp(tmp_path)
    a = jobs.create_job("a.dem", "/a", owner_user_id=1)
    b = jobs.create_job("b.dem", "/b", owner_user_id=1)
    c = jobs.create_job("c.dem", "/c", owner_user_id=1)
    d = jobs.create_job("d.dem", "/d", owner_user_id=1)
    jobs._update(a, status="parsing", progress="parsing", started_at="2026-01-01T00:00:00")
    jobs._update(b, status="analyzing", progress="saving")
    jobs._update(d, status="done")
    jobs._requeue_stale()
    assert jobs.get_job(a)["status"] == "queued" and jobs.get_job(a)["started_at"] is None  # reset
    assert jobs.get_job(b)["status"] == "queued"
    assert jobs.get_job(c)["status"] == "queued"        # was already queued
    assert jobs.get_job(d)["status"] == "done"          # finished -> untouched


def test_list_jobs_scoped_to_owner(tmp_path):
    """The owner filter isolates users (fixes one user seeing another's uploads); ownerless/legacy
    jobs stay visible (local single-user mode)."""
    _tmp(tmp_path)
    mine = jobs.create_job("mine.dem", "/m", owner_user_id=1)
    theirs = jobs.create_job("theirs.dem", "/t", owner_user_id=2)
    legacy = jobs.create_job("legacy.dem", "/l")        # owner None
    ids = lambda lst: {j["id"] for j in lst}
    assert ids(jobs.list_jobs(owner_user_id=1)) == {mine, legacy}   # mine + ownerless, NOT user 2's
    assert ids(jobs.list_jobs(owner_user_id=2)) == {theirs, legacy}
    assert ids(jobs.list_jobs()) == {mine, theirs, legacy}          # no filter = all (admin/local)


def test_public_hides_upload_path(tmp_path):
    _tmp(tmp_path)
    jid = jobs.create_job("m.dem", "/secret/server/path.dem")
    pub = jobs._public(jobs.get_job(jid))
    assert "upload_path" not in pub and "owner_user_id" not in pub
    assert pub["id"] == jid and pub["demo_id"] is None and pub["status"] == "queued"
