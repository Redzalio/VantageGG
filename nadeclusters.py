"""Auto-detect consistently-thrown utility -> nade-library candidates (#61).

If a player keeps throwing the same smoke/flash/molly to the same spot across matches, that's a real
lineup worth saving. This clusters every grenade in the cached library by (steamid, map, type, landing
cell) and surfaces clusters thrown enough times, across enough matches, to count as a repeatable
lineup -- each ready to promote into the nade library (nades.py) with one click.

Reads the FULL cached demo JSONs (they carry `grenades` with det_pos + `players` + `frames` for side),
de-duped by source_sha1. Pure-ish: `find_consistent` does the IO; `cluster_throws` is the pure core
(unit-tested). GENERIC -- keyed by steamid, no roster assumptions.
"""
import glob
import json
import os
from collections import defaultdict

CELL = 150.0          # units: landings within ~this of each other are "the same spot"
MIN_THROWS = 3        # total throws to call it a habit
MIN_MATCHES = 2       # ...across at least this many different demos


def _median(xs):
    s = sorted(xs)
    return s[len(s) // 2] if s else None


def _side_at(frames, sr, thrower, t0):
    if not frames:
        return None
    fr = frames[max(0, min(len(frames) - 1, int(round((t0 or 0) * sr))))]
    ps = fr.get("players") or []
    if 0 <= thrower < len(ps) and ps[thrower]:
        tm = ps[thrower].get("team")
        return "ct" if tm == 3 else "t" if tm == 2 else None
    return None


def throws_from_demo(data):
    """Normalize one cached demo dict -> [{steamid, name, map, type, lx, ly, tx, ty, side}]."""
    players = data.get("players") or []
    sids = [p.get("steamid") for p in players]
    names = [p.get("name") for p in players]
    frames = data.get("frames") or []
    sr = data.get("sample_rate", 8)
    mp = data.get("map") or ""
    out = []
    for g in (data.get("grenades") or []):
        thr = g.get("thrower")
        if thr is None or thr < 0 or thr >= len(sids) or not sids[thr]:
            continue
        land = g.get("det_pos")
        if not land:
            pts = g.get("pts") or []
            land = pts[-1][1:] if pts else None
        if not land:
            continue
        pts = g.get("pts") or []
        origin = pts[0] if pts else None
        out.append({
            "steamid": sids[thr], "name": names[thr], "map": mp, "type": g.get("type"),
            "lx": float(land[0]), "ly": float(land[1]),
            "tx": float(origin[1]) if origin else None, "ty": float(origin[2]) if origin else None,
            "side": _side_at(frames, sr, thr, g.get("t0")),
        })
    return out


def cluster_throws(throws, demo_of, steamid=None, min_throws=MIN_THROWS,
                   min_matches=MIN_MATCHES, cell=CELL):
    """Pure core. throws: [throw dict]; demo_of: parallel list of demo-id per throw. Returns
    consistent lineups sorted by (matches, count) desc."""
    clusters = defaultdict(lambda: {"throws": [], "demos": set(), "name": None})
    for t, dem in zip(throws, demo_of):
        if not t.get("steamid") or not t.get("type"):
            continue
        if steamid and str(t["steamid"]) != str(steamid):
            continue
        key = (t["steamid"], t["map"], t["type"], round(t["lx"] / cell), round(t["ly"] / cell))
        c = clusters[key]
        c["throws"].append(t)
        c["demos"].add(dem)
        c["name"] = t.get("name")
    out = []
    for key, c in clusters.items():
        ths = c["throws"]
        if len(ths) < min_throws or len(c["demos"]) < min_matches:
            continue
        txs = [x["tx"] for x in ths if x["tx"] is not None]
        tys = [x["ty"] for x in ths if x["ty"] is not None]
        sides = [x["side"] for x in ths if x["side"]]
        out.append({
            "steamid": key[0], "name": c["name"], "map": key[1], "type": key[2],
            "count": len(ths), "matches": len(c["demos"]),
            "land": [round(_median([x["lx"] for x in ths]), 1), round(_median([x["ly"] for x in ths]), 1)],
            "throw": [round(_median(txs), 1), round(_median(tys), 1)] if txs and tys else None,
            "side": max(set(sides), key=sides.count) if sides else "ct",
        })
    out.sort(key=lambda r: (-r["matches"], -r["count"]))
    return out


def _write_sidecar(sc, sha, throws):
    try:
        os.makedirs(os.path.dirname(sc), exist_ok=True)
        tmp = sc + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"sha": sha, "throws": throws}, f)
        os.replace(tmp, sc)
    except OSError:
        pass


