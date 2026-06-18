"""Tests for reviews.py (review bookmarks CRUD + auto-seeded review queues)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import reviews   # noqa: E402


def _demo():
    return {
        "tickrate": 64,
        "players": [{"steamid": "S1", "name": "Alice"}, {"steamid": "S2", "name": "Bob"}],
        "analytics": {
            "tickrate": 64,
            "insights": {
                "S1": [
                    {"type": "untraded_opening_death", "polarity": "issue", "round": 5,
                     "tick": 64 * 100, "text": "untraded"},
                    {"type": "low_utility", "polarity": "issue", "text": "aggregate (no tick/round)"},
                    {"type": "good_openings", "polarity": "good", "round": 7, "text": "nice opening"},
                ],
                "S2": [
                    {"type": "untraded_opening_death", "polarity": "issue", "round": 9,
                     "tick": 64 * 200, "text": "x"},
                ],
            },
            "round_cards": [
                {"round": 5, "watch_t": 60.0, "summary": "r5"},
                {"round": 7, "watch_t": 90.0, "summary": "r7"},
                {"round": 9, "watch_t": 120.0, "summary": "r9"},
            ],
            "team_coaching": {"teams": [
                {"id": "A", "name": "Team A",
                 "loss_reasons": [{"reason": "failed_retake", "rounds": [5, 9]}]},
            ]},
        },
    }


def test_auto_queues_filters_and_builds():
    qs = reviews.auto_queues(_demo())
    keys = {q["key"] for q in qs}
    assert "ins_untraded_opening_death" in keys
    assert "round_by_round" in keys
    assert any(k.startswith("team_A_") for k in keys)
    assert "ins_low_utility" not in keys           # aggregate (no tick/round) dropped

    uq = next(q for q in qs if q["key"] == "ins_untraded_opening_death")
    assert [it["round"] for it in uq["items"]] == [5, 9]      # both players, round-sorted
    assert uq["items"][0]["t"] == 98.5                        # tick/64 - 1.5s lead-in
    assert uq["items"][0]["player"] == 0                      # S1 -> roster idx 0

    gq = next(q for q in qs if q["key"] == "ins_good_openings")
    assert gq["polarity"] == "good" and gq["items"][0]["t"] == 90.0   # round-only -> watch_t

    for q in qs:                                              # every item is jumpable
        for it in q["items"]:
            assert it["round"] is not None


def test_auto_queues_orders_issues_before_rounds():
    pol = [q["polarity"] for q in reviews.auto_queues(_demo())]
    assert pol.index("issue") < pol.index("neutral")


def test_auto_queues_empty_on_garbage():
    assert reviews.auto_queues({}) == []
    assert reviews.auto_queues(None) == []


def test_bookmark_crud(tmp_path, monkeypatch):
    monkeypatch.setattr(reviews, "REVIEWS_DIR", str(tmp_path))
    monkeypatch.setattr(reviews, "REVIEWS_PATH", str(tmp_path / "reviews.json"))
    assert reviews.bookmarks("demoA") == []
    b = reviews.add_bookmark("demoA", {"t": 12.34, "round": 3, "player": 1,
                                       "note": "nice flash", "tag": "util"})
    assert b["id"].startswith("b_") and b["round"] == 3 and b["player"] == 1
    assert len(reviews.bookmarks("demoA")) == 1
    # editing by id updates in place (no duplicate)
    reviews.add_bookmark("demoA", {"id": b["id"], "t": 99.0, "round": 3, "note": "edited"})
    bms = reviews.bookmarks("demoA")
    assert len(bms) == 1 and bms[0]["note"] == "edited"
    assert reviews.delete_bookmark("demoA", b["id"]) == 1
    assert reviews.bookmarks("demoA") == []


def test_bookmark_entity_ref(tmp_path, monkeypatch):
    monkeypatch.setattr(reviews, "REVIEWS_DIR", str(tmp_path))
    monkeypatch.setattr(reviews, "REVIEWS_PATH", str(tmp_path / "reviews.json"))
    loc = reviews.add_bookmark("d", {"t": 5, "round": 2, "entity": "location", "ref": "Long",
                                     "note": "hold here", "tag": "setup"})
    util = reviews.add_bookmark("d", {"t": 5, "round": 2, "entity": "util", "ref": "smoke#1",
                                      "note": "ct smoke"})
    assert loc["entity"] == "location" and loc["ref"] == "Long" and loc["tag"] == "setup"
    assert util["entity"] == "util" and util["ref"] == "smoke#1"
    # same t/round but different entity/ref -> distinct notes (not collapsed by id)
    assert loc["id"] != util["id"]
    assert len(reviews.bookmarks("d")) == 2


def test_bookmark_rejects_bad_demo_id(tmp_path, monkeypatch):
    monkeypatch.setattr(reviews, "REVIEWS_DIR", str(tmp_path))
    monkeypatch.setattr(reviews, "REVIEWS_PATH", str(tmp_path / "reviews.json"))
    assert reviews.bookmarks("../etc/passwd") == []
    with pytest.raises(ValueError):
        reviews.add_bookmark("bad/id", {"t": 1})
