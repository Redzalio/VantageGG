"""Generate a 2D radar image + calibration for a map FROM its 3D geometry (#56).

For maps not in the radar-image source (e.g. de_cache -- not an active CS2 map, so no official
overview), we render a top-down floorplan straight from the validated `<map>_full.glb`: project
every vertex to the CS2 world XY plane (same transform the 3D view uses), bin into a 1024x1024
grid, log-scale the density to a grayscale floorplan, and derive pos_x/pos_y/scale from the SAME
projection bounds -- so player dots align with the image by construction. Updates static/maps/maps.json.

Stdlib + trimesh/numpy only (no PIL/matplotlib): a tiny grayscale PNG encoder via zlib.
Usage:  python tools/make_radar.py de_cache [de_other ...]
"""
import json
import struct
import sys
import zlib
from pathlib import Path

import numpy as np
import trimesh

REPO = Path(__file__).resolve().parent.parent
GLB_DIR = REPO / "static" / "maps3d"
MAPS_DIR = REPO / "static" / "maps"
MAPS_JSON = MAPS_DIR / "maps.json"
SIZE = 1024
UNIT = 0.0254          # glTF metres -> CS2 units (matches build_map_geometry.py / view3d.js)


def needs_radar(map_name):
    """True if the map has no radar image yet -- so we never overwrite an official/source radar."""
    try:
        maps = json.loads(MAPS_JSON.read_text(encoding="utf-8"))
        c = maps.get("maps", maps) if isinstance(maps, dict) else {}
        entry = c.get(map_name)
        if not entry or not entry.get("image"):
            return True
        return not (MAPS_DIR / entry["image"]).exists()
    except (OSError, ValueError):
        return True


def write_png_gray(path, arr):
    """Write an HxW uint8 grayscale PNG (stdlib only)."""
    h, w = arr.shape
    raw = bytearray()
    for y in range(h):
        raw.append(0)                      # filter type 0 (none) per scanline
        raw.extend(arr[y].tobytes())

    def chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(bytes(raw), 9)))
        f.write(chunk(b"IEND", b""))


def make_from_glb(glb_path, map_name):
    """Render the radar from a TRIMESH-LOADABLE glb (the uncompressed `plain.glb` from the build --
    the final `-cc` meshopt `_full.glb` can't be read by trimesh)."""
    mesh = trimesh.load(str(glb_path), force="mesh")
    v = np.asarray(mesh.vertices, dtype=float)              # metres, glTF Y-up
    wx = v[:, 0] / UNIT                                      # CS2 world X
    wy = -v[:, 2] / UNIT                                     # CS2 world Y = -glb_z
    minx, maxx, miny, maxy = wx.min(), wx.max(), wy.min(), wy.max()
    span = max(maxx - minx, maxy - miny)
    margin = 0.03 * span
    minx -= margin; maxy += margin
    span += 2 * margin
    scale = span / SIZE
    pos_x, pos_y = float(minx), float(maxy)                  # world coord at image top-left
    px = ((wx - pos_x) / scale).astype(int)
    py = ((pos_y - wy) / scale).astype(int)                 # image row 0 = max world Y
    ok = (px >= 0) & (px < SIZE) & (py >= 0) & (py < SIZE)
    grid = np.zeros((SIZE, SIZE), dtype=np.uint32)
    np.add.at(grid, (py[ok], px[ok]), 1)
    g = np.log1p(grid.astype(float))
    g = (g / g.max() * 205.0) if g.max() > 0 else g
    img = np.clip(18 + g, 0, 255).astype(np.uint8)          # dark bg + light geometry
    write_png_gray(MAPS_DIR / f"{map_name}.png", img)

    maps = json.loads(MAPS_JSON.read_text(encoding="utf-8"))
    container = maps["maps"] if isinstance(maps, dict) and "maps" in maps else maps
    container[map_name] = {"image": f"{map_name}.png", "pos_x": round(pos_x, 1),
                           "pos_y": round(pos_y, 1), "scale": round(scale, 4), "size": SIZE,
                           "generated": True}
    MAPS_JSON.write_text(json.dumps(maps, indent=2), encoding="utf-8")
    print(f"  {map_name}: radar {SIZE}px  pos=({pos_x:.0f},{pos_y:.0f}) scale={scale:.3f}  "
          f"(from {len(v)} verts) -> maps.json")
    return container[map_name]


def make(map_name):
    """Standalone: render from the shipped glb. NOTE the `-cc` meshopt `_full.glb` is NOT
    trimesh-readable -- normally radar gen runs inside build_map_geometry.py on the plain mesh."""
    return make_from_glb(GLB_DIR / f"{map_name}_full.glb", map_name)


if __name__ == "__main__":
    for m in (sys.argv[1:] or ["de_cache"]):
        make(m)
