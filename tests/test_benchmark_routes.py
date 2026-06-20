"""Route plumbing + gating for sidestats / benchmarks. The math is unit-tested in test_sidestats.py
and test_benchmarks.py; here we check scoping, the admin gate, and the no-fake-data 'unavailable' path."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db   # noqa: E402


def _seed(tmp_path):
    db.DB_PATH = str(tmp_path / "bench.sqlite")
    db.migrate()
    return db.upsert_user("76561190000000001", "U1")   # free, non-admin by default


def test_sidestats_local_mode_shape(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    import app
    monkeypatch.setattr(app, "CACHE", str(tmp_path / "cache"))      # empty -> no matches
    os.makedirs(str(tmp_path / "cache"), exist_ok=True)
    r = app.app.test_client().get("/api/sidestats")
    assert r.status_code == 200 and "maps" in r.get_json()


def test_benchmarks_compare_unavailable_without_bucket(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    import app
    r = app.app.test_client().get("/api/benchmarks/compare?type=premier_rating")
    assert r.status_code == 200 and r.get_json().get("available") is False   # no guessed numbers


def test_admin_benchmarks_blocks_non_admin(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    import app
    u1 = _seed(tmp_path)
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = u1
    assert c.get("/api/admin/benchmarks").status_code == 403
    assert c.post("/api/admin/benchmarks", json={"rows": [{}]}).status_code == 403


def test_admin_benchmarks_list_local(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)   # local = admin
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    import app
    r = app.app.test_client().get("/api/admin/benchmarks")
    assert r.status_code == 200 and "datasets" in r.get_json()
