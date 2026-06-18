"""Local team config (roster + roles) for the CS2 Demo Player.

Stdlib-only, self-contained. Stores a single team's name, players (steamid +
display name + intended role), preferred maps, and freeform notes so analytics
can later judge players against their intended role.
"""
import os
import json
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TEAM_PATH = os.environ.get("TEAM_CONFIG") or os.path.join(HERE, "team.json")

ROLES = ["igl", "entry", "support", "lurker", "awper", "anchor", "rotator"]
DEFAULT = {"name": "", "players": [], "preferred_maps": [], "notes": ""}


def load_team(path=TEAM_PATH):
    """Return the saved config dict, or a fresh copy of DEFAULT if missing/corrupt."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else dict(DEFAULT)
    except Exception:
        return dict(DEFAULT)


def _normalize(cfg):
    """Return a clean, bounded config dict."""
    cfg = cfg if isinstance(cfg, dict) else {}
    name = str(cfg.get("name") or "")[:80]
    players = []
    for p in (cfg.get("players") or []):
        if not isinstance(p, dict):
            continue
        sid = str(p.get("steamid") or "").strip()
        if not sid:
            continue
        role = p.get("role") if p.get("role") in ROLES else ""
        players.append({"steamid": sid, "name": str(p.get("name") or "").strip(), "role": role})
    players = players[:10]
    preferred_maps = [str(m).strip() for m in (cfg.get("preferred_maps") or []) if str(m).strip()][:12]
    notes = str(cfg.get("notes") or "")[:2000]
    return {"name": name, "players": players, "preferred_maps": preferred_maps, "notes": notes}


def save_team(cfg, path=TEAM_PATH):
    """Normalize then atomically write the config to path; return the normalized dict."""
    n = _normalize(cfg)
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(n, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return n


def role_of(steamid, path=TEAM_PATH):
    """Return the intended role for steamid from the saved roster, else ''."""
    sid = str(steamid or "").strip()
    for p in load_team(path).get("players") or []:
        if str(p.get("steamid") or "").strip() == sid and sid:
            return p.get("role") or ""
    return ""
