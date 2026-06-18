# Deploying CS2 Demo Player (private team)

The app is offline-first and runs great locally with `start.bat`. This guide is for hosting it
for a **private team** (a few trusted people). It is **not hardened for the public internet** --
see the security note at the bottom.

## What ships where
- **3D map geometry** (`static/maps3d/*.glb`) is baked into the image -- built offline on a
  Windows machine with the VPKs (see README "Supported maps"). The Linux container does NOT build
  geometry; it only serves it.
- **User data** lives in the mounted `./data` volume, so it survives rebuilds. The Docker image
  points every store there: parsed caches, uploaded `.dem`s, the nade library + videos, the SQLite
  DB (`cs2dp.sqlite` — index/users/teams/jobs), and the goals/playbook/reviews/team-config JSON.
- **Secrets** (`SECRET_KEY`, `STEAM_API_KEY`) and host config (`PUBLIC_BASE_URL`) are **never baked
  into the image** — supply them at run time. Copy `.env.example` → `.env` (compose auto-loads it).

## Configuration (environment variables)
| var | default | meaning |
|---|---|---|
| `HOST` | `127.0.0.1` | bind address (`0.0.0.0` to expose) |
| `PORT` | `8770` | listen port |
| `CACHE_DIR` | `./cache` | parsed-demo JSON + sidecar metadata |
| `UPLOAD_DIR` | `./uploads` | stored `.dem` files |
| `NADES_DIR` | `./nades` | lineup library + uploaded videos |
| `DATA_DIR` | repo dir | base dir for the SQLite index when `SQLITE_PATH` is unset |
| `SQLITE_PATH` | `<DATA_DIR>/cs2dp.sqlite` | metadata index + users/teams/jobs DB |
| `MAX_UPLOAD_MB` | `2048` | max `.dem` upload size |
| `MAX_VIDEO_UPLOAD_MB` | `100` | max lineup-clip size |
| `KEEP_DEM` | `1` | `0` = delete the raw `.dem` after parsing (saves ~300-500 MB/demo; the parsed cache is all the app needs). Trade-off: a future parser/schema upgrade can't re-parse old demos from disk — users re-upload. Recommended `0` for a shared host. Reclaim existing files with `tools/purge_dems.py`. |
| **Auth (Steam login) — all optional; unset = local single-user mode** | | |
| `PUBLIC_BASE_URL` | _(unset)_ | externally-reachable base URL, e.g. `https://demos.yourteam.gg`. **Setting this turns on Steam login.** Used to build the OpenID return URL — must match what the browser hits. |
| `SECRET_KEY` | ephemeral | Flask session signing key. **Set a fixed random value in production** (`python -c "import secrets;print(secrets.token_hex(32))"`) or logins reset on restart. |
| `STEAM_API_KEY` | _(unset)_ | **Optional.** Only fetches each user's display name + avatar. Login works without it (users show by SteamID). Get one at steamcommunity.com/dev/apikey. |
| `AUTH_REQUIRED` | `0` | set `1` to require login before any data is visible (enforced in the team-isolation stage). Without it, login is available but anonymous browsing still works. |
| `SESSION_COOKIE_SECURE` | `0` | set `1` when served over HTTPS so the session cookie is HTTPS-only. |
| `ADMIN_STEAM_IDS` | _(unset)_ | comma-separated SteamID64s that get the **admin panel** (instance stats + grant/revoke Pro). |
| `TIERS_ENABLED` | `0` | `1` enforces the **Free/Pro split** (free = 2D replay + basic analytics; 3D, utility, advanced analytics/trends, goals, teams need Pro; admins always Pro). Grant Pro per-user in the admin panel (manual until billing is added). |
| `FREE_UPLOAD_LIMIT` | `5` | Max demos a Free user may store (only enforced when `TIERS_ENABLED=1`; Pro/admin unlimited). |

## Run with Docker (recommended)
```bash
docker compose up -d            # builds the image, mounts ./data, serves on :8770
# or, manually:
docker build -t cs2demo .
docker run -d -p 8770:8770 -v "$PWD/data:/data" --restart unless-stopped cs2demo
```

## Run without Docker (production WSGI server)
Flask's built-in server is for local use only. For hosting, use **waitress** (cross-platform):
```bash
pip install -r requirements.txt waitress
waitress-serve --host=0.0.0.0 --port=8770 --threads=8 --channel-timeout=300 wsgi:app
```
On Linux you can use gunicorn instead: `gunicorn -w 1 -t 180 -b 0.0.0.0:8770 wsgi:app`.

> **Use ONE worker.** Uploads now enqueue a **background parse job** (a single in-process worker
> parses outside the request, so the HTTP call returns immediately and the UI polls `/api/jobs`).
> That worker is per-process, so a second WSGI worker would just add a second parser thrashing the
> same CPU. To serve more people, run more **container replicas** behind a reverse proxy with a
> shared `DATA_DIR`/`SQLITE_PATH` volume.

## Reverse proxy (TLS + a friendly hostname)
Put nginx/Caddy in front for HTTPS. Raise the upload limit and read timeout to match big demos:
```nginx
location / {
    proxy_pass         http://127.0.0.1:8770;
    proxy_read_timeout 300s;
    client_max_body_size 2048m;   # must be >= MAX_UPLOAD_MB
}
```
Caddy equivalent: `reverse_proxy 127.0.0.1:8770` plus `request_body { max_size 2048MB }`.

## Authentication (Steam login)
Steam **OpenID 2.0** login is built in and **opt-in**:
- **Local / single-user (default):** leave `PUBLIC_BASE_URL` unset. No login wall, no Steam button —
  the app behaves exactly as before, as one implicit "local" user.
- **Enable login:** set `PUBLIC_BASE_URL` to your public URL and a fixed `SECRET_KEY`. A
  *"Sign in through Steam"* chip appears top-right; the flow is `/login/steam` →
  `/auth/steam/callback` → session cookie. `STEAM_API_KEY` is optional (name/avatar only).
- **Require login:** also set `AUTH_REQUIRED=1` (data-visibility enforcement lands in the
  team-isolation stage; until then, login is available but browsing still works unauthenticated).

It uses no external OAuth/OpenID library — verification is a stdlib `check_authentication` round-trip
to Steam, so a forged callback can't pass. Uploaded demos are stamped with the uploader's id
(`owner_user_id`) ready for per-user/per-team scoping.

> Steam OpenID needs only `PUBLIC_BASE_URL` + `SECRET_KEY` — **not** an API key. The browser (not
> Steam's servers) hits your `return_to`, so even `http://localhost:8770` works for a local login test.

## Backups
Back up the `./data` directory (caches, demos, lineups) **and the SQLite DB** (`cs2dp.sqlite` plus
its `-wal`/`-shm` siblings — copy them together, or checkpoint first). The image is reproducible
from source.

## Security note (read before exposing beyond your LAN)
- **Turn on auth** (`PUBLIC_BASE_URL` + `SECRET_KEY`, ideally `AUTH_REQUIRED=1`) and **HTTPS**
  (`SESSION_COOKIE_SECURE=1`) before exposing the port. Without auth, anyone who can reach it can
  upload files and read every parsed match.
- Until the team-isolation stage enforces per-user data scoping, treat all logged-in users as able
  to see all demos — keep the audience to people you trust, or stay on a **VPN / LAN**.
- The upload-size caps limit abuse but do not authenticate users.
- Video embeds currently allow arbitrary URLs; restrict allowed domains before any public use.
