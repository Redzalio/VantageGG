"""Tests for callout_store.py (seed+override+learned merge) + db callout/sample functions + callout_learn."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import callouts
import callout_store
import callout_learn
import db


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Fresh temp DB + temp seed-callout dir, with caches cleared. Returns the seed dir."""
    db.DB_PATH = str(tmp_path / "callouts.sqlite")
    db.migrate()
    seed_dir = tmp_path / "callouts"
    seed_dir.mkdir()
    monkeypatch.setattr(callouts, "_CALLOUT_DIR", seed_dir)
    callouts._cache.clear()
    # point callout_store's maps.json cache at nothing predictable -> reset
    callout_store._maps_cache = None
    return seed_dir


def write_seed(seed_dir, map_name, callout_list):
    (seed_dir / f"{map_name}.json").write_text(
        json.dumps({"map": map_name, "callouts": callout_list}), encoding="utf-8")


SEED = [
    {"id": "a_site", "name": "A Site", "aliases": ["BombsiteA", "A"], "world": {"x": 100, "y": 200}, "side": "both"},
    {"id": "b_site", "name": "B Site", "aliases": ["BombsiteB", "B"], "world": {"x": -500, "y": 300}, "side": "both"},
    {"id": "mid", "name": "Mid", "aliases": ["Middle", "TopofMid"], "world": {"x": None, "y": None}, "side": "both"},
]


class TestEffectiveSeedOnly:
    def test_returns_seed_when_no_overrides(self, env):
        write_seed(env, "de_x", SEED)
        eff = callout_store.effective_callouts("de_x")
        assert len(eff) == 3
        assert {c["id"] for c in eff} == {"a_site", "b_site", "mid"}
        assert all(c["source"] == "seed" for c in eff)

    def test_mid_has_null_world_without_samples(self, env):
        write_seed(env, "de_x", SEED)
        mid = [c for c in callout_store.effective_callouts("de_x") if c["id"] == "mid"][0]
        assert mid["world"]["x"] is None


class TestLearnedFill:
    def test_learned_fills_missing_world(self, env):
        write_seed(env, "de_x", SEED)
        # Fold enough samples for the 'TopofMid' zone (alias of mid) to exceed MIN_LEARNED_FILL.
        agg = {"TopofMid": {"n": 20, "sum_x": 20 * 333, "sum_y": 20 * 444,
                            "min_x": 300, "min_y": 400, "max_x": 366, "max_y": 488}}
        db.fold_position_samples("sha_demo1", "de_x", agg)
        mid = [c for c in callout_store.effective_callouts("de_x") if c["id"] == "mid"][0]
        assert mid["world"]["x"] == pytest.approx(333, abs=1)
        assert mid["world"]["y"] == pytest.approx(444, abs=1)
        assert mid["source"] == "learned"
        assert mid["sample_n"] == 20

    def test_below_threshold_not_filled(self, env):
        write_seed(env, "de_x", SEED)
        agg = {"TopofMid": {"n": 3, "sum_x": 3 * 333, "sum_y": 3 * 444,
                            "min_x": 333, "min_y": 444, "max_x": 333, "max_y": 444}}
        db.fold_position_samples("sha_demo1", "de_x", agg)
        mid = [c for c in callout_store.effective_callouts("de_x") if c["id"] == "mid"][0]
        assert mid["world"]["x"] is None       # n=3 < MIN_LEARNED_FILL

    def test_fold_is_idempotent_per_sha(self, env):
        write_seed(env, "de_x", SEED)
        agg = {"TopofMid": {"n": 20, "sum_x": 20 * 333, "sum_y": 20 * 444,
                            "min_x": 300, "min_y": 400, "max_x": 366, "max_y": 488}}
        assert db.fold_position_samples("sha1", "de_x", agg) == 1
        assert db.fold_position_samples("sha1", "de_x", agg) == 0   # second time: no double-count
        learned = db.callout_learned("de_x")
        assert learned["TopofMid"]["n"] == 20


class TestOverrides:
    def test_overrides_take_over_map(self, env):
        write_seed(env, "de_x", SEED)
        custom = [{"id": "site_a", "name": "A", "aliases": ["BombsiteA"], "side": "both",
                   "world": {"x": 999, "y": 888}, "boundary": [[0, 0], [10, 0], [10, 10]]}]
        n = callout_store.save_map("de_x", custom, admin_uid=1)
        assert n == 1
        eff = callout_store.effective_callouts("de_x")
        assert len(eff) == 1                       # admin fully owns the map now
        assert eff[0]["id"] == "site_a"
        assert eff[0]["boundary"] == [[0, 0], [10, 0], [10, 10]]
        assert eff[0]["source"] == "admin"

    def test_revert_restores_seed(self, env):
        write_seed(env, "de_x", SEED)
        callout_store.save_map("de_x", [{"id": "z", "name": "Z", "world": {"x": 1, "y": 2}}], admin_uid=1)
        assert callout_store.effective_callouts("de_x")[0]["id"] == "z"
        callout_store.revert_map("de_x")
        assert len(callout_store.effective_callouts("de_x")) == 3   # back to seed

    def test_admin_coords_not_overwritten_by_learned(self, env):
        write_seed(env, "de_x", SEED)
        callout_store.save_map("de_x", [{"id": "mid", "name": "Mid", "aliases": ["TopofMid"],
                                         "world": {"x": 7, "y": 7}}], admin_uid=1)
        db.fold_position_samples("s", "de_x", {"TopofMid": {"n": 99, "sum_x": 99 * 333, "sum_y": 99 * 444,
                                                            "min_x": 1, "min_y": 1, "max_x": 1, "max_y": 1}})
        mid = callout_store.effective_callouts("de_x")[0]
        assert mid["world"]["x"] == 7              # admin wins over learned


