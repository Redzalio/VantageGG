"""goals.py -- persistent, match-aware Practice Goals (the team learning loop's "what we
practice next" node).

A goal is a measurable target the team commits to and the app GRADES across matches:
  - a metric (ADR, untraded opening deaths, retake WR, ...) with a known better-direction,
  - a target value, an optional scope (map / player), a drill, a status, and notes,
  - a baseline (the metric's value in the match the goal was created from).

Grading (cross-match, the Stage-7 slice that makes a goal useful) loads the cached demos,
de-dupes by source_sha1, filters to the goal's scope, and reports the recent series + a
verdict: fixed / improving / still_happening / insufficient (sample-size guarded).

Stdlib-only, JSON-on-disk (atomic) like nades.py / reviews.py. Stored at goals/goals.json.
"""
import datetime
import glob
import hashlib
import json
import os
import tempfile

import db          # goals now persist in SQLite (was goals/goals.json); grading still reads the cache
import matchindex   # reuse its safe per-match loader + created_at

HERE = os.path.dirname(os.path.abspath(__file__))
GOALS_DIR = os.environ.get("GOALS_DIR") or os.path.join(HERE, "goals")
GOALS_PATH = os.path.join(GOALS_DIR, "goals.json")   # legacy store -- imported into the DB once, then kept as a backup
CACHE_DIR = os.path.join(HERE, "cache")

STATUSES = ("open", "drilling", "fixed", "ignored")
MIN_SAMPLE = 3          # matches needed before we'll call a verdict (else "insufficient")

# ---- metric registry --------------------------------------------------------
# kind: how the value is computed from a match's analytics.
#   player  -> analytics.players[<scope.player>][key]  (team avg if no player)
#   insight -> count of analytics.insights[..][type==key] (team total if no player)
#   team    -> analytics.team_coaching.teams[<player's team>].<path>
# better: "high" (>= target is good) or "low" (<= target is good).
# "scopes" = value-level breakdowns the metric supports (besides map+player, which are
# universal, and role, which is a universal match-filter on player goals). side reads
# players[].sides[ct|t]; buy reads players[].buys[pistol|eco|force|full|light].
METRICS = [
    {"key": "adr", "label": "ADR", "kind": "player", "better": "high", "unit": "", "scopes": ["side"]},
    {"key": "kast", "label": "KAST %", "kind": "player", "better": "high", "unit": "%", "scopes": ["side"]},
    {"key": "kpr", "label": "Kills / round", "kind": "player", "better": "high", "unit": "", "scopes": ["side", "buy"]},
    {"key": "win_pct", "label": "Round win %", "kind": "player", "better": "high", "unit": "%", "scopes": ["buy"], "requires": "buy"},
    {"key": "hltv", "label": "Rating", "kind": "player", "better": "high", "unit": ""},
    {"key": "open_wr", "label": "Opening-duel win %", "kind": "player", "better": "high", "unit": "%"},
    {"key": "traded_pct", "label": "Traded-death %", "kind": "player", "better": "high", "unit": "%"},
    {"key": "udr", "label": "Utility dmg / round", "kind": "player", "better": "high", "unit": ""},
    {"key": "untraded_opening_death", "label": "Untraded opening deaths", "kind": "insight", "better": "low", "unit": ""},
    {"key": "dry_opening", "label": "Dry opening peeks", "kind": "insight", "better": "low", "unit": ""},
    {"key": "pos", "label": "Positioning / mid-round deaths", "kind": "insight", "better": "low", "unit": ""},
    {"key": "clumping", "label": "Bad-spacing rounds", "kind": "insight", "better": "low", "unit": ""},
    {"key": "predictable", "label": "Predictable deaths", "kind": "insight", "better": "low", "unit": ""},
    {"key": "team_trade_pct", "label": "Team trade %", "kind": "team", "better": "high", "unit": "%", "path": "trade_pct"},
    {"key": "team_entry_wr", "label": "Team entry-duel win %", "kind": "team", "better": "high", "unit": "%", "path": "entry.wr"},
    {"key": "team_post_plant_wr", "label": "Post-plant win %", "kind": "team", "better": "high", "unit": "%", "path": "post_plant.wr"},
    {"key": "team_retake_wr", "label": "Retake win %", "kind": "team", "better": "high", "unit": "%", "path": "retake.wr"},
]
_METRIC_BY_KEY = {m["key"]: m for m in METRICS}

