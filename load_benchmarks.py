"""
One-shot script: load all Leetify benchmark data from Metrics folder into VantageGG.
Runs via Flask test client (no server needed). Local mode = admin always.
"""
import os, sys, json, math

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db, benchmarks, app as flask_app

# Override after import so _load_dotenv() has already run; auth_enabled() reads os.environ at
# call time, so clearing these makes current_user() return the synthetic local-admin user.
os.environ.pop("AUTH_REQUIRED", None)
os.environ.pop("PUBLIC_BASE_URL", None)

db.migrate()

c = flask_app.app.test_client()

SOURCE     = "Leetify"
SOURCE_URL = "https://leetify.com/app/data-library"
DATE       = "2026-03-01"

# ---------------------------------------------------------------------------
# CT/T MAP WIN RATES  (from screenshots, integer %)
# blue = CT, orange = T
# images cover 6 Leetify buckets; we map them to our 6 Premier buckets
# ---------------------------------------------------------------------------
CT_T_DATA = {
    "0-5k": {
        "sample": 578289,
        "maps": {
            "de_mirage":  (52, 48),
            "de_nuke":    (52, 48),
            "de_dust2":   (51, 49),
            "de_anubis":  (51, 49),
            "de_ancient": (51, 49),
            "de_overpass":(49, 51),
            "de_inferno": (49, 51),
        },
    },
    "5k-10k": {
        "sample": 1202588,
        "maps": {
            "de_mirage":  (53, 47),
            "de_nuke":    (53, 47),
            "de_anubis":  (51, 49),
            "de_dust2":   (51, 49),
            "de_ancient": (50, 50),
            "de_overpass":(50, 50),
            "de_inferno": (49, 51),
        },
    },
    "10k-15k": {
        "sample": 1562263,
        "maps": {
            "de_mirage":  (53, 47),
            "de_nuke":    (52, 48),
            "de_anubis":  (50, 50),
            "de_dust2":   (50, 50),
            "de_overpass":(50, 50),
            "de_ancient": (50, 50),
            "de_inferno": (49, 51),
        },
    },
    "15k-20k": {
        "sample": 1255044,
        "maps": {
            "de_mirage":  (52, 48),
            "de_nuke":    (52, 48),
            "de_overpass":(51, 49),
            "de_dust2":   (50, 50),
            "de_inferno": (50, 50),
            "de_anubis":  (50, 50),
            "de_ancient": (50, 50),
        },
    },
    "20k-25k": {
        "sample": 538715,
        "maps": {
            "de_mirage":  (52, 48),
            "de_nuke":    (52, 48),
            "de_overpass":(52, 48),
            "de_inferno": (50, 50),
            "de_dust2":   (50, 50),
            "de_ancient": (50, 50),
            "de_anubis":  (49, 51),
        },
    },
    # Leetify's ">25k" covers 25k+ — we save to 25k-30k (closest bucket)
    "25k-30k": {
        "sample": 81748,
        "maps": {
            "de_mirage":  (53, 47),
            "de_overpass":(52, 48),
            "de_nuke":    (52, 48),
            "de_ancient": (50, 50),
            "de_dust2":   (50, 50),
            "de_inferno": (50, 50),
            "de_anubis":  (50, 50),
        },
    },
}

def _avg_ct(maps_dict):
    ct_vals = [v[0] for v in maps_dict.values()]
    return round(sum(ct_vals) / len(ct_vals), 1)

print("=== CT/T MAP WIN RATES ===")
ok_ct = 0
for bucket, info in CT_T_DATA.items():
    maps = info["maps"]
    avg_ct  = _avg_ct(maps)
    avg_t   = round(100 - avg_ct, 1)
    rows = [{"map": "all", "ct": avg_ct, "t": avg_t, "games": info["sample"]}]
    for map_name, (ct, t) in maps.items():
        rows.append({"map": map_name, "ct": ct, "t": t})

    body = {
        "bucket": bucket,
        "region": "all",
        "source_name": SOURCE,
        "source_date": DATE,
        "source_url": SOURCE_URL,
        "rows": rows,
    }
    r = c.post("/api/admin/benchmarks/manual", json=body)
    j = r.get_json()
    status = "OK" if j and j.get("ok") else f"FAIL {r.status_code} {j}"
    print(f"  {bucket}: {status}  (records={j.get('records') if j else '?'})")
    if j and j.get("ok"):
        ok_ct += 1

print(f"\nCT/T saved: {ok_ct}/{len(CT_T_DATA)} buckets\n")

# ---------------------------------------------------------------------------
# PERFORMANCE METRICS  (from text files)
# Files have per-1k Premier values + LVL1-10 FACEIT values
# We aggregate per-1k into Premier buckets, save FACEIT levels directly
# ---------------------------------------------------------------------------

METRICS_DIR = r"C:\Users\USER\OneDrive\Desktop\Metrics\PerformanceMetrics"

