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


# --------------------------------------------------------------------------- #
# New named playlists added to auto_queues. Each is built from analytics shapes
# that actually exist (verified against analytics.py): promoted team loss
# reasons, analytics.rounds[].impact, and round_cards[].moments kill swings.
# --------------------------------------------------------------------------- #
def _by_key(qs):
    return {q["key"]: q for q in qs}


def _rich_demo():
    """A demo whose analytics carry everything the new queues consume."""
    return {
        "tickrate": 64,
        "players": [{"steamid": "S1", "name": "Alice"}, {"steamid": "S2", "name": "Bob"}],
        "analytics": {
            "tickrate": 64,
            "insights": {},
            "rounds": [
                {"num": 1, "winner": "ct", "impact": 30.0},
                {"num": 2, "winner": "t", "impact": 220.5},   # most decisive
                {"num": 3, "winner": "ct", "impact": 0},      # no swing -> excluded
                {"num": 4, "winner": "t", "impact": 95.0},
            ],
            "round_cards": [
                {"round": 1, "watch_t": 10.0, "summary": "r1",
                 "moments": [{"type": "kill", "atk": "S1", "swing": 12.0},
                             {"type": "kill", "atk": "S2", "swing": 40.0}]},
                {"round": 2, "watch_t": 20.0, "summary": "r2",
                 "moments": [{"type": "plant", "swing": 11.0},
                             {"type": "kill", "atk": "S1", "swing": 55.0}]},
                {"round": 3, "watch_t": 30.0, "summary": "r3", "moments": []},
                {"round": 4, "watch_t": 40.0, "summary": "r4",
                 "moments": [{"type": "kill", "atk": "S2", "swing": 8.0}]},
            ],
            "team_coaching": {"teams": [
                {"id": "A", "name": "Team A", "loss_reasons": [
                    {"reason": "Lost an even full-buy", "rounds": [3, 8]},
                    {"reason": "Failed the retake", "rounds": [11]},
                    {"reason": "Lost the gunfights", "rounds": [13]},   # NOT promoted
                ]},
                {"id": "B", "name": "Team B", "loss_reasons": [
                    {"reason": "Lost an even full-buy", "rounds": [4]},     # merges with Team A's
                    {"reason": "Lost the post-plant", "rounds": [2, 6]},
                ]},
            ]},
        },
    }


def test_promoted_loss_reason_queues_present_and_merged():
    qs = _by_key(reviews.auto_queues(_rich_demo()))
    # promoted -> dedicated named playlists, aggregated across BOTH teams
    assert "lost_full_buys" in qs
    assert qs["lost_full_buys"]["label"] == "Lost full-buy rounds"
    assert [it["round"] for it in qs["lost_full_buys"]["items"]] == [3, 4, 8]   # A:[3,8]+B:[4], sorted
    assert qs["lost_full_buys"]["polarity"] == "issue"

    assert "post_plant_losses" in qs
    assert [it["round"] for it in qs["post_plant_losses"]["items"]] == [2, 6]

    assert "failed_retakes" in qs
    assert [it["round"] for it in qs["failed_retakes"]["items"]] == [11]

    # a non-promoted reason still flows through the generic per-team queue (unchanged behaviour)
    assert any(k.startswith("team_A_") for k in qs)
    # ...and is NOT swallowed into a promoted bucket
    assert "lost_gunfights" not in qs


def test_worst_swing_ranked_by_impact():
    qs = _by_key(reviews.auto_queues(_rich_demo()))
    assert "worst_swing" in qs
    ws = qs["worst_swing"]
    assert ws["polarity"] == "issue"
    # ranked by |impact| desc; round 3 (impact 0) excluded
    assert [it["round"] for it in ws["items"]] == [2, 4, 1]
    assert ws["items"][0]["t"] == 20.0          # round 2 watch_t


def test_best_rounds_from_kill_swings():
    qs = _by_key(reviews.auto_queues(_rich_demo()))
    assert "best_rounds" in qs
    br = qs["best_rounds"]
    assert br["polarity"] == "good"
    # top kill-swing per round: r2=55(S1), r1=40(S2), r4=8(S2); r3 has no kills -> excluded
    assert [it["round"] for it in br["items"]] == [2, 1, 4]
    assert br["items"][0]["player"] == 0        # S1 -> roster idx 0
    assert br["items"][1]["player"] == 1        # S2 -> roster idx 1
    # deduped by round (one entry per round)
    assert len({it["round"] for it in br["items"]}) == len(br["items"])


def test_new_queues_absent_when_data_missing():
    """No rounds/round_cards/team_coaching -> the new playlists simply don't appear (no crash)."""
    data = {
        "tickrate": 64,
        "players": [{"steamid": "S1", "name": "Alice"}],
        "analytics": {"tickrate": 64, "insights": {
            "S1": [{"type": "untraded_opening_death", "polarity": "issue", "round": 2,
                    "tick": 64 * 50, "text": "x"}]}},
    }
    keys = _by_key(reviews.auto_queues(data)).keys()
    for absent in ("lost_full_buys", "post_plant_losses", "failed_retakes",
                   "worst_swing", "best_rounds"):
        assert absent not in keys
    # the existing insight queue still builds fine
    assert "ins_untraded_opening_death" in keys


def test_worst_swing_absent_without_impact():
    """rounds present but all impact 0/missing -> worst_swing dropped, best_rounds still works."""
    data = _rich_demo()
    for r in data["analytics"]["rounds"]:
        r["impact"] = 0
    keys = _by_key(reviews.auto_queues(data)).keys()
    assert "worst_swing" not in keys
    assert "best_rounds" in keys        # independent source (round_cards moments)


def test_new_queues_never_throw_on_partial_shapes():
    """Malformed/partial analytics must not raise -- guard every key access."""
    weird = {
        "players": [{"steamid": "S1"}],
        "analytics": {
            "rounds": [{"num": 1}, {"impact": 50.0}, {}],            # missing keys
            "round_cards": [{"round": 1}, {"moments": [{"type": "kill"}]}, {}],
            "team_coaching": {"teams": [{"loss_reasons": [{"reason": "Failed the retake"}]},
                                        {}, None]},
        },
    }
    # should return a list, not raise
    assert isinstance(reviews.auto_queues(weird), list)
