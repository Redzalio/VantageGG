"""Tests for Steam OpenID login (steamauth.py) + the user-account DB helpers (db.py).

No network: verify() takes an `_opener` seam so the check_authentication round-trip is faked, and the
claimed_id / URL building / env gating are all pure. db user helpers run against a temp SQLite file."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db          # noqa: E402
import steamauth   # noqa: E402

_VALID_CLAIM = "https://steamcommunity.com/openid/id/76561198006409530"
_RETURN = "https://demos.example.com/auth/steam/callback"


def _id_res(**over):
    p = {"openid.mode": "id_res", "openid.claimed_id": _VALID_CLAIM,
         "openid.identity": _VALID_CLAIM, "openid.return_to": _RETURN,
         "openid.sig": "abc", "openid.signed": "signed,mode,claimed_id,identity,return_to"}
    p.update(over)
    return p


# ---- login URL + claimed-id parsing ----------------------------------------
def test_login_url_has_openid_params():
    url = steamauth.login_url("https://demos.example.com")
    assert url.startswith(steamauth.STEAM_OPENID_URL + "?")
    assert "openid.mode=checkid_setup" in url
    assert "openid.return_to=https%3A%2F%2Fdemos.example.com%2Fauth%2Fsteam%2Fcallback" in url
    assert "openid.realm=https%3A%2F%2Fdemos.example.com%2F" in url
    assert "identifier_select" in url


def test_login_url_strips_trailing_slash():
    url = steamauth.login_url("https://demos.example.com/")
    assert "demos.example.com%2F%2F" not in url        # no double slash from a trailing-/ base


def test_claimed_steamid_valid_and_invalid():
    assert steamauth._claimed_steamid(_VALID_CLAIM) == "76561198006409530"
    assert steamauth._claimed_steamid("https://steamcommunity.com/openid/id/123") is None   # too short
    assert steamauth._claimed_steamid("https://evil.example/openid/id/76561198006409530") is None
    assert steamauth._claimed_steamid(None) is None


# ---- verify() (check_authentication faked via _opener) ----------------------
def test_verify_ok_returns_steamid():
    sid = steamauth.verify(_id_res(), expected_return_prefix=_RETURN,
                           _opener=lambda url, data: "ns:...\nis_valid:true\n")
    assert sid == "76561198006409530"


def test_verify_rejects_when_steam_says_invalid():
    sid = steamauth.verify(_id_res(), expected_return_prefix=_RETURN,
                           _opener=lambda url, data: "ns:...\nis_valid:false\n")
    assert sid is None


def test_verify_rejects_non_id_res_mode():
    # user cancelled -> mode=cancel; must never reach Steam
    called = {"n": 0}

    def opener(url, data):
        called["n"] += 1
        return "is_valid:true"
    assert steamauth.verify(_id_res(**{"openid.mode": "cancel"}), _opener=opener) is None
    assert called["n"] == 0


def test_verify_rejects_bad_claimed_id():
    assert steamauth.verify(_id_res(**{"openid.claimed_id": "https://evil/x"}),
                            _opener=lambda u, d: "is_valid:true") is None


def test_verify_rejects_return_to_prefix_mismatch():
    sid = steamauth.verify(_id_res(), expected_return_prefix="https://attacker.example/auth/steam/callback",
                           _opener=lambda u, d: "is_valid:true")
    assert sid is None


def test_verify_opener_receives_check_authentication_mode():
    seen = {}

    def opener(url, data):
        seen["url"] = url
        seen["data"] = data.decode() if isinstance(data, bytes) else data
        return "is_valid:true"
    steamauth.verify(_id_res(), _opener=opener)
    assert seen["url"] == steamauth.STEAM_OPENID_URL
    assert "openid.mode=check_authentication" in seen["data"]
    assert "checkid_setup" not in seen["data"]          # original mode was flipped, not duplicated


# ---- env gating -------------------------------------------------------------
def test_auth_disabled_by_default(monkeypatch):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    assert steamauth.auth_enabled() is False
    assert steamauth.auth_required() is False


def test_auth_enabled_with_public_base_url(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://demos.example.com")
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    assert steamauth.auth_enabled() is True
    assert steamauth.auth_required() is False


def test_auth_required_flag(monkeypatch):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    assert steamauth.auth_enabled() is True
    assert steamauth.auth_required() is True


def test_public_base_url_prefers_env(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://proxy.example.com/")
    assert steamauth.public_base_url("http://127.0.0.1:8770/") == "https://proxy.example.com"
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    assert steamauth.public_base_url("http://127.0.0.1:8770/") == "http://127.0.0.1:8770"


def test_fetch_profile_no_key_returns_empty(monkeypatch):
    monkeypatch.delenv("STEAM_API_KEY", raising=False)
    assert steamauth.fetch_profile("76561198006409530") == {}


# ---- db user helpers --------------------------------------------------------
def _tmpdb(tmp_path):
    db.DB_PATH = str(tmp_path / "auth.sqlite")
    db.migrate()


def test_upsert_user_insert_then_get(tmp_path):
    _tmpdb(tmp_path)
    uid = db.upsert_user("76561198006409530", "Redzalio", "http://avatar/full.jpg")
    assert isinstance(uid, int)
    u = db.get_user(uid)
    assert u["steam_id_64"] == "76561198006409530" and u["name"] == "Redzalio"
    assert db.get_user_by_steamid("76561198006409530")["id"] == uid


def test_upsert_user_idempotent_same_id(tmp_path):
    _tmpdb(tmp_path)
    a = db.upsert_user("76561198006409530", "First")
    b = db.upsert_user("76561198006409530", "Second")        # same steamid -> same row
    assert a == b
    assert db.get_user(a)["name"] == "Second"


def test_upsert_user_keeps_name_when_login_has_none(tmp_path):
    _tmpdb(tmp_path)
    uid = db.upsert_user("76561198006409530", "Redzalio", "http://a/x.jpg")
    db.upsert_user("76561198006409530", None, None)          # e.g. later login with no STEAM_API_KEY
    u = db.get_user(uid)
    assert u["name"] == "Redzalio" and u["avatar"] == "http://a/x.jpg"   # COALESCE kept them


def test_get_user_none_for_missing(tmp_path):
    _tmpdb(tmp_path)
    assert db.get_user(None) is None
    assert db.get_user(99999) is None


# ---- route wiring (Flask test client) --------------------------------------
def test_api_me_local_mode(monkeypatch):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    import app                                            # worker NOT started on import (Stage 3 fix)
    me = app.app.test_client().get("/api/me").get_json()
    assert me["auth_enabled"] is False and me["authenticated"] is False
    assert me["user"]["local"] is True                  # synthetic local user, no DB hit


def test_login_steam_redirects_to_steam(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://demos.example.com")
    import app
    r = app.app.test_client().get("/login/steam")
    assert r.status_code in (301, 302)
    loc = r.headers["Location"]
    assert loc.startswith(steamauth.STEAM_OPENID_URL) and "checkid_setup" in loc
    assert "demos.example.com%2Fauth%2Fsteam%2Fcallback" in loc


def test_callback_rejects_forged_assertion(monkeypatch):
    # no real Steam round-trip -> verify() fails check_authentication -> 400, no session
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://demos.example.com")
    import app
    r = app.app.test_client().get("/auth/steam/callback?openid.mode=id_res"
                                  "&openid.claimed_id=https://steamcommunity.com/openid/id/76561198006409530")
    assert r.status_code == 400
