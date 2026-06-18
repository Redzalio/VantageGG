"""Probe whether demoparser2 exposes a per-player FOV netprop (so we can show the EXACT
scope zoom in first-person), and if so what values it takes when scoped vs not.

  python tools/probe_fov.py [uploads/<demo>.dem]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from demoparser2 import DemoParser   # noqa: E402

dem = sys.argv[1] if len(sys.argv) > 1 else os.path.join("uploads", "1473cfc69590f405.dem")
pr = DemoParser(dem)

# candidate FOV-ish prop names (Source 2 / demoparser2 spellings)
cands = ["FOV", "fov", "m_iFOV", "m_iDesiredFOV", "m_iFOVStart", "m_flFOVRate",
         "m_iFOVTime", "desired_fov", "fov_desired", "current_fov"]
valid = []
for c in cands:
    try:
        pr.parse_ticks([c], ticks=[100000])
        valid.append(c)
    except Exception as e:   # noqa: BLE001
        print("  invalid prop:", c, "->", str(e).splitlines()[0][:90])
print("VALID FOV-ISH PROPS:", valid)

# sample at 8Hz (every 8 ticks) to find scoped-sniper moments + read the FOV there
want = ["is_scoped", "active_weapon_name"] + cands     # ask for ALL candidates at once
df = pr.parse_ticks(want, ticks=list(range(0, 300000, 8)))
print("rows:", len(df))
print("RETURNED COLUMNS:", list(df.columns))
# which candidate columns actually came back with at least one non-null value?
present = [c for c in cands if c in df.columns and df[c].notna().any()]
print("FOV PROPS WITH DATA:", present)

scoped = df[df["is_scoped"] == True]   # noqa: E712
print("scoped rows:", len(scoped))
for c in present:
    sv = sorted({round(float(x), 1) for x in scoped[c].dropna().unique()})[:25]
    uv = sorted({round(float(x), 1) for x in df[df["is_scoped"] != True][c].dropna().unique()})[:15]  # noqa: E712
    print(f"\n{c}: scoped values = {sv}")
    print(f"{c}: unscoped values = {uv}")
    print(f"per-weapon scoped {c}:")
    for w, g in scoped.groupby("active_weapon_name"):
        print(f"  {w}: {sorted({round(float(x),1) for x in g[c].dropna().unique()})}")
if not present:
    print("\n=> No FOV netprop available -> fall back to is_scoped + known CS scope FOVs.")
