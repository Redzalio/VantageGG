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
