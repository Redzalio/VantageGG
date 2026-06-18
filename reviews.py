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

    # 2) team loss-reason queues (failed retakes / thrown advantages / ...), from team_coaching
    for team in (a.get("team_coaching") or {}).get("teams") or []:
        tname = team.get("name") or team.get("id") or "Team"
        for lr in (team.get("loss_reasons") or []):
            rounds = lr.get("rounds") or []
            if not rounds:
                continue
            items = []
            for rn in rounds:
                card = rc.get(rn) or {}
                items.append({"round": rn, "t": float(card.get("watch_t") or 0),
                              "player": -1, "text": card.get("summary") or f"Round {rn}"})
            queues.append({
                "key": f"team_{team.get('id','')}_{lr.get('reason','')}",
                "label": f"{tname}: {_humanize(lr.get('reason') or 'lost rounds')}",
                "polarity": "issue", "items": sorted(items, key=lambda x: x["round"])})

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