# Scope option lists (offered in the create UI). Roles come from analytics.ct_role/t_role.
SIDES = ["ct", "t"]
BUYS = ["pistol", "eco", "force", "full"]          # "light" exists in data but rarely goal-worthy
ROLES = ["Entry", "Lurker", "Support", "AWP", "Rifler", "Anchor", "Rotator"]


def metric_by_key(key):
    return _METRIC_BY_KEY.get(key)


def _split_value(split, key):
    """Read a metric from a per-side/per-buy split dict; derive kpr/kd when the split only
    carries k/d/rounds (sides have adr/kast/kd; buys have kpr/win_pct)."""
    if not isinstance(split, dict):
        return None
    v = split.get(key)
    if isinstance(v, (int, float)):
        return float(v)
    if key == "kpr" and isinstance(split.get("k"), (int, float)) and split.get("rounds"):
        return round(split["k"] / split["rounds"], 2)
    if key == "kd" and isinstance(split.get("k"), (int, float)):
        return round(split["k"] / max(1, split.get("d") or 0), 2)
    return None


def _played_role(analytics, player, role):
    """True if `player` held `role` (on either side) in this match."""
    for x in (analytics.get("players") or []):
        if str(x.get("steamid")) == str(player):
            return x.get("ct_role") == role or x.get("t_role") == role
    return False


# ---- storage (SQLite via db.py) ---------------------------------------------
def load_goals():
    """All goal records (no grading). Reads the `goals` table."""
    return db.goal_all()


