"""coaching_summary.py -- natural-language "Coaching summary" from the structured analytics.

Turns the existing analytics dict (analytics.py `analyze()` output) into a short prose
summary a coach/player can read at a glance: what happened (score/sides), why rounds were
lost, what to review first (round numbers), what utility to learn, and the single biggest fix.

Two tiers, both optional-friendly:
  * build_summary(...)   -- pure, deterministic heuristic. Zero external deps, never throws.
  * enhance_summary(...) -- OPTIONAL: if ANTHROPIC_API_KEY + AI_SUMMARY_ENABLED are set, rewrites
                            the prose with the Anthropic API (stdlib urllib, no new pip dep). On
                            ANY error it returns the heuristic result unchanged. Importing this
                            module NEVER touches the network or requires a key.

The AI path is fed ONLY the structured heuristic facts (loss reasons, weak areas, rounds to
review, util gaps, the biggest fix) -- never raw demo files or replay frames.

Public API:
  build_summary(analytics, *, my_side=None, recurring=None, player_steamid=None) -> dict
  ai_enabled() -> bool
  enhance_summary(analytics, heuristic_result, **kw) -> dict
  coaching_summary(analytics, **kw) -> dict          # build_summary then enhance_summary
"""
import json
import os
import urllib.error
import urllib.request

# --- AI gating (mirrors app.py / steamauth.py _truthy) -----------------------
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-sonnet-4-6"
ANTHROPIC_VERSION = "2023-06-01"
AI_TIMEOUT_S = 10
AI_MAX_TOKENS = 400


def _truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on") if v is not None else False


def ai_enabled():
    """AI enhancement is live only when a key is present AND the opt-in flag is truthy.

    Gated behind BOTH so the key alone (which may exist for other features) never silently
    starts making per-request network calls / spending tokens on summaries.
    """
    return bool(os.environ.get("ANTHROPIC_API_KEY")) and _truthy(os.environ.get("AI_SUMMARY_ENABLED"))


# --- small safe helpers (every read is defensive: analytics may be {} / partial) ---
def _g(d, key, default=None):
    return d.get(key, default) if isinstance(d, dict) else default


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _round_n(v):
    n = _num(v)
    return int(n) if n is not None and float(n).is_integer() else n


def _plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def _join_human(items):
    """['a','b','c'] -> 'a, b and c'. Empty -> ''."""
    items = [str(x) for x in items if x]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _teams(analytics):
    return _g(_g(analytics, "team_coaching", {}) or {}, "teams", []) or []


def _team_for(analytics, my_side=None, player_steamid=None):
    """Pick the team to coach: the one holding player_steamid, else the my_side starter, else
    the team with the most losses (most to learn from), else the first team. None if no teams."""
    teams = _teams(analytics)
    if not teams:
        return None
    if player_steamid is not None:
        sid = str(player_steamid)
        for t in teams:
            for p in (_g(t, "players", []) or []):
                pid = str(p.get("steamid") if isinstance(p, dict) else p)
                if pid == sid:
                    return t
        # players are name-lists in team_coaching; fall back to matching via roles/analytics.players
        name = None
        for pl in (_g(analytics, "players", []) or []):
            if str(pl.get("steamid")) == sid:
                name = pl.get("name")
                break
        if name:
            for t in teams:
                if name in (_g(t, "players", []) or []):
                    return t
    if my_side:
        want = "CT" if str(my_side).lower() in ("ct", "3", "counter-terrorist") else "T"
        for t in teams:
            if str(_g(t, "start_side", "")).upper() == want:
                return t
    return max(teams, key=lambda t: _g(t, "lost", 0) or 0)


def _score_clause(analytics, team):
    """'won 13-7' / 'lost 7-13' / 'went 8-8' from the chosen team, else map-level round count."""
    if team:
        won, lost = _g(team, "won", 0) or 0, _g(team, "lost", 0) or 0
        if won or lost:
            side = _g(team, "start_side")
            side_txt = f" (started {side})" if side in ("CT", "T") else ""
            if won > lost:
                return f"You won {won}-{lost}{side_txt}"
            if lost > won:
                return f"You lost {won}-{lost}{side_txt}"
            return f"You drew {won}-{lost}{side_txt}"
    n = _round_n(_g(analytics, "n_rounds"))
    if n:
        return f"The match went {n} rounds"
    return "Match reviewed"


