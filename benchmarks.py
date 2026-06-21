"""benchmarks.py -- source-agnostic skill-bucket benchmark layer for VantageGG (stdlib only).

This module turns a player's LOCAL aggregated stats (the keys analytics produces:
adr, kast, hltv, kd, open_wr, traded_pct, udr, plus utility counts) into a
side-by-side comparison against an *external skill bucket* -- e.g. "Premier 15k-20k"
or "FACEIT level 8" -- using benchmark datasets an admin has imported from a named,
attributed source (Leetify, HLTV, an esports stats site, ...).

Three things live here, all stdlib-only and import-safe with no DB and no network:

* **Bucket mapping** -- pure functions that turn a raw rating/elo into a bucket
  label: :func:`premier_bucket` and :func:`faceit_level`.
* **Dataset store** -- :func:`load_datasets` / :func:`get_dataset` read sourced
  JSON benchmark records from ``data/benchmarks/*.json`` (each record carries full
  provenance: source_name, source_url, source_date, region, map_filter, ...).
* **Compare + import** -- :func:`compare` diffs a player's stats against one bucket
  (metric-by-metric, honoring higher/lower-is-better); :func:`parse_leetify_pdl`
  is a PURE transform that maps rows shaped like the documented Leetify
  Performance Metric Tool response into our record schema.

THE NO-FAKE-DATA RULE (the whole point of this layer)
-----------------------------------------------------
Benchmark numbers are NEVER invented, estimated, defaulted, or inferred. A metric
is only ever compared against a number that is *physically present* in a loaded,
sourced dataset. If the dataset doesn't carry a given metric (or no dataset is
loaded at all), the result for that metric is status ``"unavailable"`` with
``benchmark_value: None`` -- never a guess. In particular K/D, ADR, KAST, win
rate, opening/trade/clutch are NOT present in the Leetify PDL response, so
:func:`parse_leetify_pdl` must not emit them; they stay unavailable unless a
different dataset explicitly provides them.

Design mirrors the rest of the app: env-overridable data dir, atomic JSON,
``better`` map like ``compare.py``, and pure functions that never raise on bad
input (they degrade to "unavailable"/None rather than throwing).
"""
import json
import os
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
# DATA_DIR matches appconfig.py / the rest of the app so a hosted deploy can mount it.
# Datasets live in <repo>/data/benchmarks by default; when DATA_DIR is explicitly set
# (a mounted volume) they live under <DATA_DIR>/benchmarks instead.
_DATA_DIR_ENV = os.environ.get("DATA_DIR")
DATA_DIR = _DATA_DIR_ENV or HERE
_DEFAULT_BENCHMARKS_DIR = (
    os.path.join(_DATA_DIR_ENV, "benchmarks") if _DATA_DIR_ENV
    else os.path.join(HERE, "data", "benchmarks")
)
# The benchmark dataset directory is separately overridable so tests can point it
# at a tmp_path without touching the real data volume.
BENCHMARKS_DIR = os.environ.get("BENCHMARKS_DIR") or _DEFAULT_BENCHMARKS_DIR

# Valid bucket_type values a dataset record may declare.
BUCKET_TYPES = ("faceit_level", "premier_rating", "premier_ct_t_side_winrates")

# Premier rating bucket edges (5k-wide bands; 30k+ is open-ended).
_PREMIER_BUCKETS = ("0-5k", "5k-10k", "10k-15k", "15k-20k", "20k-25k", "25k-30k", "30k+")

# Official FACEIT elo -> level brackets (level: (lo, hi_inclusive)); 10 is open-ended.
_FACEIT_BRACKETS = [
    (1, 100, 500),
    (2, 501, 750),
    (3, 751, 900),
    (4, 901, 1050),
    (5, 1051, 1200),
    (6, 1201, 1350),
    (7, 1351, 1530),
    (8, 1531, 1750),
    (9, 1751, 2000),
    (10, 2001, None),
]

# ---- per-metric direction map ----------------------------------------------
# Maps a metric key -> "high" (larger is better) or "low" (smaller is better).
# Used both for LOCAL stat keys (so compare() knows which way a delta is good) and
# for Leetify util/aim keys (e.g. faster reaction time / fewer team flashes = better).
# A metric NOT in this map defaults to "high".
_BETTER = {
    # local analytics stat keys
    "hltv": "high", "adr": "high", "kast": "high", "kd": "high",
    "open_wr": "high", "traded_pct": "high", "udr": "high",
    "enemy_flashed": "high", "avg_blind": "high",
    "team_flashed": "low",            # flashing teammates is bad
    # Leetify Performance Metric Tool aim/util keys
    "avg_reaction_time": "low",       # faster is better
    "avg_preaim": "low",              # smaller crosshair-placement error is better
    "avg_accuracy_enemy_spotted": "high",
    "avg_spray_accuracy": "high",
    "avg_accuracy_head": "high",
    "avg_counter_strafing_good_ratio": "high",
    "avg_flashbang_hit_foe_avg_duration": "high",
    "avg_flashbang_hit_foe": "high",
    "avg_total_flash_blind_duration": "high",
    "avg_he_foes_damage_avg": "high",
    "avg_flashbang_hit_friend": "low",   # flashing friends is bad
    # neutral "volume" metrics (no strong better direction) -> treated as "high"
    # by default; they exist mostly for display, not as a skill signal.
    "avg_he_thrown": "high", "avg_molotov_thrown": "high",
    "avg_smoke_thrown": "high", "avg_flashbang_thrown": "high",
}

