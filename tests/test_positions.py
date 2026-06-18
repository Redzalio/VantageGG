"""Tests for positions.py (#62 per-callout performance breakdown)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import positions   # noqa: E402

# round 1: a1/a2 = CT(3), b1/b2 = T(2)
TBR = {1: {"a1": 3, "a2": 3, "b1": 2, "b2": 2}}
ROSTER = {"a1", "a2", "b1", "b2"}


def _d(tick, atk, vic, place, rnd=1):
    return {"round": rnd, "tick": tick, "atk": atk, "vic": vic, "place": place}


def _run(D):
    dbr = {}
    for d in D:
        dbr.setdefault(d["round"], []).append(d)
    return positions.build_position_stats(D, dbr, TBR, ROSTER)


def test_side_and_opening_attribution():
    d1 = _d(100, "b1", "a1", "Long")   # opening: b1 (T) kills a1 (CT) at Long
    d2 = _d(200, "a2", "b1", "Long")   # a2 (CT) trades b1 at Long
    out = _run([d1, d2])
    a1 = {r["zone"]: r for r in out["a1"]}["Long"]
    assert a1["ct_d"] == 1 and a1["open_d"] == 1 and a1["k"] == 0 and a1["d"] == 1
    a2 = {r["zone"]: r for r in out["a2"]}["Long"]
    assert a2["ct_k"] == 1 and a2["k"] == 1 and a2["open_k"] == 0
    b1 = {r["zone"]: r for r in out["b1"]}["Long"]
    assert b1["t_k"] == 1 and b1["t_d"] == 1 and b1["open_k"] == 1 and b1["kd"] == 1.0


def test_kd_and_sorting_by_activity():
    D = [_d(100, "a1", "b1", "Pit"), _d(150, "a1", "b2", "Pit"),   # a1: 2 kills at Pit
         _d(300, "b1", "a1", "Ramp")]                              # a1: 1 death at Ramp
    out = _run(D)
    rows = out["a1"]
    assert rows[0]["zone"] == "Pit"        # most active position first (2 events vs 1)
    pit = {r["zone"]: r for r in rows}["Pit"]
    assert pit["k"] == 2 and pit["d"] == 0 and pit["kd"] == 2.0
    ramp = {r["zone"]: r for r in rows}["Ramp"]
    assert ramp["d"] == 1 and ramp["k"] == 0


def test_blank_place_skipped():
    out = _run([_d(100, "a1", "b1", ""), _d(120, "a2", "b2", None)])
    assert out["a1"] == [] and out["a2"] == []


def test_attach_sets_field():
    players = [{"steamid": "a1"}, {"steamid": "b1"}]
    D = [_d(100, "b1", "a1", "Long")]
    dbr = {1: D}
    positions.attach(players, D, dbr, TBR)
    assert all("position_stats" in p for p in players)
    assert any(r["zone"] == "Long" for r in players[0]["position_stats"])
