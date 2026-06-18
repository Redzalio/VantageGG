"""Verify the upload code path: one shared DemoParser -> replay + analytics, sanitized."""
import json
import math
import time
from demoparser2 import DemoParser
import parser as P
import analytics as A

def clean(o):
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [clean(v) for v in o]
    return o

DEM = r"C:\Program Files (x86)\Steam\steamapps\common\Counter-Strike Global Offensive\game\csgo\replays\match730_003824808423086620690_1361276506_405.dem"
t = time.time()
pr = DemoParser(DEM)
data = P.parse_demo(pr)
data["analytics"] = A.analyze(pr)
data = clean(data)
s = json.dumps(data)
print("OK shared-parse path. analytics players:",
      len(data["analytics"]["players"]),
      "| insights:", sum(len(v) for v in data["analytics"]["insights"].values()),
      "| has NaN token:", "NaN" in s,
      "| elapsed_s:", round(time.time() - t, 1))
