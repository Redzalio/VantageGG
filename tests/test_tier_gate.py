"""Server-side Pro-tier enforcement (defense in depth). The frontend already hides Pro tools, but a
free account hitting a Pro endpoint directly must be refused server-side. Verified here because the
dev preview runs in open mode (tiers off) and can't exercise gating. Temp DB, no parsing."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db   # noqa: E402


def _p(sid, name):
    return {"steamid": sid, "name": name, "kills": 10, "deaths": 10, "kd": 1.0,
            "adr": 75, "kast": 70, "hltv": 1.0, "open_wr": 50, "traded_pct": 20, "udr": 20}


def _match(sha, mp, players):
    return {"source_sha1": sha, "map": mp, "version": 14, "duration": 1800.0,
            "rounds": [{"score_ct": 13, "score_t": 7}],
            "analytics": {"version": 9, "n_rounds": 20, "players": players}}


def _seed(tmp_path):
    db.DB_PATH = str(tmp_path / "tier.sqlite")
    db.migrate()
    u1 = db.upsert_user("76561190000000001", "U1")          # free by default
    db.index_demo(_match("a" * 40, "de_a", [_p("1", "A"), _p("2", "B")]), "a" * 16, owner_user_id=u1)
    return u1


def _client(app, tmp_path, monkeypatch, tiers=True):
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.setattr(app, "TIERS_ENABLED", tiers)
    u1 = _seed(tmp_path)
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = u1
    return c, u1


def test_compare_blocks_free_user(tmp_path, monkeypatch):
    import app
    c, _ = _client(app, tmp_path, monkeypatch, tiers=True)
    r = c.get("/api/compare?a=1&b=2")
    assert r.status_code == 402
    assert r.get_json().get("feature") == "advancedAnalytics"


def test_tendencies_blocks_free_user(tmp_path, monkeypatch):
    import app
    c, _ = _client(app, tmp_path, monkeypatch, tiers=True)
    assert c.get("/api/tendencies/1").status_code == 402


def test_pro_user_allowed(tmp_path, monkeypatch):
    import app
    c, u1 = _client(app, tmp_path, monkeypatch, tiers=True)
    db.set_user_tier(u1, "pro")                              # upgrade -> gate opens
    assert c.get("/api/compare?a=1&b=2").status_code == 200
    assert c.get("/api/tendencies/1").status_code == 200


def test_tiers_disabled_allows_everyone(tmp_path, monkeypatch):
    import app
    c, _ = _client(app, tmp_path, monkeypatch, tiers=False)  # tiers off -> no gating
    assert c.get("/api/compare?a=1&b=2").status_code == 200
    assert c.get("/api/tendencies/1").status_code == 200


def test_goals_blocked_free_user(tmp_path, monkeypatch):
    import app
    c, _ = _client(app, tmp_path, monkeypatch, tiers=True)
    assert c.get("/api/goals").status_code == 402            # Practice Goals is Pro


def test_glb_blocked_free_user(tmp_path, monkeypatch):
    import app
    c, _ = _client(app, tmp_path, monkeypatch, tiers=True)
    r = c.get("/static/maps3d/de_dust2.glb")                 # 3D geometry is Pro (gated before serving)
    assert r.status_code == 402


def test_glb_allowed_when_tiers_off(tmp_path, monkeypatch):
    import app
    c, _ = _client(app, tmp_path, monkeypatch, tiers=False)
    # tiers off -> gate is a no-op; file may not exist (404) but must NOT be 402
    assert c.get("/static/maps3d/de_dust2.glb").status_code != 402


def _sample_cache(app, tmp_path, monkeypatch, map_name="de_dust2"):
    cache = tmp_path / "cache"
    data = tmp_path / "data"
    cache.mkdir()
    data.mkdir()
    (cache / "sample.json").write_text(json.dumps({"map": map_name}), encoding="utf-8")
    monkeypatch.setattr(app, "CACHE", str(cache))
    monkeypatch.setattr(app, "DATA_DIR", str(data))


def test_sample_glb_allowed_for_public_preview(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    import app
    monkeypatch.setattr(app, "TIERS_ENABLED", True)
    _sample_cache(app, tmp_path, monkeypatch, "de_dust2")
    c = app.app.test_client()

    r = c.get("/static/maps3d/de_dust2_full.glb?sample=1")

    assert r.status_code != 402


def test_sample_preview_marker_does_not_unlock_other_maps(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    import app
    monkeypatch.setattr(app, "TIERS_ENABLED", True)
    _sample_cache(app, tmp_path, monkeypatch, "de_dust2")
    c = app.app.test_client()

    r = c.get("/static/maps3d/de_mirage_full.glb?sample=1")

    assert r.status_code == 402


def test_shared_store_write_blocked_anon_when_locked(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    import app
    monkeypatch.setattr(app, "TIERS_ENABLED", True)
    _seed(tmp_path)
    c = app.app.test_client()                                # no session = anon
    assert c.post("/api/nades", json={"map": "de_dust2", "type": "smoke"}).status_code == 401
    assert c.post("/api/team", json={}).status_code == 401
