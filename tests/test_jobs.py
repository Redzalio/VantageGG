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


def test_public_hides_upload_path(tmp_path):
    _tmp(tmp_path)
    jid = jobs.create_job("m.dem", "/secret/server/path.dem")
    pub = jobs._public(jobs.get_job(jid))
    assert "upload_path" not in pub and "owner_user_id" not in pub
    assert pub["id"] == jid and pub["demo_id"] is None and pub["status"] == "queued"
