"""Tests for benchmarks.py -- the source-agnostic skill-bucket benchmark layer.

Covers: Premier/FACEIT bucket-mapping boundaries; the NO-FAKE-DATA contract
(compare returns "unavailable", never a number, for a metric absent from the
dataset, and "available": False with no dataset loaded); parse_leetify_pdl
mapping the documented util/aim fields and NOT emitting KD/ADR/KAST; the
player_count-weighted 1k -> 5k Premier aggregation; provenance/attribution in
output; and missing-bucket -> unavailable.

Hermetic: all dataset I/O is pointed at tmp_path via the BENCHMARKS_DIR env knob;
the real data/benchmarks/ folder is never read.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import benchmarks  # noqa: E402


# ---- manual_perf_record: hand-entered Performance-Metric values (fetch fallback) ----
def test_manual_perf_record_keeps_known_drops_unknown_and_blanks():
    rec = benchmarks.manual_perf_record(
        "premier_rating", "15k-20k",
        {"avg_reaction_time": 0.6, "avg_preaim": "", "bogus_key": 5, "avg_accuracy_head": "30"},
        source_name="Leetify", source_date="2026-03-01", sample_size=500)
    assert rec["bucket_type"] == "premier_rating" and rec["bucket"] == "15k-20k"
    assert rec["map_filter"] == "all" and rec["sample_size"] == 500 and rec["attribution"]
    # only documented keys with finite numbers survive; unknown key + blank are dropped (no fake 0)
    assert rec["metrics"] == {"avg_reaction_time": 0.6, "avg_accuracy_head": 30.0}


def test_manual_perf_record_faceit_and_none_paths():
    f = benchmarks.manual_perf_record("faceit_level", 8, {"avg_spray_accuracy": 22.5})
    assert f["bucket_type"] == "faceit_level" and f["bucket"] == 8
    assert benchmarks.manual_perf_record("bad_type", "15k-20k", {"avg_reaction_time": 0.6}) is None  # bad bucket_type
    assert benchmarks.manual_perf_record("premier_rating", "15k-20k", {}) is None                    # no metrics
    assert benchmarks.manual_perf_record("premier_rating", "15k-20k",
                                         {"avg_preaim": None, "x": "y"}) is None                      # nothing usable


# ---- a fixture shaped like the documented Leetify PDL response --------------
# Each row carries ONLY the documented Performance Metric Tool fields plus the
# bucket/region/map/player_count metadata. Crucially there is NO kd/adr/kast/
# win-rate/opening here -- the real PDL response does not include them.
def _leetify_premier_row(rating, player_count, **over):
    row = {
        "rank_category": rating,            # raw 1k Premier rating
        "game_map_id": "all",
        "region": "eu",
        "player_count": player_count,
        "avg_reaction_time": 0.60,
        "avg_preaim": 7.0,
        "avg_accuracy_enemy_spotted": 30.0,
        "avg_spray_accuracy": 25.0,
        "avg_accuracy_head": 24.0,
        "avg_he_thrown": 1.2,
        "avg_he_foes_damage_avg": 9.0,
        "avg_molotov_thrown": 0.8,
        "avg_smoke_thrown": 2.1,
        "avg_flashbang_thrown": 1.5,
        "avg_counter_strafing_good_ratio": 0.55,
        "avg_flashbang_hit_foe_avg_duration": 1.1,
        "avg_flashbang_hit_foe": 0.9,
        "avg_flashbang_hit_friend": 0.2,
        "avg_total_flash_blind_duration": 2.0,
    }
    row.update(over)
    return row


def _leetify_faceit_row(level, player_count, **over):
    row = _leetify_premier_row(0, player_count, **over)
    row["rank_category"] = level                # 1..10 => FACEIT level
    return row


# =====================================================================
# Bucket mapping -- Premier
# =====================================================================
def test_premier_bucket_boundaries():
    assert benchmarks.premier_bucket(0) == "0-5k"
    assert benchmarks.premier_bucket(4999) == "0-5k"
    assert benchmarks.premier_bucket(5000) == "5k-10k"
    assert benchmarks.premier_bucket(9999) == "5k-10k"
    assert benchmarks.premier_bucket(10000) == "10k-15k"
    assert benchmarks.premier_bucket(14999) == "10k-15k"
    assert benchmarks.premier_bucket(15000) == "15k-20k"
    assert benchmarks.premier_bucket(19999) == "15k-20k"
    assert benchmarks.premier_bucket(20000) == "20k-25k"
    assert benchmarks.premier_bucket(24999) == "20k-25k"
    assert benchmarks.premier_bucket(25000) == "25k-30k"
    assert benchmarks.premier_bucket(29999) == "25k-30k"
    assert benchmarks.premier_bucket(30000) == "30k+"
    assert benchmarks.premier_bucket(99999) == "30k+"


def test_premier_bucket_invalid_is_none():
    assert benchmarks.premier_bucket(None) is None
    assert benchmarks.premier_bucket(-1) is None
    assert benchmarks.premier_bucket("abc") is None
    assert benchmarks.premier_bucket(True) is None     # bool is not a rating
    assert benchmarks.premier_bucket(float("nan")) is None


# =====================================================================
# Bucket mapping -- FACEIT
# =====================================================================
def test_faceit_level_boundaries():
    # exact bracket edges (inclusive on both ends)
    assert benchmarks.faceit_level(100) == 1
    assert benchmarks.faceit_level(500) == 1
    assert benchmarks.faceit_level(501) == 2
    assert benchmarks.faceit_level(750) == 2
    assert benchmarks.faceit_level(751) == 3
    assert benchmarks.faceit_level(900) == 3
    assert benchmarks.faceit_level(901) == 4
    assert benchmarks.faceit_level(1050) == 4
    assert benchmarks.faceit_level(1051) == 5
    assert benchmarks.faceit_level(1200) == 5
    assert benchmarks.faceit_level(1201) == 6
    assert benchmarks.faceit_level(1350) == 6
    assert benchmarks.faceit_level(1351) == 7
    assert benchmarks.faceit_level(1530) == 7
    assert benchmarks.faceit_level(1531) == 8
    assert benchmarks.faceit_level(1750) == 8
    assert benchmarks.faceit_level(1751) == 9
    assert benchmarks.faceit_level(2000) == 9
    assert benchmarks.faceit_level(2001) == 10
    assert benchmarks.faceit_level(5000) == 10


def test_faceit_level_out_of_range_is_none():
    assert benchmarks.faceit_level(99) is None         # below the floor
    assert benchmarks.faceit_level(None) is None
    assert benchmarks.faceit_level("nope") is None
    assert benchmarks.faceit_level(True) is None


# =====================================================================
# compare() -- NO-FAKE-DATA contract
# =====================================================================
def _write_dataset(tmp_path, records, name="ds.json"):
    """Write records to a temp benchmarks dir and point the module at it."""
    d = tmp_path / "benchmarks"
    d.mkdir(exist_ok=True)
    (d / name).write_text(json.dumps(records), encoding="utf-8")
    benchmarks.BENCHMARKS_DIR = str(d)
    return str(d)


def test_compare_unavailable_for_metric_absent_from_dataset(tmp_path):
    # Dataset carries only avg_reaction_time for this bucket. The player also has
    # kast/adr -- those must come back "unavailable" with benchmark_value None,
    # NOT a fabricated number.
    _write_dataset(tmp_path, [{
        "source_name": "TestSrc", "source_url": "https://t.invalid",
        "source_date": "2026-06-01", "bucket_type": "premier_rating",
        "bucket": "15k-20k", "region": "all", "map_filter": "all",
        "metrics": {"avg_reaction_time": 0.62},
        "attribution": "Test attribution",
    }])
    res = benchmarks.compare(
        {"kast": 72.0, "adr": 85.0, "avg_reaction_time": 0.58},
        "premier_rating", "15k-20k",
    )
    assert res["available"] is True
    by = {m["metric"]: m for m in res["metrics"]}

    # present in dataset -> compared with a real number
    assert by["avg_reaction_time"]["benchmark_value"] == 0.62
    assert by["avg_reaction_time"]["status"] in ("above", "near", "below")
    # faster reaction (0.58 < 0.62, lower-is-better) -> player is "above" (good side)
    assert by["avg_reaction_time"]["status"] == "above"

    # absent from dataset -> unavailable, benchmark stays None (never guessed)
    for k in ("kast", "adr"):
        assert by[k]["benchmark_value"] is None
        assert by[k]["delta"] is None
        assert by[k]["status"] == "unavailable"
        assert not isinstance(by[k]["benchmark_value"], (int, float))


def test_compare_no_dataset_loaded_available_false(tmp_path):
    # Empty benchmarks dir -> nothing matches -> available False, and EVERY player
    # metric is "unavailable" with a None benchmark (no fabrication anywhere).
    d = tmp_path / "benchmarks"
    d.mkdir(exist_ok=True)
    benchmarks.BENCHMARKS_DIR = str(d)
    res = benchmarks.compare({"kast": 72.0, "adr": 85.0, "hltv": 1.1},
                             "premier_rating", "15k-20k")
    assert res["available"] is False
    assert res["source"] is None
    assert res["source_url"] is None
    assert res["source_date"] is None
    assert res["attribution"] is None
    for m in res["metrics"]:
        assert m["benchmark_value"] is None
        assert m["delta"] is None
        assert m["status"] == "unavailable"


def test_compare_missing_bucket_is_unavailable(tmp_path):
    # Dataset has 15k-20k only; asking for 30k+ matches nothing -> available False.
    _write_dataset(tmp_path, [{
        "source_name": "TestSrc", "source_url": "https://t.invalid",
        "source_date": "2026-06-01", "bucket_type": "premier_rating",
        "bucket": "15k-20k", "metrics": {"avg_reaction_time": 0.62},
    }])
    res = benchmarks.compare({"avg_reaction_time": 0.6}, "premier_rating", "30k+")
    assert res["available"] is False
    assert all(m["status"] == "unavailable" for m in res["metrics"])
    assert all(m["benchmark_value"] is None for m in res["metrics"])


def test_compare_provenance_present_in_output(tmp_path):
    _write_dataset(tmp_path, [{
        "source_name": "Leetify", "source_url": "https://leetify.com/x",
        "source_date": "2026-06-10", "bucket_type": "faceit_level", "bucket": 8,
        "region": "all", "map_filter": "all",
        "metrics": {"avg_preaim": 6.2}, "sample_size": 1234,
        "attribution": "Data: Leetify",
    }])
    res = benchmarks.compare({"avg_preaim": 6.0}, "faceit_level", 8)
    assert res["source"] == "Leetify"
    assert res["source_url"] == "https://leetify.com/x"
    assert res["source_date"] == "2026-06-10"
    assert res["attribution"] == "Data: Leetify"
    assert res["sample_size"] == 1234


def test_compare_near_band_and_below(tmp_path):
    _write_dataset(tmp_path, [{
        "source_name": "S", "source_url": "u", "source_date": "d",
        "bucket_type": "premier_rating", "bucket": "10k-15k",
        "metrics": {"adr": 80.0, "team_flashed": 2.0},
    }])
    # adr 81 vs 80 -> within 5% band -> near; higher-better otherwise.
    res = benchmarks.compare({"adr": 81.0, "team_flashed": 5.0},
                             "premier_rating", "10k-15k")
    by = {m["metric"]: m for m in res["metrics"]}
    assert by["adr"]["status"] == "near"
    # team_flashed is lower-is-better; 5 > 2 by a lot -> player is "below" (worse).
    assert by["team_flashed"]["status"] == "below"


def test_compare_non_dict_player_stats_does_not_raise(tmp_path):
    d = tmp_path / "benchmarks"
    d.mkdir(exist_ok=True)
    benchmarks.BENCHMARKS_DIR = str(d)
    res = benchmarks.compare(None, "premier_rating", "15k-20k")
    assert res["available"] is False
    assert res["metrics"] == []


# =====================================================================
# parse_leetify_pdl -- maps documented fields, never KD/ADR/KAST
# =====================================================================
def test_parse_leetify_maps_util_aim_fields():
    rows = [_leetify_faceit_row(8, 5000)]
    recs = benchmarks.parse_leetify_pdl(
        rows, source_url="https://leetify.com/pdl", source_date="2026-06-15")
    assert len(recs) == 1
    rec = recs[0]
    assert rec["bucket_type"] == "faceit_level"
    assert rec["bucket"] == 8
    assert rec["source_name"] == "Leetify"
    assert rec["source_url"] == "https://leetify.com/pdl"
    assert rec["source_date"] == "2026-06-15"
    # every documented metric field mapped through
    for f in benchmarks.LEETIFY_METRIC_FIELDS:
        assert f in rec["metrics"], f
    assert rec["metrics"]["avg_reaction_time"] == 0.60
    assert rec["metrics"]["avg_accuracy_head"] == 24.0


def test_parse_leetify_does_not_emit_kd_adr_kast():
    # Even if a (malformed) row sneaks in kd/adr/kast/win-rate keys, the parser must
    # ignore them -- only documented fields are mapped.
    row = _leetify_faceit_row(5, 1000)
    row.update({"kd": 1.3, "adr": 90.0, "kast": 75.0, "win_rate": 55.0,
                "opening_wr": 60.0, "rating": 1.2})
    recs = benchmarks.parse_leetify_pdl([row], source_url="u", source_date="d")
    metrics = recs[0]["metrics"]
    for forbidden in ("kd", "adr", "kast", "win_rate", "opening_wr", "rating",
                      "hltv", "open_wr", "traded_pct"):
        assert forbidden not in metrics, forbidden
    # and a compare() against this benchmark leaves kd/adr/kast unavailable
    res = benchmarks.compare(
        {"kd": 1.4, "adr": 88.0, "kast": 74.0, "avg_preaim": 6.0},
        "faceit_level", 5,
        datasets=[benchmarks._normalize_record(recs[0])],
    )
    by = {m["metric"]: m for m in res["metrics"]}
    for k in ("kd", "adr", "kast"):
        assert by[k]["status"] == "unavailable"
        assert by[k]["benchmark_value"] is None


def test_parse_leetify_premier_per_row_default():
    rows = [_leetify_premier_row(14200, 1000), _leetify_premier_row(15600, 1000)]
    recs = benchmarks.parse_leetify_pdl(rows, source_url="u", source_date="d")
    # default (no aggregation): one record per 1k row, bucket = raw rating
    assert len(recs) == 2
    assert all(r["bucket_type"] == "premier_rating" for r in recs)
    assert {r["bucket"] for r in recs} == {14200, 15600}


# =====================================================================
# Weighted 1k -> 5k Premier aggregation
# =====================================================================
def test_parse_leetify_weighted_aggregation_5k():
    # Two 1k rows in the 15k-20k band with different player_counts and different
    # avg_reaction_time. The 5k bucket value must be the player_count-WEIGHTED mean.
    rows = [
        _leetify_premier_row(15500, 1000, avg_reaction_time=0.60),
        _leetify_premier_row(16500, 3000, avg_reaction_time=0.50),
    ]
    recs = benchmarks.parse_leetify_pdl(
        rows, source_url="u", source_date="d", aggregate_premier=True)
    band = [r for r in recs if r["bucket"] == "15k-20k"]
    assert len(band) == 1
    rec = band[0]
    # weighted mean = (0.60*1000 + 0.50*3000) / 4000 = 0.525  (NOT the 0.55 simple mean)
    assert abs(rec["metrics"]["avg_reaction_time"] - 0.525) < 1e-9
    assert rec["sample_size"] == 4000
    assert rec.get("available", True) is True


def test_parse_leetify_aggregation_empty_bucket_is_unavailable():
    # All rows fall in 15k-20k; the 20k-25k bucket has no rows -> emitted unavailable
    # with metrics={} and available False (NOT an unweighted/empty average).
    rows = [_leetify_premier_row(15500, 1000), _leetify_premier_row(16000, 1000)]
    recs = benchmarks.parse_leetify_pdl(
        rows, source_url="u", source_date="d", aggregate_premier=True)
    buckets = {r["bucket"]: r for r in recs}
    # only the populated band exists; no fabricated empty bands are created
    assert "15k-20k" in buckets
    assert "20k-25k" not in buckets


def test_parse_leetify_aggregation_zero_playercount_is_unavailable():
    # Rows exist in a band but their player_count sums to 0 -> bucket unavailable,
    # NOT a silent unweighted average.
    rows = [
        _leetify_premier_row(15500, 0, avg_reaction_time=0.60),
        _leetify_premier_row(16500, 0, avg_reaction_time=0.50),
    ]
    recs = benchmarks.parse_leetify_pdl(
        rows, source_url="u", source_date="d", aggregate_premier=True)
    band = [r for r in recs if r["bucket"] == "15k-20k"]
    assert len(band) == 1
    rec = band[0]
    assert rec.get("available") is False
    assert rec["metrics"] == {}


def test_parse_leetify_non_list_returns_empty():
    assert benchmarks.parse_leetify_pdl(None) == []
    assert benchmarks.parse_leetify_pdl("nope") == []


# =====================================================================
# Dataset store round-trip (save -> load -> get)
# =====================================================================
def test_save_load_get_dataset_roundtrip(tmp_path):
    d = str(tmp_path / "benchmarks")
    benchmarks.BENCHMARKS_DIR = d
    recs = benchmarks.parse_leetify_pdl(
        [_leetify_faceit_row(8, 5000)], source_url="u", source_date="2026-06-15")
    path = benchmarks.save_datasets(recs, "leetify.json", directory=d)
    assert os.path.exists(path)

    loaded = benchmarks.load_datasets(d)
    assert len(loaded) == 1
    got = benchmarks.get_dataset("faceit_level", 8, datasets=loaded)
    assert got is not None
    assert got["source_name"] == "Leetify"
    assert "avg_reaction_time" in got["metrics"]


def test_load_datasets_skips_corrupt_files(tmp_path):
    d = tmp_path / "benchmarks"
    d.mkdir()
    (d / "good.json").write_text(json.dumps({
        "bucket_type": "faceit_level", "bucket": 3,
        "metrics": {"avg_preaim": 8.0}, "source_name": "S",
    }), encoding="utf-8")
    (d / "bad.json").write_text("{not valid json", encoding="utf-8")
    (d / "notrecord.json").write_text(json.dumps({"hello": "world"}), encoding="utf-8")
    loaded = benchmarks.load_datasets(str(d))
    # only the one valid record survives; corrupt + non-record are skipped, no raise
    assert len(loaded) == 1
    assert loaded[0]["bucket"] == 3


def test_normalize_drops_non_numeric_metric_values():
    rec = benchmarks._normalize_record({
        "bucket_type": "premier_rating", "bucket": "10k-15k",
        "metrics": {"adr": 80.0, "kast": None, "udr": "oops", "hltv": float("nan")},
        "source_name": "S",
    })
    # only the real finite number survives; null/garbage/NaN are dropped (-> unavailable)
    assert rec["metrics"] == {"adr": 80.0}


# =====================================================================
# CT/T side-winrate import -- parse_leetify_ct_t
# =====================================================================
# A fixture row shaped like the documented premier-ct-t-side-winrates response.
def _ctt_row(game_map_id, bucket, **over):
    row = {
        "game_map_id": game_map_id,
        "region": "eu",
        "rating_bucket": bucket,
        "total_games": 1200,
        "total_rounds": 30000,
        "avg_ct_win_rate": 0.53,     # fraction in this fixture (0..1)
        "avg_t_win_rate": 0.47,
        "avg_ct_rounds_won": 8.0,
        "avg_t_rounds_won": 7.0,
    }
    row.update(over)
    return row


def test_parse_ct_t_maps_de_ids_and_emits_metrics():
    rows = [_ctt_row("de_mirage", "15k-20k")]
    recs = benchmarks.parse_leetify_ct_t(
        rows, source_url="https://leetify.com/dl", source_date="2026-06-15")
    assert len(recs) == 1
    rec = recs[0]
    assert rec["bucket_type"] == "premier_ct_t_side_winrates"
    assert rec["bucket"] == "15k-20k"
    assert rec["map_filter"] == "de_mirage"
    assert rec["region"] == "eu"
    assert rec["source_name"] == "Leetify"
    assert rec["source_url"] == "https://leetify.com/dl"
    assert rec["source_date"] == "2026-06-15"
    assert rec["sample_size"] == 1200
    assert rec["attribution"] == "Leetify"
    # 0.53 fraction normalized to a 0-100 percent
    assert rec["metrics"]["ct_win_rate"] == 53.0
    assert rec["metrics"]["t_win_rate"] == 47.0


def test_parse_ct_t_normalizes_percent_passthrough():
    # If the source already gives percents (>1), they pass through unchanged.
    rows = [_ctt_row("de_inferno", "20k-25k", avg_ct_win_rate=55.0, avg_t_win_rate=45.0)]
    recs = benchmarks.parse_leetify_ct_t(rows, source_url="u", source_date="d")
    m = recs[0]["metrics"]
    assert m["ct_win_rate"] == 55.0
    assert m["t_win_rate"] == 45.0


def test_parse_ct_t_skips_unknown_map_id():
    # Unknown game_map_id -> SKIPPED (never pinned to a guessed map). Known ones survive.
    rows = [
        _ctt_row("de_nuke", "10k-15k"),
        _ctt_row("some_unknown_workshop_map", "10k-15k"),
        _ctt_row("", "10k-15k"),          # empty id -> skip
    ]
    recs = benchmarks.parse_leetify_ct_t(rows, source_url="u", source_date="d")
    assert len(recs) == 1
    assert recs[0]["map_filter"] == "de_nuke"


def test_parse_ct_t_alias_and_bare_name_mapping():
    # A bare "mirage" maps to de_mirage via the alias table.
    rows = [_ctt_row("mirage", "5k-10k")]
    recs = benchmarks.parse_leetify_ct_t(rows, source_url="u", source_date="d")
    assert recs[0]["map_filter"] == "de_mirage"


def test_parse_ct_t_non_list_returns_empty():
    assert benchmarks.parse_leetify_ct_t(None) == []
    assert benchmarks.parse_leetify_ct_t("nope") == []


# =====================================================================
# ct_t_benchmark lookup
# =====================================================================
def test_ct_t_benchmark_none_when_absent():
    # No datasets / no matching map+bucket -> None (never fabricated).
    assert benchmarks.ct_t_benchmark("de_mirage", "15k-20k", datasets=[]) is None
    recs = benchmarks.parse_leetify_ct_t(
        [_ctt_row("de_mirage", "15k-20k")], source_url="u", source_date="d")
    ds = [benchmarks._normalize_record(r) for r in recs]
    # right map, wrong bucket -> None
    assert benchmarks.ct_t_benchmark("de_mirage", "30k+", datasets=ds) is None
    # wrong map, right bucket -> None
    assert benchmarks.ct_t_benchmark("de_dust2", "15k-20k", datasets=ds) is None


def test_ct_t_benchmark_returns_values_and_source():
    recs = benchmarks.parse_leetify_ct_t(
        [_ctt_row("de_mirage", "15k-20k", region="all")],
        source_url="https://leetify.com/x", source_date="2026-06-10")
    ds = [benchmarks._normalize_record(r) for r in recs]
    out = benchmarks.ct_t_benchmark("de_mirage", "15k-20k", datasets=ds)
    assert out is not None
    assert out["ct_win_rate"] == 53.0
    assert out["t_win_rate"] == 47.0
    assert out["sample_size"] == 1200
    assert out["source_name"] == "Leetify"
    assert out["source_url"] == "https://leetify.com/x"
    assert out["source_date"] == "2026-06-10"
    assert out["attribution"] == "Leetify"


def test_ct_t_benchmark_prefers_exact_region():
    # An "all"-region and an "eu"-region record for the same map+bucket: asking for
    # eu must return the eu numbers, not the global "all" ones.
    rows = [
        _ctt_row("de_ancient", "10k-15k", region="all",
                 avg_ct_win_rate=0.50, avg_t_win_rate=0.50, total_games=9999),
        _ctt_row("de_ancient", "10k-15k", region="eu",
                 avg_ct_win_rate=0.57, avg_t_win_rate=0.43, total_games=1111),
    ]
    recs = benchmarks.parse_leetify_ct_t(rows, source_url="u", source_date="d")
    ds = [benchmarks._normalize_record(r) for r in recs]
    out = benchmarks.ct_t_benchmark("de_ancient", "10k-15k", region="eu", datasets=ds)
    assert out["ct_win_rate"] == 57.0
    assert out["sample_size"] == 1111


def test_ct_t_benchmark_none_when_no_winrate_present():
    # A record matching the key but carrying neither win rate -> unavailable (None).
    rec = benchmarks._normalize_record({
        "bucket_type": "premier_ct_t_side_winrates", "bucket": "15k-20k",
        "map_filter": "de_mirage", "region": "all",
        "metrics": {},  # no ct/t winrate
        "source_name": "Leetify",
    })
    assert benchmarks.ct_t_benchmark("de_mirage", "15k-20k", datasets=[rec]) is None


# =====================================================================
# fetch_leetify -- URL building + JSON shape parsing (NO real network)
# =====================================================================
class _FakeResp:
    """Minimal context-manager standing in for urllib's response object."""
    def __init__(self, body):
        self._body = body.encode("utf-8") if isinstance(body, str) else body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


