"""reviews.py -- per-demo REVIEW bookmarks + auto-seeded review queues.

The first piece of the "team learning loop": after a demo is analysed, a team can
(a) save BOOKMARKS (a round/time/player + note + tag) to build a review list, and
(b) jump through AUTO-QUEUES that are computed from the analytics already in the demo
JSON (untraded opening deaths, dry opens, bad spacing, good rounds, team loss reasons,
round-by-round, ...). No new parsing -- auto-queues are pure functions over the cached
insight engine output, so every analytical finding becomes a clickable review moment.

Storage mirrors nades.py: stdlib-only, atomic JSON write, one file keyed by demo id
(`source_sha1`). Bookmarks are user content; queues are derived and never stored.
"""
import hashlib
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
REVIEWS_DIR = os.environ.get("REVIEWS_DIR") or os.path.join(HERE, "reviews")
REVIEWS_PATH = os.path.join(REVIEWS_DIR, "reviews.json")   # { "<demo_id>": {"bookmarks": [...]} }


# ---- storage ----------------------------------------------------------------
def _ensure():
    os.makedirs(REVIEWS_DIR, exist_ok=True)


def load_all():
    try:
        with open(REVIEWS_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_all(data):
    _ensure()
    tmp = REVIEWS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, REVIEWS_PATH)


def _safe_id(demo_id):
    return bool(demo_id) and bool(re.fullmatch(r"[A-Za-z0-9_-]+", str(demo_id)))


def bookmarks(demo_id):
    if not _safe_id(demo_id):
        return []
    return (load_all().get(demo_id) or {}).get("bookmarks", [])


def _bid(b):
    base = (f"{b.get('t')}|{b.get('round')}|{b.get('player')}|{b.get('label')}|{b.get('note')}"
            f"|{b.get('entity')}|{b.get('ref')}")
    return "b_" + hashlib.sha1(base.encode("utf-8")).hexdigest()[:10]


def add_bookmark(demo_id, raw):
    """Add (or replace by id) a bookmark for a demo. Returns the stored bookmark."""
    if not _safe_id(demo_id):
        raise ValueError("bad demo id")
    g = raw.get
    bm = {
        "t": round(float(g("t") or 0), 2),
        "round": int(g("round")) if g("round") is not None else None,
        "player": int(g("player")) if g("player") is not None else -1,   # roster idx or -1
        "label": (g("label") or "").strip()[:80],
        "note": (g("note") or "").strip()[:500],
        "tag": (g("tag") or "").strip()[:24],
        # #41: what this note is attached to (round|tick|player|location|util|"") + a free ref
        # (callout name, util id, ...) so notes can target more than a moment in time.
        "entity": (g("entity") or "").strip()[:16],
        "ref": (g("ref") or "").strip()[:64],
    }
    bm["id"] = g("id") or _bid(bm)
    data = load_all()
    entry = data.setdefault(demo_id, {})
    bms = entry.setdefault("bookmarks", [])
    for i, ex in enumerate(bms):
        if ex.get("id") == bm["id"]:
            bms[i] = bm
            break
    else:
        bms.append(bm)
    bms.sort(key=lambda b: b.get("t") or 0)
    save_all(data)
    return bm


def delete_bookmark(demo_id, bm_id):
    if not _safe_id(demo_id):
        return 0
    data = load_all()
    entry = data.get(demo_id) or {}
    bms = entry.get("bookmarks") or []
    n = len(bms)
    entry["bookmarks"] = [b for b in bms if b.get("id") != bm_id]
    data[demo_id] = entry
    save_all(data)
    return n - len(entry["bookmarks"])


# ---- auto-seeded review queues (derived from analytics, never stored) --------
# Friendly names for the insight `type` strings the analytics engine emits. Unknown
# types fall back to a humanised version of the type, so new detectors show up for free.
_QUEUE_LABELS = {
    "pos": "Positioning & mid-round deaths",
    "untraded_opening_death": "Untraded opening deaths",
    "dry_opening": "Dry opening peeks (no flash support)",
    "weak_opening": "Weak opening duels",
    "predictable": "Predictable deaths (same spot/angle)",
    "predictable_death": "Predictable deaths (same spot/angle)",
    "clumping": "Bad spacing (teammates clumped)",
    "isolated_death": "Isolated deaths (too far from team)",
    "midround_kd_leak": "Mid-round K/D leaks",
    "mid_round_death": "Mid-round deaths",
    "bad_save": "Bad saves (kept a gun in a lost round)",
    "eco_discipline": "Eco discipline (over-bought on eco)",
    "moving_shots": "Shooting while moving",
    "low_trade": "Low trade participation",
    "team_flash": "Team flashes",
    "low_util": "Under-used utility",
    "good_openings": "Good opening duels",
    "good_opening": "Good opening duels",
    "good_spacing": "Good spacing",
    "good_utility": "Good utility",
    "good_trade": "Good trades",
    "multikills": "Multikills",
    "multikill": "Multikills",
    "high_impact": "High-impact rounds",
    "clutch": "Clutches",
    "clutch_won": "Clutches won",
}

