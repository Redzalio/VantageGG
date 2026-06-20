"""Tests for goals.py (Practice Goals: schema, metric values, cross-match grading, CRUD)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import goals   # noqa: E402


def _match(mp="de_x", players=None, insights=None, teams=None):
    return {"key": "k", "sha": "s", "map": mp, "created_at": "",
            "analytics": {"players": players or [], "insights": insights or {},
                          "team_coaching": {"teams": teams or []}}}


# ---- metric registry + values ----------------------------------------------
def test_metric_registry():
    assert goals.metric_by_key("adr")["better"] == "high"
    assert goals.metric_by_key("untraded_opening_death")["better"] == "low"
    assert goals.metric_by_key("nope") is None


def test_metric_value_player_scoped_and_avg():
    a = _match(players=[{"steamid": "S1", "adr": 90}, {"steamid": "S2", "adr": 70}])["analytics"]
    m = goals.metric_by_key("adr")
    assert goals.metric_value(m, a, {"player": "S1"}) == 90
    assert goals.metric_value(m, a, {}) == 80      # team avg
    assert goals.metric_value(m, a, {"player": "ZZ"}) is None


def test_metric_value_insight_counts():
    ins = {"S1": [{"type": "untraded_opening_death"}, {"type": "untraded_opening_death"}, {"type": "pos"}],
           "S2": [{"type": "untraded_opening_death"}]}
    a = _match(insights=ins)["analytics"]
    m = goals.metric_by_key("untraded_opening_death")
    assert goals.metric_value(m, a, {"player": "S1"}) == 2.0
    assert goals.metric_value(m, a, {}) == 3.0      # team total


def test_metric_value_team_path_and_player_team():
    teams = [{"id": "A", "players": [{"steamid": "S1"}], "retake": {"wr": 40.0}, "trade_pct": 16.5},
             {"id": "B", "players": ["S2"], "retake": {"wr": 60.0}, "trade_pct": 12.0}]
    a = _match(teams=teams)["analytics"]
    assert goals.metric_value(goals.metric_by_key("team_retake_wr"), a, {"player": "S2"}) == 60.0
    assert goals.metric_value(goals.metric_by_key("team_trade_pct"), a, {"player": "S1"}) == 16.5


# ---- side / buy / role scoping ---------------------------------------------
def test_metric_value_side_scoped():
    p = {"steamid": "S1", "adr": 88, "kast": 70,
         "sides": {"ct": {"rounds": 12, "k": 14, "adr": 110, "kast": 80},
                   "t": {"rounds": 12, "k": 6, "adr": 60, "kast": 60}}}
    a = _match(players=[p])["analytics"]
    adr = goals.metric_by_key("adr")
    assert goals.metric_value(adr, a, {"player": "S1", "side": "t"}) == 60
    assert goals.metric_value(adr, a, {"player": "S1", "side": "ct"}) == 110
    assert goals.metric_value(adr, a, {"player": "S1"}) == 88          # no side -> overall
    # kpr derived from a side's k/rounds (sides carry no kpr field)
    assert goals.metric_value(goals.metric_by_key("kpr"), a, {"player": "S1", "side": "ct"}) == round(14 / 12, 2)


def test_metric_value_buy_scoped():
    p = {"steamid": "S1", "kpr": 0.7,
         "buys": {"eco": {"rounds": 2, "k": 4, "kpr": 2.0, "win_pct": 50.0},
                  "full": {"rounds": 15, "k": 8, "kpr": 0.53, "win_pct": 60.0}}}
    a = _match(players=[p])["analytics"]
    assert goals.metric_value(goals.metric_by_key("win_pct"), a, {"player": "S1", "buy": "eco"}) == 50.0
    assert goals.metric_value(goals.metric_by_key("kpr"), a, {"player": "S1", "buy": "full"}) == 0.53
    assert goals.metric_value(goals.metric_by_key("win_pct"), a, {"player": "S1"}) is None   # no overall win%


def test_scope_unsupported_falls_back_to_overall():
    p = {"steamid": "S1", "hltv": 1.2, "sides": {"t": {"adr": 60}}}
    a = _match(players=[p])["analytics"]
    # hltv has no side breakdown -> side scope ignored, overall returned
    assert goals.metric_value(goals.metric_by_key("hltv"), a, {"player": "S1", "side": "t"}) == 1.2


def test_grade_role_filter():
    mk = lambda role, adr: _match(players=[{"steamid": "S1", "adr": adr, "t_role": role, "ct_role": "Rifler"}])
    ms = [mk("Entry", 90), mk("Lurker", 50), mk("Entry", 80)]    # newest-first; only 2 are Entry
    g = {"metric": "adr", "target": 85, "scope": {"player": "S1", "role": "Entry"}, "baseline": None}
    grade = goals.grade(g, ms)
    assert grade["samples"] == 2 and grade["current"] == 90
    # role matches on EITHER side (ct_role Entry counts too)
    ms2 = [_match(players=[{"steamid": "S1", "adr": 70, "ct_role": "Entry", "t_role": "Rifler"}])]
    assert goals.grade({"metric": "adr", "target": 85, "scope": {"player": "S1", "role": "Entry"}}, ms2)["samples"] == 1


def test_registry_new_metrics():
    assert goals.metric_by_key("kpr")["scopes"] == ["side", "buy"]
    assert goals.metric_by_key("win_pct").get("requires") == "buy"
    assert goals.metric_by_key("adr")["scopes"] == ["side"]
    assert not goals.metric_by_key("hltv").get("scopes")     # hltv has no value-level scopes


# ---- group scope: squad / team (average over YOUR players, not the whole-match 10) ----
def test_metric_value_group_player_avg():
    a = _match(players=[{"steamid": "S1", "adr": 90}, {"steamid": "S2", "adr": 70},
                        {"steamid": "ENEMY", "adr": 200}])["analytics"]
    m = goals.metric_by_key("adr")
    assert goals.metric_value(m, a, {"group": "squad", "members": ["S1", "S2"]}) == 80   # your players only, no ENEMY
    assert goals.metric_value(m, a, {"group": "team", "members": ["S1", "S2"]}) == 80    # team group grades the same
    assert goals.metric_value(m, a, {"group": "squad", "members": ["S1", "GHOST"]}) == 90  # only the present member
    assert goals.metric_value(m, a, {"group": "squad", "members": ["X", "Y"]}) is None     # none played -> skip


def test_metric_value_group_insight_sum():
    players = [{"steamid": "S1"}, {"steamid": "S2"}, {"steamid": "S3"}]
    ins = {"S1": [{"type": "pos"}, {"type": "pos"}], "S2": [{"type": "pos"}], "ENEMY": [{"type": "pos"}] * 5}
    a = _match(players=players, insights=ins)["analytics"]
    m = goals.metric_by_key("pos")
    assert goals.metric_value(m, a, {"group": "team", "members": ["S1", "S2", "S3"]}) == 3.0  # 2+1+0, no ENEMY
    assert goals.metric_value(m, a, {"group": "squad", "members": ["S3"]}) == 0.0             # played, 0 occurrences
    assert goals.metric_value(m, a, {"group": "squad", "members": ["GHOST"]}) is None         # didn't play


def test_normalize_group_scope():
    g = goals.normalize({"metric": "adr", "target": 80, "scope": {
        "group": "team", "members": ["A", "B", "C"], "label": "Team: Smoke Criminals",
        "player": "A", "role": "Entry", "map": "de_x"}})
    assert g["scope"]["group"] == "team" and g["scope"]["members"] == ["A", "B", "C"]
    assert g["scope"]["label"] == "Team: Smoke Criminals"
    assert "player" not in g["scope"] and "role" not in g["scope"]   # a group isn't a single player
    assert g["scope"]["map"] == "de_x"                               # other scope keys survive
    big = goals.normalize({"metric": "adr", "target": 1, "scope": {"group": "squad", "members": [str(i) for i in range(10)]}})
    assert len(big["scope"]["members"]) == 6                         # capped
    for bad in ({"group": "squad", "members": []}, {"group": "team", "members": "S1"}, {"group": "squad"}):
        s = goals.normalize({"metric": "adr", "target": 1, "scope": bad})["scope"]
        assert "group" not in s and "members" not in s              # invalid member list -> falls back to whole-match


def test_grade_group_skips_matches_without_your_players():
    ms = [_match(players=[{"steamid": "S1", "adr": 90}, {"steamid": "S2", "adr": 70}]),   # your avg 80
          _match(players=[{"steamid": "S1", "adr": 60}]),                                 # avg 60 (S2 absent)
          _match(players=[{"steamid": "ENEMY", "adr": 99}])]                              # none of you -> skipped
    g = {"metric": "adr", "target": 75, "scope": {"group": "team", "members": ["S1", "S2"]}, "baseline": 60}
    grade = goals.grade(g, ms)
    assert grade["samples"] == 2 and grade["current"] == 80


def test_grade_member_breakdown():
    """A team/squad goal also reports each member's own current value + trend, best-first."""
    def mk(a1, a2, a3):
        return _match(players=[{"steamid": "S1", "name": "Red", "adr": a1},
                               {"steamid": "S2", "name": "Vid", "adr": a2},
                               {"steamid": "S3", "name": "Bun", "adr": a3}])
    ms = [mk(75, 80, 90), mk(70, 78, 92), mk(72, 82, 88)]   # newest-first
    g = {"metric": "adr", "target": 85, "scope": {"group": "team", "members": ["S1", "S2", "S3"]}, "baseline": 80}
    res = goals.grade(g, ms)
    assert [x["name"] for x in res["members"]] == ["Bun", "Vid", "Red"]   # sorted best-first (higher better)
    mem = {x["name"]: x for x in res["members"]}
    assert mem["Bun"]["current"] == 90 and mem["Bun"]["meets"] is True    # recent avg 90 >= 85
    assert mem["Red"]["current"] == 75 and mem["Red"]["meets"] is False   # recent avg ~72 < 85
    assert mem["Vid"]["meets"] is False and mem["Vid"]["samples"] == 3
    # a non-group (single player) goal has no member breakdown
    assert "members" not in goals.grade({"metric": "adr", "target": 85, "scope": {"player": "S1"}}, ms)


