# Local-dev launcher: forces pure-local mode (no Steam auth, no tiers) so the
# whole app is reachable on localhost for UI testing against the REAL local DB
# (so dashboard analytics has all parsed demos). NOT used in production.
# dotenv only sets keys that are absent, so pre-seeding them here wins.
import os
os.environ["AUTH_REQUIRED"] = "0"
os.environ["PUBLIC_BASE_URL"] = ""
os.environ["TIERS_ENABLED"] = "0"
os.environ["KEEP_DEM"] = "1"   # keep the .dem locally so re-parses are cheap during testing

# Belt-and-suspenders (mirrors tools/preview_run.py): if a harness injects the
# project's .env (AUTH_REQUIRED=1, PUBLIC_BASE_URL=...) into this child after the
# pre-seed above, patch the gate functions directly so every request guard + the
# prod-config validator still see "local/open" mode.
import steamauth  # noqa: E402
steamauth.auth_required = lambda: False
steamauth.auth_enabled = lambda: False

import app  # noqa: E402

# Opt-in (LOCAL_ADMIN=1): treat the local session as an admin so the admin-only panels
# (e.g. the sample-demo manager) are reachable for UI testing. Pure-local convenience only;
# never set on the VPS. Patches the admin gate without changing dashboard scope (still open mode).
if os.environ.get("LOCAL_ADMIN") == "1":
    _LA = {"id": None, "steam_id_64": "76561198106326204", "name": "LocalAdmin",
           "local": True, "tier": "pro", "role": "admin"}
    app.is_admin = lambda u=None: True
    app._admin_or_none = lambda: _LA

app.start_workers()
app.app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "8770")),
            threaded=True, debug=False, use_reloader=False)
