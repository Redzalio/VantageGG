#!/usr/bin/env python3
"""Validate a 3D map mesh against REAL spawn data -- no eyeballing.

Brute-forces the ground-plane orientation (the up axis = glb Y is fixed) and, for each, checks
the floor under every real spawn vs the spawn's own Z. Reports which orientation aligns and how
well. IMPORTANT: trimesh's loaded frame differs from three.js's GLTFLoader frame, so this only
proves the MESH has spawn-aligned floors under some orientation; the renderer's actual placement
is set by `rotationDeg` in transforms.json (90 for VRF exports) and is verified in-browser against
three.js raycasts (the authoritative check). Takes an UNCOMPRESSED glb (trimesh can't read -cc).

    python tools/validate_alignment.py <map> <plain_glb_path>
"""
import json
import sys
from pathlib import Path

import numpy as np
import trimesh

REPO = Path(__file__).resolve().parent.parent
INCH = 0.0254  # metres per Source unit; VRF exports glTF in metres


def load_glb(glb_path):
    """Load a glb -> raw vertices (metres, as stored) + faces. No axis assumption."""
    m = trimesh.load(glb_path, force="mesh", process=False)
    return np.asarray(m.vertices, dtype=np.float64), np.asarray(m.faces, dtype=np.int64)


# Horizontal orientation candidates (up axis = glb Y is fixed; only the ground-plane
# rotation/mirror is unknown). IMPORTANT: trimesh's loaded frame is NOT the same as
# three.js GLTFLoader's, so the winning candidate here is only a sanity check that the
# mesh HAS spawn-aligned floors under SOME orientation. The renderer's actual placement is
# driven by `rotationDeg` in transforms.json (90 for VRF exports, verified in-browser).
def _orient_candidates():
    cands = []
    for (xi, yi) in ((0, 2), (2, 0)):          # which glb horizontal axis -> world.x
        for xs in (1, -1):
            for ys in (1, -1):
                cands.append((xi, xs, yi, ys))
    return cands


def world_from_glb(g, cand):
    xi, xs, yi, ys = cand
    V = np.empty_like(g)
    V[:, 0] = xs * g[:, xi] / INCH
    V[:, 1] = ys * g[:, yi] / INCH
    V[:, 2] = g[:, 1] / INCH                    # height = glb Y
    return V


def surfaces_at(V, faces, px, py):
    """Return sorted Z of every mesh surface directly at (px,py).
    Vectorised point-in-triangle (XY) + barycentric Z."""
    a = V[faces[:, 0], :2]
    b = V[faces[:, 1], :2]
    c = V[faces[:, 2], :2]
    v0 = b - a
    v1 = c - a
    p = np.array([px, py])
    v2 = p - a
    d00 = (v0 * v0).sum(1)
    d01 = (v0 * v1).sum(1)
    d11 = (v1 * v1).sum(1)
    d20 = (v2 * v0).sum(1)
    d21 = (v2 * v1).sum(1)
    denom = d00 * d11 - d01 * d01
    ok = np.abs(denom) > 1e-12
    vv = np.where(ok, (d11 * d20 - d01 * d21) / np.where(ok, denom, 1), -1.0)
    ww = np.where(ok, (d00 * d21 - d01 * d20) / np.where(ok, denom, 1), -1.0)
    uu = 1.0 - vv - ww
    inside = ok & (uu >= -1e-3) & (vv >= -1e-3) & (ww >= -1e-3)
    if not inside.any():
        return np.array([])
    za = V[faces[:, 0], 2]
    zb = V[faces[:, 1], 2]
    zc = V[faces[:, 2], 2]
    zhit = uu * za + vv * zb + ww * zc
    return np.sort(zhit[inside])


def floor_under_feet(zs, sz):
    """Highest surface at or just below the spawn feet (the floor you stand on)."""
    below = zs[zs <= sz + 2.0]
    if below.size:
        return float(below.max())
    return None


def nearest_surface(zs, sz):
    if zs.size == 0:
        return None
    return float(zs[np.argmin(np.abs(zs - sz))])


def stats(vals):
    a = np.array([v for v in vals if v is not None], dtype=np.float64)
    miss = sum(1 for v in vals if v is None)
    if a.size == 0:
        return {"n": len(vals), "miss": miss}
    return {"n": len(vals), "miss": miss,
            "median": round(float(np.median(a))), "mean": round(float(a.mean())),
            "min": round(float(a.min())), "max": round(float(a.max())),
            "within20": int((np.abs(a) <= 20).sum()),
            "within40": int((np.abs(a) <= 40).sum())}


def score_mapping(g, faces, spawns, cand):
    V = world_from_glb(g, cand)
    near = []
    for (sx, sy, sz) in spawns:
        nz = nearest_surface(surfaces_at(V, faces, sx, sy), sz)
        near.append(None if nz is None else nz - sz)
    a = np.array([d for d in near if d is not None], dtype=np.float64)
    return {"within40": int((np.abs(a) <= 40).sum()) if a.size else 0,
            "hit": int(a.size), "mean_abs": round(float(np.abs(a).mean())) if a.size else 9999}


def main():
    mapname = sys.argv[1]
    glb = sys.argv[2]
    anchors = json.loads((REPO / "static" / "maps3d" / f"{mapname}_anchors.json").read_text())
    spawns = anchors["ct_spawns"] + anchors["t_spawns"]
    g, faces = load_glb(glb)
    print(f"[{mapname}] mesh: {len(g):,} verts, {len(faces):,} tris  (file {Path(glb).name})")
    print(f"  brute-forcing {len(spawns)} spawns over horizontal orientations (up = glb Y):")
    results = []
    for cand in _orient_candidates():
        s = score_mapping(g, faces, spawns, cand)
        results.append((s["within40"], -s["mean_abs"], cand, s))
        print(f"    map {cand}: {s['within40']}/{len(spawns)} within 40u  meanAbs {s['mean_abs']}")
    results.sort(reverse=True)
    best = results[0]
    print(f"  BEST trimesh-frame mapping {best[2]} -> {best[3]['within40']}/{len(spawns)} "
          f"spawns within 40u (meanAbs {best[3]['mean_abs']}).")
    print("  NOTE: trimesh's frame != three.js GLTFLoader's. This only confirms the mesh HAS "
          "spawn-aligned floors. The RENDERER placement is set by rotationDeg in transforms.json "
          "(90 for VRF), verified in-browser against three.js raycasts.")


if __name__ == "__main__":
    main()