def test_normalize_ownership():
    g = goals.normalize({"metric": "adr", "target": 80, "owner_user_id": "5", "team_id": "7"})
    assert g["owner_user_id"] == 5 and g["team_id"] == 7        # coerced to int
    g2 = goals.normalize({"metric": "adr", "target": 80})
    assert g2["owner_user_id"] is None and g2["team_id"] is None
    assert goals.normalize({"metric": "adr", "target": 80, "team_id": "abc"})["team_id"] is None   # junk -> None


def test_is_visible():
    assert goals.is_visible({"owner_user_id": None}, 5, {1, 2})              # legacy/local -> everyone
    assert goals.is_visible({"owner_user_id": 5}, 5, set())                  # your own personal goal
    assert not goals.is_visible({"owner_user_id": 9}, 5, set())              # someone else's personal goal
    assert goals.is_visible({"owner_user_id": 9, "team_id": 2}, 5, {2})      # shared with a team you're in
    assert not goals.is_visible({"owner_user_id": 9, "team_id": 7}, 5, {2})  # shared with a team you're NOT in


# ---- grading verdicts ------------------------------------------------------
def _adr_goal(target=85, scope=None, baseline=None):
    return {"metric": "adr", "target": target, "scope": scope or {"player": "S1"}, "baseline": baseline}