# "near" band: a player within this fraction of the benchmark counts as "near"
# rather than above/below. Relative so it works for both rates and raw counts;
# a tiny absolute floor avoids div-by-zero / silly results near 0.
_NEAR_REL = 0.05
_NEAR_ABS_FLOOR = 1e-9


def better_for(metric):
    """Direction for a metric key: "high" (bigger better) or "low" (smaller better)."""
    return _BETTER.get(metric, "high")


# ---- bucket mapping (pure) --------------------------------------------------
def premier_bucket(rating):
    """Map a CS2 Premier rating int to a 5k-wide bucket label, or None if invalid.

    0-4999 -> "0-5k", 5000-9999 -> "5k-10k", ... 25000-29999 -> "25k-30k",
    30000+ -> "30k+". None / non-numeric / negative -> None (we do NOT guess a
    bucket for garbage input).
    """
    r = _as_int(rating)
    if r is None or r < 0:
        return None
    idx = r // 5000
    if idx >= len(_PREMIER_BUCKETS):
        return _PREMIER_BUCKETS[-1]   # 30k+
    return _PREMIER_BUCKETS[idx]


def faceit_level(elo):
    """Map a FACEIT elo int to a level 1-10 using official brackets, or None.

    Brackets (inclusive): 1:100-500, 2:501-750, 3:751-900, 4:901-1050,
    5:1051-1200, 6:1201-1350, 7:1351-1530, 8:1531-1750, 9:1751-2000, 10:2001+.
    Below 100 or non-numeric -> None.
    """
    e = _as_int(elo)
    if e is None:
        return None
    for level, lo, hi in _FACEIT_BRACKETS:
        if e >= lo and (hi is None or e <= hi):
            return level
    return None   # below the lowest bracket floor


def _as_int(v):
    """v as an int, or None if missing / not an integer-like number. Bools are not ints here."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):  # NaN/Inf
        return None
    return int(f)


def _as_float(v):
    """v as a finite float, or None if missing / not numeric (NaN/Inf -> None). Bools excluded."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


# ---- dataset store ----------------------------------------------------------
def _normalize_record(rec, *, source_file=None):
    """Coerce a raw loaded JSON object into a benchmark record with stable fields.

    Returns the record dict (with provenance + a numeric-cleaned ``metrics`` map),
    or None if it's not a usable record (not a dict, or no bucket_type). We do NOT
    fabricate any field: absent provenance stays None, absent metrics stays {}.
    """
    if not isinstance(rec, dict):
        return None
    bucket_type = rec.get("bucket_type")
    if bucket_type not in BUCKET_TYPES:
        return None
    metrics_in = rec.get("metrics") if isinstance(rec.get("metrics"), dict) else {}
    # Keep only metric values that are real finite numbers. A null/garbage metric is
    # DROPPED (becomes "unavailable" at compare time) rather than coerced to 0.
    metrics = {}
    for k, v in metrics_in.items():
        fv = _as_float(v)
        if fv is not None:
            metrics[k] = fv
    return {
        "source_name": rec.get("source_name"),
        "source_url": rec.get("source_url"),
        "source_date": rec.get("source_date"),
        "region": rec.get("region", "all"),
        "map_filter": rec.get("map_filter", "all"),
        "bucket_type": bucket_type,
        "bucket": rec.get("bucket"),
        "metrics": metrics,
        "sample_size": rec.get("sample_size"),
        "attribution": rec.get("attribution"),
        "_source_file": source_file,
    }


def load_datasets(directory=None):
    """Load + normalize every benchmark record from ``*.json`` in the benchmarks dir.

    Each file may be a single record object or a JSON list of records. Files that
    are missing/corrupt/not-records are skipped silently (never raises). Returns a
    flat list of normalized records. Reads from disk every call (datasets are small
    and admin-imported infrequently); wrap in a cache at the call site if needed.
    """
    directory = directory or BENCHMARKS_DIR
    out = []
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return out
    for name in names:
        if not name.lower().endswith(".json"):
            continue
        path = os.path.join(directory, name)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        records = data if isinstance(data, list) else [data]
        for raw in records:
            rec = _normalize_record(raw, source_file=name)
            if rec is not None:
                out.append(rec)
    return out


def _matches(rec, bucket_type, bucket, region, map_filter):
    """True if a record matches the requested bucket + region/map filters.

    region/map_filter use loose matching: a request of "all" matches any record;
    otherwise we match the exact region/map OR a record that itself declares "all"
    (a global dataset applies to every region/map). Bucket compares as a string so
    faceit level 8 (int) and "8" both match.
    """
    if rec.get("bucket_type") != bucket_type:
        return False
    if str(rec.get("bucket")) != str(bucket):
        return False
    if region != "all":
        rr = rec.get("region", "all")
        if rr != "all" and str(rr).lower() != str(region).lower():
            return False
    if map_filter != "all":
        rm = rec.get("map_filter", "all")
        if rm != "all" and str(rm).lower() != str(map_filter).lower():
            return False
    return True


def get_dataset(bucket_type, bucket, *, region="all", map_filter="all", datasets=None):
    """Return the single best-matching benchmark record for a bucket, or None.

    Prefers a record whose region/map_filter exactly equal the request over a
    generic "all" record. ``datasets`` lets a caller pass a pre-loaded list (e.g. a
    cached :func:`load_datasets` result) to avoid re-reading disk.
    """
    records = datasets if datasets is not None else load_datasets()
    candidates = [r for r in records
                  if _matches(r, bucket_type, bucket, region, map_filter)]
    if not candidates:
        return None

    def specificity(r):
        score = 0
        if region != "all" and str(r.get("region", "all")).lower() == str(region).lower():
            score += 2
        if map_filter != "all" and str(r.get("map_filter", "all")).lower() == str(map_filter).lower():
            score += 1
        return score

    candidates.sort(key=specificity, reverse=True)
    return candidates[0]


