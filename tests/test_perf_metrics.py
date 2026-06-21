"""Tests for the Leetify-comparable performance metrics added to analytics:
HE-damage-per-HE split, TRUE headshot accuracy (hitgroup), overall accuracy, ungated flash per-game,
and the per-metric data-quality flags. These cover the parts the no-fake-data rule depends on:
HE damage excludes molotov/fire, headshot ACCURACY != headshot-KILL %, and missing data -> unavailable
(never a fake 0). See PERF_METRICS_FEASIBILITY.md."""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analytics as an   # noqa: E402


# ---- _hitgroup -------------------------------------------------------------
def test_hitgroup_parsing():
    assert an._hitgroup(1) == 1
    assert an._hitgroup("1") == 1
    assert an._hitgroup(0) == 0
    assert an._hitgroup(None) is None          # absent -> headshot accuracy stays unavailable
    assert an._hitgroup(float("nan")) is None
    assert an._hitgroup("head") is None


# ---- _accumulate_hit (HE split + bullet/head hit counting) -----------------
def _hit(weapon, hg=None):
    return {"util": an.is_util_damage_weapon(weapon), "he": an.is_he_damage_weapon(weapon),
            "fire": an.is_fire_damage_weapon(weapon), "hg": hg}


def test_accumulate_hit_he_excludes_fire():
    acc = defaultdict(float)
    an._accumulate_hit(acc, _hit("hegrenade"), 50)
    an._accumulate_hit(acc, _hit("hegrenade"), 30)
    an._accumulate_hit(acc, _hit("molotov"), 40)      # fire: counts to molly_dmg + util, NOT he_dmg
    an._accumulate_hit(acc, _hit("inferno"), 20)
    assert acc["he_dmg"] == 80                          # 50 + 30 only
    assert acc["molly_dmg"] == 60                       # 40 + 20
    assert acc["util_dmg"] == 140                       # all utility damage combined (back-compat udr)
    assert acc["bullet_hits"] == 0                      # utility is not a bullet hit


def test_accumulate_hit_bullet_and_head_counts():
    acc = defaultdict(float)
    an._accumulate_hit(acc, _hit("ak47", hg=1), 30)    # head
    an._accumulate_hit(acc, _hit("ak47", hg=2), 25)    # chest
    an._accumulate_hit(acc, _hit("ak47", hg=None), 18) # unknown hitgroup -> hit but not head
    assert acc["bullet_hits"] == 3
    assert acc["head_hits"] == 1                        # only the hitgroup==1 hit
    assert acc["he_dmg"] == 0 and acc["util_dmg"] == 0


# ---- _attach_aim: overall accuracy = enemy hits / shots fired ---------------
def _shots(player, n):
    return [{"type": "shot", "player": player} for _ in range(n)]


def test_accuracy_from_shots_and_hits():
    players = [{"index": 0, "bullet_hits": 20}, {"index": 1, "bullet_hits": 5}]
    ev = _shots(0, 50) + _shots(1, 10)
    an._attach_aim(players, {"events": ev})
    assert players[0]["shots_fired"] == 50
    assert players[0]["accuracy"] == 40.0              # 20/50
    # player 1: only 10 shots (< MIN_SHOTS_FOR_ACC) -> unavailable, never a fake number
    assert players[1]["shots_fired"] == 10
    assert players[1]["accuracy"] is None


def test_accuracy_unavailable_with_no_shots():
    players = [{"index": 0, "bullet_hits": 0}]
    an._attach_aim(players, {"events": []})
    assert players[0]["accuracy"] is None and players[0]["shots_fired"] == 0


# ---- _attach_perf_quality: exact / unavailable + reasons -------------------
def test_perf_quality_flags():
    players = [{
        "hes": 3, "flashes_thrown": 5, "smokes": 4, "molotovs": 1,
        "bullet_hits": 40, "shots_fired": 120, "flashes_hit_foe_per_game": 6,
    }]
    an._attach_perf_quality(players, has_blind=True, hitgroup_seen=True)
    q = players[0]["perf_quality"]
    assert q["he_thrown"]["status"] == "exact" and q["he_thrown"]["sample_size"] == 3
    assert q["he_foes_damage_avg"]["status"] == "exact"
    assert q["accuracy_head"]["status"] == "exact" and q["accuracy_head"]["sample_size"] == 40
    assert q["accuracy"]["status"] == "exact"
    assert q["flashbang_hit_foe"]["status"] == "exact"


def test_perf_quality_unavailable_paths():
    players = [{"hes": 0, "bullet_hits": 5, "shots_fired": 10}]
    # no hitgroup data, too few hits/shots, and no player_blind in the demo
    an._attach_perf_quality(players, has_blind=False, hitgroup_seen=False)
    q = players[0]["perf_quality"]
    assert q["he_foes_damage_avg"]["status"] == "unavailable"        # 0 HEs thrown
    assert q["accuracy_head"]["status"] == "unavailable"             # no hitgroup
    assert "hitgroup" in q["accuracy_head"]["reason"]
    assert q["accuracy"]["status"] == "unavailable"                  # < MIN_SHOTS_FOR_ACC
    assert q["flashbang_hit_foe"]["status"] == "unavailable"         # no player_blind
    assert "player_blind" in q["flashbang_hit_foe"]["reason"]


def test_perf_quality_low_hits_unavailable():
    players = [{"hes": 2, "bullet_hits": an.MIN_HITS_FOR_HS_ACC - 1, "shots_fired": 200}]
    an._attach_perf_quality(players, has_blind=True, hitgroup_seen=True)
    q = players[0]["perf_quality"]
    assert q["accuracy_head"]["status"] == "unavailable"             # below min-sample
    assert q["accuracy"]["status"] == "exact"                        # plenty of shots
