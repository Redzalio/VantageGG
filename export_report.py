"""export_report.py -- a rich, server-side match-report exporter for VantageGG.

Turns the `analytics` dict (analytics.py `analyze()` output) into a single structured
report model, then renders it to three shareable formats:

  * to_text(report) -> str   -- a sectioned, column-aligned plain-text report (paste anywhere)
  * to_json(report) -> str   -- pretty json.dumps of the structured model
  * to_html(report) -> str   -- a self-contained, print-friendly HTML doc (the "PDF" path:
                                open it and print-to-PDF; inline <style>, no scripts, no assets)

The structured model is built by:

  * build_report(analytics, *, my_side=None, recurring=None, title=None,
                 map_name=None, score=None, date=None) -> dict

This export is intentionally MUCH richer than the existing Discord text blurb
(static/js/analytics.js shareReportText / shareCoaching): a full both-teams scoreboard,
the coaching summary (via coaching_summary.build_summary), top fixes + what-went-well,
key rounds with buy types, economy/buy outcomes, utility totals, and per-player position
notes.

Design rules:
  * NEVER throws on missing/empty/partial input (analytics may be {} or partial).
  * Pure / dependency-free apart from the local coaching_summary module + stdlib.
  * Importing this module touches no network and needs no key (coaching_summary's AI path
    is NOT used here -- we call the deterministic build_summary directly).

Convenience:
  * render(analytics, fmt="text", **kw) -> str   -- dispatch to text/json/html.
"""
import html
import json

import coaching_summary


# --------------------------------------------------------------------------- #
# small, defensive accessors (every read assumes the dict may be {} / partial)
# --------------------------------------------------------------------------- #
def _d(v):
    return v if isinstance(v, dict) else {}


def _l(v):
    return v if isinstance(v, list) else []


def _num(v, default=0):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _s(v, default=""):
    if v is None:
        return default
    return str(v)


def _round_n(v):
    n = v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    if n is None:
        return None
    return int(n) if float(n).is_integer() else n


def _fmt_num(v, nd=0):
    """Format a numeric for display; '--' when not a real number."""
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return "--"
    if nd == 0:
        return str(int(round(v)))
    return f"{v:.{nd}f}"


# --------------------------------------------------------------------------- #
# meta / score
# --------------------------------------------------------------------------- #
def _resolve_meta(analytics, *, title, map_name, score, date):
    """Match-level meta. The analytics dict itself does NOT carry map/score/date (the app
    stores those on the demo wrapper), so they arrive as kwargs; fall back to analytics.meta
    when a caller passes the raw analytics meta through, else leave blank."""
    meta = _d(analytics.get("meta"))
    mp = map_name or meta.get("map") or analytics.get("map")
    dt = date or meta.get("date") or meta.get("parsed_at") or analytics.get("date")
    sc = score or meta.get("score") or analytics.get("score")
    n_rounds = _round_n(analytics.get("n_rounds"))
    return {
        "title": title or "CS2 Match Report",
        "map": _s(mp) or None,
        "score": _s(sc) or None,
        "date": _s(dt) or None,
        "n_rounds": n_rounds,
        "have_econ": bool(analytics.get("have_econ", False)),
        "analytics_version": meta.get("analytics_version") or analytics.get("version"),
        "note": meta.get("note"),
    }


def _teams(analytics):
    return _l(_d(analytics.get("team_coaching")).get("teams"))


def _score_line(analytics, meta):
    """A one-line scoreboard headline: 'Team A 13 - 11 Team B (de_dust2)'."""
    teams = _teams(analytics)
    parts = []
    if len(teams) >= 2:
        a, b = teams[0], teams[1]
        an = _s(a.get("name")) or ("Team " + _s(a.get("id"), "A"))
        bn = _s(b.get("name")) or ("Team " + _s(b.get("id"), "B"))
        aw = int(_num(a.get("won")))
        bw = int(_num(b.get("won")))
        parts.append(f"{an} {aw} - {bw} {bn}")
    elif meta.get("score"):
        parts.append(_s(meta["score"]))
    if meta.get("map"):
        parts.append(f"({meta['map']})")
    if not parts and meta.get("n_rounds"):
        parts.append(f"{meta['n_rounds']} rounds")
    return "  ".join(parts) if parts else "Match report"


