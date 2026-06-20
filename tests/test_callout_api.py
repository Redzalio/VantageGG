"""Route tests for the callout API: public effective/coverage/label + gated admin editor CRUD."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db          # noqa: E402
import callouts    # noqa: E402
import callout_store  # noqa: E402

SEED = [
    {"id": "a_site", "name": "A Site", "aliases": ["BombsiteA", "A"], "world": {"x": 100, "y": 200}, "side": "both"},
    {"id": "b_site", "name": "B Site", "aliases": ["BombsiteB", "B"], "world": {"x": -500, "y": 300}, "side": "both"},
]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_STEAM_IDS", "76561198106326204")
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    import app
    db.DB_PATH = str(tmp_path / "co.sqlite")
    db.migrate()
    seed_dir = tmp_path / "callouts"
    seed_dir.mkdir()
    (seed_dir / "de_x.json").write_text(json.dumps({"map": "de_x", "callouts": SEED}), encoding="utf-8")
    monkeypatch.setattr(callouts, "_CALLOUT_DIR", seed_dir)
    callouts._cache.clear()
    callout_store._maps_cache = None
    c = app.app.test_client()
    admin = db.upsert_user("76561198106326204", "Redzalio")
    return c, admin


def _as_admin(c, admin):
    with c.session_transaction() as s:
        s["uid"] = admin


class TestPublicRoutes:
    def test_effective_callouts(self, client):
        c, _ = client
        r = c.get("/api/callouts/de_x")
        assert r.status_code == 200
        data = r.get_json()
        assert data["map"] == "de_x"
        assert {co["id"] for co in data["callouts"]} == {"a_site", "b_site"}

    def test_coverage(self, client):
        c, _ = client
        r = c.get("/api/callouts")
        assert r.status_code == 200
        cov = {m["map"]: m for m in r.get_json()["coverage"]}
        assert "de_x" in cov and cov["de_x"]["count"] == 2

    def test_label(self, client):
        c, _ = client
        r = c.get("/api/label/de_x?x=105&y=205")
        assert r.status_code == 200
        assert r.get_json()["id"] == "a_site"

    def test_label_requires_coords(self, client):
        c, _ = client
        assert c.get("/api/label/de_x").status_code == 400


class TestAdminGating:
    def test_anon_cannot_get_editor(self, client):
        c, _ = client
        assert c.get("/api/admin/callouts/de_x").status_code == 403

    def test_anon_cannot_save(self, client):
        c, _ = client
        assert c.post("/api/admin/callouts/de_x", json={"callouts": []}).status_code == 403

    def test_admin_can_get_editor(self, client):
        c, admin = client
        _as_admin(c, admin)
        r = c.get("/api/admin/callouts/de_x")
        assert r.status_code == 200
        d = r.get_json()
        assert d["map"] == "de_x" and "callouts" in d and "unmapped_learned" in d


class TestAdminSaveRevert:
    def test_save_then_effective_reflects_it(self, client):
        c, admin = client
        _as_admin(c, admin)
        custom = [{"id": "site_a", "name": "A reworked", "aliases": ["BombsiteA"], "side": "both",
                   "world": {"x": 1, "y": 2}, "boundary": [[0, 0], [10, 0], [10, 10]]}]
        r = c.post("/api/admin/callouts/de_x", json={"callouts": custom})
        assert r.status_code == 200 and r.get_json()["saved"] == 1
        eff = c.get("/api/callouts/de_x").get_json()["callouts"]
        assert len(eff) == 1 and eff[0]["id"] == "site_a" and eff[0]["boundary"]

    def test_save_requires_list(self, client):
        c, admin = client
        _as_admin(c, admin)
        assert c.post("/api/admin/callouts/de_x", json={"nope": 1}).status_code == 400

    def test_revert_restores_seed(self, client):
        c, admin = client
        _as_admin(c, admin)
        c.post("/api/admin/callouts/de_x", json={"callouts": [{"id": "z", "name": "Z", "world": {"x": 1, "y": 2}}]})
        assert len(c.get("/api/callouts/de_x").get_json()["callouts"]) == 1
        assert c.post("/api/admin/callouts/de_x/revert").status_code == 200
        assert len(c.get("/api/callouts/de_x").get_json()["callouts"]) == 2

    def test_ingest_runs(self, client):
        c, admin = client
        _as_admin(c, admin)
        r = c.post("/api/admin/callouts/de_x/ingest")
        assert r.status_code == 200
        assert "folded" in r.get_json()
