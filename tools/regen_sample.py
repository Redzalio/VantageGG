"""Refresh a cached demo's analytics to the current ANALYTICS_VERSION from its .dem, WITHOUT
re-parsing the replay frames (reuses the cached replay). Keeps cache/sample.json current after
an analytics bump -- the sample's source .dem is uploads/1dc8db2090ec6eb7.dem.

  python tools/regen_sample.py [cache/sample.json] [uploads/<sha>.dem]
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analytics                       # noqa: E402
import app                             # reuse clean_nan / atomic_write_json / write_meta  # noqa: E402
import parser as demo_parser           # noqa: E402
from demoparser2 import DemoParser     # noqa: E402
from schema import ANALYTICS_VERSION, SCHEMA_VERSION   # noqa: E402

CACHE = sys.argv[1] if len(sys.argv) > 1 else os.path.join("cache", "sample.json")
DEM = sys.argv[2] if len(sys.argv) > 2 else os.path.join("uploads", "1dc8db2090ec6eb7.dem")

data = json.load(open(CACHE, encoding="utf-8"))
pr = DemoParser(DEM)
if data.get("version") != SCHEMA_VERSION:
    # replay schema is stale -> FULL re-parse (frames/loadouts), then analytics
    print(f"replay v{data.get('version')} != {SCHEMA_VERSION}: FULL re-parse of {DEM} ...")
    sha = data.get("source_sha1")
    data = demo_parser.parse_demo(pr)
    data["source_sha1"] = sha
else:
    print(f"re-running analytics on {DEM} (replay reused from {CACHE}) ...")
data["analytics"] = app.clean_nan(analytics.analyze(pr, replay=data))
data = app.clean_nan(data)
app.atomic_write_json(CACHE, data)
app.write_meta(CACHE, data, data.get("source_sha1"))
av = data["analytics"]["version"]
ok = "OK" if av == ANALYTICS_VERSION and data["version"] == SCHEMA_VERSION else "MISMATCH"
print(f"[{ok}] wrote {CACHE}: schema v{data['version']} analytics v{av}, "
      f"players={len(data['analytics']['players'])}, loadouts={len(data.get('loadouts') or {})}")