def _adr_matches(values):   # newest-first
    return [_match(players=[{"steamid": "S1", "adr": v}]) for v in values]


def test_grade_fixed():
    g = grade = goals.grade(_adr_goal(85), _adr_matches([90, 88, 86, 70, 60]))
    assert grade["verdict"] == "fixed" and grade["current"] == 90 and grade["samples"] == 5


def test_grade_improving():
    grade = goals.grade(_adr_goal(85, baseline=55), _adr_matches([80, 78, 76, 60, 55]))
    assert grade["verdict"] == "improving"


def test_grade_still_happening():
    grade = goals.grade(_adr_goal(85, baseline=62), _adr_matches([56, 58, 55, 60, 62]))
    assert grade["verdict"] == "still_happening"


def test_grade_insufficient_sample():
    grade = goals.grade(_adr_goal(85), _adr_matches([90, 88]))
    assert grade["verdict"] == "insufficient" and grade["samples"] == 2


def test_grade_no_data():
    grade = goals.grade(_adr_goal(85, scope={"player": "GHOST"}), _adr_matches([90, 88, 86]))
    assert grade["verdict"] == "no_data"


def test_grade_map_scope_filters_matches():
    ms = [_match("de_a", [{"steamid": "S1", "adr": 90}]),
          _match("de_b", [{"steamid": "S1", "adr": 50}]),
          _match("de_a", [{"steamid": "S1", "adr": 88}])]
    grade = goals.grade(_adr_goal(85, scope={"player": "S1", "map": "de_a"}), ms)
    assert grade["samples"] == 2 and grade["current"] == 90    # only de_a matches counted


