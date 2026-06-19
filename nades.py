"""nades.py -- local grenade-lineup library (CSNADES-style schema, import-compatible).

Stored at nades/library.json. We deliberately DON'T scrape csnades.gg (their lineup
database + videos are their content / ToS-protected); instead we mirror the same
conceptual fields so a user can IMPORT csnades-style exports they have rights to, and we
build accurate libraries from the user's OWN demo throws (exact in-game world coords).

A lineup:
  id, map, side(T/CT/both), type(smoke/flash/molotov/he/decoy), name,
  throw_callout, target_callout, throw_pos[x,y,z]|None, land_pos[x,y,z]|None,
  movement, technique[], aim, video, image, tags[], strat_group, source, notes
"""
import hashlib
import json
import os
import urllib.parse

# Only these video sources are allowed (rendered into iframe/video in the frontend). Anything else --
# data:/javascript:/blob:/file:/ftp:/protocol-relative/unknown hosts -- is dropped, so imported or
# manually-entered nade data can't inject arbitrary/abusive media URLs.
_VIDEO_HOST_ALLOW = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}


def safe_video(url):
    """Allow only: '' , a local uploaded clip path ('/nades/videos/...'), or an https YouTube URL.
    Everything else -> '' . Authoritative server-side check (frontend mirrors it as defense-in-depth)."""
    if not url or not isinstance(url, str):
        return ""
    u = url.strip()
    if u.startswith("/nades/videos/"):
        return u
    try:
        p = urllib.parse.urlparse(u)
    except ValueError:
        return ""
    if p.scheme != "https":
        return ""
    return u if (p.hostname or "").lower() in _VIDEO_HOST_ALLOW else ""

HERE = os.path.dirname(os.path.abspath(__file__))
LIB_DIR = os.environ.get("NADES_DIR") or os.path.join(HERE, "nades")   # env-overridable for deploys
LIB_PATH = os.path.join(LIB_DIR, "library.json")
VIDEOS_DIR = os.path.join(LIB_DIR, "videos")   # uploaded lineup videos (content-addressed)
TYPES = {"smoke", "flash", "molotov", "he", "decoy"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".m4v"}


def _ensure():
    os.makedirs(LIB_DIR, exist_ok=True)


def load_library():
    try:
        with open(LIB_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, list) else d.get("nades", [])
    except (OSError, ValueError):
        return []


def save_library(nades):
    _ensure()
    tmp = LIB_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(nades, f, indent=2)
    os.replace(tmp, LIB_PATH)


def _nid(n):
    base = f"{n.get('map')}|{n.get('type')}|{n.get('side')}|{n.get('name')}|{n.get('land_pos')}"
    return "n_" + hashlib.sha1(base.encode("utf-8")).hexdigest()[:10]


def normalize(raw):
    """Map a raw / csnades-style dict to our schema (tolerant of many field spellings)."""
    g = raw.get
    n = {
        "map": g("map") or g("mapName") or "",
        "side": (g("side") or g("team") or "both"),
        "type": str(g("type") or g("grenade") or g("nadeType") or "smoke").lower(),
        "name": g("name") or g("title") or "",
        "throw_callout": g("throw_callout") or g("from") or g("throwCallout") or "",
        "target_callout": g("target_callout") or g("to") or g("targetCallout") or g("location") or "",
        "throw_pos": g("throw_pos") or g("throwPos") or g("from_pos"),
        "land_pos": g("land_pos") or g("landPos") or g("to_pos"),
        "movement": g("movement") or "",
        "technique": g("technique") or g("techniques") or (["jumpthrow"] if g("jumpthrow") else []),
        "aim": g("aim") or g("description") or g("desc") or "",
        "video": safe_video(g("video") or g("videoUrl") or g("youtube") or ""),
        "image": g("image") or g("imageUrl") or "",
        "tags": g("tags") or [],
        "strat_group": g("strat_group") or g("group") or "",
        "favorite": bool(g("favorite")),
        "source": g("source") or "import",
        "notes": g("notes") or "",
    }
    if n["type"] not in TYPES:
        n["type"] = "smoke"
    if isinstance(n["technique"], str):
        n["technique"] = [n["technique"]]
    if isinstance(n["tags"], str):
        n["tags"] = [t.strip() for t in n["tags"].split(",") if t.strip()]
    n["id"] = raw.get("id") or _nid(n)
    return n


def add_nade(entry):
    nades = load_library()
    n = normalize(entry)
    n["source"] = entry.get("source", "user")
    nades = [x for x in nades if x.get("id") != n["id"]]
    nades.append(n)
    save_library(nades)
    return n


def update_nade(nid, entry):
    """Edit an existing lineup IN PLACE, preserving its id even if name/land_pos changed
    (add_nade would mint a new id and orphan the old one). Returns the updated nade or None."""
    nades = load_library()
    idx = next((i for i, x in enumerate(nades) if x.get("id") == nid), None)
    if idx is None:
        return None
    old = nades[idx]
    n = normalize(entry)
    n["id"] = nid                                    # keep identity stable across edits
    n["source"] = entry.get("source") or old.get("source", "user")
    if "favorite" not in entry:                      # don't clobber the star on a plain edit
        n["favorite"] = old.get("favorite", False)
    nades[idx] = n
    save_library(nades)
    return n


def set_favorite(nid, fav):
    nades = load_library()
    found = False
    for x in nades:
        if x.get("id") == nid:
            x["favorite"] = bool(fav)
            found = True
    if found:
        save_library(nades)
    return found


def delete_nade(nid):
    nades = load_library()
    keep = [x for x in nades if x.get("id") != nid]
    save_library(keep)
    return len(nades) - len(keep)


def import_nades(items, source="csnades-import"):
    nades = load_library()
    by_id = {x.get("id"): x for x in nades}
    added = 0
    for raw in items:
        n = normalize(raw)
        n["source"] = raw.get("source", source)
        if n["id"] not in by_id:
            added += 1
        by_id[n["id"]] = n
    save_library(list(by_id.values()))
    return added, len(by_id)


def from_demo(replay, per_type=(("smoke", 12), ("molotov", 8), ("flash", 8), ("he", 6))):
    """Candidate lineups from a parsed demo's grenades (real coords), deduped by landing."""
    out = []
    grenades = replay.get("grenades", [])
    mp = replay.get("map", "")
    for gtype, cap in per_type:
        seen = []
        for g in grenades:
            if g.get("type") != gtype or not g.get("pts"):
                continue
            # trajectory points are [t, x, y, z] (z added in schema v7; default 0 if absent)
            throw, land = g["pts"][0], g["pts"][-1]
            tx, ty, tz = throw[1], throw[2], (throw[3] if len(throw) > 3 else 0)
            lx, ly, lz = land[1], land[2], (land[3] if len(land) > 3 else 0)
            if any(abs(lx - s[0]) < 120 and abs(ly - s[1]) < 120 for s in seen):
                continue
            seen.append((lx, ly))
            out.append({
                "map": mp, "type": gtype, "side": "both",
                "name": f"{gtype} @ ({int(lx)}, {int(ly)})",
                "throw_pos": [round(tx, 1), round(ty, 1), round(tz, 1)],
                "land_pos": [round(lx, 1), round(ly, 1), round(lz, 1)],
                "source": "demo", "round": g.get("round"),
            })
            if len(seen) >= cap:
                break
    return [normalize(o) | {"source": "demo"} for o in out]
