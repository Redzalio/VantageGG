"""coaching_summary.py -- a personal, natural-language "Coaching summary" from the structured analytics.

Turns the existing analytics dict (analytics.py `analyze()` output) into a short, specific prose
summary a coach/player can read at a glance: what happened (score/sides), why rounds were lost,
what to review first (round numbers/callouts), what utility to learn, and the single thing to work
on next. When a player is selected it speaks to THAT player; otherwise it speaks to the team/side.

Two tiers, both optional-friendly:
  * build_summary(...)   -- pure, deterministic composer. Zero external deps, never throws. It
                            collects normalized facts, scores them (selected player/team > global,
                            repeated > one-off, has exact rounds/callouts/numbers > vague,
                            sample-guarded), and fills the highest-scoring ranked phrasings.
  * enhance_summary(...) -- OPTIONAL: if ANTHROPIC_API_KEY + AI_SUMMARY_ENABLED are set, rewrites
                            the prose with the Anthropic API (stdlib urllib, no new pip dep). On
                            ANY error it returns the composed result unchanged. Importing this
                            module NEVER touches the network or requires a key. OFF by default.

The AI path is fed ONLY the structured composed facts (loss reasons, weak areas, rounds to
review, util gaps, the next fix) -- never raw demo files or replay frames.

Public API:
  build_summary(analytics, *, my_side=None, recurring=None, player_steamid=None) -> dict
  ai_enabled() -> bool
  enhance_summary(analytics, composed_result, **kw) -> dict
  coaching_summary(analytics, **kw) -> dict          # build_summary then enhance_summary

build_summary returns (backward-compatible -- the route + report UI read these):
  text          composed paragraph
  bullets       key facts as short lines
  review_rounds int list (rounds tied to the chosen issue)
  utility_focus list of utility lines
  source        "composed"
  ai            False
plus optional, non-breaking extras: headline, top_issue, positive, next_review, next_goal, scope.
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

# A fact computed from fewer than this many observations is "low sample": kept, but never the
# headline, and phrased as "spot-check" rather than "this is a pattern".
MIN_SAMPLE = 3


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
    """'1 match' / '3 matches' / '2 rounds'. Handles -ch/-s/-x/-z words ('match'->'matches')."""
    if n == 1:
        return f"{n} {word}"
    if word.endswith(("ch", "sh", "s", "x", "z")):
        return f"{n} {word}es"
    return f"{n} {word}s"


def _pct(v):
    """A guarded percent like '42%' (drops decimals when whole), or '' if not a number."""
    n = _num(v)
    if n is None:
        return ""
    return (f"{int(n)}%" if float(n).is_integer() else f"{round(n, 1)}%")


def _val(v):
    """A guarded numeric string (drops trailing .0), or '' if not a number."""
    n = _num(v)
    if n is None:
        return ""
    return (str(int(n)) if float(n).is_integer() else str(round(n, 1)))


# ---- person-aware phrasing: speak in 2nd person ("you ...") for an unnamed/own subject, and in
# 3rd person ("<name> ...") for a named player, with the verb conjugated to match. This keeps every
# player line grammatical whether or not a name is in scope ("you were" / "Alice was"). ----
_VERB_3RD = {"were": "was", "have": "has", "do": "does", "don't": "doesn't",
             "weren't": "wasn't", "aren't": "isn't", "are": "is"}


def _subj(player_name):
    """The sentence subject: a capitalized name, or 'You'."""
    return _clean(player_name) or "You"


def _v(player_name, second_person_verb):
    """Conjugate a verb for the subject. 2nd-person form in (e.g. 'were'); returns the 3rd-person
    form ('was') when a name is in scope, else the verb unchanged ('were')."""
    return _VERB_3RD.get(second_person_verb, second_person_verb) if _clean(player_name) \
        else second_person_verb


def _clean(s):
    """A safe display string: never None/empty/the literal 'None'/'nan'. Trim trailing space."""
    if s is None:
        return ""
    s = str(s).strip()
    if not s or s.lower() in ("none", "nan", "null", "undefined"):
        return ""
    return s


def _first_word_lower(s):
    """Lower-case the first character only (so a mid-sentence clause reads naturally)."""
    s = _clean(s)
    return (s[:1].lower() + s[1:]) if s else ""


def _join_human(items):
    """['a','b','c'] -> 'a, b and c'. Empty -> ''."""
    items = [_clean(x) for x in items]
    items = [x for x in items if x]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _round_list(rounds, limit=4):
    """De-duped int round numbers, in first-seen order, capped."""
    out, seen = [], set()
    for r in (rounds or []):
        n = _round_n(r)
        if isinstance(n, int) and n not in seen:
            seen.add(n)
            out.append(n)
            if len(out) >= limit:
                break
    return out


def _rounds_txt(rounds):
    """'R4, R7 and R9' from a list of ints, or '' if none."""
    return _join_human([f"R{n}" for n in _round_list(rounds, limit=4)])


def _ensure_period(s):
    s = (s or "").rstrip()
    if s and not s.endswith((".", "!", "?")):
        s += "."
    return s


def _teams(analytics):
    return _g(_g(analytics, "team_coaching", {}) or {}, "teams", []) or []


def _player_name(analytics, player_steamid):
    """Display name for a steamid from analytics.players, or '' if unknown/unscoped."""
    if player_steamid is None:
        return ""
    sid = str(player_steamid)
    for p in (_g(analytics, "players", []) or []):
        if isinstance(p, dict) and str(p.get("steamid")) == sid:
            return _clean(p.get("name"))
    return ""


def _player_obj(analytics, player_steamid):
    if player_steamid is None:
        return None
    sid = str(player_steamid)
    for p in (_g(analytics, "players", []) or []):
        if isinstance(p, dict) and str(p.get("steamid")) == sid:
            return p
    return None


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
        # players are name-lists in team_coaching; fall back to matching via analytics.players name
        name = _player_name(analytics, sid)
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


# ============================================================================
# FACT MODEL
# ----------------------------------------------------------------------------
# A "fact" is a small dict: {kind, score, text, bullet, rounds, util, ...}. Collectors turn the
# analytics dict into facts; the composer scores them and fills ranked templates. Keeping every
# string-building in collectors means each output line is guarded at the point the value is read.
# ============================================================================

# Loss-reason -> (a few ranked opener-style phrasings of WHY). Variety so different top reasons
# produce visibly different prose. {rounds} is filled only when rounds exist.
_LOSS_PHRASE = {
    "Opening death, no trade":
        "kept losing the opening duel without a trade, handing over free man-advantages",
    "Lost with a man up":
        "threw rounds while up a player -- refragging into a stack instead of playing it slow",
    "Threw a 2+ man advantage":
        "gave back 2+ man advantages -- over-committing instead of closing the round out",
    "Lost the post-plant":
        "couldn't close out the post-plant -- no crossfires on the bomb and util spent early",
    "Failed the retake":
        "kept failing the retake -- hitting the site uncoordinated and untraded",
    "Lost an even full-buy":
        "lost even full-buys on aim alone -- the executes and defaults weren't there",
    "Lost on an eco/save":
        "dropped rounds on ecos/saves -- expected, but worth a stack-or-save plan",
    "Lost the gunfights":
        "simply lost the gunfights -- fights taken without utility or a numbers edge",
}


def _loss_facts(team, side_label):
    """Top loss reasons (non-eco preferred) as ranked facts, each carrying its rounds."""
    lrs = _g(team, "loss_reasons", []) or [] if team else []
    cleaned = []
    for lr in lrs:
        if not isinstance(lr, dict):
            continue
        reason = _clean(lr.get("reason"))
        if not reason:
            continue
        cleaned.append((reason, int(_num(lr.get("count")) or 0), _round_list(lr.get("rounds"), limit=6)))
    cleaned.sort(key=lambda x: -x[1])
    non_eco = [c for c in cleaned if c[0] != "Lost on an eco/save"]
    chosen = non_eco or cleaned
    facts = []
    for i, (reason, count, rounds) in enumerate(chosen[:3]):
        phrase = _LOSS_PHRASE.get(reason, f"kept losing rounds to: {_first_word_lower(reason)}")
        # score: frequency + a bonus for having concrete rounds, minus a touch for low sample
        score = 40 + count * 4 + (12 if rounds else 0) - (8 if count < MIN_SAMPLE else 0) - i
        side_pre = f"On {side_label}, you " if side_label else "You "
        facts.append({
            "kind": "loss_reason",
            "reason": reason,
            "label": _first_word_lower(reason),
            "count": count,
            "rounds": rounds,
            "score": score,
            "evidence": _ensure_period(side_pre + phrase
                                       + (f" (look at {_rounds_txt(rounds)})" if rounds else "")),
            "bullet": f"{reason} ({_plural(count, 'round')})",
            "low_sample": count < MIN_SAMPLE,
        })
    return facts


# Focus-area -> ranked phrasing of a player issue. value/benchmark filled only when numeric.
def _player_issue_facts(analytics, player_obj, player_name):
    """Per-player issues from focus[] (benchmark gaps + worst-impact + flagged mistakes)."""
    if not isinstance(player_obj, dict):
        return []
    who = _subj(player_name)            # "Alice" or "You"
    facts = []
    for f in (player_obj.get("focus", []) or []):
        if not isinstance(f, dict):
            continue
        area = _clean(f.get("area"))
        detail = _clean(f.get("detail"))
        fix = _clean(f.get("fix"))
        sev = int(_num(f.get("severity")) or 1)
        rnd = _round_n(f.get("round"))
        rounds = [rnd] if isinstance(rnd, int) else []
        # base score by severity; concrete round (a Mistake) ranks above a generic gap
        score = 30 + sev * 6 + (10 if rounds else 0)
        # a human label for this issue that NEVER leaks the internal "Mistake" tag
        label = area if area and area != "Mistake" else "a flagged round"
        if area == "Mistake":
            # detail is the flagged-round text (already 2nd-person + names the round); lead with it
            evidence = _ensure_period(_first_word_caps(detail)) if detail else ""
            bullet = detail or "Flagged round to review"
            score += 6
        elif area == "KAST":
            v = _pct(f.get("value"))
            wnt = _v(player_name, "weren't")
            evidence = _ensure_period(f"{who} {wnt} contributing every round"
                                      + (f" -- KAST {v}" if v else "")
                                      + (f", below a ~{_pct(f.get('benchmark'))} target"
                                         if _pct(f.get("benchmark")) else ""))
            bullet = detail or "Low KAST"
        elif area == "ADR":
            v = _val(f.get("value"))
            evidence = _ensure_period(f"{who} did light damage"
                                      + (f" -- {v} ADR" if v else "")
                                      + (f" vs a ~{_val(f.get('benchmark'))} target"
                                         if _val(f.get("benchmark")) else ""))
            bullet = detail or "Low ADR"
        elif area == "Opening win%":
            v = _num(f.get("value"))
            vtxt = _pct(f.get("value"))
            bm = _pct(f.get("benchmark"))
            # don't claim they "lost" duels if the rate is actually winning (>=50%): frame it as
            # "needs to be higher vs target" so we never contradict the number we just printed.
            if v is not None and v < 50:
                evidence = _ensure_period(f"{who} lost the opening duels"
                                          + (f" ({vtxt} win)" if vtxt else ""))
            else:
                evidence = _ensure_period(f"{who} could win more opening duels"
                                          + (f" ({vtxt} win" if vtxt else "")
                                          + (f" vs a ~{bm} target)" if (vtxt and bm) else
                                             (")" if vtxt else "")))
            bullet = detail or "Opening duels to improve"
            label = "opening duels"
        elif area == "Traded death%":
            v = _pct(f.get("value"))
            evidence = _ensure_period(f"{who} died in spots teammates couldn't punish"
                                      + (f" -- only {v} of deaths traded" if v else ""))
            bullet = detail or "Low traded-death %"
            label = "traded deaths"
        elif area in ("Util dmg/rd", "Utility"):
            # the dedicated utility line usually carries this; keep it as a valid lead too
            v = _val(f.get("value"))
            evidence = _ensure_period(f"{who} got little value from utility"
                                      + (f" -- {v} util dmg/round" if v else ""))
            bullet = detail or "Low utility damage"
            label = "utility damage"
            score -= 4
        elif area in ("Opening", "Trading", "Firepower", "Clutch"):
            evidence = _ensure_period(detail) if detail else \
                _ensure_period(f"{who} lost the most impact in {_first_word_lower(area)}")
            bullet = detail or f"{area} is the biggest impact drain"
        else:
            evidence = _ensure_period(detail) if detail else ""
            bullet = detail or area or ""
        if not evidence:
            continue
        fix_line = _ensure_period(_first_word_caps(fix)) if fix else ""
        facts.append({
            "kind": "player_issue",
            "area": area,
            "label": label,
            "score": score,
            "rounds": rounds,
            "evidence": evidence,
            "bullet": bullet,
            "fix": fix_line,
            "low_sample": False,
            "who": who,
        })
    return facts


def _first_word_caps(s):
    s = _clean(s)
    return (s[:1].upper() + s[1:]) if s else ""


def _role_fact(player_obj, player_name):
    """Role-aware coaching from player.role_coaching, when present and 'below' target."""
    if not isinstance(player_obj, dict):
        return None
    rc = player_obj.get("role_coaching")
    if not isinstance(rc, dict):
        return None
    role = _clean(rc.get("role"))
    watch = _clean(rc.get("watch"))
    if not role or not watch:
        return None
    verdict = _clean(rc.get("verdict"))
    lead = (player_name + "'s role is ") if _clean(player_name) else "Your role is "
    score = 22 + (8 if verdict == "below" else 0)
    return {
        "kind": "role",
        "label": f"the {role} role",
        "score": score,
        "rounds": [],
        "evidence": _ensure_period(f"{lead}{role} -- {_first_word_lower(watch)}"),
        "bullet": f"Role ({role}): {watch}",
        "drill": _ensure_period(_first_word_caps(rc.get("drill"))) if _clean(rc.get("drill")) else "",
        "low_sample": False,
    }


def _utility_facts(analytics, team, player_obj, player_name, recurring):
    """What utility to learn: player util dmg, team flashes, team practice-plan util items,
    recurring util/flash mistakes. Each yields a guarded line for the utility_focus list."""
    facts = []
    seen = set()

    def add(text, score, rounds=None, bullet=None):
        text = _clean(text)
        if not text or text in seen:
            return
        seen.add(text)
        facts.append({"kind": "utility", "score": score, "rounds": rounds or [],
                      "util": text, "bullet": bullet or f"Utility: {text}",
                      "evidence": _ensure_period(("On utility, " if not text[0].isupper()
                                                  else "") + text)})

    who = _subj(player_name)            # "Alice" or "You"
    # 1) selected player's low utility damage (concrete number)
    if isinstance(player_obj, dict):
        udr = _num(player_obj.get("udr"))
        if udr is not None and udr < 5:
            add(f"{who} got {_val(udr)} util dmg/round -- learn 2-3 HE/molly lineups for "
                f"common stack spots", 60, bullet="Utility: low util damage")
        tf = _num(player_obj.get("team_flashed"))
        if tf is not None and tf >= 3:
            add(f"{who} blinded teammates {int(tf)}x -- re-aim pop-flashes to pop over their "
                f"peek, not into their face", 58, bullet="Utility: team flashes")
    # 2) team practice-plan items that are utility-shaped. Only in TEAM scope -- when a single
    #    player is selected, their own util data + recurring is the relevant signal (a team drill
    #    isn't about them), so we don't dilute the player's line with a team-wide plan item.
    if player_obj is None:
        for t in ([team] if team else []):
            for pp in (_g(t, "practice_plan", []) or []):
                focus = str(_g(pp, "focus", "")).lower()
                drill = _clean(_g(pp, "drill"))
                if drill and ("util" in focus or "smoke" in focus or "flash" in focus
                              or "post-plant" in focus or "retake" in focus):
                    add(drill, 40, rounds=_round_list(_g(pp, "rounds"), limit=4),
                        bullet=f"Utility: {drill}")
    # 3) recurring utility/flash mistakes across matches (cross-match = strong signal)
    for rm in _g(recurring or {}, "recurring", []) or []:
        if not isinstance(rm, dict):
            continue
        t = _clean(rm.get("type"))
        label = _clean(rm.get("label")) or t
        present = int(_num(rm.get("matches_present")) or 0)
        if not label:
            continue
        if t in ("low_utility", "team_flashes") or "util" in t or "flash" in t:
            add(f"{label} keeps showing up ({_plural(present, 'match')}) -- make it a utility focus",
                50 + present, bullet=f"Utility (recurring): {label}")
    facts.sort(key=lambda f: -f["score"])
    return facts


def _callout_facts(analytics, player_obj, player_name, side_label):
    """Location/callout issues from player.position_stats: a hot death zone or a callout where the
    player keeps losing the opening duel. Only emitted when a player is in scope (named data)."""
    if not isinstance(player_obj, dict):
        return []
    rows = player_obj.get("position_stats") or []
    if not isinstance(rows, list):
        return []
    who = _subj(player_name)            # "Alice" or "You"
    facts = []
    # worst death zone (most deaths, with a losing or even K/D there)
    death_rows = [r for r in rows if isinstance(r, dict) and _num(r.get("d"))]
    death_rows.sort(key=lambda r: -(_num(r.get("d")) or 0))
    for r in death_rows[:1]:
        zone = _clean(r.get("zone"))
        d = int(_num(r.get("d")) or 0)
        k = int(_num(r.get("k")) or 0)
        if not zone or d < 2:
            continue
        score = 28 + d * 3 + (10 if k <= d else 0) - (8 if d < MIN_SAMPLE else 0)
        facts.append({
            "kind": "callout",
            "callout": zone,
            "label": zone,
            "score": score,
            "rounds": [],
            "evidence": _ensure_period(f"{who} kept dying at {zone} ({_plural(d, 'death')}"
                                       + (f", {k}-{d} there" if (k or d) else "")
                                       + ") -- they're pre-aiming that spot; vary timing and peek"),
            "bullet": f"Death cluster: {zone} ({_plural(d, 'death')})",
            "low_sample": d < MIN_SAMPLE,
        })
    # callout where opening duels go badly
    for r in rows:
        if not isinstance(r, dict):
            continue
        zone = _clean(r.get("zone"))
        ok = int(_num(r.get("open_k")) or 0)
        od = int(_num(r.get("open_d")) or 0)
        tot = ok + od
        if zone and tot >= 2 and od > ok:
            score = 26 + od * 3 - (8 if tot < MIN_SAMPLE else 0)
            facts.append({
                "kind": "callout",
                "callout": zone,
                "label": zone,
                "score": score,
                "rounds": [],
                "evidence": _ensure_period(f"{who} lost the opening duel at {zone} {ok}-{od} "
                                           f"-- take that first peek with a flash or a teammate"),
                "bullet": f"Opening duels at {zone} ({ok}-{od})",
                "low_sample": tot < MIN_SAMPLE,
            })
            break
    return facts


def _economy_facts(analytics, team):
    """Economy issue: a full-buy that underperforms, or anti-eco rounds dropped. Guarded on econ."""
    if not isinstance(team, dict):
        return []
    econ = team.get("economy") or {}
    facts = []
    full = econ.get("full") if isinstance(econ, dict) else None
    if isinstance(full, dict):
        n = int(_num(full.get("rounds")) or 0)
        wp = _num(full.get("win_pct"))
        if n >= 6 and wp is not None and wp < 45:
            facts.append({
                "kind": "economy",
                "label": "full-buy conversion",
                "score": 30,
                "rounds": [],
                "evidence": _ensure_period(f"Your full-buys underperformed ({_pct(wp)} of "
                                           f"{n}) -- win them on set executes and defaults, not aim"),
                "bullet": f"Full-buy conversion: {_pct(wp)} of {n}",
                "low_sample": False,
            })
    return facts


def _recurring_goal_fact(recurring, player_name):
    """The strongest cross-match recurring mistake, as the 'what to work on next' goal fact.

    Carries a suggested metric->target / trend when present. Highest signal when it shows up in
    multiple matches. Returns a single fact or None."""
    rec = [r for r in (_g(recurring or {}, "recurring", []) or []) if isinstance(r, dict)]
    if not rec:
        return None
    rec.sort(key=lambda r: (-(int(_num(r.get("matches_present")) or 0)),
                            -(int(_num(r.get("total")) or 0))))
    r = rec[0]
    label = _clean(r.get("label")) or _clean(r.get("type"))
    if not label:
        return None
    present = int(_num(r.get("matches_present")) or 0)
    total_m = int(_num(r.get("matches_total")) or 0)
    trend = _clean(r.get("trend"))
    target = _num(r.get("suggested_target"))
    who = (player_name + "'s ") if player_name else "your "
    pieces = [f"Next, drill {who}recurring weakness -- {_first_word_lower(label)}"]
    if present and total_m:
        pieces.append(f" (seen in {present} of {total_m} matches")
        if trend == "improving":
            pieces.append(", and it's trending down")
        elif trend == "worsening":
            pieces.append(", and it's getting worse")
        pieces.append(")")
    elif present:
        pieces.append(f" (seen in {_plural(present, 'match')})")
    if target is not None:
        pieces.append(f"; aim for {_val(target)} or fewer per match")
    goal_line = _ensure_period("".join(pieces))
    # score high: cross-match recurring is the most actionable next step
    score = 70 + present * 5
    return {
        "kind": "goal",
        "score": score,
        "rounds": [],   # a recurring mistake's series are per-match counts, not round numbers
        "goal_line": goal_line,
        "bullet": f"Work on next: {label}" + (f" (recurs in {present} matches)" if present else ""),
        "label": label,
        "low_sample": present < 2,
    }


def _positive_fact(analytics, player_obj, player_steamid, team):
    """One genuine positive (good-polarity insight or a strong stat) to keep the read balanced.

    Scoped: when a player is selected, only THEIR positives; else the selected team's players."""
    insights = _g(analytics, "insights", {}) or {}
    # which steamids are in scope
    if player_steamid is not None:
        sids = [str(player_steamid)]
    elif team:
        names = set(_g(team, "players", []) or [])
        sids = [str(p.get("steamid")) for p in (_g(analytics, "players", []) or [])
                if isinstance(p, dict) and _clean(p.get("name")) in names]
    else:
        sids = [str(p.get("steamid")) for p in (_g(analytics, "players", []) or [])
                if isinstance(p, dict)]
    name_of = {str(p.get("steamid")): _clean(p.get("name"))
               for p in (_g(analytics, "players", []) or []) if isinstance(p, dict)}
    best = None
    for sid in sids:
        for ins in (insights.get(sid) or []):
            if not isinstance(ins, dict) or ins.get("polarity") != "good":
                continue
            txt = _clean(ins.get("text"))
            if not txt:
                continue
            # prefer the named player's own positive; phrase in 2nd person if it's the selected one
            if player_steamid is not None:
                line = _ensure_period(_lead_you(txt))
            else:
                nm = name_of.get(sid)
                line = _ensure_period((nm + ": " + txt) if nm else txt)
            best = {"kind": "positive", "score": 10, "rounds": [],
                    "positive": line, "bullet": "Bright spot: " + (txt[:80])}
            break
        if best:
            break
    return best


