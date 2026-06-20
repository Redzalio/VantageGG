"""Tests for the callouts.py module."""
import json
import math
import os
import sys

import pytest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def callout_dir(tmp_path, monkeypatch):
    """Patch callouts._CALLOUT_DIR to a temp dir and return it."""
    import callouts
    monkeypatch.setattr(callouts, "_CALLOUT_DIR", tmp_path)
    callouts._cache.clear()   # clear module-level cache between tests
    return tmp_path


def write_map(callout_dir, map_name, callout_list):
    f = callout_dir / f"{map_name}.json"
    f.write_text(json.dumps({"map": map_name, "callouts": callout_list}), encoding="utf-8")


MIRAGE_CALLOUTS = [
    {"id": "a_site", "name": "A Site", "aliases": ["BombsiteA", "ASite", "A"],
     "world": {"x": 170, "y": -1100}, "side": "both"},
    {"id": "b_site", "name": "B Site", "aliases": ["BombsiteB", "BSite", "B"],
     "world": {"x": -1180, "y": -1100}, "side": "both"},
    {"id": "mid", "name": "Top Mid", "aliases": ["TopofMid", "TopMid", "Mid"],
     "world": {"x": -650, "y": -750}, "side": "both"},
    {"id": "ct_spawn", "name": "CT Spawn", "aliases": ["CTSpawn", "CT"],
     "world": {"x": 460, "y": -1900}, "side": "ct"},
]


class TestLoadCallouts:
    def test_returns_list_for_known_map(self, callout_dir):
        write_map(callout_dir, "de_mirage", MIRAGE_CALLOUTS)
        from callouts import load_callouts
        result = load_callouts("de_mirage")
        assert len(result) == 4
        assert result[0]["id"] == "a_site"

    def test_returns_empty_for_unknown_map(self, callout_dir):
        from callouts import load_callouts
        result = load_callouts("de_unknown")
        assert result == []

    def test_caches_result(self, callout_dir):
        write_map(callout_dir, "de_mirage", MIRAGE_CALLOUTS)
        from callouts import load_callouts, _cache
        load_callouts("de_mirage")
        assert "de_mirage" in _cache

    def test_cache_cleared_between_fixtures(self, callout_dir):
        # callout_dir fixture clears _cache each time
        from callouts import _cache
        assert "de_mirage" not in _cache


class TestNearestCallout:
    def test_finds_nearest_within_threshold(self, callout_dir):
        write_map(callout_dir, "de_mirage", MIRAGE_CALLOUTS)
        from callouts import nearest_callout
        # A site is at (170, -1100); query very close
        c, d = nearest_callout("de_mirage", 200, -1050)
        assert c is not None
        assert c["id"] == "a_site"
        assert d == pytest.approx(math.hypot(200 - 170, -1050 - (-1100)), abs=1)

    def test_returns_none_beyond_threshold(self, callout_dir):
        write_map(callout_dir, "de_mirage", MIRAGE_CALLOUTS)
        from callouts import nearest_callout
        c, d = nearest_callout("de_mirage", 9000, 9000, threshold=500)
        assert c is None
        assert d is None

    def test_returns_none_for_empty_map(self, callout_dir):
        from callouts import nearest_callout
        c, d = nearest_callout("de_unknown", 0, 0)
        assert c is None and d is None

    def test_distinguishes_a_and_b_sites(self, callout_dir):
        write_map(callout_dir, "de_mirage", MIRAGE_CALLOUTS)
        from callouts import nearest_callout
        c_a, _ = nearest_callout("de_mirage", 200, -1100)
        c_b, _ = nearest_callout("de_mirage", -1200, -1100)
        assert c_a["id"] == "a_site"
        assert c_b["id"] == "b_site"

    def test_skips_callouts_without_world_coords(self, callout_dir):
        bad = [
            {"id": "nope", "name": "Nope", "aliases": [], "world": {}, "side": "both"},
            {"id": "a_site", "name": "A Site", "aliases": [], "world": {"x": 170, "y": -1100}, "side": "both"},
        ]
        write_map(callout_dir, "de_mirage", bad)
        from callouts import nearest_callout
        c, _ = nearest_callout("de_mirage", 170, -1100)
        assert c is not None
        assert c["id"] == "a_site"


class TestZoneToCallout:
    def test_exact_alias_match(self, callout_dir):
        write_map(callout_dir, "de_mirage", MIRAGE_CALLOUTS)
        from callouts import zone_to_callout
        c = zone_to_callout("de_mirage", "BombsiteA")
        assert c is not None
        assert c["id"] == "a_site"

    def test_case_insensitive(self, callout_dir):
        write_map(callout_dir, "de_mirage", MIRAGE_CALLOUTS)
        from callouts import zone_to_callout
        assert zone_to_callout("de_mirage", "bombsitea") is not None
        assert zone_to_callout("de_mirage", "BOMBSITEA") is not None

    def test_matches_id(self, callout_dir):
        write_map(callout_dir, "de_mirage", MIRAGE_CALLOUTS)
        from callouts import zone_to_callout
        c = zone_to_callout("de_mirage", "a_site")
        assert c is not None

    def test_matches_name(self, callout_dir):
        write_map(callout_dir, "de_mirage", MIRAGE_CALLOUTS)
        from callouts import zone_to_callout
        c = zone_to_callout("de_mirage", "A Site")
        assert c is not None

    def test_returns_none_for_unknown(self, callout_dir):
        write_map(callout_dir, "de_mirage", MIRAGE_CALLOUTS)
        from callouts import zone_to_callout
        assert zone_to_callout("de_mirage", "XyzInvalid") is None

    def test_returns_none_for_empty_string(self, callout_dir):
        write_map(callout_dir, "de_mirage", MIRAGE_CALLOUTS)
        from callouts import zone_to_callout
        assert zone_to_callout("de_mirage", "") is None

    def test_engine_camelcase_alias(self, callout_dir):
        write_map(callout_dir, "de_mirage", MIRAGE_CALLOUTS)
        from callouts import zone_to_callout
        # TopofMid is a common engine last_place_name
        c = zone_to_callout("de_mirage", "TopofMid")
        assert c is not None
        assert c["name"] == "Top Mid"


class TestDisplayName:
    def test_returns_callout_name_when_matched(self, callout_dir):
        write_map(callout_dir, "de_mirage", MIRAGE_CALLOUTS)
        from callouts import callout_display_name
        assert callout_display_name("de_mirage", "BombsiteA") == "A Site"

    def test_humanizes_unknown_camelcase(self, callout_dir):
        from callouts import callout_display_name
        # Not in any callout file but should still be readable
        name = callout_display_name("de_mirage", "LongDoors")
        assert "Long" in name
        assert "Doors" in name

    def test_humanizes_underscore(self, callout_dir):
        from callouts import _humanize
        assert _humanize("long_doors") == "Long Doors"


class TestAvailableMaps:
    def test_lists_json_files(self, callout_dir):
        write_map(callout_dir, "de_mirage", [])
        write_map(callout_dir, "de_inferno", [])
        from callouts import available_maps
        maps = available_maps()
        assert "de_mirage" in maps
        assert "de_inferno" in maps

    def test_returns_sorted(self, callout_dir):
        write_map(callout_dir, "de_mirage", [])
        write_map(callout_dir, "de_ancient", [])
        from callouts import available_maps
        maps = available_maps()
        assert maps == sorted(maps)

    def test_empty_when_no_files(self, callout_dir):
        from callouts import available_maps
        assert available_maps() == []