def _loss_reasons(team, limit=2):
    """[(reason, count, [rounds])] top loss reasons for the team (ignoring pure eco losses
    when there's something more actionable)."""
    lrs = _g(team, "loss_reasons", []) or [] if team else []
    cleaned = []
    for lr in lrs:
        if not isinstance(lr, dict):
            continue
        reason = lr.get("reason")
        if not reason:
            continue
        cleaned.append((reason, lr.get("count", 0) or 0, lr.get("rounds", []) or []))
    cleaned.sort(key=lambda x: -x[1])
    # prefer non-eco reasons if we have any, but keep eco if it's all there is
    non_eco = [c for c in cleaned if c[0] != "Lost on an eco/save"]
    chosen = non_eco or cleaned
    return chosen[:limit]


def _weak_areas(analytics, limit=2):
    """Team-level focus areas from build_team_review.top_areas -> ['KAST','ADR',...]."""
    top = _g(_g(analytics, "team", {}) or {}, "top_areas", []) or []
    out = []
    for a in top:
        if isinstance(a, dict) and a.get("area") and a["area"] != "Mistake":
            out.append(a["area"])
    return out[:limit]


def _player_focus_items(analytics, player_steamid=None):
    """Flatten focus items. If a player is given, just theirs; else pool everyone's (issues only)."""
    players = _g(analytics, "players", []) or []
    items = []
    for p in players:
        if not isinstance(p, dict):
            continue
        if player_steamid is not None and str(p.get("steamid")) != str(player_steamid):
            continue
        for f in (p.get("focus", []) or []):
            if isinstance(f, dict):
                items.append((p.get("name", "?"), f))
    return items


def _review_rounds(analytics, team, limit=4):
    """Round numbers to review first: the team's loss-reason rounds, then the most decisive
    (highest-impact) LOST rounds from round_cards/rounds. De-duped, capped, sorted."""
    rounds = []
    seen = set()

    def push(n):
        n = _round_n(n)
        if isinstance(n, int) and n not in seen:
            seen.add(n)
            rounds.append(n)

    # 1) rounds tied to the team's primary loss reasons (most actionable)
    for _reason, _cnt, rs in _loss_reasons(team, limit=3):
        for r in rs:
            push(r)

    # 2) most decisive lost rounds by swing impact (from the rounds[] list + round_cards winners)
    cards = _g(analytics, "round_cards", []) or []
    winner_by_round = {}
    for c in cards:
        if isinstance(c, dict) and _round_n(c.get("round")) is not None:
            winner_by_round[_round_n(c["round"])] = str(c.get("winner") or "").lower()
    my_side = None
    if team and _g(team, "start_side") in ("CT", "T"):
        my_side = _g(team, "start_side").lower()
    rl = _g(analytics, "rounds", []) or []
    impactful = sorted(
        (r for r in rl if isinstance(r, dict) and _num(r.get("impact")) is not None),
        key=lambda r: -(r.get("impact") or 0))
    for r in impactful:
        rn = _round_n(r.get("num"))
        if rn is None:
            continue
        # prefer rounds the team likely lost (winner != my starting side is a rough proxy; sides
        # swap at half so this is only a hint -- still surfaces decisive rounds either way)
        rounds.append(rn) if rn not in seen else None
        seen.add(rn)
        if len(rounds) >= limit:
            break

    return rounds[:limit]


def _utility_focus(analytics, team, player_focus, recurring):
    """What utility to learn: util-damage gaps + off-book/util mistakes from focus & recurring."""
    out = []
    seen = set()

    def add(msg):
        if msg and msg not in seen:
            seen.add(msg)
            out.append(msg)

    # low team/player util damage (focus "Util dmg/rd")
    for _name, f in player_focus:
        area = f.get("area")
        if area in ("Util dmg/rd", "Utility"):
            detail = f.get("detail") or "Utility damage is low"
            add(detail)
    # team practice-plan items that are utility-shaped
    for t in ([team] if team else []):
        for pp in (_g(t, "practice_plan", []) or []):
            focus = str(_g(pp, "focus", "")).lower()
            if "util" in focus or "smoke" in focus or "flash" in focus or "post-plant" in focus or "retake" in focus:
                add(_g(pp, "drill") or _g(pp, "focus"))
    # recurring utility/flash mistakes across matches
    for rm in _g(recurring or {}, "recurring", []) or []:
        if not isinstance(rm, dict):
            continue
        t = rm.get("type", "")
        label = rm.get("label") or t
        if t in ("low_utility", "team_flashes") or "util" in t or "flash" in t:
            add(f"Recurring: {label} ({_plural(rm.get('matches_present', 0) or 0, 'match')})")
    return out[:3]


