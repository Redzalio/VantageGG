"""Legal/policy pages (Terms, Privacy, Cookies, Refunds) -- standard, app-specific PLACEHOLDER
content served as standalone pages. Stdlib only.

IMPORTANT: this is a reasonable starting template, NOT legal advice. The subscription/refund wording,
jurisdiction, and privacy obligations (GDPR/CCPA/etc.) should be reviewed by an attorney before you
rely on them, especially with live billing. Each page shows that disclaimer.
"""
import datetime
import html
import os

UPDATED = "June 19, 2026"
SITE = "VantageGG"
CONTACT = os.environ.get("SUPPORT_CONTACT") or "support@vantagegg.com"
ENTITY = os.environ.get("LEGAL_ENTITY") or "VantageGG"            # business/legal entity name
JURISDICTION = os.environ.get("LEGAL_JURISDICTION") or "[your jurisdiction]"

_DISCLAIMER = (
    "This document is a general template provided for convenience and is <strong>not legal "
    "advice</strong>. It should be reviewed and customized by a qualified attorney for your "
    "jurisdiction before launch."
)


def _shell(slug, title, body):
    c = html.escape(CONTACT)
    nav = "".join(
        '<a href="/%s"%s>%s</a>' % (s, ' class="on"' if s == slug else "", t)
        for s, t in (("terms", "Terms"), ("privacy", "Privacy"), ("cookies", "Cookies"), ("refunds", "Refunds")))
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} &middot; {site}</title>
<link rel="stylesheet" href="/static/css/style.css">
<style>
  body.legal{{background:var(--bg,#0b0e12);color:var(--txt,#e8eef5);margin:0;
    font:15px/1.65 Inter,system-ui,-apple-system,sans-serif}}
  .lg-top{{display:flex;align-items:center;gap:16px;flex-wrap:wrap;padding:14px 20px;
    border-bottom:1px solid var(--line,#222b36);position:sticky;top:0;background:rgba(11,14,18,.92);backdrop-filter:blur(6px)}}
  .lg-brand{{font-weight:800;font-size:18px;letter-spacing:.5px;color:var(--txt);text-decoration:none}}
  .lg-brand span{{color:var(--accent,#e8743b)}}
  .lg-nav{{display:flex;gap:14px;flex-wrap:wrap;margin-left:auto;font-size:13px}}
  .lg-nav a{{color:var(--mut,#9aa7b4);text-decoration:none}}
  .lg-nav a:hover,.lg-nav a.on{{color:var(--accent)}}
  .lg-wrap{{max-width:820px;margin:0 auto;padding:30px 20px 90px}}
  .lg-wrap h1{{font-size:26px;margin:.2em 0 .1em}}
  .lg-upd{{color:var(--mut);font-size:13px;margin-bottom:18px}}
  .lg-note{{background:rgba(232,116,59,.10);border:1px solid rgba(232,116,59,.4);border-radius:10px;
    padding:11px 14px;font-size:13px;color:#ffd9c2;margin:0 0 26px}}
  .lg-wrap h2{{font-size:18px;margin:30px 0 6px;border-top:1px solid var(--line);padding-top:22px}}
  .lg-wrap h2:first-of-type{{border-top:0;padding-top:0}}
  .lg-wrap p,.lg-wrap li{{color:var(--txt2,#c9d4df)}}
  .lg-wrap ul{{padding-left:20px}} .lg-wrap li{{margin:4px 0}}
  .lg-wrap a{{color:var(--accent)}}
  .lg-foot{{margin-top:40px;padding-top:18px;border-top:1px solid var(--line);color:var(--mut);font-size:13px}}
</style></head>
<body class="legal">
  <header class="lg-top">
    <a class="lg-brand" href="/">Vantage<span>GG</span></a>
    <nav class="lg-nav">{nav}<a href="/">&larr; Back to app</a></nav>
  </header>
  <main class="lg-wrap">
    <h1>{title}</h1>
    <div class="lg-upd">Last updated: {updated}</div>
    <div class="lg-note">{disc}</div>
    {body}
    <div class="lg-foot">Questions about this policy? Contact <a href="mailto:{contact}">{contact}</a>.</div>
  </main>
</body></html>""".format(title=html.escape(title), site=html.escape(SITE), nav=nav,
                         updated=html.escape(UPDATED), disc=_DISCLAIMER, body=body, contact=c)


# --- document bodies (HTML fragments) ---------------------------------------
def _terms():
    return """
<p>These Terms of Service ("Terms") govern your access to and use of {site} (the "Service"). By using
the Service you agree to these Terms. If you do not agree, do not use the Service.</p>

<h2>1. Who can use {site}</h2>
<p>You must be at least 13 years old (or the minimum age of digital consent in your country) to use
the Service. By using it you represent that you meet this requirement and that the information you
provide is accurate.</p>

<h2>2. Accounts &amp; sign-in</h2>
<p>You sign in with your Steam account via Steam OpenID. You are responsible for activity that occurs
under your account. We do not receive or store your Steam password.</p>

<h2>3. The Service</h2>
<p>{site} lets you upload Counter-Strike demo files (".dem") to generate a 2D/3D replay, statistics,
and coaching insights. Features available depend on your plan (Free or Pro).</p>

<h2>4. Your content &amp; rights</h2>
<ul>
  <li>You must own, or have the necessary rights to upload and process, any demo or video you submit.</li>
  <li>You retain your rights to your content. You grant {site} a limited license to store and process
      it solely to provide the Service to you and your team.</li>
  <li>You are responsible for ensuring your uploads do not violate any third party's rights or any law.</li>
</ul>

<h2>5. Acceptable use</h2>
<p>You agree not to: abuse, overload, or attempt to disrupt the Service; upload malware or illegal
content; attempt to gain unauthorized access; scrape or resell the Service; or use it to harass others
or violate any law or platform rules (including Valve/Steam terms).</p>

<h2>6. Subscriptions &amp; billing</h2>
<p>Pro is a paid subscription billed through our payment processor (Stripe). Plans renew automatically
at the end of each term until cancelled. Prices are shown at checkout. You can cancel anytime; see our
<a href="/refunds">Refund &amp; Cancellation Policy</a>. We do not store your full card details.</p>

<h2>7. Service availability &amp; changes</h2>
<p>The Service is provided on an "as is" and "as available" basis. We may modify, suspend, or
discontinue features at any time. We aim to give reasonable notice of material changes where practical.</p>

<h2>8. Disclaimers</h2>
<p>To the fullest extent permitted by law, {site} disclaims all warranties, express or implied,
including merchantability, fitness for a particular purpose, and non-infringement. Statistics and
insights are provided for informational purposes and may contain inaccuracies.</p>

<h2>9. Limitation of liability</h2>
<p>To the fullest extent permitted by law, {site} and {entity} will not be liable for any indirect,
incidental, special, consequential, or punitive damages, or for any loss of data, arising from your
use of the Service.</p>

<h2>10. Termination</h2>
<p>You may stop using the Service at any time. We may suspend or terminate access for violations of
these Terms. You can request account deletion as described in our
<a href="/privacy">Privacy Policy</a>.</p>

<h2>11. Changes to these Terms</h2>
<p>We may update these Terms from time to time. Continued use after an update constitutes acceptance
of the revised Terms.</p>

<h2>12. Governing law &amp; contact</h2>
<p>These Terms are governed by the laws of {jur}, without regard to conflict-of-laws rules. Questions:
<a href="mailto:{contact}">{contact}</a>.</p>
""".format(site=SITE, entity=html.escape(ENTITY), jur=html.escape(JURISDICTION), contact=html.escape(CONTACT))


def _privacy():
    return """
<p>This Privacy Policy explains what {site} collects, how we use it, and the choices you have.</p>

<h2>1. Information we collect</h2>
<ul>
  <li><strong>Steam identity:</strong> your SteamID and public profile info (display name, avatar) via
      Steam OpenID, used to identify your account.</li>
  <li><strong>Demos &amp; derived data:</strong> demo files you upload and the parsed match data,
      statistics, and coaching insights generated from them.</li>
  <li><strong>Retained stats:</strong> when you delete a replay, we keep a small compact stats record
      (match summary + per-player aggregates) so your long-term trends, goals, and profile history
      remain accurate. This record contains no replay/positional data.</li>
  <li><strong>Usage &amp; logs:</strong> basic technical logs (e.g. request and error logs) used to
      operate, secure, and debug the Service.</li>
  <li><strong>Cookies &amp; local storage:</strong> see our <a href="/cookies">Cookie Policy</a>.</li>
  <li><strong>Payment data:</strong> if you subscribe, payments are processed by Stripe. We receive a
      subscription/customer reference and status, but <strong>not</strong> your full card number.</li>
</ul>

<h2>2. How we use information</h2>
<p>To provide and improve the Service, generate your replays and statistics, operate subscriptions,
maintain security, and communicate with you about your account or the Service.</p>

<h2>3. How we share information</h2>
<p>We do not sell your personal information. We share data only with service providers that help us run
the Service (for example, our payment processor and hosting provider) and where required by law.</p>

<h2>4. Data retention</h2>
<ul>
  <li>Raw .dem files are deleted after parsing (we keep the parsed replay, not the original upload).</li>
  <li>Parsed replay data is kept until you delete the match or your account.</li>
  <li>Compact match stats may be retained to preserve your long-term performance history.</li>
  <li>Logs are kept for a limited period for security and troubleshooting.</li>
</ul>

<h2>5. Your rights &amp; choices</h2>
<p>You can delete an individual match's replay at any time from your library. You may request access to,
correction of, or deletion of your account and associated data by contacting
<a href="mailto:{contact}">{contact}</a>. Depending on your location, you may have additional rights
under laws such as the GDPR or CCPA.</p>

<h2>6. Security</h2>
<p>We use reasonable technical and organizational measures to protect your data (including HTTPS,
authentication, and access controls). No method of transmission or storage is 100% secure.</p>

<h2>7. Children</h2>
<p>The Service is not directed to children under 13, and we do not knowingly collect their personal
information.</p>

<h2>8. International users</h2>
<p>The Service may be operated from, and your data processed in, a country other than your own. By
using the Service you consent to such processing.</p>

<h2>9. Changes &amp; contact</h2>
<p>We may update this policy; material changes will be reflected by the "Last updated" date. Questions:
<a href="mailto:{contact}">{contact}</a>.</p>
""".format(site=SITE, contact=html.escape(CONTACT))


def _cookies():
    return """
<p>This Cookie Policy explains the cookies and local storage {site} uses. We use only what's needed to
run the site &mdash; <strong>no advertising or third-party tracking cookies.</strong></p>

<h2>1. Strictly necessary cookies</h2>
<ul>
  <li><strong>Session cookie:</strong> a signed cookie that keeps you logged in after you sign in with
      Steam. Without it, the Service cannot keep you authenticated.</li>
</ul>

<h2>2. Local storage (on your device)</h2>
<p>We use your browser's local/session storage to remember preferences and improve your experience.
This data stays in your browser and is not used for advertising. It includes, for example:</p>
<ul>
  <li>your replay/display settings (e.g. dot size, nameplate size, toggles);</li>
  <li>whether you've seen the first-time walkthrough;</li>
  <li>a short-lived cache of the last demo you viewed, so it loads faster.</li>
</ul>

<h2>3. No tracking or advertising</h2>
<p>We do not use analytics, marketing, or cross-site tracking cookies. Because we use only strictly
necessary cookies and local preferences, prior consent is generally not required &mdash; but we show a
brief notice so you're informed.</p>

<h2>4. Managing cookies &amp; storage</h2>
<p>You can clear or block cookies and local storage in your browser settings. Note that blocking the
session cookie will prevent you from staying signed in.</p>

<h2>5. Changes &amp; contact</h2>
<p>If we ever introduce non-essential cookies, we will update this policy and add a consent option
before loading them. Questions: <a href="mailto:{contact}">{contact}</a>.</p>
""".format(site=SITE, contact=html.escape(CONTACT))


def _refunds():
    return """
<p>This policy describes how {site} subscriptions, cancellations, and refunds work. It supplements our
<a href="/terms">Terms of Service</a>.</p>

<h2>1. Subscription terms</h2>
<p>Pro is offered on recurring terms (for example monthly, 3-monthly, 6-monthly, or yearly). Your plan
renews automatically at the end of each term at the then-current price until you cancel.</p>

<h2>2. Cancelling</h2>
<p>You can cancel at any time from your account's subscription management (which opens the secure
billing portal). When you cancel, your Pro access continues until the end of the current paid term and
does not renew after that. Cancelling does not, by itself, trigger a refund of the current term unless
required by law or stated below.</p>

<h2>3. Refunds</h2>
<p><em>[Placeholder &mdash; confirm with your payment processor and attorney.]</em> If you believe you
were charged in error, or you are within any applicable statutory cooling-off period, contact
<a href="mailto:{contact}">{contact}</a> and we will review your request. Where required by consumer-
protection law in your jurisdiction, your statutory refund rights apply and are not affected by this
policy.</p>

<h2>4. Failed or disputed payments</h2>
<p>If a renewal payment fails, we may suspend Pro features until payment is resolved. Please contact us
before initiating a chargeback so we can help.</p>

<h2>5. Price changes</h2>
<p>We may change subscription prices; any change applies to future terms, and we will make current
pricing visible before you are charged for a renewal where required.</p>

<h2>6. Contact</h2>
<p>Billing questions: <a href="mailto:{contact}">{contact}</a>.</p>
""".format(site=SITE, contact=html.escape(CONTACT))


_DOCS = {
    "terms": ("Terms of Service", _terms),
    "privacy": ("Privacy Policy", _privacy),
    "cookies": ("Cookie Policy", _cookies),
    "refunds": ("Refund &amp; Cancellation Policy", _refunds),
}


def render(slug):
    """Full HTML page for a slug, or None if unknown."""
    doc = _DOCS.get(slug)
    if not doc:
        return None
    title, body_fn = doc
    return _shell(slug, title, body_fn())


def slugs():
    return list(_DOCS.keys())