def test_grade_rolling_windows():
    # 6 matches newest-first: 80,82,84,60,55,50  (target 85, higher better)
    grade = goals.grade(_adr_goal(85, baseline=50), _adr_matches([80, 82, 84, 60, 55, 50]))
    w = grade["windows"]
    assert w["3"]["avg"] == 82.0 and w["3"]["meets"] is False     # (80+82+84)/3
    assert w["5"]["avg"] == 72.2                                   # (80+82+84+60+55)/5
    assert w["10"] is None                                         # only 6 matches -> no 10-window
    # a 2-match goal has no 3-window
    assert goals.grade(_adr_goal(85), _adr_matches([90, 88]))["windows"]["3"] is None


def test_grade_low_metric_fixed():
    g = {"metric": "untraded_opening_death", "target": 5, "scope": {}, "baseline": 12}
    ms = [_match(insights={"S1": [{"type": "untraded_opening_death"}] * n}) for n in (3, 4, 2, 10, 12)]
    assert goals.grade(g, ms)["verdict"] == "fixed"   # recent [3,4,2] all <= 5


# ---- callout-scoped (location) grading (#62 position_stats) -----------------
def _pmatch(mp="de_x", position_stats=None, steamid="S1", extra_players=None):
    """A match whose player S1 carries position_stats (per-callout K/D rows)."""
    p = {"steamid": steamid, "name": "Red", "adr": 80, "position_stats": position_stats or []}
    return _match(mp, players=[p] + (extra_players or []))


def _ps(zone, k=0, d=0, ct_k=0, ct_d=0, t_k=0, t_d=0, open_k=0, open_d=0):
    return {"zone": zone, "k": k or (ct_k + t_k), "d": d or (ct_d + t_d),
            "kd": round((k or ct_k + t_k) / (d or ct_d + t_d), 2) if (d or ct_d + t_d) else float(k or ct_k + t_k),
            "ct_k": ct_k, "ct_d": ct_d, "t_k": t_k, "t_d": t_d, "open_k": open_k, "open_d": open_d}


def test_callout_supported_map():
    assert goals.callout_supported("pos") and goals.callout_supported("open_wr")
    assert goals.callout_supported("untraded_opening_death") and goals.callout_supported("kd")
    assert not goals.callout_supported("adr")    # ratio stat -> no per-callout numerator
    assert not goals.callout_supported("kast") and not goals.callout_supported("team_retake_wr")


