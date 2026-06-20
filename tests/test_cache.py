"""Tests for cache validation + atomic write (app.py helpers)."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app   # noqa: E402
from schema import ANALYTICS_VERSION, SCHEMA_VERSION   # noqa: E402


def test_replay_valid():
    assert app.replay_valid({"version": SCHEMA_VERSION, "frames": []}) is True
    assert app.replay_valid({"version": SCHEMA_VERSION - 1, "frames": []}) is False
    assert app.replay_valid({"version": SCHEMA_VERSION}) is False   # no frames
    assert app.replay_valid(None) is False
    assert app.replay_valid({}) is False


def test_analytics_valid():
    ok = {"version": SCHEMA_VERSION, "frames": [],
          "analytics": {"version": ANALYTICS_VERSION, "players": []}}
    assert app.analytics_valid(ok) is True
    stale = {"analytics": {"version": ANALYTICS_VERSION - 1}}
    assert app.analytics_valid(stale) is False
    assert app.analytics_valid({"analytics": None}) is False
    assert app.analytics_valid({}) is False


def test_load_cache_missing_and_corrupt(tmp_path):
    assert app.load_cache(str(tmp_path / "nope.json")) is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json")
    assert app.load_cache(str(bad)) is None
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"version": SCHEMA_VERSION, "frames": []}))
    assert app.load_cache(str(good)) == {"version": SCHEMA_VERSION, "frames": []}


def test_atomic_write_json_roundtrip(tmp_path):
    dst = str(tmp_path / "out.json")
    payload = {"version": SCHEMA_VERSION, "frames": [1, 2, 3], "x": "unicode"}
    app.atomic_write_json(dst, payload)
    assert json.load(open(dst, encoding="utf-8")) == payload
    # no leftover temp files in the dir
    leftovers = [f for f in os.listdir(tmp_path) if f.startswith(".tmp_")]
    assert leftovers == []


def test_sha1_file(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"hello world")
    # sha1("hello world") is stable + well-known
    assert app._sha1_file(str(f)) == "2aae6c35c94fcfb415dbe95f408b9ce91ee846ed"


# ---- cache sidecar metadata --------------------------------------------------
def test_meta_path_for():
    assert app.meta_path_for("/c/abc.json").endswith("abc.meta.json")
    assert app.meta_path_for("/c/abc").endswith("abc.meta.json")


def test_build_meta():
    data = {"version": SCHEMA_VERSION, "map": "de_dust2", "duration": 100.0,
            "rounds": [1, 2], "players": [1, 2, 3, 4, 5], "frames": [{}, {}],
            "analytics": {"version": ANALYTICS_VERSION}, "source_sha1": "abc"}
    m = app.build_meta(data)
    assert m["map"] == "de_dust2" and m["schema_version"] == SCHEMA_VERSION
    assert m["analytics_version"] == ANALYTICS_VERSION and m["has_analytics"] is True
    assert m["rounds"] == 2 and m["players"] == 5 and m["frames"] == 2
    assert m["source_sha1"] == "abc" and m["parse_status"] == "ok" and "created_at" in m


def test_build_meta_no_analytics():
    m = app.build_meta({"version": SCHEMA_VERSION, "analytics": None})
    assert m["has_analytics"] is False and m["analytics_version"] is None


# ---- mock stays in lock-step with the current schema -------------------------
def test_mockgen_build_data_current_schema():
    import mockgen
    d = mockgen.build_data()
    assert app.replay_valid(d) is True and d["version"] == SCHEMA_VERSION
    assert d["analytics"] is None and d["mock"] is True
    assert len(d["players"]) == 10 and len(d["rounds"]) >= 1 and len(d["frames"]) > 0


def test_mockgen_build_writes_to_path(tmp_path):
    import mockgen
    p = str(tmp_path / "s.json")
    mockgen.build(p)
    assert app.replay_valid(app.load_cache(p))


# ---- /api/sample is schema-aware --------------------------------------------
def test_sample_regenerates_when_missing(tmp_path, monkeypatch):
    import mockgen
    monkeypatch.setattr(app, "CACHE", str(tmp_path))
    orig = mockgen.build
    monkeypatch.setattr(mockgen, "build", lambda: orig(os.path.join(str(tmp_path), "sample.json")))
    r = app.app.test_client().get("/api/sample", headers={"Accept-Encoding": "identity"})
    assert r.status_code == 200
    data = json.loads(r.data)
    assert app.replay_valid(data) and data["version"] == SCHEMA_VERSION
    assert os.path.exists(os.path.join(str(tmp_path), "sample.meta.json"))


def test_sample_drops_stale_analytics(tmp_path, monkeypatch):
    # truly-stale analytics (no in-place migration path) is dropped so the UI shows the honest empty
    # state instead of wrong numbers. Force "unmigratable" by clearing the migration registry.
    monkeypatch.setattr(app, "CACHE", str(tmp_path))
    monkeypatch.setattr(app.analytics_migrations, "MIGRATIONS", {})
    p = os.path.join(str(tmp_path), "sample.json")
    app.atomic_write_json(p, {"version": SCHEMA_VERSION, "frames": [], "map": "de_x",
                              "rounds": [], "players": [],
                              "analytics": {"version": ANALYTICS_VERSION - 1}})
    r = app.app.test_client().get("/api/sample", headers={"Accept-Encoding": "identity"})
    data = json.loads(r.data)
    assert data["analytics"] is None and app.replay_valid(data)


def test_sample_migrates_stale_analytics(tmp_path, monkeypatch):
    # stale-BUT-migratable analytics is upgraded in place (.dem-free) instead of dropped, so old demos
    # keep coaching data + pick up new derivable fields (here: econ_verdict back-fill on round_cards).
    monkeypatch.setattr(app, "CACHE", str(tmp_path))
    p = os.path.join(str(tmp_path), "sample.json")
    app.atomic_write_json(p, {"version": SCHEMA_VERSION, "frames": [], "map": "de_x",
                              "rounds": [], "players": [],
                              "analytics": {"version": ANALYTICS_VERSION - 1,
                                            "round_cards": [{"round": 1, "winner": "CT",
                                                             "buy_ct": "full", "buy_t": "eco"}]}})
    r = app.app.test_client().get("/api/sample", headers={"Accept-Encoding": "identity"})
    data = json.loads(r.data)
    assert data["analytics"] is not None
    assert data["analytics"]["version"] == ANALYTICS_VERSION              # upgraded, not dropped
    assert data["analytics"]["round_cards"][0]["econ_verdict"] == "eco_loss"


def test_sample_serves_valid_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "CACHE", str(tmp_path))
    p = os.path.join(str(tmp_path), "sample.json")
    good = {"version": SCHEMA_VERSION, "frames": [], "map": "de_x", "rounds": [], "players": [],
            "analytics": {"version": ANALYTICS_VERSION, "players": []}}
    app.atomic_write_json(p, good)
    r = app.app.test_client().get("/api/sample", headers={"Accept-Encoding": "identity"})
    data = json.loads(r.data)
    assert data["analytics"]["version"] == ANALYTICS_VERSION   # left intact
    assert os.path.exists(os.path.join(str(tmp_path), "sample.meta.json"))   # sidecar backfilled
