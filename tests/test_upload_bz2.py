"""Bzip2 upload path (#19): Valve matchmaking demos download as .dem.bz2. The web tier saves the
.bz2 + enqueues; the WORKER bz2-decompresses to a byte-identical .dem before parsing. Temp dirs,
no real parsing (the parse fn is monkeypatched)."""
import bz2
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db        # noqa: E402
import library   # noqa: E402
import app       # noqa: E402


def test_is_bz2_helpers():
    assert library.is_bz2_name("match.dem.bz2") and library.is_bz2_name("X.BZ2")
    assert not library.is_bz2_name("match.dem") and not library.is_bz2_name("")
    assert library.strip_bz2("match.dem.bz2") == "match.dem"
    assert library.strip_bz2("match.dem") == "match.dem"        # no-op when not bzipped


def test_upload_route_accepts_bz2(tmp_path, monkeypatch):
    """POST a .dem.bz2 -> a job queues under the de-.bz2'd name, pointing at a .bz2 temp (web tier
    does NOT decompress)."""
    db.DB_PATH = str(tmp_path / "u.sqlite")
    db.migrate()
    monkeypatch.setattr(app, "UPLOADS", str(tmp_path))
    uid = db.upsert_user("76561198106326204", "Redzalio")
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid
    blob = bz2.compress(b"FAKE-DEM-BYTES" * 1000)
    r = c.post("/api/upload", data={"files": (io.BytesIO(blob), "match.dem.bz2")},
               content_type="multipart/form-data")
    assert r.status_code == 200
    jobs_out = r.get_json()["jobs"]
    assert len(jobs_out) == 1 and jobs_out[0]["ok"] and jobs_out[0]["filename"] == "match.dem"
    import jobs as jobs_mod
    j = jobs_mod.get_job(jobs_out[0]["id"])
    assert j["status"] == "queued" and j["upload_path"].endswith(".bz2")
    assert os.path.exists(j["upload_path"])


def test_worker_bunzips_to_identical_dem_and_cleans_up(tmp_path, monkeypatch):
    """The worker bz2-decompresses to a byte-identical .dem, then cleans up both temps."""
    monkeypatch.setattr(app, "UPLOADS", str(tmp_path))
    monkeypatch.setattr(app.jobs, "set_progress", lambda *a, **k: None)
    raw = b"DEMO\x00" + bytes(range(256)) * 200
    bz_path = os.path.join(str(tmp_path), "_jobup_x.dem.bz2")
    with bz2.open(bz_path, "wb") as f:
        f.write(raw)
    seen = {}

    def fake_parse(path, name, progress=None):
        with open(path, "rb") as fh:
            seen["bytes"] = fh.read()
        seen["path"] = path
        return {"source_sha1": "sha_bz2"}

    monkeypatch.setattr(app, "_parse_or_load_dem", fake_parse)
    monkeypatch.setattr(app, "_save_to_library", lambda *a, **k: {"ok": True})
    sha = app._process_upload_job(
        {"upload_path": bz_path, "filename": "match.dem", "id": "j1", "owner_user_id": None})
    assert sha == "sha_bz2"
    assert seen["bytes"] == raw                                  # decompressed losslessly
    assert seen["path"].endswith(".dem") and "_jobbz_" in os.path.basename(seen["path"])
    assert not os.path.exists(bz_path)                           # .bz2 temp cleaned
    assert not os.path.exists(seen["path"])                      # decompressed temp cleaned