# --------------------------------------------------------------------------- #
# scoreboard (both teams)
# --------------------------------------------------------------------------- #
# columns: (key, header, formatter)
_SCORE_COLS = [
    ("name", "Player", lambda p: _s(p.get("name"), "?")),
    ("kad", "K-A-D", lambda p: f"{int(_num(p.get('kills')))}-"
                                f"{int(_num(p.get('assists')))}-{int(_num(p.get('deaths')))}"),
    ("adr", "ADR", lambda p: _fmt_num(p.get("adr"), 0)),
    ("kast", "KAST", lambda p: _fmt_num(p.get("kast"), 0) + ("%" if _num(p.get("kast")) else "")),
    ("hltv", "HLTV", lambda p: _fmt_num(p.get("hltv"), 2)),
    ("kd", "K/D", lambda p: _fmt_num(p.get("kd"), 2)),
    ("open", "Open W-L", lambda p: f"{int(_num(p.get('open_k')))}-{int(_num(p.get('open_d')))}"),
    ("traded", "Trd%", lambda p: _fmt_num(p.get("traded_pct"), 0)),
    ("udr", "UDR", lambda p: _fmt_num(p.get("udr"), 1)),
    ("util", "Util", lambda p: str(_util_thrown(p))),
]


def _util_thrown(p):
    """Total grenades thrown by a player (smokes+flashes+he+molotov), best-effort."""
    return int(_num(p.get("smokes")) + _num(p.get("flashes_thrown"))
               + _num(p.get("hes")) + _num(p.get("molotovs")))


def _player_row(p):
    """One scoreboard row as an ordered dict of display strings + a few raw values."""
    row = {key: fmt(p) for key, _hdr, fmt in _SCORE_COLS}
    row["steamid"] = _s(p.get("steamid"))
    row["hltv_raw"] = _num(p.get("hltv"))
    return row


def _team_name(t, fallback):
    return _s(t.get("name")) or fallback


def _build_scoreboard(analytics):
    """Both teams' scoreboards. Players are grouped by team_coaching rosters (by name); any
    player not matched to a team lands in an 'Other' group so nobody is dropped."""
    players = _l(analytics.get("players"))
    teams = _teams(analytics)
    by_name = {}
    for p in players:
        if isinstance(p, dict):
            by_name[_s(p.get("name"))] = p

    groups = []
    claimed = set()
    for i, t in enumerate(teams):
        names = [_s(n) for n in _l(t.get("players"))]
        rows = []
        for nm in names:
            p = by_name.get(nm)
            if p is not None:
                rows.append(_player_row(p))
                claimed.add(nm)
        rows.sort(key=lambda r: -(r.get("hltv_raw") or 0))
        groups.append({
            "id": _s(t.get("id"), chr(ord("A") + i)),
            "name": _team_name(t, "Team " + _s(t.get("id"), chr(ord("A") + i))),
            "start_side": _s(t.get("start_side")) or None,
            "won": int(_num(t.get("won"))),
            "lost": int(_num(t.get("lost"))),
            "rows": rows,
        })

    # any players not on a listed team (subs, missing rosters, no team_coaching at all)
    leftover = [p for nm, p in by_name.items() if nm not in claimed]
    if leftover and (groups or len(players)):
        rows = [_player_row(p) for p in leftover]
        rows.sort(key=lambda r: -(r.get("hltv_raw") or 0))
        # if there were NO teams at all, this is the whole scoreboard -> call it "Players"
        groups.append({"id": "X", "name": "Players" if not teams else "Other",
                       "start_side": None, "won": 0, "lost": 0, "rows": rows})
    return [g for g in groups if g["rows"]]


_SCORE_HEADERS = [hdr for _k, hdr, _f in _SCORE_COLS]


# --------------------------------------------------------------------------- #
# fixes / what-went-well / key rounds / economy / utility / positions
# --------------------------------------------------------------------------- #
def _top_fixes(analytics, team):
    """Top-3 things to fix: prefer the chosen team's practice plan, else team-level top areas."""
    out = []
    for pp in _l(_d(team).get("practice_plan"))[:3]:
        if not isinstance(pp, dict):
            continue
        out.append({"focus": _s(pp.get("focus")) or "Fix",
                    "drill": _s(pp.get("drill")) or "",
                    "rounds": [r for r in _l(pp.get("rounds")) if _round_n(r) is not None][:8]})
    if not out:
        for a in _l(_d(analytics.get("team")).get("top_areas"))[:3]:
            if isinstance(a, dict) and a.get("area") and a.get("area") != "Mistake":
                out.append({"focus": _s(a["area"]),
                            "drill": f"Most common gap on the team ({int(_num(a.get('players')))} players).",
                            "rounds": []})
    return out