def _lead_you(txt):
    """Light touch: many insight texts already use 'you'. Leave as-is, just ensure it reads."""
    return _clean(txt)


# ---- score-line (kept from the prior build, hardened) -----------------------
def _score_fact(analytics, team, side_label, player_name):
    """The opener fact: 'You won 13-7 (CT start)' / team-named / map-level fallback. Never None."""
    if team:
        won = int(_num(_g(team, "won")) or 0)
        lost = int(_num(_g(team, "lost")) or 0)
        if won or lost:
            side = _g(team, "start_side")
            side_txt = f" (started {side})" if side in ("CT", "T") else ""
            if won > lost:
                lead = f"You won {won}-{lost}{side_txt}"
                verb = "won"
            elif lost > won:
                lead = f"You lost {won}-{lost}{side_txt}"
                verb = "lost"
            else:
                lead = f"You drew {won}-{lost}{side_txt}"
                verb = "drew"
            return {"kind": "score", "lead": _ensure_period(lead), "result": verb,
                    "won": won, "lost": lost}
    n = _round_n(_g(analytics, "n_rounds"))
    if n:
        return {"kind": "score", "lead": _ensure_period(f"This match went {n} rounds"),
                "result": None, "won": None, "lost": None}
    return {"kind": "score", "lead": "Match reviewed.", "result": None, "won": None, "lost": None}