# ---- compare ----------------------------------------------------------------
def _status(player_val, bench_val, better):
    """Per-metric status: above/near/below/unavailable, honoring better=high/low.

    "unavailable" whenever EITHER side is missing -- we never compare against a
    number we don't have. "near" when within the relative band; otherwise above/
    below from the player's perspective (above == player is on the good side).
    """
    if player_val is None or bench_val is None:
        return "unavailable"
    band = max(abs(bench_val) * _NEAR_REL, _NEAR_ABS_FLOOR)
    diff = player_val - bench_val
    if abs(diff) <= band:
        return "near"
    player_higher = diff > 0
    good = player_higher if better == "high" else not player_higher
    return "above" if good else "below"


def compare(player_stats, bucket_type, bucket, *, region="all", map_filter="all",
            datasets=None):
    """Compare a player's LOCAL stats against one skill-bucket benchmark.

    ``player_stats`` is a dict of the local analytics keys (adr, kast, hltv, kd,
    open_wr, traded_pct, udr, plus utility counts). ``bucket_type`` is one of
    :data:`BUCKET_TYPES`; ``bucket`` is the bucket label (e.g. "15k-20k" or 8).

    For every metric the matched dataset carries (intersected with metrics the
    player has), returns a row::

        {metric, player_value, benchmark_value, delta, status}

    ``status`` in {"above","near","below","unavailable"}; ``delta`` is
    ``player_value - benchmark_value`` (None if either side missing). A metric the
    dataset does NOT carry is emitted with ``benchmark_value: None`` and status
    ``"unavailable"`` -- it is NEVER fabricated. The result always carries the
    source provenance (or nulls). When no dataset matches, ``available`` is False
    and every metric the player has is reported "unavailable".

    PURE over its inputs (apart from optionally reading datasets from disk); never
    raises on bad ``player_stats`` -- non-dict input is treated as empty.
    """
    player_stats = player_stats if isinstance(player_stats, dict) else {}
    rec = get_dataset(bucket_type, bucket, region=region, map_filter=map_filter,
                      datasets=datasets)

    # Player-side comparable values (finite numbers only).
    player_vals = {}
    for k, v in player_stats.items():
        fv = _as_float(v)
        if fv is not None:
            player_vals[k] = fv

    if rec is None:
        # No sourced benchmark at all -> fabricate nothing.
        metrics_out = [
            {"metric": k, "player_value": player_vals[k], "benchmark_value": None,
             "delta": None, "status": "unavailable"}
            for k in sorted(player_vals)
        ]
        return {
            "available": False,
            "bucket_type": bucket_type,
            "bucket": bucket,
            "region": region,
            "map_filter": map_filter,
            "source": None,
            "source_url": None,
            "source_date": None,
            "attribution": None,
            "sample_size": None,
            "metrics": metrics_out,
        }

    bench = rec.get("metrics") or {}
    # Compare the union of what the player has and what the benchmark carries, so a
    # benchmark metric with no matching player stat (and vice versa) is still
    # surfaced as "unavailable" rather than silently dropped.
    keys = sorted(set(player_vals) | set(bench))
    metrics_out = []
    for k in keys:
        pv = player_vals.get(k)
        bv = _as_float(bench.get(k))   # bench already numeric-cleaned, but be safe
        status = _status(pv, bv, better_for(k))
        delta = round(pv - bv, 4) if (pv is not None and bv is not None) else None
        metrics_out.append({
            "metric": k,
            "player_value": pv,
            "benchmark_value": bv,
            "delta": delta,
            "status": status,
        })

    return {
        "available": True,
        "bucket_type": rec.get("bucket_type"),
        "bucket": rec.get("bucket"),
        "region": rec.get("region", "all"),
        "map_filter": rec.get("map_filter", "all"),
        "source": rec.get("source_name"),
        "source_url": rec.get("source_url"),
        "source_date": rec.get("source_date"),
        "attribution": rec.get("attribution"),
        "sample_size": rec.get("sample_size"),
        "metrics": metrics_out,
    }


# ---- perf-metric comparison: curated SAFE player->Leetify key map -----------
# compare() matches purely by key name, and our analytics keys (counter_strafe, hs_pct, smokes...)
# don't share names with Leetify's avg_* keys -- so NOTHING is compared unless we explicitly bridge
# it here. This map is that bridge, and it contains ONLY pairs whose DEFINITIONS are compatible, so
# a misleading apples-to-oranges delta is impossible by construction. Audit: PERF_METRICS_FEASIBILITY.md.
#
# DELIBERATELY ABSENT (unsafe -- do NOT add without aligning definitions / sourcing data):
#   counter_strafe -> avg_counter_strafing_good_ratio   different definition + ~2x scale (~60% vs ~30%)
#   hs_pct         -> avg_accuracy_head                 hs_pct is headshot-KILL %, not head-HIT accuracy
#   <none yet>     -> avg_spray_accuracy                approximate (needs shot<->hit matching)
#   accuracy       -> avg_accuracy_enemy_spotted        overall accuracy != SPOTTED accuracy (no vis data)
#   <none>         -> avg_preaim                        experimental (no LOS/occlusion, 16fps)
#   <none>         -> avg_reaction_time                 no visibility/contact-time data
PERF_COMPARE_MAP = {
    "hes": "avg_he_thrown",
    "flashes_thrown": "avg_flashbang_thrown",
    "smokes": "avg_smoke_thrown",
    "molotovs": "avg_molotov_thrown",
    "he_dmg_per_he": "avg_he_foes_damage_avg",
    "headshot_accuracy": "avg_accuracy_head",
    "flashes_hit_foe_per_game": "avg_flashbang_hit_foe",
    "flashes_hit_friend_per_game": "avg_flashbang_hit_friend",
    "total_flash_blind_duration_per_game": "avg_total_flash_blind_duration",
    "flash_foe_avg_duration": "avg_flashbang_hit_foe_avg_duration",
}