def migrate_legacy_json():
    """One-time import of the old goals/goals.json into the DB, then rename it as a backup so it
    won't re-import. Idempotent: no file (or already imported) -> no-op. Returns the number imported."""
    if not os.path.exists(GOALS_PATH):
        return 0
    try:
        with open(GOALS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return 0
    n = 0
    for g in (data if isinstance(data, list) else []):
        if isinstance(g, dict) and g.get("id") and db.goal_get(g["id"]) is None:
            db.goal_upsert(normalize(g))      # normalize keeps the existing id + coerces owner/team
            n += 1
    try:
        os.replace(GOALS_PATH, GOALS_PATH + ".imported")   # mark done + keep the original as a backup
    except OSError:
        pass
    return n


def _gid(g):
    base = (f"{g.get('metric')}|{g.get('target')}|{json.dumps(g.get('scope'), sort_keys=True)}"
            f"|{g.get('created_at')}|{g.get('owner_user_id')}|{g.get('team_id')}")
    return "g_" + hashlib.sha1(base.encode("utf-8")).hexdigest()[:10]


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _int_or_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def normalize(raw):
    """Raw dict (from the UI) -> a stored goal record."""
    g = raw.get
    metric = str(g("metric") or "")
    m = metric_by_key(metric)
    scope = g("scope") or {}
    if not isinstance(scope, dict):
        scope = {}
    scope = {k: scope.get(k) for k in ("map", "player", "side", "role", "buy", "group", "members", "label") if scope.get(k)}
    # A "squad"/"team" group averages a metric over a snapshot of YOUR players' steamids -- so a goal
    # tracks your side, not the whole-match average (analytics.players is all 10, opponents included).
    # squad = your auto-detected stack; team = a team you created/joined. Either has no single
    # player/role (role is a per-player filter); an empty/bad member list falls back to whole-match.
    if scope.get("group") in ("squad", "team"):
        mem = scope.get("members")
        scope["members"] = [str(s) for s in mem if s][:6] if isinstance(mem, list) else []
        if scope["members"]:
            scope.pop("player", None)
            scope.pop("role", None)
            if scope.get("label"):
                scope["label"] = str(scope["label"])[:40]
            else:
                scope.pop("label", None)
        else:
            scope.pop("group", None)
            scope.pop("members", None)
            scope.pop("label", None)
    else:
        scope.pop("group", None)
        scope.pop("members", None)
        scope.pop("label", None)
    try:
        target = float(g("target"))
    except (TypeError, ValueError):
        target = 0.0
    status = g("status") if g("status") in STATUSES else "open"
    goal = {
        "metric": metric,
        "title": (g("title") or (m["label"] if m else metric)).strip()[:100],
        "target": round(target, 2),
        "scope": scope,
        "drill": (g("drill") or "").strip()[:400],
        "status": status,
        "notes": (g("notes") or "").strip()[:1000],
        "source_match_key": g("source_match_key") or None,
        "created_at": g("created_at") or _now(),
        "baseline": g("baseline"),   # filled in by add_goal from the source match if absent
        # ownership (set by the API from the session, NOT trusted from the client):
        "owner_user_id": _int_or_none(g("owner_user_id")),   # creator; None = local/legacy (visible to all)
        "team_id": _int_or_none(g("team_id")),               # shared with this team; None = personal
    }
    goal["id"] = g("id") or _gid(goal)
    return goal


def add_goal(raw, cache_dir=CACHE_DIR):
    goal = normalize(raw)
    if goal.get("baseline") is None and goal.get("source_match_key"):
        goal["baseline"] = _baseline_from_source(goal, cache_dir)
    db.goal_upsert(goal)
    return goal


def update_goal(goal_id, fields):
    """Validate then persist editable fields (status/notes/title/target/drill). Returns the updated
    record, or None if the goal doesn't exist."""
    clean = {}
    for k in ("status", "notes", "title", "target", "drill"):
        if k not in fields:
            continue
        if k == "target":
            try:
                clean["target"] = round(float(fields[k]), 2)
            except (TypeError, ValueError):
                pass
        elif k == "status":
            if fields[k] in STATUSES:
                clean["status"] = fields[k]
        else:
            clean[k] = str(fields[k])[:1000]
    return db.goal_update(goal_id, clean)


def delete_goal(goal_id):
    return db.goal_delete(goal_id)


# ---- match metrics + grading ------------------------------------------------
def _get_path(obj, path):
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _team_of(teams, steamid):
    if steamid:
        sid = str(steamid)
        for t in teams:
            for x in (t.get("players") or []):
                xid = str(x.get("steamid") if isinstance(x, dict) else x)
                if xid == sid:
                    return t
    return teams[0] if teams else None


def metric_value(metric, analytics, scope):
    """The metric's value for one match's analytics under `scope`, or None if unavailable."""
    if not metric or not isinstance(analytics, dict):
        return None
    scope = scope or {}
    player = scope.get("player")
    members = [str(s) for s in (scope.get("members") or [])] if scope.get("group") in ("squad", "team") else None
    if metric["kind"] == "player":
        players = analytics.get("players") or []
        key = metric["key"]
        caps = metric.get("scopes") or []

        def _one(p):                                         # this player's value under side/buy scope
            if scope.get("buy") and "buy" in caps:
                return _split_value((p.get("buys") or {}).get(scope["buy"]), key)
            if scope.get("side") and "side" in caps:
                return _split_value((p.get("sides") or {}).get(scope["side"]), key)
            v = p.get(key)
            return float(v) if isinstance(v, (int, float)) else None
        if members:                                          # average over your players who played
            ids = set(members)
            vals = [v for p in players if str(p.get("steamid")) in ids
                    for v in (_one(p),) if v is not None]
            return round(sum(vals) / len(vals), 2) if vals else None
        if player:
            p = next((x for x in players if str(x.get("steamid")) == str(player)), None)
            return _one(p) if p is not None else None
        vals = [x.get(key) for x in players if isinstance(x.get(key), (int, float))]
        return round(sum(vals) / len(vals), 2) if vals else None
    if metric["kind"] == "insight":
        ins = analytics.get("insights") or {}
        if members:                                          # sum occurrences across your players present
            pids = {str(x.get("steamid")) for x in (analytics.get("players") or [])}
            present = set(members) & pids
            if not present:
                return None                                  # none of them played this match -> skip it
            return float(sum(1 for sid in present for x in (ins.get(sid) or []) if x.get("type") == metric["key"]))
        if player:
            lst = ins.get(str(player)) or []
            return float(sum(1 for x in lst if x.get("type") == metric["key"]))
        return float(sum(1 for lst in ins.values() for x in (lst or []) if x.get("type") == metric["key"]))
    if metric["kind"] == "team":
        teams = (analytics.get("team_coaching") or {}).get("teams") or []
        anchor = player or (members[0] if members else None)   # group -> the team holding its first member
        t = _team_of(teams, anchor)
        if not t:
            return None
        v = _get_path(t, metric["path"])
        return float(v) if isinstance(v, (int, float)) else None
    return None


_ANA_SUBDIR = "_ana"             # analytics-only sidecars live in cache/_ana/
_matches_memo = {"sig": None, "recs": None}


def _ana_path(cache_dir, key):
    return os.path.join(cache_dir, _ANA_SUBDIR, key + ".json")


def _compact_record(path, cache_dir):
    """{key, sha, map, created_at, analytics} for one cache file, via a cheap analytics-only
    sidecar (built once per source, refreshed when the source file changes). Demo JSONs carry
    multi-MB frame data; grading only needs `analytics`, so re-parsing the whole file on every
    grade is what made /api/goals take ~6s. The sidecar drops that to a few ms. None = not a match."""
    key = os.path.splitext(os.path.basename(path))[0]
    side = _ana_path(cache_dir, key)
    try:
        if os.path.getmtime(side) >= os.path.getmtime(path):
            with open(side, encoding="utf-8") as f:
                return json.load(f)
    except (OSError, ValueError):
        pass                                  # missing / stale / corrupt sidecar -> rebuild
    data = matchindex._load_match(path)       # full parse (frames incl.) -- only on a miss
    if data is None:
        return None
    rec = {"key": key, "sha": data.get("source_sha1") or key, "map": data.get("map"),
           "created_at": matchindex._created_at(path, key), "analytics": data.get("analytics") or {}}
    try:                                       # best-effort atomic sidecar write
        os.makedirs(os.path.dirname(side), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tmp_ana_", suffix=".json", dir=os.path.dirname(side))
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(rec, out)
        os.replace(tmp, side)
    except OSError:
        pass
    return rec


def _matches(cache_dir=CACHE_DIR):
    """Unique cached matches (de-duped by source_sha1), newest-first, as compact records.
    Memoized in-process keyed by the cache dir's *.json (path, mtime) signature, so repeated
    grades (e.g. a status change -> refresh) don't touch disk until a demo is added/re-parsed."""
    paths = sorted(glob.glob(os.path.join(cache_dir, "*.json")))
    try:
        sig = tuple((p, os.path.getmtime(p)) for p in paths)
    except OSError:
        sig = None
    if sig is not None and _matches_memo["sig"] == sig:
        return _matches_memo["recs"]
    by_sha = {}
    for path in paths:
        rec = _compact_record(path, cache_dir)
        if rec is None:
            continue
        prev = by_sha.get(rec["sha"])
        if prev is None or (rec["created_at"] or "") > (prev["created_at"] or ""):
            by_sha[rec["sha"]] = rec
    out = sorted(by_sha.values(), key=lambda r: r["created_at"] or "", reverse=True)
    _matches_memo["sig"], _matches_memo["recs"] = sig, out
    return out


def _baseline_from_source(goal, cache_dir):
    m = metric_by_key(goal["metric"])
    if not m:
        return None
    src = goal.get("source_match_key")
    for rec in _matches(cache_dir):
        if rec["sha"] == src or rec["key"] == src or rec["sha"].startswith(str(src)):
            v = metric_value(m, rec["analytics"], goal.get("scope"))
            return round(v, 2) if v is not None else None
    return None


def _meets(better, value, target):
    return value <= target if better == "low" else value >= target


def grade(goal, matches):
    """Cross-match progress for one goal: recent series (newest-first) + a verdict."""
    m = metric_by_key(goal.get("metric"))
    if not m:
        return {"verdict": "unknown_metric", "samples": 0, "series": []}
    scope = goal.get("scope") or {}
    role, player = scope.get("role"), scope.get("player")
    series = []
    for rec in matches:                      # newest-first
        if scope.get("map") and rec["map"] != scope["map"]:
            continue
        if role and player and not _played_role(rec["analytics"], player, role):
            continue                          # only matches where the player held this role
        v = metric_value(m, rec["analytics"], scope)
        if v is None:
            continue
        series.append({"key": rec["key"][:12], "map": rec["map"],
                       "created_at": rec["created_at"], "value": round(v, 1)})
    n = len(series)
    target = goal.get("target", 0)
    better = m["better"]
    res = {"metric_label": m["label"], "unit": m["unit"], "better": better,
           "target": target, "samples": n, "series": series[:10],
           "current": series[0]["value"] if n else None}
    if n == 0:
        res["verdict"] = "no_data"
        return res
    baseline = goal.get("baseline")
    if not isinstance(baseline, (int, float)):
        baseline = series[-1]["value"]       # oldest tracked match
    res["baseline"] = round(baseline, 1)
    vals = [s["value"] for s in series]          # newest-first
    recent = vals[:3]
    recent_avg = round(sum(recent) / len(recent), 1)
    res["recent_avg"] = recent_avg
    res["meets_target"] = _meets(better, recent_avg, target)
    # rolling 3 / 5 / 10-match averages (null until that many matches exist) -- the
    # "rolling trend check" surface; each is also flagged for whether it meets the target.
    res["windows"] = {str(w): ({"avg": round(sum(vals[:w]) / w, 1),
                                "meets": _meets(better, round(sum(vals[:w]) / w, 1), target)}
                               if n >= w else None)
                      for w in (3, 5, 10)}
    if n < MIN_SAMPLE:
        res["verdict"] = "insufficient"
    elif all(_meets(better, v, target) for v in recent) or _meets(better, recent_avg, target):
        res["verdict"] = "fixed"
    else:
        toward = (recent_avg < baseline) if better == "low" else (recent_avg > baseline)
        margin = max(0.5, abs(baseline) * 0.05)
        res["verdict"] = "improving" if (toward and abs(recent_avg - baseline) >= margin) else "still_happening"
    # For a squad/team goal, also break the average down PER MEMBER (each member's own current value +
    # trend), so you can see who's above/below target -- not just the collapsed average. Reuses grade()
    # with a single-player scope (no group -> no recursion); members with no tracked match are dropped.
    members = scope.get("members") if scope.get("group") in ("squad", "team") else None
    if members:
        names = _name_map(matches)
        breakdown = []
        for sid in members:
            sub_scope = {k: scope[k] for k in ("map", "side", "buy") if scope.get(k)}
            sub_scope["player"] = str(sid)
            sg = grade({"metric": goal.get("metric"), "target": target, "scope": sub_scope}, matches)
            if not sg.get("samples"):
                continue
            breakdown.append({"steamid": str(sid), "name": names.get(str(sid), str(sid)),
                              "current": sg.get("current"), "recent_avg": sg.get("recent_avg"),
                              "baseline": sg.get("baseline"), "meets": sg.get("meets_target"),
                              "verdict": sg.get("verdict"), "samples": sg.get("samples"),
                              "series": sg.get("series", [])[:8]})
        breakdown.sort(key=lambda x: (x["current"] is None,
                                      -(x["current"] or 0) if better == "high" else (x["current"] or 0)))
        res["members"] = breakdown
    return res


def _name_map(matches):
    """steamid -> most-recent display name across the matches (newest-first; first seen wins)."""
    names = {}
    for rec in matches:
        for p in (rec["analytics"].get("players") or []):
            sid = str(p.get("steamid"))
            if sid and sid not in names and p.get("name"):
                names[sid] = p["name"]
    return names


def progress(cache_dir=CACHE_DIR):
    """All goals with their grading attached. One match-load pass shared across goals."""
    matches = _matches(cache_dir)
    out = []
    for g in load_goals():
        out.append({**g, "progress": grade(g, matches)})
    return out


# ---- ownership / visibility (personal vs shared-with-a-team) -----------------
def is_visible(g, uid, team_ids):
    """A goal is visible to user `uid` (in teams `team_ids`) if they created it, it's shared with one
    of their teams, or it's a legacy/local goal with no owner (single-user installs -> visible to all)."""
    owner = g.get("owner_user_id")
    if owner is None:
        return True
    if uid is not None and owner == uid:
        return True
    tid = g.get("team_id")
    return tid is not None and tid in (team_ids or ())


def visible_progress(cache_dir, uid, team_ids):
    """Graded goals the user may see (own + their teams' shared + legacy ownerless). The visibility
    filter runs in SQL (db.goals_visible); grading then runs over the cached matches."""
    matches = _matches(cache_dir)
    return [{**g, "progress": grade(g, matches)} for g in db.goals_visible(uid, team_ids)]


def get_goal(goal_id):
    return db.goal_get(goal_id)


# ---- recurring mistakes (repeated-mistake detection across matches) ---------
# Issue insight-type -> (human label, the goal metric to suggest when you turn it into a goal).
# None metric -> the UI keyword-guesses one. Keys mirror analytics.py insight types.
RECURRING_LABELS = {
    "untraded_opening_death": ("Untraded opening deaths", "untraded_opening_death"),
    "dry_opening": ("Dry opening peeks", "dry_opening"),
    "weak_opening_duels": ("Weak opening duels", "open_wr"),
    "pos": ("Positioning / mid-round deaths", "pos"),
    "mid_round_leak": ("Mid-round deaths", "pos"),
    "isolated_death": ("Isolated deaths (far from team)", "pos"),
    "clumping": ("Bad spacing (clumping)", "clumping"),
    "predictable": ("Predictable deaths (same spot)", "predictable"),
    "low_traded_deaths": ("Low traded-death %", "traded_pct"),
    "untraded_despite_support": ("Untraded despite support", "traded_pct"),
    "low_utility": ("Low utility damage", "udr"),
    "team_flashes": ("Team flashes", None),
    "eco_discipline": ("Eco discipline (over-buying)", None),
    "moving_shots": ("Shooting while moving", None),
    "bad_save": ("Bad saves (kept a gun in a lost round)", None),
}


def _suggest_target(series, suggest_metric):
    """Suggest a reachable improvement target from a recurring-mistake series.

    `series` is the per-match occurrence count (newest-first). For insight-type
    metrics (better=="low") the target is ~30 % fewer than the recent 3-match
    average -- achievable but not perfection. For player metrics that are
    better=="high" (open_wr, traded_pct, udr) the series is occurrence counts,
    not the metric value, so we return None and let the user fill in the target.
    """
    recent = series[:3] if series else []
    if not recent:
        return None
    recent_avg = sum(recent) / len(recent)
    m = _METRIC_BY_KEY.get(suggest_metric or "")
    # If the goal metric is "high-better" (open_wr, traded_pct, …) the series
    # counts insight occurrences, not the metric value — can't derive a target.
    if m is not None and m["better"] == "high":
        return None
    # Low-better / count metric: target = 70 % of recent avg (30 % improvement).
    if recent_avg <= 0:
        return None
    t = round(recent_avg * 0.70)
    # Never suggest 0 unless the average is already very close (≤ 1.0) — zero
    # is usually perfection, not a realistic first goal.
    if t == 0 and recent_avg > 1.0:
        t = 1
    return float(t)


def _count_trend(series):
    """Direction of an occurrence-count series (newest-first; lower = better). Zeros count --
    a recent match where it didn't happen IS improvement."""
    if len(series) < 2:
        return "new"
    h = max(1, len(series) // 2)
    recent = sum(series[:h]) / h
    older = sum(series[h:]) / max(1, len(series) - h)
    if recent < older - 0.5:
        return "improving"
    if recent > older + 0.5:
        return "worsening"
    return "steady"


def recurring_mistakes(cache_dir=CACHE_DIR, player=None, min_matches=2):
    """Issue-polarity insight TYPES that recur across the (deduped) cached matches.
    For one player (only matches they played) or the whole team. Each recurring type gets a
    per-match occurrence series (newest-first, 0 where it didn't happen), how many matches it
    showed up in, a trend, and a suggested goal metric -- so the UI can offer '+ Goal'."""
    matches = _matches(cache_dir)                   # newest-first, deduped
    rows = []
    for rec in matches:
        a = rec["analytics"]
        pids = {str(x.get("steamid")) for x in (a.get("players") or [])}
        if player and str(player) not in pids:
            continue                                # only matches the player actually played
        ins = a.get("insights") or {}
        sources = [ins.get(str(player)) or []] if player else list(ins.values())
        counts = {}
        for lst in sources:
            for x in (lst or []):
                if x.get("polarity") == "good":
                    continue
                t = x.get("type")
                if t:
                    counts[t] = counts.get(t, 0) + 1
        rows.append({"map": rec["map"], "counts": counts})
    n = len(rows)
    types = set()
    for r in rows:
        types.update(r["counts"])
    out = []
    for t in types:
        series = [r["counts"].get(t, 0) for r in rows]    # newest-first; 0 where absent
        present = sum(1 for c in series if c > 0)
        if present < min_matches:
            continue
        label, metric = RECURRING_LABELS.get(t, (t.replace("_", " ").capitalize(), None))
        out.append({"type": t, "label": label, "suggest_metric": metric,
                    "matches_present": present, "matches_total": n,
                    "total": sum(series), "recent": series[0] if series else 0,
                    "series": series[:10], "trend": _count_trend(series),
                    "suggested_target": _suggest_target(series, metric)})
    out.sort(key=lambda r: (-r["matches_present"], -r["total"]))
    return {"player": player, "matches": n, "recurring": out}