_POS_LABEL = {
    "good_openings": "Strong entrying",
    "good_spacing": "Great spacing (deaths get traded)",
    "good_utility": "Useful utility damage",
    "multikills": "Multi-kill rounds",
    "high_impact": "Consistent round impact",
}


def _what_went_well(analytics, team):
    """Up to 5 distinct positive ('good' polarity) insights for the chosen team's players."""
    insights = _d(analytics.get("insights"))
    players = _l(analytics.get("players"))
    name_by_sid = {_s(p.get("steamid")): _s(p.get("name")) for p in players if isinstance(p, dict)}
    team_names = set(_s(n) for n in _l(_d(team).get("players")))
    out, seen = [], set()
    for sid, lst in insights.items():
        nm = name_by_sid.get(_s(sid), "")
        if team_names and nm not in team_names:
            continue
        for x in _l(lst):
            if not isinstance(x, dict) or x.get("polarity") != "good":
                continue
            typ = _s(x.get("type"))
            key = typ + "|" + nm
            if key in seen:
                continue
            seen.add(key)
            label = _POS_LABEL.get(typ) or typ.replace("_", " ").strip() or "Strength"
            out.append({"label": label, "player": nm, "text": _s(x.get("text"))})
    return out[:5]


def _key_rounds(analytics, limit=8):
    """Most decisive rounds (by round impact), each with buy types + the round-card summary."""
    cards = _l(analytics.get("round_cards"))
    rounds = _l(analytics.get("rounds"))
    impact_by_num, buy_by_num = {}, {}
    for r in rounds:
        if isinstance(r, dict) and _round_n(r.get("num")) is not None:
            n = _round_n(r["num"])
            impact_by_num[n] = _num(r.get("impact"))
            buy_by_num[n] = (_s(r.get("buy_ct")), _s(r.get("buy_t")))
    out = []
    for c in cards:
        if not isinstance(c, dict):
            continue
        n = _round_n(c.get("round"))
        if n is None:
            continue
        bct, bt = buy_by_num.get(n, (_s(c.get("buy_ct")), _s(c.get("buy_t"))))
        out.append({
            "round": n,
            "winner": _s(c.get("winner")) or "?",
            "reason": _s(c.get("reason")),
            "buy_ct": bct or "?",
            "buy_t": bt or "?",
            "summary": _s(c.get("summary")),
            "watch_t": _num(c.get("watch_t"), None) if isinstance(c.get("watch_t"), (int, float)) else None,
            "impact": _num(impact_by_num.get(n, 0)),
        })
    out.sort(key=lambda r: -(r["impact"] or 0))
    return out[:limit]


def _economy(analytics, team):
    """Buy-type outcomes (rounds + win%). Prefer the chosen team's economy; else the
    match-level team.buy_outcomes."""
    src = _d(team).get("economy") or _d(analytics.get("team")).get("buy_outcomes") or {}
    order = ["pistol", "eco", "force", "light", "full", "unknown"]
    out = []
    for bt in order + [b for b in src if b not in order]:
        d = src.get(bt)
        if isinstance(d, dict) and _num(d.get("rounds")):
            out.append({"buy": bt, "rounds": int(_num(d.get("rounds"))),
                        "win_pct": _num(d.get("win_pct"), None)})
    return out


def _utility_totals(analytics, team):
    """Team utility totals + util dmg/round (averaged over the team's players)."""
    players = _l(analytics.get("players"))
    team_names = set(_s(n) for n in _l(_d(team).get("players")))
    mine = [p for p in players if isinstance(p, dict)
            and (not team_names or _s(p.get("name")) in team_names)]
    if not mine:
        mine = [p for p in players if isinstance(p, dict)]
    tot = {"smokes": 0, "flashes": 0, "he": 0, "molotov": 0}
    udr_sum, udr_n = 0.0, 0
    for p in mine:
        tot["smokes"] += int(_num(p.get("smokes")))
        tot["flashes"] += int(_num(p.get("flashes_thrown")))
        tot["he"] += int(_num(p.get("hes")))
        tot["molotov"] += int(_num(p.get("molotovs")))
        udr_sum += _num(p.get("udr"))
        udr_n += 1
    tot["udr_avg"] = round(udr_sum / udr_n, 1) if udr_n else 0.0
    tot["enemy_flashed"] = sum(int(_num(p.get("enemy_flashed"))) for p in mine)
    tot["team_flashed"] = sum(int(_num(p.get("team_flashed"))) for p in mine)
    return tot


