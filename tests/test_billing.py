"""Stripe billing logic: customer<->user linking, webhook event -> tier transitions, and the
billing-off safety (no key => disabled, endpoints don't pretend). No real Stripe calls -- apply_event
touches only db + a monkeypatched _retrieve_sub."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db        # noqa: E402
import billing   # noqa: E402
import app       # noqa: E402


def _tmp(tmp_path):
    db.DB_PATH = str(tmp_path / "billing.sqlite")
    db.migrate()


def _future():
    return int(time.time()) + 30 * 86400


def test_disabled_without_key(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    assert billing.enabled() is False
    assert billing.create_checkout_session({"id": 1, "steam_id_64": "7"}, "monthly", "https://x") is None
    assert billing.create_portal_session({"id": 1, "stripe_customer_id": "cus_1"}, "https://x") is None


def test_db_stripe_customer_link(tmp_path):
    _tmp(tmp_path)
    uid = db.upsert_user("76561190000000001", "U1")
    assert db.user_by_stripe_customer("cus_x") is None
    assert db.set_stripe_customer(uid, "cus_x") is True
    u = db.user_by_stripe_customer("cus_x")
    assert u and u["id"] == uid
    assert db.get_user(uid)["stripe_customer_id"] == "cus_x"


def test_subscription_updated_grants_pro(tmp_path):
    _tmp(tmp_path)
    uid = db.upsert_user("76561190000000002", "U2")
    ev = {"type": "customer.subscription.updated", "data": {"object": {
        "status": "active", "current_period_end": _future(), "customer": "cus_2",
        "metadata": {"uid": str(uid)}}}}
    assert billing.apply_event(ev) is True
    u = db.get_user(uid)
    assert u["tier"] == "pro" and u["pro_until"] and u["pro_until"] > "2026-06-19"


def test_subscription_deleted_downgrades(tmp_path):
    _tmp(tmp_path)
    uid = db.upsert_user("76561190000000003", "U3")
    db.set_user_tier(uid, "pro", "2099-01-01T00:00:00")
    db.set_stripe_customer(uid, "cus_3")
    # deleted event carries only the customer -> _uid_for resolves it via the link
    ev = {"type": "customer.subscription.deleted", "data": {"object": {
        "status": "canceled", "customer": "cus_3"}}}
    assert billing.apply_event(ev) is True
    u = db.get_user(uid)
    assert u["tier"] == "free" and u["pro_until"] is None


def test_checkout_completed_links_customer_and_grants(tmp_path, monkeypatch):
    _tmp(tmp_path)
    uid = db.upsert_user("76561190000000004", "U4")
    monkeypatch.setattr(billing, "_retrieve_sub", lambda sub_id: {
        "status": "active", "current_period_end": _future(), "customer": "cus_4",
        "metadata": {"uid": str(uid)}})
    ev = {"type": "checkout.session.completed", "data": {"object": {
        "client_reference_id": str(uid), "customer": "cus_4", "subscription": "sub_4"}}}
    assert billing.apply_event(ev) is True
    u = db.get_user(uid)
    assert u["stripe_customer_id"] == "cus_4"      # linked for the Portal
    assert u["tier"] == "pro"                       # unlocked immediately


def test_inactive_status_is_free(tmp_path):
    _tmp(tmp_path)
    uid = db.upsert_user("76561190000000005", "U5")
    db.set_user_tier(uid, "pro", "2099-01-01T00:00:00")
    ev = {"type": "customer.subscription.updated", "data": {"object": {
        "status": "incomplete_expired", "customer": "cus_5", "metadata": {"uid": str(uid)}}}}
    billing.apply_event(ev)
    assert db.get_user(uid)["tier"] == "free"


def test_checkout_endpoint_503_when_billing_off(tmp_path, monkeypatch):
    _tmp(tmp_path)
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    c = app.app.test_client()
    r = c.post("/api/billing/checkout", json={"period": "monthly"})
    assert r.status_code == 503


def test_webhook_rejects_bad_signature(tmp_path, monkeypatch):
    _tmp(tmp_path)
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    c = app.app.test_client()
    r = c.post("/api/stripe/webhook", data=b"{}", headers={"Stripe-Signature": "bogus"})
    assert r.status_code == 400
