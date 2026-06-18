"""Tests for teamplay.py (#43 trade network + spacing)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import teamplay   # noqa: E402

TR = 64
# 2v2: team A (a1,a2) started CT (3); team B (b1,b2) started T (2).
TBR = {1: {"a1": 3, "a2": 3, "b1": 2, "b2": 2},
       2: {"a1": 3, "a2": 3, "b1": 2, "b2": 2}}
NAMES = {"a1": "A1", "a2": "A2", "b1": "B1", "b2": "B2"}


def _d(tick, atk, vic, vx=0.0, vy=0.0, ax=0.0, ay=0.0, rnd=1):
    return {"round": rnd, "tick": tick, "atk": atk, "vic": vic,
            "vx": vx, "vy": vy, "ax": ax, "ay": ay, "place": "Site"}


def _net(deaths_by_round):
    return teamplay.build_trade_network(deaths_by_round, TBR, NAMES, TR)


def test_trade_attributed_to_teammate():
    # a1 dies to b1 @100; a2 kills b1 @200 (within 5s/320t) -> a2 traded a1
    net = _net({1: [_d(100, "b1", "a1"), _d(200, "a2", "b1")]})
    A = {p["steamid"]: p for p in net["A"]["players"]}
    assert A["a1"]["traded"] == 1 and A["a1"]["untraded"] == 0 and A["a1"]["traded_pct"] == 100.0
    assert A["a2"]["trades_made"] == 1
    assert any(e["trader_sid"] == "a2" and e["victim_sid"] == "a1" and e["count"] == 1
               for e in net["A"]["edges"])
    # the killer b1 dies untraded (no B teammate avenged him)
    B = {p["steamid"]: p for p in net["B"]["players"]}
    assert B["b1"]["untraded"] == 1 and B["b1"]["traded_pct"] == 0.0


def test_trade_window_expires():
    # avenging kill arrives after the 5s window -> NOT a trade
    net = _net({1: [_d(100, "b1", "a1"), _d(100 + 6 * TR, "a2", "b1")]})
    A = {p["steamid"]: p for p in net["A"]["players"]}
    assert A["a1"]["traded"] == 0 and A["a1"]["untraded"] == 1
    assert net["A"]["edges"] == []


def test_self_and_teamkill_deaths_skipped():
    # suicide (atk None) + teamkill (a2 kills a1) are not tradeable deaths
    net = _net({1: [_d(100, None, "a1"), _d(150, "a2", "a1")]})
    A = {p["steamid"]: p for p in net["A"]["players"]}
    assert A["a1"]["deaths"] == 0   # neither counted toward the network


def test_team_traded_pct_and_weak_links():
    # a1 traded 1/5; everything else untraded -> a1 is a weak link (<40%, >=4 deaths)
    deaths = []
    for k in range(5):
        t = 1000 * (k + 1)
        deaths.append(_d(t, "b1", "a1"))
        if k == 0:                      # only the first death gets traded
            deaths.append(_d(t + 100, "a2", "b1"))
    net = _net({1: deaths})
    A = {p["steamid"]: p for p in net["A"]["players"]}
    assert A["a1"]["deaths"] == 5 and A["a1"]["traded"] == 1 and A["a1"]["traded_pct"] == 20.0
    assert any(w["steamid"] == "a1" for w in net["A"]["weak_links"])
    assert net["A"]["team_traded_pct"] is not None


# ---- spacing ---------------------------------------------------------------
def _replay(frames):
    return {"frames": frames, "sample_rate": 1,
            "players": [{"steamid": s} for s in ("a1", "a2", "b1", "b2")]}


def _frame(positions):
    # positions: {sid: (x, y, team, alive)}
    order = ("a1", "a2", "b1", "b2")
    return {"players": [{"x": positions[s][0], "y": positions[s][1],
                         "team": positions[s][2], "alive": positions[s][3]} for s in order]}


def test_spacing_isolated_vs_supported():
    # sr=1 so frame index = round(tick/64). a1 dies @64 -> frame[1].
    far = _frame({"a1": (0, 0, 3, False), "a2": (2000, 0, 3, True),
                  "b1": (0, 0, 2, True), "b2": (0, 0, 2, True)})
    frames = [far, far]
    sp = teamplay.build_spacing({1: [_d(64, "b1", "a1", vx=0, vy=0)]}, TBR, NAMES, _replay(frames), TR)
    A = {p["steamid"]: p for p in sp["A"]["players"]}
    assert A["a1"]["avg_support_dist"] == 2000 and A["a1"]["isolated"] == 1
    assert sp["A"]["isolated_deaths"] == 1


def test_spacing_clumped_deaths():
    # a1 @64 (idx1) and a2 @70 (idx1) die close in time + space -> clumped
    fr = _frame({"a1": (0, 0, 3, False), "a2": (100, 0, 3, False),
                 "b1": (0, 0, 2, True), "b2": (0, 0, 2, True)})
    frames = [fr, fr]
    deaths = {1: [_d(64, "b1", "a1", vx=0, vy=0), _d(70, "b2", "a2", vx=100, vy=0)]}
    sp = teamplay.build_spacing(deaths, TBR, NAMES, _replay(frames), TR)
    assert sp["A"]["clumped_deaths"] >= 2


def test_spacing_no_frames_graceful():
    assert teamplay.build_spacing({1: [_d(100, "b1", "a1")]}, TBR, NAMES, None, TR) == {}
    assert teamplay.build_spacing({1: [_d(100, "b1", "a1")]}, TBR, NAMES, {"frames": []}, TR) == {}


def test_build_team_play_merges():
    tp = teamplay.build_team_play({1: [_d(100, "b1", "a1"), _d(200, "a2", "b1")]},
                                  TBR, NAMES, None, TR)
    assert "A" in tp and "B" in tp
    assert "players" in tp["A"] and "edges" in tp["A"]
    assert tp["A"]["spacing"] is None         # no replay -> spacing absent, trade still present
