"""Tests for analytics._attach_aim (counter-strafe % from per-shot velocity)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analytics   # noqa: E402


def _shots(player, n, vel):
    return [{"type": "shot", "player": player, "vel": vel} for _ in range(n)]


def test_counter_strafe_pct():
    players = [{"index": 0, "name": "A"}, {"index": 1, "name": "B"}]
    ev = (_shots(0, 8, 10.0)        # 8 stopped (< 85 u/s)
          + _shots(0, 2, 220.0)     # 2 running -> 8/10 = 80%
          + _shots(1, 3, 5.0))      # only 3 shots -> below MIN sample, skipped
    ev.append({"type": "shot", "player": 0})            # no vel (old demo) -> ignored
    ev.append({"type": "kill", "player": 0, "vel": 0})  # not a shot -> ignored
    analytics._attach_aim(players, {"events": ev})
    assert players[0]["counter_strafe"] == 80.0
    assert players[0]["shots"] == 10                    # the velocity-less shot isn't counted
    assert "counter_strafe" not in players[1]           # < 5 shots with velocity


def test_threshold_boundary():
    players = [{"index": 0}]
    ev = _shots(0, 5, analytics.COUNTER_STRAFE_STOP)     # exactly at the cap = NOT stopped (strict <)
    analytics._attach_aim(players, {"events": ev})
    assert players[0]["counter_strafe"] == 0.0


def test_no_shots_no_field():
    players = [{"index": 0}]
    analytics._attach_aim(players, {"events": []})
    assert "counter_strafe" not in players[0]
