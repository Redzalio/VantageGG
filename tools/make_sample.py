"""Regenerate cache/sample.json from a real .dem -- same pipeline the upload path uses
(DemoParser -> parse_demo -> analyze), NaN-sanitized, written atomically.

Usage (from the project root, with the venv python):
  python tools/make_sample.py "C:\\path\\to\\match.dem"
"""
import hashlib
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import parser as demo_parser           # noqa: E402
import analytics as an                 # noqa: E402
from demoparser2 import DemoParser     # noqa: E402


def clean(o):
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [clean(v) for v in o]
    return o


def sha1_file(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    if len(sys.argv) < 2:
        print("usage: python tools/make_sample.py <path-to.dem>")
        sys.exit(1)
    src = sys.argv[1]
    if not os.path.exists(src):
        print(f"not found: {src}")
        sys.exit(1)
    cache = os.environ.get("CACHE_DIR") or os.path.join(HERE, "cache")
    os.makedirs(cache, exist_ok=True)
    out = os.path.join(cache, "sample.json")

    print(f"parsing {os.path.basename(src)} ({os.path.getsize(src) // (1 << 20)} MB)...")
    pr = DemoParser(src)
    data = demo_parser.parse_demo(pr)
    print(f"  map={data.get('map')}  rounds={len(data.get('rounds') or [])}  "
          f"frames={len(data.get('frames') or [])}  version={data.get('version')}")
    print("computing analytics...")
    data["analytics"] = an.analyze(pr, replay=data)
    data["source_sha1"] = sha1_file(src)
    data = clean(data)

    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, out)                                # atomic -> no partial read by a running server

    a = data.get("analytics") or {}
    ins = a.get("insights") or {}
    n_ins = sum(len(v) for v in ins.values()) if isinstance(ins, dict) else 0
    print(f"wrote {out}: players={len(a.get('players') or [])} analytics_version={a.get('version')} "
          f"insights={n_ins} size={os.path.getsize(out) // (1 << 20)}MB")


if __name__ == "__main__":
    main()
