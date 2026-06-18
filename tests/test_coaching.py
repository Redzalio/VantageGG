"""Tests for the P3 coaching layer: win-prob, clutches, round-swing (synthetic)."""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analytics as an   # noqa: E402


def test_winprob_man_advantage_and_plant():
    assert abs(an._winprob_ct(5, 5, False) - 0.5) < 1e-9      # even
    assert an._winprob_ct(5, 4, False) > 0.5                  # CT up a man
    assert an._winprob_ct(4, 5, False) < 0.5                  # CT down a man
    assert an._winprob_ct(1, 0, False) == 1.0                 # enemy wiped
    assert an._winprob_ct(0, 1, False) == 0.0
    # equal players but bomb down -> T favoured
    assert an._winprob_ct(3, 3, True) < an._winprob_ct(3, 3, False)


def _clutch_fixture():
    # CT: a,b ; T: c,d. b dies -> a is 1v2 -> a kills c then d -> CT win.
    team_by_round = {1: {"a": 3, "b": 3, "c": 2, "d": 2}}
    deaths_by_round = defaultdict(list, {1: [
        {"round": 1, "tick": 100, "atk": "c", "vic": "b", "vx": 0, "vy": 0, "ax": 0, "ay": 0},
        {"round": 1, "tick": 200, "atk": "a", "vic": "c", "vx": 0, "vy": 0, "ax": 0, "ay": 0},
        {"round": 1, "tick": 300, "atk": "a", "vic": "d", "vx": 0, "vy": 0, "ax": 0, "ay": 0},
    ]})
    return team_by_round, deaths_by_round


def test_compute_clutches_1v2_won():
    team_by_round, deaths_by_round = _clutch_fixture()
    roster = {"a": 0, "b": 1, "c": 2, "d": 3}
    cl = an.compute_clutches(deaths_by_round, team_by_round, {1: "ct"}, roster, 1)
    assert cl["a"]["won"] == 1 and cl["a"]["lost"] == 0
    assert cl["a"]["attempts"] == 1
    assert cl["a"]["by_x"][2] == [1, 0]      # 1v2 won
    # nobody else was in a clutch
    assert cl["c"]["attempts"] == 0


def test_compute_round_swing_attribution():
    team_by_round, deaths_by_round = _clutch_fixture()
    roster = {"a": 0, "b": 1, "c": 2, "d": 3}
    rounds = [{"num": 1, "start": 0, "freeze_end": 10, "end": 400, "winner": "CT", "reason": ""}]
    swing, cat, story, rimpact = an.compute_round_swing(
        deaths_by_round, team_by_round, rounds, plant_by_round={}, roster=roster)
    # a (CT) made the two winning kills -> net positive swing toward CT
    assert swing["a"] > 0
    # c killed a CT (b) first -> c's swing is positive toward T (stored as +ve for the actor)
    assert swing["c"] > 0
    # round produced swing magnitude + a story
    assert rimpact[1] > 0
    assert len(story[1]) >= 1


def test_build_breakdown_and_focus_shapes():
    players = [{"steamid": "a", "name": "A", "kast": 50, "open_wr": 30, "traded_pct": 10,
                "adr": 60, "udr": 2, "enemy_flashed": 1, "team_flashed": 0}]
    swing = {"a": 0.5}
    swing_cat = {"a": {"Opening": 0.1, "Trading": 0.2, "Firepower": 0.2}}
    clutches = {"a": {"won": 1, "lost": 0, "attempts": 1, "by_x": {2: [1, 0]}}}
    an.build_breakdown(players, swing, swing_cat, clutches)
    p = players[0]
    assert "impact_breakdown" in p and "impact_score" in p and p["round_swing"] == 50.0
    assert abs(p["impact_score"] - sum(p["impact_breakdown"].values())) < 0.05
    an.build_focus(players, {"a": []}, an.BENCH)
    assert isinstance(p["focus"], list) and len(p["focus"]) <= 5
    # low KAST/ADR/util should surface as focus areas
    assert any(f["area"] == "KAST" for f in p["focus"])


