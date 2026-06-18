"""Tests for analytics formulas + split computation (synthetic; no demo needed)."""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analytics as an   # noqa: E402


def test_hltv_and_impact_formulas():
    # spot-check the regression at a known-ish operating point
    imp = an.impact_rating(0.7, 0.1)
    assert 1.0 < imp < 1.2
    r = an.hltv2(kast=70, kpr=0.7, dpr=0.65, impact=imp, adr=80)
    assert 0.8 < r < 1.4               # sane rating band
    # more kills/less deaths => higher rating
    r2 = an.hltv2(kast=80, kpr=0.9, dpr=0.5, impact=an.impact_rating(0.9, 0.1), adr=100)
    assert r2 > r


def test_sid_normalization():
    assert an._sid(76561198106326204) == "76561198106326204"
    assert an._sid("76561198106326204") == "76561198106326204"
    assert an._sid(None) is None
    assert an._sid(float("nan")) is None


def _two_round_fixture():
    # 2 rounds, 2 players a (T r1 / CT r2) vs b. econ: r1 pistol, r2 b full / a eco.
    team_by_round = {1: {"a": 2, "b": 3}, 2: {"a": 3, "b": 2}}
    econ_by_round = {1: {"a": {"equip": 800}, "b": {"equip": 800}},
                     2: {"a": {"equip": 500}, "b": {"equip": 4000}}}
    rounds = [{"num": 1, "start": 0, "freeze_end": 10, "end": 100, "winner": "T", "reason": ""},
              {"num": 2, "start": 200, "freeze_end": 210, "end": 300, "winner": "T", "reason": ""}]
    return team_by_round, econ_by_round, rounds


def test_compute_round_buys_pistol_and_antieco():
    team_by_round, econ_by_round, rounds = _two_round_fixture()
    rb = an.compute_round_buys(rounds, team_by_round, econ_by_round, have_econ=True)
    assert rb[1]["pistol"] is True
    assert rb[1]["ct"] == "pistol" and rb[1]["t"] == "pistol"
    # round 2: a (CT) eco $500, b (T) full $4000 -> T anti-eco
    assert rb[2]["ct"] == "eco" and rb[2]["t"] == "full"
    assert rb[2]["anti_eco_t"] is True and rb[2]["anti_eco_ct"] is False


def test_compute_round_buys_side_aware_full_floor():
    # round 2 (not a pistol/flip): CT avg $4000 = force (CT floor $4300, needs kit/util);
    # T avg $4000 = full (T floor $3900). Same equip, different label by side.
    tbr = {1: {"a": 3, "b": 3, "c": 2, "d": 2}, 2: {"a": 3, "b": 3, "c": 2, "d": 2}}
    e4 = {"equip": 4000}
    ebr = {1: {k: {"equip": 800} for k in "abcd"},
           2: {"a": e4, "b": e4, "c": e4, "d": e4}}
    rounds = [{"num": 1, "start": 0, "freeze_end": 10, "end": 100, "winner": "CT", "reason": ""},
              {"num": 2, "start": 200, "freeze_end": 210, "end": 300, "winner": "CT", "reason": ""}]
    rb = an.compute_round_buys(rounds, tbr, ebr, have_econ=True)
    assert rb[2]["pistol"] is False
    assert rb[2]["ct"] == "force"      # CT needs more for a full buy
    assert rb[2]["t"] == "full"


def test_compute_round_buys_mixed_and_hero():
    # round 2: CT = mixed/broken (one $4000 rifle, two saves -> spread 3700); T = hero
    # (team light overall but one player on a $3200 rifle).
    tbr = {1: {k: (3 if k in "abc" else 2) for k in "abcde"},
           2: {k: (3 if k in "abc" else 2) for k in "abcde"}}
    ebr = {1: {k: {"equip": 800} for k in "abcde"},
           2: {"a": {"equip": 4000}, "b": {"equip": 300}, "c": {"equip": 300},
               "d": {"equip": 3200}, "e": {"equip": 300}}}
    rounds = [{"num": 1, "start": 0, "freeze_end": 10, "end": 100, "winner": "CT", "reason": ""},
              {"num": 2, "start": 200, "freeze_end": 210, "end": 300, "winner": "CT", "reason": ""}]
    rb = an.compute_round_buys(rounds, tbr, ebr, have_econ=True)
    assert rb[2]["mixed_ct"] is True       # CT equip spread 4000-300 = 3700 >= 2500
    assert rb[2]["hero_t"] is True         # T is light overall but one player has a rifle


