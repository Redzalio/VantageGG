"""Re-parse every library demo IN PLACE with the current parser -- for a parser change that
does NOT bump SCHEMA_VERSION (e.g. the last-round frame-coverage fix: the header undercounted
the tick range, so the final round's frames were missing).

For each library row it finds uploads/<id[:16]>.dem, re-parses, and rewrites BOTH the library
copy (lib_<id>.json) and the content cache (<id[:16]>.json) + recomputes analytics. File mtimes
are restored so trend ordering (created_at) is preserved, and the goals analytics sidecars
(cache/_ana) are dropped so they rebuild from the fresh data.

  python tools/reparse_all.py
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analytics                       # noqa: E402
import app                             # clean_nan / atomic_write_json  # noqa: E402
import parser as demo_parser           # noqa: E402
from demoparser2 import DemoParser     # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(HERE, "cache")
UP = os.path.join(HERE, "uploads")


def _write_preserving_mtime(path, data):
    old = os.path.getmtime(path) if os.path.exists(path) else None
    app.atomic_write_json(path, data)
    if old is not None:
        os.utime(path, (old, old))


rows = json.load(open(os.path.join(CACHE, "library.json"), encoding="utf-8"))
if isinstance(rows, dict):
    rows = rows.get("demos", [])
print(f"{len(rows)} library demos to re-parse", flush=True)

for row in rows:
    did = row.get("id")
    if not did:
        continue
    dem = os.path.join(UP, did[:16] + ".dem")
    if not os.path.exists(dem):
        print(f"  SKIP {did[:16]} ({row.get('map')}): no .dem in uploads/", flush=True)
        continue
    t = time.time()
    pr = DemoParser(dem)
    data = demo_parser.parse_demo(pr)
    data["source_sha1"] = did
    try:
        data["analytics"] = app.clean_nan(analytics.analyze(pr, replay=data))
    except Exception as e:  # noqa: BLE001
        print(f"    analytics failed: {e}", flush=True)
        data["analytics"] = None
    data = app.clean_nan(data)

    libp = os.path.join(CACHE, f"lib_{did}.json")
    _write_preserving_mtime(libp, data)
    contentp = os.path.join(CACHE, did[:16] + ".json")
    if os.path.exists(contentp):
        _write_preserving_mtime(contentp, data)

    rs = data.get("rounds") or []
    fr = data.get("frames") or []
    sr = data.get("sample_rate") or 8
    last_fr = (len(fr) - 1) / sr if fr else 0
    r_end = rs[-1].get("end_t", 0) if rs else 0
    ok = "OK" if last_fr >= r_end - 3 else "SHORT"
    print(f"  [{time.time()-t:.0f}s] {row.get('map')} {did[:12]} rounds={len(rs)} "
          f"frames_to={last_fr:.0f}s lastRoundEnd={r_end:.0f}s [{ok}]", flush=True)

# drop the goals analytics sidecars so they rebuild from the refreshed caches
ana = os.path.join(CACHE, "_ana")
if os.path.isdir(ana):
    for f in os.listdir(ana):
        try:
            os.remove(os.path.join(ana, f))
        except OSError:
            pass
    print("cleared cache/_ana sidecars (rebuild on next /api/goals)", flush=True)
print("done", flush=True)
