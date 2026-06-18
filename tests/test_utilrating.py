"""Tests for utilrating.py (#50 two-tier util rating: volume x quality)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utilrating   # noqa: E402


def _p(**over):
    p = {"rounds_played": 24, "util_pr": 3.5, "udr": 8.0, "flashes_thrown": 8,
         "enemy_flashed": 7, "avg_blind": 1.1, "team_flashed": 0}
    p.update(over)
    return p


def test_high_volume_high_quality():
    r = utilrating.compute_util_rating(_p(util_pr=5, udr=14, flashes_thrown=10, enemy_flashed=12, avg_blind=1.5))
    assert r["volume"]["score"] >= 60 and r["quality"]["score"] >= 60
    assert r["verdict"].startswith("Impactful")


def test_high_volume_low_quality():
    r = utilrating.compute_util_rating(_p(util_pr=5, udr=2, flashes_thrown=10, enemy_flashed=2, avg_blind=0.5))
    assert r["volume"]["score"] >= 60 and r["quality"]["score"] < 60
    assert "low impact" in r["verdict"]


def test_low_volume_high_quality():
    r = utilrating.compute_util_rating(_p(util_pr=2, udr=14, flashes_thrown=10, enemy_flashed=12, avg_blind=1.5))
    assert r["volume"]["score"] < 60 and r["quality"]["score"] >= 60
    assert r["verdict"].startswith("Efficient")


def test_low_both():
    r = utilrating.compute_util_rating(_p(util_pr=1, udr=1, flashes_thrown=0, enemy_flashed=0))
    assert r["volume"]["score"] < 60 and r["quality"]["score"] < 60
    assert "Underutilizing" in r["verdict"]


def test_thin_flash_sample_uses_damage_only():
    r = utilrating.compute_util_rating(_p(flashes_thrown=1, enemy_flashed=1))
    assert r["flash_conv"] is None        # below MIN_FLASHES -> no flash component


def test_team_flash_penalty_lowers_quality():
    clean = utilrating.compute_util_rating(_p(udr=10, flashes_thrown=10, enemy_flashed=10, team_flashed=0))
    spammer = utilrating.compute_util_rating(_p(udr=10, flashes_thrown=10, enemy_flashed=10, team_flashed=24))
    assert spammer["quality"]["score"] < clean["quality"]["score"]
    assert spammer["team_flash_penalty"] > 0


def test_util_pr_fallback_from_counts():
    p = {"rounds_played": 6, "smokes": 2, "flashes_thrown": 2, "hes": 1, "molotovs": 1, "udr": 5}
    r = utilrating.compute_util_rating(p)
    assert r["util_pr"] == 1.0            # (2+2+1+1)/6


def test_attach_sets_field():
    players = [_p(), _p(util_pr=1, udr=1, flashes_thrown=0)]
    utilrating.attach(players)
    assert all("util_rating" in p for p in players)
    assert players[0]["util_rating"]["volume"]["band"] in ("S", "A", "B", "C", "D", "F")


def test_flash_unavailable_not_penalized():
    # demo with no player_blind data: ft high but ef=0 for everyone -> 'unavailable', not 'whiffed'
    p = _p(udr=8, flashes_thrown=20, enemy_flashed=0, avg_blind=0, team_flashed=0)
    avail = utilrating.compute_util_rating(p, has_flash=True)    # 0-conv flash comp drags quality down
    unavail = utilrating.compute_util_rating(p, has_flash=False)  # quality on damage alone
    assert unavail["flash_data"] is False and unavail["flash_conv"] is None
    assert unavail["quality"]["score"] > avail["quality"]["score"]


def test_attach_infers_flash_availability():
    no_flash = [_p(enemy_flashed=0), _p(enemy_flashed=0)]
    utilrating.attach(no_flash)
    assert all(p["util_rating"]["flash_data"] is False for p in no_flash)
    some_flash = [_p(enemy_flashed=0), _p(enemy_flashed=5)]
    utilrating.attach(some_flash)
    assert all(p["util_rating"]["flash_data"] is True for p in some_flash)