def test_callout_value_deaths_and_open_and_kd():
    a = _pmatch(position_stats=[_ps("BombsiteB", ct_k=4, ct_d=1, t_k=1, t_d=3, open_k=1, open_d=2),
                               _ps("Connector", ct_k=0, ct_d=2, t_k=2, t_d=0)])["analytics"]
    pos = goals.metric_by_key("pos")              # deaths at the callout
    assert goals.callout_value(pos, a, {"player": "S1", "callout": "BombsiteB"}) == 4.0   # 1+3 deaths
    assert goals.callout_value(pos, a, {"player": "S1", "callout": "Connector"}) == 2.0
    uod = goals.metric_by_key("untraded_opening_death")   # opening deaths at the callout
    assert goals.callout_value(uod, a, {"player": "S1", "callout": "BombsiteB"}) == 2.0
    owr = goals.metric_by_key("open_wr")          # opening win% = open_k/(open_k+open_d)*100
    assert goals.callout_value(owr, a, {"player": "S1", "callout": "BombsiteB"}) == round(1 / 3 * 100, 1)
    # kd metric isn't in the registry, so build a metric-like dict directly via CALLOUT_METRICS
    kd_metric = {"key": "kd", "better": "high"}
    assert goals.callout_value(kd_metric, a, {"player": "S1", "callout": "BombsiteB"}) == 1.25  # 5k/4d


def test_callout_value_side_filter():
    a = _pmatch(position_stats=[_ps("Long", ct_k=3, ct_d=1, t_k=0, t_d=4, open_k=1, open_d=1)])["analytics"]
    pos = goals.metric_by_key("pos")
    assert goals.callout_value(pos, a, {"player": "S1", "callout": "Long", "side": "ct"}) == 1.0  # ct deaths
    assert goals.callout_value(pos, a, {"player": "S1", "callout": "Long", "side": "t"}) == 4.0   # t deaths
    assert goals.callout_value(pos, a, {"player": "S1", "callout": "Long"}) == 5.0                # both sides


def test_callout_value_absent_callout_and_unsupported_metric():
    a = _pmatch(position_stats=[_ps("Pit", ct_d=2)])["analytics"]
    pos = goals.metric_by_key("pos")
    # player played but never at this callout -> count metric reads 0 (not dying there IS progress)
    assert goals.callout_value(pos, a, {"player": "S1", "callout": "Ramp"}) == 0.0
    # ratio metric (open_wr) needs a real row at the callout -> None (skip the match)
    owr = goals.metric_by_key("open_wr")
    assert goals.callout_value(owr, a, {"player": "S1", "callout": "Ramp"}) is None
    # player not in the match at all -> None
    assert goals.callout_value(pos, a, {"player": "GHOST", "callout": "Pit"}) is None
    # unsupported metric -> None (caller falls back to map-wide)
    assert goals.callout_value(goals.metric_by_key("adr"), a, {"player": "S1", "callout": "Pit"}) is None


def test_grade_callout_series_only_reflects_that_callout():
    """A callout-scoped goal grades by deaths AT the callout per match -- not map-wide deaths."""
    ms = [_pmatch(position_stats=[_ps("A", ct_d=1), _ps("Mid", ct_d=5)]),       # newest: 1 death at A
          _pmatch(position_stats=[_ps("A", ct_d=2), _ps("Mid", ct_d=4)]),       # 2 at A
          _pmatch(position_stats=[_ps("A", ct_d=3), _ps("Mid", ct_d=9)])]       # oldest: 3 at A
    g = {"metric": "pos", "target": 2, "scope": {"player": "S1", "callout": "A"}, "baseline": 3}
    res = goals.grade(g, ms)
    assert [s["value"] for s in res["series"]] == [1.0, 2.0, 3.0]   # ONLY callout A, not Mid's big counts
    assert res["callout"] == "A" and res["callout_status"] == "graded"
    assert "deaths at A" in res["callout_label"]
    assert res["samples"] == 3 and res["current"] == 1.0
    # recent avg (1+2+3)/3 = 2.0 == target 2 (low-better) -> meets target -> "fixed"
    assert res["recent_avg"] == 2.0 and res["verdict"] == "fixed"