# ---- review-playlist fact ---------------------------------------------------
def _review_fact(analytics, team, issue_facts, player_obj):
    """What to review next: the chosen issue's rounds, else the team's decisive lost rounds, else
    a 'what to review instead' when there are no concrete rounds (sparse data)."""
    rounds = []
    label = None
    # 1) the top issue with concrete rounds (label is a human string, never the internal tag)
    for f in issue_facts:
        if f.get("rounds"):
            rounds = _round_list(f["rounds"], limit=4)
            label = _clean(f.get("label"))
            break
    # 2) else the most decisive rounds from the analytics rounds[] (impact-sorted)
    if not rounds:
        rl = _g(analytics, "rounds", []) or []
        impactful = sorted(
            (r for r in rl if isinstance(r, dict) and _num(r.get("impact")) is not None),
            key=lambda r: -(_num(r.get("impact")) or 0))
        rounds = _round_list((r.get("num") for r in impactful), limit=4)
    if rounds:
        lead = f"Review {_rounds_txt(rounds)} first"
        if label and label != "a flagged round":
            lead += f" -- that's where {_first_word_lower(label)} cost you"
        elif label == "a flagged round":
            lead += " -- start with that flagged round"
        else:
            lead += " -- those were the most decisive rounds"
        return {"rounds": rounds, "line": _ensure_period(lead),
                "bullet": "Review rounds: " + ", ".join(f"R{n}" for n in rounds)}
    # 3) nothing concrete: say what to review instead (don't invent a pattern)
    has_player_rounds = isinstance(player_obj, dict) and any(
        isinstance(f, dict) and f.get("round") for f in (player_obj.get("focus", []) or []))
    if has_player_rounds:
        return {"rounds": [], "line": "Review your flagged rounds in the replay to see the pattern.",
                "bullet": "Review: your flagged rounds"}
    return {"rounds": [], "line": "Re-watch the match and tag the rounds you'd play differently.",
            "bullet": "Review: tag rounds to revisit"}


