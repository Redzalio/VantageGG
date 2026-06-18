"""Tests for playbook.py (#45 team playbook store + adherence engine)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import playbook   # noqa: E402


def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(playbook, "PLAYBOOK_DIR", str(tmp_path))
    monkeypatch.setattr(playbook, "PLAYBOOK_PATH", str(tmp_path / "playbook.json"))


def test_store_crud(tmp_path, monkeypatch):
    _tmp(tmp_path, monkeypatch)
    assert playbook.plays_for("de_dust2") == []
    p = playbook.add_play({"map": "de_dust2", "side": "t", "name": "A exec",
                           "util": [{"type": "smoke", "x": 100, "y": 100}]})
    assert p["id"].startswith("pb_") and p["side"] == "t"
    assert len(playbook.plays_for("de_dust2")) == 1
    assert playbook.plays_for("de_mirage") == []        # filtered by map
    # replace by id (no dupe)
    playbook.add_play({"id": p["id"], "map": "de_dust2", "side": "t", "name": "A exec v2",
                       "util": [{"type": "smoke", "x": 1, "y": 2}]})
    plays = playbook.plays_for("de_dust2")
    assert len(plays) == 1 and plays[0]["name"] == "A exec v2"
    assert playbook.delete_play(p["id"]) == 1 and playbook.plays_for("de_dust2") == []


def test_side_defaults_and_util_norm(tmp_path, monkeypatch):
    _tmp(tmp_path, monkeypatch)
    p = playbook.add_play({"map": "m", "side": "bogus",
                           "util": [{"type": "flash", "x": "3.456", "y": 7}, {"type": "he"}]})
    assert p["side"] == "ct"                              # invalid side -> ct
    assert p["util"] == [{"type": "flash", "x": 3.5, "y": 7.0}]   # bad coords dropped, rounded


def test_play_from_throws_dedups():
    throws = [{"type": "smoke", "x": 100, "y": 100}, {"type": "smoke", "x": 130, "y": 110},  # dup landing
              {"type": "smoke", "x": 800, "y": 800}, {"type": "flash", "x": 100, "y": 100}]  # diff spot / type
    util = playbook.play_from_throws(throws)
    assert len(util) == 3       # the 2nd smoke is deduped; far smoke + flash kept


def _play():
    return {"map": "m", "side": "ct", "name": "p",
            "util": [{"type": "smoke", "x": 100, "y": 100}, {"type": "flash", "x": 500, "y": 500}]}


def test_adherence_executed_vs_partial():
    throws = [
        {"type": "smoke", "round": 1, "side": "ct", "x": 110, "y": 110},   # r1 both -> executed
        {"type": "flash", "round": 1, "side": "ct", "x": 520, "y": 490},
        {"type": "smoke", "round": 2, "side": "ct", "x": 120, "y": 90},    # r2 only smoke -> 0.5
        {"type": "flash", "round": 2, "side": "ct", "x": 2000, "y": 2000},
        {"type": "smoke", "round": 3, "side": "ct", "x": 100, "y": 100},   # r3 both -> executed
        {"type": "flash", "round": 3, "side": "ct", "x": 500, "y": 500},
        {"type": "smoke", "round": 1, "side": "t", "x": 100, "y": 100},    # wrong side -> ignored
    ]
    r = playbook.check_adherence(_play(), throws)
    assert r["rounds_applicable"] == 3 and r["rounds_executed"] == 2 and r["adherence_pct"] == 67
    by = {e["type"]: e for e in r["elements"]}
    assert by["smoke"]["used"] == 3 and by["smoke"]["used_pct"] == 100
    assert by["flash"]["used"] == 2 and by["flash"]["used_pct"] == 67


def test_adherence_empty_cases():
    assert playbook.check_adherence({"side": "ct", "util": []}, [{"round": 1, "side": "ct", "type": "smoke", "x": 0, "y": 0}])["rounds_applicable"] == 0
    # util present but no throws on that side
    assert playbook.check_adherence(_play(), [{"type": "smoke", "round": 1, "side": "t", "x": 100, "y": 100}])["rounds_applicable"] == 0