def _biggest_fix(team, weak_areas, loss_reasons, player_focus, recurring):
    """The single highest-priority fix. Priority: a recurring cross-match mistake > the team's
    top loss reason's drill > the worst player focus fix > the top weak area. Always a string."""
    # 1) recurring mistake that shows up in the most matches (cross-match = highest signal)
    rec = sorted((r for r in (_g(recurring or {}, "recurring", []) or []) if isinstance(r, dict)),
                 key=lambda r: -(r.get("matches_present", 0) or 0))
    if rec and (rec[0].get("matches_present", 0) or 0) >= 2:
        r = rec[0]
        return f"Drill your recurring weakness -- {r.get('label', r.get('type', 'a repeated mistake'))} " \
               f"(seen in {_plural(r.get('matches_present', 0) or 0, 'match')})."
    # 2) team's #1 loss reason + its drill (from team_coaching.practice_plan)
    lrs = loss_reasons or _loss_reasons(team)
    if lrs:
        reason = lrs[0][0]
        drill = None
        for pp in (_g(team, "practice_plan", []) or []) if team else []:
            if _g(pp, "focus") == reason:
                drill = _g(pp, "drill")
                break
        if drill:
            return f"{reason}: {drill}"
        return f"Fix your most common loss cause: {reason.lower()}."
    # 3) worst player focus item with a concrete fix
    sev = sorted(player_focus, key=lambda nf: -(nf[1].get("severity", 0) or 0))
    for _name, f in sev:
        if f.get("fix"):
            detail = f.get("detail") or f.get("area") or "this area"
            return f"{detail} -- {f['fix']}"
    # 4) the top weak area
    if weak_areas:
        return f"Tighten up {weak_areas[0]} -- it's the most common gap on the team."
    return "Review the flagged rounds together and pick one habit to drill next session."


def build_summary(analytics, *, my_side=None, recurring=None, player_steamid=None):
    """Deterministic, dependency-free coaching summary from the analytics dict.

    Returns:
      {"text": <2-4 sentence prose>, "bullets": [...], "review_rounds": [int,...],
       "utility_focus": [...], "source": "heuristic", "ai": False}

    Robust to missing/empty/partial analytics (have_econ False, no teams, {} input): never raises.
    """
    if not isinstance(analytics, dict):
        analytics = {}

    team = _team_for(analytics, my_side=my_side, player_steamid=player_steamid)
    loss_reasons = _loss_reasons(team)
    weak_areas = _weak_areas(analytics)
    player_focus = _player_focus_items(analytics, player_steamid=player_steamid)
    review_rounds = _review_rounds(analytics, team)
    utility_focus = _utility_focus(analytics, team, player_focus, recurring)
    biggest_fix = _biggest_fix(team, weak_areas, loss_reasons, player_focus, recurring)

    # ---- compose prose ----
    sentences = []
    sentences.append(_score_clause(analytics, team) + ".")

    # why rounds were lost
    if loss_reasons:
        reasons_txt = _join_human([f"{r.lower()} ({c})" for r, c, _rs in loss_reasons])
        sentences.append(f"Most rounds slipped away from {reasons_txt}.")
    elif weak_areas:
        sentences.append(f"The biggest weaknesses on the team were {_join_human(weak_areas)}.")

    # what to review first
    if review_rounds:
        rtxt = ", ".join(f"R{n}" for n in review_rounds)
        sentences.append(f"Review {rtxt} first -- those were the most decisive rounds.")

    # what utility to learn (only if we have something concrete)
    if utility_focus:
        sentences.append(f"On utility: {utility_focus[0]}")
        if not str(sentences[-1]).rstrip().endswith((".", "!", "?")):
            sentences[-1] = sentences[-1].rstrip() + "."

    # the single biggest fix
    sentences.append(f"Biggest fix: {biggest_fix}")
    if not sentences[-1].rstrip().endswith((".", "!", "?")):
        sentences[-1] = sentences[-1].rstrip() + "."

    text = " ".join(s for s in sentences if s and s.strip())

    # ---- bullets (structured, for a UI list) ----
    bullets = []
    for r, c, _rs in loss_reasons:
        bullets.append(f"{r} ({_plural(c, 'round')})")
    for a in weak_areas:
        b = f"Team gap: {a}"
        if b not in bullets:
            bullets.append(b)
    for msg in utility_focus:
        bullets.append(f"Utility: {msg}")
    if review_rounds:
        bullets.append("Review rounds: " + ", ".join(f"R{n}" for n in review_rounds))
    bullets.append(f"Biggest fix: {biggest_fix}")
    # de-dupe while preserving order
    seen, uniq = set(), []
    for b in bullets:
        if b not in seen:
            seen.add(b)
            uniq.append(b)

    return {
        "text": text,
        "bullets": uniq,
        "review_rounds": review_rounds,
        "utility_focus": utility_focus,
        "source": "heuristic",
        "ai": False,
    }


