"""Tests for the transparent, lobby-relative Context Rating module."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import context_rating   # noqa: E402

SUB_KEYS = {"kills", "damage", "survival", "kast", "multi", "swing"}
RAW_KEYS = {"kills_pr", "adr", "survival_pct", "kast", "multi_pr", "swing"}


def test_three_players_bounds_and_keys():
    players = [
        {"steamid": "A", "rounds_played": 20, "kills": 25, "deaths": 10, "adr": 100.0,
         "kast": 80.0, "multi": {"2": 5, "3": 2}, "swing": 4.0},
        {"steamid": "B", "rounds_played": 20, "kills": 15, "deaths": 14, "adr": 70.0,
         "kast": 68.0, "multi": {"2": 2}, "swing": 0.0},
        {"steamid": "C", "rounds_played": 20, "kills": 8, "deaths": 18, "adr": 45.0,
         "kast": 55.0, "multi": {}, "swing": -3.0},
    ]
    eco = {"A": 1.0, "B": 1.2, "C": 0.9}
    res = context_rating.compute_context_rating(players, eco)
    assert set(res.keys()) == {"A", "B", "C"}
    for sid, r in res.items():
        assert set(r["sub"].keys()) == SUB_KEYS
        assert set(r["raw"].keys()) == RAW_KEYS
        assert "eco_factor" in r and "context_rating" in r
        for v in r["sub"].values():
            assert 0.4 <= v <= 1.8
        # rating is a weighted average of sub-ratings, so it lives in the same range
        assert 0.4 <= r["context_rating"] <= 1.8


def test_identical_players_average_to_one():
    p = {"rounds_played": 20, "kills": 18, "deaths": 13, "adr": 80.0,
         "kast": 72.0, "multi": {"2": 3, "3": 1}, "swing": 1.5}
    players = [dict(p, steamid="A"), dict(p, steamid="B")]
    eco = {"A": 1.0, "B": 1.0}
    res = context_rating.compute_context_rating(players, eco)
    for sid in ("A", "B"):
        for k, v in res[sid]["sub"].items():
            assert abs(v - 1.0) <= 0.05, (sid, k, v)
        assert abs(res[sid]["context_rating"] - 1.0) <= 0.05


def test_eco_boost_raises_kills_and_damage():
    base = {"rounds_played": 20, "kills": 18, "deaths": 13, "adr": 80.0,
            "kast": 72.0, "multi": {"2": 3}, "swing": 1.0}
    players = [dict(base, steamid="rich"), dict(base, steamid="poor")]
    eco = {"rich": 1.0, "poor": 1.6}  # "poor" faced much richer enemies
    res = context_rating.compute_context_rating(players, eco)
    assert res["poor"]["sub"]["kills"] > res["rich"]["sub"]["kills"]
    assert res["poor"]["sub"]["damage"] > res["rich"]["sub"]["damage"]


def test_empty_players_returns_empty():
    assert context_rating.compute_context_rating([], {}) == {}