def _throws_cached(cache_dir, path):
    """Throws for one cache file, via a tiny `_nades/<file>` sidecar so we only json.load the
    big frame-laden cache ONCE (subsequent calls read the small sidecar). Returns (sha, throws)."""
    sc = os.path.join(cache_dir, "_nades", os.path.basename(path))
    try:
        if os.path.exists(sc) and os.path.getmtime(sc) >= os.path.getmtime(path):
            d = json.load(open(sc, encoding="utf-8"))
            return d.get("sha"), d.get("throws") or []
    except (OSError, ValueError):
        pass
    try:
        data = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return None, []
    if not isinstance(data, dict) or not data.get("grenades"):
        sha = data.get("source_sha1") if isinstance(data, dict) else None
        _write_sidecar(sc, sha, [])      # remember it has no nade data; don't re-load next time
        return sha, []
    sha = data.get("source_sha1") or path
    throws = throws_from_demo(data)
    _write_sidecar(sc, sha, throws)
    return sha, throws


def _sha_matches(sha, want):
    """Tolerant compare so a 16-char cache key and a full source_sha1 still match (one is a prefix)."""
    if not sha or not want:
        return False
    return sha == want or sha.startswith(want) or want.startswith(sha)


def find_consistent(cache_dir="cache", steamid=None, min_throws=MIN_THROWS,
                    min_matches=MIN_MATCHES, cell=CELL, map_filter=None, only_sha=None):
    """Scan the cached library (de-duped by source_sha1) for repeatable lineups. Uses per-demo
    throw sidecars so it's cheap after the first build. `map_filter` (e.g. "de_dust2") limits the
    suggestions to one map; `only_sha` limits them to a SINGLE demo (its source_sha1) so you only
    ever see lineups from the demo you're watching -- not other demos on the same map."""
    throws, demo_of, seen = [], [], set()
    for path in sorted(glob.glob(os.path.join(cache_dir, "*.json"))):
        if path.endswith(".meta.json"):
            continue
        sha, ths = _throws_cached(cache_dir, path)
        if only_sha and not _sha_matches(sha, only_sha):
            continue
        if not ths or (sha or path) in seen:
            continue
        seen.add(sha or path)
        for t in ths:
            if map_filter and t.get("map") != map_filter:
                continue
            throws.append(t)
            demo_of.append(sha or path)
    return cluster_throws(throws, demo_of, steamid, min_throws, min_matches, cell)


def to_nade(cluster):
    """Map a consistent-lineup cluster to a nade-library entry (for nades.add)."""
    land = cluster.get("land") or [0, 0]
    throw = cluster.get("throw")
    cnt, matches, who = cluster.get("count"), cluster.get("matches") or 1, cluster.get("name")
    # "Smoke - HeavyGod (3x)"; only mention demos when it actually recurs across more than one
    label = f"{(cluster.get('type') or 'util').capitalize()}"
    if who:
        label += f" - {who}"
    label += f" ({cnt}x" + (f" in {matches} demos)" if matches > 1 else ")")
    return {
        "map": cluster.get("map"), "side": cluster.get("side", "ct"), "type": cluster.get("type"),
        "name": label,
        "land_pos": [round(land[0], 1), round(land[1], 1), 0.0],
        "throw_pos": ([round(throw[0], 1), round(throw[1], 1), 0.0] if throw else None),
        "source": "auto", "tags": ["auto-detected"],
    }
