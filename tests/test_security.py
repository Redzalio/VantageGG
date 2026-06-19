"""Stage-1 security gates: anonymous requests blocked on a locked (AUTH_REQUIRED) site, baseline
security headers, and the nade video-URL allowlist. Temp DB; no real parsing."""
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db        # noqa: E402
import app       # noqa: E402
import nades     # noqa: E402


def _client(tmp_path):
    db.DB_PATH = str(tmp_path / "sec.sqlite")
    db.migrate()
    return app.app.test_client()


def test_anon_blocked_when_auth_required(tmp_path, monkeypatch):
    """With AUTH_REQUIRED=1 and no session, resource/data endpoints return 401 before doing work."""
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    c = _client(tmp_path)
    up = c.post("/api/upload", data={"files": (io.BytesIO(b"x"), "m.dem")},
                content_type="multipart/form-data")
    assert up.status_code == 401
    assert c.get("/api/jobs").status_code == 401
    assert c.get("/api/jobs/whatever").status_code == 401
    assert c.get("/nades/videos/x.mp4").status_code == 401
    assert c.post("/api/nades/video").status_code == 401


def test_anon_allowed_in_local_mode(tmp_path, monkeypatch):
    """Auth not required (local/open) -> the guard never blocks; upload with no files is a 400, not 401."""
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    c = _client(tmp_path)
    r = c.post("/api/upload")
    assert r.status_code != 401     # guard passed (400 = no files), local mode unaffected


def test_baseline_security_headers(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/me")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert "Referrer-Policy" in r.headers
    assert "Permissions-Policy" in r.headers


def test_safe_video_allowlist():
    keep = [
        "https://youtu.be/abc123",
        "https://www.youtube.com/watch?v=xyz",
        "https://youtube.com/embed/xyz",
        "/nades/videos/deadbeef00.mp4",
        "",
    ]
    drop = [
        "javascript:alert(1)",
        "data:text/html,<script>evil</script>",
        "blob:https://x/y",
        "file:///etc/passwd",
        "http://youtube.com/x",          # not https
        "https://evil.example.com/x",    # not an allowed host
        "//evil.example.com/x",          # protocol-relative
        "ftp://youtube.com/x",
    ]
    for u in keep:
        assert nades.safe_video(u) == u, u
    for u in drop:
        assert nades.safe_video(u) == "", u
    # normalize() applies it
    assert nades.normalize({"video": "javascript:alert(1)"})["video"] == ""
    assert nades.normalize({"video": "https://youtu.be/abc"})["video"] == "https://youtu.be/abc"


def test_logout_post_only(tmp_path):
    c = _client(tmp_path)
    assert c.get("/logout").status_code == 405      # GET no longer mutates (logout-CSRF closed)
    assert c.post("/logout").status_code == 200


def test_csrf_origin_guard(tmp_path, monkeypatch):
    """Logged-in state-changing requests need a same-origin Origin/Referer; webhook + no-session +
    local mode are exempt."""
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://vantagegg.com")
    db.DB_PATH = str(tmp_path / "csrf.sqlite")
    db.migrate()
    uid = db.upsert_user("76561190000000009", "U9")
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid
    good = {"Origin": "https://vantagegg.com"}
    # matching origin -> guard passes (handler runs; not 403)
    assert c.post("/api/account/name", json={"name": "Zed"}, headers=good).status_code != 403
    # foreign origin -> blocked
    assert c.post("/api/account/name", json={"name": "Zed"},
                  headers={"Origin": "https://evil.example.com"}).status_code == 403
    # missing origin/referer on a cookie session -> blocked
    assert c.post("/api/account/name", json={"name": "Zed"}).status_code == 403
    # the Stripe webhook is exempt even with a bad origin (signature is its auth) -> not 403
    assert c.post("/api/stripe/webhook", data=b"{}",
                  headers={"Origin": "https://evil.example.com", "Stripe-Signature": "x"}).status_code != 403


def test_csrf_skips_anon_and_local(tmp_path, monkeypatch):
    # no session -> no CSRF surface (handler may 401, but not a 403 from the guard)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://vantagegg.com")
    c = _client(tmp_path)
    assert c.post("/api/account/name", json={"name": "X"},
                  headers={"Origin": "https://evil.example.com"}).status_code != 403
    # local/open mode (no PUBLIC_BASE_URL) -> never enforced
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    uid = db.upsert_user("76561190000000010", "U10")
    c2 = app.app.test_client()
    with c2.session_transaction() as s:
        s["uid"] = uid
    assert c2.post("/api/account/name", json={"name": "X"},
                   headers={"Origin": "https://evil.example.com"}).status_code != 403


def test_validate_prod_config(monkeypatch):
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    app._validate_prod_config()                      # not production -> no-op
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://x")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "1")
    monkeypatch.setenv("ADMIN_STEAM_IDS", "123")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(SystemExit):                  # production + missing SECRET_KEY -> refuse to start
        app._validate_prod_config()
    monkeypatch.setenv("SECRET_KEY", "abc")
    app._validate_prod_config()                      # fully configured -> passes


def test_rate_limit_429(tmp_path):
    c = _client(tmp_path)
    app._rl_hits.clear()
    xff = {"X-Forwarded-For": "203.0.113.7"}         # unique bucket; /login/steam is cheap (a redirect)
    statuses = [c.get("/login/steam", headers=xff).status_code for _ in range(31)]
    assert 429 not in statuses[:10]                  # first requests allowed
    assert statuses[-1] == 429                        # 30/min window exceeded -> throttled


def test_active_job_cap_429(tmp_path, monkeypatch):
    import jobs as jobs_mod
    db.DB_PATH = str(tmp_path / "cap.sqlite")
    db.migrate()
    uid = db.upsert_user("76561190000000011", "U11")
    monkeypatch.setattr(jobs_mod, "count_active", lambda owner=None: app.MAX_ACTIVE_JOBS)
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid
    app._rl_hits.clear()
    r = c.post("/api/upload", data={"files": (io.BytesIO(b"x"), "m.dem")},
               content_type="multipart/form-data")
    assert r.status_code == 429


def test_disk_full_guard_507(tmp_path, monkeypatch):
    import jobs as jobs_mod
    db.DB_PATH = str(tmp_path / "disk.sqlite")
    db.migrate()
    uid = db.upsert_user("76561190000000012", "U12")
    monkeypatch.setattr(jobs_mod, "count_active", lambda owner=None: 0)
    monkeypatch.setattr(app.shutil, "disk_usage",
                        lambda p: type("U", (), {"free": 0, "total": 1, "used": 1})())
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid
    app._rl_hits.clear()
    r = c.post("/api/upload", data={"files": (io.BytesIO(b"x"), "m.dem")},
               content_type="multipart/form-data")
    assert r.status_code == 507
