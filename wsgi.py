"""WSGI entrypoint for a production server (Flask's dev server is fine for local use only).

  waitress (cross-platform):  waitress-serve --host=0.0.0.0 --port=8770 --threads=8 wsgi:app
  gunicorn (Linux):           gunicorn -w 1 -t 180 -b 0.0.0.0:8770 wsgi:app

Use ONE worker: demo parsing runs in-process and is CPU-heavy, so scale with container
replicas behind a reverse proxy, not with multiple workers in one process. See DEPLOY.md.
"""
from app import app, start_workers  # noqa: F401  (the WSGI callable)

start_workers()      # start the background parse-job worker under waitress/gunicorn
