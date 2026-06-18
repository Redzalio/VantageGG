"""Steam OpenID 2.0 login -- stdlib only (urllib), no external OpenID/requests dependency.

Steam supports OpenID 2.0. The flow:
  1. /login/steam        -> redirect the browser to steamcommunity.com/openid/login (checkid_setup)
  2. Steam authenticates the user, redirects back to our return_to with signed openid.* params
  3. We re-POST those params with mode=check_authentication; Steam replies "is_valid:true"
  4. The verified identity URL ends in the user's 64-bit SteamID -> that's the login.

IMPORTANT: OpenID login itself needs NO Steam API key -- only a public callback URL the user's
browser can reach (PUBLIC_BASE_URL, or inferred from the request for local use). STEAM_API_KEY is
OPTIONAL and used only to fetch the player's display name + avatar (GetPlayerSummaries). Login works
fine without it (the user just shows up by SteamID until a key is configured).

This module is pure functions + env reads -- no Flask import -- so it's unit-testable in isolation.
"""
import json
import os
import re
import urllib.parse
import urllib.request

OPENID_NS = "http://specs.openid.net/auth/2.0"
OPENID_IDENTIFIER_SELECT = "http://specs.openid.net/auth/2.0/identifier_select"
STEAM_OPENID_URL = "https://steamcommunity.com/openid/login"
CALLBACK_PATH = "/auth/steam/callback"
# a SteamID64 is exactly 17 digits; the claimed_id is .../openid/id/<steamid64>
_STEAM_ID_RE = re.compile(r"^https?://steamcommunity\.com/openid/id/(\d{17})$")
_HTTP_TIMEOUT = 12


def _truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on") if v is not None else False


def public_base_url(request_root=None):
    """Externally-reachable base URL, no trailing slash. Prefer PUBLIC_BASE_URL (correct behind a
    reverse proxy / HTTPS); fall back to the request's own root for local/dev use."""
    base = os.environ.get("PUBLIC_BASE_URL") or (request_root or "")
    return base.rstrip("/")


def auth_enabled():
    """Whether the operator has opted into Steam auth (drives login UI + demo-ownership stamping).
    A pure-local install leaves PUBLIC_BASE_URL and AUTH_REQUIRED unset and runs exactly as before
    (a synthetic 'local' user owns nothing, everything stays visible)."""
    return bool(os.environ.get("PUBLIC_BASE_URL")) or _truthy(os.environ.get("AUTH_REQUIRED"))


def auth_required():
    """Whether anonymous users must be blocked from data (enforced in Stage 5)."""
    return _truthy(os.environ.get("AUTH_REQUIRED"))


def login_url(base_url):
    """Build the Steam OpenID redirect URL. `base_url` = our externally reachable root."""
    base = (base_url or "").rstrip("/")
    params = {
        "openid.ns": OPENID_NS,
        "openid.mode": "checkid_setup",
        "openid.return_to": base + CALLBACK_PATH,
        "openid.realm": base + "/",
        "openid.identity": OPENID_IDENTIFIER_SELECT,
        "openid.claimed_id": OPENID_IDENTIFIER_SELECT,
    }
    return STEAM_OPENID_URL + "?" + urllib.parse.urlencode(params)


def _claimed_steamid(claimed_id):
    m = _STEAM_ID_RE.match((claimed_id or "").strip())
    return m.group(1) if m else None


def verify(params, expected_return_prefix=None, _opener=None):
    """Verify an OpenID callback. `params` = the openid.* args Steam sent back (dict or Flask MultiDict;
    read via .get/.keys). Returns the verified 64-bit SteamID string, or None if invalid.

    Security: requires mode=id_res, a well-formed Steam claimed_id, an optional return_to prefix match
    (so an assertion minted for another site can't be replayed here), and a positive
    check_authentication round-trip with Steam -- so a forged callback can't pass. `_opener` is a test
    seam (callable(url, data) -> response bytes); production uses urllib."""
    if params.get("openid.mode") != "id_res":
        return None                                       # user cancelled, or malformed
    steamid = _claimed_steamid(params.get("openid.claimed_id"))
    if not steamid:
        return None
    if expected_return_prefix:
        rt = params.get("openid.return_to") or ""
        if not rt.startswith(expected_return_prefix):
            return None                                   # assertion was minted for a different realm
    # echo every signed field back to Steam, flipping mode to check_authentication
    check = {k: params.get(k) for k in params.keys() if str(k).startswith("openid.")}
    check["openid.mode"] = "check_authentication"
    data = urllib.parse.urlencode(check).encode()
    try:
        body = _opener(STEAM_OPENID_URL, data) if _opener else _post(STEAM_OPENID_URL, data)
    except Exception:
        return None
    return steamid if re.search(r"is_valid\s*:\s*true", body or "") else None


def _post(url, data):
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
        return resp.read().decode("utf-8", "replace")


def fetch_profile(steamid64):
    """Optional: {name, avatar} via the Steam Web API. Needs STEAM_API_KEY; returns {} with no key or
    on any error -- profile info is cosmetic and must never block or break login."""
    key = os.environ.get("STEAM_API_KEY")
    if not key or not steamid64:
        return {}
    url = ("https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/?key="
           + urllib.parse.quote(key) + "&steamids=" + urllib.parse.quote(str(steamid64)))
    try:
        with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as resp:
            obj = json.loads(resp.read().decode("utf-8", "replace"))
        players = (obj.get("response") or {}).get("players") or []
        if players:
            p = players[0]
            return {"name": p.get("personaname"),
                    "avatar": p.get("avatarfull") or p.get("avatarmedium") or p.get("avatar")}
    except Exception:
        pass
    return {}