def perf_player_vals(player_stats):
    """Re-key a player's analytics stats into Leetify metric keys, SAFE pairs only. A field that's
    missing or None is skipped, so it stays 'unavailable' downstream rather than a guessed delta."""
    player_stats = player_stats if isinstance(player_stats, dict) else {}
    out = {}
    for local_key, leetify_key in PERF_COMPARE_MAP.items():
        v = _as_float(player_stats.get(local_key))
        if v is not None:
            out[leetify_key] = v
    return out


def perf_compare(player_stats, bucket_type, bucket, *, region="all", datasets=None):
    """compare() a player's performance metrics against a skill bucket using ONLY the curated safe
    key map. Unsafe metrics never receive a player value, so they surface as 'unavailable' (benchmark
    shown, player side blank) instead of a wrong delta. bucket_type is premier_rating | faceit_level."""
    return compare(perf_player_vals(player_stats), bucket_type, bucket,
                   region=region, map_filter="all", datasets=datasets)


# ---- Leetify Performance Metric Tool import ---------------------------------
# The ONLY metric keys the documented Leetify PDL response carries. parse_leetify_pdl
# maps exactly these into the record's metrics map and NOTHING else -- so K/D, ADR,
# KAST, win rate, opening/trade/clutch can never leak in from this source.
LEETIFY_METRIC_FIELDS = (
    "avg_reaction_time",
    "avg_preaim",
    "avg_accuracy_enemy_spotted",
    "avg_spray_accuracy",
    "avg_accuracy_head",
    "avg_he_thrown",
    "avg_he_foes_damage_avg",
    "avg_molotov_thrown",
    "avg_smoke_thrown",
    "avg_flashbang_thrown",
    "avg_counter_strafing_good_ratio",
    "avg_flashbang_hit_foe_avg_duration",
    "avg_flashbang_hit_foe",
    "avg_flashbang_hit_friend",
    "avg_total_flash_blind_duration",
)

# Premier rating buckets expressed as [lo, hi) on the raw 1k-row rating, so 1k rows
# can be aggregated up into our 5k buckets.
_PREMIER_5K_RANGES = [
    ("0-5k", 0, 5000),
    ("5k-10k", 5000, 10000),
    ("10k-15k", 10000, 15000),
    ("15k-20k", 15000, 20000),
    ("20k-25k", 20000, 25000),
    ("25k-30k", 25000, 30000),
    ("30k+", 30000, None),
]


def _leetify_metrics(row):
    """Extract ONLY the documented Leetify metric fields from a row, numeric-cleaned.

    A field that's absent or non-numeric is omitted (stays "unavailable" downstream);
    we never substitute 0 or a guess.
    """
    out = {}
    for f in LEETIFY_METRIC_FIELDS:
        fv = _as_float(row.get(f))
        if fv is not None:
            out[f] = fv
    return out


def _row_rating(row):
    """Best-effort numeric Premier rating for a 1k row (from rank_category/rating/bucket)."""
    for key in ("rank_category", "rating", "premier_rating", "bucket"):
        v = _as_int(row.get(key))
        if v is not None:
            return v
    return None


def _weighted_avg_metrics(rows):
    """player_count-WEIGHTED average of the Leetify metric fields across rows.

    Returns (metrics_dict, total_player_count). For each metric we only average over
    rows that actually carry that metric AND a positive player_count; a metric no row
    carries is omitted (left unavailable). If NO row has a positive player_count we
    return ({}, 0) so the caller can mark the bucket unavailable instead of emitting
    an unweighted (i.e. fabricated-shaped) average.
    """
    total_pc = 0
    for r in rows:
        pc = _as_float(r.get("player_count"))
        if pc and pc > 0:
            total_pc += pc
    if total_pc <= 0:
        return {}, 0

    metrics = {}
    for field in LEETIFY_METRIC_FIELDS:
        num = 0.0
        wsum = 0.0
        for r in rows:
            pc = _as_float(r.get("player_count"))
            val = _as_float(r.get(field))
            if pc and pc > 0 and val is not None:
                num += val * pc
                wsum += pc
        if wsum > 0:
            metrics[field] = round(num / wsum, 6)
    return metrics, int(total_pc)


