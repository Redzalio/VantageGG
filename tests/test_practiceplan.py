"""Tests for the practice-plan done-state store."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import practiceplan   # noqa: E402


def test_load_missing_is_empty(tmp_path):
    assert practiceplan.load_done(str(tmp_path / "nope.json")) == {}


def test_set_and_clear_done(tmp_path):
    p = str(tmp_path / "practice.json")
    practiceplan.set_done("item_a", True, p)
    practiceplan.set_done("item_b", True, p)
    assert practiceplan.load_done(p) == {"item_a": True, "item_b": True}
    # clearing removes it
    practiceplan.set_done("item_a", False, p)
    assert practiceplan.load_done(p) == {"item_b": True}
    # no leftover temp files
    assert [f for f in os.listdir(tmp_path) if f.startswith(".tmp_")] == []