def test_buy_shape_helpers():
    assert an._is_mixed([4000, 300, 300], is_pistol=False) is True
    assert an._is_mixed([2000, 2000, 2000], is_pistol=False) is False
    assert an._is_mixed([4000, 300], is_pistol=True) is False        # pistols are never "mixed"
    assert an._is_hero("light", [3200, 800, 500], is_pistol=False) is True
    assert an._is_hero("full", [3200, 3000, 3000], is_pistol=False) is False   # not a saving team
    assert an._is_hero("eco", [900, 800], is_pistol=False) is False  # nobody has a rifle


def test_compute_splits_reconcile_with_totals():
    team_by_round, econ_by_round, rounds = _two_round_fixture()
    round_buy = an.compute_round_buys(rounds, team_by_round, econ_by_round, have_econ=True)
    round_winner = {1: "t", 2: "t"}
    # a kills b in r1 (T), b kills a in r2; a takes opening death r2 traded? keep simple.
    D = [
        {"round": 1, "tick": 50, "atk": "a", "vic": "b", "ast": None, "vx": 0, "vy": 0, "ax": 100, "ay": 0},
        {"round": 2, "tick": 250, "atk": "b", "vic": "a", "ast": None, "vx": 0, "vy": 0, "ax": 50, "ay": 0},
    ]
    dmg_acc = {(1, "a", "b"): 100.0, (2, "b", "a"): 100.0}
    kast_rounds = {"a": {1}, "b": {2}}
    deaths_by_round = defaultdict(list)
    for d in D:
        deaths_by_round[d["round"]].append(d)
    roster = {"a": 0, "b": 1}
    sp = an.compute_splits(D, dmg_acc, kast_rounds, deaths_by_round, roster, team_by_round,
                           round_buy, econ_by_round, round_winner, n_rounds=2, tickrate=64)
    # a: 1 kill on T (r1), 1 death on CT (r2)
    assert sp["a"]["sides"]["t"]["k"] == 1
    assert sp["a"]["sides"]["ct"]["d"] == 1
    # side kills+deaths reconcile to totals (1 kill, 1 death each)
    for s in ("a", "b"):
        tot_k = sp[s]["sides"]["ct"]["k"] + sp[s]["sides"]["t"]["k"]
        tot_d = sp[s]["sides"]["ct"]["d"] + sp[s]["sides"]["t"]["d"]
        assert tot_k == 1 and tot_d == 1
    # buy split: a was eco in r2, full? r1 pistol. b full r2, pistol r1.
    assert "pistol" in sp["a"]["buys"]


def test_credit_damage_caps_at_victim_hp():
    # a single overkill headshot on a full-HP victim is credited only 100 (not the rolled 131)
    assert list(an.credit_damage([{"tick": 1, "atk": "A", "vic": "V", "dmg": 131}]))[0][1] == 100.0
    # weakened victim: A does 70 (V -> 30 HP), B finishes with a 131 headshot -> B credited only 30
    hits = [{"tick": 1, "atk": "A", "vic": "V", "dmg": 70}, {"tick": 2, "atk": "B", "vic": "V", "dmg": 131}]
    cr = {h["atk"]: c for h, c in an.credit_damage(hits)}
    assert cr["A"] == 70.0 and cr["B"] == 30.0           # total 100, not 201 (multi-attacker overkill)
    # one attacker whittling down (60 then a 60 killing blow) credits exactly 100
    whittle = [{"tick": 1, "atk": "A", "vic": "V", "dmg": 60}, {"tick": 2, "atk": "A", "vic": "V", "dmg": 60}]
    assert sum(c for _, c in an.credit_damage(whittle)) == 100.0
    # tick order is respected (HP depletes in time order regardless of input order)
    assert sum(c for _, c in an.credit_damage(list(reversed(hits)))) == 100.0