def test_grade_callout_open_wr_high_metric():
    ms = [_pmatch(position_stats=[_ps("Site", open_k=4, open_d=1)]),    # 80%
          _pmatch(position_stats=[_ps("Site", open_k=3, open_d=2)]),    # 60%
          _pmatch(position_stats=[_ps("Site", open_k=1, open_d=4)])]    # 20%
    g = {"metric": "open_wr", "target": 55, "scope": {"player": "S1", "callout": "Site"}, "baseline": 20}
    res = goals.grade(g, ms)
    assert [s["value"] for s in res["series"]] == [80.0, 60.0, 20.0]
    assert res["unit"] == "%" and res["callout_status"] == "graded"
    assert res["recent_avg"] == round((80 + 60 + 20) / 3, 1)   # 53.3
    assert res["meets_target"] is False            # 53.3 < target 55 (open_wr is high-better)
    assert res["current"] == 80.0


def test_grade_callout_fallback_when_metric_unsupported():
    """A callout goal on a metric with no per-callout form grades map-wide + says so."""
    ms = [_match(players=[{"steamid": "S1", "adr": 90}]),
          _match(players=[{"steamid": "S1", "adr": 88}]),
          _match(players=[{"steamid": "S1", "adr": 86}])]
    g = {"metric": "adr", "target": 85, "scope": {"player": "S1", "callout": "Mid"}, "baseline": 60}
    res = goals.grade(g, ms)
    assert res["callout"] == "Mid" and res["callout_status"] == "fallback"
    assert "map-wide" in res["callout_label"]
    assert [s["value"] for s in res["series"]] == [90.0, 88.0, 86.0]   # normal ADR series, unchanged
    assert res["metric_label"] == "ADR"            # registry label preserved on fallback


def test_grade_callout_group_aggregates_members():
    """A squad callout goal sums death-counts across your members at the callout per match."""
    def mk(s1_d, s2_d):
        return _match(players=[{"steamid": "S1", "position_stats": [_ps("A", ct_d=s1_d)]},
                               {"steamid": "S2", "position_stats": [_ps("A", ct_d=s2_d)]},
                               {"steamid": "ENEMY", "position_stats": [_ps("A", ct_d=99)]}])
    ms = [mk(1, 1), mk(2, 1), mk(2, 2)]   # team deaths at A: 2, 3, 4 (ENEMY excluded)
    g = {"metric": "pos", "target": 2, "scope": {"group": "squad", "members": ["S1", "S2"], "callout": "A"},
         "baseline": 4}
    res = goals.grade(g, ms)
    assert [s["value"] for s in res["series"]] == [2.0, 3.0, 4.0]   # summed, no ENEMY
    assert res["callout_status"] == "graded"
    # per-member breakdown is also callout-scoped
    mem = {x["steamid"]: x for x in res["members"]}
    assert mem["S1"]["current"] == 1.0 and mem["S2"]["current"] == 1.0


def test_grade_no_callout_unchanged():
    """Guarantee: a goal WITHOUT scope.callout grades exactly as before (no callout keys added)."""
    ms = _adr_matches([90, 88, 86, 70, 60])
    res = goals.grade(_adr_goal(85), ms)
    assert res["verdict"] == "fixed" and res["current"] == 90 and res["samples"] == 5
    assert "callout" not in res and "callout_status" not in res
    # a callout goal for a player who isn't in any match -> no_data (never throws)
    g = {"metric": "pos", "target": 2, "scope": {"player": "GHOST", "callout": "A"}}
    empty = goals.grade(g, _adr_matches([90, 88]))
    assert empty["verdict"] == "no_data" and empty["callout_status"] == "graded"
    # a present player with no position_stats reads 0 deaths at the callout (not dying there is real)
    g2 = {"metric": "pos", "target": 2, "scope": {"player": "S1", "callout": "A"}}
    pres = goals.grade(g2, _adr_matches([90, 88]))   # S1 present, but no position_stats
    assert [s["value"] for s in pres["series"]] == [0.0, 0.0] and pres["verdict"] == "insufficient"