def _parse_file(fname, to_float=True, strip_pct=False, div=None):
    """Return {label: value} dict from tab/whitespace file."""
    path = os.path.join(METRICS_DIR, fname)
    result = {}
    with open(path, encoding="utf-8-sig") as f:  # utf-8-sig strips BOM if present
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            label = parts[0]
            val_str = parts[1]
            if strip_pct:
                val_str = val_str.rstrip("%")
            if "ms" in val_str:
                val_str = val_str.replace("ms", "")
            if "s" in val_str and "ms" not in val_str:
                val_str = val_str.replace("s", "")
            try:
                val = float(val_str)
            except ValueError:
                continue
            if div:
                val = val / div
            result[label] = val
    return result

def _bucket_avg(data, keys):
    """Average of values for given keys that exist in data."""
    vals = [data[k] for k in keys if k in data]
    return round(sum(vals) / len(vals), 4) if vals else None

# Premier bucket key ranges (non-overlapping)
PREMIER_RANGES = {
    "0-5k":    ["1k","2k","3k","4k","5k"],
    "5k-10k":  ["6k","7k","8k","9k","10k"],
    "10k-15k": ["11k","12k","13k","14k","15k"],
    "15k-20k": ["16k","17k","18k","19k","20k"],
    "20k-25k": ["21k","22k","23k","24k","25k"],
    "25k-30k": ["26k","27k","28k","29k","30k"],
    "30k+":    ["30k"],
}

FACEIT_KEYS = {str(i): f"LVL{i}" for i in range(1, 11)}

# metric_key -> (filename, strip_pct, div_by_1000_for_ms)
METRIC_FILES = {
    "avg_reaction_time":                ("TimeToDamage.txt",                          False, 1000),   # ms→s
    "avg_preaim":                       ("Crosshair Placement.txt",                  False, None),
    "avg_accuracy_enemy_spotted":       ("Spotted Accuracy.txt",                     True,  None),
    "avg_spray_accuracy":               ("Spray Accuracy.txt",                       True,  None),
    "avg_accuracy_head":                ("Headshot Accuracy.txt",                    True,  None),
    "avg_counter_strafing_good_ratio":  ("Counter Strafing.txt",                     True,  None),
    "avg_he_foes_damage_avg":           ("HE Damage per HE.txt",                     False, None),
    "avg_he_thrown":                    ("HEs Thrown per Game.txt",                  False, None),
    "avg_molotov_thrown":               ("Molotovs Thrown per Game.txt",             False, None),
    "avg_smoke_thrown":                 ("Smokes Thrown per Game.txt",               False, None),
    "avg_flashbang_thrown":             ("Flashes Thrown per Game.txt",              False, None),
    "avg_flashbang_hit_foe":            ("Flashes Hit Foe per Game.txt",             False, None),
    "avg_total_flash_blind_duration":   ("Total Flash Blind Duration per Game sec.txt", False, None),
    "avg_flashbang_hit_friend":         ("Flashes Hit Friend per Game.txt",          False, None),
    # avg_flashbang_hit_foe_avg_duration — no file provided, skip
}

# Load all metric files once
raw = {}  # metric_key -> {label: value}
for mkey, (fname, strip_pct, div) in METRIC_FILES.items():
    raw[mkey] = _parse_file(fname, strip_pct=strip_pct, div=div)

print("=== PERFORMANCE METRICS (PREMIER) ===")
ok_perf = 0
for bucket, keys in PREMIER_RANGES.items():
    metrics = {}
    for mkey in METRIC_FILES:
        v = _bucket_avg(raw[mkey], keys)
        if v is not None:
            metrics[mkey] = round(v, 4)
    if not metrics:
        print(f"  premier {bucket}: SKIP (no data)")
        continue

    body = {
        "platform": "premier",
        "bucket": bucket,
        "region": "all",
        "source_name": SOURCE,
        "source_date": DATE,
        "source_url": SOURCE_URL,
        "metrics": metrics,
    }
    r = c.post("/api/admin/benchmarks/perf-manual", json=body)
    j = r.get_json()
    status = "OK" if j and j.get("ok") else f"FAIL {r.status_code} {j}"
    print(f"  premier {bucket}: {status}  (metrics={j.get('metrics') if j else '?'})")
    if j and j.get("ok"):
        ok_perf += 1

print(f"\n=== PERFORMANCE METRICS (FACEIT) ===")
ok_faceit = 0
for level, lbl in FACEIT_KEYS.items():
    metrics = {}
    for mkey in METRIC_FILES:
        v = raw[mkey].get(lbl)
        if v is not None:
            metrics[mkey] = round(v, 4)
    if not metrics:
        print(f"  faceit {level}: SKIP")
        continue

    body = {
        "platform": "faceit",
        "bucket": level,
        "region": "all",
        "source_name": SOURCE,
        "source_date": DATE,
        "source_url": SOURCE_URL,
        "metrics": metrics,
    }
    r = c.post("/api/admin/benchmarks/perf-manual", json=body)
    j = r.get_json()
    status = "OK" if j and j.get("ok") else f"FAIL {r.status_code} {j}"
    print(f"  faceit level {level}: {status}  (metrics={j.get('metrics') if j else '?'})")
    if j and j.get("ok"):
        ok_faceit += 1

print(f"\n=== SUMMARY ===")
print(f"CT/T buckets saved:       {ok_ct}/{len(CT_T_DATA)}")
print(f"Premier perf buckets:     {ok_perf}/{len(PREMIER_RANGES)}")
print(f"FACEIT perf levels:       {ok_faceit}/10")
print("\nDone.")