def test_compute_trade_opportunities():
    # a,b are teammates (T); e is the enemy (CT). Trade chance = a living teammate within
    # TRADE_DIST of the victim at death; converted if the attacker is then killed in-window.
    roster = {"a": 0, "b": 1, "e": 2}
    team = {"a": 2, "b": 2, "e": 3}
    team_of = lambda sid, rnum: team.get(sid, 0)   # noqa: E731

    def fp(ax, ay, bx, by, ex, ey):
        return [{"x": ax, "y": ay, "alive": 1, "team": 2},
                {"x": bx, "y": by, "alive": 1, "team": 2},
                {"x": ex, "y": ey, "alive": 1, "team": 3}]

    D = [
        {"round": 1, "tick": 64, "atk": "e", "vic": "a", "vx": 0, "vy": 0},
        {"round": 1, "tick": 96, "atk": "b", "vic": "e", "vx": 1000, "vy": 1000},  # the refrag
        {"round": 2, "tick": 300, "atk": "e", "vic": "b", "vx": 0, "vy": 0},
        {"round": 3, "tick": 600, "atk": "e", "vic": "a", "vx": 0, "vy": 0},
    ]
    replay = {"frames": [
        {"t": 1.0, "players": fp(0, 0, 100, 0, 1000, 1000)},      # b near a  -> chance (traded)
        {"t": 4.6875, "players": fp(50, 0, 0, 0, 500, 500)},      # a near b  -> chance (failed)
        {"t": 9.375, "players": fp(0, 0, 2000, 2000, 100, 100)},  # b far from a -> no chance
    ]}
    r = an.compute_trade_opportunities(D, replay, roster, team_of, tickrate=64, trade_ticks=320)
    assert r["a"]["chances"] == 1 and r["a"]["traded"] == 1 and r["a"]["pct"] == 100.0
    assert r["b"]["chances"] == 1 and r["b"]["failed"] == 1 and r["b"]["pct"] == 0.0
    assert r["e"]["chances"] == 0 and r["e"]["pct"] is None


def test_stamp_confidence_defaults():
    ins = {"x": [{"type": "untraded_opening_death"}, {"type": "low_utility"}, {"type": "unknownX"}]}
    an._stamp_confidence(ins)
    assert ins["x"][0]["confidence"] == "high"
    assert ins["x"][1]["confidence"] == "med"
    assert ins["x"][2]["confidence"] == "med"   # default
    # every stamped insight gets a reason, polarity and an evidence dict
    for ic in ins["x"]:
        assert ic["confidence_reason"] and ic["polarity"] == "issue"
        assert isinstance(ic["evidence"], dict)


def test_build_insights_evidence_and_positives():
    out_players = [
        {"steamid": "bad", "name": "Bad", "open_k": 1, "open_d": 6, "open_wr": 14.0,
         "deaths": 10, "traded_pct": 20.0, "kills": 12, "udr": 2.0, "team_flashed": 4,
         "multi": {}, "kast": 50.0, "hltv": 0.8,
         "trade_opp": {"chances": 6, "traded": 1, "failed": 5, "pct": 16.7}},
        {"steamid": "good", "name": "Good", "open_k": 6, "open_d": 2, "open_wr": 75.0,
         "deaths": 8, "traded_pct": 75.0, "kills": 20, "udr": 10.0, "team_flashed": 0,
         "multi": {"3": 2}, "kast": 80.0, "hltv": 1.3,
         "trade_opp": {"chances": 3, "traded": 3, "failed": 0, "pct": 100.0}},
    ]
    D = [{"round": 1, "tick": 100, "atk": "good", "vic": "bad", "ast": None}]
    ins = an.build_insights(out_players, D, [{"num": 1}], {1: {"bad": 2, "good": 3}}, tickrate=64)
    an._stamp_confidence(ins)
    # every card carries machine-readable evidence + polarity + confidence reason
    for lst in ins.values():
        for ic in lst:
            assert isinstance(ic["evidence"], dict)
            assert ic["polarity"] in ("issue", "good")
            assert ic["confidence_reason"]
    tb = {ic["type"] for ic in ins["bad"]}
    assert "untraded_opening_death" in tb and "untraded_despite_support" in tb
    assert all(ic["polarity"] == "issue" for ic in ins["bad"])
    # the trade-opportunity card exposes the chance/fail counts as evidence
    to_card = next(ic for ic in ins["bad"] if ic["type"] == "untraded_despite_support")
    assert to_card["evidence"]["failed"] == 5 and to_card["evidence"]["chances"] == 6
    # the strong player gets "what went right" positives
    tg = {ic["type"] for ic in ins["good"]}
    assert {"good_openings", "good_utility", "multikills"} <= tg
    assert any(ic["polarity"] == "good" for ic in ins["good"])