# ============================================================================
# COMPOSER
# ============================================================================
def build_summary(analytics, *, my_side=None, recurring=None, player_steamid=None):
    """Deterministic, dependency-free coaching summary composed from ranked facts.

    Returns a dict with backward-compatible keys (text, bullets, review_rounds, utility_focus,
    source, ai) plus optional extras (headline, top_issue, positive, next_review, next_goal,
    scope). Robust to missing/empty/partial analytics ({} input, no teams): never raises.
    """
    if not isinstance(analytics, dict):
        analytics = {}

    team = _team_for(analytics, my_side=my_side, player_steamid=player_steamid)
    player_obj = _player_obj(analytics, player_steamid)
    player_name = _player_name(analytics, player_steamid)
    # If a player was requested but isn't in this match's data, drop the player scope (don't
    # name someone who isn't here, and don't silently mislabel a team line as a player line).
    if player_steamid is not None and player_obj is None:
        player_steamid = None
        player_name = ""

    # side label for the chosen team (only when we actually have a team with a known start side)
    start_side = _clean(_g(team, "start_side")) if team else ""
    side_label = start_side if start_side in ("CT", "T") else ""

    # ---- collect facts ----
    score = _score_fact(analytics, team, side_label, player_name)

    issue_facts = []          # the "why it went wrong" pool (loss reasons + player issues + ...)
    if player_steamid is not None:
        # PLAYER SCOPE: lead with THIS player's own issues; never reference other players.
        issue_facts += _player_issue_facts(analytics, player_obj, player_name)
        issue_facts += _callout_facts(analytics, player_obj, player_name, side_label)
        rf = _role_fact(player_obj, player_name)
        if rf:
            issue_facts.append(rf)
    else:
        # TEAM / SIDE SCOPE: describe the team/side, not any one player.
        issue_facts += _loss_facts(team, side_label)
        issue_facts += _economy_facts(analytics, team)

    issue_facts.sort(key=lambda f: -f.get("score", 0))

    utility_facts = _utility_facts(analytics, team, player_obj, player_name, recurring)
    review = _review_fact(analytics, team, issue_facts, player_obj)
    goal_fact = _recurring_goal_fact(recurring, player_name)
    positive = _positive_fact(analytics, player_obj, player_steamid, team)

    top_issue = issue_facts[0] if issue_facts else None

    # ---- compose prose: opener + 1-2 evidence + review-next + work-on-next ----
    sentences = [score["lead"]]

    # The dedicated utility line carries any utility issue, so drop util-labelled issues from the
    # evidence pool when we have one (otherwise the same point is made twice in a row).
    have_util_line = any(uf.get("evidence") for uf in utility_facts)
    evidence_pool = [f for f in issue_facts
                     if not (have_util_line and _clean(f.get("label")) == "utility damage")]

    used_evidence = 0
    for f in evidence_pool:
        if used_evidence >= 2:
            break
        ev = f.get("evidence")
        if ev and ev not in sentences:
            sentences.append(ev)
            used_evidence += 1
    # if no issue evidence at all, fall back to a utility or positive observation, else a steer
    if used_evidence == 0:
        if utility_facts and utility_facts[0].get("evidence"):
            sentences.append(utility_facts[0]["evidence"])
        elif positive and positive.get("positive"):
            sentences.append(positive["positive"])

    # a dedicated utility line if we have one and it isn't already the lead evidence
    util_line_added = False
    for uf in utility_facts:
        ev = uf.get("evidence")
        if ev and ev not in sentences:
            sentences.append(ev)
            util_line_added = True
            break

    # what to review next
    if review.get("line"):
        sentences.append(review["line"])

    # what to work on next (goal): recurring cross-match mistake (strongest, has a target) > the
    # top issue's own fix > ANY ranked issue's fix/drill > the top loss reason. Always concrete.
    next_goal_line = ""
    if goal_fact and goal_fact.get("goal_line"):
        next_goal_line = goal_fact["goal_line"]
    else:
        fix_src = None
        for f in issue_facts:                      # highest-scored issue with an actionable fix
            if _clean(f.get("fix")):
                fix_src = _clean(f["fix"])
                break
            if _clean(f.get("drill")):
                fix_src = _clean(f["drill"])
                break
        if fix_src:
            next_goal_line = _ensure_period("Work on next -- " + _first_word_lower(fix_src))
        elif top_issue and _clean(top_issue.get("reason")):
            next_goal_line = _ensure_period("Work on next -- fix your most common loss cause, "
                                            + _first_word_lower(top_issue["reason"]))
        elif top_issue and _clean(top_issue.get("label")) \
                and _clean(top_issue.get("label")) != "a flagged round":
            next_goal_line = _ensure_period("Work on next -- tighten up "
                                            + _first_word_lower(top_issue["label"]))
    if next_goal_line:
        sentences.append(next_goal_line)

    # If we somehow only have the opener (very sparse), add a concrete steer (never a placeholder).
    if len([s for s in sentences if s and s.strip()]) <= 1:
        sentences.append("Not much stood out in the data -- re-watch the match and tag the rounds "
                         "you'd play differently, then pick one habit to drill.")

    text = " ".join(_clean(s) for s in sentences if _clean(s))

    # ---- bullets (structured, for a UI list) ----
    bullets = []
    for f in issue_facts:
        b = _clean(f.get("bullet"))
        if b:
            bullets.append(b)
    for uf in utility_facts[:2]:
        b = _clean(uf.get("bullet"))
        if b:
            bullets.append(b)
    if review.get("bullet"):
        bullets.append(review["bullet"])
    if goal_fact and goal_fact.get("bullet"):
        bullets.append(goal_fact["bullet"])
    if positive and positive.get("bullet"):
        bullets.append(positive["bullet"])
    # de-dupe while preserving order
    seen, uniq = set(), []
    for b in bullets:
        if b not in seen:
            seen.add(b)
            uniq.append(b)

    # review_rounds: prefer the chosen issue's rounds, fall back to the review fact's rounds
    review_rounds = []
    if top_issue and top_issue.get("rounds"):
        review_rounds = _round_list(top_issue["rounds"], limit=4)
    if not review_rounds:
        review_rounds = _round_list(review.get("rounds"), limit=4)

    utility_focus = [uf["util"] for uf in utility_facts if _clean(uf.get("util"))][:3]

    headline = _compose_headline(score, top_issue, player_name, side_label)

    scope = {
        "team": _clean(_g(team, "name")) if team else "",
        "player": player_name,
        "side": side_label,
        "callout": _clean(top_issue.get("callout")) if (top_issue and top_issue.get("callout")) else "",
    }

    return {
        # --- backward-compatible fields (route + report UI depend on these) ---
        "text": text,
        "bullets": uniq,
        "review_rounds": review_rounds,
        "utility_focus": utility_focus,
        "source": "composed",
        "ai": False,
        # --- optional, non-breaking extras ---
        "headline": headline,
        "top_issue": _clean(top_issue.get("bullet")) if top_issue else "",
        "positive": _clean(positive.get("positive")) if positive else "",
        "next_review": _clean(review.get("line")),
        "next_goal": _clean(next_goal_line),
        "scope": scope,
    }


