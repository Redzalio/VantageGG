import json, os
from collections import Counter
P = r"C:\Users\USER\CS2DemoPlayer\cache\anubis_real.json"
d = json.load(open(P, encoding="utf-8"))
print("map", d["map"], "| dur", round(d["duration"]), "s | frames", len(d["frames"]),
      "| rounds", len(d["rounds"]), "| size", round(os.path.getsize(P)/1e6, 1), "MB")
print("players:", [(p["name"], p["team"]) for p in d["players"]])
print("event types:", dict(Counter(e["type"] for e in d["events"])))
print("rounds (n,winner,ct,t,reason):")
for r in d["rounds"]:
    print("  ", r["number"], r["winner"], r["score_ct"], r["score_t"], r.get("reason"))
ks = [e for e in d["events"] if e["type"] == "kill"]
print("sample kill:", ks[0] if ks else None)
# frame sanity
f = d["frames"][len(d["frames"])//2]
print("mid frame t=", f["t"], "round=", f["round"], "p0=", f["players"][0])
