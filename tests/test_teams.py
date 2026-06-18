"""Tests for the local team config library (load/normalize/save/role_of)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import teams   # noqa: E402


def test_load_missing_returns_default_shape(tmp_path):
    p = str(tmp_path / "team.json")
    cfg = teams.load_team(path=p)
    assert cfg["name"] == "" and cfg["players"] == []
    assert cfg == teams.DEFAULT


def test_save_normalizes_and_round_trips(tmp_path):
    p = str(tmp_path / "team.json")
    players = [
        {"steamid": "S_valid", "name": "Alice", "role": "entry"},
        {"steamid": "S_badrole", "name": "Bob", "role": "banana"},  # role -> ""
        {"steamid": "   ", "name": "Ghost", "role": "support"},     # blank steamid -> dropped
    ]
    # 11 more valid players so total valid = 13 -> capped to 10
    players += [{"steamid": "extra_%d" % i, "name": "P%d" % i, "role": "rotator"} for i in range(11)]

    saved = teams.save_team(
        {"name": "X" * 100, "players": players, "preferred_maps": ["de_dust2", "  ", "de_mirage"],
         "notes": "scrim notes"},
        path=p,
    )

    # name truncated 100 -> 80
    assert len(saved["name"]) == 80 and saved["name"] == "X" * 80
    # capped at 10
    assert len(saved["players"]) == 10
    # first kept player keeps its valid role
    assert saved["players"][0] == {"steamid": "S_valid", "name": "Alice", "role": "entry"}
    # bad role normalized to ""
    assert saved["players"][1]["steamid"] == "S_badrole" and saved["players"][1]["role"] == ""
    # blank-steamid player dropped (no such steamid survives)
    assert all(pl["steamid"].strip() for pl in saved["players"])
    assert "Ghost" not in [pl["name"] for pl in saved["players"]]
    # preferred_maps stripped of blanks
    assert saved["preferred_maps"] == ["de_dust2", "de_mirage"]
    assert saved["notes"] == "scrim notes"

    # round-trip: reloading equals the saved normalized config
    assert teams.load_team(path=p) == saved


def test_role_of(tmp_path):
    p = str(tmp_path / "team.json")
    teams.save_team(
        {"name": "T", "players": [{"steamid": "76561198000000000", "name": "Awp", "role": "awper"}]},
        path=p,
    )
    assert teams.role_of("76561198000000000", path=p) == "awper"
    assert teams.role_of("nope", path=p) == ""
