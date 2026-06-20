"""Tests for export_report.py -- the rich match-report exporter (text / json / html).

Covers: build_report assembles every section from a realistic synthetic analytics dict;
to_text contains the score, player names and section headers; to_json round-trips;
to_html is a full <html> doc with the scoreboard and NO <script>; and everything is robust
to {} / partial / garbage input (never throws). No network, no DB.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import export_report as er   # noqa: E402


# --- a realistic synthetic analytics dict (shapes per analytics.py analyze()) ---
def _analytics():
    return {
        "version": 7,
        "tickrate": 64,
        "n_rounds": 24,
        "have_econ": True,
        "players": [
            {"steamid": "111", "name": "Alice", "kills": 18, "deaths": 20, "assists": 4,
             "kd": 0.9, "adr": 62.0, "kast": 58.0, "hltv": 0.92, "impact": 0.8,
             "kpr": 0.75, "dpr": 0.83, "hs_pct": 41.0, "open_k": 3, "open_d": 6,
             "open_wr": 33.0, "trade_k": 4, "traded_d": 5, "traded_pct": 25.0, "udr": 3.0,
             "multi": {"2": 2, "3": 1}, "enemy_flashed": 6, "team_flashed": 2,
             "smokes": 9, "flashes_thrown": 7, "hes": 4, "molotovs": 3, "util_pr": 0.96,
             "ct_role": "Support", "t_role": "Lurker",
             "clutch": {"won": 1, "lost": 2, "attempts": 3},
             "position_stats": [
                 {"zone": "Mid", "k": 4, "d": 7, "kd": 0.57, "ct_k": 2, "ct_d": 4,
                  "t_k": 2, "t_d": 3, "open_k": 1, "open_d": 3},
                 {"zone": "B Site", "k": 3, "d": 2, "kd": 1.5, "ct_k": 2, "ct_d": 1,
                  "t_k": 1, "t_d": 1, "open_k": 0, "open_d": 0},
             ],
             "focus": [
                 {"area": "KAST", "value": 58.0, "benchmark": 70, "unit": "%", "severity": 3,
                  "detail": "KAST 58% vs ~70% target", "fix": "Trade/refrag drills."},
             ]},
            {"steamid": "222", "name": "Bob", "kills": 22, "deaths": 17, "assists": 6,
             "kd": 1.29, "adr": 84.0, "kast": 74.0, "hltv": 1.18, "impact": 1.2,
             "kpr": 0.92, "dpr": 0.71, "hs_pct": 55.0, "open_k": 7, "open_d": 5,
             "open_wr": 58.0, "trade_k": 6, "traded_d": 4, "traded_pct": 24.0, "udr": 9.0,
             "multi": {"2": 3}, "enemy_flashed": 10, "team_flashed": 1,
             "smokes": 11, "flashes_thrown": 8, "hes": 5, "molotovs": 4, "util_pr": 1.16,
             "ct_role": "Entry", "t_role": "Entry",
             "clutch": {"won": 2, "lost": 1, "attempts": 3},
             "position_stats": [
                 {"zone": "A Main", "k": 8, "d": 3, "kd": 2.67, "ct_k": 3, "ct_d": 1,
                  "t_k": 5, "t_d": 2, "open_k": 4, "open_d": 1},
             ]},
            {"steamid": "333", "name": "Carl", "kills": 15, "deaths": 19, "assists": 5,
             "kd": 0.79, "adr": 70.0, "kast": 66.0, "hltv": 1.0, "impact": 0.9,
             "kpr": 0.62, "dpr": 0.79, "hs_pct": 48.0, "open_k": 5, "open_d": 4,
             "open_wr": 55.6, "trade_k": 3, "traded_d": 6, "traded_pct": 31.0, "udr": 6.0,
             "multi": {"2": 1}, "enemy_flashed": 7, "team_flashed": 0,
             "smokes": 8, "flashes_thrown": 6, "hes": 3, "molotovs": 2, "util_pr": 0.79,
             "ct_role": "AWP", "t_role": "Lurker",
             "position_stats": [
                 {"zone": "B Site", "k": 6, "d": 6, "kd": 1.0, "ct_k": 4, "ct_d": 4,
                  "t_k": 2, "t_d": 2, "open_k": 2, "open_d": 1},
             ]},
            {"steamid": "444", "name": "Dave", "kills": 20, "deaths": 16, "assists": 7,
             "kd": 1.25, "adr": 81.0, "kast": 72.0, "hltv": 1.12, "impact": 1.1,
             "kpr": 0.83, "dpr": 0.67, "hs_pct": 52.0, "open_k": 6, "open_d": 5,
             "open_wr": 54.5, "trade_k": 5, "traded_d": 5, "traded_pct": 31.0, "udr": 8.0,
             "multi": {"2": 2, "4": 1}, "enemy_flashed": 9, "team_flashed": 1,
             "smokes": 10, "flashes_thrown": 7, "hes": 4, "molotovs": 3, "util_pr": 1.0,
             "ct_role": "Anchor", "t_role": "Support", "position_stats": []},
        ],
        "rounds": [
            {"num": n, "winner": ("ct" if n % 2 else "t"), "reason": "elimination",
             "buy_ct": "full", "buy_t": "full", "equip_ct": 4200, "equip_t": 3900,
             "impact": float(100 - n * 3), "pistol": n in (1, 13)} for n in range(1, 25)
        ],
        "round_cards": [
            {"round": n, "winner": ("ct" if n % 2 else "t"), "reason": "elimination",
             "buy_ct": "full", "buy_t": "full",
             "summary": f"Round {n}: someone took first blood; bomb planted.",
             "watch_t": 12.0 + n, "moments": []} for n in range(1, 25)
        ],
        "team": {
            "top_areas": [
                {"area": "KAST", "players": 3},
                {"area": "Traded death%", "players": 2},
                {"area": "Util dmg/rd", "players": 2},
            ],
            "practice_plan": [
                {"focus": "KAST", "players": 3, "drill": "Trade/refrag drills together."},
            ],
            "buy_outcomes": {"full": {"rounds": 18, "win_pct": 44.4},
                             "eco": {"rounds": 4, "win_pct": 25.0},
                             "pistol": {"rounds": 2, "win_pct": 50.0}},
        },
        "team_coaching": {
            "teams": [
                {"id": "A", "start_side": "CT", "name": "Alice's team",
                 "players": ["Alice", "Bob"], "won": 11, "lost": 13, "trade_pct": 24.0,
                 "entry": {"attempts": 14, "won": 6, "wr": 42.9},
                 "post_plant": {"n": 5, "wr": 40.0}, "retake": {"n": 6, "wr": 33.3},
                 "loss_reasons": [
                     {"reason": "Opening death, no trade", "count": 5, "rounds": [4, 7, 9, 15, 18]},
                     {"reason": "Lost the post-plant", "count": 3, "rounds": [11, 14, 20]},
                 ],
                 "economy": {"full": {"rounds": 18, "win_pct": 44.4},
                             "eco": {"rounds": 4, "win_pct": 25.0},
                             "pistol": {"rounds": 2, "win_pct": 50.0}},
                 "top_death_zones": [{"zone": "Mid", "side": "CT", "deaths": 8}],
                 "roles": [], "practice_plan": [
                     {"focus": "Opening death, no trade", "rounds": [4, 7, 9, 15, 18],
                      "drill": "Entry + trade pairs -- never let first contact go unsupported."},
                     {"focus": "Lost the post-plant", "rounds": [11, 14, 20],
                      "drill": "Default post-plants: crossfires on the bomb."},
                 ]},
                {"id": "B", "start_side": "T", "name": "Carl's team",
                 "players": ["Carl", "Dave"], "won": 13, "lost": 11, "trade_pct": 31.0,
                 "entry": {"attempts": 14, "won": 8, "wr": 57.1},
                 "post_plant": {"n": 6, "wr": 66.7}, "retake": {"n": 5, "wr": 60.0},
                 "loss_reasons": [
                     {"reason": "Lost the gunfights", "count": 4, "rounds": [1, 3, 5, 21]},
                 ],
                 "economy": {"full": {"rounds": 18, "win_pct": 55.6}},
                 "top_death_zones": [{"zone": "B", "side": "T", "deaths": 6}],
                 "roles": [], "practice_plan": []},
            ]
        },
        "team_play": {},
        "insights": {
            "111": [
                {"round": 4, "tick": 5000, "type": "untraded_opening_death", "severity": 3,
                 "polarity": "issue", "text": "R4: you took the opening death and weren't traded."},
            ],
            "222": [
                {"round": None, "tick": None, "type": "good_openings", "severity": 0,
                 "polarity": "good", "text": "Strong entrying: 7-5 opening duels (58% win)."},
                {"round": None, "tick": None, "type": "high_impact", "severity": 0,
                 "polarity": "good", "text": "Consistent impact: 74% KAST, 1.18 rating."},
            ],
            "333": [
                {"round": None, "tick": None, "type": "good_spacing", "severity": 0,
                 "polarity": "good", "text": "Great spacing: 31% of your deaths got traded."},
            ],
        },
        "benchmarks": {"kast": 70, "adr": 80},
        "meta": {"analytics_version": 7,
                 "note": "Ratings are transparent approximations -- treat as directional."},
    }


def _recurring():
    return {
        "player": "111", "matches": 5,
        "recurring": [
            {"type": "untraded_opening_death", "label": "Untraded opening deaths",
             "matches_present": 4, "matches_total": 5, "total": 12, "recent": 3,
             "series": [3, 2, 3, 4, 0], "trend": "steady", "suggested_target": 2.0},
        ],
    }


# --- build_report: full structured model --------------------------------------
def test_build_report_has_all_sections():
    r = er.build_report(_analytics(), my_side="CT", title="My Match",
                        map_name="de_dust2", date="2026-06-20")
    for key in ("meta", "score_line", "summary", "scoreboard", "top_fixes",
                "what_went_well", "key_rounds", "economy", "utility", "position_notes"):
        assert key in r, f"missing section: {key}"
    # meta carries the passed-through match info
    assert r["meta"]["map"] == "de_dust2"
    assert r["meta"]["date"] == "2026-06-20"
    assert r["meta"]["title"] == "My Match"
    assert r["meta"]["n_rounds"] == 24
    assert r["meta"]["have_econ"] is True


def test_scoreboard_groups_both_teams_and_all_players():
    r = er.build_report(_analytics())
    sb = r["scoreboard"]
    assert isinstance(sb, list) and len(sb) >= 2
    # every one of the 4 players must appear exactly once across the groups
    names = [row["name"] for g in sb for row in g["rows"]]
    assert sorted(names) == ["Alice", "Bob", "Carl", "Dave"]
    # rows carry the expected display fields (K-A-D, ADR, etc.)
    a_row = next(row for g in sb for row in g["rows"] if row["name"] == "Alice")
    assert a_row["kad"] == "18-4-20"
    assert a_row["open"] == "3-6"
    # util thrown total = 9+7+4+3 = 23
    assert a_row["util"] == "23"


def test_top_fixes_and_well_and_key_rounds_populated():
    r = er.build_report(_analytics(), my_side="CT")
    assert r["top_fixes"], "expected practice-plan fixes for the CT team"
    assert r["top_fixes"][0]["focus"] == "Opening death, no trade"
    assert r["what_went_well"], "expected positive insights"
    # key rounds sorted by impact desc -> R1 (impact 97) is the most decisive
    assert r["key_rounds"]
    assert r["key_rounds"][0]["round"] == 1
    impacts = [c["impact"] for c in r["key_rounds"]]
    assert impacts == sorted(impacts, reverse=True)


def test_economy_and_utility_and_positions():
    r = er.build_report(_analytics(), my_side="CT")
    buys = {e["buy"] for e in r["economy"]}
    assert {"full", "eco", "pistol"} <= buys
    util = r["utility"]
    assert util["smokes"] == 20  # Alice 9 + Bob 11 (CT team only)
    assert util["udr_avg"] > 0
    pos = r["position_notes"]
    assert any(p["name"] == "Alice" for p in pos)
    alice_pos = next(p for p in pos if p["name"] == "Alice")
    assert alice_pos["zones"][0]["zone"] == "Mid"


def test_recurring_flows_into_summary():
    r = er.build_report(_analytics(), my_side="CT", recurring=_recurring())
    txt = (r["summary"].get("text") or "").lower()
    assert "recurring" in txt or "untraded opening" in txt


# --- to_text ------------------------------------------------------------------
def test_to_text_contains_score_names_and_headers():
    txt = er.to_text(er.build_report(_analytics(), my_side="CT", map_name="de_dust2"))
    assert isinstance(txt, str) and len(txt) > 400
    # score line: team names + scores
    assert "11" in txt and "13" in txt
    assert "Alice's team" in txt
    # all player names present
    for nm in ("Alice", "Bob", "Carl", "Dave"):
        assert nm in txt
    # section headers (upper-cased in the renderer)
    for hdr in ("SCOREBOARD", "COACHING SUMMARY", "TOP THINGS TO FIX",
                "KEY ROUNDS", "ECONOMY", "UTILITY"):
        assert hdr in txt, f"missing section header: {hdr}"
    # the map shows up
    assert "de_dust2" in txt
    # column header row of the scoreboard
    assert "ADR" in txt and "KAST" in txt and "HLTV" in txt


def test_to_text_far_richer_than_blurb():
    # sanity: the text export is substantially longer than a Discord blurb
    txt = er.to_text(er.build_report(_analytics(), my_side="CT"))
    assert txt.count("\n") > 30


# --- to_json ------------------------------------------------------------------
def test_to_json_round_trips():
    report = er.build_report(_analytics(), my_side="CT")
    s = er.to_json(report)
    assert isinstance(s, str)
    back = json.loads(s)            # must be valid JSON
    assert isinstance(back, dict)
    assert "scoreboard" in back and "meta" in back
    assert back["meta"]["n_rounds"] == 24


# --- to_html ------------------------------------------------------------------
def test_to_html_is_full_doc_with_scoreboard_and_no_script():
    html = er.to_html(er.build_report(_analytics(), my_side="CT",
                                      title="VantageGG Report", map_name="de_dust2"))
    assert isinstance(html, str)
    low = html.lower()
    assert low.startswith("<!doctype html>")
    assert "<html" in low and "</html>" in low
    assert "<style" in low                       # self-contained styling
    assert "<script" not in low                  # no scripts at all
    # scoreboard table + every player name rendered
    assert "<table" in low and "scoreboard" in low
    for nm in ("Alice", "Bob", "Carl", "Dave"):
        assert nm in html
    # print rules for the PDF path
    assert "@media print" in low and "@page" in low
    assert "VantageGG Report" in html


def test_to_html_escapes_user_content():
    a = _analytics()
    a["players"][0]["name"] = "<script>x</script>Eve"
    html = er.to_html(er.build_report(a))
    # the raw tag must be escaped, never emitted literally
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html


# --- render dispatch ----------------------------------------------------------
def test_render_dispatch():
    a = _analytics()
    assert er.render(a, "json").lstrip().startswith("{")
    assert er.render(a, "html").lower().startswith("<!doctype")
    assert "SCOREBOARD" in er.render(a, "text")
    # unknown format falls back to text
    assert "SCOREBOARD" in er.render(a, "bogus")


# --- robustness: must NEVER throw on empty / partial / garbage ----------------
def test_empty_dict_never_throws():
    r = er.build_report({})
    assert isinstance(r, dict)
    for key in ("meta", "scoreboard", "summary", "key_rounds", "economy", "utility"):
        assert key in r
    # all renderers survive an empty model
    assert isinstance(er.to_text(r), str) and er.to_text(r)
    json.loads(er.to_json(r))                     # still valid JSON
    h = er.to_html(r)
    assert h.lower().startswith("<!doctype") and "<script" not in h.lower()


def test_none_and_non_dict_input():
    for bad in (None, [], "nope", 42, 3.14, True):
        r = er.build_report(bad)
        assert isinstance(r, dict) and "scoreboard" in r
        assert isinstance(er.to_text(r), str)
        json.loads(er.to_json(r))
        assert er.to_html(r).lower().startswith("<!doctype")


def test_renderers_tolerate_non_dict_report():
    for bad in (None, [], "x", 7):
        assert isinstance(er.to_text(bad), str)
        assert isinstance(er.to_json(bad), str)
        assert er.to_html(bad).lower().startswith("<!doctype")


def test_partial_no_econ_no_teams():
    partial = {"n_rounds": 16, "have_econ": False, "players": [
        {"steamid": "1", "name": "Solo", "kills": 10, "deaths": 12, "assists": 2,
         "adr": 60.0, "kast": 60.0, "hltv": 0.95, "kd": 0.83}],
        "team_coaching": {"teams": []}, "team": {}, "rounds": [], "round_cards": []}
    r = er.build_report(partial)
    # player with no team still shows up on the scoreboard
    assert any(row["name"] == "Solo" for g in r["scoreboard"] for row in g["rows"])
    txt = er.to_text(r)
    assert "Solo" in txt
    assert r["meta"]["have_econ"] is False
    # the no-econ warning surfaces in text + html
    assert "approximate" in txt.lower()
    assert "approximate" in er.to_html(r).lower()


def test_garbage_nested_types_never_throw():
    a = {"n_rounds": "x", "players": "nope", "team": [],
         "team_coaching": {"teams": "bad"}, "rounds": {}, "round_cards": None,
         "insights": "nah", "meta": []}
    r = er.build_report(a)
    assert isinstance(r, dict)
    assert isinstance(er.to_text(r), str)
    json.loads(er.to_json(r))
    assert er.to_html(r).lower().startswith("<!doctype")


def test_players_present_but_team_rosters_empty():
    # players exist but team_coaching lists no member names -> they land in a leftover group
    a = _analytics()
    for t in a["team_coaching"]["teams"]:
        t["players"] = []
    r = er.build_report(a)
    names = [row["name"] for g in r["scoreboard"] for row in g["rows"]]
    assert sorted(names) == ["Alice", "Bob", "Carl", "Dave"]
