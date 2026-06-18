#!/usr/bin/env python3
"""Build faithful, ALIGNED 3D map geometry for the CS2 demo player -- from real VPKs.

Pipeline per map (everything derived from the installed CS2 map files, no eyeballing):
  1. extract spawn anchors from entities          -> static/maps3d/<map>_anchors.json
  2. VRF export the world RENDER mesh (untextured) -> temp/world.glb  (Source metres, Y-up)
  3. gltfpack compress: meshopt + border-locked    -> static/maps3d/<map>_full.glb
     simplify (-slb keeps floors; NO aggressive -si that drops thin surfaces)
  4. VALIDATE floors against the real spawns       -> hard gate: won't ship if floors missing
  5. record the (verified) transform               -> static/maps3d/transforms.json

The transform is IDENTICAL for every VRF world export -- that's the whole point of validating
it once against ground truth:
    glTF is metres, Y-up; CS2 world = (gx, -gz, gy) / 0.0254 ; no rotation; origin preserved.
Players stay in canonical Source world units; only the GLB group is transformed in view3d.js.

    python tools/build_map_geometry.py de_anubis de_dust2 de_train
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract_map_anchors as anchors_mod   # noqa: E402
import validate_alignment as val            # noqa: E402
import make_radar                           # noqa: E402  (#56: 2D radar from the plain mesh)

REPO = Path(__file__).resolve().parent.parent
CLI = REPO / "tools" / "Source2Viewer-CLI.exe"
GLTFPACK = REPO / "tools" / "gltfpack.exe"
VPK_DIR = anchors_mod.MAPS_VPK_DIR
OUT = REPO / "static" / "maps3d"
TRANSFORMS = OUT / "transforms.json"

# The verified transform (same for all VRF world exports). axisMap "vrf_yup" means
# world = (gx, -gz, gy) / unitScale ; the renderer applies the inverse to place the GLB.
BASE_TRANSFORM = {
    "sourceType": "source2viewer_world",
    "unitScale": 0.0254,
    "axisMap": "vrf_yup",
    # VRF's glTF export + three.js GLTFLoader land the world mesh rotated 90 deg about the
    # up axis relative to the demo's world frame. Verified empirically in the renderer against
    # real spawns for de_anubis/de_dust2/de_train (each: 90 deg aligns all spawns; 0/180/270 do not).
    "rotationDeg": 90,
    "translate": [0.0, 0.0, 0.0],
}


def run(cmd):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"cmd failed: {' '.join(str(c) for c in cmd)}\n{r.stdout}\n{r.stderr}")
    return r


def export_world(mapname, tmp):
    vpk = VPK_DIR / f"{mapname}.vpk"
    if not vpk.exists():
        raise SystemExit(f"no VPK: {vpk}")
    run([CLI, "-i", vpk, "-f", f"maps/{mapname}/world.vwrld_c", "-d",
         "--gltf_export_format", "glb", "-o", tmp])
    g = list(Path(tmp).rglob("world.glb"))
    if not g:
        raise SystemExit("VRF produced no world.glb")
    return g[0]


def validate(glb, mapname):
    """Brute-force orientations and return the best spawn-floor alignment (sanity gate).

    Confirms the MESH has spawn-aligned floors under some orientation. The renderer's actual
    placement uses rotationDeg in transforms.json (90, verified in-browser).
    """
    a = json.loads((OUT / f"{mapname}_anchors.json").read_text())
    spawns = a["ct_spawns"] + a["t_spawns"]
    g, faces = val.load_glb(str(glb))
    best, best_cand = None, None
    for cand in val._orient_candidates():
        s = val.score_mapping(g, faces, spawns, cand)
        if best is None or s["within40"] > best["within40"]:
            best, best_cand = s, cand
    return {"best_mapping": best_cand, "n": len(spawns), **best}


def main():
    maps = sys.argv[1:] or ["de_anubis"]
    transforms = json.loads(TRANSFORMS.read_text()) if TRANSFORMS.exists() else {}
    OUT.mkdir(parents=True, exist_ok=True)
    for m in maps:
        print(f"=== {m} ===")
        try:
            res = anchors_mod.extract(m)
        except SystemExit as e:
            print(f"  SKIP anchors: {e}")
            continue
        (OUT / f"{m}_anchors.json").write_text(json.dumps(res, indent=2))
        print(f"  anchors: {len(res['ct_spawns'])} CT, {len(res['t_spawns'])} T spawns")
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            try:
                world = export_world(m, tmp)
            except SystemExit as e:
                print(f"  SKIP export: {e}")
                continue
            print(f"  world.glb {world.stat().st_size // 1048576} MB (raw render mesh)")
            plain = tmp / "plain.glb"
            run([GLTFPACK, "-i", world, "-o", plain, "-si", "0.7", "-slb"])
            v = validate(plain, m)
            print(f"  VALIDATE  best {v['best_mapping']} -> {v['within40']}/{v['n']} spawns "
                  f"within 40u (meanAbs {v['mean_abs']})")
            ok = v["within40"] >= v["n"] - 3        # mesh has spawn-aligned floors
            if not ok:
                print(f"  !! FLOOR VALIDATION FAILED -- keeping existing {m}_full.glb, not shipping")
                continue
            ship = OUT / f"{m}_full.glb"
            run([GLTFPACK, "-i", world, "-o", ship, "-cc", "-si", "0.7", "-slb"])
            print(f"  SHIPPED {ship.name}  {ship.stat().st_size // 1048576} MB  (validated)")
            transforms[m] = {**BASE_TRANSFORM, "glb": f"{m}_full.glb",
                             "verified": True, "validation": v}
            # #56: derive the 2D radar from the uncompressed plain mesh (the shipped -cc glb
            # isn't trimesh-readable). Only if the map has no official radar image yet.
            try:
                if make_radar.needs_radar(m):
                    make_radar.make_from_glb(plain, m)
            except Exception as e:
                print(f"  radar gen failed (3D still shipped): {e}")
    TRANSFORMS.write_text(json.dumps(transforms, indent=2))
    print(f"\nwrote {TRANSFORMS.relative_to(REPO)}  ({len(transforms)} maps)")


if __name__ == "__main__":
    main()