def _position_notes(analytics, limit_players=10, per_player=2):
    """Per-player top position notes from position_stats (most-contested callouts)."""
    players = _l(analytics.get("players"))
    out = []
    for p in players:
        if not isinstance(p, dict):
            continue
        rows = _l(p.get("position_stats"))
        if not rows:
            continue
        zones = []
        for z in rows[:per_player]:
            if not isinstance(z, dict):
                continue
            zones.append({
                "zone": _s(z.get("zone"), "?"),
                "k": int(_num(z.get("k"))),
                "d": int(_num(z.get("d"))),
                "kd": _num(z.get("kd"), None),
                "open_k": int(_num(z.get("open_k"))),
                "open_d": int(_num(z.get("open_d"))),
            })
        if zones:
            out.append({"name": _s(p.get("name"), "?"), "zones": zones})
    return out[:limit_players]


def _pick_team(analytics, my_side):
    """The team to focus the coaching narrative on: my_side starter, else most losses, else first."""
    teams = _teams(analytics)
    if not teams:
        return None
    if my_side:
        want = "CT" if _s(my_side).lower() in ("ct", "3", "counter-terrorist") else "T"
        for t in teams:
            if _s(t.get("start_side")).upper() == want:
                return t
    return max(teams, key=lambda t: _num(t.get("lost")))


# --------------------------------------------------------------------------- #
# the structured model
# --------------------------------------------------------------------------- #
def build_report(analytics, *, my_side=None, recurring=None, title=None,
                 map_name=None, score=None, date=None):
    """Build the single structured report model that every renderer consumes.

    Robust to {} / partial analytics; never raises. Returns a dict with these sections:
      meta, score_line, summary, scoreboard, top_fixes, what_went_well, key_rounds,
      economy, utility, position_notes.
    """
    if not isinstance(analytics, dict):
        analytics = {}

    meta = _resolve_meta(analytics, title=title, map_name=map_name, score=score, date=date)
    team = _pick_team(analytics, my_side)

    # coaching summary (deterministic heuristic; never the network AI path here)
    try:
        summary = coaching_summary.build_summary(analytics, my_side=my_side, recurring=recurring)
    except Exception:
        summary = {"text": "", "bullets": [], "review_rounds": [],
                   "utility_focus": [], "source": "heuristic", "ai": False}

    return {
        "meta": meta,
        "score_line": _score_line(analytics, meta),
        "focus_team": (_s(_d(team).get("name")) or None) if team else None,
        "summary": summary,
        "scoreboard": _build_scoreboard(analytics),
        "top_fixes": _top_fixes(analytics, team),
        "what_went_well": _what_went_well(analytics, team),
        "key_rounds": _key_rounds(analytics),
        "economy": _economy(analytics, team),
        "utility": _utility_totals(analytics, team),
        "position_notes": _position_notes(analytics),
    }


# --------------------------------------------------------------------------- #
# JSON
# --------------------------------------------------------------------------- #
def to_json(report):
    """Pretty JSON of the structured model."""
    return json.dumps(report if isinstance(report, dict) else {}, indent=2,
                      ensure_ascii=False, default=str, sort_keys=False)


# --------------------------------------------------------------------------- #
# TEXT  (sectioned, column-aligned scoreboard)
# --------------------------------------------------------------------------- #
def _rule(width=78, ch="="):
    return ch * width


def _section(title):
    return [_rule(), title.upper(), _rule()]


def _ascii_table(headers, rows):
    """Render a left/right-aligned monospace table. First column left-aligned, rest right."""
    cols = len(headers)
    widths = [len(h) for h in headers]
    for r in rows:
        for i in range(cols):
            widths[i] = max(widths[i], len(r[i]) if i < len(r) else 0)

    def line(cells):
        out = []
        for i in range(cols):
            c = cells[i] if i < len(cells) else ""
            out.append(c.ljust(widths[i]) if i == 0 else c.rjust(widths[i]))
        return "  ".join(out).rstrip()

    sep = "  ".join("-" * w for w in widths)
    return [line(headers), sep] + [line(r) for r in rows]


