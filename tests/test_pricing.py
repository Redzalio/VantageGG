"""Pricing module + admin pricing endpoints: defaults, per-month/savings derivation, persistence,
and the gated GET/POST /api/admin/pricing (anon/non-admin/helper -> 403, admin -> 200 + persists)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db        # noqa: E402
import pricing   # noqa: E402


def _iso(p):
    return {x["key"]: x for x in p}


def test_defaults_and_compute(tmp_path, monkeypatch):
    monkeypatch.setattr(pricing, "CONFIG_PATH", str(tmp_path / "pricing.json"))   # no file -> defaults
    plans = _iso(pricing.public_plans())
    assert [p["key"] for p in pricing.public_plans()] == ["monthly", "q", "h", "year"]   # display order
    assert plans["monthly"]["mo"] == "$10" and plans["monthly"]["bill"] == "billed monthly"
    assert plans["q"]["mo"] == "$9" and plans["q"]["save_pct"] == 10
    assert plans["h"]["mo"] == "$8.50" and plans["h"]["save_pct"] == 15            # cents kept
    assert plans["year"]["mo"] == "$8" and plans["year"]["save_pct"] == 20
    assert "save 20%" in plans["year"]["bill"] and "billed yearly" in plans["year"]["bill"]


def test_save_reload_and_recompute(tmp_path, monkeypatch):
    monkeypatch.setattr(pricing, "CONFIG_PATH", str(tmp_path / "pricing.json"))
    pricing.save_config(prices={"monthly": 12, "year": 120}, currency="£")
    cfg = pricing.get_config()
    assert cfg["currency"] == "£" and cfg["prices"]["monthly"] == 12 and cfg["prices"]["year"] == 120
    plans = _iso(pricing.public_plans())
    assert plans["monthly"]["mo"] == "£12"
    assert plans["year"]["mo"] == "£10"                                            # 120 / 12 months
    assert plans["year"]["save_pct"] == 17                                         # 1 - 120/(12*12) ~ 16.7 -> 17
    assert plans["q"]["price"] == 27                                               # untouched key keeps default


def test_save_ignores_garbage(tmp_path, monkeypatch):
    monkeypatch.setattr(pricing, "CONFIG_PATH", str(tmp_path / "pricing.json"))
    pricing.save_config(prices={"monthly": "abc", "boguskey": 5, "year": -50}, currency="")
    cfg = pricing.get_config()
    assert cfg["prices"]["monthly"] == 10.0          # bad float ignored -> default
    assert "boguskey" not in cfg["prices"]           # unknown period dropped
    assert cfg["prices"]["year"] == 0.0              # negative clamped to 0


def test_admin_pricing_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_STEAM_IDS", "76561198106326204")
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    monkeypatch.setattr(pricing, "CONFIG_PATH", str(tmp_path / "pricing.json"))
    import app
    db.DB_PATH = str(tmp_path / "p.sqlite")
    db.migrate()
    admin_uid = db.upsert_user("76561198106326204", "Redzalio")
    helper_uid = db.upsert_user("111", "Helen")
    db.set_user_role(helper_uid, "helper")
    c = app.app.test_client()
    assert c.get("/api/admin/pricing").status_code in (401, 403)            # anonymous
    with c.session_transaction() as s:
        s["uid"] = helper_uid
    assert c.get("/api/admin/pricing").status_code == 403                   # helper: pricing is admin-only
    with c.session_transaction() as s:
        s["uid"] = admin_uid
    g = c.get("/api/admin/pricing").get_json()
    assert g["config"]["prices"]["monthly"] == 10 and "plans" in g and "periods" in g
    r = c.post("/api/admin/pricing", json={"currency": "$", "prices": {"monthly": 15, "year": 144}})
    assert r.status_code == 200
    assert pricing.get_config()["prices"]["monthly"] == 15                   # persisted
    assert _iso(r.get_json()["plans"])["year"]["mo"] == "$12"               # 144/12 recomputed


def test_api_me_includes_pricing(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.setattr(pricing, "CONFIG_PATH", str(tmp_path / "pricing.json"))
    import app
    db.DB_PATH = str(tmp_path / "me.sqlite")
    db.migrate()
    c = app.app.test_client()
    me = c.get("/api/me").get_json()
    assert isinstance(me["pricing"], list) and len(me["pricing"]) == 4
    assert {p["key"] for p in me["pricing"]} == {"monthly", "q", "h", "year"}
