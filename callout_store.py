"""Effective callout layer: fuse the static JSON seed, admin overrides, and demo-learned centroids.

Three sources, in priority order:
  1. Admin overrides (db.callout_overrides)  -- once a map has any, the admin fully owns its set.
  2. Static JSON seed (callouts.load_callouts) -- the shipped default for un-edited maps.
  3. Demo-learned centroids (db.callout_samples) -- fill missing coords + power editor suggestions.

`effective_callouts(map)` is the single source of truth every consumer (2D overlay, utility matching,
analytics handoff, review, labeling) reads. `editor_data(map)` is the richer payload the admin editor
loads; `save_map` / `revert_map` write the override layer.

Coordinates are CS2 world units (matching static/maps/maps.json radar calibration). Labeling delegates
to callouts.label_position (boundary-first, nearest-fallback).
"""
import json
import os
import re

import callouts
import db

# A learned centroid needs at least this many death samples before it auto-fills a seed callout that
# has no coordinates. (Editor suggestions show regardless of count.)
MIN_LEARNED_FILL = 8

_REF_DIR = os.path.join(os.path.dirname(__file__), "static", "maps", "callout_ref")
_MAPS_JSON = os.path.join(os.path.dirname(__file__), "static", "maps", "maps.json")
_maps_cache = None


def _norm(s):
    """Normalize a callout token for fuzzy matching: lowercase, alphanumeric-only."""
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _callout_tokens(c):
    """All normalized name forms for a callout (id, name, aliases) -> set, for zone matching."""
    toks = {_norm(c.get("id")), _norm(c.get("name"))}
    toks |= {_norm(a) for a in (c.get("aliases") or [])}
    toks.discard("")
    return toks


def map_calibration(map_name):
    """The radar world<->pixel calibration for a map from maps.json, or None."""
    global _maps_cache
    if _maps_cache is None:
        try:
            with open(_MAPS_JSON, encoding="utf-8") as f:
                _maps_cache = json.load(f)
        except Exception:
            _maps_cache = {}
    return _maps_cache.get(map_name)


def ref_image(map_name):
    """Relative URL of the Simple Radar reference image (callout labels baked in), or None."""
    if os.path.exists(os.path.join(_REF_DIR, f"{map_name}.png")):
        return f"maps/callout_ref/{map_name}.png"
    return None


def _match_learned(callout, learned):
    """Sum the learned zones whose name matches this callout -> (centroid_x, centroid_y, n, zones[]).
    Returns (None, None, 0, []) if nothing matched."""
    toks = _callout_tokens(callout)
    sx = sy = 0.0
    n = 0
    zones = []
    for zone, info in learned.items():
        if _norm(zone) in toks:
            sx += info["x"] * info["n"]
            sy += info["y"] * info["n"]
            n += info["n"]
            zones.append(zone)
    if n == 0:
        return None, None, 0, []
    return round(sx / n, 1), round(sy / n, 1), n, zones


def _base_callouts(map_name):
    """The base set before learned-fill: admin overrides if the map is managed, else the JSON seed."""
    overrides = db.callout_overrides_for(map_name)
    if overrides:
        return overrides, True
    seed = [dict(c, source=c.get("source", "seed")) for c in callouts.load_callouts(map_name)]
    return seed, False


def effective_callouts(map_name):
    """The merged callout list every consumer reads. Each entry:
      {id, name, aliases[], side, world:{x,y}, boundary[[x,y]..]|None, source, sample_n}
    For un-managed maps, callouts missing world coords are auto-filled from a learned centroid once it
    has >= MIN_LEARNED_FILL samples. Admin coords are never overwritten."""
    base, managed = _base_callouts(map_name)
    learned = db.callout_learned(map_name)
    out = []
    for c in base:
        w = dict(c.get("world") or {})
        cx, cy, n, _zones = _match_learned(c, learned)
        sample_n = n
        source = c.get("source", "seed")
        if (w.get("x") is None or w.get("y") is None) and not managed and n >= MIN_LEARNED_FILL:
            w = {"x": cx, "y": cy}
            source = "learned"
        out.append({
            "id": c.get("id"), "name": c.get("name") or c.get("id"),
            "aliases": list(c.get("aliases") or []),
            "side": c.get("side") or "both",
            "world": {"x": w.get("x"), "y": w.get("y")},
            "boundary": c.get("boundary"),
            "source": source, "sample_n": sample_n,
        })
    return out