def to_text(report):
    """A rich, readable, sectioned plain-text report. Far more than the Discord blurb."""
    if not isinstance(report, dict):
        report = {}
    meta = _d(report.get("meta"))
    out = []

    # ---- header ----
    out.append(_rule())
    out.append(_s(meta.get("title"), "CS2 Match Report").center(78))
    out.append(_rule())
    out.append(_s(report.get("score_line")))
    hdr_bits = []
    if meta.get("map"):
        hdr_bits.append(f"Map: {meta['map']}")
    if meta.get("n_rounds"):
        hdr_bits.append(f"Rounds: {meta['n_rounds']}")
    if meta.get("date"):
        hdr_bits.append(f"Date: {meta['date']}")
    if hdr_bits:
        out.append("  |  ".join(hdr_bits))
    if not meta.get("have_econ"):
        out.append("(no economy data in this demo -- buy types are approximate)")
    out.append("")

    # ---- coaching summary ----
    summ = _d(report.get("summary"))
    if summ.get("text") or summ.get("bullets"):
        out += _section("Coaching Summary")
        if summ.get("text"):
            out.append(_s(summ["text"]))
        for b in _l(summ.get("bullets")):
            out.append(f"  - {b}")
        rr = [str(n) for n in _l(summ.get("review_rounds"))]
        if rr:
            out.append("Review first: " + ", ".join("R" + n for n in rr))
        uf = _l(summ.get("utility_focus"))
        if uf:
            out.append("Utility to learn: " + "; ".join(_s(u) for u in uf))
        out.append("")

    # ---- scoreboard ----
    sb = _l(report.get("scoreboard"))
    if sb:
        out += _section("Scoreboard")
        for g in sb:
            title = _s(g.get("name"), "Team")
            wl = ""
            if g.get("won") or g.get("lost"):
                wl = f"  ({int(_num(g.get('won')))}-{int(_num(g.get('lost')))})"
            side = f"  [started {g['start_side']}]" if g.get("start_side") else ""
            out.append(title + wl + side)
            rows = [[_s(r.get(k)) for k, _h, _f in _SCORE_COLS] for r in _l(g.get("rows"))]
            out += _ascii_table(_SCORE_HEADERS, rows)
            out.append("")

    # ---- top fixes ----
    fixes = _l(report.get("top_fixes"))
    if fixes:
        out += _section("Top Things To Fix")
        for i, f in enumerate(fixes, 1):
            line = f"{i}. {_s(f.get('focus'))}"
            if f.get("drill"):
                line += f" -- {_s(f['drill'])}"
            out.append(line)
            if f.get("rounds"):
                out.append("   watch: " + ", ".join("R" + str(r) for r in f["rounds"]))
        out.append("")

    # ---- what went well ----
    well = _l(report.get("what_went_well"))
    if well:
        out += _section("What Went Well")
        for w in well:
            who = f" ({_s(w.get('player'))})" if w.get("player") else ""
            out.append(f"  + {_s(w.get('label'))}{who}")
            if w.get("text"):
                out.append(f"      {_s(w['text'])}")
        out.append("")

    # ---- key rounds ----
    kr = _l(report.get("key_rounds"))
    if kr:
        out += _section("Key Rounds (biggest swings)")
        for c in kr:
            head = (f"R{c.get('round')}  {_s(c.get('winner'))} won  "
                    f"[{_s(c.get('buy_ct'))}/{_s(c.get('buy_t'))}]")
            if isinstance(c.get("impact"), (int, float)):
                head += f"  impact {c['impact']}"
            out.append(head)
            if c.get("summary"):
                out.append(f"   {_s(c['summary'])}")
        out.append("")

    # ---- economy ----
    econ = _l(report.get("economy"))
    if econ:
        out += _section("Economy / Buy Outcomes")
        rows = [[_s(e.get("buy")), str(int(_num(e.get("rounds")))),
                 (_fmt_num(e.get("win_pct"), 0) + "%") if isinstance(e.get("win_pct"), (int, float)) else "--"]
                for e in econ]
        out += _ascii_table(["Buy", "Rounds", "Win%"], rows)
        out.append("")

    # ---- utility ----
    util = _d(report.get("utility"))
    if util:
        out += _section("Utility")
        out.append(f"Smokes {int(_num(util.get('smokes')))}  |  "
                   f"Flashes {int(_num(util.get('flashes')))}  |  "
                   f"HE {int(_num(util.get('he')))}  |  "
                   f"Molotov {int(_num(util.get('molotov')))}")
        out.append(f"Util dmg/round (avg): {_fmt_num(util.get('udr_avg'), 1)}  |  "
                   f"Enemies flashed: {int(_num(util.get('enemy_flashed')))}  |  "
                   f"Team flashed: {int(_num(util.get('team_flashed')))}")
        out.append("")

    # ---- position notes ----
    pos = _l(report.get("position_notes"))
    if pos:
        out += _section("Position Notes (most-contested callouts)")
        for p in pos:
            zbits = []
            for z in _l(p.get("zones")):
                kd = _fmt_num(z.get("kd"), 2) if isinstance(z.get("kd"), (int, float)) else "--"
                zbits.append(f"{_s(z.get('zone'))} {int(_num(z.get('k')))}K/"
                             f"{int(_num(z.get('d')))}D (KD {kd})")
            out.append(f"  {_s(p.get('name'))}: " + "; ".join(zbits))
        out.append("")

    out.append(_rule())
    out.append("via VantageGG")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# HTML  (self-contained, print-friendly -- the "PDF" path)