def parse_leetify_pdl(rows, *, kind="performance-metric-tool", source_url=None,
                      source_date=None, source_name="Leetify", aggregate_premier=False):
    """PURE transform: map documented Leetify PDL rows into benchmark records.

    ``rows`` is a list of dicts shaped like the Leetify Performance Metric Tool
    response. This function does NOT fetch anything -- the caller supplies the rows.

    Each output record carries provenance (``source_name`` default "Leetify",
    plus the caller-supplied ``source_url`` / ``source_date``) and a ``metrics`` map
    containing ONLY :data:`LEETIFY_METRIC_FIELDS`. K/D, ADR, KAST, win rate and
    opening/trade/clutch are NOT in this response and are never emitted here.

    bucket_type is derived from the row's ``rank_category`` shape:
      * FACEIT rows (rank_category looks like a level 1-10) -> bucket_type
        "faceit_level", bucket = the level int.
      * Premier rows (rank_category is a rating like 14000) -> bucket_type
        "premier_rating", bucket = the raw rating (1k row) by default.

    Premier aggregation: with ``aggregate_premier=True`` the 1k Premier rows are
    grouped into our 5k buckets and combined with a **player_count-weighted**
    average. A 5k bucket with no rows -- or rows whose player_count sum is 0 -- is
    emitted as an ``unavailable`` record (``metrics={}``, ``available=False``),
    NOT an unweighted average. region / game_map_id / player_count are carried
    through (player_count summed for aggregated buckets).
    """
    if not isinstance(rows, list):
        return []

    faceit_rows = []
    premier_rows = []
    other_records = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        region = row.get("region", "all")
        map_filter = _map_from_row(row)
        rc = row.get("rank_category")
        level = _as_int(rc)
        # A FACEIT level is a small int 1..10; anything bigger is a Premier rating.
        if level is not None and 1 <= level <= 10:
            faceit_rows.append((row, region, map_filter, level))
        elif level is not None:
            premier_rows.append((row, region, map_filter, level))
        else:
            # Unknown bucket shape -> keep nothing fabricated; skip.
            continue

    out = []

    # FACEIT: one record per row, bucket = level.
    for row, region, map_filter, level in faceit_rows:
        out.append(_record(
            source_name, source_url, source_date, region, map_filter,
            "faceit_level", level, _leetify_metrics(row),
            sample_size=_as_int(row.get("player_count")),
        ))

    if not aggregate_premier:
        # Premier: one record per 1k row, bucket = the raw rating.
        for row, region, map_filter, rating in premier_rows:
            out.append(_record(
                source_name, source_url, source_date, region, map_filter,
                "premier_rating", rating, _leetify_metrics(row),
                sample_size=_as_int(row.get("player_count")),
            ))
        return out + other_records

    # Premier aggregation into 5k buckets, grouped by (region, map_filter).
    groups = {}
    for row, region, map_filter, rating in premier_rows:
        b = premier_bucket(rating)
        if b is None:
            continue
        groups.setdefault((region, map_filter, b), []).append(row)

    for (region, map_filter, bucket_label), _lo, _hi in _expand_groups(groups):
        rows_in = groups.get((region, map_filter, bucket_label), [])
        metrics, total_pc = _weighted_avg_metrics(rows_in)
        if not rows_in or total_pc <= 0 or not metrics:
            # No data / no weight -> explicitly unavailable; do NOT unweighted-average.
            out.append(_record(
                source_name, source_url, source_date, region, map_filter,
                "premier_rating", bucket_label, {}, sample_size=None,
                available=False,
            ))
        else:
            out.append(_record(
                source_name, source_url, source_date, region, map_filter,
                "premier_rating", bucket_label, metrics, sample_size=total_pc,
            ))
    return out + other_records


def _expand_groups(groups):
    """Yield (group_key_tuple, lo, hi) for each present (region, map, bucket) group.

    Just unpacks the keys; lo/hi are unused placeholders kept for clarity/future use.
    """
    for (region, map_filter, bucket_label) in groups:
        yield (region, map_filter, bucket_label), None, None


def _map_from_row(row):
    """Map filter from a Leetify row's game_map_id, defaulting to "all" (all maps)."""
    m = row.get("game_map_id")
    if m in (None, "", 0):
        return "all"
    return m


def _record(source_name, source_url, source_date, region, map_filter,
            bucket_type, bucket, metrics, *, sample_size=None, attribution=None,
            available=True):
    """Build a normalized benchmark record dict (the on-disk / get_dataset shape)."""
    rec = {
        "source_name": source_name,
        "source_url": source_url,
        "source_date": source_date,
        "region": region if region is not None else "all",
        "map_filter": map_filter if map_filter is not None else "all",
        "bucket_type": bucket_type,
        "bucket": bucket,
        "metrics": metrics if isinstance(metrics, dict) else {},
        "sample_size": sample_size,
        "attribution": attribution or source_name,
    }
    if not available:
        rec["available"] = False
    return rec


def manual_perf_record(bucket_type, bucket, metrics, *, region="all", source_name="Leetify",
                       source_date=None, source_url=None, sample_size=None):
    """Build ONE Performance-Metric benchmark record from hand-entered values (admin manual entry).

    Mirrors what :func:`parse_leetify_pdl` emits, but takes a metrics dict directly instead of a raw
    provider row -- the friendly fallback for when the Leetify fetch isn't working. Only keys in
    :data:`LEETIFY_METRIC_FIELDS` are kept, each coerced to a finite float; a blank/garbage value is
    DROPPED (-> "unavailable" downstream, never a fabricated 0). ``bucket_type`` must be
    "premier_rating" or "faceit_level". Returns the record dict, or None when nothing usable was
    entered / the bucket_type is invalid (the caller treats None as "nothing to save").
    """
    if bucket_type not in ("premier_rating", "faceit_level"):
        return None
    clean = {}
    for k, v in (metrics or {}).items():
        if k in LEETIFY_METRIC_FIELDS:
            fv = _as_float(v)
            if fv is not None:
                clean[k] = fv
    if not clean:
        return None
    return _record(source_name, source_url, source_date, region, "all",
                   bucket_type, bucket, clean, sample_size=_as_int(sample_size))


