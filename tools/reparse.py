"""Force a FULL re-parse (frames + events + loadouts) + analytics of a .dem into a
cache JSON, regardless of the existing file's version. Used to refresh caches after a
SCHEMA_VERSION bump (regen_sample.py only re-runs analytics when the replay schema is
already current, which is wrong when a stale file happens to share the new version int,
e.g. a mock sample).

  python tools/reparse.py <cache.json> <demo.dem>

Preserves the existing file's source_sha1 (the library id / full content digest) when
present so re-uploading the same demo still de-dupes to one library row.
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

CACHE, DEM = sys.argv[1], sys.argv[2]

sha = None
if os.path.exists(CACHE):
    try:
        sha = (json.load(open(CACHE, encoding="utf-8")) or {}).get("source_sha1")
    except (OSError, ValueError):
        pass

print(f"FULL re-parse {DEM} -> {CACHE} ...", flush=True)
pr = DemoParser(DEM)
data = demo_parser.parse_demo(pr)
if sha:
    data["source_sha1"] = sha
try:
    data["analytics"] = app.clean_nan(analytics.analyze(pr, replay=data))
except Exception as e:  # noqa: BLE001
    print(f"  analytics failed: {e}", flush=True)
    data["analytics"] = None
data = app.clean_nan(data)
app.atomic_write_json(CACHE, data)
app.write_meta(CACHE, data, data.get("source_sha1"))

ev = data.get("events") or []
shots = sum(1 for e in ev if e.get("type") == "shot")
av = (data.get("analytics") or {}).get("version")
ok = "OK" if data.get("version") == SCHEMA_VERSION else "MISMATCH"
print(f"[{ok}] {CACHE}: schema v{data.get('version')} analytics v{av} "
      f"rounds={len(data.get('rounds') or [])} shots={shots} "
      f"players={len(data.get('players') or [])}", flush=True)