def test_team_loss_reason_priority():
    # eco loss is tagged as eco, not as a choke
    assert an._team_loss_reason("eco", True, False, 2, False, "t") == "Lost on an eco/save"
    # threw a big advantage
    assert an._team_loss_reason("full", False, False, 2, False, "ct") == "Threw a 2+ man advantage"
    # untraded opening on a full buy
    assert an._team_loss_reason("full", True, False, 0, False, "t") == "Opening death, no trade"
    # CT loses with bomb down = failed retake
    assert an._team_loss_reason("full", False, True, 0, True, "ct") == "Failed the retake"


def test_build_advanced_insights():
    from collections import defaultdict
    # player 'a' (T) dies at Mid first every round, bunched with teammate 'b', no support flash
    team_by_round = {r: {"a": 2, "b": 2, "c": 3} for r in range(1, 5)}
    D, dbr = [], defaultdict(list)
    for r in range(1, 5):
        da = {"round": r, "tick": r * 1000 + 100, "atk": "c", "vic": "a", "ast": None,
              "place": "Mid", "vx": 0, "vy": 0}
        db = {"round": r, "tick": r * 1000 + 150, "atk": "c", "vic": "b", "ast": None,
              "place": "Mid", "vx": 120, "vy": 0}
        D += [da, db]
        dbr[r] = [da, db]
    insights = {}
    an.build_advanced_insights(insights, D, dbr, team_by_round, {}, {}, None, [], 4, 64)
    types = {ins["type"] for ins in insights.get("a", [])}
    assert "dry_opening" in types        # 4 unsupported opening deaths
    assert "predictable" in types        # died at Mid 4x
    assert "clumping" in types           # died bunched with 'b'
    # a friendly flash before the opening should suppress the dry-opening flag
    flashes = [{"tick": r * 1000 + 80, "fl": "b", "vic": "c"} for r in range(1, 5)]
    ins2 = {}
    an.build_advanced_insights(ins2, D, dbr, team_by_round, {}, {}, None, flashes, 4, 64)
    assert "dry_opening" not in {ins["type"] for ins in ins2.get("a", [])}


def test_build_team_coaching_two_teams_consistent():
    from collections import defaultdict
    # 2 rounds, 4 players (a,b CT-start ; c,d T-start). R1 CT win, R2 (sides same) T win.
    team_by_round = {1: {"a": 3, "b": 3, "c": 2, "d": 2},
                     2: {"a": 3, "b": 3, "c": 2, "d": 2}}
    deaths_by_round = defaultdict(list, {
        1: [{"round": 1, "tick": 100, "atk": "a", "vic": "c", "place": "Mid"},
            {"round": 1, "tick": 200, "atk": "b", "vic": "d", "place": "A"}],
        2: [{"round": 2, "tick": 100, "atk": "c", "vic": "a", "place": "B"},
            {"round": 2, "tick": 200, "atk": "d", "vic": "b", "place": "B"}],
    })
    out_players = [{"steamid": s, "name": s.upper(), "traded_d": 0, "impact_score": 0,
                    "ct_role": "Rifler", "t_role": "Rifler", "open_wr": 50} for s in "abcd"]
    round_buy = {1: {"ct": "full", "t": "full"}, 2: {"ct": "full", "t": "full"}}
    round_winner = {1: "ct", 2: "t"}
    tc = an.build_team_coaching(out_players, deaths_by_round, team_by_round, round_buy,
                                round_winner, plant_by_round={}, defuse_rounds=set(),
                                names={s: s for s in "abcd"}, n_rounds=2)
    assert len(tc["teams"]) == 2
    a_team = next(t for t in tc["teams"] if t["start_side"] == "CT")
    assert a_team["won"] == 1 and a_team["lost"] == 1     # CT team: won R1, lost R2
    assert a_team["entry"]["attempts"] == 2               # one opening per round
