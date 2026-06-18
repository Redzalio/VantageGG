"""Tests for roles.py (#49 multi-label role model + role coaching)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import roles   # noqa: E402


def _sig(awp=0.0, op=2, cd=500, mv=50, util=1.0, fl=2, alive=300):
    return {"awp_frac": awp, "open_part": op, "cdist": cd, "move": mv,
            "util_pr": util, "flashes": fl, "alive": alive}


def test_t_side_primaries():
    sig = {
        0: _sig(awp=0.5),                       # AWP
        1: _sig(op=20, mv=120),                 # Entry (most opening involvement)
        2: _sig(cd=3000),                       # Lurker (farthest from team)
        3: _sig(util=4.0, fl=15),               # Support (most utility)
        4: _sig(op=8, mv=220),                  # Spacetaker (most mobile)
    }
    r = roles.assign_side_roles(2, sig)
    assert r[0]["primary"] == "AWP"
    assert r[1]["primary"] == "Entry"
    assert r[2]["primary"] == "Lurker"
    assert r[3]["primary"] == "Support"
    assert r[4]["primary"] == "Spacetaker"


def test_ct_anchor_vs_rotator():
    sig = {0: _sig(mv=20, cd=400), 1: _sig(mv=300, cd=800)}
    r = roles.assign_side_roles(3, sig)
    assert r[0]["primary"] == "Anchor"
    assert r[1]["primary"] == "Rotator"


def test_labels_are_weighted_and_sum_to_one():
    sig = {0: _sig(op=20, mv=120, util=2), 1: _sig(cd=3000), 2: _sig(awp=0.5)}
    r = roles.assign_side_roles(2, sig)
    for v in r.values():
        assert len(v["labels"]) >= 1
        assert abs(sum(l["weight"] for l in v["labels"]) - 1.0) < 0.02
        # weights descending, primary == first label
        ws = [l["weight"] for l in v["labels"]]
        assert ws == sorted(ws, reverse=True)
        assert v["primary"] == v["labels"][0]["role"]


def test_committed_awp_dominates():
    sig = {0: _sig(awp=0.45, op=15), 1: _sig(op=3), 2: _sig(cd=2000)}
    r = roles.assign_side_roles(2, sig)
    assert r[0]["primary"] == "AWP"
    assert r[0]["labels"][0]["weight"] >= 0.4


def test_low_alive_skipped():
    sig = {0: _sig(alive=5), 1: _sig(op=20, alive=300)}
    r = roles.assign_side_roles(2, sig)
    assert 0 not in r and 1 in r


def test_confidence_low_on_short_sample():
    sig = {0: _sig(op=20, alive=60), 1: _sig(cd=2000, alive=60)}
    r = roles.assign_side_roles(2, sig)
    assert r[0]["confidence"] == "low"


def test_role_coaching_verdict():
    bench = {"open_wr": 52, "udr": 8, "kast": 70, "adr": 80}
    above = roles.role_coaching("Entry", {"open_wr": 60}, bench)
    below = roles.role_coaching("Entry", {"open_wr": 40}, bench)
    assert above["verdict"] == "above" and above["metric"] == "open_wr"
    assert below["verdict"] == "below"
    assert "drill" in above and "watch" in above


def test_label_str():
    assert roles.label_str([{"role": "Entry", "weight": 0.6}, {"role": "Support", "weight": 0.25}]) \
        == "Entry 60% · Support 25%"