def editor_data(map_name):
    """Rich payload for the admin editor: effective callouts + per-callout learned suggestion, plus any
    learned zones not yet mapped to a callout (one-click 'create from learned'), calibration + ref img."""
    base, managed = _base_callouts(map_name)
    learned = db.callout_learned(map_name)
    matched_zones = set()
    callouts_out = []
    for c in base:
        cx, cy, n, zones = _match_learned(c, learned)
        matched_zones.update(zones)
        w = c.get("world") or {}
        callouts_out.append({
            "id": c.get("id"), "name": c.get("name") or c.get("id"),
            "aliases": list(c.get("aliases") or []),
            "side": c.get("side") or "both",
            "world": {"x": w.get("x"), "y": w.get("y")},
            "boundary": c.get("boundary"),
            "notes": c.get("notes", ""),
            "sort_order": c.get("sort_order", 0),
            "learned": ({"x": cx, "y": cy, "n": n} if n else None),
        })
    unmapped = [{"zone": z, "x": info["x"], "y": info["y"], "n": info["n"], "bbox": info["bbox"]}
                for z, info in learned.items() if z not in matched_zones]
    unmapped.sort(key=lambda u: -u["n"])
    return {
        "map": map_name, "managed": managed,
        "callouts": callouts_out,
        "unmapped_learned": unmapped,
        "calibration": map_calibration(map_name),
        "ref_image": ref_image(map_name),
        "seed_count": len(callouts.load_callouts(map_name)),
    }


def save_map(map_name, callout_list, admin_uid=None):
    """Persist the editor's full callout list as the map's override set. Returns count saved."""
    return db.callout_overrides_replace(map_name, callout_list, admin_uid=admin_uid)


def revert_map(map_name):
    """Drop admin overrides -> revert the map to its JSON seed (+ learned fill). Returns rows removed."""
    return db.callout_overrides_clear(map_name)


def label(map_name, wx, wy, threshold=500):
    """Label a world position with the best effective callout (boundary-first, nearest-fallback).
    Returns the label_position dict with the callout flattened to id/name for convenience."""
    res = callouts.label_position(effective_callouts(map_name), wx, wy, threshold)
    c = res.get("callout")
    return {
        "id": (c.get("id") if c else None),
        "name": (c.get("name") if c else None),
        "confidence": res["confidence"],
        "distance": res["distance"],
        "between": res.get("between"),
    }


def display_name(map_name, zone_str):
    """Human display name for a raw engine zone, using the EFFECTIVE set (honors admin renames),
    falling back to the seed humanizer."""
    if not zone_str:
        return zone_str
    nz = _norm(zone_str)
    for c in effective_callouts(map_name):
        if nz in _callout_tokens(c):
            return c["name"]
    return callouts._humanize(zone_str)


def available_maps():
    """Union of maps that have a seed file or admin overrides."""
    maps = set(callouts.available_maps())
    maps |= db.maps_with_overrides()
    return sorted(maps)


def coverage():
    """Per-map callout coverage for the admin readout / /api/callouts list."""
    overridden = db.maps_with_overrides()
    samples = db.callout_sample_maps()
    out = []
    for m in available_maps():
        eff = effective_callouts(m)
        with_world = sum(1 for c in eff if c["world"].get("x") is not None)
        with_bounds = sum(1 for c in eff if c.get("boundary"))
        out.append({
            "map": m, "count": len(eff),
            "with_world": with_world, "with_boundary": with_bounds,
            "managed": m in overridden,
            "samples": samples.get(m, 0),
            "ref_image": bool(ref_image(m)),
        })
    return out
