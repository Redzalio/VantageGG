# CS2 Demo Player -- private team deploy.
#   docker build -t cs2demo .
#   docker run -p 8770:8770 -v $PWD/data:/data cs2demo
# (or use docker-compose.yml). 3D map GLBs are baked in; user data lives in /data volumes.
FROM python:3.12-slim

WORKDIR /app

# Python deps first (better layer caching). demoparser2/trimesh/numpy ship manylinux wheels,
# so no compiler is needed on a glibc base. waitress = the production WSGI server.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt waitress

# app code + static (incl. static/maps3d/*.glb 3D geometry); .dockerignore keeps data/tools out
COPY . .

# radar PNGs + maps.json (no-op if already vendored in static/maps/)
RUN python fetch_radars.py || true

# data dirs are mounted volumes; HOST 0.0.0.0 to be reachable from outside the container.
# SQLITE_PATH lives under /data so the metadata/users/teams/jobs DB persists across rebuilds.
# Secrets (SECRET_KEY, STEAM_API_KEY) and PUBLIC_BASE_URL are NOT baked in -- pass them at run time
# (docker run -e / compose env_file). See .env.example + DEPLOY.md.
ENV HOST=0.0.0.0 \
    PORT=8770 \
    DATA_DIR=/data \
    CACHE_DIR=/data/cache \
    UPLOAD_DIR=/data/uploads \
    NADES_DIR=/data/nades \
    SQLITE_PATH=/data/cs2dp.sqlite \
    GOALS_DIR=/data/goals \
    PLAYBOOK_DIR=/data/playbook \
    REVIEWS_DIR=/data/reviews \
    PRACTICE_FILE=/data/practice.json \
    TEAM_CONFIG=/data/team.json \
    IMPORT_DIR=/data/incoming \
    MAX_UPLOAD_MB=2048 \
    MAX_VIDEO_UPLOAD_MB=100
VOLUME ["/data"]
EXPOSE 8770

# liveness: /api/me always answers 200 (no auth needed) -> good readiness signal
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8770/api/me',timeout=4).status==200 else 1)" || exit 1

# Web server only -- parsing runs in the separate `worker` compose service, so this process does
# fast I/O (serve + receive uploads + enqueue). --threads=16 for concurrent uploads/reads;
# --channel-timeout=1800 so a 400-500MB demo uploaded over a slow home link (8-10 min) is never cut
# mid-transfer (the old 300s cut concurrent uploads while a parse held the GIL -> EOF -> HTTP 502).
CMD ["waitress-serve", "--host=0.0.0.0", "--port=8770", "--threads=16", "--channel-timeout=1800", "wsgi:app"]
