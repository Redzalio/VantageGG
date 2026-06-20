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
