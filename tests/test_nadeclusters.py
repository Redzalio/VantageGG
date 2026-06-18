"""Tests for nadeclusters.py (#61 auto-detect consistent utility)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nadeclusters   # noqa: E402


def _t(sid="S1", mp="de_dust2", ty="smoke", lx=100, ly=100, tx=900, ty_=900, side="ct"):
    return {"steamid": sid, "map": mp, "type": ty, "lx": lx, "ly": ly, "tx": tx, "ty": ty_, "side": side}


def test_cluster_groups_repeated_spot():
    throws = [_t(lx=100, ly=100), _t(lx=130, ly=110), _t(lx=90, ly=95)]   # ~same spot
    res = nadeclusters.cluster_throws(throws, ["d1", "d1", "d2"])
    assert len(res) == 1
    assert res[0]["count"] == 3 and res[0]["matches"] == 2 and res[0]["type"] == "smoke"


def test_below_thresholds_drop():
    assert nadeclusters.cluster_throws([_t(), _t()], ["d1", "d2"]) == []          # < min_throws
    assert nadeclusters.cluster_throws([_t(), _t(), _t()], ["d1", "d1", "d1"]) == []  # < min_matches


def test_steamid_filter():
    throws = [_t(sid="S1"), _t(sid="S1"), _t(sid="S2"), _t(sid="S2"), _t(sid="S2")]
    res = nadeclusters.cluster_throws(throws, ["d1", "d2", "d1", "d2", "d3"], steamid="S2")
    assert len(res) == 1 and res[0]["steamid"] == "S2" and res[0]["count"] == 3


def test_distant_spots_separate():
    throws = [_t(lx=100, ly=100), _t(lx=100, ly=100), _t(lx=2000, ly=2000), _t(lx=2000, ly=2000)]
    res = nadeclusters.cluster_throws(throws, ["d1", "d2", "d1", "d2"], min_throws=2)
    assert len(res) == 2


def test_side_majority():
    res = nadeclusters.cluster_throws([_t(side="t"), _t(side="t"), _t(side="ct")], ["d1", "d2", "d1"])
    assert res and res[0]["side"] == "t"


def test_throws_from_demo_extracts_side_and_coords():
    demo = {"map": "de_dust2", "sample_rate": 1,
            "players": [{"steamid": "S1", "name": "A"}, {"steamid": "S2", "name": "B"}],
            "frames": [{"players": [{"team": 3}, {"team": 2}]}, {"players": [{"team": 3}, {"team": 2}]}],
            "grenades": [{"type": "smoke", "thrower": 0, "t0": 1.0, "det_pos": [500, 600, 100],
                          "pts": [[1.0, 900, 900, 64]]}]}
    ts = nadeclusters.throws_from_demo(demo)
    assert len(ts) == 1
    t = ts[0]
    assert t["steamid"] == "S1" and t["lx"] == 500 and t["tx"] == 900 and t["side"] == "ct"


def test_to_nade_shape():
    n = nadeclusters.to_nade({"map": "de_dust2", "side": "t", "type": "flash", "count": 4,
                              "matches": 2, "land": [500, 600], "throw": [900, 900]})
    assert n["map"] == "de_dust2" and n["side"] == "t" and n["type"] == "flash"
    assert n["land_pos"][0] == 500 and n["throw_pos"][0] == 900 and n["source"] == "auto"
