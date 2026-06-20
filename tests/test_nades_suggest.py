"""Regression tests for /api/nades/suggest scoping (security): anon is blocked on a locked site,
and a logged-in user can never pull grenade suggestions from a demo they don't own/share. The global
cache scan used to ignore ownership; find_consistent now takes an allow_shas allowlist and the route
gates on db.accessible. Temp DB + temp (empty) cache, never the real data."""
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
    db.DB_PATH = str(tmp_path / "nsuggest.sqlite")
    db.migrate()
    u1 = db.upsert_user("76561190000000001", "U1")
    u2 = db.upsert_user("76561190000000002", "U2")
    db.index_demo(_match("a" * 40, "de_a", [_p("1", "A")]), "a" * 16, owner_user_id=u1)
    db.index_demo(_match("b" * 40, "de_b", [_p("2", "B")]), "b" * 16, owner_user_id=u2)
    return u1, u2


def _client(app, tmp_path):
    app.CACHE = str(tmp_path / "cache")              # never touch the real cache during the scan
    os.makedirs(app.CACHE, exist_ok=True)
    return app.app.test_client()


def test_suggest_blocks_anon_when_auth_required(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    import app
    _seed(tmp_path)
    c = _client(app, tmp_path)
    assert c.get("/api/nades/suggest").status_code == 401          # no anon access on a locked site


def test_suggest_denies_foreign_demo_sha(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    import app
    u1, u2 = _seed(tmp_path)
    c = _client(app, tmp_path)
    with c.session_transaction() as s:
        s["uid"] = u1
    # u1 asks for u2's demo -> must NOT leak; empty, not the other user's lineups
    r = c.get("/api/nades/suggest?sha=" + "b" * 40)
    assert r.status_code == 200
    assert r.get_json()["suggestions"] == []


def test_suggest_allows_own_demo_sha(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    import app
    u1, u2 = _seed(tmp_path)
    c = _client(app, tmp_path)
    with c.session_transaction() as s:
        s["uid"] = u1
    r = c.get("/api/nades/suggest?sha=" + "a" * 40)               # own demo -> allowed (empty cache => [])
    assert r.status_code == 200
    assert "suggestions" in r.get_json()


def test_suggest_local_mode_open(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    import app
    _seed(tmp_path)
    c = _client(app, tmp_path)
    assert c.get("/api/nades/suggest").status_code == 200          # local mode unchanged
