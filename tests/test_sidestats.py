"""Tests for sidestats.py -- per-map CT/T round-winrate derivation.

Synthetic analytics dicts with KNOWN per-round side wins. We construct rounds so that the
team-under-test (start_side) wins an exact number of CT and T rounds, then assert the derived
winrates. Side-swap rules under test:
  * regulation: R1-12 = start_side, R13-24 = flipped (MR12)
  * overtime:  first OT half keeps the regulation-2nd-half side, then swaps every 3 (MR3)
All validated against the real cached corpus in the module's investigation.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sidestats  # noqa: E402


# --------------------------------------------------------------------------- helpers

def _make_analytics(round_winners, *, teamA_start="CT", map_name=None,
                    teamA_players=("Alice",), teamB_players=("Bob",),
                    players=None, with_team_coaching=True):
    """Build a minimal analytics dict.

    round_winners: list of "CT"/"T"/"" -- winner side per round, in order (round num = i+1).
    teamA_start:   start_side of team A (team B is the opposite). Team A is id 'A'.
    players:       optional explicit players list ([{steamid,name,...}]); else derived from
                   the team rosters with synthetic steamids.
    """
    rounds = [{"num": i + 1, "winner": w} for i, w in enumerate(round_winners)]
    a = {"rounds": rounds}
    if map_name is not None:
        a["map"] = map_name
    if players is None:
        players = []
        sid = 1000
        for nm in list(teamA_players) + list(teamB_players):
            players.append({"steamid": str(sid), "name": nm})
            sid += 1
    a["players"] = players
    if with_team_coaching:
        teamB_start = "T" if teamA_start == "CT" else "CT"
        a["team_coaching"] = {"teams": [
            {"id": "A", "start_side": teamA_start, "players": list(teamA_players)},
            {"id": "B", "start_side": teamB_start, "players": list(teamB_players)},
        ]}
    return a


def _regulation_winners(teamA_start, ct_wins_for_A, t_wins_for_A):
    """24-round regulation winners giving team A exactly `ct_wins_for_A` CT wins (rounds where
    A is on CT) and `t_wins_for_A` T wins. A is on `teamA_start` for R1-12, flipped R13-24."""
    flip = "T" if teamA_start == "CT" else "CT"
    winners = []
    # First half: A on teamA_start (12 rounds)
    for i in range(12):
        if teamA_start == "CT":
            # A on CT: an "A CT win" means winner == CT
            winners.append("CT" if i < ct_wins_for_A else "T")
        else:
            # A on T: an "A T win" means winner == T
            winners.append("T" if i < t_wins_for_A else "CT")
    # Second half: A on flip (12 rounds)
    for i in range(12):
        if flip == "CT":
            winners.append("CT" if i < ct_wins_for_A else "T")
        else:
            winners.append("T" if i < t_wins_for_A else "CT")
    return winners


# --------------------------------------------------------------------------- core derivation

def test_known_ct_and_t_winrates():
    """Team wins 9 CT of 12 and 4 T of 12 -> ct_wr 75.0, t_wr 33.3 (the brief's example)."""
    # Team A starts CT. Put its 12 CT rounds in the first half, 12 T rounds in the second half.
    winners = _regulation_winners("CT", ct_wins_for_A=9, t_wins_for_A=4)
    a = _make_analytics(winners, teamA_start="CT", map_name="de_dust2")
    res = sidestats.match_side_winrates(a, steamid="1000")  # Alice = team A
    assert res is not None
    assert res["map"] == "de_dust2"
    assert res["ct_rounds"] == 12 and res["ct_won"] == 9
    assert res["t_rounds"] == 12 and res["t_won"] == 4
    # round-trip through aggregate to check the winrate math
    agg = sidestats.aggregate_side_winrates([a], steamid="1000")
    assert agg["de_dust2"]["ct_wr"] == 75.0
    assert agg["de_dust2"]["t_wr"] == 33.3  # 4/12 = 33.333 -> 1dp


def test_opponent_team_is_mirror():
    """The team that started T sees the swapped tally (their CT rounds are A's T rounds)."""
    winners = _regulation_winners("CT", ct_wins_for_A=9, t_wins_for_A=4)
    a = _make_analytics(winners, teamA_start="CT")
    # Bob is team B (started T). Of the 12 rounds B spends on CT, those are the rounds where the
    # *winner* is CT but A was on T -> 12 - (A's T wins as a T-winner) ... compute directly.
    res_b = sidestats.match_side_winrates(a, steamid="1001")
    assert res_b is not None
    assert res_b["ct_rounds"] == 12 and res_b["t_rounds"] == 12
    # Team B CT rounds = rounds 13-24 (when A is on T). Winner==CT there = 12 - t_wins_for_A=4 -> 8.
    assert res_b["ct_won"] == 8
    # Team B T rounds = rounds 1-12 (A on CT). Winner==T there = 12 - ct_wins_for_A=9 -> 3.
    assert res_b["t_won"] == 3


def test_default_team_when_no_steamid():
    """steamid=None falls back to the team that started CT (team A)."""
    winners = _regulation_winners("CT", ct_wins_for_A=10, t_wins_for_A=2)
    a = _make_analytics(winners, teamA_start="CT")
    res = sidestats.match_side_winrates(a)  # no steamid
    assert res is not None
    assert res["ct_won"] == 10 and res["t_won"] == 2


def test_default_team_picks_ct_starter_regardless_of_order():
    """Even if the T-starting team is listed first, None should pick the CT starter."""
    winners = _regulation_winners("T", ct_wins_for_A=5, t_wins_for_A=7)
    # Here team A STARTS T; build with teamA_start='T' so team B starts CT.
    a = _make_analytics(winners, teamA_start="T",
                        teamA_players=("Tee",), teamB_players=("Cee",))
    res = sidestats.match_side_winrates(a)  # None -> should pick team B (started CT)
    assert res is not None
    # Team B started CT: its CT rounds are R1-12 (when A, the T-starter, is on T).
    # winner==CT in R1-12 = 12 - t_wins_for_A(7) = 5.
    assert res["ct_rounds"] == 12 and res["t_rounds"] == 12
    assert res["ct_won"] == 5


# --------------------------------------------------------------------------- MR12 half handling

def test_mr12_half_swap():
    """A team that wins ONLY its first 12 rounds (all CT, since it started CT) must show those
    as CT wins and zero T wins -- proving R1-12 map to start_side and R13-24 to the flip."""
    teamA_start = "CT"
    # First 12 rounds: CT wins (A on CT). Last 12 rounds: CT wins too, but A is on T there,
    # so those count as A's *opponent* winning -> A gets 12 CT wins, 0 T wins.
    winners = ["CT"] * 24
    a = _make_analytics(winners, teamA_start=teamA_start, map_name="de_inferno")
    res = sidestats.match_side_winrates(a, steamid="1000")
    assert res["ct_rounds"] == 12 and res["t_rounds"] == 12
    assert res["ct_won"] == 12   # all R1-12 (A on CT) won
    assert res["t_won"] == 0     # R13-24 A on T but winner was CT -> not A's wins


def test_side_for_round_schedule():
    """Directly exercise the swap schedule incl. overtime (MR3)."""
    f = sidestats.side_for_round
    # regulation
    assert f("CT", 1) == "CT"
    assert f("CT", 12) == "CT"
    assert f("CT", 13) == "T"
    assert f("CT", 24) == "T"
    # overtime block 0 (R25-27) keeps regulation-2nd-half side (T for a CT starter)
    assert f("CT", 25) == "T"
    assert f("CT", 27) == "T"
    # overtime block 1 (R28-30) swaps back to start_side
    assert f("CT", 28) == "CT"
    assert f("CT", 30) == "CT"
    # second OT (R31-36): block 2 -> flip again, block 3 -> start
    assert f("CT", 31) == "T"
    assert f("CT", 34) == "CT"
    # T starter is the mirror
    assert f("T", 1) == "T"
    assert f("T", 13) == "CT"
    assert f("T", 25) == "CT"
    assert f("T", 28) == "T"
    # bad inputs
    assert f("CT", 0) is None
    assert f("CT", -3) is None
    assert f("xx", 5) is None
    assert f("CT", None) is None


def test_overtime_match_totals_reconcile():
    """A 30-round (1 OT) match: derived ct_won + t_won must equal the team's total round wins.

    Mirrors the real-demo reconciliation: regulation 12+12 plus a 3+3 overtime, with the OT
    first half on the regulation-2nd-half side.
    """
    teamA_start = "CT"
    # Construct explicit winners so we know team A's wins. A's side per round via the schedule.
    # Let team A win: R1-6 (CT), R13-18 (T), R25-27 all (T side), R28 (CT). Everything else lost.
    winners = []
    a_win_rounds = set([1, 2, 3, 4, 5, 6] + [13, 14, 15, 16, 17, 18] + [25, 26, 27] + [28])
    for rnum in range(1, 31):
        side = sidestats.side_for_round(teamA_start, rnum)
        if rnum in a_win_rounds:
            winners.append(side)            # team A wins -> winner == A's side
        else:
            winners.append("T" if side == "CT" else "CT")  # opponent wins
    a = _make_analytics(winners, teamA_start=teamA_start, map_name="de_nuke")
    res = sidestats.match_side_winrates(a, steamid="1000")
    assert res is not None
    assert res["ct_rounds"] + res["t_rounds"] == 30
    assert res["ct_rounds"] == 15 and res["t_rounds"] == 15  # 12 reg + 3 OT each side
    assert res["ct_won"] + res["t_won"] == len(a_win_rounds)  # 16 total wins reconcile


# --------------------------------------------------------------------------- aggregation

def test_aggregate_sums_across_matches():
    """Two matches on the same map sum CT/T rounds+wins and recompute winrate."""
    w1 = _regulation_winners("CT", ct_wins_for_A=9, t_wins_for_A=3)
    w2 = _regulation_winners("CT", ct_wins_for_A=6, t_wins_for_A=6)
    a1 = _make_analytics(w1, teamA_start="CT", map_name="de_mirage")
    a2 = _make_analytics(w2, teamA_start="CT", map_name="de_mirage")
    agg = sidestats.aggregate_side_winrates([a1, a2], steamid="1000")
    assert set(agg.keys()) == {"de_mirage"}
    d = agg["de_mirage"]
    assert d["ct_rounds"] == 24 and d["ct_won"] == 15   # 9 + 6
    assert d["t_rounds"] == 24 and d["t_won"] == 9      # 3 + 6
    assert d["ct_wr"] == 62.5    # 15/24
    assert d["t_wr"] == 37.5     # 9/24
    assert d["games"] == 2


def test_aggregate_groups_by_map():
    """Different maps stay in separate buckets."""
    a1 = _make_analytics(_regulation_winners("CT", 8, 4), map_name="de_mirage")
    a2 = _make_analytics(_regulation_winners("CT", 7, 5), map_name="de_dust2")
    agg = sidestats.aggregate_side_winrates([a1, a2], steamid="1000")
    assert set(agg.keys()) == {"de_mirage", "de_dust2"}
    assert agg["de_mirage"]["ct_won"] == 8
    assert agg["de_dust2"]["ct_won"] == 7


def test_aggregate_map_from_tuple_override():
    """When the analytics dict lacks 'map', a (analytics, map_name) tuple supplies it."""
    a = _make_analytics(_regulation_winners("CT", 9, 4))  # no map key
    agg = sidestats.aggregate_side_winrates([(a, "de_overpass")], steamid="1000")
    assert "de_overpass" in agg
    assert agg["de_overpass"]["ct_won"] == 9


def test_small_sample_flag_true_for_thin_data():
    """One short match -> small_sample True (under both round and game thresholds)."""
    a = _make_analytics(_regulation_winners("CT", 6, 6), map_name="de_vertigo")
    agg = sidestats.aggregate_side_winrates([a], steamid="1000")
    d = agg["de_vertigo"]
    assert d["games"] == 1
    assert (d["ct_rounds"] + d["t_rounds"]) == 24  # < 50 rounds
    assert d["small_sample"] is True


def test_small_sample_flag_false_with_enough_data():
    """3 full matches (72 rounds) clears both thresholds -> small_sample False."""
    mats = [_make_analytics(_regulation_winners("CT", 8, 5), map_name="de_anubis")
            for _ in range(3)]
    agg = sidestats.aggregate_side_winrates(mats, steamid="1000")
    d = agg["de_anubis"]
    assert d["games"] == 3
    assert (d["ct_rounds"] + d["t_rounds"]) == 72  # >= 50
    assert d["small_sample"] is False


def test_missing_side_data_returns_none_and_is_skipped():
    """A match without team_coaching -> match_side_winrates None; aggregate skips it (not 0)."""
    good = _make_analytics(_regulation_winners("CT", 9, 4), map_name="de_train")
    bad = _make_analytics(_regulation_winners("CT", 5, 5), map_name="de_train",
                          with_team_coaching=False)
    assert sidestats.match_side_winrates(bad, steamid="1000") is None
    agg = sidestats.aggregate_side_winrates([good, bad], steamid="1000")
    # Only the good match contributes -> games==1, NOT 2, and rounds reflect only `good`.
    assert agg["de_train"]["games"] == 1
    assert agg["de_train"]["ct_won"] == 9 and agg["de_train"]["t_won"] == 4
    assert agg["de_train"]["ct_rounds"] == 12  # bad match's rounds not added


def test_steamid_not_in_match_returns_none_and_skipped():
    """A steamid that isn't in a match's roster -> that match yields no side data."""
    a1 = _make_analytics(_regulation_winners("CT", 9, 4), map_name="de_cache",
                         teamA_players=("Alice",), teamB_players=("Bob",))  # sids 1000,1001
    a2 = _make_analytics(_regulation_winners("CT", 7, 5), map_name="de_cache",
                         teamA_players=("Carol",), teamB_players=("Dave",))  # sids 1000,1001 again
    # Aggregate for a steamid present only in... actually both reuse 1000/1001. Use an absent one.
    assert sidestats.match_side_winrates(a1, steamid="999999") is None
    agg = sidestats.aggregate_side_winrates([a1, a2], steamid="999999")
    assert agg == {}  # never matched -> nothing counted


def test_map_fallback_unknown():
    """No map anywhere -> 'unknown' bucket, still aggregated."""
    a = _make_analytics(_regulation_winners("CT", 9, 4))  # no map, no override
    res = sidestats.match_side_winrates(a, steamid="1000")
    assert res["map"] == "unknown"
    agg = sidestats.aggregate_side_winrates([a], steamid="1000")
    assert "unknown" in agg


def test_map_override_beats_analytics_map():
    """Explicit map_name wins over analytics['map']."""
    a = _make_analytics(_regulation_winners("CT", 9, 4), map_name="de_dust2")
    res = sidestats.match_side_winrates(a, steamid="1000", map_name="de_mirage")
    assert res["map"] == "de_mirage"


# --------------------------------------------------------------------------- robustness

def test_never_throws_on_empty_and_partial():
    """{} / None / partial shapes -> None or {} (the never-throw contract)."""
    assert sidestats.match_side_winrates({}) is None
    assert sidestats.match_side_winrates(None) is None
    assert sidestats.match_side_winrates({"rounds": []}) is None
    assert sidestats.match_side_winrates({"rounds": [{"num": 1, "winner": "CT"}]}) is None  # no teams
    assert sidestats.match_side_winrates({"team_coaching": {"teams": []}}) is None
    assert sidestats.match_side_winrates("not a dict") is None
    assert sidestats.match_side_winrates([1, 2, 3]) is None
    assert sidestats.aggregate_side_winrates([]) == {}
    assert sidestats.aggregate_side_winrates(None) == {}
    assert sidestats.aggregate_side_winrates([{}, None, "junk"]) == {}


def test_never_throws_on_malformed_rounds_and_teams():
    """Garbage inside rounds/teams is skipped, not fatal."""
    a = {
        "map": "de_dust2",
        "players": [{"steamid": "1000", "name": "Alice"}],
        "team_coaching": {"teams": [
            {"id": "A", "start_side": "CT", "players": ["Alice"]},
            {"id": "B", "start_side": "T", "players": ["Bob"]},
        ]},
        "rounds": [
            {"num": 1, "winner": "CT"},
            {"num": 2, "winner": None},        # null winner -> counts as a round, no win
            {"num": 3},                        # missing winner
            "not a dict",                      # junk round
            {"num": "x", "winner": "CT"},      # bad round num -> skipped
            {"num": 4, "winner": "T"},
        ],
    }
    res = sidestats.match_side_winrates(a, steamid="1000")
    assert res is not None
    # Rounds 1-4 are all first-half (A on CT). ct_rounds counts valid-num rounds: 1,2,3,4 = 4.
    assert res["ct_rounds"] == 4
    assert res["ct_won"] == 1   # only R1 had winner CT (R4 winner T doesn't help A on CT)
    assert res["t_rounds"] == 0


def test_zero_round_map_omitted():
    """If a match somehow yields no usable rounds it's skipped, so no empty-map bucket appears."""
    # match_side_winrates returns None here, so aggregate omits it entirely.
    bad = {"map": "de_dust2", "rounds": [{"num": 1, "winner": "CT"}]}  # no team_coaching
    agg = sidestats.aggregate_side_winrates([bad], steamid="1000")
    assert agg == {}


def test_no_division_by_zero_when_only_one_side_played():
    """A 1-round 'match' (only CT side seen) -> t_rounds 0, t_wr 0.0, no ZeroDivisionError."""
    a = _make_analytics(["CT"], teamA_start="CT", map_name="de_short")
    res = sidestats.match_side_winrates(a, steamid="1000")
    assert res is not None
    assert res["ct_rounds"] == 1 and res["t_rounds"] == 0
    agg = sidestats.aggregate_side_winrates([a], steamid="1000")
    d = agg["de_short"]
    assert d["t_wr"] == 0.0 and d["ct_wr"] == 100.0
