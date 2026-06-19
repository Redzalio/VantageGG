"""Isolated preview launcher for browser-verifying frontend changes.

NEVER touches the real cs2dp.sqlite / cache. Spins app.py up on PREVIEW_PORT (default 8771)
with a throwaway DATA_DIR/CACHE under TEMP, in local/open mode (no Steam login, no CSRF
origin enforcement) so the bundled sample demo (real de_mirage match) loads straight away.

Used only during dev verification; the launch.json `cs2demo-preview` entry runs this, and
the harness tears both down afterwards.
"""
import os
import sys
import tempfile

PORT = os.environ.get("PREVIEW_PORT", "8771")
BASE = os.path.join(tempfile.gettempdir(), "cs2dp_preview")
CACHE = os.path.join(BASE, "cache")
os.makedirs(CACHE, exist_ok=True)

# Isolation: fresh data dir + DB + cache, so the real instance is never read or mutated.
os.environ["DATA_DIR"] = BASE
os.environ["SQLITE_PATH"] = os.path.join(BASE, "preview.sqlite")
os.environ["CACHE_DIR"] = CACHE
os.environ["PORT"] = PORT
os.environ.pop("AUTH_REQUIRED", None)        # local/open mode -> no login wall
os.environ.pop("PUBLIC_BASE_URL", None)      # no CSRF same-origin enforcement
os.environ.pop("SESSION_COOKIE_SECURE", None)
os.environ["PARSE_WORKERS"] = "0"            # nothing to parse; just serve the sample

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The preview harness loads the project's local .env (AUTH_REQUIRED=1, PUBLIC_BASE_URL=...) into this
# child's environment, which the env pops above can't always undo in time. Force auth fully OFF by
# patching the two gate functions directly -- app.py calls them module-qualified, so every request-time
# guard + the prod-config validator see "local/open" no matter what the injected env says.
import steamauth  # noqa: E402
steamauth.auth_required = lambda: False
steamauth.auth_enabled = lambda: False

import app  # noqa: E402  (env must be set + steamauth patched before app reads them)

app.start_workers()                          # unpacks the bundled sample into the throwaway cache
print(f"[preview] isolated SPA on http://127.0.0.1:{PORT}  (data: {BASE})", flush=True)
app.app.run(host="127.0.0.1", port=int(PORT), threaded=True, debug=False)