def _compose_headline(score, top_issue, player_name, side_label):
    """A short headline. Never contains a placeholder or internal tag; always non-empty."""
    who = _clean(player_name) or (f"{side_label} side" if side_label else "Your team")
    res = score.get("result")
    if top_issue:
        focus = _clean(top_issue.get("label"))
        if focus and focus not in ("a flagged round", "Mistake"):
            return _clean(f"{who}: fix {_first_word_lower(focus)}")
    if res == "won":
        return _clean(f"{who} won -- tighten the gaps")
    if res == "lost":
        return _clean(f"{who} lost -- key fixes inside")
    if res == "drew":
        return _clean(f"{who} drew -- margins to win")
    return _clean(f"{who}: what to review next")


# --- optional AI enhancement (stdlib only; fully optional) -------------------
def _build_ai_prompt(analytics, composed_result):
    """Compact text prompt from STRUCTURED facts only (never raw demo data)."""
    hr = composed_result or {}
    facts = {
        "headline": hr.get("headline"),
        "score_line": (hr.get("text") or "").split(".")[0],
        "scope": hr.get("scope"),
        "key_facts": hr.get("bullets", []),
        "review_rounds": hr.get("review_rounds", []),
        "utility_focus": hr.get("utility_focus", []),
        "next_goal": hr.get("next_goal"),
        "n_rounds": _round_n(_g(analytics, "n_rounds")),
        "have_econ": bool(_g(analytics, "have_econ", False)),
    }
    return (
        "You are a concise CS2 coach. Using ONLY these structured facts from a single match, "
        "write a 3-5 sentence coaching summary in plain English. Cover what happened, why rounds "
        "were lost, which rounds to review first (use the round numbers), what utility to learn, "
        "and the single thing to work on next. Stay within the given scope (if a player is named, "
        "speak to that player and don't mention anyone else). Be specific and direct; do not "
        "invent stats not present in the facts.\n\n"
        "FACTS (JSON):\n" + json.dumps(facts, default=str)
    )