def save_datasets(records, filename, *, directory=None):
    """Atomically write a list of records to ``<benchmarks_dir>/<filename>`` (admin import).

    Mirrors app.atomic_write_json's pattern (temp file + os.replace) so a partial
    write never leaves a corrupt dataset. Returns the path written. Provided as a
    convenience for an admin-import route; the module never writes on its own.
    """
    directory = directory or BENCHMARKS_DIR
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, filename)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(records, fh, indent=2)
    os.replace(tmp, path)
    return path


# ---- Leetify attribution (Public Data Library requirement) ------------------
# Leetify's Public Data Library guidelines require a VISIBLE credit + a link back
# to leetify.com wherever their numbers are shown (UI and exports). These constants
# give the UI/export layer one place to render that required attribution; the data
# itself stays attributed per-record via the "attribution" field too.
LEETIFY_URL = "https://leetify.com"
ATTRIBUTION_TEXT = "Data provided by Leetify"


# ---- Leetify CT/T side-winrate import ---------------------------------------
# The documented "premier-ct-t-side-winrates" rows identify the map with a
# game_map_id. We map that id to our LOCAL de_* map name. The mapping is
# deliberately TOLERANT but NEVER guesses:
#   * an id that already looks like a de_* name is used as-is (lower-cased);
#   * a known variant/alias is translated via _LEETIFY_MAP_ALIASES;
#   * anything else (an unknown id) is SKIPPED -- we do not invent a map.
# This mirrors the no-fake-data rule: an unrecognized map id yields no record
# rather than a record pinned to a guessed map.
_LEETIFY_MAP_ALIASES = {
    # bare map names (no de_ prefix) seen in some Leetify exports
    "mirage": "de_mirage",
    "inferno": "de_inferno",
    "nuke": "de_nuke",
    "overpass": "de_overpass",
    "vertigo": "de_vertigo",
    "ancient": "de_ancient",
    "anubis": "de_anubis",
    "dust2": "de_dust2",
    "dust_2": "de_dust2",
    "dust-2": "de_dust2",
    "train": "de_train",
    "cache": "de_cache",
    "cbble": "de_cbble",
    "cobblestone": "de_cbble",
    "office": "de_office",
    "italy": "de_italy",
    # NOTE: Leetify's live game_map_id is an opaque NUMERIC id (e.g. 101, 1911359) with no public
    # id->name reference we could verify. We deliberately do NOT guess those (no-fake-data rule) --
    # per-map rows with an unmapped numeric id are skipped. The "all" (all-maps) row IS authoritative
    # and is handled in _local_map_id, so the global comparison works without any id mapping. An admin
    # can supply a verified id->map dict via parse_leetify_ct_t(..., extra_map_ids=...) to light up per-map.
}


def _local_map_id(game_map_id):
    """Map a Leetify game_map_id to a LOCAL map name (de_*), or None if unmappable.

    Tolerant but never-guessing: a value that already looks like a ``de_*`` (or
    ``cs_*``/``ar_*``) map name is accepted as-is (lower-cased); a known alias is
    translated; an empty/unknown id returns None so the caller SKIPS the row.
    """
    if game_map_id is None:
        return None
    s = str(game_map_id).strip().lower()
    if not s:
        return None
    if s == "all":                 # the authoritative all-maps aggregate row (no id mapping needed)
        return "all"
    # already a real map name -> use directly (do not second-guess a valid de_ id).
    if s.startswith(("de_", "cs_", "ar_")):
        return s
    return _LEETIFY_MAP_ALIASES.get(s)


# Public, importable alias table so callers/tests can inspect or extend the mapping.
LEETIFY_MAP_IDS = dict(_LEETIFY_MAP_ALIASES)

# The CT/T metric keys a side-winrate record carries. Both are 0-100 percentages
# after normalization (see _winrate_to_percent). Higher win rate is "better".
CT_T_METRIC_FIELDS = ("ct_win_rate", "t_win_rate")
_BETTER["ct_win_rate"] = "high"
_BETTER["t_win_rate"] = "high"


def _winrate_to_percent(v):
    """Normalize a Leetify win-rate value to a 0-100 percentage, or None.

    ASSUMPTION (documented): Leetify's avg_ct_win_rate / avg_t_win_rate may be
    published either as a FRACTION (0..1, e.g. 0.52) or already as a PERCENT
    (0..100, e.g. 52.0). We normalize deterministically: a value whose magnitude is
    <= 1.0 is treated as a fraction and multiplied by 100; a value > 1.0 is treated
    as already a percent and passed through. This is a unit normalization, not a
    fabricated number -- the underlying value is whatever the source carried.
    A non-numeric/missing value returns None (stays "unavailable" downstream).
    """
    f = _as_float(v)
    if f is None:
        return None
    if abs(f) <= 1.0:
        return round(f * 100.0, 4)
    return round(f, 4)


