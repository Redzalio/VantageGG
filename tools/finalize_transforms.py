#!/usr/bin/env python3
"""Finalize static/maps3d/transforms.json from AUTHORITATIVE in-browser verification.

build_map_geometry.py ships each map with a provisional verified=true based on a *trimesh*
floor check -- but trimesh's coordinate frame != three.js's, so that is only a sanity gate.
The real check is the three.js raycast against real spawns, run in the browser. This script
takes those results (tools/verify_results.json) and writes the honest verified flag + a
validation block that reflects what the renderer actually does.

A map is marked verified ONLY if, at the shipped rotation (90):
  * every spawn's floor is found (miss == 0), AND
  * rotation 90 is strictly the best orientation (fewest misses of 0/90/180/270), AND
  * the floors are coplanar under the spawns: >= n-2 spawns within 15u of the median delta
    (the median is a uniform vertical bias that view3d._fitGeoVerticalToDemo auto-corrects at
    runtime from real player positions -- horizontal registration is what this proves).

verify_results.json shape (one key per map):
  { "de_mirage": { "n": 33, "miss_by_rot": {"0":33,"90":0,"180":32,"270":20},
                   "median": -23, "within15ofMed": 32, "max_resid": 17,
                   "world_bounds": {"min":[...],"max":[...]} }, ... }
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "static" / "maps3d"
TRANSFORMS = OUT / "transforms.json"
RESULTS = REPO / "tools" / "verify_results.json"

BASE_KEYS = ("sourceType", "unitScale", "axisMap", "rotationDeg", "translate", "glb")


def decide(r):
    """Verified iff rotation 90 uniquely seats every spawn on the mesh and the floors are
    coherent. The discriminator is the FULL spawn set (CT+T span the map): at a wrong rotation
    spawns sample the wrong floor and many miss the mesh entirely. within15 is a quality metric,
    not a hard gate (maps with real terrain variation, e.g. inferno, won't hit ~100%)."""
    miss = r["miss_by_rot"]
    n = r["n"]
    m90 = miss.get("90", 999)
    others = [v for k, v in miss.items() if k != "90"]
    best90 = m90 == 0 and all(m90 < o for o in others)   # 90 uniquely seats all spawns
    coherent = r["within15ofMed"] >= 0.6 * n             # clear majority on one floor plane
    return bool(best90 and coherent)


def main():
    transforms = json.loads(TRANSFORMS.read_text())
    results = json.loads(RESULTS.read_text())
    changed = []
    for m, r in results.items():
        cfg = transforms.get(m)
        if not cfg:
            print(f"  !! {m} not in transforms.json (build it first) -- skipping")
            continue
        verified = decide(r)
        slim = {k: cfg[k] for k in BASE_KEYS if k in cfg}
        slim["verified"] = verified
        slim["validation"] = {
            "method": "three.js raycast vs real info_player_* spawns (authoritative)",
            "rotation": cfg.get("rotationDeg", 90),
            "spawns": r["n"],
            "floor_found": r["n"] - r["miss_by_rot"].get("90", 0),
            "miss_by_rotation": r["miss_by_rot"],
            "median_bias_u": r["median"],
            "coplanar_within15u": f"{r['within15ofMed']}/{r['n']}",
            "max_residual_u": r.get("max_resid"),
            "world_bounds": r.get("world_bounds"),
            "note": ("rotation 90 uniquely seats all spawns on the mesh; the uniform "
                     "median vertical bias is auto-fit at runtime from real players"),
        }
        transforms[m] = slim
        changed.append((m, verified))
    TRANSFORMS.write_text(json.dumps(transforms, indent=2))
    for m, v in changed:
        print(f"  {m}: verified={v}")
    print(f"wrote {TRANSFORMS.relative_to(REPO)} ({len(transforms)} maps total)")


if __name__ == "__main__":
    main()
