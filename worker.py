"""Dedicated parse-worker process.

Runs the background parse-job queue in its OWN process, separate from the web server. CS2 demo
parsing is CPU-heavy and Python-GIL-bound; when it ran as threads inside the waitress web process
it starved waitress's I/O loop, so concurrent large uploads stalled and got cut at channel-timeout
(HTTP 502). Splitting parsing into this process means the web server stays responsive to uploads
while demos parse here. Both processes share the /data volume and the SQLite jobs table (atomic
claim coordinates them); set PARSE_WORKERS=0 on the web service and >=1 here.

Run:  python worker.py     (docker compose `worker` service)
"""
import threading

from app import start_workers   # registers the parse fn + (PARSE_WORKERS>0) spawns parse threads

if __name__ == "__main__":
    start_workers()
    print("[worker] parse worker started; waiting for jobs", flush=True)
    threading.Event().wait()    # block forever; the daemon parse threads do the work
