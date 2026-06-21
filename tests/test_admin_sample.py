"""Admin-managed sample-demo replacement (samplemgr.py + /api/admin/sample* routes).

Covers the endpoint contract + the safety rules:
  * GET/POST are admin-gated server-side (401 anon, 403 non-admin).
  * An INVALID upload (no real analytics) does NOT replace the current sample and returns ok:false.
  * A VALID upload atomically replaces the sample (status reflects new map/rounds; analytics_valid).
  * revert returns to the bundled sample.
  * /api/sample prefers the admin sample when valid; falls back to bundled when it's missing/invalid.
  * rebuild re-parses the retained raw .dem; errors clearly when no raw is stored.

No real .dem and no real cs2dp.sqlite/cache: db.DB_PATH + app.DATA_DIR + app.CACHE are pointed at
tmp_path, and the trusted parse path (app._parse_sample_dem) is monkeypatched to return known
good/bad parsed dicts. The bundled-sample fallback is exercised by writing a cache/sample.json that
already passes replay+analytics validation."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db          # noqa: E402
import app         # noqa: E402
import samplemgr   # noqa: E402
from schema import ANALYTICS_VERSION, SCHEMA_VERSION   # noqa: E402


# ---------------------------------------------------------------------------
def _good_parsed(map_name="de_dust2", rounds=24, score=("13", "11")):
    """A parsed dict that passes replay_valid + analytics_valid + the sample gate."""
    rs = [{"num": i + 1, "score_ct": 0, "score_t": 0} for i in range(rounds)]
    if rs:
        rs[-1]["score_ct"], rs[-1]["score_t"] = int(score[0]), int(score[1])
    return {
        "version": SCHEMA_VERSION,
        "map": map_name,
        "frames": [{"players": [{"steamid": "1", "duck": 0}]}],
        "rounds": rs,
        "players": [{"steamid": "1", "name": "A"}, {"steamid": "2", "name": "B"}],
        "duration": 1800,
        "source_sha1": "deadbeef" * 5,
        "analytics": {
            "version": ANALYTICS_VERSION,
            "players": [{"steamid": "1"}, {"steamid": "2"}],
            "rounds": [{"num": 1}],
            "round_cards": [{"num": 1}],
            "position_samples": [[0, 0]],
            "team_play": {"x": 1},
            "insights": [{"type": "good_openings"}],
            "have_econ": True,
        },
    }


def _bad_parsed():
    """Replay is fine but analytics did NOT compute (the fallback state) -> must be rejected."""
    d = _good_parsed(map_name="de_inferno", rounds=20)
    d["analytics"] = None
    return d


def _admin_client(tmp_path, monkeypatch, *, admin=True):
    db.DB_PATH = str(tmp_path / "as.sqlite")
    db.migrate()
    sample_root = tmp_path / "data"
    sample_root.mkdir(exist_ok=True)
    cache = tmp_path / "cache"
    cache.mkdir(exist_ok=True)
    monkeypatch.setattr(app, "DATA_DIR", str(sample_root))
    monkeypatch.setattr(app, "CACHE", str(cache))
    admin_sid = "76561190000000099"
    monkeypatch.setenv("ADMIN_STEAM_IDS", admin_sid)
    if admin:
        uid = db.upsert_user(admin_sid, "AdminGuy")
    else:
        uid = db.upsert_user("76561190000000002", "Rando")   # not in ADMIN_STEAM_IDS
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid
    app._rl_hits.clear()
    return c, str(cache), str(sample_root)


def _write_bundled(cache_dir):
    """A bundled cache/sample.json that already passes validation, for the fallback case."""
    with open(os.path.join(cache_dir, "sample.json"), "w", encoding="utf-8") as fh:
        json.dump(_good_parsed(map_name="de_mirage", rounds=16), fh)


# ---- auth gating -----------------------------------------------------------
def test_get_rejects_anonymous(tmp_path, monkeypatch):
    db.DB_PATH = str(tmp_path / "as0.sqlite")
    db.migrate()
    monkeypatch.setenv("ADMIN_STEAM_IDS", "76561190000000099")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.test")   # auth-enabled -> anon is None
    c = app.app.test_client()
    app._rl_hits.clear()
    r = c.get("/api/admin/sample")
    assert r.status_code == 401


def test_get_rejects_non_admin(tmp_path, monkeypatch):
    c, _cache, _root = _admin_client(tmp_path, monkeypatch, admin=False)
    r = c.get("/api/admin/sample")
    assert r.status_code == 403


def test_post_rejects_non_admin(tmp_path, monkeypatch):
    import io
    c, _cache, _root = _admin_client(tmp_path, monkeypatch, admin=False)
    r = c.post("/api/admin/sample",
               data={"demo": (io.BytesIO(b"not-a-real-dem"), "x.dem")},
               content_type="multipart/form-data")
    assert r.status_code == 403


def test_rebuild_revert_reject_non_admin(tmp_path, monkeypatch):
    c, _cache, _root = _admin_client(tmp_path, monkeypatch, admin=False)
    assert c.post("/api/admin/sample/rebuild").status_code == 403
    assert c.post("/api/admin/sample/revert").status_code == 403


# ---- upload: invalid does not replace; valid replaces ----------------------
def _upload(c, monkeypatch, parsed):
    import io
    monkeypatch.setattr(app, "_parse_sample_dem", lambda path: parsed)
    return c.post("/api/admin/sample",
                  data={"demo": (io.BytesIO(b"fake-dem-bytes"), "match.dem")},
                  content_type="multipart/form-data")


def test_invalid_upload_does_not_replace(tmp_path, monkeypatch):
    c, _cache, root = _admin_client(tmp_path, monkeypatch)
    # First install a GOOD sample so there's a "previous sample" to protect.
    assert _upload(c, monkeypatch, _good_parsed(map_name="de_nuke", rounds=22)).status_code == 200
    before = open(samplemgr.current_json_path(root), encoding="utf-8").read()

    # Now a BAD upload (no analytics) must be rejected AND leave the old sample untouched.
    r = _upload(c, monkeypatch, _bad_parsed())
    assert r.status_code == 400
    j = r.get_json()
    assert j["ok"] is False and j["error"]
    after = open(samplemgr.current_json_path(root), encoding="utf-8").read()
    assert after == before                                  # previous sample preserved byte-for-byte
    # GET still reports the original good sample
    st = c.get("/api/admin/sample").get_json()
    assert st["source"] == "admin" and st["map"] == "de_nuke" and st["analytics_valid"] is True


def test_valid_upload_replaces(tmp_path, monkeypatch):
    c, _cache, root = _admin_client(tmp_path, monkeypatch)
    r = _upload(c, monkeypatch, _good_parsed(map_name="de_ancient", rounds=18, score=("13", "5")))
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] is True and "status" in j
    st = j["status"]
    assert st["source"] == "admin"
    assert st["map"] == "de_ancient"
    assert st["rounds"] == 18
    assert st["replay_valid"] is True and st["analytics_valid"] is True and st["has_analytics"] is True
    assert st["score"] == {"ct": 13, "t": 5}
    assert st["original_filename"] == "match.dem"
    assert st["schema_version"] == SCHEMA_VERSION and st["analytics_version"] == ANALYTICS_VERSION
    assert st["raw_retained"] is True                       # raw .dem retained for rebuild
    assert st["weak_features"] == []                        # the good parsed dict is rich
    # the parsed JSON + metadata + retained raw all exist on the data volume
    assert os.path.exists(samplemgr.current_json_path(root))
    assert os.path.exists(samplemgr.current_meta_path(root))
    assert os.path.exists(samplemgr.current_raw_path(root))


# ---- /api/sample prefers admin when valid, else bundled --------------------
def test_api_sample_prefers_admin_then_falls_back(tmp_path, monkeypatch):
    c, cache, root = _admin_client(tmp_path, monkeypatch)
    _write_bundled(cache)                                    # bundled = de_mirage

    # No admin sample yet -> /api/sample serves the bundled one.
    r = c.get("/api/sample")
    assert r.status_code == 200 and r.get_json()["map"] == "de_mirage"

    # Install an admin sample -> /api/sample now prefers it.
    assert _upload(c, monkeypatch, _good_parsed(map_name="de_vertigo", rounds=20)).status_code == 200
    assert c.get("/api/sample").get_json()["map"] == "de_vertigo"

    # Revert -> back to the bundled sample.
    rr = c.post("/api/admin/sample/revert")
    assert rr.status_code == 200 and rr.get_json()["ok"] is True
    assert rr.get_json()["status"]["source"] == "bundled"
    assert c.get("/api/sample").get_json()["map"] == "de_mirage"
    assert not os.path.exists(samplemgr.current_json_path(root))   # admin sample dropped


def test_api_sample_ignores_invalid_admin_sample(tmp_path, monkeypatch):
    """If a stale admin sample is on disk but no longer valid, /api/sample must fall back to bundled
    and GET status must warn (source=bundled), never serve wrong numbers."""
    c, cache, root = _admin_client(tmp_path, monkeypatch)
    _write_bundled(cache)
    # Hand-write an admin current.json that is schema-stale (invalid) -- simulating a version bump.
    samplemgr.ensure_dirs(root)
    stale = _good_parsed(map_name="de_cache", rounds=30)
    stale["version"] = SCHEMA_VERSION + 999                  # now replay_valid() is False
    with open(samplemgr.current_json_path(root), "w", encoding="utf-8") as fh:
        json.dump(stale, fh)

    # /api/sample falls back to bundled
    assert c.get("/api/sample").get_json()["map"] == "de_mirage"
    # status reports bundled + a warning
    st = c.get("/api/admin/sample").get_json()
    assert st["source"] == "bundled"
    assert st["replay_valid"] is False
    assert st["warning"]


# ---- rebuild ---------------------------------------------------------------
def test_rebuild_without_raw_errors(tmp_path, monkeypatch):
    c, _cache, root = _admin_client(tmp_path, monkeypatch)
    samplemgr.ensure_dirs(root)
    # current.json present but NO retained raw .dem
    with open(samplemgr.current_json_path(root), "w", encoding="utf-8") as fh:
        json.dump(_good_parsed(), fh)
    r = c.post("/api/admin/sample/rebuild")
    assert r.status_code == 400
    j = r.get_json()
    assert j["ok"] is False
    assert j["error"] == "Raw demo not stored. Upload a new sample demo to regenerate analytics."


def test_rebuild_reparses_retained_raw(tmp_path, monkeypatch):
    c, _cache, root = _admin_client(tmp_path, monkeypatch)
    # Install a good sample (retains a raw .dem).
    assert _upload(c, monkeypatch, _good_parsed(map_name="de_overpass", rounds=24)).status_code == 200
    assert os.path.exists(samplemgr.current_raw_path(root))

    # Rebuild re-parses the retained raw; the (fake) parser now yields a different map.
    monkeypatch.setattr(app, "_parse_sample_dem", lambda path: _good_parsed(map_name="de_train", rounds=26))
    r = c.post("/api/admin/sample/rebuild")
    assert r.status_code == 200
    st = r.get_json()["status"]
    assert st["source"] == "admin" and st["map"] == "de_train" and st["rounds"] == 26
    assert st["analytics_valid"] is True


# ---- samplemgr unit: validation rejects fallbacks --------------------------
def test_validate_parsed_rejects_missing_analytics():
    ok, reason = samplemgr.validate_parsed(_bad_parsed(), app.replay_valid, app.analytics_valid)
    assert ok is False and "Analytics" in reason


def test_validate_parsed_accepts_good():
    ok, reason = samplemgr.validate_parsed(_good_parsed(), app.replay_valid, app.analytics_valid)
    assert ok is True and reason is None


def test_install_failure_keeps_old_sample(tmp_path):
    """Unit-level: installing a bad parsed dict over a good one leaves the good one in place."""
    root = str(tmp_path / "data")
    os.makedirs(root, exist_ok=True)
    ok, _ = samplemgr.install_parsed(root, _good_parsed(map_name="de_dust2"),
                                     app.replay_valid, app.analytics_valid)
    assert ok is True
    before = open(samplemgr.current_json_path(root), encoding="utf-8").read()
    ok2, reason = samplemgr.install_parsed(root, _bad_parsed(), app.replay_valid, app.analytics_valid)
    assert ok2 is False and reason
    after = open(samplemgr.current_json_path(root), encoding="utf-8").read()
    assert after == before
