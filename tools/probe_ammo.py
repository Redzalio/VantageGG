"""Probe whether the demo records per-tick AMMO (clip + reserve) and RELOAD state, so the
first-person HUD can show e.g. AWP 4/5 and "RELOADING".

  python tools/probe_ammo.py [uploads/<demo>.dem]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from demoparser2 import DemoParser   # noqa: E402

dem = sys.argv[1] if len(sys.argv) > 1 else os.path.join("uploads", "1473cfc69590f405.dem")
pr = DemoParser(dem)

clip_cands = ["active_weapon_ammo", "m_iClip1", "ammo", "clip_ammo"]
reserve_cands = ["total_ammo_left", "m_iClip2", "reserve_ammo", "m_iPrimaryReserveAmmoCount"]
reload_cands = ["is_reloading", "m_bInReload", "reloading", "is_in_reload", "m_bIsReloading"]
cands = clip_cands + reserve_cands + reload_cands

df = pr.parse_ticks(["active_weapon_name"] + cands, ticks=list(range(0, 300000, 8)))
present = [c for c in cands if c in df.columns and df[c].notna().any()]
print("RETURNED COLS:", list(df.columns))
print("PRESENT WITH DATA:", present)

awp = df[df["active_weapon_name"].astype(str).str.contains("AWP", case=False, na=False)]
ak = df[df["active_weapon_name"].astype(str).str.contains("AK-47", case=False, na=False)]
print("\nAWP rows:", len(awp), " AK rows:", len(ak))
for c in present:
    av = sorted({int(x) for x in awp[c].dropna().unique() if str(x).replace('.', '', 1).lstrip('-').isdigit()})[:20] if len(awp) else []
    kv = sorted({int(x) for x in ak[c].dropna().unique() if str(x).replace('.', '', 1).lstrip('-').isdigit()})[:20] if len(ak) else []
    # reload props are bool -> show raw uniques instead
    araw = sorted({str(x) for x in awp[c].dropna().unique()})[:6] if len(awp) else []
    print(f"\n{c}:")
    print(f"  AWP ints: {av}")
    print(f"  AK  ints: {kv}")
    print(f"  AWP raw uniques: {araw}")