# --------------------------------------------------------------------------- #
def _e(v):
    return html.escape(_s(v))


_HTML_STYLE = """
:root{
  --ink:#1c2230; --muted:#5b6577; --line:#d9dee8; --bg:#ffffff;
  --head:#10131a; --accent:#2f6df0; --good:#1a8a4a; --bad:#c0392b;
  --zebra:#f5f7fb; --chip:#eef2fb;
}
*{box-sizing:border-box;}
html,body{margin:0;padding:0;}
body{font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
  color:var(--ink); background:#e9edf3; padding:24px;}
.sheet{max-width:820px; margin:0 auto; background:var(--bg); color:var(--ink);
  padding:32px 36px; border:1px solid var(--line); border-radius:8px;
  box-shadow:0 2px 14px rgba(20,30,60,.08);}
h1{font-size:22px; margin:0 0 4px;}
h2{font-size:15px; text-transform:uppercase; letter-spacing:.04em;
  color:var(--muted); border-bottom:2px solid var(--line); padding-bottom:4px;
  margin:26px 0 12px;}
.scoreline{font-size:18px; font-weight:700; margin:6px 0;}
.meta{color:var(--muted); font-size:13px; margin-bottom:2px;}
.warn{color:var(--bad); font-size:12px; margin-top:4px;}
table{width:100%; border-collapse:collapse; font-size:13px; margin:8px 0 4px;}
th,td{padding:5px 8px; text-align:right; border-bottom:1px solid var(--line);
  white-space:nowrap;}
th:first-child,td:first-child{text-align:left;}
thead th{color:var(--muted); font-weight:600; border-bottom:2px solid var(--line);}
tbody tr:nth-child(even){background:var(--zebra);}
.teamhdr{font-weight:700; font-size:14px; margin:14px 0 2px;}
.teamhdr .wl{color:var(--accent);}
.teamhdr .side{color:var(--muted); font-weight:400; font-size:12px;}
ol.fixes{margin:6px 0; padding-left:20px;}
ol.fixes li{margin:6px 0;}
.drill{color:var(--muted);}
.rounds{color:var(--accent); font-size:12px;}
ul.well{list-style:none; margin:6px 0; padding:0;}
ul.well li{margin:5px 0;}
ul.well .lbl{font-weight:600; color:var(--good);}
ul.well .who{color:var(--muted);}
ul.well .sub{display:block; color:var(--muted); font-size:12px; margin-left:14px;}
.round{margin:6px 0; padding:6px 10px; background:var(--zebra); border-radius:6px;}
.round .rn{font-weight:700;}
.round .buy{color:var(--muted); font-size:12px;}
.round .sub{color:var(--ink); font-size:13px;}
.chips span{display:inline-block; background:var(--chip); border-radius:12px;
  padding:2px 10px; margin:3px 6px 3px 0; font-size:12px;}
.summary{background:var(--zebra); border-radius:8px; padding:12px 14px;}
.summary p{margin:0 0 8px;}
.summary ul{margin:6px 0; padding-left:18px;}
.foot{margin-top:26px; color:var(--muted); font-size:12px; text-align:center;}
@media print{
  body{background:#fff; padding:0;}
  .sheet{box-shadow:none; border:none; max-width:none; width:100%;
    padding:0 8mm; border-radius:0;}
  h2{page-break-after:avoid;}
  table,.round,ol.fixes li,ul.well li{page-break-inside:avoid;}
  @page{size:A4; margin:14mm;}
}
"""


