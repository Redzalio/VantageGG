"""Convert the Simple Radar callout .dds images (reference assets) to PNG.

Source: C:\\Users\\USER\\OneDrive\\Desktop\\RadarCallouts\\<map>_radar.dds
Output: static/maps/callout_ref/<map>.png  (browser-viewable reference for the admin editor)

These Simple Radar images have every callout NAME baked in at its position, so they're
the human-verification reference for the admin callout editor. They are NOT used as a
coordinate source (their framing differs from the app's Valve radars + maps.json
calibration) -- demo-learned centroids remain the source of truth for world coords.
"""
import os
from pathlib import Path
from PIL import Image

SRC = Path(r"C:\Users\USER\OneDrive\Desktop\RadarCallouts")
OUT = Path(__file__).parent / "static" / "maps" / "callout_ref"
OUT.mkdir(parents=True, exist_ok=True)

# de_nuke_lower is the lower-bombsite floor; keep it as de_nuke_lower for the multi-level case.
RENAME = {"de_nuke_lower_radar": "de_nuke_lower"}

count = 0
for dds in sorted(SRC.glob("*.dds")):
    stem = dds.stem  # e.g. de_mirage_radar
    map_name = RENAME.get(stem) or stem.replace("_radar", "")
    im = Image.open(dds).convert("RGBA")
    # Downscale to 1024 max (they're already 1024) and save as optimized PNG.
    out_path = OUT / f"{map_name}.png"
    im.save(out_path, optimize=True)
    count += 1
    print(f"{dds.name} -> {out_path.name}  ({im.size[0]}x{im.size[1]})")

print(f"\nConverted {count} reference images to {OUT}")
