"""Callout/location knowledge layer.

Loads per-map callout JSON files from static/maps/callouts/<map>.json and provides
nearest-callout lookup, alias resolution, and display-name normalization.

Callout JSON schema:
  {"map": "de_mirage", "callouts": [{"id":"a_site","name":"A Site",
    "aliases":["BombsiteA","ASite","A"],"world":{"x":170,"y":-1100},"side":"both"},...]}

world.x/y are CS2 world coordinates matching the radar calibration in static/maps/maps.json.
"""
import json
import math
import re
from functools import lru_cache
from pathlib import Path

_CALLOUT_DIR = Path(__file__).parent / "static" / "maps" / "callouts"

# Module-level cache: map_name -> list of callout dicts
_cache: dict = {}


def _load(map_name: str) -> list:
    """Load and cache callout list for one map. Returns [] if no file."""
    if map_name in _cache:
        return _cache[map_name]
    p = _CALLOUT_DIR / f"{map_name}.json"
    if not p.exists():
        _cache[map_name] = []
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        result = data.get("callouts", [])
    except Exception:
        result = []
    _cache[map_name] = result
    return result


def load_callouts(map_name: str) -> list:
    """Return full callout list for map_name, or [] if unknown."""
    return _load(map_name)


def nearest_callout(map_name: str, wx: float, wy: float, threshold: float = 500) -> tuple:
    """Find nearest callout to (wx, wy) world position.

    Returns (callout_dict, distance) if within threshold, else (None, None).
    Callouts with missing world coords are skipped.
    """
    callouts = _load(map_name)
    best = None
    best_d = float("inf")
    for c in callouts:
        w = c.get("world") or {}
        cx, cy = w.get("x"), w.get("y")
        if cx is None or cy is None:
            continue
        d = math.hypot(wx - cx, wy - cy)
        if d < best_d:
            best_d = d
            best = c
    if best is not None and best_d <= threshold:
        return best, round(best_d, 1)
    return None, None


def point_in_polygon(px: float, py: float, poly: list) -> bool:
    """Ray-casting point-in-polygon test. `poly` = [[x,y], ...] (world coords). Robust to the point
    lying on horizontal edges; good enough for callout zones."""
    if not poly or len(poly) < 3:
        return False
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i][0], poly[i][1]
        xj, yj = poly[j][0], poly[j][1]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / ((yj - yi) or 1e-9) + xi):
            inside = not inside
        j = i
    return inside


def label_position(callouts: list, wx: float, wy: float, threshold: float = 500) -> dict:
    """Label a world position with the best callout. Boundary polygons win (exact containment); else
    nearest center within `threshold`. Returns:
      {"callout": <dict|None>, "confidence": "inside"|"nearest"|"nearby"|"ambiguous"|"none",
       "distance": float|None, "between": <other callout name|None>}

    confidence:
      inside    -> point is within a callout's drawn boundary polygon
      nearest   -> closest center, comfortably (<= threshold/2)
      nearby    -> closest center, but loosely (<= threshold)
      ambiguous -> two centers nearly tied -> 'between A / B'
      none      -> nothing within threshold
    """
    if wx is None or wy is None:
        return {"callout": None, "confidence": "none", "distance": None, "between": None}
    # 1) boundary containment wins
    for c in callouts:
        b = c.get("boundary")
        if b and point_in_polygon(wx, wy, b):
            return {"callout": c, "confidence": "inside", "distance": 0.0, "between": None}
    # 2) nearest center
    ranked = []
    for c in callouts:
        w = c.get("world") or {}
        cx, cy = w.get("x"), w.get("y")
        if cx is None or cy is None:
            continue
        ranked.append((math.hypot(wx - cx, wy - cy), c))
    if not ranked:
        return {"callout": None, "confidence": "none", "distance": None, "between": None}
    ranked.sort(key=lambda t: t[0])
    best_d, best = ranked[0]
    if best_d > threshold:
        return {"callout": None, "confidence": "none", "distance": round(best_d, 1), "between": None}
    # two centers nearly tied -> ambiguous ("between A / B")
    if len(ranked) > 1:
        second_d, second = ranked[1]
        if second_d - best_d < 0.25 * threshold:
            return {"callout": best, "confidence": "ambiguous",
                    "distance": round(best_d, 1), "between": second.get("name") or second.get("id")}
    conf = "nearest" if best_d <= threshold * 0.5 else "nearby"
    return {"callout": best, "confidence": conf, "distance": round(best_d, 1), "between": None}


def zone_to_callout(map_name: str, zone_str: str):
    """Resolve a raw engine last_place_name token to a callout dict via alias matching.

    Tries exact case-insensitive match against id, name, and aliases.
    Returns the callout dict or None.
    """
    if not zone_str:
        return None
    callouts = _load(map_name)
    zl = zone_str.lower().strip()
    for c in callouts:
        candidates = [c.get("id", ""), c.get("name", "")] + list(c.get("aliases", []))
        if any(a.lower() == zl for a in candidates if a):
            return c
    return None


def callout_display_name(map_name: str, zone_str: str) -> str:
    """Return a human-readable display name for an engine zone string.

    Falls back to a humanized version of the raw string if no match found.
    """
    c = zone_to_callout(map_name, zone_str)
    if c:
        return c["name"]
    return _humanize(zone_str)


def _humanize(s: str) -> str:
    """Convert CamelCase or underscore_case to Title Case with spaces."""
    if not s:
        return s
    # Insert space before capital letters (CamelCase -> Camel Case)
    s = re.sub(r"([A-Z])", r" \1", s).strip()
    # Replace underscores with spaces
    s = s.replace("_", " ")
    # Collapse multiple spaces
    s = re.sub(r"\s+", " ", s)
    return s.title()


def available_maps() -> list:
    """Return sorted list of map names that have callout JSON files."""
    if not _CALLOUT_DIR.exists():
        return []
    return sorted(p.stem for p in _CALLOUT_DIR.glob("*.json"))


def callout_info(map_name: str) -> dict:
    """Return summary info about callout coverage for a map (for admin panel)."""
    callouts = _load(map_name)
    return {
        "map": map_name,
        "count": len(callouts),
        "has_world_coords": sum(1 for c in callouts if c.get("world", {}).get("x") is not None),
        "sides": list({c.get("side", "both") for c in callouts}),
    }