def test_callout_baseline_from_source_measured_at_callout(tmp_path):
    """add_goal's baseline for a callout goal is the value AT the callout, not the map-wide value."""
    import db
    db.DB_PATH = str(tmp_path / "goals.sqlite")
    db.migrate()
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "src.json").write_text(json.dumps({
        "map": "de_x", "source_sha1": "SRC", "rounds": [{}],
        "analytics": {"players": [{"steamid": "S1",
                                   "position_stats": [_ps("A", ct_d=3), _ps("Mid", ct_d=9)]}]}}),
        encoding="utf-8")
    goals._matches_memo["sig"] = None
    g = goals.add_goal({"metric": "pos", "target": 2, "source_match_key": "SRC",
                        "scope": {"player": "S1", "callout": "A"}}, cache_dir=str(cache))
    assert g["baseline"] == 3.0    # deaths at callout A in the source match (NOT 12 map-wide)


# ---- CRUD (SQLite-backed) --------------------------------------------------
def test_crud(tmp_path):
    import db
    db.DB_PATH = str(tmp_path / "goals.sqlite")
    db.migrate()
    assert goals.load_goals() == []
    g = goals.add_goal({"metric": "adr", "target": 85, "scope": {"player": "S1", "junk": "x"},
                        "title": "ADR 85", "drill": "aim_botz"}, cache_dir=str(tmp_path))
    assert g["id"].startswith("g_") and g["status"] == "open"
    assert "junk" not in g["scope"] and g["scope"]["player"] == "S1"   # scope filtered
    assert len(goals.load_goals()) == 1
    assert goals.get_goal(g["id"])["scope"]["player"] == "S1"          # scope round-trips through JSON column
    u = goals.update_goal(g["id"], {"status": "drilling", "notes": "wk1", "status_bad": "x"})
    assert u["status"] == "drilling" and u["notes"] == "wk1"
    assert goals.update_goal(g["id"], {"status": "garbage"})["status"] == "drilling"   # invalid ignored
    assert goals.delete_goal(g["id"]) == 1 and goals.load_goals() == []


def test_migrate_legacy_json(tmp_path, monkeypatch):
    import db
    db.DB_PATH = str(tmp_path / "g.sqlite")
    db.migrate()
    jpath = tmp_path / "goals.json"
    jpath.write_text(json.dumps([
        {"id": "g_old1", "metric": "adr", "target": 80, "scope": {"player": "S1"}, "status": "open",
         "created_at": "2026-01-01T00:00:00"}]), encoding="utf-8")
    monkeypatch.setattr(goals, "GOALS_PATH", str(jpath))
    assert goals.migrate_legacy_json() == 1
    got = goals.get_goal("g_old1")
    assert got and got["metric"] == "adr" and got["scope"]["player"] == "S1"
    assert not os.path.exists(str(jpath)) and os.path.exists(str(jpath) + ".imported")   # renamed -> backup
    assert goals.migrate_legacy_json() == 0          # idempotent: nothing left to import


def test_normalize_defaults():
    g = goals.normalize({"metric": "team_retake_wr"})
    assert g["status"] == "open" and g["title"] == "Retake win %" and g["target"] == 0.0


def _write_match(cache, name, players, insights, mp="de_x", sha=None):
    cache.mkdir(exist_ok=True)
    (cache / f"{name}.json").write_text(json.dumps({
        "map": mp, "source_sha1": sha or name, "rounds": [{}],
        "analytics": {"players": players, "insights": insights}}), encoding="utf-8")


def _iss(t):
    return {"type": t, "polarity": "issue"}


def test_recurring_basic(tmp_path):
    cache = tmp_path / "cache"
    P = [{"steamid": "S1"}, {"steamid": "S2"}]
    _write_match(cache, "m1", P, {"S1": [_iss("untraded_opening_death"), _iss("pos")]}, sha="A")
    _write_match(cache, "m2", P, {"S1": [_iss("untraded_opening_death"), _iss("pos"), _iss("dry_opening")]}, sha="B")
    _write_match(cache, "m3", P, {"S1": [_iss("untraded_opening_death"), {"type": "multikills", "polarity": "good"}]}, sha="C")
    goals._matches_memo["sig"] = None
    r = goals.recurring_mistakes(str(cache), player="S1", min_matches=2)
    assert r["matches"] == 3
    by = {x["type"]: x for x in r["recurring"]}
    assert by["untraded_opening_death"]["matches_present"] == 3
    assert by["pos"]["matches_present"] == 2
    assert "dry_opening" not in by          # in only 1 match -> below min_matches
    assert "multikills" not in by           # good polarity excluded
    assert by["untraded_opening_death"]["suggest_metric"] == "untraded_opening_death"
    assert by["untraded_opening_death"]["series"][0] == 1   # newest match first


