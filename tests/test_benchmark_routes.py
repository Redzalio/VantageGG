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


# ---- manual CT/T benchmark entry (hand-typed, attributed, per-bucket file) ----
def test_manual_benchmark_blocks_non_admin(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    import app, benchmarks
    monkeypatch.setattr(benchmarks, "BENCHMARKS_DIR", str(tmp_path / "bm"))
    u1 = _seed(tmp_path)
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = u1
    assert c.get("/api/admin/benchmarks/manual?bucket=15k-20k").status_code == 403
    assert c.post("/api/admin/benchmarks/manual",
                  json={"bucket": "15k-20k", "rows": [{"map": "all", "ct": 52, "t": 48}]}).status_code == 403


def test_manual_benchmark_save_prefill_and_overall(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)   # local = admin
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    import app, benchmarks
    monkeypatch.setattr(benchmarks, "BENCHMARKS_DIR", str(tmp_path / "bm"))
    monkeypatch.setattr(app, "CACHE", str(tmp_path / "cache"))      # empty -> no matches, overall still works
    os.makedirs(str(tmp_path / "cache"), exist_ok=True)
    db.DB_PATH = str(tmp_path / "bench.sqlite"); db.migrate()
    c = app.app.test_client()
    body = {"bucket": "15k-20k", "region": "all", "source_name": "Leetify", "source_date": "2026-03-01",
            "rows": [{"map": "all", "ct": 53.1, "t": 46.9, "games": 1000},
                     {"map": "de_mirage", "ct": 55, "t": 45},
                     {"map": "de_nuke", "ct": "", "t": ""}]}      # nuke blank -> skipped, never a fake 0
    j = c.post("/api/admin/benchmarks/manual", json=body).get_json()
    assert j.get("ok") and j.get("records") == 2                  # all + mirage; blank nuke dropped

    g = c.get("/api/admin/benchmarks/manual?bucket=15k-20k&region=all").get_json()
    assert g["rows"]["all"]["ct"] == 53.1 and g["rows"]["de_mirage"]["ct"] == 55.0
    assert "de_nuke" not in g["rows"]

    s = c.get("/api/sidestats?bucket=15k-20k").get_json()         # overall flows into the Trends panel
    assert s.get("benchmark_available") is True
    assert s["benchmark"]["overall"]["ct_wr"] == 53.1            # the all-maps aggregate, not a per-map row


def test_manual_benchmark_replaces_bucket(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    import app, benchmarks
    monkeypatch.setattr(benchmarks, "BENCHMARKS_DIR", str(tmp_path / "bm"))
    db.DB_PATH = str(tmp_path / "bench.sqlite"); db.migrate()
    c = app.app.test_client()
    c.post("/api/admin/benchmarks/manual", json={"bucket": "15k-20k", "rows": [{"map": "all", "ct": 50, "t": 50}]})
    c.post("/api/admin/benchmarks/manual", json={"bucket": "15k-20k", "rows": [{"map": "all", "ct": 60, "t": 40}]})
    ds = [r for r in benchmarks.load_datasets() if r.get("bucket_type") == "premier_ct_t_side_winrates"]
    assert len(ds) == 1 and ds[0]["metrics"]["ct_win_rate"] == 60.0   # re-save replaced, did not append


def test_manual_benchmark_rejects_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    import app, benchmarks
    monkeypatch.setattr(benchmarks, "BENCHMARKS_DIR", str(tmp_path / "bm"))
    c = app.app.test_client()
    assert c.post("/api/admin/benchmarks/manual",
                  json={"rows": [{"map": "all", "ct": 50}]}).status_code == 400          # no bucket
    assert c.post("/api/admin/benchmarks/manual",
                  json={"bucket": "15k-20k", "rows": [{"map": "all"}]}).status_code == 400  # all blank -> nothing to save
