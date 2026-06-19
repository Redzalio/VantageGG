"""Public policy pages (Terms/Privacy/Cookies/Refunds): served as standalone HTML, work logged-out,
carry the standard sections + the not-legal-advice disclaimer."""
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
    expect = {
        "terms": "Terms of Service",
        "privacy": "Privacy Policy",
        "cookies": "Cookie Policy",
        "refunds": "Refund",
    }
    for slug, must in expect.items():
        r = c.get("/" + slug)
        assert r.status_code == 200, slug
        assert r.headers["Content-Type"].startswith("text/html")
        body = r.get_data(as_text=True)
        assert must in body
        assert "not legal advice" in body            # disclaimer always present
        assert "Last updated" in body
        # links to the other policies + contact are present
        assert "/privacy" in body and "mailto:" in body


def test_privacy_mentions_key_data_and_stripe():
    body = legal.render("privacy")
    for kw in ("SteamID", "Stripe", "compact stats", "delete"):
        assert kw.lower() in body.lower(), kw


def test_cookies_no_tracking_claim():
    body = legal.render("cookies").lower()
    assert "essential" in body and "tracking" in body
