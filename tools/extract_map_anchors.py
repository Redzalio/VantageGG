#!/usr/bin/env python3
"""Extract real CS2 calibration anchors from a map VPK.

Decompiles maps/<map>/entities/default_ents.vents_c via Source2Viewer-CLI, parses the
KeyValues dump, and writes static/maps3d/<map>_anchors.json. These are *real Source-world
coordinates* (the same frame the demo reports player positions in) and are used to
validate / calibrate the 3D map transform: players at round start must stand on the
spawn markers, and the spawn markers must sit on the real spawn geometry.

Usage:
    python tools/extract_map_anchors.py de_anubis [de_dust2 de_train ...]
    python tools/extract_map_anchors.py --all          # every *.glb in static/maps3d
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CLI = REPO / "tools" / "Source2Viewer-CLI.exe"
MAPS_VPK_DIR = Path(r"C:\Program Files (x86)\Steam\steamapps\common"
                    r"\Counter-Strike Global Offensive\game\csgo\maps")
OUT_DIR = REPO / "static" / "maps3d"

# classname -> bucket in the output JSON
WANT = {
    "info_player_counterterrorist": "ct_spawns",
    "info_player_terrorist": "t_spawns",
    "info_bomb_target": "bomb_targets",
    "func_bomb_target": "bomb_targets",
    "info_deathmatch_spawn": "dm_spawns",
}


def parse_vec(s):
    s = s.strip().strip('"').strip("[]")
    parts = re.split(r"[ ,]+", s.strip())
    nums = []
    for p in parts:
        try:
            nums.append(float(p))
        except ValueError:
            return None
    return nums[:3] if len(nums) >= 3 else None


def decompile_ents(mapname):
    vpk = MAPS_VPK_DIR / f"{mapname}.vpk"
    if not vpk.exists():
        raise SystemExit(f"VPK not found: {vpk}")
    out = Path(tempfile.mkdtemp(prefix=f"{mapname}_ents_"))
    rel = f"maps/{mapname}/entities/default_ents.vents_c"
    r = subprocess.run([str(CLI), "-i", str(vpk), "-f", rel, "-d", "-o", str(out)],
                       capture_output=True, text=True)
    found = list(out.rglob("*.vents"))
    if not found:
        raise SystemExit(f"decompile produced no .vents for {mapname}\n{r.stdout}\n{r.stderr}")
    return found[0]


def bounds(points):
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    zs = [p[2] for p in points]
    return {"min": [min(xs), min(ys), min(zs)], "max": [max(xs), max(ys), max(zs)]}


def extract(mapname):
    vents = decompile_ents(mapname)
    text = vents.read_text(encoding="utf-8", errors="replace")
    blocks = re.split(r"^=+\d+=+\s*$", text, flags=re.MULTILINE)
    result = {"map": mapname, "ct_spawns": [], "t_spawns": [],
              "bomb_targets": [], "dm_spawns": []}
    allpts = []
    for b in blocks:
        cls = re.search(r'^\s*classname\s+"([^"]+)"', b, flags=re.MULTILINE)
        if not cls:
            continue
        org = re.search(r'^\s*origin\s+(.+)$', b, flags=re.MULTILINE)
        if not org:
            continue
        v = parse_vec(org.group(1))
        if not v:
            continue
        allpts.append(v)
        key = WANT.get(cls.group(1))
        if key:
            result.setdefault(key, []).append(v)
    result["ent_bounds"] = bounds(allpts)
    result["spawn_bounds"] = bounds(result["ct_spawns"] + result["t_spawns"])
    return result


def main():
    args = sys.argv[1:]
    if not args:
        args = ["de_anubis"]
    if args == ["--all"]:
        args = sorted({p.stem.replace("_full", "") for p in OUT_DIR.glob("*.glb")})
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for mapname in args:
        try:
            res = extract(mapname)
        except SystemExit as e:
            print(f"{mapname}: SKIP ({e})")
            continue
        outpath = OUT_DIR / f"{mapname}_anchors.json"
        outpath.write_text(json.dumps(res, indent=2))
        print(f"{mapname}: {len(res['ct_spawns'])} CT, {len(res['t_spawns'])} T, "
              f"{len(res['bomb_targets'])} bomb targets -> {outpath.name}")
        print(f"   spawn_bounds: {res['spawn_bounds']}")
        print(f"   ent_bounds:   {res['ent_bounds']}")


if __name__ == "__main__":
    main()
