#!/usr/bin/env python3
"""Export the RAW uncompressed world meshes (untextured GLB, pre-gltfpack) for hand-editing.

For each map, runs Source2Viewer-CLI to decompile maps/<map>/world.vwrld_c -> world.glb and
copies it to the output dir as <map>.glb. These are the "originals before compression": full
triangle count, untextured greyscale -- the same source the shipped static/maps3d/<map>_full.glb
is simplified+compressed from. Fix them in a 3D editor, then re-run build_map_geometry-style
compression to ship the cleaned mesh.

Usage:
  python tools/export_uncompressed.py "<out_dir>" [map ...]
  (no maps -> all 10 shipped maps)
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CLI = REPO / "tools" / "Source2Viewer-CLI.exe"
VPK_DIR = Path(r"C:\Program Files (x86)\Steam\steamapps\common"
               r"\Counter-Strike Global Offensive\game\csgo\maps")
SHIPPED = ["de_ancient", "de_anubis", "de_cache", "de_dust2", "de_inferno",
           "de_mirage", "de_nuke", "de_overpass", "de_train", "de_vertigo"]


def export_one(mapname, out_dir):
    vpk = VPK_DIR / f"{mapname}.vpk"
    if not vpk.exists():
        print(f"  SKIP {mapname}: no VPK at {vpk}")
        return False
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run(
            [str(CLI), "-i", str(vpk), "-f", f"maps/{mapname}/world.vwrld_c",
             "-d", "--gltf_export_format", "glb", "-o", td],
            capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  FAIL {mapname}: {(r.stderr or r.stdout)[:300]}")
            return False
        found = list(Path(td).rglob("world.glb"))
        if not found:
            print(f"  FAIL {mapname}: VRF produced no world.glb")
            return False
        dst = Path(out_dir) / f"{mapname}.glb"
        shutil.copy2(found[0], dst)
        print(f"  OK {mapname}.glb  {dst.stat().st_size / 1048576:.1f} MB")
        return True


def main():
    if len(sys.argv) < 2:
        raise SystemExit('usage: export_uncompressed.py "<out_dir>" [map ...]')
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)
    maps = sys.argv[2:] or SHIPPED
    print(f"exporting {len(maps)} map(s) -> {out_dir}")
    ok = 0
    for m in maps:
        print(f"=== {m} ===")
        if export_one(m, out_dir):
            ok += 1
    print(f"done: {ok}/{len(maps)} exported")


if __name__ == "__main__":
    main()
