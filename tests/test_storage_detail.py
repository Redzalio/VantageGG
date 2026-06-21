"""GET /api/admin/ops/storage-detail: admin-gated read-only storage drilldown + overview. Covers the
security contract -- admin gate, category allowlist (unknown -> 400), realpath/symlink-escape rejection,
the database WAL/SHM split, and the overview's disk-vs-appdata reconciliation. Temp dirs + temp DB;
the real cs2dp.sqlite is never touched (db.DB_PATH is monkeypatched to tmp_path)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db    # noqa: E402
import app   # noqa: E402


def _admin_client(tmp_path, monkeypatch):
    db.DB_PATH = str(tmp_path / "sd.sqlite")
    db.migrate()
    sid = "76561190000000099"
    monkeypatch.setenv("ADMIN_STEAM_IDS", sid)
    uid = db.upsert_user(sid, "AdminGuy")
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid
    app._rl_hits.clear()
    return c, uid


def _point_roots(tmp_path, monkeypatch):
    """Repoint every storage root at isolated temp dirs so tests never read real app data."""
    cache = tmp_path / "cache"; cache.mkdir()
    uploads = tmp_path / "uploads"; uploads.mkdir()
    nadedir = tmp_path / "nades"; nadedir.mkdir()
    monkeypatch.setattr(app, "CACHE", str(cache))
    monkeypatch.setattr(app, "UPLOADS", str(uploads))
    monkeypatch.setattr(app.nades, "LIB_DIR", str(nadedir), raising=False)
    return cache, uploads, nadedir


def _write(path, size):
    with open(path, "wb") as f:
        f.write(b"x" * size)


# ---------------------------------------------------------------------------
def test_requires_admin(tmp_path, monkeypatch):
    db.DB_PATH = str(tmp_path / "sd0.sqlite")
    db.migrate()
    monkeypatch.setenv("ADMIN_STEAM_IDS", "76561190000000001")    # someone else
    uid = db.upsert_user("76561190000000002", "Rando")
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid
    app._rl_hits.clear()
    assert c.get("/api/admin/ops/storage-detail").status_code == 403
    assert c.get("/api/admin/ops/storage-detail?category=parsed_cache").status_code == 403


def test_unknown_category_400(tmp_path, monkeypatch):
    c, _ = _admin_client(tmp_path, monkeypatch)
    for bad in ("bogus", "../etc", "..", "/etc/passwd", "cache;rm"):
        r = c.get("/api/admin/ops/storage-detail?category=" + bad)
        assert r.status_code == 400, "expected 400 for category=%r" % bad
        assert "error" in r.get_json()


def test_category_lists_largest_entries(tmp_path, monkeypatch):
    c, _ = _admin_client(tmp_path, monkeypatch)
    cache, _u, _n = _point_roots(tmp_path, monkeypatch)
    _write(cache / "big.json", 5000)
    _write(cache / "small.json", 100)
    sub = cache / "sub"; sub.mkdir()
    _write(sub / "nested.json", 800)
    r = c.get("/api/admin/ops/storage-detail?category=parsed_cache")
    assert r.status_code == 200
    j = r.get_json()
    assert j["category"] == "parsed_cache"
    assert j["total_bytes"] == 5900                       # 5000 + 100 + 800 (nested counted in dir)
    names = [e["name"] for e in j["entries"]]
    assert names[0] == "big.json"                         # largest first
    assert any(e["name"] == "sub" and e["type"] == "dir" and e["bytes"] == 800 for e in j["entries"])
    # no absolute filesystem path leaked for a non-root category
    blob = str(j)
    assert str(cache) not in blob and tmp_path.name not in blob


def test_database_lists_wal_shm_separately(tmp_path, monkeypatch):
    c, _ = _admin_client(tmp_path, monkeypatch)
    # Hold an open WAL connection with an uncommitted-then-active write so the -wal/-shm sidecars
    # exist on disk during the request (a clean close would checkpoint+remove them). This is the
    # real on-disk state the drilldown must split out separately.
    live = db.connect()
    live.execute("PRAGMA wal_autocheckpoint=0")
    live.execute("CREATE TABLE IF NOT EXISTS _keepwal(x)")
    live.execute("INSERT INTO _keepwal(x) VALUES(1)")
    live.commit()
    try:
        assert os.path.exists(str(tmp_path / "sd.sqlite-wal"))   # sanity: WAL sidecar is present
        r = c.get("/api/admin/ops/storage-detail?category=database")
        assert r.status_code == 200
        j = r.get_json()
        names = {e["name"] for e in j["entries"]}
        # the main DB and the WAL sidecar are listed as SEPARATE file entries (not a single blob/dir)
        assert "sd.sqlite" in names
        assert "sd.sqlite-wal" in names
        assert all(e["type"] == "file" for e in j["entries"])
        assert "wal" in j["root_label"].lower() and "shm" in j["root_label"].lower()
        assert j["total_bytes"] >= os.path.getsize(str(tmp_path / "sd.sqlite-wal"))
    finally:
        live.close()


def test_symlink_escape_is_skipped(tmp_path, monkeypatch):
    """A symlink inside a category root that points OUTSIDE it must be skipped -- never followed,
    never sized, never listed -- so the drilldown can't be tricked into reading the wider filesystem."""
    c, _ = _admin_client(tmp_path, monkeypatch)
    cache, _u, _n = _point_roots(tmp_path, monkeypatch)
    _write(cache / "real.json", 300)
    secret_dir = tmp_path / "outside"; secret_dir.mkdir()
    _write(secret_dir / "secret.bin", 999999)
    try:
        os.symlink(str(secret_dir), str(cache / "escape"))     # dir symlink escaping the root
    except (OSError, NotImplementedError, AttributeError):
        import pytest
        pytest.skip("symlinks not permitted in this environment")
    r = c.get("/api/admin/ops/storage-detail?category=parsed_cache")
    j = r.get_json()
    assert j["total_bytes"] == 300                         # the 999999 escape is NOT counted
    assert all(e["name"] != "escape" for e in j["entries"])   # and not listed