def _html_scoreboard(report):
    sb = _l(report.get("scoreboard"))
    if not sb:
        return ""
    parts = ["<h2>Scoreboard</h2>"]
    for g in sb:
        wl = ""
        if g.get("won") or g.get("lost"):
            wl = f' <span class="wl">{int(_num(g.get("won")))}-{int(_num(g.get("lost")))}</span>'
        side = f' <span class="side">started {_e(g["start_side"])}</span>' if g.get("start_side") else ""
        parts.append(f'<div class="teamhdr">{_e(g.get("name"))}{wl}{side}</div>')
        parts.append("<table><thead><tr>"
                     + "".join(f"<th>{_e(h)}</th>" for h in _SCORE_HEADERS)
                     + "</tr></thead><tbody>")
        for r in _l(g.get("rows")):
            cells = "".join(f"<td>{_e(r.get(k))}</td>" for k, _h, _f in _SCORE_COLS)
            parts.append(f"<tr>{cells}</tr>")
        parts.append("</tbody></table>")
    return "\n".join(parts)


def _html_summary(report):
    s = _d(report.get("summary"))
    if not (s.get("text") or s.get("bullets")):
        return ""
    parts = ['<h2>Coaching Summary</h2>', '<div class="summary">']
    if s.get("text"):
        parts.append(f"<p>{_e(s['text'])}</p>")
    bullets = _l(s.get("bullets"))
    if bullets:
        parts.append("<ul>" + "".join(f"<li>{_e(b)}</li>" for b in bullets) + "</ul>")
    rr = _l(s.get("review_rounds"))
    if rr:
        parts.append('<div class="chips">Review first: '
                     + "".join(f"<span>R{_e(n)}</span>" for n in rr) + "</div>")
    uf = _l(s.get("utility_focus"))
    if uf:
        parts.append('<div class="meta">Utility to learn: ' + _e("; ".join(_s(u) for u in uf)) + "</div>")
    parts.append("</div>")
    return "\n".join(parts)


def _html_fixes(report):
    fixes = _l(report.get("top_fixes"))
    if not fixes:
        return ""
    parts = ["<h2>Top Things To Fix</h2>", '<ol class="fixes">']
    for f in fixes:
        drill = f' &mdash; <span class="drill">{_e(f["drill"])}</span>' if f.get("drill") else ""
        rounds = ""
        if f.get("rounds"):
            rounds = '<div class="rounds">watch ' + ", ".join("R" + _e(r) for r in f["rounds"]) + "</div>"
        parts.append(f"<li><b>{_e(f.get('focus'))}</b>{drill}{rounds}</li>")
    parts.append("</ol>")
    return "\n".join(parts)


def _html_well(report):
    well = _l(report.get("what_went_well"))
    if not well:
        return ""
    parts = ["<h2>What Went Well</h2>", '<ul class="well">']
    for w in well:
        who = f' <span class="who">({_e(w.get("player"))})</span>' if w.get("player") else ""
        sub = f'<span class="sub">{_e(w["text"])}</span>' if w.get("text") else ""
        parts.append(f'<li><span class="lbl">{_e(w.get("label"))}</span>{who}{sub}</li>')
    parts.append("</ul>")
    return "\n".join(parts)


def _html_key_rounds(report):
    kr = _l(report.get("key_rounds"))
    if not kr:
        return ""
    parts = ["<h2>Key Rounds (biggest swings)</h2>"]
    for c in kr:
        imp = f" &middot; impact {_e(c['impact'])}" if isinstance(c.get("impact"), (int, float)) else ""
        sub = f'<div class="sub">{_e(c["summary"])}</div>' if c.get("summary") else ""
        parts.append(
            f'<div class="round"><span class="rn">R{_e(c.get("round"))}</span> '
            f'{_e(c.get("winner"))} won '
            f'<span class="buy">[{_e(c.get("buy_ct"))}/{_e(c.get("buy_t"))}]{imp}</span>{sub}</div>')
    return "\n".join(parts)


