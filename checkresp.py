import json
P = r"C:\Users\USER\CS2DemoPlayer\cache\_resp.json"
raw = open(P, "rb").read()
print("size", len(raw), "| has_analytics_key", b'"analytics"' in raw, "| has_NaN_token", b'NaN' in raw)
# strict browser-style validity: NaN/Infinity are NOT valid JSON
def boom(x):
    raise ValueError("invalid constant: " + x)
try:
    json.loads(raw.decode("utf-8"), parse_constant=boom)
    print("STRICT VALID JSON (browser-parseable)")
except ValueError as e:
    print("STRICT INVALID:", e)
d = json.loads(raw.decode("utf-8"))
an = d.get("analytics") or {}
print("map", d.get("map"), "| rounds", len(d.get("rounds", [])),
      "| frames", len(d.get("frames", [])), "| grenades", len(d.get("grenades", [])),
      "| analytics_players", len(an.get("players", [])),
      "| insights", sum(len(v) for v in an.get("insights", {}).values()))
