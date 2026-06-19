"""Public policy pages (Terms/Privacy/Cookies/Refunds): served as standalone HTML, work logged-out,
carry the operator's content with the brand normalized to VantageGG / vantagegg.com and the
hexlynx@gmail.com contact."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app     # noqa: E402
import legal   # noqa: E402


def test_legal_slugs_and_unknown():
    assert set(legal.slugs()) == {"terms", "privacy", "cookies", "refunds"}
    assert legal.render("nope") is None


def test_legal_routes_render(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "1")          # even on a locked site, policy pages are public
    c = app.app.test_client()
    app._rl_hits.clear()
    expect = {"terms": "Terms of Service", "privacy": "Privacy Policy",
              "cookies": "Cookie Policy", "refunds": "Refund"}
    for slug, must in expect.items():
        r = c.get("/" + slug)
        assert r.status_code == 200, slug
        assert r.headers["Content-Type"].startswith("text/html")
        body = r.get_data(as_text=True)
        assert must in body
        assert "Last updated" in body
        # brand normalized + correct contact (the source docs said VancedGG / vancedgg.com)
        assert "VantageGG" in body and "VancedGG" not in body
        assert "vancedgg.com" not in body
        assert "hexlynx@gmail.com" in body


def test_brand_and_domain_substituted():
    for slug in legal.slugs():
        body = legal.render(slug)
        assert "vancedgg" not in body.lower()
        assert "VantageGG" in body


def test_privacy_mentions_key_data():
    body = legal.render("privacy").lower()
    for kw in ("steamid64", "payment processor", "compact stats", "delete", "pennsylvania"):
        assert kw in body, kw


def test_payment_processor_named():
    # Stripe is named in the docs that cover payments
    for slug in ("terms", "privacy", "refunds"):
        assert "stripe" in legal.render(slug).lower(), slug


def test_cookies_no_tracking_claim():
    body = legal.render("cookies").lower()
    assert "essential" in body and "tracking" in body


def test_intros_note_not_legal_advice():
    # the cookie/privacy/refund docs carry their own "not legal advice" caveat in the intro
    for slug in ("cookies", "privacy", "refunds"):
        assert "not legal advice" in legal.render(slug).lower(), slug
