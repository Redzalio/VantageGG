"""Tests for the pure round/buy helpers (no demo parsing needed)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from roundlib import (classify_buy, is_util_damage_weapon, kill_reward,   # noqa: E402
                      norm_weapon, pair_rounds, winner_str)


def test_winner_str_variants():
    assert winner_str("CT") == "CT"
    assert winner_str("ct") == "CT"
    assert winner_str(3) == "CT"
    assert winner_str("3") == "CT"
    assert winner_str("TERRORIST") == "T"
    assert winner_str("T") == "T"
    assert winner_str(2) == "T"
    assert winner_str(None) == ""
    assert winner_str("") == ""
    assert winner_str("draw") == ""


def test_pair_rounds_drops_warmup_and_null_ends():
    # warmup starts (50,120), a restart start (9000) with no following valid end,
    # one null-winner end (800) and one empty end (9500) -> only 2 real rounds.
    starts = [50, 120, 1000, 5200, 9000]
    freezes = [200, 1100, 5300, 9100]
    ends = [{"tick": 800, "winner": None},
            {"tick": 5000, "winner": "CT", "reason": "elim"},
            {"tick": 8800, "winner": 2, "reason": "bomb"},
            {"tick": 9500, "winner": ""}]
    r = pair_rounds(starts, freezes, ends)
    assert len(r) == 2
    assert r[0]["num"] == 1 and r[0]["start"] == 1000 and r[0]["winner"] == "CT"
    assert r[0]["freeze_end"] == 5300 or r[0]["freeze_end"] >= r[0]["start"]
    assert r[1]["num"] == 2 and r[1]["start"] == 5200 and r[1]["winner"] == "T"


def test_pair_rounds_anchors_start_to_nearest_prior():
    # two starts before one end -> the LATER start wins (restart mid-freeze)
    r = pair_rounds([100, 300], [350], [{"tick": 2000, "winner": "T"}])
    assert len(r) == 1
    assert r[0]["start"] == 300


def test_pair_rounds_fallback_when_no_winners():
    # no winner-bearing ends -> best-effort index pairing keeps the rounds visible
    r = pair_rounds([100, 5000], [150, 5050],
                    [{"tick": 4000, "winner": None}, {"tick": 9000, "winner": None}])
    assert len(r) == 2


def test_classify_buy_buckets():
    # full = rifle + armor + utility, not a bare rifle. Neutral/T full floor = $3900.
    assert classify_buy(0) == "eco"
    assert classify_buy(900) == "eco"
    assert classify_buy(1000) == "light"      # upgraded pistols / light armor
    assert classify_buy(2399) == "light"
    assert classify_buy(2400) == "force"      # a rifle but incomplete kit
    assert classify_buy(2500) == "force"      # a bare rifle is NOT a full buy
    assert classify_buy(3899) == "force"      # just under the full floor
    assert classify_buy(3900) == "full"       # real T/neutral full buy
    assert classify_buy(6000) == "full"
    assert classify_buy(5000, is_pistol=True) == "pistol"
    assert classify_buy(None) == "unknown"


def test_classify_buy_side_aware():
    # CT full costs more (kits + costlier util): a lone M4 + armor (~$3900, no kit/util) is a
    # FORCE on CT but a FULL on T. CT full floor = $4300.
    assert classify_buy(3900, side="T") == "full"
    assert classify_buy(3900, side="CT") == "force"
    assert classify_buy(4299, side="CT") == "force"
    assert classify_buy(4300, side="CT") == "full"
    # pistol / unknown overrides ignore side
    assert classify_buy(9999, is_pistol=True, side="CT") == "pistol"
    assert classify_buy(None, side="T") == "unknown"


def test_kill_reward():
    # CS2 kill rewards (verified 2026-06-17, docs/CS2_ECONOMY_REFERENCE.md)
    assert kill_reward("weapon_knife") == 1500
    assert kill_reward("bayonet") == 1500
    assert kill_reward("weapon_taser") == 100        # Zeus
    assert kill_reward("awp") == 100
    assert kill_reward("ak47") == 300
    assert kill_reward("weapon_m4a1_silencer") == 300
    assert kill_reward("deagle") == 300
    assert kill_reward("cz75a") == 300               # CZ75 is $300 in CS2 (was $100)
    assert kill_reward("mp9") == 600                 # SMG
    assert kill_reward("weapon_mac10") == 600
    assert kill_reward("mp5sd") == 600
    assert kill_reward("p90") == 300                 # SMG exception
    assert kill_reward("nova") == 900                # shotgun
    assert kill_reward("mag7") == 900
    assert kill_reward("xm1014") == 600              # shotgun exception
    assert kill_reward("g3sg1") == 300               # autosniper = rifle reward
    assert kill_reward("ssg08") == 300               # scout = rifle reward, NOT AWP's $100
    assert kill_reward("hegrenade") == 300
    assert kill_reward("molotov") == 300
    assert kill_reward("") == 300                     # default


def test_norm_weapon():
    assert norm_weapon("weapon_ak47") == "ak47"
    assert norm_weapon("AK47") == "ak47"
    assert norm_weapon("hegrenade") == "hegrenade"
    assert norm_weapon(None) == ""
    assert norm_weapon("  weapon_M4A1  ") == "m4a1"


def test_is_util_damage_weapon():
    # the real bug: only the bare names were matched; variants silently undercounted UDR
    for w in ("hegrenade", "weapon_hegrenade", "molotov", "weapon_molotov",
              "inferno", "incgrenade", "inc_grenade", "incendiary", "weapon_incgrenade"):
        assert is_util_damage_weapon(w) is True, w
    for w in ("ak47", "weapon_ak47", "knife", "awp", "deagle", "", None):
        assert is_util_damage_weapon(w) is False, w
