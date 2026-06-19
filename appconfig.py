"""appconfig.py -- small admin-editable site settings (stdlib only).

Currently just the Free-plan upload limit. The value is stored in appconfig.json in DATA_DIR so an
admin can change it from the admin panel and it takes effect immediately -- no env edit, no redeploy.
Defaults live here (NOT read from the FREE_UPLOAD_LIMIT env, which is the superseded legacy knob), so
a fresh install shows the intended default and the admin override always wins.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR") or HERE
CONFIG_PATH = os.environ.get("APPCONFIG_PATH") or os.path.join(DATA_DIR, "appconfig.json")

DEFAULT_FREE_UPLOAD_LIMIT = 10
_FREE_MIN, _FREE_MAX = 1, 1000


def _load():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def _save(cfg):
    os.makedirs(os.path.dirname(CONFIG_PATH) or ".", exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    os.replace(tmp, CONFIG_PATH)


def free_upload_limit():
    """Current Free-plan upload cap: the admin override if set + valid, else the default."""
    v = _load().get("free_upload_limit")
    try:
        if v is not None:
            return max(_FREE_MIN, min(_FREE_MAX, int(v)))
    except (TypeError, ValueError):
        pass
    return DEFAULT_FREE_UPLOAD_LIMIT


def set_free_upload_limit(n):
    """Persist a new Free-plan upload cap (clamped 1..1000). Returns the stored value."""
    n = max(_FREE_MIN, min(_FREE_MAX, int(n)))
    cfg = _load()
    cfg["free_upload_limit"] = n
    _save(cfg)
    return n