def parse_leetify_ct_t(rows, *, source_url=None, source_date=None,
                       source_name="Leetify", extra_map_ids=None):
    """PURE transform: documented premier-ct-t-side-winrates rows -> benchmark records.

    ``rows`` is a list of dicts shaped like the Leetify
    ``premier-ct-t-side-winrates`` response. Documented fields used:
    ``game_map_id``, ``region``, ``rating_bucket``, ``total_games``,
    ``total_rounds``, ``avg_ct_win_rate``, ``avg_t_win_rate`` (others ignored).

    Emits ONE record per (map, rating_bucket, region) with::

        bucket_type = "premier_ct_t_side_winrates"
        bucket      = rating_bucket
        map_filter  = <local de_* map>
        region      = <row region or "all">
        metrics     = {"ct_win_rate": <0-100>, "t_win_rate": <0-100>}
        sample_size = total_games

    Rows whose ``game_map_id`` can't be mapped to a local map are SKIPPED (never
    pinned to a guessed map). Win-rate values are normalized to 0-100 percentages
    via :func:`_winrate_to_percent`. Does NOT fetch -- the caller supplies rows.
    Never raises on bad input (non-list -> []; bad row -> skipped).
    """
    if not isinstance(rows, list):
        return []
    emap = {str(k): str(v) for k, v in (extra_map_ids or {}).items()}   # admin-supplied verified id->map
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        gid = row.get("game_map_id")
        local_map = emap.get(str(gid)) or _local_map_id(gid)
        if local_map is None:
            continue  # unknown map id -> skip, never guess
        bucket = row.get("rating_bucket")
        if bucket is None or (isinstance(bucket, str) and not bucket.strip()):
            continue  # no bucket -> nothing to key on
        region = row.get("region") or "all"
        metrics = {}
        ct = _winrate_to_percent(row.get("avg_ct_win_rate"))
        t = _winrate_to_percent(row.get("avg_t_win_rate"))
        if ct is not None:
            metrics["ct_win_rate"] = ct
        if t is not None:
            metrics["t_win_rate"] = t
        out.append(_record(
            source_name, source_url, source_date, region, local_map,
            "premier_ct_t_side_winrates", bucket, metrics,
            sample_size=_as_int(row.get("total_games")),
        ))
    return out


def ct_t_benchmark(map_name, bucket, *, region="all", datasets=None):
    """Public CT/T side win-rates for a map + bucket (+ region), or None.

    Looks up a ``premier_ct_t_side_winrates`` record for ``map_name`` and ``bucket``,
    preferring an exact ``region`` match over a generic "all" record (via
    :func:`get_dataset`'s specificity ranking). Returns::

        {ct_win_rate, t_win_rate, sample_size,
         source_name, source_url, source_date, attribution}

    where win rates are 0-100 percentages (the normalized values stored on the
    record), or None when no matching dataset is loaded / the record carries
    neither win rate. ``datasets`` may be a pre-loaded list to avoid disk reads.
    Never fabricates a value -- a missing metric stays None, and if BOTH win rates
    are absent the whole lookup is considered unavailable (returns None).

    The all-maps aggregate (``map_name == "all"``) is matched EXPLICITLY against
    records whose ``map_filter`` is literally "all" -- :func:`get_dataset` treats a
    requested map_filter of "all" as "any map", which (now that per-map rows can
    coexist with the aggregate) could otherwise return a single map's row.
    """
    if str(map_name).lower() == "all":
        recs = datasets if datasets is not None else load_datasets()
        cands = [r for r in recs
                 if r.get("bucket_type") == "premier_ct_t_side_winrates"
                 and str(r.get("bucket")) == str(bucket)
                 and str(r.get("map_filter", "all")).lower() == "all"
                 and (region == "all" or str(r.get("region", "all")).lower() in ("all", str(region).lower()))]
        # prefer an exact region match over a generic "all"-region aggregate
        cands.sort(key=lambda r: 1 if (region != "all"
                   and str(r.get("region", "all")).lower() == str(region).lower()) else 0, reverse=True)
        rec = cands[0] if cands else None
    else:
        rec = get_dataset("premier_ct_t_side_winrates", bucket, region=region,
                          map_filter=map_name, datasets=datasets)
    if rec is None:
        return None
    metrics = rec.get("metrics") or {}
    ct = _as_float(metrics.get("ct_win_rate"))
    t = _as_float(metrics.get("t_win_rate"))
    if ct is None and t is None:
        return None  # no real winrate present -> unavailable, not a guess
    return {
        "ct_win_rate": ct,
        "t_win_rate": t,
        "sample_size": rec.get("sample_size"),
        "source_name": rec.get("source_name"),
        "source_url": rec.get("source_url"),
        "source_date": rec.get("source_date"),
        "attribution": rec.get("attribution"),
    }


# ---- admin-triggered fetch (network; NEVER called at import time or in tests) ----
# Base host for Leetify's public data library API. The full URL is built per call
# from (platform, kind, date) -- see fetch_leetify. This module never calls it on
# its own; only an admin route does, one-shot (no retries/looping).
LEETIFY_API_BASE = "https://api-public-data-library.i-prod.leetify.com/api"
_FETCH_TIMEOUT = 15  # seconds
_FETCH_USER_AGENT = "VantageGG/1.0 (+https://leetify.com)"


def leetify_url(date, *, kind="performance-metric-tool", platform="premier"):
    """Build the Leetify public-data-library URL for (platform, kind, date).

    e.g. ``leetify_url("2026-03-01")`` ->
    ``https://api-public-data-library.i-prod.leetify.com/api/premier/v1/performance-metric-tool/2026-03-01``.
    Pure string builder (no network) -- shared by fetch_leetify and tests.
    """
    return "%s/%s/v1/%s/%s" % (LEETIFY_API_BASE, platform, kind, date)