def test_recurring_only_matches_player_played(tmp_path):
    cache = tmp_path / "cache"
    _write_match(cache, "m1", [{"steamid": "S1"}], {"S1": [_iss("pos")]}, sha="A")
    _write_match(cache, "m2", [{"steamid": "S2"}], {"S2": [_iss("pos")]}, sha="B")   # no S1
    goals._matches_memo["sig"] = None
    r = goals.recurring_mistakes(str(cache), player="S1", min_matches=1)
    assert r["matches"] == 1                 # only m1 counts toward S1's denominator


def test_count_trend():
    assert goals._count_trend([0, 0, 3, 3]) == "improving"   # newest-first: recent low, older high
    assert goals._count_trend([3, 3, 0, 0]) == "worsening"
    assert goals._count_trend([2, 2, 2, 2]) == "steady"


# ---- suggested target logic ------------------------------------------------
def test_suggest_target_low_metric_30pct_improvement():
    # avg 5.0 -> target ~3-4 (30% of 5 = 3.5 -> rounds to 4 if round(), but 0.7*5=3.5 -> round=4)
    # Actually 0.70 * 5 = 3.5, round() in Python rounds to nearest even -> 4
    t = goals._suggest_target([5, 5, 5], "untraded_opening_death")
    assert t is not None and t < 5.0 and t >= 1.0


def test_suggest_target_not_zero_when_avg_above_one():
    # avg 1.5 -> 0.70*1.5=1.05 -> round=1 (not 0)
    t = goals._suggest_target([2, 1, 2], "untraded_opening_death")
    assert t is not None and t >= 1.0


def test_suggest_target_high_metric_returns_none():
    # open_wr is better=="high" -> should return None
    assert goals._suggest_target([50, 48, 52], "open_wr") is None
    # traded_pct is also high-better
    assert goals._suggest_target([30, 28, 32], "traded_pct") is None


def test_suggest_target_empty_series_returns_none():
    assert goals._suggest_target([], "untraded_opening_death") is None


def test_suggest_target_included_in_recurring(tmp_path):
    cache = tmp_path / "cache"
    P = [{"steamid": "S1"}, {"steamid": "S2"}]
    for i in range(3):
        _write_match(cache, f"m{i}", P, {"S1": [_iss("untraded_opening_death")] * (3 + i)}, sha=f"R{i}")
    goals._matches_memo["sig"] = None
    r = goals.recurring_mistakes(str(cache), player="S1", min_matches=2)
    by = {x["type"]: x for x in r["recurring"]}
    row = by["untraded_opening_death"]
    assert "suggested_target" in row
    assert row["suggested_target"] is not None
    # target must be strictly less than the recent average
    avg = sum(row["series"][:3]) / 3
    assert row["suggested_target"] < avg


def test_matches_sidecar_and_memo(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    for nm, mp, sha, adr in [("a", "de_a", "AAA", 90), ("b", "de_b", "BBB", 50)]:
        (cache / f"{nm}.json").write_text(json.dumps({
            "map": mp, "source_sha1": sha, "rounds": [{}],
            "analytics": {"players": [{"steamid": "S1", "adr": adr}]}}), encoding="utf-8")
    goals._matches_memo["sig"] = None        # reset cross-test memo
    recs = goals._matches(str(cache))
    assert len(recs) == 2
    assert os.path.isdir(cache / "_ana")     # analytics sidecars were built
    assert goals._matches(str(cache)) is recs   # 2nd call hits the in-process memo (same object)
