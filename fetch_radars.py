"""
Download CS2 radar overview images + calibration data and build static/maps/maps.json.

Source: https://github.com/2mlml/cs2-radar-images  (branch: master)
Each map has:
  <map>.png   - 1024x1024 radar overview image
  <map>.txt   - Valve KeyValues calibration ("pos_x","pos_y","scale", verticalsections)

World -> radar pixel (on the 1024px source image):
  px = (world_x - pos_x) / scale
  py = (pos_y - world_y) / scale

Multi-level maps (nuke / vertigo / train) ship a <map>_lower.png and a
"verticalsections" block with an altitude split. We store the lower image +
the altitude threshold so the client can swap layers by player Z.

Re-runnable: skips files already present unless --force is passed.
"""
import json
import os
import sys
import urllib.request
import urllib.error

RAW = "https://raw.githubusercontent.com/2mlml/cs2-radar-images/master/{name}"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "static", "maps")

# Maps to fetch. Active-duty + popular pool; extras are harmless if present.
MAPS = [
    "de_mirage", "de_inferno", "de_nuke", "de_ancient", "de_anubis",
    "de_dust2", "de_train", "de_overpass", "de_vertigo",
    "de_basalt", "de_brewery", "de_edin", "de_dogtown", "de_grail",
    "de_jura", "de_palais", "de_whistle",
    "cs_office", "cs_italy", "cs_agency",
    "ar_baggage", "ar_shoots", "ar_pool_day",
]

# Lower-level image variants known to exist in the repo.
LOWER_VARIANTS = {
    "de_nuke": "de_nuke_lower",
    "de_vertigo": "de_vertigo_lower",
    "de_train": "de_train_lower",
    "ar_baggage": "ar_baggage_lower",
}

# Maps with generated radar images that do not exist in the upstream radar-image repo.
# Keep these here because Docker builds re-run this script and rewrite maps.json.
GENERATED_RADARS = {
    "de_cache": {
        "image": "de_cache.png",
        "pos_x": -3888.4,
        "pos_y": 2831.5,
        "scale": 7.7108,
        "size": 1024,
        "generated": True,
    },
}


def fetch(name, dest, force=False):
    if os.path.exists(dest) and not force:
        return True
    url = RAW.format(name=name)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "cs2demo/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        with open(dest, "wb") as f:
            f.write(data)
        print(f"  downloaded {name} ({len(data)//1024} KB)")
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        print(f"  ! {name}: HTTP {e.code}")
        return False
    except Exception as e:
        print(f"  ! {name}: {e}")
        return False


# --- minimal Valve KeyValues (VDF) tokenizer/parser -------------------------
def parse_kv(text):
    """Parse a Valve KeyValues string into nested dict (values are str)."""
    tokens = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c == '"':
            j = i + 1
            buf = []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j + 1]); j += 2
                else:
                    buf.append(text[j]); j += 1
            tokens.append(("str", "".join(buf)))
            i = j + 1
        elif c in "{}":
            tokens.append(("brace", c))
            i += 1
        elif c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
        else:
            # bare token
            j = i
            while j < n and text[j] not in ' \t\r\n"{}':
                j += 1
            tokens.append(("str", text[i:j]))
            i = j

    pos = 0

    def parse_block():
        nonlocal pos
        obj = {}
        while pos < len(tokens):
            kind, val = tokens[pos]
            if kind == "brace" and val == "}":
                pos += 1
                return obj
            # key
            key = val
            pos += 1
            if pos >= len(tokens):
                break
            k2, v2 = tokens[pos]
            if k2 == "brace" and v2 == "{":
                pos += 1
                obj[key.lower()] = parse_block()
            else:
                obj[key.lower()] = v2
                pos += 1
        return obj

    # top level: "mapname" { ... }
    if pos < len(tokens) and tokens[pos][0] == "str":
        name = tokens[pos][1]
        pos += 1
        if pos < len(tokens) and tokens[pos] == ("brace", "{"):
            pos += 1
            return name, parse_block()
    return None, {}


def fnum(d, key, default=None):
    try:
        return float(d[key])
    except (KeyError, ValueError, TypeError):
        return default


def build():
    force = "--force" in sys.argv
    os.makedirs(OUT_DIR, exist_ok=True)
    maps = {}
    for m in MAPS:
        print(m)
        png_ok = fetch(f"{m}.png", os.path.join(OUT_DIR, f"{m}.png"), force)
        txt_path = os.path.join(OUT_DIR, f"{m}.txt")
        txt_ok = fetch(f"{m}.txt", txt_path, force)
        if not (png_ok and txt_ok):
            print(f"  skip {m} (png={png_ok} txt={txt_ok})")
            continue
        with open(txt_path, "r", encoding="utf-8", errors="ignore") as f:
            _, kv = parse_kv(f.read())
        entry = {
            "image": f"{m}.png",
            "pos_x": fnum(kv, "pos_x", 0.0),
            "pos_y": fnum(kv, "pos_y", 0.0),
            "scale": fnum(kv, "scale", 1.0),
            "size": 1024,
        }
        # multi-level handling
        vs = kv.get("verticalsections")
        if vs and isinstance(vs, dict):
            lower = vs.get("lower") or vs.get("lowerlevel")
            default = vs.get("default")
            if lower and m in LOWER_VARIANTS:
                low_name = LOWER_VARIANTS[m]
                if fetch(f"{low_name}.png", os.path.join(OUT_DIR, f"{low_name}.png"), force):
                    # threshold = top of the lower band (== bottom of default band)
                    thr = fnum(lower, "altitudemax")
                    if thr is None and default:
                        thr = fnum(default, "altitudemin")
                    entry["lower"] = {
                        "image": f"{low_name}.png",
                        "altitude_max": thr if thr is not None else 0.0,
                    }
        maps[m] = entry
        print(f"  ok pos=({entry['pos_x']},{entry['pos_y']}) scale={entry['scale']}"
              + (" +lower" if "lower" in entry else ""))

    for m, entry in GENERATED_RADARS.items():
        if not os.path.exists(os.path.join(OUT_DIR, entry["image"])):
            print(f"{m}\n  skip generated radar (missing {entry['image']})")
            continue
        maps[m] = dict(entry)
        print(f"{m}\n  ok generated pos=({entry['pos_x']},{entry['pos_y']}) scale={entry['scale']}")

    with open(os.path.join(OUT_DIR, "maps.json"), "w", encoding="utf-8") as f:
        json.dump(maps, f, indent=2)
    print(f"\nWrote {len(maps)} maps -> static/maps/maps.json")


if __name__ == "__main__":
    build()
