"""Identify the map (+ size) of every .dem in the CS2 replays folder."""
import glob
import os
from demoparser2 import DemoParser

REPLAYS = r"C:\Program Files (x86)\Steam\steamapps\common\Counter-Strike Global Offensive\game\csgo\replays"

for f in sorted(glob.glob(os.path.join(REPLAYS, "*.dem"))):
    try:
        h = DemoParser(f).parse_header()
        print(os.path.basename(f), "|", h.get("map_name"), "|",
              round(os.path.getsize(f) / 1e6, 1), "MB")
    except Exception as e:
        print("ERR", os.path.basename(f), e)
