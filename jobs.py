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
import multiprocessing
import os
import threading
import traceback
import uuid

import db

# Per-job wall-clock cap. A malformed/oversized demo must not pin a worker slot forever (queue DoS).
# We run each parse in a forked subprocess and kill it past this deadline. 0 disables the cap.
PARSE_TIMEOUT = int(os.environ.get("PARSE_TIMEOUT_SECONDS", "600") or 600)
# Subprocess isolation needs fork (Linux worker container). On Windows/local + under tests we run the
# parse in-process (tests force this in their setup), so the timeout/kill path is Linux-prod-only.
RUN_IN_SUBPROCESS = hasattr(os, "fork") and PARSE_TIMEOUT > 0

# How many demos parse at once. The claim (UPDATE ... WHERE status='queued') is atomic, so N workers
# never double-process a job. Each big-demo parse peaks ~1-3 GB, so size N to the box's RAM/cores.
# PARSE_WORKERS=0 => this process never parses (enqueue-only web tier; a separate `worker` process
# does the parsing). clamp at 0, not 1, so the web tier can truly opt out.
WORKERS = max(0, int(os.environ.get("PARSE_WORKERS", "2") or 2))

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
            "finished_at": j["finished_at"],
            "upload_ms": j.get("upload_ms"), "bytes": j.get("bytes")}


def create_job(filename, upload_path, owner_user_id=None, upload_ms=None, size_bytes=None):
    """Enqueue a parse job; returns its id. upload_ms/size_bytes (19A) record how long the server
    spent receiving+saving the file and how big it was, so the admin can split upload vs parse time."""
    jid = uuid.uuid4().hex
    con = db.connect()
    try:
        con.execute(
            "INSERT INTO jobs(id,owner_user_id,filename,upload_path,status,progress,created_at,upload_ms,bytes) "
            "VALUES(?,?,?,?, 'queued', 'queued', ?,?,?)",
            (jid, owner_user_id, filename, upload_path, _now(), upload_ms, size_bytes))
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


def count_active(owner_user_id=None):
    """How many jobs are in flight (queued/parsing/analyzing), optionally for one owner. Used to cap a
    single user from flooding the queue."""
    con = db.connect()
    try:
        sql = "SELECT COUNT(*) n FROM jobs WHERE status IN ('queued','parsing','analyzing')"
        args = []
        if owner_user_id is not None:
            sql += " AND owner_user_id=?"
            args.append(owner_user_id)
        return con.execute(sql, args).fetchone()["n"]
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


def _run_job(job):
    """Invoke the injected parse fn. Used in-process (Windows/tests) and inside the fork subprocess."""
    return (_process_fn or (lambda j: None))(job)


def _child_target(job, q):
    """Subprocess entry: run the parse, push ('ok', sha) | ('err', text) onto the result queue."""
    try:
        q.put(("ok", _run_job(job) or ""))
    except Exception as e:
        q.put(("err", (f"{e}\n{traceback.format_exc()}")[:4000]))


def _run_with_timeout(job):
    """Run the parse in a forked subprocess with a wall-clock cap; kill it (freeing the worker slot)
    if it overruns. Returns ('ok', sha) | ('err', text). Result payloads are tiny so the join-then-get
    order is safe."""
    ctx = multiprocessing.get_context("fork")
    q = ctx.Queue()
    p = ctx.Process(target=_child_target, args=(job, q), daemon=True)
    p.start()
    p.join(PARSE_TIMEOUT)
    if p.is_alive():
        p.terminate()
        p.join(5)
        if p.is_alive():
            p.kill()
        return ("err", "parse exceeded PARSE_TIMEOUT_SECONDS=%ds and was killed "
                       "(malformed or oversized demo)" % PARSE_TIMEOUT)
    try:
        return q.get(timeout=5)
    except Exception:
        return ("err", "parse worker exited without a result (exit code %s)" % p.exitcode)


def process_next():
    """Claim + run the next queued job. Returns the job id processed, or None if the queue is empty.
    On the Linux worker each parse runs in a killable subprocess (PARSE_TIMEOUT); elsewhere/in tests
    it runs in-process. Called by the worker loop and directly in tests."""
    job = _claim_one()
    if job is None:
        return None
    jid = job["id"]
    if RUN_IN_SUBPROCESS:
        kind, payload = _run_with_timeout(job)
    else:
        try:
            kind, payload = ("ok", _run_job(job) or "")
        except Exception as e:
            kind, payload = ("err", (f"{e}\n{traceback.format_exc()}")[:4000])
    if kind == "ok":
        _update(jid, status="done", progress="done", finished_at=_now(), demo_sha1=payload)
    else:
        _update(jid, status="failed", progress="failed", finished_at=_now(), error=payload)
        print(f"[job {jid}] FAILED: {payload.splitlines()[0] if payload else ''}")
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
    """Register the parse function and start the (idempotent) daemon worker thread(s).

    PARSE_WORKERS=0 => enqueue-only mode: the web process spawns no parse threads and does NOT
    requeue (requeuing would steal the dedicated worker process's in-flight job). Parsing then
    runs in a separate `worker` container -- CPU-bound parsing in its own process can't starve
    waitress's I/O loop, which was stalling concurrent uploads until channel-timeout 502'd them."""
    global _process_fn, _worker_started
    _process_fn = process_fn
    if WORKERS <= 0:                  # web/enqueue-only process: never parse or requeue here
        return
    if _worker_started:
        return
    _worker_started = True
    _requeue_stale()                 # recover jobs orphaned by a previous restart
    for i in range(WORKERS):         # N parallel parsers (atomic claim prevents double-processing)
        threading.Thread(target=_run, name=f"parse-worker-{i + 1}", daemon=True).start()
    _wake.set()                      # kick the loop so re-queued jobs start immediately
