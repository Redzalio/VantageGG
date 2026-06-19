"""Background parse-job queue -- SQLite-backed, single daemon worker. Stdlib only.

POST /api/upload enqueues a job per file and returns immediately; a worker thread parses outside
the request/response path. Status flow: queued -> parsing -> analyzing -> done | failed.

ONE worker processes jobs sequentially on purpose -- two concurrent 500MB demoparser2 parses
thrash memory. The store is a plain `jobs` SQLite table (in db.py) with an optimistic claim
(UPDATE ... WHERE status='queued'), so a Redis/RQ/Celery backend could replace this worker later
without changing the API or the table.

The actual parse is injected by app.py via start_worker(process_fn) to avoid importing app here.
process_fn(job_dict) -> demo_sha1 (or None); it may call set_progress() and raises on failure.
"""
import datetime
import threading
import traceback
import uuid

import db

_process_fn = None              # injected: process_fn(job) -> demo_sha1 ; raises on failure
_worker_started = False
_wake = threading.Event()


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _public(j):
    """Job row -> API-safe dict (hides the server upload_path; exposes demo_id for opening)."""
    if not j:
        return None
    return {"id": j["id"], "filename": j["filename"], "status": j["status"],
            "progress": j["progress"], "error": j["error"], "demo_id": j["demo_sha1"],
            "created_at": j["created_at"], "started_at": j["started_at"],
            "finished_at": j["finished_at"]}


def create_job(filename, upload_path, owner_user_id=None):
    """Enqueue a parse job; returns its id."""
    jid = uuid.uuid4().hex
    con = db.connect()
    try:
        con.execute(
            "INSERT INTO jobs(id,owner_user_id,filename,upload_path,status,progress,created_at) "
            "VALUES(?,?,?,?, 'queued', 'queued', ?)",
            (jid, owner_user_id, filename, upload_path, _now()))
        con.commit()
    finally:
        con.close()
    _wake.set()
    return jid


def get_job(job_id, con=None):
    c = con or db.connect()
    try:
        r = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(r) if r else None
    finally:
        if con is None:
            c.close()


def list_jobs(owner_user_id=None, active_only=False, limit=50):
    con = db.connect()
    try:
        sql, args, where = "SELECT * FROM jobs", [], []
        if active_only:
            where.append("status IN ('queued','parsing','analyzing')")
        if owner_user_id is not None:
            where.append("(owner_user_id=? OR owner_user_id IS NULL)")
            args.append(owner_user_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in con.execute(sql, args)]
    finally:
        con.close()


def _update(job_id, **fields):
    con = db.connect()
    try:
        con.execute("UPDATE jobs SET {} WHERE id=?".format(",".join(k + "=?" for k in fields)),
                    (*fields.values(), job_id))
        con.commit()
    finally:
        con.close()


def set_progress(job_id, status=None, progress=None):
    """Report mid-parse progress from inside process_fn (e.g. 'parsing' -> 'analyzing')."""
    f = {}
    if status:
        f["status"] = status
    if progress is not None:
        f["progress"] = progress
    if f:
        _update(job_id, **f)


def _claim_one():
    """Optimistically claim the oldest queued job -> parsing. Returns the job dict or None."""
    con = db.connect()
    try:
        row = con.execute("SELECT id FROM jobs WHERE status='queued' "
                          "ORDER BY created_at LIMIT 1").fetchone()
        if not row:
            return None
        n = con.execute("UPDATE jobs SET status='parsing', progress='parsing', started_at=? "
                        "WHERE id=? AND status='queued'", (_now(), row["id"])).rowcount
        con.commit()
        if not n:
            return None                                  # claimed by someone else
        return get_job(row["id"], con)
    finally:
        con.close()


def process_next():
    """Claim + run the next queued job synchronously. Returns the job id processed, or None if the
    queue is empty. Called by the worker loop; also called directly in tests."""
    job = _claim_one()
    if job is None:
        return None
    jid = job["id"]
    try:
        sha = (_process_fn or (lambda j: None))(job)
        _update(jid, status="done", progress="done", finished_at=_now(), demo_sha1=sha or "")
    except Exception as e:
        _update(jid, status="failed", progress="failed", finished_at=_now(),
                error=(f"{e}\n{traceback.format_exc()}")[:4000])
        print(f"[job {jid}] FAILED: {e}")
    return jid


def _run():
    while True:
        try:
            if process_next() is None:
                _wake.wait(timeout=2.0)
                _wake.clear()
        except Exception as e:                            # never let the worker thread die
            print(f"[jobs] worker error: {e}")
            _wake.wait(timeout=2.0)


def _requeue_stale():
    """On startup, any job left at 'parsing'/'analyzing' belonged to a worker that died (a restart /
    redeploy -- the queue is in-memory). Re-queue them so they get reprocessed; their uploaded .dem is
    still on disk. (If the file is gone, the parse just fails cleanly instead of hanging forever.)"""
    con = db.connect()
    try:
        n = con.execute("UPDATE jobs SET status='queued', progress='queued', started_at=NULL "
                        "WHERE status IN ('parsing','analyzing')").rowcount
        con.commit()
        if n:
            print(f"[jobs] re-queued {n} stale in-flight job(s) after restart")
    finally:
        con.close()


def start_worker(process_fn):
    """Register the parse function and start the (idempotent) daemon worker thread."""
    global _process_fn, _worker_started
    _process_fn = process_fn
    if _worker_started:
        return
    _worker_started = True
    _requeue_stale()                 # recover jobs orphaned by a previous restart
    threading.Thread(target=_run, name="parse-worker", daemon=True).start()
    _wake.set()                      # kick the loop so re-queued jobs start immediately