# --- optional AI enhancement (stdlib only; fully optional) -------------------
def _build_ai_prompt(analytics, heuristic_result):
    """Compact text prompt from STRUCTURED facts only (never raw demo data)."""
    hr = heuristic_result or {}
    facts = {
        "score_line": (hr.get("text") or "").split(".")[0],
        "loss_reasons": hr.get("bullets", []),
        "review_rounds": hr.get("review_rounds", []),
        "utility_focus": hr.get("utility_focus", []),
        "n_rounds": _round_n(_g(analytics, "n_rounds")),
        "have_econ": bool(_g(analytics, "have_econ", False)),
    }
    return (
        "You are a concise CS2 coach. Using ONLY these structured facts from a single match, "
        "write a 2-4 sentence coaching summary in plain English. Cover what happened, why rounds "
        "were lost, which rounds to review first (use the round numbers), and the single biggest "
        "fix. Be specific and direct; do not invent stats not present in the facts.\n\n"
        "FACTS (JSON):\n" + json.dumps(facts, default=str)
    )


def enhance_summary(analytics, heuristic_result, **kw):
    """If AI is enabled, rewrite the prose via the Anthropic API (stdlib urllib). On ANY failure
    (disabled, no key, network, timeout, parse), return `heuristic_result` UNCHANGED.

    Never raises. Never sends raw demo files -- only the structured heuristic facts.
    """
    if not isinstance(heuristic_result, dict):
        return heuristic_result
    if not ai_enabled():
        return heuristic_result
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return heuristic_result
    try:
        prompt = _build_ai_prompt(analytics, heuristic_result)
        payload = json.dumps({
            "model": ANTHROPIC_MODEL,
            "max_tokens": AI_MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")
        req = urllib.request.Request(
            ANTHROPIC_URL, data=payload, method="POST",
            headers={
                "content-type": "application/json",
                "x-api-key": key,
                "anthropic-version": ANTHROPIC_VERSION,
            },
        )
        with urllib.request.urlopen(req, timeout=AI_TIMEOUT_S) as resp:
            body = resp.read()
        data = json.loads(body.decode("utf-8"))
        # Anthropic Messages API: {"content": [{"type":"text","text": "..."}], ...}
        parts = data.get("content") or []
        text = "".join(
            p.get("text", "") for p in parts
            if isinstance(p, dict) and p.get("type") == "text"
        ).strip()
        if not text:
            return heuristic_result
        return {**heuristic_result, "text": text, "source": "ai", "ai": True}
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError,
            KeyError, TypeError, OSError):
        return heuristic_result
    except Exception:  # belt-and-suspenders: AI must never break the route
        return heuristic_result


def coaching_summary(analytics, **kw):
    """Convenience: deterministic summary, then optional AI enhancement. Pass-through kwargs:
    my_side, recurring, player_steamid. Always returns a summary dict (heuristic if AI is off)."""
    my_side = kw.pop("my_side", None)
    recurring = kw.pop("recurring", None)
    player_steamid = kw.pop("player_steamid", None)
    base = build_summary(analytics, my_side=my_side, recurring=recurring,
                         player_steamid=player_steamid)
    return enhance_summary(analytics, base, **kw)
