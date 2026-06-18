"""Probe whether the demo lets us tell AWP/SSG zoom level 1 from level 2.

is_scoped is a boolean, so we need another signal: the weapon_zoom / weapon_zoom_rezoom
game events (one per right-click), or a zoom-level netprop. This checks what's available
and prints a timeline so we can reverse-engineer how to reconstruct the level.

  python tools/probe_zoom.py [uploads/<demo>.dem]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from demoparser2 import DemoParser   # noqa: E402

dem = sys.argv[1] if len(sys.argv) > 1 else os.path.join("uploads", "1473cfc69590f405.dem")
pr = DemoParser(dem)

# 1) which game events mention zoom?
try:
    evs = pr.list_game_events()
    print("zoom-ish game events:", [e for e in evs if "zoom" in e.lower()])
except Exception as e:   # noqa: BLE001
    print("list_game_events err:", e)

# 2) parse each zoom event -> rows / columns / sample
zoom_dfs = {}
for name in ["weapon_zoom", "weapon_zoom_rezoom"]:
    try:
        df = pr.parse_event(name)
        zoom_dfs[name] = df
        print(f"\n{name}: rows={len(df)} cols={list(df.columns)}")
        if len(df):
            print(df.head(5).to_string())
    except Exception as e:   # noqa: BLE001
        print(f"\n{name}: ERR {str(e).splitlines()[0][:100]}")

# 3) candidate per-tick zoom-level props (do they return real data?)
for c in ["m_zoomLevel", "zoom_lvl", "weapon_zoom_level", "m_weaponMode", "m_iScopeLevel"]:
    try:
        df = pr.parse_ticks([c], ticks=list(range(60000, 120000, 8)))
        ok = c in df.columns and df[c].notna().any()
        uniq = sorted(df[c].dropna().unique())[:8] if ok else None
        print(f"prop {c}: data={ok} values={uniq}")
    except Exception as e:   # noqa: BLE001
        print(f"prop {c}: ERR {str(e).splitlines()[0][:80]}")

# 4) timeline: for the first AWP scoper, overlay zoom events on the scope state
df = pr.parse_ticks(["is_scoped", "active_weapon_name"], ticks=list(range(0, 300000, 4)))
awp_scoped = df[(df["is_scoped"] == True) & (df["active_weapon_name"].str.contains("AWP", case=False, na=False))]  # noqa: E712
if len(awp_scoped):
    sid = awp_scoped.iloc[0]["steamid"]
    t0 = int(awp_scoped.iloc[0]["tick"])
    print(f"\n--- timeline for steamid {sid} around tick {t0} (his scope state, every 4 ticks) ---")
    win = df[(df["steamid"] == sid) & (df["tick"] >= t0 - 64) & (df["tick"] <= t0 + 320)]
    scoped_ticks = [int(r["tick"]) for _, r in win.iterrows() if r["is_scoped"]]
    print("scoped ticks (4-step):", scoped_ticks[:60])
    for name, zdf in zoom_dfs.items():
        if len(zdf):
            col = "user_steamid" if "user_steamid" in zdf.columns else ("steamid" if "steamid" in zdf.columns else None)
            if col:
                mine = zdf[(zdf[col].astype(str) == str(sid)) & (zdf["tick"] >= t0 - 64) & (zdf["tick"] <= t0 + 320)]
                print(f"{name} ticks for this player in window:", [int(t) for t in mine["tick"].tolist()])