def enhance_summary(analytics, composed_result, **kw):
    """If AI is enabled, rewrite the prose via the Anthropic API (stdlib urllib). On ANY failure
    (disabled, no key, network, timeout, parse), return `composed_result` UNCHANGED.

    Never raises. Never sends raw demo files -- only the structured composed facts.
    """
    if not isinstance(composed_result, dict):
        return composed_result
    if not ai_enabled():
        return composed_result
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return composed_result
    try:
        prompt = _build_ai_prompt(analytics, composed_result)
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
            return composed_result
        return {**composed_result, "text": text, "source": "ai", "ai": True}
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError,
            KeyError, TypeError, OSError):
        return composed_result
    except Exception:  # belt-and-suspenders: AI must never break the route
        return composed_result


def coaching_summary(analytics, **kw):
    """Convenience: deterministic composed summary, then optional AI enhancement. Pass-through
    kwargs: my_side, recurring, player_steamid. Always returns a summary dict (composed if AI off)."""
    my_side = kw.pop("my_side", None)
    recurring = kw.pop("recurring", None)
    player_steamid = kw.pop("player_steamid", None)
    base = build_summary(analytics, my_side=my_side, recurring=recurring,
                         player_steamid=player_steamid)
    return enhance_summary(analytics, base, **kw)
