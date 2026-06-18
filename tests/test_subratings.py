"""Tests for subratings.py (Aim/Utility/Positioning pillars + bands)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import subratings   # noqa: E402


def _solid(**over):
    """A 'solid' (~B) baseline player; override fields per test."""
    p = {"steamid": "A", "name": "t", "rounds_played": 24, "kills": 22, "deaths": 16,
         "adr": 88.0, "kast": 74.0, "kpr": 0.92, "dpr": 0.67, "hs_pct": 58.0,
         "counter_strafe": 61.0, "open_k": 6, "open_d": 4, "open_wr": 60.0,
         "traded_pct": 31.0, "udr": 9.4, "util_pr": 3.8, "enemy_flashed": 14,
         "team_flashed": 2}
    p.update(over)
    return p


def test_pillars_present_and_banded():
    sr = subratings.compute_subratings([_solid()])["A"]
    for pillar in ("aim", "utility", "positioning"):
        assert pillar in sr
        assert 0 <= sr[pillar]["score"] <= 100
        assert sr[pillar]["band"] in ("S", "A", "B", "C", "D", "F")
    # a solid all-rounder should land around B/A on aim
    assert sr["aim"]["band"] in ("B", "A")


def test_band_thresholds():
    assert subratings.band_for(95)[0] == "S"
    assert subratings.band_for(70)[0] == "B"      # 'good' anchor -> 70 -> B/Solid
    assert subratings.band_for(52)[0] == "C"
    assert subratings.band_for(10)[0] == "F"
    assert subratings.band_for(None)[0] == "--"


def test_higher_is_better_monotonic():
    lo = subratings.compute_subratings([_solid(adr=55, kpr=0.50, hs_pct=25,
                                               counter_strafe=30, open_wr=40)])["A"]
    hi = subratings.compute_subratings([_solid(adr=110, kpr=1.0, hs_pct=72,
                                               counter_strafe=82, open_wr=70)])["A"]
    assert hi["aim"]["score"] > lo["aim"]["score"]


def test_lower_is_better_dpr():
    """Fewer deaths/round must score HIGHER on positioning (anchors decrease)."""
    survives = subratings.compute_subratings([_solid(dpr=0.50, open_d=1)])["A"]
    dies = subratings.compute_subratings([_solid(dpr=0.90, open_d=8, open_k=2)])["A"]
    assert survives["positioning"]["score"] > dies["positioning"]["score"]


def test_missing_counter_strafe_dropped():
    """Old demos without per-shot velocity: aim still computes, sans counter-strafe."""
    p = _solid()
    p.pop("counter_strafe")
    sr = subratings.compute_subratings([p])["A"]
    keys = [m["key"] for m in sr["aim"]["metrics"]]
    assert "counter_strafe" not in keys
    assert sr["aim"]["score"] is not None and len(keys) == 4


def test_thin_sample_drops_metrics():
    """Few kills/opening duels/deaths -> those metrics drop out (no misleading 0%)."""
    p = _solid(kills=2, deaths=2, open_k=0, open_d=1)
    sr = subratings.compute_subratings([p])["A"]
    aim_keys = [m["key"] for m in sr["aim"]["metrics"]]
    pos_keys = [m["key"] for m in sr["positioning"]["metrics"]]
    assert "hs_pct" not in aim_keys        # kills < 4
    assert "open_wr" not in aim_keys       # open_total < 4
    assert "traded_pct" not in pos_keys    # deaths < 4
    assert sr["utility"]["score"] is not None   # utility doesn't depend on those


def test_team_flash_penalty():
    clean = subratings.compute_subratings([_solid(team_flashed=0)])["A"]
    spammer = subratings.compute_subratings([_solid(team_flashed=20)])["A"]
    assert spammer["utility"]["score"] < clean["utility"]["score"]


def test_empty_roster():
    assert subratings.compute_subratings([]) == {}


def test_confidence_low_on_short_match():
    sr = subratings.compute_subratings([_solid(rounds_played=4)])["A"]
    assert sr["aim"]["confidence"] == "low"
    sr2 = subratings.compute_subratings([_solid(rounds_played=24)])["A"]
    assert sr2["aim"]["confidence"] == "med"