class TestEditorData:
    def test_unmapped_learned_surfaced(self, env):
        write_seed(env, "de_x", SEED)
        # 'Catwalk' isn't an alias of any seed callout -> should appear as unmapped learned.
        db.fold_position_samples("s", "de_x", {"Catwalk": {"n": 15, "sum_x": 15 * 50, "sum_y": 15 * 60,
                                                           "min_x": 1, "min_y": 1, "max_x": 1, "max_y": 1}})
        ed = callout_store.editor_data("de_x")
        zones = {u["zone"] for u in ed["unmapped_learned"]}
        assert "Catwalk" in zones

    def test_matched_learned_attached(self, env):
        write_seed(env, "de_x", SEED)
        db.fold_position_samples("s", "de_x", {"BombsiteA": {"n": 12, "sum_x": 12 * 110, "sum_y": 12 * 210,
                                                            "min_x": 1, "min_y": 1, "max_x": 1, "max_y": 1}})
        ed = callout_store.editor_data("de_x")
        a = [c for c in ed["callouts"] if c["id"] == "a_site"][0]
        assert a["learned"] is not None
        assert a["learned"]["n"] == 12


class TestLabel:
    def test_label_uses_effective(self, env):
        write_seed(env, "de_x", SEED)
        res = callout_store.label("de_x", 105, 205, threshold=500)
        assert res["id"] == "a_site"
        assert res["confidence"] in ("nearest", "nearby", "ambiguous")


class TestDisplayName:
    def test_honors_admin_rename(self, env):
        write_seed(env, "de_x", SEED)
        callout_store.save_map("de_x", [{"id": "a_site", "name": "Bombsite Alpha",
                                         "aliases": ["BombsiteA"], "world": {"x": 1, "y": 2}}], admin_uid=1)
        assert callout_store.display_name("de_x", "BombsiteA") == "Bombsite Alpha"

    def test_humanize_fallback(self, env):
        write_seed(env, "de_x", SEED)
        assert "Long" in callout_store.display_name("de_x", "LongDoors")


class TestCoverage:
    def test_coverage_shape(self, env):
        write_seed(env, "de_x", SEED)
        cov = {c["map"]: c for c in callout_store.coverage()}
        assert "de_x" in cov
        assert cov["de_x"]["count"] == 3
        assert cov["de_x"]["with_world"] == 2       # mid has null coords
        assert cov["de_x"]["managed"] is False


class TestNadeMatching:
    def _co(self, **kw):
        base = {"id": "a_site", "name": "A Site", "aliases": ["BombsiteA"],
                "world": {"x": 0, "y": 0}, "boundary": None}
        base.update(kw)
        return base

    def test_match_by_target_callout_name(self):
        import nades
        lst = [{"name": "exec smoke", "target_callout": "BombsiteA", "land_pos": [9999, 9999, 0]}]
        m = nades.nades_for_callout(lst, self._co())
        assert len(m) == 1 and m[0][1] == "name"

    def test_match_by_land_pos_near_center(self):
        import nades
        lst = [{"name": "near", "target_callout": "", "land_pos": [100, 100, 0]}]
        m = nades.nades_for_callout(lst, self._co(world={"x": 0, "y": 0}), threshold=400)
        assert len(m) == 1 and m[0][1] == "near"

    def test_match_by_boundary_inside(self):
        import nades
        co = self._co(world={"x": 9000, "y": 9000}, boundary=[[0, 0], [200, 0], [200, 200], [0, 200]])
        lst = [{"name": "in", "target_callout": "", "land_pos": [100, 100, 0]}]
        m = nades.nades_for_callout(lst, co)
        assert len(m) == 1 and m[0][1] == "inside"

    def test_no_match_when_far_and_unlabelled(self):
        import nades
        lst = [{"name": "far", "target_callout": "", "land_pos": [5000, 5000, 0]}]
        m = nades.nades_for_callout(lst, self._co(world={"x": 0, "y": 0}), threshold=400)
        assert m == []


class TestCalloutLearn:
    def test_from_deaths_aggregates(self):
        D = [{"place": "BombsiteA", "vx": 100, "vy": 200},
             {"place": "BombsiteA", "vx": 110, "vy": 210},
             {"place": "Mid", "vx": -5, "vy": -5},
             {"place": "", "vx": 1, "vy": 1},            # skipped: no zone
             {"place": "Mid", "vx": None, "vy": 3}]      # skipped: no coord
        agg = callout_learn.from_deaths(D)
        assert agg["BombsiteA"]["n"] == 2
        assert agg["BombsiteA"]["sum_x"] == 210
        assert agg["BombsiteA"]["min_x"] == 100
        assert agg["BombsiteA"]["max_y"] == 210
        assert agg["Mid"]["n"] == 1
