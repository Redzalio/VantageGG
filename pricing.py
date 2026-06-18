"""pricing.py -- single source of truth for Pro subscription prices (stdlib only).

Defaults live here; an admin can override them from the admin panel (which writes pricing.json in
DATA_DIR -- you can also edit that file by hand). The frontend reads the *computed* plans from
/api/me, so the landing card, the in-app upgrade modal, and the locked-feature upsell all show the
same numbers and update the moment you change them. Billing isn't live yet -- these are presentational
until Stripe is wired, at which point each period maps to a Stripe Price ID.

You set the TOTAL charged per period (what the customer pays for the whole term); per-month and the
"save X%" badge are derived from that vs. the monthly price * the term length.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR") or HERE
CONFIG_PATH = os.environ.get("PRICING_PATH") or os.path.join(DATA_DIR, "pricing.json")

# Display order + the term length (months) that drives per-month + savings math. Editing prices is
# enough for normal use; add/rename periods here only if the plan structure itself changes.
PERIODS = [
    {"key": "monthly", "label": "Monthly", "months": 1},
    {"key": "q", "label": "3-Monthly", "months": 3},
    {"key": "h", "label": "6-Monthly", "months": 6},
    {"key": "year", "label": "Yearly", "months": 12},
]
_PERIOD_BY_KEY = {p["key"]: p for p in PERIODS}

# Default TOTAL charged per period.
DEFAULTS = {"currency": "$", "prices": {"monthly": 10.0, "q": 27.0, "h": 51.0, "year": 96.0}}


def _fmt_money(cur, amount):
    """'$10', '$8.50', '$27' -- drop a trailing .0, keep cents when there are any."""
    if abs(amount - round(amount)) < 0.005:
        return "%s%d" % (cur, round(amount))
    return "%s%.2f" % (cur, amount)


def _load_raw():
    """Defaults merged with any valid overrides from pricing.json. Never raises on a bad file."""
    cfg = {"currency": DEFAULTS["currency"], "prices": dict(DEFAULTS["prices"])}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        cur = data.get("currency")
        if isinstance(cur, str) and cur.strip():
            cfg["currency"] = cur.strip()[:3]
        for k, v in (data.get("prices") or {}).items():
            if k in _PERIOD_BY_KEY:
                try:
                    cfg["prices"][k] = max(0.0, float(v))
                except (TypeError, ValueError):
                    pass
    except (OSError, ValueError):
        pass
    return cfg


def get_config():
    """Raw editable config for the admin editor: {currency, prices:{key: total}}."""
    return _load_raw()


def save_config(prices=None, currency=None):
    """Persist overrides (atomic write). prices = {periodKey: total}. Returns the new raw config."""
    cfg = _load_raw()
    if currency is not None and str(currency).strip():
        cfg["currency"] = str(currency).strip()[:3]
    for k, v in (prices or {}).items():
        if k in _PERIOD_BY_KEY:
            try:
                cfg["prices"][k] = max(0.0, round(float(v), 2))
            except (TypeError, ValueError):
                pass
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_PATH)
    return cfg


def public_plans():
    """Computed plans for the frontend (display order). Each:
       {key, label, months, price (total), mo (per-month str), bill (text), save_pct}."""
    cfg = _load_raw()
    cur = cfg["currency"]
    monthly_total = cfg["prices"].get("monthly", DEFAULTS["prices"]["monthly"])
    out = []
    for p in PERIODS:
        total = cfg["prices"].get(p["key"], DEFAULTS["prices"][p["key"]])
        months = p["months"]
        per_mo = total / months if months else total
        save_pct = 0
        if months > 1 and monthly_total > 0:
            ref = monthly_total * months
            if ref > 0:
                save_pct = max(0, int(round((1 - total / ref) * 100)))
        save_txt = (" · save %d%%" % save_pct) if save_pct > 0 else ""
        if p["key"] == "monthly":
            bill = "billed monthly"
        elif p["key"] == "year":
            bill = "%s billed yearly%s" % (_fmt_money(cur, total), save_txt)
        else:
            bill = "%s billed every %d months%s" % (_fmt_money(cur, total), months, save_txt)
        out.append({"key": p["key"], "label": p["label"], "months": months,
                    "price": round(total, 2), "mo": _fmt_money(cur, per_mo),
                    "bill": bill, "save_pct": save_pct})
    return out
