"""3D asset status for the map viewer panel (stdlib-only, never raises)."""
import os
import json

HERE = os.path.dirname(os.path.abspath(__file__))
MAPS3D_DIR = os.path.join(HERE, "static", "maps3d")


def _load_json(path):
    """Read a JSON file; return None on missing/corrupt (never raises)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _count_spawns(anchors_path):
    """len(ct_spawns)+len(t_spawns) from an anchors file, else None."""
    data = _load_json(anchors_path)
    if not isinstance(data, dict):
        return None
    try:
        return len(data.get("ct_spawns") or []) + len(data.get("t_spawns") or [])
    except Exception:
        return None


def map_status(maps3d_dir=MAPS3D_DIR):
    """Per-map 3D-geometry status: which maps have verified GLB geometry on disk."""
    transforms = _load_json(os.path.join(maps3d_dir, "transforms.json"))
    if not isinstance(transforms, dict):
        transforms = {}

    try:
        files = os.listdir(maps3d_dir)
    except Exception:
        files = []
    disk_full = {f[:-len("_full.glb")] for f in files if f.endswith("_full.glb")}
    disk_anchor = {f[:-len("_anchors.json")] for f in files if f.endswith("_anchors.json")}

    maps = []
    for name in sorted(set(transforms) | disk_full | disk_anchor):
        entry = transforms.get(name) if isinstance(transforms.get(name), dict) else None
        has_entry = entry is not None
        glb = (entry.get("glb") if has_entry else None) or (
            name + "_full.glb" if name in disk_full else None)
        glb_present = bool(glb) and os.path.isfile(os.path.join(maps3d_dir, glb))
        glb_mb = round(os.path.getsize(os.path.join(maps3d_dir, glb)) / 1048576, 1) \
            if glb_present else None
        verified = bool(entry.get("verified", False)) if has_entry else False

        if has_entry and not glb_present:
            status = "geometry-missing"
        elif not has_entry:
            status = "transform-missing"
        elif verified and glb_present:
            status = "verified"
        else:
            status = "unverified"

        anchors_present = name in disk_anchor
        maps.append({
            "map": name,
            "verified": verified,
            "rotation": entry.get("rotationDeg") if has_entry else None,
            "glb": glb,
            "glb_present": glb_present,
            "glb_mb": glb_mb,
            "anchors_present": anchors_present,
            "spawns": _count_spawns(os.path.join(maps3d_dir, name + "_anchors.json"))
            if anchors_present else None,
            "validation": entry.get("validation") if has_entry else None,
            "status": status,
        })

    return {
        "maps3d_dir": maps3d_dir,
        "maps": maps,
        "summary": {
            "total": len(maps),
            "verified": sum(1 for m in maps if m["status"] == "verified"),
            "with_geometry": sum(1 for m in maps if m["glb_present"]),
        },
    }


if __name__ == "__main__":
    print(json.dumps(map_status(), indent=2))