def test_fetch_leetify_url_patterns():
    # Pure URL builder: premier/faceit x perf-metric / ct-t-side-winrates.
    assert benchmarks.leetify_url("2026-03-01") == (
        "https://api-public-data-library.i-prod.leetify.com/api/"
        "premier/v1/performance-metric-tool/2026-03-01")
    assert benchmarks.leetify_url(
        "2026-03-01", kind="premier-ct-t-side-winrates") == (
        "https://api-public-data-library.i-prod.leetify.com/api/"
        "premier/v1/premier-ct-t-side-winrates/2026-03-01")
    assert benchmarks.leetify_url("2026-03-01", platform="faceit") == (
        "https://api-public-data-library.i-prod.leetify.com/api/"
        "faceit/v1/performance-metric-tool/2026-03-01")


def test_fetch_leetify_parses_bare_list(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["ua"] = req.get_header("User-agent")
        return _FakeResp(json.dumps([{"rating_bucket": "15k-20k"}]))

    monkeypatch.setattr(benchmarks.urllib.request, "urlopen", fake_urlopen)
    rows = benchmarks.fetch_leetify("2026-03-01")
    assert rows == [{"rating_bucket": "15k-20k"}]
    # built the premier perf-metric URL, set a timeout + a User-Agent
    assert captured["url"].endswith("/premier/v1/performance-metric-tool/2026-03-01")
    assert captured["timeout"] and captured["timeout"] > 0
    assert captured["ua"]


def test_fetch_leetify_parses_data_wrapper(monkeypatch):
    def fake_urlopen(req, timeout=None):
        return _FakeResp(json.dumps({"data": [{"game_map_id": "de_mirage"}]}))

    monkeypatch.setattr(benchmarks.urllib.request, "urlopen", fake_urlopen)
    rows = benchmarks.fetch_leetify(
        "2026-03-01", kind="premier-ct-t-side-winrates")
    assert rows == [{"game_map_id": "de_mirage"}]


def test_fetch_leetify_faceit_platform_url(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _FakeResp("[]")

    monkeypatch.setattr(benchmarks.urllib.request, "urlopen", fake_urlopen)
    benchmarks.fetch_leetify("2026-03-01", platform="faceit",
                             kind="premier-ct-t-side-winrates")
    assert captured["url"].endswith(
        "/faceit/v1/premier-ct-t-side-winrates/2026-03-01")


def test_fetch_leetify_raises_on_bad_json(monkeypatch):
    def fake_urlopen(req, timeout=None):
        return _FakeResp("{not json")

    monkeypatch.setattr(benchmarks.urllib.request, "urlopen", fake_urlopen)
    try:
        benchmarks.fetch_leetify("2026-03-01")
        assert False, "expected a RuntimeError on bad JSON"
    except RuntimeError:
        pass


def test_fetch_leetify_raises_on_unexpected_shape(monkeypatch):
    # A dict with no 'data' list is not usable -> clear error, not a silent [].
    def fake_urlopen(req, timeout=None):
        return _FakeResp(json.dumps({"nope": 1}))

    monkeypatch.setattr(benchmarks.urllib.request, "urlopen", fake_urlopen)
    try:
        benchmarks.fetch_leetify("2026-03-01")
        assert False, "expected a RuntimeError on unexpected shape"
    except RuntimeError:
        pass


def test_attribution_constants_present():
    assert benchmarks.LEETIFY_URL == "https://leetify.com"
    assert isinstance(benchmarks.ATTRIBUTION_TEXT, str) and benchmarks.ATTRIBUTION_TEXT
    assert "leetify" in benchmarks.ATTRIBUTION_TEXT.lower()


# =====================================================================
# biggest_gap -- single most actionable reliable weakness
# =====================================================================
def _ds_premier(metrics, *, bucket="15k-20k", sample_size=5000, region="all",
                map_filter="all"):
    """One normalized premier_rating benchmark record with the given metrics."""
    return benchmarks._normalize_record({
        "source_name": "Leetify", "source_url": "https://leetify.com/x",
        "source_date": "2026-06-10", "bucket_type": "premier_rating",
        "bucket": bucket, "region": region, "map_filter": map_filter,
        "metrics": metrics, "sample_size": sample_size,
        "attribution": "Data: Leetify",
    })


def test_biggest_gap_picks_worst_below_metric():
    # Benchmark carries two higher-is-better metrics. Player is below on both, but
    # avg_accuracy_head is the bigger NORMALIZED gap -> that one is returned.
    ds = [_ds_premier({"avg_accuracy_head": 28.0, "avg_spray_accuracy": 26.0})]
    player = {"avg_accuracy_head": 18.0, "avg_spray_accuracy": 24.0}
    out = benchmarks.biggest_gap(player, "premier_rating", "15k-20k", datasets=ds)
    assert out is not None
    assert out["metric"] == "avg_accuracy_head"
    assert out["label"] == "Head Accuracy"
    assert out["player_value"] == 18.0
    assert out["benchmark_value"] == 28.0
    assert out["delta"] == -10.0
    assert out["source_name"] == "Leetify"
    assert out["source_url"] == "https://leetify.com/x"
    assert out["attribution"] == "Data: Leetify"


def test_biggest_gap_none_when_nothing_below():
    # Player is at/above benchmark on every carried metric -> no actionable gap.
    ds = [_ds_premier({"avg_accuracy_head": 20.0})]
    out = benchmarks.biggest_gap(
        {"avg_accuracy_head": 30.0}, "premier_rating", "15k-20k", datasets=ds)
    assert out is None


def test_biggest_gap_never_returns_unavailable_metric():
    # Player has kast/adr that the dataset does NOT carry (-> unavailable). Even
    # though those would be "below" if we guessed, biggest_gap must ignore them and
    # only consider the metric the dataset really has.
    ds = [_ds_premier({"avg_accuracy_head": 28.0})]
    player = {"avg_accuracy_head": 27.5,   # ~near, not below
              "kast": 40.0, "adr": 50.0}   # would look terrible IF a benchmark existed
    out = benchmarks.biggest_gap(player, "premier_rating", "15k-20k", datasets=ds)
    # head accuracy is within ~5% near-band, kast/adr are unavailable -> nothing below
    assert out is None


def test_biggest_gap_honors_lower_is_better():
    # avg_reaction_time is lower-is-better. Player SLOWER (higher) than benchmark is
    # the weakness; a faster metric must NOT be chosen as the gap.
    ds = [_ds_premier({"avg_reaction_time": 0.50, "avg_preaim": 6.0})]
    player = {"avg_reaction_time": 0.70,   # slower -> below (worse)
              "avg_preaim": 5.0}           # smaller error -> above (better)
    out = benchmarks.biggest_gap(player, "premier_rating", "15k-20k", datasets=ds)
    assert out is not None
    assert out["metric"] == "avg_reaction_time"
    assert out["player_value"] == 0.70
    assert out["benchmark_value"] == 0.50


def test_biggest_gap_requires_sample_size():
    # A matching dataset with NO/zero sample_size is not reliable enough to coach off
    # -> None even though the player is clearly below.
    ds = [_ds_premier({"avg_accuracy_head": 28.0}, sample_size=None)]
    out = benchmarks.biggest_gap(
        {"avg_accuracy_head": 18.0}, "premier_rating", "15k-20k", datasets=ds)
    assert out is None
    ds0 = [_ds_premier({"avg_accuracy_head": 28.0}, sample_size=0)]
    out0 = benchmarks.biggest_gap(
        {"avg_accuracy_head": 18.0}, "premier_rating", "15k-20k", datasets=ds0)
    assert out0 is None


def test_biggest_gap_none_when_no_dataset():
    # No benchmark dataset at all -> compare() unavailable -> biggest_gap None.
    out = benchmarks.biggest_gap(
        {"adr": 50.0}, "premier_rating", "15k-20k", datasets=[])
    assert out is None


def test_biggest_gap_ct_t_winrate_metric():
    # biggest_gap works against a CT/T side-winrate dataset too: player below on CT.
    recs = benchmarks.parse_leetify_ct_t(
        [_ctt_row("de_mirage", "15k-20k", region="all",
                  avg_ct_win_rate=0.55, avg_t_win_rate=0.45)],
        source_url="https://leetify.com/x", source_date="2026-06-10")
    ds = [benchmarks._normalize_record(r) for r in recs]
    # player wins only 40% on CT (below 55), 46% on T (near 45)
    out = benchmarks.biggest_gap(
        {"ct_win_rate": 40.0, "t_win_rate": 46.0},
        "premier_ct_t_side_winrates", "15k-20k",
        map_filter="de_mirage", datasets=ds)
    assert out is not None
    assert out["metric"] == "ct_win_rate"
    assert out["label"] == "CT Win Rate"
    assert out["benchmark_value"] == 55.0


# ---- perf-metric comparison: the CURATED SAFE player->Leetify key gate ------
def test_perf_compare_map_contains_only_safe_pairs():
    m = benchmarks.PERF_COMPARE_MAP
    # safe pairs are present
    assert m["hes"] == "avg_he_thrown"
    assert m["smokes"] == "avg_smoke_thrown"
    assert m["flashes_thrown"] == "avg_flashbang_thrown"
    assert m["molotovs"] == "avg_molotov_thrown"
    assert m["he_dmg_per_he"] == "avg_he_foes_damage_avg"
    assert m["headshot_accuracy"] == "avg_accuracy_head"
    assert m["flashes_hit_foe_per_game"] == "avg_flashbang_hit_foe"
    # UNSAFE pairs must NEVER be bridged (would show a misleading delta)
    for unsafe in ("counter_strafe", "hs_pct", "accuracy", "spray_accuracy", "preaim", "reaction_time"):
        assert unsafe not in m, unsafe
    # and no value maps to the unsafe Leetify keys
    bad = {"avg_counter_strafing_good_ratio", "avg_spray_accuracy",
           "avg_accuracy_enemy_spotted", "avg_preaim", "avg_reaction_time"}
    assert not (set(m.values()) & bad)


def test_perf_player_vals_rekeys_safe_present_only():
    pv = benchmarks.perf_player_vals({
        "hes": 5, "headshot_accuracy": 24.5, "smokes": 3.0,
        "counter_strafe": 61.0,        # unsafe -> dropped
        "hs_pct": 55.0,                # unsafe -> dropped
        "flashes_thrown": None,        # None -> skipped (no fake)
    })
    assert pv == {"avg_he_thrown": 5.0, "avg_accuracy_head": 24.5, "avg_smoke_thrown": 3.0}
    assert benchmarks.perf_player_vals(None) == {}     # non-dict safe


def test_perf_compare_bridges_safe_leaves_unsafe_unavailable(tmp_path):
    # Dataset carries a SAFE metric (avg_he_thrown) and an UNSAFE one
    # (avg_counter_strafing_good_ratio). The player has both a bridgeable field (hes) and an
    # unbridgeable one (counter_strafe). perf_compare must compare the safe metric and leave the
    # unsafe one player-side unavailable (benchmark shown, no delta) -- never a wrong comparison.
    _write_dataset(tmp_path, [{
        "source_name": "Leetify", "source_url": "https://leetify.invalid",
        "source_date": "2026-03-01", "bucket_type": "premier_rating",
        "bucket": "15k-20k", "region": "all", "map_filter": "all",
        "metrics": {"avg_he_thrown": 6.0, "avg_counter_strafing_good_ratio": 32.0,
                    "avg_accuracy_head": 20.0},
        "attribution": "Leetify",
    }])
    res = benchmarks.perf_compare(
        {"hes": 8, "counter_strafe": 61.0, "headshot_accuracy": 24.0},
        "premier_rating", "15k-20k")
    assert res["available"] is True
    by = {m["metric"]: m for m in res["metrics"]}
    # SAFE: bridged + compared
    assert by["avg_he_thrown"]["player_value"] == 8.0
    assert by["avg_he_thrown"]["benchmark_value"] == 6.0
    assert by["avg_he_thrown"]["status"] in ("above", "near", "below")
    assert by["avg_accuracy_head"]["player_value"] == 24.0
    # UNSAFE: benchmark present but player side stays unavailable (NOT a 61-vs-32 delta)
    assert by["avg_counter_strafing_good_ratio"]["benchmark_value"] == 32.0
    assert by["avg_counter_strafing_good_ratio"]["player_value"] is None
    assert by["avg_counter_strafing_good_ratio"]["delta"] is None
    assert by["avg_counter_strafing_good_ratio"]["status"] == "unavailable"
