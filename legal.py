"""Legal/policy pages (Terms, Privacy, Cookies, Refunds) served as standalone HTML. Stdlib only.

Content is the operator-provided policy text (VantageGG, Pennsylvania, USA; contact hexlynx@gmail.com).
Each document carries its own "not legal advice / review by counsel before launch" language where the
source provided it. Brand normalized to VantageGG / vantagegg.com.
"""
import html
import os

UPDATED = "June 19, 2026"
SITE = "VantageGG"
DOMAIN = "vantagegg.com"
CONTACT = os.environ.get("SUPPORT_CONTACT") or "hexlynx@gmail.com"
LOCATION = "Pennsylvania, United States"


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
  /* the app stylesheet pins html/body to the viewport with overflow:hidden (it's a fixed-layout SPA);
     these standalone doc pages must scroll normally, so override that here. */
  html{{height:auto}}
  body.legal{{background:var(--bg,#0b0e12);color:var(--txt,#e8eef5);margin:0;
    font:15px/1.65 Inter,system-ui,-apple-system,sans-serif;
    display:block;height:auto;min-height:100vh;overflow:visible}}
  .lg-top{{display:flex;align-items:center;gap:16px;flex-wrap:wrap;padding:14px 20px;
    border-bottom:1px solid var(--line,#222b36);position:sticky;top:0;background:rgba(11,14,18,.92);backdrop-filter:blur(6px)}}
  .lg-brand{{font-weight:800;font-size:18px;letter-spacing:.5px;color:var(--txt);text-decoration:none}}
  .lg-brand span{{color:var(--accent,#e8743b)}}
  .lg-nav{{display:flex;gap:14px;flex-wrap:wrap;margin-left:auto;font-size:13px}}
  .lg-nav a{{color:var(--mut,#9aa7b4);text-decoration:none}}
  .lg-nav a:hover,.lg-nav a.on{{color:var(--accent)}}
  .lg-wrap{{max-width:820px;margin:0 auto;padding:30px 20px 90px}}
  .lg-wrap h1{{font-size:26px;margin:.2em 0 .1em}}
  .lg-upd{{color:var(--mut);font-size:13px;margin-bottom:22px}}
  .lg-wrap h2{{font-size:18px;margin:30px 0 6px;border-top:1px solid var(--line);padding-top:22px}}
  .lg-wrap h2:first-of-type{{border-top:0;padding-top:0}}
  .lg-wrap h3{{font-size:15px;margin:16px 0 4px;color:var(--txt)}}
  .lg-wrap p,.lg-wrap li{{color:var(--txt2,#c9d4df)}}
  .lg-wrap ul{{padding-left:20px}} .lg-wrap li{{margin:4px 0}}
  .lg-wrap a{{color:var(--accent)}}
  .lg-contact{{background:rgba(255,255,255,.03);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin:14px 0}}
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
    {body}
    <div class="lg-foot">Questions about this policy? Contact <a href="mailto:{contact}">{contact}</a>.</div>
  </main>
</body></html>""".format(title=html.escape(title), site=html.escape(SITE), nav=nav,
                         updated=html.escape(UPDATED), body=body, contact=c)


def _contact_block():
    return ('<div class="lg-contact"><strong>%s</strong><br>Email: '
            '<a href="mailto:%s">%s</a><br>Location: %s</div>'
            % (SITE, html.escape(CONTACT), html.escape(CONTACT), LOCATION))


def _cookies():
    cb = _contact_block()
    return """
<p>This Cookie Policy explains how {site} ("{site}," "we," "us," or "our") uses cookies, localStorage,
sessionStorage, and similar technologies when you use https://{domain} and related services (the
"Services").</p>
<p>This policy should be read together with our Privacy Policy and Terms of Service. This document is
intended to describe our cookie and storage practices in plain language. It is not legal advice, and
cookie/consent requirements should be reviewed by qualified legal counsel before public launch.</p>
<h2>Contact</h2>
{cb}
<h2>1. What are cookies and similar technologies?</h2>
<p>Cookies are small text files stored on your device by a website. Similar technologies include
localStorage, sessionStorage, pixels, tags, SDKs, and other browser storage or tracking tools.</p>
<p>{site} may use these technologies to keep the site working, remember your choices, secure accounts,
improve performance, and, if enabled, understand how the Services are used.</p>
<h2>2. Types of cookies and storage we may use</h2>
<h3>Strictly necessary cookies and storage</h3>
<p>These are required for the Services to work. They may be used for:</p>
<ul>
  <li>Login and session management.</li><li>Steam authentication state.</li>
  <li>Security and abuse prevention.</li><li>CSRF or anti-forgery protection.</li>
  <li>Remembering privacy/cookie choices.</li><li>Keeping upload/replay workflows functional.</li>
</ul>
<p>Because these are necessary to provide the Services, they may be used without optional consent where
allowed by law.</p>
<h3>Preference cookies and storage</h3>
<p>These remember choices you make, such as:</p>
<ul>
  <li>Replay settings.</li><li>Display and UI preferences.</li><li>Tutorial/onboarding state.</li>
  <li>Selected dashboard/library settings.</li><li>Accessibility or reduced-motion preferences.</li>
</ul>
<h3>Analytics cookies and storage</h3>
<p>If enabled, analytics tools may help us understand:</p>
<ul>
  <li>Which pages and features are used.</li><li>Site performance and errors.</li>
  <li>Upload, parsing, and replay reliability.</li><li>General usage trends.</li>
</ul>
<p>Where required by law, we should ask for consent before using non-essential analytics cookies or
similar tracking technologies.</p>
<h3>Marketing or advertising cookies</h3>
<p>{site} does not currently intend to use advertising cookies. If we later use advertising,
retargeting, cross-site tracking, or similar technologies, we should update this policy and provide
required notice, consent, and opt-out choices before enabling them.</p>
<h3>Third-party cookies and embeds</h3>
<p>Some features may use third-party services, such as Steam login, payment providers, video embeds,
hosting, analytics, or support tools. These third parties may set their own cookies or use similar
technologies. Their use is governed by their own privacy and cookie policies.</p>
<h2>3. Why we use cookies and similar technologies</h2>
<ul>
  <li>Provide and maintain the Services.</li>
  <li>Authenticate users and keep accounts secure.</li>
  <li>Remember your settings and preferences.</li>
  <li>Save privacy/cookie choices.</li>
  <li>Support upload, parsing, replay, and dashboard features.</li>
  <li>Detect and prevent fraud, abuse, DDoS attempts, spam, and unauthorized access.</li>
  <li>Diagnose errors and improve performance.</li>
  <li>Measure usage if analytics tools are enabled.</li>
  <li>Support subscriptions or billing status if paid plans are enabled.</li>
</ul>
<h2>4. Cookie consent and preferences</h2>
<p>If we use only strictly necessary cookies/storage, a simple notice may be sufficient in some
locations. If we use analytics, marketing, advertising, or other non-essential cookies/storage, we
should provide a cookie banner or preference center where required.</p>
<p>The preference center should allow users to:</p>
<ul>
  <li>Accept all non-essential cookies.</li><li>Reject non-essential cookies.</li>
  <li>Choose categories of cookies.</li><li>Change or withdraw consent later.</li>
</ul>
<p>Refusing non-essential cookies should not block access to core Services, but some optional features
may not work the same way.</p>
<h2>5. Do not sell or share / targeted advertising</h2>
<p>We do not intend to sell personal information. If we later use advertising or analytics tools that
qualify as "sharing" for cross-context behavioral advertising, targeted advertising, or similar
concepts under applicable privacy laws, we should provide required opt-out mechanisms, such as a "Do
Not Sell or Share My Personal Information" or equivalent link.</p>
<h2>6. How long cookies last</h2>
<ul>
  <li>Session-based, meaning they expire when you close your browser.</li>
  <li>Persistent, meaning they remain until they expire or you delete them.</li>
</ul>
<p>Retention periods depend on the specific cookie or storage item. We should keep cookies and stored
preferences only as long as needed for the purpose described.</p>
<h2>7. How to control cookies</h2>
<ul>
  <li>The {site} cookie preference center, if available.</li><li>Your browser settings.</li>
  <li>Device privacy settings.</li><li>Third-party opt-out tools, where applicable.</li>
</ul>
<p>If you block or delete cookies, some Services may not work correctly. For example, you may be signed
out, replay preferences may reset, or cookie consent choices may need to be set again.</p>
<h2>8. Changes to this Cookie Policy</h2>
<p>We may update this Cookie Policy from time to time. When we do, we will update the "Last updated"
date above.</p>
<h2>9. Contact us</h2>
<p>If you have questions about this Cookie Policy, contact us at:</p>
{cb}
""".format(site=SITE, domain=DOMAIN, cb=cb)


def _privacy():
    cb = _contact_block()
    return """
<p>This Privacy Policy explains how {site} ("{site}," "we," "us," or "our") collects, uses, stores,
shares, and protects information when you use https://{domain} and any related products or services
that link to this Privacy Policy (collectively, the "Services").</p>
<p>{site} is a web-based Counter-Strike 2 demo review, replay, analytics, and coaching platform for
players and teams. Users can upload CS2 demo files to generate 2D and 3D replay tools, match
statistics, performance trends, utility and grenade analysis, team review features, practice goals,
and coaching-focused insights.</p>
<p>This Privacy Policy is intended to describe our current privacy practices in plain language. It is
not legal advice. Privacy laws vary by location, and this policy should be reviewed by qualified legal
counsel before public launch or commercial use.</p>
<h2>Contact</h2>
<p>If you have questions about this Privacy Policy or want to make a privacy request, contact us at:</p>
{cb}
<h2>1. Information we collect</h2>
<h3>Account and Steam information</h3>
<ul>
  <li>Steam ID or SteamID64.</li>
  <li>Steam profile name, avatar, and public Steam profile information if available through Steam login or the Steam Web API.</li>
  <li>Login/session information needed to authenticate your account.</li>
  <li>Account display name, team memberships, roles, subscription tier, and admin/helper status if applicable.</li>
</ul>
<h3>Uploaded demo and gameplay information</h3>
<ul>
  <li>CS2 demo files that you upload, including raw .dem files while they are being uploaded, parsed, processed, stored, or deleted.</li>
  <li>Compressed upload files such as .zip, .bz2, or .rar when supported.</li>
  <li>Match metadata, including map, date/time, rounds, score, teams, player names, player Steam IDs, weapons, utility, kills, deaths, assists, damage, economy, positions, grenade events, replay frames, and other gameplay events extracted from demos.</li>
  <li>Generated analytics, such as ratings, trends, goals, coaching insights, utility analysis, team-play analysis, and profile history.</li>
  <li>Retained compact stats after a replay is deleted, if you choose to delete replay files but keep long-term statistics.</li>
</ul>
<h3>Team and collaboration information</h3>
<ul><li>Team names, team membership, invite codes, shared demos, team demo visibility, team roles,
practice goals, notes, bookmarks, reviews, playbook items, and saved utility/nade library entries.</li></ul>
<h3>Upload, parsing, and admin/ops information</h3>
<ul>
  <li>Upload start and finish times, upload duration, parse start and finish times, parse duration, queue wait time, job status, file size, archive type, parse failures, error logs, and diagnostic information.</li>
  <li>Admin-visible logs may include technical details needed to troubleshoot uploads, parsing, security, abuse, storage, or service reliability.</li>
</ul>
<h3>Preferences and device information</h3>
<ul>
  <li>Replay settings, display preferences, selected controls, tutorial/onboarding state, and saved local preferences.</li>
  <li>Browser type, device type, IP address, approximate location derived from IP address, operating system, timestamps, pages viewed, and security logs.</li>
  <li>Cookies, localStorage, sessionStorage, or similar technologies as described below.</li>
</ul>
<h3>Payment and subscription information</h3>
<ul>
  <li>If paid features are enabled, we may receive limited payment/subscription information from our payment processor, Stripe, such as subscription status, plan, billing period, and transaction identifiers.</li>
  <li>We do not store full payment card numbers directly. Payment card processing is handled by our third-party payment processor, Stripe, under its own terms and privacy policy.</li>
</ul>
<h3>Support and communications</h3>
<ul><li>Emails, support requests, feedback, reports, and any information you choose to send us.</li></ul>
<h2>2. How we collect information</h2>
<ul>
  <li>You create or log in to an account.</li><li>You authenticate through Steam.</li>
  <li>You upload a demo or compressed archive.</li><li>The Services parse or analyze a demo.</li>
  <li>You create teams, goals, notes, bookmarks, playbooks, nade entries, or other saved content.</li>
  <li>You use replay, analytics, dashboard, team, admin, or settings features.</li>
  <li>You contact us for support.</li>
  <li>Your browser sends technical information needed to operate, secure, and improve the Services.</li>
</ul>
<h2>3. How we use information</h2>
<ul>
  <li>Provide, operate, and maintain the Services.</li>
  <li>Authenticate users and manage sessions.</li>
  <li>Process uploaded CS2 demos and generate replay/analytics features.</li>
  <li>Display personal and team demo libraries.</li>
  <li>Provide statistics, trends, coaching insights, utility analysis, team review, goals, and practice tools.</li>
  <li>Preserve compact historical stats after replay files are deleted, where applicable.</li>
  <li>Enable team collaboration and shared demo access.</li>
  <li>Diagnose upload, parse, replay, and account issues.</li>
  <li>Detect, prevent, and respond to fraud, abuse, unauthorized access, DDoS attempts, spam, security incidents, and violations of our Terms.</li>
  <li>Improve performance, reliability, user experience, and product features.</li>
  <li>Provide support and respond to user requests.</li>
  <li>Manage subscriptions, billing status, and account entitlements if paid plans are enabled.</li>
  <li>Comply with legal obligations and enforce our Terms.</li>
</ul>
<h2>4. Steam data</h2>
<p>{site} may use Steam login and Steam-related identifiers to authenticate users and connect uploaded
demos to player profiles. Steam data may include your Steam ID, public persona name, avatar, and other
public profile information made available by Steam.</p>
<p>We use Steam data to: let you sign in; identify your player profile in uploaded demos; associate
stats, goals, trends, and team data with your account; and display your name/avatar where appropriate.</p>
<p>We do not control Steam's own privacy practices. Steam data is also subject to Valve/Steam policies
and your Steam privacy settings.</p>
<h2>5. Cookies and similar technologies</h2>
<p>We may use cookies, localStorage, sessionStorage, and similar technologies to keep you signed in,
remember settings and preferences, store tutorial/onboarding state, support security and abuse
prevention, improve site functionality and performance, and measure usage or analytics if analytics
tools are enabled.</p>
<p>Strictly necessary cookies/storage are used to operate the Services. If we use non-essential
analytics, advertising, marketing, or third-party tracking technologies, we should provide notice and,
where required, ask for consent before enabling them. You may be able to control cookies through your
browser settings, but disabling necessary cookies may prevent parts of the Services from working. See
our <a href="/cookies">Cookie Policy</a> for details.</p>
<h2>6. How we share information</h2>
<p>We do not sell your uploaded demos. We may share information in the following limited situations:</p>
<h3>With service providers</h3>
<p>We may share information with vendors who help us host, secure, process, analyze, monitor, support,
or bill for the Services. These providers should only use the information to provide services to us.</p>
<h3>With team members</h3>
<p>If you join a team or share/upload a demo to a team, other members of that team may be able to view
team demos, team match data, notes, goals, statistics, and related collaboration information according
to the app's permissions.</p>
<h3>With payment processors</h3>
<p>If paid subscriptions are enabled, payment information is processed by our third-party payment
provider, Stripe. We do not store full payment card numbers directly.</p>
<h3>For legal, safety, and security reasons</h3>
<p>We may disclose information if we believe it is necessary to comply with law, enforce our Terms,
protect users, investigate abuse or security incidents, prevent fraud, or protect the rights,
property, or safety of {site} or others.</p>
<h3>Business transfers</h3>
<p>If {site} is involved in a merger, acquisition, financing, reorganization, bankruptcy, or sale of
assets, information may be transferred as part of that transaction.</p>
<h3>With your direction or consent</h3>
<p>We may share information if you ask us to or consent to it.</p>
<h2>7. User-generated content and team sharing</h2>
<p>Uploaded demos, notes, playbooks, nade entries, and team information may contain information about
other players. You are responsible for ensuring you have the right to upload and share content through
the Services.</p>
<p>Personal demos should be visible only according to your account permissions. Team demos may be
visible to members of the team they are shared with. Do not upload content that you do not have the
right to use or share.</p>
<h2>8. Retention and deletion</h2>
<p>We keep information for as long as needed to provide the Services, comply with legal obligations,
resolve disputes, enforce our Terms, maintain security, and improve the Services.</p>
<h3>Raw demo and replay files</h3>
<p>Raw .dem files, compressed archives, full parsed replay caches, frames, grenade paths, tick-by-tick
replay data, and similar large replay files may be retained while a demo is active and replayable. If
you delete a demo from your library, these storage-heavy files should be removed unless another user or
team still legitimately owns or uses the same replay.</p>
<h3>Compact retained stats</h3>
<p>When a replay is deleted, we may keep a tiny compact stats record so your long-term profile, goals,
trends, team/site aggregate learning, and historical analytics can continue to work. This retained
record should not include full replay data, raw demo files, frame-by-frame data, grenade paths, or
large replay-only payloads.</p>
<h3>Account deletion</h3>
<p>If you request account deletion, we will delete or anonymize account-related personal information as
required by applicable law and our operational needs. Some information may be retained where necessary
for security, legal compliance, abuse prevention, financial records, backups, or legitimate business
purposes.</p>
<h3>Backups and logs</h3>
<p>Deleted information may remain in backups or logs for a limited period until overwritten or deleted
according to our backup and retention practices.</p>
<h2>9. Security</h2>
<p>We use reasonable technical and organizational measures designed to protect information from
unauthorized access, loss, misuse, alteration, or disclosure. These measures may include
authentication, access controls, HTTPS, logging, rate limits, abuse prevention, security headers, and
administrative safeguards.</p>
<p>No online service can guarantee perfect security. You are responsible for keeping your account
credentials and Steam account secure and for notifying us if you believe your account or data has been
compromised.</p>
<h2>10. Your privacy choices and rights</h2>
<p>Depending on where you live, you may have rights to:</p>
<ul>
  <li>Access the personal information we hold about you.</li>
  <li>Correct inaccurate information.</li><li>Delete certain personal information.</li>
  <li>Receive a copy of certain information.</li>
  <li>Object to or restrict certain processing.</li>
  <li>Opt out of certain sharing, sale, targeted advertising, or profiling if applicable.</li>
  <li>Withdraw cookie or non-essential tracking consent where applicable.</li>
</ul>
<p>To make a request, contact us at <a href="mailto:{contact}">{contact}</a>. We may need to verify
your identity before processing your request. We will not discriminate against you for exercising
privacy rights required by law.</p>
<h2>11. California and other U.S. state privacy rights</h2>
<p>Some U.S. state privacy laws, including the California Consumer Privacy Act as amended, may give
eligible residents additional rights. These may include the right to know what personal information is
collected, used, shared, or sold; the right to delete; the right to correct; the right to opt out of
certain sale or sharing; and the right not to be discriminated against for exercising privacy rights.</p>
<p>We do not intend to sell personal information. If we later use advertising or analytics technologies
that count as "sharing" or targeted advertising under applicable law, we should provide required
notices and opt-out mechanisms.</p>
<h2>12. International users</h2>
<p>The Services are operated from the United States. If you access the Services from outside the United
States, you understand that your information may be processed and stored in the United States or other
countries where our service providers operate. These countries may have data protection laws different
from those in your country.</p>
<p>If we serve users in the European Economic Area, United Kingdom, or similar jurisdictions,
additional legal bases, transfer safeguards, cookie consent, and data subject rights may apply and
should be reviewed before launch.</p>
<h2>13. Children's privacy</h2>
<p>The Services are intended for users who are at least 18 years old. We do not knowingly collect
personal information from children under 13. If we learn that we have collected personal information
from a child in violation of applicable law, we will take appropriate steps to delete it.</p>
<h2>14. Third-party links and services</h2>
<p>The Services may link to or integrate with third-party services, including Steam, hosting providers,
analytics providers, payment processors, or video platforms. We are not responsible for the privacy
practices of third parties. You should review their privacy policies before using them.</p>
<h2>15. Changes to this Privacy Policy</h2>
<p>We may update this Privacy Policy from time to time. When we do, we will update the "Last updated"
date above. Your continued use of the Services after changes are posted means you acknowledge the
updated Privacy Policy.</p>
<h2>16. Contact us</h2>
<p>If you have questions, concerns, or privacy requests, contact us at:</p>
{cb}
""".format(site=SITE, domain=DOMAIN, cb=cb, contact=html.escape(CONTACT))


def _refunds():
    cb = _contact_block()
    return """
<p>This Refund and Cancellation Policy explains how refunds, cancellations, renewals, and billing
issues work for paid {site} subscriptions and services. This policy should be read together with our
<a href="/terms">Terms of Service</a> and <a href="/privacy">Privacy Policy</a>.</p>
<p>This document is intended to be a practical public-facing policy. It is not legal advice, and
subscription, refund, tax, and consumer-protection requirements should be reviewed by qualified legal
counsel before public launch.</p>
<h2>Contact</h2>
{cb}
<h2>1. Overview</h2>
<p>{site} is a web-based Counter-Strike 2 demo review, replay, analytics, and coaching platform. Some
features may be offered for free, while advanced features may require a paid subscription or paid plan.</p>
<p>Paid features may include, depending on the plan:</p>
<ul>
  <li>Expanded demo uploads or storage.</li><li>3D replay features.</li>
  <li>Advanced analytics and trends.</li><li>Team workspace features.</li>
  <li>Practice goals and coaching tools.</li><li>Utility, grenade, and review tools.</li>
</ul>
<h2>2. Subscriptions and renewals</h2>
<p>If you purchase a subscription, your subscription may automatically renew at the end of each billing
period unless you cancel before renewal.</p>
<p>Before you are charged, the checkout flow should clearly show: the plan you are buying; the price;
the billing period; whether the subscription renews automatically; how to cancel; and any trial or
promotional terms, if offered.</p>
<h2>3. Cancellation</h2>
<p>You may cancel your subscription at any time by using the account or billing settings provided in
the Services, if available, or by contacting us at <a href="mailto:{contact}">{contact}</a>.</p>
<p>If you cancel:</p>
<ul>
  <li>You will generally keep access to paid features until the end of the current paid billing period.</li>
  <li>Your subscription will not renew after the current paid period ends.</li>
  <li>Canceling does not automatically delete your account or uploaded data.</li>
  <li>You may still be able to use free features after cancellation, depending on the plan and account status.</li>
</ul>
<p>We should make cancellation reasonably easy and not more difficult than signing up. If online signup
is available, online cancellation should also be available or clearly supported.</p>
<h2>4. Refunds</h2>
<p>Unless required by applicable law or stated otherwise at checkout, subscription fees are generally
non-refundable once a billing period has started.</p>
<p>However, we may review refund requests on a case-by-case basis. Refunds may be considered when:</p>
<ul>
  <li>You were charged because of a clear billing error.</li>
  <li>You were charged after a timely cancellation request that we failed to process.</li>
  <li>You experienced a significant service failure that prevented meaningful use of paid features.</li>
  <li>Duplicate charges occurred.</li><li>Applicable law requires a refund.</li>
</ul>
<p>Refunds are not generally provided for:</p>
<ul>
  <li>Partial use of a billing period.</li><li>Forgetting to cancel before renewal.</li>
  <li>Dissatisfaction after substantial use of paid features.</li>
  <li>Loss of access caused by violation of our Terms.</li>
  <li>Issues caused by unsupported devices, browsers, local files, network conditions, or third-party services outside our control.</li>
</ul>
<h2>5. How to request a refund</h2>
<p>To request a refund, contact us at <a href="mailto:{contact}">{contact}</a>. Please include:</p>
<ul>
  <li>Your account email or Steam ID, if applicable.</li>
  <li>The date and amount of the charge.</li><li>The plan or subscription involved.</li>
  <li>The reason for the refund request.</li>
  <li>Any relevant screenshots or billing details.</li>
</ul>
<p>We may ask for additional information to verify your account and the charge.</p>
<h2>6. Processing refunds</h2>
<p>If a refund is approved: refunds will usually be returned to the original payment method via our
payment processor, Stripe; processing time may depend on Stripe, your bank, or card network; and
access to paid features may be removed or downgraded after the refund. If a refund is denied, we will
try to explain the reason.</p>
<h2>7. Free trials and promotions</h2>
<p>If {site} offers a free trial or promotional price, the terms should be shown at signup. Trial or
promotional terms may include trial length; price after the trial or promotion ends; whether a payment
method is required; when billing starts; and how to cancel before being charged.</p>
<h2>8. Plan changes</h2>
<p>If you upgrade or downgrade your plan, changes may take effect immediately or at the next billing
period depending on the billing system. Any credits, prorations, or price changes should be shown
before the change is confirmed where practical.</p>
<h2>9. Service outages</h2>
<p>{site} may occasionally experience downtime, maintenance, upload delays, parsing delays, replay
issues, or third-party service problems. Short interruptions do not automatically entitle users to a
refund. If there is a major extended outage that materially affects paid features, we may choose to
offer credits, extensions, or refunds at our discretion or as required by law.</p>
<h2>10. Account termination or Terms violations</h2>
<p>If your account is suspended or terminated because you violated our Terms of Service, abused the
Services, attempted unauthorized access, uploaded prohibited content, attacked the service, or engaged
in fraud, you may lose access to paid features and may not be eligible for a refund, except where
required by law.</p>
<h2>11. Data after cancellation</h2>
<p>Canceling a paid subscription does not automatically delete your account, personal data, uploaded
demos, or retained compact stats. You may request deletion or manage data according to the Privacy
Policy and account tools available in the Services. If your plan changes to a free tier, certain
storage limits or feature limits may apply.</p>
<h2>12. Chargebacks and payment disputes</h2>
<p>If you believe a charge is incorrect, please contact us first so we can try to resolve it.
Chargebacks or payment disputes may result in account review, temporary suspension of paid access, or
additional verification.</p>
<h2>13. Changes to this policy</h2>
<p>We may update this Refund and Cancellation Policy from time to time. When we do, we will update the
"Last updated" date above. Updated terms will apply to future purchases, renewals, and refund requests
unless applicable law requires otherwise.</p>
<h2>14. Contact us</h2>
<p>For cancellation, billing, or refund questions, contact us at:</p>
{cb}
""".format(site=SITE, cb=cb, contact=html.escape(CONTACT))


def _terms():
    return """
<h2>Agreement to our legal terms</h2>
<p>We are {site} ("Company," "we," "us," "our"), based in {loc}.</p>
<p>We operate the website https://{domain} (the "Site"), as well as any other related products and
services that refer or link to these legal terms (the "Legal Terms") (collectively, the "Services").</p>
<p>{site} is a web-based Counter-Strike 2 demo review, replay, analytics, and coaching platform for
players and teams. Users can upload CS2 demo files to generate 2D and 3D replay tools, match
statistics, performance trends, utility and grenade analysis, team review features, practice goals, and
coaching-focused insights. The platform may process uploaded demo files, Steam account identifiers,
match metadata, gameplay events, player statistics, team information, saved preferences, upload and
parsing records, retained compact stats, and related analytics in order to provide replay, analysis,
storage, profile, team collaboration, security, and service-improvement features.</p>
<p>You can contact us by email at <a href="mailto:{contact}">{contact}</a>. {site} is based in {loc}.</p>
<p>These Legal Terms constitute a legally binding agreement made between you, whether personally or on
behalf of an entity ("you"), and {site}, concerning your access to and use of the Services. You agree
that by accessing the Services, you have read, understood, and agreed to be bound by all of these Legal
Terms. IF YOU DO NOT AGREE WITH ALL OF THESE LEGAL TERMS, THEN YOU ARE EXPRESSLY PROHIBITED FROM USING
THE SERVICES AND YOU MUST DISCONTINUE USE IMMEDIATELY.</p>
<p>Supplemental terms and conditions or documents that may be posted on the Services from time to time
are hereby expressly incorporated herein by reference. We reserve the right, in our sole discretion, to
make changes or modifications to these Legal Terms at any time and for any reason. We will alert you
about any changes by updating the "Last updated" date of these Legal Terms, and you waive any right to
receive specific notice of each such change. It is your responsibility to periodically review these
Legal Terms to stay informed of updates. You will be subject to, and will be deemed to have been made
aware of and to have accepted, the changes in any revised Legal Terms by your continued use of the
Services after the date such revised Legal Terms are posted.</p>
<p>The Services are intended for users who are at least 18 years old. Persons under the age of 18 are
not permitted to use or register for the Services.</p>
<p>We recommend that you print a copy of these Legal Terms for your records.</p>
<h2>1. Our services</h2>
<p>The information provided when using the Services is not intended for distribution to or use by any
person or entity in any jurisdiction or country where such distribution or use would be contrary to law
or regulation or which would subject us to any registration requirement within such jurisdiction or
country. Accordingly, those persons who choose to access the Services from other locations do so on
their own initiative and are solely responsible for compliance with local laws, if and to the extent
local laws are applicable.</p>
<p>The Services are not tailored to comply with industry-specific regulations (Health Insurance
Portability and Accountability Act (HIPAA), Federal Information Security Management Act (FISMA), etc.),
so if your interactions would be subjected to such laws, you may not use the Services. You may not use
the Services in a way that would violate the Gramm-Leach-Bliley Act (GLBA).</p>
<h2>2. Intellectual property rights</h2>
<h3>Our intellectual property</h3>
<p>We are the owner or the licensee of all intellectual property rights in our Services, including all
source code, databases, functionality, software, website designs, audio, video, text, photographs, and
graphics in the Services (collectively, the "Content"), as well as the trademarks, service marks, and
logos contained therein (the "Marks"). Our Content and Marks are protected by copyright and trademark
laws and treaties in the United States and around the world. The Content and Marks are provided in or
through the Services "AS IS" for your personal, non-commercial use or internal business purpose only.</p>
<h3>Your use of our Services</h3>
<p>Subject to your compliance with these Legal Terms, including the "Prohibited activities" section
below, we grant you a non-exclusive, non-transferable, revocable license to access the Services and
download or print a copy of any portion of the Content to which you have properly gained access, solely
for your personal, non-commercial use or internal business purpose.</p>
<p>Except as set out in this section or elsewhere in our Legal Terms, no part of the Services and no
Content or Marks may be copied, reproduced, aggregated, republished, uploaded, posted, publicly
displayed, encoded, translated, transmitted, distributed, sold, licensed, or otherwise exploited for
any commercial purpose whatsoever, without our express prior written permission. If you wish to make
any use of the Services, Content, or Marks other than as set out here, please address your request to
<a href="mailto:{contact}">{contact}</a>. We reserve all rights not expressly granted to you in and to
the Services, Content, and Marks. Any breach of these Intellectual Property Rights will constitute a
material breach of our Legal Terms and your right to use our Services will terminate immediately.</p>
<h3>Your submissions and contributions</h3>
<p><strong>Submissions:</strong> By directly sending us any question, comment, suggestion, idea,
feedback, or other information about the Services ("Submissions"), you agree to assign to us all
intellectual property rights in such Submission. You agree that we shall own this Submission and be
entitled to its unrestricted use and dissemination for any lawful purpose, commercial or otherwise,
without acknowledgment or compensation to you.</p>
<p><strong>Contributions:</strong> The Services may invite you to chat, contribute to, or participate
in blogs, message boards, online forums, and other functionality during which you may create, submit,
post, display, transmit, publish, distribute, or broadcast content and materials ("Contributions").
Any Submission that is publicly posted shall also be treated as a Contribution. You understand that
Contributions may be viewable by other users of the Services.</p>
<p>By posting any Contributions, you grant us an unrestricted, unlimited, irrevocable, perpetual,
non-exclusive, transferable, royalty-free, fully-paid, worldwide right and license to use, copy,
reproduce, distribute, sell, resell, publish, broadcast, retitle, store, publicly perform, publicly
display, reformat, translate, excerpt (in whole or in part), and exploit your Contributions (including
your image, name, and voice) for any purpose, and to sublicense the licenses granted in this section.
This license includes our use of your name, company name, and franchise name, and any of the
trademarks, service marks, trade names, logos, and personal and commercial images you provide.</p>
<p>You are responsible for what you post or upload. You confirm that you will not post any Contribution
that is illegal, harassing, hateful, harmful, defamatory, obscene, bullying, abusive, discriminatory,
threatening, sexually explicit, false, inaccurate, deceitful, or misleading; you waive any and all moral
rights to the extent permissible by law; you warrant that your Contributions are original to you or that
you have the necessary rights and licenses; and you warrant that your Contributions do not constitute
confidential information. You are solely responsible for your Submissions and/or Contributions and you
agree to reimburse us for any losses we may suffer because of your breach of this section, any third
party's intellectual property rights, or applicable law.</p>
<p>We may remove or edit your Content at any time without notice if in our reasonable opinion we
consider such Contributions harmful or in breach of these Legal Terms, and we may also suspend or
disable your account and report you to the authorities.</p>
<h2>3. User representations</h2>
<p>By using the Services, you represent and warrant that: (1) you have the legal capacity and you agree
to comply with these Legal Terms; (2) you are not a minor in the jurisdiction in which you reside; (3)
you will not access the Services through automated or non-human means; (4) you will not use the Services
for any illegal or unauthorized purpose; and (5) your use of the Services will not violate any
applicable law or regulation. If you provide any information that is untrue, inaccurate, not current, or
incomplete, we have the right to suspend or terminate your account.</p>
<h2>4. Purchases and payment</h2>
<p>We accept the following forms of payment: Visa, Mastercard, and American Express. Payments are
processed securely through our third-party payment processor, Stripe. We do not store full payment
card numbers ourselves.</p>
<p>You agree to provide current, complete, and accurate purchase and account information for all
purchases made via the Services. You further agree to promptly update account and payment information,
including email address, payment method, and payment card expiration date, so that we can complete your
transactions and contact you as needed. Sales tax will be added to the price of purchases as deemed
required by us. We may change prices at any time. All payments shall be in US dollars.</p>
<p>You agree to pay all charges at the prices then in effect for your purchases, and you authorize us to
charge your chosen payment provider for any such amounts upon placing your order. We reserve the right
to correct any errors or mistakes in pricing, even if we have already requested or received payment. We
reserve the right to refuse any order placed through the Services.</p>
<h2>5. Subscriptions</h2>
<h3>Billing and renewal</h3>
<p>Your subscription will continue and automatically renew unless canceled. You consent to our charging
your payment method on a recurring basis without requiring your prior approval for each recurring
charge, until such time as you cancel the applicable order. The length of your billing cycle will
depend on the type of subscription plan you choose.</p>
<h3>Cancellation</h3>
<p>You can cancel your subscription at any time by logging into your account. Your cancellation will
take effect at the end of the current paid term. If you have any questions or are unsatisfied with our
Services, please email us at <a href="mailto:{contact}">{contact}</a>.</p>
<h3>Fee changes</h3>
<p>We may, from time to time, make changes to the subscription fee and will communicate any price
changes to you in accordance with applicable law.</p>
<h2>6. Prohibited activities</h2>
<p>You may not access or use the Services for any purpose other than that for which we make the Services
available. The Services may not be used in connection with any commercial endeavors except those that
are specifically endorsed or approved by us. As a user of the Services, you agree not to:</p>
<ul>
  <li>Systematically retrieve data or other content to create or compile a collection, compilation, database, or directory without written permission from us.</li>
  <li>Trick, defraud, or mislead us and other users, especially to learn sensitive account information such as user passwords.</li>
  <li>Circumvent, disable, or otherwise interfere with security-related features of the Services.</li>
  <li>Disparage, tarnish, or otherwise harm, in our opinion, us and/or the Services.</li>
  <li>Use any information obtained from the Services to harass, abuse, or harm another person.</li>
  <li>Make improper use of our support services or submit false reports of abuse or misconduct.</li>
  <li>Use the Services in a manner inconsistent with any applicable laws or regulations.</li>
  <li>Engage in unauthorized framing of or linking to the Services.</li>
  <li>Upload or transmit viruses, Trojan horses, or other material (including spam) that interferes with the use of the Services or modifies, impairs, disrupts, alters, or interferes with the Services.</li>
  <li>Engage in any automated use of the system, such as scripts, data mining, robots, or similar tools.</li>
  <li>Delete the copyright or other proprietary rights notice from any Content.</li>
  <li>Attempt to impersonate another user or person.</li>
  <li>Upload or transmit any passive or active information collection mechanism (gifs, 1x1 pixels, web bugs, cookies, or similar "spyware").</li>
  <li>Interfere with, disrupt, or create an undue burden on the Services or connected networks.</li>
  <li>Harass, annoy, intimidate, or threaten any of our employees or agents.</li>
  <li>Attempt to bypass measures designed to prevent or restrict access to the Services.</li>
  <li>Copy or adapt the Services' software, including HTML, JavaScript, or other code.</li>
  <li>Decipher, decompile, disassemble, or reverse engineer any of the software, except as permitted by law.</li>
  <li>Use, launch, develop, or distribute any automated system (spider, robot, cheat utility, scraper, or offline reader), or any unauthorized script.</li>
  <li>Use a buying agent or purchasing agent to make purchases on the Services.</li>
  <li>Collect usernames and/or email addresses to send unsolicited email, or create accounts by automated means or under false pretenses.</li>
  <li>Use the Services to compete with us or for any revenue-generating endeavor or commercial enterprise not approved by us.</li>
  <li>Sell or otherwise transfer your profile.</li>
</ul>
<h2>7. User generated contributions</h2>
<p>When you create or make available any Contributions, you represent and warrant that: your
Contributions do not infringe any third party's proprietary rights; you have the necessary rights and
permissions, including from each identifiable person depicted; your Contributions are not false,
inaccurate, or misleading; they are not unsolicited advertising, spam, or solicitation; they are not
obscene, harassing, libelous, or otherwise objectionable; they do not ridicule, mock, disparage,
intimidate, or abuse anyone; they do not violate any applicable law, privacy or publicity rights, or
any law concerning the protection of minors; and they do not otherwise violate these Legal Terms. Any
use of the Services in violation of the foregoing may result in termination or suspension of your
rights to use the Services.</p>
<h2>8. Contribution license</h2>
<p>By posting your Contributions to any part of the Services, you grant us an unrestricted, unlimited,
irrevocable, perpetual, non-exclusive, transferable, royalty-free, fully-paid, worldwide right and
license to host, use, copy, reproduce, disclose, sell, resell, publish, broadcast, retitle, archive,
store, cache, publicly perform, publicly display, reformat, translate, transmit, excerpt, and
distribute such Contributions for any purpose, and to prepare derivative works of or incorporate them
into other works, and to sublicense the foregoing. We do not assert any ownership over your
Contributions; you retain full ownership of your Contributions and any associated intellectual property
rights. We have the right, in our sole discretion, to edit, re-categorize, pre-screen, or delete any
Contributions at any time and for any reason, without notice. We have no obligation to monitor your
Contributions.</p>
<h2>9. Services management</h2>
<p>We reserve the right, but not the obligation, to: (1) monitor the Services for violations of these
Legal Terms; (2) take appropriate legal action against anyone who violates the law or these Legal
Terms; (3) refuse, restrict access to, limit the availability of, or disable any of your Contributions;
(4) remove or disable files and content that are excessive in size or burdensome to our systems; and
(5) otherwise manage the Services to protect our rights and property and facilitate proper functioning.</p>
<h2>10. Privacy policy</h2>
<p>We care about data privacy and security. By using the Services, you agree to be bound by our
<a href="/privacy">Privacy Policy</a>, which is incorporated into these Legal Terms. The Services are
hosted in the United States. If you access the Services from another region with laws governing personal
data that differ from United States law, then through your continued use you are transferring your data
to the United States and you expressly consent to have your data transferred to and processed in the
United States.</p>
<h2>11. Copyright infringements</h2>
<p>We respect the intellectual property rights of others. If you believe that any material available on
or through the Services infringes upon any copyright you own or control, please immediately notify us
using the contact information provided below (a "Notification"). A copy of your Notification will be
sent to the person who posted or stored the material. You may be held liable for damages if you make
material misrepresentations in a Notification.</p>
<h2>12. Term and termination</h2>
<p>These Legal Terms shall remain in full force and effect while you use the Services. WITHOUT LIMITING
ANY OTHER PROVISION OF THESE LEGAL TERMS, WE RESERVE THE RIGHT TO, IN OUR SOLE DISCRETION AND WITHOUT
NOTICE OR LIABILITY, DENY ACCESS TO AND USE OF THE SERVICES TO ANY PERSON FOR ANY REASON OR FOR NO
REASON. We may terminate your use or participation in the Services or delete any content or information
that you posted at any time, without warning, in our sole discretion. If we terminate or suspend your
account, you are prohibited from registering and creating a new account under your name, a fake or
borrowed name, or the name of any third party.</p>
<h2>13. Modifications and interruptions</h2>
<p>We reserve the right to change, modify, or remove the contents of the Services at any time or for any
reason at our sole discretion without notice. We have no obligation to update any information on our
Services. We cannot guarantee the Services will be available at all times. We may experience hardware,
software, or other problems or need to perform maintenance, resulting in interruptions, delays, or
errors. You agree that we have no liability for any loss, damage, or inconvenience caused by your
inability to access or use the Services during any downtime or discontinuance.</p>
<h2>14. Governing law</h2>
<p>These Legal Terms and your use of the Services are governed by and construed in accordance with the
laws of the Commonwealth of Pennsylvania applicable to agreements made and to be entirely performed
within the Commonwealth of Pennsylvania, without regard to its conflict of law principles.</p>
<h2>15. Dispute resolution</h2>
<p>Any legal action of whatever nature brought by either you or us shall be commenced or prosecuted in
the state and federal courts located in Pennsylvania, and the Parties consent to, and waive all
defenses of lack of personal jurisdiction and forum non conveniens with respect to venue and
jurisdiction in such courts. Application of the United Nations Convention on Contracts for the
International Sale of Goods and the Uniform Computer Information Transaction Act (UCITA) are excluded.
In no event shall any claim related to the Services be commenced more than one (1) year after the cause
of action arose.</p>
<h2>16. Corrections</h2>
<p>There may be information on the Services that contains typographical errors, inaccuracies, or
omissions, including descriptions, pricing, availability, and other information. We reserve the right to
correct any errors, inaccuracies, or omissions and to change or update the information at any time,
without prior notice.</p>
<h2>17. Disclaimer</h2>
<p>THE SERVICES ARE PROVIDED ON AN AS-IS AND AS-AVAILABLE BASIS. YOU AGREE THAT YOUR USE OF THE SERVICES
WILL BE AT YOUR SOLE RISK. TO THE FULLEST EXTENT PERMITTED BY LAW, WE DISCLAIM ALL WARRANTIES, EXPRESS
OR IMPLIED, IN CONNECTION WITH THE SERVICES AND YOUR USE THEREOF, INCLUDING THE IMPLIED WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND NON-INFRINGEMENT. WE MAKE NO WARRANTIES ABOUT THE
ACCURACY OR COMPLETENESS OF THE SERVICES' CONTENT AND ASSUME NO LIABILITY FOR ANY ERRORS, PERSONAL
INJURY, UNAUTHORIZED ACCESS TO OUR SERVERS, INTERRUPTION OF TRANSMISSION, BUGS OR VIRUSES, OR ANY LOSS
OR DAMAGE RESULTING FROM USE OF THE SERVICES.</p>
<h2>18. Limitations of liability</h2>
<p>IN NO EVENT WILL WE OR OUR DIRECTORS, EMPLOYEES, OR AGENTS BE LIABLE TO YOU OR ANY THIRD PARTY FOR
ANY DIRECT, INDIRECT, CONSEQUENTIAL, EXEMPLARY, INCIDENTAL, SPECIAL, OR PUNITIVE DAMAGES, INCLUDING
LOST PROFIT, LOST REVENUE, LOSS OF DATA, OR OTHER DAMAGES ARISING FROM YOUR USE OF THE SERVICES, EVEN IF
WE HAVE BEEN ADVISED OF THE POSSIBILITY OF SUCH DAMAGES. OUR LIABILITY TO YOU FOR ANY CAUSE WHATSOEVER
WILL AT ALL TIMES BE LIMITED TO THE LESSER OF THE AMOUNT PAID, IF ANY, BY YOU TO US DURING THE SIX (6)
MONTH PERIOD PRIOR TO ANY CAUSE OF ACTION ARISING OR $100.00 USD.</p>
<h2>19. Indemnification</h2>
<p>You agree to defend, indemnify, and hold us harmless, including our subsidiaries, affiliates, and all
of our respective officers, agents, partners, and employees, from and against any loss, damage,
liability, claim, or demand, including reasonable attorneys' fees and expenses, made by any third party
due to or arising out of your Contributions, use of the Services, breach of these Legal Terms, breach of
your representations and warranties, your violation of the rights of a third party, or any harmful act
toward another user.</p>
<h2>20. User data</h2>
<p>We will maintain certain data that you transmit to the Services for the purpose of managing the
performance of the Services, as well as data relating to your use of the Services. Although we perform
regular routine backups of data, you are solely responsible for all data that you transmit or that
relates to any activity you have undertaken using the Services. You agree that we shall have no
liability to you for any loss or corruption of any such data.</p>
<h2>21. Electronic communications, transactions, and signatures</h2>
<p>Visiting the Services, sending us emails, and completing online forms constitute electronic
communications. You consent to receive electronic communications, and you agree that all agreements,
notices, disclosures, and other communications we provide to you electronically satisfy any legal
requirement that such communication be in writing. You agree to the use of electronic signatures,
contracts, orders, and other records, and to electronic delivery of notices, policies, and records.</p>
<h2>22. California users and residents</h2>
<p>If any complaint with us is not satisfactorily resolved, you can contact the Complaint Assistance
Unit of the Division of Consumer Services of the California Department of Consumer Affairs in writing at
1625 North Market Blvd., Suite N 112, Sacramento, California 95834 or by telephone at (800) 952-5210 or
(916) 445-1254.</p>
<h2>23. Miscellaneous</h2>
<p>These Legal Terms and any policies or operating rules posted by us constitute the entire agreement
and understanding between you and us. Our failure to exercise or enforce any right or provision shall
not operate as a waiver. We may assign any or all of our rights and obligations to others at any time.
We shall not be responsible or liable for any loss, damage, delay, or failure to act caused by any
cause beyond our reasonable control. If any provision or part of a provision is determined to be
unlawful, void, or unenforceable, that provision is deemed severable and does not affect the validity
of the remaining provisions. There is no joint venture, partnership, employment, or agency relationship
created between you and us as a result of these Legal Terms or use of the Services.</p>
<h2>24. Contact us</h2>
<p>In order to resolve a complaint regarding the Services or to receive further information regarding
use of the Services, please contact us at:</p>
{cb}
""".format(site=SITE, domain=DOMAIN, loc=LOCATION, contact=html.escape(CONTACT), cb=_contact_block())


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