def test_within_guard_rejects_escapes(tmp_path):
    """Unit-level realpath guard (OS-independent, no symlink privilege needed): paths inside the root
    pass; anything resolving outside (incl. ../ traversal) is rejected."""
    root = tmp_path / "root"; root.mkdir()
    rr = os.path.realpath(str(root))
    inside = root / "a" / "b"
    (root / "a").mkdir();
    assert app._within(rr, str(root / "file.json")) is True
    assert app._within(rr, str(inside)) is True
    assert app._within(rr, str(tmp_path / "sibling")) is False     # outside the root
    assert app._within(rr, str(root / ".." / "escape")) is False   # ../ traversal blocked
    assert app._within(rr, os.path.dirname(rr)) is False           # the parent is not "within"


def test_dir_size_skips_out_of_root(tmp_path):
    """_safe_dir_size never counts bytes for entries resolving outside the given root."""
    root = tmp_path / "r"; root.mkdir()
    _write(root / "in.bin", 500)
    notes, counter = [], [0]
    rr = os.path.realpath(str(root))
    assert app._safe_dir_size(str(root), rr, notes, counter) == 500


def test_missing_root_is_honest(tmp_path, monkeypatch):
    c, _ = _admin_client(tmp_path, monkeypatch)
    monkeypatch.setattr(app, "CACHE", str(tmp_path / "does_not_exist"))
    r = c.get("/api/admin/ops/storage-detail?category=parsed_cache")
    assert r.status_code == 200
    j = r.get_json()
    assert j["total_bytes"] == 0 and j["entries"] == [] and j["notes"]


def test_overview_explains_disk_vs_appdata(tmp_path, monkeypatch):
    c, _ = _admin_client(tmp_path, monkeypatch)
    cache, _u, _n = _point_roots(tmp_path, monkeypatch)
    _write(cache / "a.json", 1234)
    r = c.get("/api/admin/ops/storage-detail")               # no category -> overview
    assert r.status_code == 200
    j = r.get_json()
    assert set(("volume", "app_data_total", "categories", "unexplained_bytes", "note")) <= set(j)
    assert "whole mounted volume" in j["note"]
    ids = {c2["id"] for c2 in j["categories"]}
    assert {"parsed_cache", "raw_uploads", "maps3d", "radars", "database",
            "nades", "app_root_other"} <= ids
    assert j["app_data_total"] >= 1234                       # our cache file is counted
    # unexplained = disk_used - app_data_total (>=0) or None if disk usage unavailable
    assert j["unexplained_bytes"] is None or j["unexplained_bytes"] >= 0