# Team loss-reason strings (from analytics._team_loss_reason) that we surface as dedicated,
# nicely-named cross-team playlists instead of the generic per-team "{team}: {reason}" queue.
# Mapping: reason string -> (queue key, queue label). Reasons NOT listed here still flow through
# the generic per-team loop unchanged, so no loss reason is ever dropped.
_PROMOTED_LOSS_REASONS = {
    "Lost an even full-buy": ("lost_full_buys", "Lost full-buy rounds"),
    "Lost the post-plant": ("post_plant_losses", "Post-plant losses"),
    "Failed the retake": ("failed_retakes", "Failed retakes"),
}


def _humanize(t):
    return _QUEUE_LABELS.get(t) or str(t).replace("_", " ").capitalize()


def auto_queues(data):
    """Build review queues from a parsed+analysed demo dict. Returns a list of:
        {key, label, polarity('issue'|'good'|'neutral'), items:[{round,t,player,text}]}
    Empty queues are dropped. `t` is seconds (jump target); `player` is a roster idx or -1.
    """
    if not isinstance(data, dict):
        return []
    a = data.get("analytics") or {}
    tickrate = a.get("tickrate") or data.get("tickrate") or 64
    players = data.get("players") or []
    sid2idx = {}
    for i, p in enumerate(players):
        sid = str(p.get("steamid") or p.get("steam_id") or "")
        if sid:
            sid2idx[sid] = i

    # round_cards indexed by round -> a fallback jump time for round-level insights
    rc = {r.get("round"): r for r in (a.get("round_cards") or []) if r.get("round") is not None}

    def t_of(ins):
        """Jump time (s) for an insight, or None if it isn't a specific moment. Prefer the event
        tick (land a beat before it); else the round's watch_t; else not jumpable (an aggregate)."""
        if ins.get("tick") is not None:
            return max(0.0, round(ins["tick"] / tickrate, 2) - 1.5)
        rn = ins.get("round")
        if rn is not None and rn in rc:
            return float(rc[rn].get("watch_t") or 0)
        return None

    # 1) per-insight-type queues (issues + positives), from the per-steamid insight feed.
    #    Aggregate/summary insights (no tick AND no round) aren't a moment -> skipped here;
    #    they still live in the analytics panel.
    by_type = {}    # type -> {polarity, items}
    insights = a.get("insights") or {}
    if isinstance(insights, dict):
        for sid, lst in insights.items():
            idx = sid2idx.get(str(sid), -1)
            for ins in (lst or []):
                tt = t_of(ins)
                if tt is None:
                    continue
                ty = ins.get("type") or "insight"
                bucket = by_type.setdefault(ty, {"polarity": ins.get("polarity") or "issue", "items": []})
                bucket["items"].append({
                    "round": ins.get("round"),
                    "t": tt,
                    "player": idx,
                    "text": ins.get("text") or _humanize(ty),
                    "severity": ins.get("severity", 1),
                })

    queues = []
    for ty, b in by_type.items():
        items = sorted(b["items"], key=lambda x: (x.get("round") or 0, x.get("t") or 0))
        queues.append({"key": "ins_" + ty, "label": _humanize(ty),
                       "polarity": b["polarity"], "items": items})

    def _round_item(rn, player=-1, text=None):
        """A queue item anchored to a round, using the round card's watch time + summary."""
        card = rc.get(rn) or {}
        return {"round": rn, "t": float(card.get("watch_t") or 0), "player": player,
                "text": text or card.get("summary") or f"Round {rn}"}

    # 2) team loss-reason queues (thrown advantages / lost ecos / ...), from team_coaching.
    #    A few high-value reasons are PROMOTED to their own cross-team named playlists below
    #    (Lost full-buy / Post-plant losses / Failed retakes); everything else stays here.
    promoted = {}   # promoted queue key -> {label, items} aggregated across both teams
    for team in (a.get("team_coaching") or {}).get("teams") or []:
        if not isinstance(team, dict):
            continue
        tname = team.get("name") or team.get("id") or "Team"
        for lr in (team.get("loss_reasons") or []):
            if not isinstance(lr, dict):
                continue
            rounds = lr.get("rounds") or []
            if not rounds:
                continue
            reason = lr.get("reason") or "lost rounds"
            prom = _PROMOTED_LOSS_REASONS.get(reason)
            if prom:
                pkey, plabel = prom
                bucket = promoted.setdefault(pkey, {"label": plabel, "items": []})
                for rn in rounds:
                    bucket["items"].append(_round_item(rn, text=f"{tname}: {(rc.get(rn) or {}).get('summary') or 'Round %d' % rn}"))
                continue
            items = [_round_item(rn) for rn in rounds]
            queues.append({
                "key": f"team_{team.get('id','')}_{reason}",
                "label": f"{tname}: {_humanize(reason)}",
                "polarity": "issue", "items": sorted(items, key=lambda x: x["round"])})

    # 2b) promoted named loss-reason playlists (issues first). Built only when the underlying
    #     loss reason actually occurred -> empty ones are simply never created.
    for pkey, b in promoted.items():
        items = sorted(b["items"], key=lambda x: (x.get("round") or 0))
        queues.append({"key": pkey, "label": b["label"], "polarity": "issue", "items": items})

    # 2c) worst-swing rounds -- rank rounds by the per-round impact (total win-prob swing magnitude
    #     the engine already computed; analytics.rounds[].impact). This is a REAL per-round value,
    #     not a fabricated metric. Top 8 most decisive rounds. Skipped if no impact data exists.
    swing_rows = [r for r in (a.get("rounds") or [])
                  if isinstance(r, dict)
                  and (r.get("num") if r.get("num") is not None else r.get("round")) is not None
                  and (r.get("impact") or 0) > 0]
    if swing_rows:
        swing_rows.sort(key=lambda r: -(r.get("impact") or 0))
        items = []
        for r in swing_rows[:8]:
            rn = r.get("num") if r.get("num") is not None else r.get("round")
            card = rc.get(rn) or {}
            items.append({"round": rn, "t": float(card.get("watch_t") or 0), "player": -1,
                          "text": f"Swing {r.get('impact')} -- {card.get('summary') or 'Round %d' % rn}"})
        queues.append({"key": "worst_swing", "label": "Worst-swing rounds",
                       "polarity": "issue", "items": items})

    # 2d) best rounds -- biggest positive momentum swings. The good-polarity insights
    #     (multikills/high_impact) are AGGREGATES with no round/tick, so they aren't jumpable;
    #     instead we surface each round's single largest kill-swing moment (round_cards[].moments,
    #     a real engine-computed per-kill win-prob swing), keep the top 8, deduped by round.
    best = []
    for r in (a.get("round_cards") or []):
        if not isinstance(r, dict):
            continue
        rn = r.get("round")
        if rn is None:
            continue
        kills = [m for m in (r.get("moments") or [])
                 if isinstance(m, dict) and m.get("type") == "kill" and m.get("swing")]
        if not kills:
            continue
        top = max(kills, key=lambda m: m.get("swing") or 0)
        atk = top.get("atk")
        best.append((top.get("swing") or 0, {
            "round": rn, "t": float(r.get("watch_t") or 0),
            "player": sid2idx.get(str(atk), -1),
            "text": f"Top play +{top.get('swing')}% swing -- {r.get('summary') or 'Round %d' % rn}"}))
    if best:
        best.sort(key=lambda x: -x[0])
        seen, items = set(), []
        for _, it in best:
            if it["round"] in seen:
                continue
            seen.add(it["round"])
            items.append(it)
        queues.append({"key": "best_rounds", "label": "Best rounds",
                       "polarity": "good", "items": items[:8]})

    # 3) round-by-round queue (always available) from round_cards
    rounds_q = []
    for r in (a.get("round_cards") or []):
        rounds_q.append({"round": r.get("round"), "t": float(r.get("watch_t") or 0),
                         "player": -1, "text": r.get("summary") or f"Round {r.get('round')}"})
    if rounds_q:
        queues.append({"key": "round_by_round", "label": "Round-by-round",
                       "polarity": "neutral", "items": rounds_q})

    # order: issues (most items first), then positives, then neutral/rounds
    order = {"issue": 0, "good": 1, "neutral": 2}
    queues.sort(key=lambda q: (order.get(q["polarity"], 3), -len(q["items"])))
    return [q for q in queues if q["items"]]
