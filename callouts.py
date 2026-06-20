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
