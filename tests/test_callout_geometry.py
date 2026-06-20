"""Tests for callouts.py geometry: point_in_polygon + label_position (boundary-first, nearest-fallback)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from callouts import point_in_polygon, label_position

SQUARE = [[0, 0], [100, 0], [100, 100], [0, 100]]


class TestPointInPolygon:
    def test_inside(self):
        assert point_in_polygon(50, 50, SQUARE) is True

    def test_outside(self):
        assert point_in_polygon(150, 50, SQUARE) is False
        assert point_in_polygon(-10, 50, SQUARE) is False

    def test_empty_or_degenerate(self):
        assert point_in_polygon(0, 0, []) is False
        assert point_in_polygon(0, 0, [[0, 0], [1, 1]]) is False  # < 3 points

    def test_concave_polygon(self):
        # An L-shape: the notch is outside.
        L = [[0, 0], [100, 0], [100, 40], [40, 40], [40, 100], [0, 100]]
        assert point_in_polygon(20, 20, L) is True     # in the foot
        assert point_in_polygon(70, 70, L) is False    # in the notch


CALLOUTS = [
    {"id": "a", "name": "A Site", "world": {"x": 0, "y": 0}},
    {"id": "b", "name": "B Site", "world": {"x": 1000, "y": 0}},
    {"id": "boxed", "name": "Boxed", "world": {"x": 5000, "y": 5000},
     "boundary": [[0, 0], [100, 0], [100, 100], [0, 100]]},
]


class TestLabelPosition:
    def test_boundary_wins_over_distance(self):
        # (50,50) is far from Boxed's center (5000,5000) but inside its polygon -> inside wins.
        res = label_position(CALLOUTS, 50, 50)
        assert res["callout"]["id"] == "boxed"
        assert res["confidence"] == "inside"
        assert res["distance"] == 0.0

    def test_nearest_center(self):
        res = label_position(CALLOUTS, 980, 20, threshold=500)
        assert res["callout"]["id"] == "b"
        assert res["confidence"] in ("nearest", "nearby")

    def test_none_beyond_threshold(self):
        res = label_position(CALLOUTS, 50000, 50000, threshold=500)
        assert res["callout"] is None
        assert res["confidence"] == "none"

    def test_ambiguous_between_two(self):
        # Equidistant between A (0,0) and B (1000,0) -> ambiguous "between".
        res = label_position(CALLOUTS, 500, 0, threshold=2000)
        assert res["confidence"] == "ambiguous"
        assert res["between"] is not None

    def test_missing_coords_returns_none(self):
        res = label_position(CALLOUTS, None, None)
        assert res["callout"] is None
        assert res["confidence"] == "none"

    def test_skips_callouts_without_world(self):
        cs = [{"id": "x", "name": "X", "world": {}}, {"id": "a", "name": "A", "world": {"x": 0, "y": 0}}]
        res = label_position(cs, 10, 10, threshold=500)
        assert res["callout"]["id"] == "a"
