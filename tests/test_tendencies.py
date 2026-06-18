"""Tests for tendencies.py (#44 cross-match tendency / repeated-pattern detection)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tendencies   # noqa: E402


def _match(map_="de_dust2", players=None, key="k"):
    return {"map": map_, "created_at": "2026-01-01", "key": key, "sha": key,
            "analytics": {"players": players or []}}


def _pl(steamid="S1", **over):
    p = {"steamid": steamid, "name": "P", "rounds_played": 20, "open_k": 2, "open_d": 2,
         "open_wr": 50, "zones": {}, "buys": {}, "ct_role": "Rifler", "t_role": "Rifler"}
    p.update(over)
    return p


def _kinds(r, kind):
    return [t for t in r["tendencies"] if t["kind"] == kind]


def test_needs_min_matches():
    r = tendencies.cross_tendencies([_match(players=[_pl()])], "S1")
    assert r["n_matches"] == 1 and r["tendencies"] == []


def test_player_absent():
    r = tendencies.cross_tendencies([_match(players=[_pl(steamid="OTHER")])], "S1")
    assert r["n_matches"] == 0 and r["tendencies"] == []


def test_role_consistency():
    ms = [_match(players=[_pl(t_role="Lurker")]) for _ in range(3)] + [_match(players=[_pl(t_role="Rifler")])]
    r = tendencies.cross_tendencies(ms, "S1")
    assert r["n_matches"] == 4
    role = _kinds(r, "role")
    tside = [t for t in role if t["side"] == "T"]
    assert tside and "Lurker" in tside[0]["text"] and "3/4" in tside[0]["text"]


def test_multilabel_role_preferred():
    ms = [_match(players=[_pl(t_roles=[{"role": "Entry", "weight": 0.6}], t_role="Rifler")]) for _ in range(2)]
    r = tendencies.cross_tendencies(ms, "S1")
    tside = [t for t in _kinds(r, "role") if t["side"] == "T"]
    assert tside and "Entry" in tside[0]["text"]      # multi-label primary wins over single


def test_recurring_death_spot():
    ms = [_match(players=[_pl(zones={"Mid": {"k": 0, "d": 3}})]) for _ in range(3)]
    r = tendencies.cross_tendencies(ms, "S1")
    ds = _kinds(r, "death_spot")
    assert ds and ds[0]["zone"] == "Mid" and ds[0]["severity"] == 2


def test_recurring_strong_spot():
    ms = [_match(players=[_pl(zones={"Long": {"k": 4, "d": 0}})]) for _ in range(3)]
    r = tendencies.cross_tendencies(ms, "S1")
    ss = _kinds(r, "strong_spot")
    assert ss and ss[0]["zone"] == "Long"


def test_opening_tendency_aggressive_low_win():
    ms = [_match(players=[_pl(open_k=1, open_d=5, open_wr=30)]) for _ in range(3)]
    r = tendencies.cross_tendencies(ms, "S1")
    op = _kinds(r, "opening")
    assert op and op[0]["severity"] == 2 and "win only 30%" in op[0]["text"]


def test_buy_tendency_loses_force():
    ms = [_match(players=[_pl(buys={"force": {"rounds": 3, "win_pct": 20}})]) for _ in range(3)]
    r = tendencies.cross_tendencies(ms, "S1")
    b = _kinds(r, "buy")
    assert b and b[0]["evidence"]["buy"] == "force" and b[0]["evidence"]["win_pct"] == 20


def test_one_off_not_flagged():
    # dies at Mid in only ONE of three matches -> not a recurring spot
    ms = [_match(players=[_pl(zones={"Mid": {"k": 0, "d": 4}})]),
          _match(players=[_pl(zones={"A": {"k": 2, "d": 1}})]),
          _match(players=[_pl(zones={"B": {"k": 1, "d": 1}})])]
    r = tendencies.cross_tendencies(ms, "S1")
    assert not any(t.get("zone") == "Mid" for t in _kinds(r, "death_spot"))
