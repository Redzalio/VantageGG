"""One-off: merge parsed replay + analytics into cache/sample.json (NaN-sanitized)."""
import json
import math

B = r"C:\Users\USER\CS2DemoPlayer\cache"


def clean(o):
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [clean(v) for v in o]
    return o


real = json.load(open(B + r"\anubis_real.json", encoding="utf-8"))
ana = json.load(open(B + r"\anubis_analytics.json", encoding="utf-8"))
real["analytics"] = ana
json.dump(clean(real), open(B + r"\sample.json", "w", encoding="utf-8"))
print("merged + sanitized: analytics players", len(ana["players"]),
      "insights", sum(len(v) for v in ana["insights"].values()))