def _html_economy(report):
    econ = _l(report.get("economy"))
    if not econ:
        return ""
    parts = ["<h2>Economy / Buy Outcomes</h2>",
             "<table><thead><tr><th>Buy</th><th>Rounds</th><th>Win%</th></tr></thead><tbody>"]
    for e in econ:
        wp = (_fmt_num(e.get("win_pct"), 0) + "%") if isinstance(e.get("win_pct"), (int, float)) else "--"
        parts.append(f"<tr><td>{_e(e.get('buy'))}</td>"
                     f"<td>{int(_num(e.get('rounds')))}</td><td>{_e(wp)}</td></tr>")
    parts.append("</tbody></table>")
    return "\n".join(parts)


def _html_utility(report):
    u = _d(report.get("utility"))
    if not u:
        return ""
    return ("<h2>Utility</h2>"
            '<div class="chips">'
            f"<span>Smokes {int(_num(u.get('smokes')))}</span>"
            f"<span>Flashes {int(_num(u.get('flashes')))}</span>"
            f"<span>HE {int(_num(u.get('he')))}</span>"
            f"<span>Molotov {int(_num(u.get('molotov')))}</span>"
            f"<span>Util dmg/rd {_fmt_num(u.get('udr_avg'), 1)}</span>"
            f"<span>Enemies flashed {int(_num(u.get('enemy_flashed')))}</span>"
            f"<span>Team flashed {int(_num(u.get('team_flashed')))}</span>"
            "</div>")


def _html_positions(report):
    pos = _l(report.get("position_notes"))
    if not pos:
        return ""
    parts = ["<h2>Position Notes (most-contested callouts)</h2>",
             "<table><thead><tr><th>Player</th><th>Top callouts (K/D)</th></tr></thead><tbody>"]
    for p in pos:
        zbits = []
        for z in _l(p.get("zones")):
            kd = _fmt_num(z.get("kd"), 2) if isinstance(z.get("kd"), (int, float)) else "--"
            zbits.append(f"{_e(z.get('zone'))} {int(_num(z.get('k')))}K/{int(_num(z.get('d')))}D (KD {kd})")
        parts.append(f"<tr><td>{_e(p.get('name'))}</td><td style='text-align:left'>"
                     + "; ".join(zbits) + "</td></tr>")
    parts.append("</tbody></table>")
    return "\n".join(parts)


def to_html(report):
    """A self-contained, print-friendly HTML document (open + print-to-PDF). No scripts/assets."""
    if not isinstance(report, dict):
        report = {}
    meta = _d(report.get("meta"))
    title = _e(meta.get("title") or "CS2 Match Report")

    meta_bits = []
    if meta.get("map"):
        meta_bits.append(f"Map: {_e(meta['map'])}")
    if meta.get("n_rounds"):
        meta_bits.append(f"Rounds: {_e(meta['n_rounds'])}")
    if meta.get("date"):
        meta_bits.append(f"Date: {_e(meta['date'])}")
    meta_html = (' &nbsp;|&nbsp; '.join(meta_bits)) if meta_bits else ""
    warn = "" if meta.get("have_econ") else \
        '<div class="warn">No economy data in this demo &mdash; buy types are approximate.</div>'

    body = "\n".join(filter(None, [
        _html_summary(report),
        _html_scoreboard(report),
        _html_fixes(report),
        _html_well(report),
        _html_key_rounds(report),
        _html_economy(report),
        _html_utility(report),
        _html_positions(report),
    ]))

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{title}</title>\n"
        f"<style>{_HTML_STYLE}</style>\n"
        "</head><body>\n"
        '<div class="sheet">\n'
        f"<h1>{title}</h1>\n"
        f'<div class="scoreline">{_e(report.get("score_line"))}</div>\n'
        + (f'<div class="meta">{meta_html}</div>\n' if meta_html else "")
        + warn + "\n"
        + body + "\n"
        '<div class="foot">Generated by VantageGG &middot; transparent, directional analytics</div>\n'
        "</div>\n</body></html>"
    )


# --------------------------------------------------------------------------- #
# dispatch
# --------------------------------------------------------------------------- #
_RENDERERS = {"text": to_text, "txt": to_text, "json": to_json,
              "html": to_html, "pdf": to_html}


def render(analytics, fmt="text", **kw):
    """Build the report from `analytics` and render it in `fmt` (text|json|html). Unknown
    formats fall back to text. Extra kwargs flow to build_report (my_side, recurring, title,
    map_name, score, date)."""
    report = build_report(analytics, **kw)
    return _RENDERERS.get(_s(fmt).lower(), to_text)(report)