def fetch_leetify(date, *, kind="performance-metric-tool", platform="premier"):
    """Fetch one Leetify public-data-library dataset (ADMIN-ONLY; makes a network call).

    Builds the URL via :func:`leetify_url` (``date`` like "2026-03-01"), GETs it with
    the stdlib ``urllib`` using a ~15s timeout and a normal User-Agent, parses the
    JSON body, and returns the ROWS list. Both a bare JSON list and a
    ``{"data": [...]}`` wrapper are accepted (the rows list is returned either way).

    One-shot: NO retries, NO looping. Raises a clear ``RuntimeError`` on any
    HTTP/URL/parse problem so the admin route can surface it. This function is
    NEVER called at import time or in tests -- only by an explicit admin action.
    """
    url = leetify_url(date, kind=kind, platform=platform)
    req = urllib.request.Request(url, headers={"User-Agent": _FETCH_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError("Leetify fetch failed (HTTP %s) for %s" % (exc.code, url))
    except urllib.error.URLError as exc:
        raise RuntimeError("Leetify fetch failed (%s) for %s" % (exc.reason, url))
    except Exception as exc:  # socket timeout, etc.
        raise RuntimeError("Leetify fetch failed (%s) for %s" % (exc, url))
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Leetify fetch returned invalid JSON for %s: %s" % (url, exc))
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        rows = data.get("data")
        if isinstance(rows, list):
            return rows
        raise RuntimeError("Leetify fetch JSON has no 'data' list for %s" % url)
    raise RuntimeError("Leetify fetch returned unexpected JSON shape for %s" % url)


# ---- biggest actionable gap -------------------------------------------------
# Human-readable labels for the metrics biggest_gap may surface. A metric not here
# falls back to its raw key (we never block a real gap just for lacking a label).
_METRIC_LABELS = {
    "hltv": "Rating", "adr": "ADR", "kast": "KAST", "kd": "K/D",
    "open_wr": "Opening WR", "traded_pct": "Traded %", "udr": "Util Dmg/Rd",
    "enemy_flashed": "Enemies Flashed", "team_flashed": "Teammates Flashed",
    "avg_blind": "Avg Blind Time",
    "ct_win_rate": "CT Win Rate", "t_win_rate": "T Win Rate",
    "avg_reaction_time": "Reaction Time", "avg_preaim": "Pre-aim Error",
    "avg_accuracy_enemy_spotted": "Accuracy (enemy spotted)",
    "avg_spray_accuracy": "Spray Accuracy", "avg_accuracy_head": "Head Accuracy",
    "avg_counter_strafing_good_ratio": "Counter-strafing",
    "avg_flashbang_hit_foe_avg_duration": "Flash Foe Duration",
    "avg_flashbang_hit_foe": "Enemies Flashed (avg)",
    "avg_flashbang_hit_friend": "Teammates Flashed (avg)",
    "avg_total_flash_blind_duration": "Total Blind Duration",
    "avg_he_foes_damage_avg": "HE Damage", "avg_he_thrown": "HE Thrown",
    "avg_molotov_thrown": "Molotovs Thrown", "avg_smoke_thrown": "Smokes Thrown",
    "avg_flashbang_thrown": "Flashes Thrown",
}


def metric_label(metric):
    """Human label for a metric key (falls back to the raw key)."""
    return _METRIC_LABELS.get(metric, metric)


def biggest_gap(player_stats, bucket_type, bucket, *, region="all",
                map_filter="all", datasets=None):
    """The single most actionable, reliable weakness vs a sourced benchmark, or None.

    Runs :func:`compare` and picks the ONE metric where the player is reliably
    WORSE than the benchmark -- i.e. ``status == "below"`` (already honors each
    metric's higher/lower-is-better direction via :func:`better_for`) AND a real
    ``benchmark_value`` is present. Candidates are ranked by NORMALIZED gap size so
    a percentage metric doesn't always dominate a raw-count one; the largest
    normalized gap wins.

    Reliability guard: the matched dataset must carry a present, non-trivial
    ``sample_size`` (> 0). Without a real sample size we return None rather than
    coach off a one-off / unsourced number.

    Returns::

        {metric, label, player_value, benchmark_value, delta,
         source_name, source_url, source_date, attribution}

    or None when nothing is reliably below benchmark. NEVER returns an
    "unavailable"/no-benchmark metric (those are filtered out up front).
    """
    res = compare(player_stats, bucket_type, bucket, region=region,
                  map_filter=map_filter, datasets=datasets)
    if not res.get("available"):
        return None
    # Reliability: require a real, non-trivial sample size on the matched dataset.
    sample_size = _as_int(res.get("sample_size"))
    if sample_size is None or sample_size <= 0:
        return None

    best = None
    best_score = -1.0
    for m in res.get("metrics", []):
        if m.get("status") != "below":
            continue
        pv = m.get("player_value")
        bv = m.get("benchmark_value")
        if pv is None or bv is None:   # defensive: "below" implies both, but be safe
            continue
        gap = abs(pv - bv)
        base = max(abs(pv) + abs(bv), 1.0)   # normalize; avoid div-by-zero near 0
        score = gap / base
        if score > best_score:
            best_score = score
            best = m

    if best is None:
        return None
    return {
        "metric": best["metric"],
        "label": metric_label(best["metric"]),
        "player_value": best["player_value"],
        "benchmark_value": best["benchmark_value"],
        "delta": best.get("delta"),
        "source_name": res.get("source"),
        "source_url": res.get("source_url"),
        "source_date": res.get("source_date"),
        "attribution": res.get("attribution"),
    }
