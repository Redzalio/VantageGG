#!/usr/bin/env bash
# VantageGG deploy routine. Run on the VPS: `cd /opt/vantagegg && bash deploy.sh`
#   1. pull latest, 2. rebuild + restart (db.migrate runs on start), 3. RECLAIM Docker build
# cache + dangling images so disk doesn't creep (build cache grows ~unbounded on every --build).
set -euo pipefail
cd "$(dirname "$0")"

echo "==> git pull"
git pull --ff-only

echo "==> rebuild + restart (always --build: static is baked into the image)"
docker compose up -d --build

echo "==> reclaim docker build cache + dangling images (self-clean)"
docker builder prune -f || true
docker image prune -f || true

echo "==> disk after"
df -h / | tail -1
echo "==> deploy done"
