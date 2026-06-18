"""Tests for the nade library (normalize/add/import/delete/from_demo)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nades   # noqa: E402


def test_normalize_maps_csnades_fields():
    n = nades.normalize({"map": "de_dust2", "grenade": "Flash", "from": "T Spawn",
                         "to": "Xbox", "jumpthrow": True, "video": "http://x", "tags": "mid,exec"})
    assert n["type"] == "flash"
    assert n["throw_callout"] == "T Spawn" and n["target_callout"] == "Xbox"
    assert "jumpthrow" in n["technique"]
    assert n["video"] == "http://x"
    assert n["tags"] == ["mid", "exec"]
    assert n["id"].startswith("n_")


def test_normalize_bad_type_defaults_to_smoke():
    assert nades.normalize({"map": "de_x", "type": "banana"})["type"] == "smoke"


def test_add_import_delete(tmp_path, monkeypatch):
    monkeypatch.setattr(nades, "LIB_DIR", str(tmp_path))
    monkeypatch.setattr(nades, "LIB_PATH", str(tmp_path / "lib.json"))
    assert nades.load_library() == []
    n = nades.add_nade({"map": "de_x", "type": "smoke", "name": "A"})
    assert len(nades.load_library()) == 1
    added, total = nades.import_nades([{"map": "de_x", "type": "smoke", "name": "B"},
                                       {"map": "de_x", "type": "he", "name": "C"}])
    assert added == 2 and total == 3
    assert nades.delete_nade(n["id"]) == 1
    assert len(nades.load_library()) == 2


def test_update_nade_preserves_id_and_favorite(tmp_path, monkeypatch):
    monkeypatch.setattr(nades, "LIB_DIR", str(tmp_path))
    monkeypatch.setattr(nades, "LIB_PATH", str(tmp_path / "lib.json"))
    n = nades.add_nade({"map": "de_x", "type": "smoke", "name": "A", "target_callout": "Mid"})
    nid = n["id"]
    assert nades.set_favorite(nid, True) is True
    # editing name/callout keeps the SAME id (no orphan) and preserves the favorite flag
    u = nades.update_nade(nid, {"map": "de_x", "type": "smoke", "name": "A2",
                                "target_callout": "B site"})
    assert u and u["id"] == nid and u["name"] == "A2" and u["favorite"] is True
    lib = nades.load_library()
    assert len(lib) == 1 and lib[0]["name"] == "A2" and lib[0]["target_callout"] == "B site"
    assert nades.update_nade("n_nope", {"map": "de_x", "type": "smoke"}) is None


def test_set_favorite(tmp_path, monkeypatch):
    monkeypatch.setattr(nades, "LIB_DIR", str(tmp_path))
    monkeypatch.setattr(nades, "LIB_PATH", str(tmp_path / "lib.json"))
    n = nades.add_nade({"map": "de_x", "type": "smoke", "name": "A"})
    assert nades.set_favorite(n["id"], True) is True
    assert nades.load_library()[0]["favorite"] is True
    assert nades.set_favorite("n_missing", True) is False


def test_from_demo_dedupes_and_keeps_coords():
    replay = {"map": "de_x", "grenades": [
        {"type": "smoke", "round": 1, "pts": [[0, 0, 10], [100, 100, 5]]},
        {"type": "smoke", "round": 1, "pts": [[0, 0, 10], [105, 105, 5]]},   # near -> deduped
        {"type": "smoke", "round": 2, "pts": [[0, 0, 10], [900, 900, 5]]},   # far -> kept
    ]}
    smokes = [x for x in nades.from_demo(replay) if x["type"] == "smoke"]
    assert len(smokes) == 2
    assert all(x["land_pos"] and x["throw_pos"] for x in smokes)
    assert all(x["source"] == "demo" for x in smokes)
