"""Idempotently create the 'VantageGG Pro' product + its 4 recurring prices in whatever Stripe mode
the key belongs to (test or live). Run this once per mode so prices resolve by lookup_key.

    STRIPE_SECRET_KEY=sk_test_... python tools/stripe_seed.py     # seed TEST mode
    STRIPE_SECRET_KEY=sk_live_... python tools/stripe_seed.py     # (live was already seeded by hand)

Safe to re-run: prices are matched by lookup_key (pro_monthly/pro_q/pro_h/pro_year) and only created
if missing. Prints the product + price ids and the mode. Amounts mirror pricing.py DEFAULTS.
"""
import os
import sys

try:
    import stripe
except ImportError:
    sys.exit("stripe lib not installed -- run: pip install stripe")

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY") or ""
if not stripe.api_key:
    sys.exit("set STRIPE_SECRET_KEY first (sk_test_... to seed test mode)")

PRODUCT_NAME = "VantageGG Pro"
PRODUCT_DESC = ("Full access to VantageGG: 3D replay, utility & nade tools, advanced analytics & "
                "trends, practice goals, and team workspaces — plus unlimited demo uploads.")
TAX_CODE = "txcd_10103000"        # Software as a Service (SaaS) - Personal Use
# (lookup_key, cents, interval, interval_count, nickname, period, months)
PRICES = [
    ("pro_monthly", 1000, "month", 1, "Pro — Monthly", "monthly", "1"),
    ("pro_q", 2700, "month", 3, "Pro — Quarterly (3 mo)", "q", "3"),
    ("pro_h", 5100, "month", 6, "Pro — 6-Month", "h", "6"),
    ("pro_year", 9600, "year", 1, "Pro — Yearly", "year", "12"),
]


def main():
    # find existing prices by lookup_key (the idempotency anchor)
    existing = {}
    product_id = None
    for lk, *_ in PRICES:
        res = stripe.Price.list(lookup_keys=[lk], active=True, limit=1)
        if res.data:
            existing[lk] = res.data[0].id
            product_id = product_id or res.data[0].product

    if not product_id:
        prod = stripe.Product.create(name=PRODUCT_NAME, description=PRODUCT_DESC,
                                     tax_code=TAX_CODE, metadata={"app": "vantagegg_pro"})
        product_id = prod.id
        print(f"created product {product_id}")
    else:
        print(f"reusing product {product_id}")

    for lk, cents, interval, count, nickname, period, months in PRICES:
        if lk in existing:
            print(f"  price {lk}: exists ({existing[lk]})")
            continue
        p = stripe.Price.create(
            product=product_id, currency="usd", unit_amount=cents,
            recurring={"interval": interval, "interval_count": count},
            nickname=nickname, lookup_key=lk,
            metadata={"period": period, "months": months})
        existing[lk] = p.id
        print(f"  price {lk}: CREATED ({p.id})  ${cents / 100:.2f} / {count} {interval}")

    mode = "LIVE" if stripe.api_key.startswith("sk_live_") else "TEST"
    print(f"\n[{mode}] product={product_id}")
    for lk, *_ in PRICES:
        print(f"  {lk} = {existing.get(lk)}")
    print("done.")


if __name__ == "__main__":
    main()
