"""Learn callout centers/extents from real demo positions.

The parser records each death's victim zone (last_place_name) paired with the victim's X/Y. Averaging
those positions per zone, across many demos, yields an accurate callout centroid -- far better than a
hand-placed guess. This module computes one demo's per-zone aggregate; db.fold_position_samples folds
it into the rolling callout_samples table (once per demo), and callout_store turns the running mean
into a learned centroid + bounding box the admin editor can adopt.

Pure + stdlib-only. Source of (zone -> x,y) pairs is the analytics death list D (built at parse time).
"""


def aggregate(rows):
    """rows: iterable of (zone, x, y). Returns {zone: {n, sum_x, sum_y, min_x, min_y, max_x, max_y}}.
    Skips rows with a missing zone or coordinate."""
    out = {}
    for zone, x, y in rows:
        if not zone or x is None or y is None:
            continue
        try:
            x = float(x); y = float(y)
        except (TypeError, ValueError):
            continue
        z = str(zone)
        a = out.get(z)
        if a is None:
            out[z] = {"n": 1, "sum_x": x, "sum_y": y,
                      "min_x": x, "min_y": y, "max_x": x, "max_y": y}
        else:
            a["n"] += 1
            a["sum_x"] += x; a["sum_y"] += y
            if x < a["min_x"]: a["min_x"] = x
            if y < a["min_y"]: a["min_y"] = y
            if x > a["max_x"]: a["max_x"] = x
            if y > a["max_y"]: a["max_y"] = y
    return out


def from_deaths(D):
    """D = analytics death list [{place, vx, vy, ...}]. Each death is one sample of its victim's zone
    (the only death-record field carrying both a callout name and coordinates)."""
    return aggregate((d.get("place"), d.get("vx"), d.get("vy")) for d in (D or []))
