"""billing.py -- Stripe subscription billing for VantageGG Pro.

Opt-in like the rest of the deploy: set STRIPE_SECRET_KEY (sk_test_… or sk_live_…) to enable. Without
it, billing is OFF and the app behaves exactly as before (Pro granted only manually via the admin
panel). Prices are resolved by LOOKUP KEY (pro_monthly/pro_q/pro_h/pro_year), so the SAME code works
in test and live mode -- each mode just needs its own product+prices (see tools/stripe_seed.py).

Flow:
  - /api/billing/checkout -> create_checkout_session() -> Stripe-hosted Checkout (subscription mode).
  - Stripe -> /api/stripe/webhook -> verify_event() + apply_event() -> db.set_user_tier(pro/free).
  - /api/billing/portal -> create_portal_session() -> Stripe Customer Portal (self-serve cancel/switch).

The user id travels in client_reference_id AND subscription metadata, so any webhook event can find the
account regardless of delivery order; the Stripe customer is also linked to the user on first checkout.
"""
import datetime
import os

try:
    import stripe
except ImportError:                      # lib not installed (e.g. minimal local env) -> billing off
    stripe = None

import db

# period key (pricing.py) -> Stripe price lookup_key (set on the prices in tools/stripe_seed.py)
LOOKUP = {"monthly": "pro_monthly", "q": "pro_q", "h": "pro_h", "year": "pro_year"}

# subscription statuses that should keep Pro unlocked (past_due = payment retrying, keep access in grace)
ACTIVE_STATES = {"active", "trialing", "past_due"}
GRACE_DAYS = 2                           # absorb renewal-webhook latency so users don't flicker to Free

_price_cache = {}                        # lookup_key -> price id (per process)


def _secret():
    return os.environ.get("STRIPE_SECRET_KEY") or ""


def enabled():
    """Billing is live only when the lib is present AND a secret key is configured."""
    return bool(stripe and _secret())


def _client():
    """Configure + return the stripe module, or None if billing is off."""
    if not enabled():
        return None
    stripe.api_key = _secret()
    return stripe


def price_id(period):
    """Resolve a period key -> Stripe price id via its lookup_key (cached). None if unknown/missing."""
    lk = LOOKUP.get(period)
    s = _client()
    if not lk or not s:
        return None
    if lk in _price_cache:
        return _price_cache[lk]
    try:
        prices = s.Price.list(lookup_keys=[lk], active=True, limit=1)
        pid = prices.data[0].id if prices.data else None
    except Exception:
        pid = None
    if pid:
        _price_cache[lk] = pid
    return pid


def create_checkout_session(user, period, base_url):
    """Stripe-hosted Checkout (subscription mode) for `user` on `period`. Returns the redirect URL,
    or None if billing is off / the period is unknown. `base_url` = the site root (for return URLs)."""
    s = _client()
    pid = price_id(period)
    if not s or not pid or not user or not user.get("id"):
        return None
    base = base_url.rstrip("/")
    uid = str(user["id"])
    params = {
        "mode": "subscription",
        "line_items": [{"price": pid, "quantity": 1}],
        "success_url": base + "/?checkout=success",
        "cancel_url": base + "/?checkout=cancel",
        "client_reference_id": uid,
        "allow_promotion_codes": True,
        "metadata": {"uid": uid, "period": period, "steam_id": str(user.get("steam_id_64") or "")},
        # uid on the subscription too -> subscription.* webhooks can find the user even out of order
        "subscription_data": {"metadata": {"uid": uid, "period": period}},
    }
    if user.get("stripe_customer_id"):
        params["customer"] = user["stripe_customer_id"]      # reuse the existing customer
    # else: in subscription mode Stripe ALWAYS creates a Customer automatically (passing
    # customer_creation is an error here); the checkout.session.completed webhook links it to the user.
    sess = s.checkout.Session.create(**params)
    return sess.url


def create_portal_session(user, base_url):
    """Stripe Customer Portal for self-serve cancel/switch. Returns the URL, or None if the user has
    no linked Stripe customer (never subscribed) / billing is off."""
    s = _client()
    if not s or not user or not user.get("stripe_customer_id"):
        return None
    sess = s.billing_portal.Session.create(
        customer=user["stripe_customer_id"], return_url=base_url.rstrip("/") + "/?portal=return")
    return sess.url


def verify_event(payload_bytes, sig_header):
    """Verify a webhook payload against STRIPE_WEBHOOK_SECRET -> the event dict. Raises on a bad/forged
    signature (the route returns 400). Requires the lib + the webhook secret to be set."""
    s = _client()
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET") or ""
    if not s or not secret:
        raise RuntimeError("billing webhook not configured (STRIPE_WEBHOOK_SECRET unset)")
    return s.Webhook.construct_event(payload_bytes, sig_header, secret)


def _retrieve_sub(sub_id):
    s = _client()
    if not s or not sub_id:
        return None
    try:
        return s.Subscription.retrieve(sub_id)
    except Exception:
        return None


def _to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _uid_for(obj):
    """Find our user id for a Stripe object: subscription/session metadata.uid first, else the linked
    customer (set at first checkout)."""
    uid = _to_int((obj.get("metadata") or {}).get("uid"))
    if uid:
        return uid
    u = db.user_by_stripe_customer(obj.get("customer"))
    return u["id"] if u else None


def _grant(uid, sub):
    """Set a user's tier from a Stripe subscription object: active/trialing/past_due -> Pro until the
    current period end (+grace); anything else -> Free."""
    if not uid or not sub:
        return False
    status = sub.get("status")
    if status in ACTIVE_STATES:
        cpe = sub.get("current_period_end")
        pro_until = None
        if cpe:
            # naive-local ISO to match app._pro_expired's datetime.now() comparison (Docker TZ = UTC)
            until = datetime.datetime.fromtimestamp(int(cpe)) + datetime.timedelta(days=GRACE_DAYS)
            pro_until = until.isoformat(timespec="seconds")
        return db.set_user_tier(uid, "pro", pro_until)
    return db.set_user_tier(uid, "free")


def apply_event(event):
    """Act on a verified webhook event. Returns True if it changed state, False if ignored. Pure-ish
    (only touches db + _retrieve_sub) so it's unit-testable without real Stripe traffic."""
    t = event.get("type")
    obj = (event.get("data") or {}).get("object") or {}
    if t == "checkout.session.completed":
        uid = _to_int(obj.get("client_reference_id")) or _to_int((obj.get("metadata") or {}).get("uid"))
        cust = obj.get("customer")
        if uid and cust:
            db.set_stripe_customer(uid, cust)                # link customer so the Portal works later
        sub = _retrieve_sub(obj.get("subscription"))         # unlock immediately from this one event
        if sub:
            _grant(_uid_for(sub) or uid, sub)
        return True
    if t in ("customer.subscription.created", "customer.subscription.updated"):
        _grant(_uid_for(obj), obj)                           # renewals, plan switches, status changes
        return True
    if t == "customer.subscription.deleted":
        uid = _uid_for(obj)
        if uid:
            db.set_user_tier(uid, "free")                    # canceled -> downgrade
        return True
    return False
