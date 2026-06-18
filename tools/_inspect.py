import json, sys
d = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "cache/sample.json", encoding="utf-8"))
names = [p["name"][:14] for p in d["players"]]
fr = d["frames"]
r1 = d["rounds"][0]
print("round1:", {k: r1[k] for k in ("number", "start_t", "freeze_end_t", "end_t")})

def frame_at(t):
    best = min(fr, key=lambda f: abs(f["t"] - t))
    return best

# money across round 1: at start, freeze_end, and mid-round
for label, t in [("start", r1["start_t"]), ("freeze_end", r1["freeze_end_t"]),
                 ("mid", (r1["freeze_end_t"] + r1["end_t"]) / 2)]:
    f = frame_at(t)
    money = [(names[i], pl["money"]) for i, pl in enumerate(f["players"]) if pl]
    print(f"\nmoney @ {label} (t={f['t']}):")
    for n, m in money:
        print(f"   {n:15s} ${m}")

# also: balance range over the WHOLE demo for player 0 (to see warmup vs in-game)
ms = [f["players"][0]["money"] for f in fr if f["players"][0]]
print(f"\nplayer0 money: min={min(ms)} max={max(ms)} first5={ms[:5]}")

# loadout timeline for player 0
lo = d.get("loadouts", {}).get("0") or d.get("loadouts", {}).get(0) or []
print(f"\nplayer0 ({names[0]}) loadout timeline ({len(lo)} changes), first 6:")
for e in lo[:6]:
    print(f"   t={e[0]:7.1f}  {e[1]}")
