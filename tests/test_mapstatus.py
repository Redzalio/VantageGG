"""Tests for the 3D-asset map_status helper (stdlib-only, never raises)."""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mapstatus   # noqa: E402


def _write(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def _build_fake_maps3d(tmp_path):
    """de_a=verified+geometry, de_b=geometry-missing, de_c=transform-missing."""
    d = str(tmp_path)
    _write(os.path.join(d, "transforms.json"), {
        "de_a": {"rotationDeg": 90, "glb": "de_a_full.glb", "verified": True,
                 "validation": {"ct": {"n": 5, "miss": 0}}},
        "de_b": {"rotationDeg": 90, "glb": "de_b_full.glb", "verified": True},
    })
    with open(os.path.join(d, "de_a_full.glb"), "wb") as f:
        f.write(b"glTF\x00\x00\x00\x00")
    _write(os.path.join(d, "de_a_anchors.json"),
           {"map": "de_a", "ct_spawns": [[0, 0, 0], [1, 1, 1]],
            "t_spawns": [[2, 2, 2], [3, 3, 3], [4, 4, 4]]})
    # de_b's glb is intentionally NOT created -> geometry-missing
    # de_c has a stray glb with no transforms entry -> transform-missing
    with open(os.path.join(d, "de_c_full.glb"), "wb") as f:
        f.write(b"glTF\x00\x00\x00\x00")
    return d


def test_map_status_classifies_each_case(tmp_path):
    d = _build_fake_maps3d(tmp_path)
    result = mapstatus.map_status(d)
    by_map = {m["map"]: m for m in result["maps"]}

    a = by_map["de_a"]
    assert a["status"] == "verified"
    assert a["glb_present"] is True
    assert isinstance(a["glb_mb"], float)
    assert a["spawns"] == 5
    assert a["rotation"] == 90
    assert a["anchors_present"] is True
    assert isinstance(a["validation"], dict)

    b = by_map["de_b"]
    assert b["status"] == "geometry-missing"
    assert b["glb_present"] is False
    assert b["glb_mb"] is None
    assert b["spawns"] is None

    c = by_map["de_c"]
    assert c["status"] == "transform-missing"
    assert c["glb_present"] is True
    assert c["verified"] is False
    assert c["validation"] is None

    assert result["summary"] == {"total": 3, "verified": 1, "with_geometry": 2}
    # maps are sorted by name
    assert [m["map"] for m in result["maps"]] == ["de_a", "de_b", "de_c"]
    assert result["maps3d_dir"] == d


def test_no_transforms_file_still_works(tmp_path):
    d = str(tmp_path)
    # No transforms.json at all; just a stray geometry file.
    with open(os.path.join(d, "de_solo_full.glb"), "wb") as f:
        f.write(b"glTF")
    result = mapstatus.map_status(d)
    by_map = {m["map"]: m for m in result["maps"]}
    assert list(by_map) == ["de_solo"]
    assert by_map["de_solo"]["status"] == "transform-missing"
    assert by_map["de_solo"]["glb_present"] is True
    assert result["summary"] == {"total": 1, "verified": 0, "with_geometry": 1}


def test_missing_dir_does_not_raise(tmp_path):
    result = mapstatus.map_status(os.path.join(str(tmp_path), "nope"))
    assert result["maps"] == []
    assert result["summary"] == {"total": 0, "verified": 0, "with_geometry": 0}
