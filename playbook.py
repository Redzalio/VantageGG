"""Team Playbook + adherence checking (#45).

A team's standard plays -- named utility setups per map+side (an A-exec, a default, a B-rush) --
saved as a small library, then CHECKED against a demo: of the rounds where you used utility on that
side, how often did your throws actually match the play? Surfaces which executes you run
consistently and which lineups you keep skipping.

A play is defined by its expected utility landings (type + map x/y) -- the most reliably checkable,
GENERIC signal (no callout polygons needed; matched by landing proximity, like the nade library).
The easy way to create one is `play_from_throws` over a round you ran the execute well.

Storage mirrors nades.py / reviews.py: stdlib-only, atomic JSON write. The adherence ENGINE is a
pure function (unit-tested here); the frontend mirrors it to check the loaded demo live -- KEEP THE
TWO IN SYNC (match_dist / exec_frac / the matching rule).
"""
import hashlib
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
PLAYBOOK_DIR = os.environ.get("PLAYBOOK_DIR") or os.path.join(HERE, "playbook")
PLAYBOOK_PATH = os.path.join(PLAYBOOK_DIR, "playbook.json")   # {"plays": [...]}

MATCH_DIST = 220.0     # units: a thrown nade within this of an expected landing counts as that element
EXEC_FRAC = 0.6        # a round "ran the play" if >= this fraction of its util elements were present
DEDUP_DIST = 120.0     # units: collapse near-identical landings when building a play from throws


# ---- storage ----------------------------------------------------------------
def _ensure():
    os.makedirs(PLAYBOOK_DIR, exist_ok=True)


def load_all():
    try:
        with open(PLAYBOOK_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and isinstance(d.get("plays"), list) else {"plays": []}
    except (OSError, ValueError):
        return {"plays": []}


def save_all(data):
    _ensure()
    tmp = PLAYBOOK_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, PLAYBOOK_PATH)


def plays_for(map_name):
    return [p for p in load_all()["plays"] if not map_name or p.get("map") == map_name]


def _pid(p):
    base = f"{p.get('map')}|{p.get('side')}|{p.get('name')}|{len(p.get('util') or [])}"
    return "pb_" + hashlib.sha1(base.encode("utf-8")).hexdigest()[:10]


def _norm_util(util):
    out = []
    for u in (util or []):
        try:
            out.append({"type": str(u.get("type") or "smoke"),
                        "x": round(float(u.get("x")), 1), "y": round(float(u.get("y")), 1)})
        except (TypeError, ValueError):
            continue
    return out


def add_play(raw):
    """Add or replace (by id) a play. side is 'ct'/'t'. Returns the stored play."""
    g = raw.get
    side = (g("side") or "").lower()
    play = {
        "map": (g("map") or "").strip()[:32],
        "side": side if side in ("ct", "t") else "ct",
        "name": (g("name") or "Play").strip()[:60],
        "note": (g("note") or "").strip()[:300],
        "buy": (g("buy") or "").strip()[:16],
        "util": _norm_util(g("util")),
    }
    play["id"] = g("id") or _pid(play)
    data = load_all()
    plays = data["plays"]
    for i, ex in enumerate(plays):
        if ex.get("id") == play["id"]:
            plays[i] = play
            break
    else:
        plays.append(play)
    save_all(data)
    return play


def delete_play(pid):
    data = load_all()
    n = len(data["plays"])
    data["plays"] = [p for p in data["plays"] if p.get("id") != pid]
    save_all(data)
    return n - len(data["plays"])


# ---- pure helpers (unit-tested; mirrored in the frontend) --------------------
def _dist(ax, ay, bx, by):
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def play_from_throws(throws, dedup_dist=DEDUP_DIST):
    """Build a play's `util` from a set of throws [{type, x, y}] (e.g. one round, one side),
    de-duping near-identical landings."""
    util = []
    for t in throws:
        try:
            x, y, ty = float(t["x"]), float(t["y"]), str(t.get("type") or "smoke")
        except (KeyError, TypeError, ValueError):
            continue
        if any(u["type"] == ty and _dist(x, y, u["x"], u["y"]) <= dedup_dist for u in util):
            continue
        util.append({"type": ty, "x": round(x, 1), "y": round(y, 1)})
    return util


def check_adherence(play, throws, match_dist=MATCH_DIST, exec_frac=EXEC_FRAC):
    """How consistently the demo ran `play`. throws = [{type, round, side('ct'/'t'/None), x, y}].

    Denominator = rounds where the play's side threw ANY utility (i.e. rounds you were active on
    that side), so adherence reads "when you use util on this side, how often is it this play".
    Returns rounds_applicable / rounds_executed / adherence_pct + per-element usage + per-round score.
    """
    util = play.get("util") or []
    side = play.get("side")
    base = {"rounds_applicable": 0, "rounds_executed": 0, "adherence_pct": 0,
            "executed_rounds": [], "elements": [], "by_round": []}
    if not util:
        return base
    rounds = sorted({t["round"] for t in throws
                     if t.get("side") == side and t.get("round") is not None})
    if not rounds:
        return base
    by_round = {}
    for r in rounds:
        rt = [t for t in throws if t.get("round") == r and t.get("side") == side]
        elems = []
        for u in util:
            hit = any(t.get("type") == u["type"]
                      and _dist(float(t["x"]), float(t["y"]), u["x"], u["y"]) <= match_dist
                      for t in rt)
            elems.append(bool(hit))
        present = sum(elems)
        by_round[r] = {"score": round(present / len(util), 2), "present": present,
                       "of": len(util), "elems": elems}
    executed = [r for r in rounds if by_round[r]["score"] >= exec_frac]
    elements = []
    for i, u in enumerate(util):
        used = sum(1 for r in rounds if by_round[r]["elems"][i])
        elements.append({"type": u["type"], "x": u["x"], "y": u["y"], "used": used,
                         "of": len(rounds), "used_pct": round(100.0 * used / len(rounds))})
    return {
        "rounds_applicable": len(rounds), "rounds_executed": len(executed),
        "adherence_pct": round(100.0 * len(executed) / len(rounds)),
        "executed_rounds": executed, "elements": elements,
        "by_round": [{"round": r, **by_round[r]} for r in rounds],
    }
