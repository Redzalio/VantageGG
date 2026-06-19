"""Client-side-gzip upload path: browsers gzip the .dem before upload (~1.5x smaller); the web tier
saves the .gz + enqueues, and the WORKER gunzips to a byte-identical .dem before parsing. Temp dirs,
no real parsing (the parse fn is monkeypatched)."""
import gzip
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db        # noqa: E402
import library   # noqa: E402
import app       # noqa: E402


def test_is_gz_helpers():
    assert library.is_gz_name("match.dem.gz") and library.is_gz_name("X.GZ")
    assert not library.is_gz_name("match.dem") and not library.is_gz_name("")
    assert library.strip_gz("match.dem.gz") == "match.dem"
    assert library.strip_gz("match.dem") == "match.dem"      # no-op when not gzipped


def test_upload_route_accepts_gz(tmp_path, monkeypatch):
    """POST a gzipped demo -> a job is queued under the de-.gz'd display name, pointing at a .gz temp
    (the web tier does NOT decompress)."""
    db.DB_PATH = str(tmp_path / "u.sqlite")
    db.migrate()
    monkeypatch.setattr(app, "UPLOADS", str(tmp_path))
    uid = db.upsert_user("76561198106326204", "Redzalio")
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid
    gz = gzip.compress(b"FAKE-DEM-BYTES" * 1000)
    r = c.post("/api/upload", data={"files": (io.BytesIO(gz), "match.dem.gz")},
               content_type="multipart/form-data")
    assert r.status_code == 200
    jobs_out = r.get_json()["jobs"]
    assert len(jobs_out) == 1 and jobs_out[0]["ok"] and jobs_out[0]["filename"] == "match.dem"
    import jobs as jobs_mod
    j = jobs_mod.get_job(jobs_out[0]["id"])
    assert j["status"] == "queued" and j["upload_path"].endswith(".gz")
    assert os.path.exists(j["upload_path"])                  # the .gz is persisted for the worker


def test_worker_gunzips_to_identical_dem_and_cleans_up(tmp_path, monkeypatch):
    """The worker decompresses a .gz upload to a byte-identical .dem (so the content-hash cache key is
    unchanged) before parsing, then cleans up both temps."""
    monkeypatch.setattr(app, "UPLOADS", str(tmp_path))
    monkeypatch.setattr(app.jobs, "set_progress", lambda *a, **k: None)
    raw = b"DEMO\x00" + bytes(range(256)) * 200            # arbitrary "demo" bytes
    gz_path = os.path.join(str(tmp_path), "_jobup_x.dem.gz")
    with gzip.open(gz_path, "wb") as f:
        f.write(raw)
    seen = {}

    def fake_parse(path, name, progress=None):
        with open(path, "rb") as fh:
            seen["bytes"] = fh.read()
        seen["path"] = path
        return {"source_sha1": "sha_test"}

    monkeypatch.setattr(app, "_parse_or_load_dem", fake_parse)
    monkeypatch.setattr(app, "_save_to_library", lambda *a, **k: {"ok": True})
    sha = app._process_upload_job(
        {"upload_path": gz_path, "filename": "match.dem", "id": "j1", "owner_user_id": None})
    assert sha == "sha_test"
    assert seen["bytes"] == raw                              # decompressed losslessly
    assert seen["path"].endswith(".dem") and "_jobgz_" in os.path.basename(seen["path"])
    assert not os.path.exists(gz_path)                       # .gz temp cleaned
    assert not os.path.exists(seen["path"])                  # decompressed temp cleaned
