#!/usr/bin/env python3
"""Validate (cheaply) + ship hand-fixed map meshes from a handback folder into static/maps3d.

Trimesh-free + fast: triangle count and bounding box are read straight from the GLB JSON header
(milliseconds, no mesh load), so even 300MB meshes process instantly. For each <map>.glb:
  1. read tris + bbox from the header
  2. sanity-check the transform vs the raw export's bbox (min corner must match -> not moved/rescaled;
     the user should only DELETE geometry, never transform the whole map)
  3. pick a simplify ratio toward a shippable density (dense maps only)
  4. back up the current static/maps3d/<map>_full.glb -> .glb.bak
  5. meshopt-compress (border-locked) into static/maps3d/<map>_full.glb
Final on-floor alignment is confirmed in the browser (the authoritative check).

Usage: python tools/import_fixed_maps.py "<finished_dir>" [--raw "<raw_dir>"] [map ...]
"""
import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GLTFPACK = REPO / "tools" / "gltfpack.exe"
OUT = REPO / "static" / "maps3d"
TARGET_FACES = 2_200_000     # shippable density ceiling (renderer handles ~2M comfortably)


def glb_header(path):
    """(tris, bbox_min, bbox_max) read from the GLB JSON chunk -- no mesh load."""
    with open(path, "rb") as f:
        f.read(12)                                   # magic, version, length
        clen, _ = struct.unpack("<II", f.read(8))
        js = json.loads(f.read(clen))
    accs = js.get("accessors", [])
    tris = 0
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    for m in js.get("meshes", []):
        for p in m.get("primitives", []):
            idx = p.get("indices")
            pos = (p.get("attributes") or {}).get("POSITION")
            if idx is not None and idx < len(accs):
                tris += (accs[idx].get("count", 0)) // 3
            elif pos is not None and pos < len(accs):
                tris += (accs[pos].get("count", 0)) // 3
            if pos is not None and pos < len(accs):
                a = accs[pos]
                if a.get("min") and a.get("max"):
                    for i in range(3):
                        lo[i] = min(lo[i], a["min"][i])
                        hi[i] = max(hi[i], a["max"][i])
    return tris, [round(x, 1) for x in lo], [round(x, 1) for x in hi]


def gltfpack(src, dst, si=None):
    cmd = [str(GLTFPACK), "-i", str(src), "-o", str(dst), "-cc", "-slb"]
    if si is not None:
        cmd += ["-si", f"{si:.3f}"]
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"gltfpack failed: {(r.stderr or r.stdout)[:300]}")


def process(mapname, finished_dir, raw_dir):
    src = Path(finished_dir) / f"{mapname}.glb"
    if not src.exists():
        return f"{mapname}: SKIP (no file in handback folder)"
    tris, lo, hi = glb_header(src)
    note = ""
    raw = Path(raw_dir) / f"{mapname}.glb" if raw_dir else None
    if raw and raw.exists():
        _, rlo, rhi = glb_header(raw)
        moved = any(abs(lo[i] - rlo[i]) > 2.0 for i in range(3))      # min corner should match
        if moved:
            return (f"{mapname}: FAIL transform changed -- min corner {lo} != raw {rlo}. "
                    f"NOT shipped (don't move/rescale the map in the editor; only delete).")
        note = f"bbox ok (min matches raw); raw tris {((glb_header(raw)[0])):,}"
    si = round(TARGET_FACES / tris, 3) if tris > TARGET_FACES else None
    ship = OUT / f"{mapname}_full.glb"
    if ship.exists():
        shutil.copy2(ship, Path(str(ship) + ".bak"))
    gltfpack(src, ship, si=si)
    mb = ship.stat().st_size / 1048576
    return (f"{mapname}: OK -> {mb:.1f}MB  tris={tris:,}  si={si if si else 'none (kept full)'}  {note}")


def main():
    args = sys.argv[1:]
    if not args:
        raise SystemExit('usage: import_fixed_maps.py "<finished_dir>" [--raw "<raw_dir>"] [map ...]')
    finished = args.pop(0)
    raw_dir = None
    if args and args[0] == "--raw":
        args.pop(0)
        raw_dir = args.pop(0)
    maps = args or ["de_anubis", "de_dust2", "de_inferno", "de_nuke"]
    print(f"importing {len(maps)} map(s) from {finished}")
    for m in maps:
        try:
            print(process(m, finished, raw_dir))
        except Exception as e:
            print(f"{m}: ERROR {e}")
    print("done")


if __name__ == "__main__":
    main()
