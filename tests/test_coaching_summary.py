"""Tests for coaching_summary.py -- the natural-language coaching summary generator.

Covers: the deterministic heuristic on a realistic analytics dict, robustness to empty/partial
input (never throws), the AI env gate, and that enhance_summary is a no-op (NO network call)
when AI is disabled. No real network calls; no DB.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import coaching_summary as cs   # noqa: E402


# --- a realistic synthetic analytics dict (shapes per analytics.py analyze()) ---
def _analytics():
    return {
        "version": 7,
        "tickrate": 64,
        "n_rounds": 24,
        "have_econ": True,
        "players": [
            {"steamid": "111", "name": "Alice", "kills": 18, "deaths": 20, "assists": 4,
             "kd": 0.9, "adr": 62.0, "kast": 58.0, "hltv": 0.92, "open_wr": 33.0,
             "traded_pct": 18.0, "udr": 3.0, "clutch": {"won": 1, "lost": 2, "attempts": 3},
             "focus": [
                 {"area": "KAST", "value": 58.0, "benchmark": 70, "unit": "%", "severity": 3,
                  "detail": "KAST 58% vs ~70% target", "fix": "Trade/refrag drills."},
                 {"area": "Util dmg/rd", "value": 3.0, "benchmark": 8, "unit": "", "severity": 2,
                  "detail": "Util dmg/rd 3.0 vs ~8 target", "fix": "Learn 2-3 HE/molly lineups."},
                 {"area": "Mistake", "value": None, "benchmark": None, "unit": "", "severity": 3,
                  "detail": "R4: you took the opening death and weren't traded.",
                  "fix": "Watch the flagged round.", "round": 4, "tick": 5000,
                  "confidence": "high"},
             ]},
            {"steamid": "222", "name": "Bob", "kills": 22, "deaths": 17, "assists": 6,
             "kd": 1.29, "adr": 84.0, "kast": 74.0, "hltv": 1.18, "open_wr": 55.0,
             "traded_pct": 24.0, "udr": 9.0, "clutch": {"won": 2, "lost": 1, "attempts": 3},
             "focus": [
                 {"area": "Opening win%", "value": 55.0, "benchmark": 52, "unit": "%",
                  "severity": 2, "detail": "Opening win% 55% vs ~52% target",
                  "fix": "Prefire/peek practice."},
             ]},
        ],
        "rounds": [
            {"num": n, "winner": ("ct" if n % 2 else "t"), "reason": "",
             "buy_ct": "full", "buy_t": "full", "impact": float(100 - n * 3),
             "pistol": n in (1, 13)} for n in range(1, 25)
        ],
        "round_cards": [
            {"round": n, "winner": ("ct" if n % 2 else "t"), "reason": "",
             "buy_ct": "full", "buy_t": "full", "summary": "stuff happened.",
             "watch_t": 12.0, "moments": []} for n in range(1, 25)
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
                             "eco": {"rounds": 4, "win_pct": 25.0}},
        },
        "team_coaching": {
            "teams": [
                {"id": "A", "start_side": "CT", "name": "Alice's team",
                 "players": ["Alice", "Bob"], "won": 11, "lost": 13, "trade_pct": 19.0,
                 "entry": {"attempts": 14, "won": 6, "wr": 42.9},
                 "post_plant": {"n": 5, "wr": 40.0},
                 "retake": {"n": 6, "wr": 33.3},
                 "loss_reasons": [
                     {"reason": "Opening death, no trade", "count": 5, "rounds": [4, 7, 9, 15, 18]},
                     {"reason": "Lost the post-plant", "count": 3, "rounds": [11, 14, 20]},
                     {"reason": "Lost on an eco/save", "count": 2, "rounds": [2, 16]},
                 ],
                 "economy": {"full": {"rounds": 18, "win_pct": 44.4},
                             "eco": {"rounds": 4, "win_pct": 25.0}},
                 "top_death_zones": [{"zone": "Mid", "side": "CT", "deaths": 8}],
                 "roles": [{"name": "Bob", "ct": "Entry", "t": "Entry", "open_wr": 55.0,
                            "impact": 12.0},
                           {"name": "Alice", "ct": "Support", "t": "Lurker", "open_wr": 33.0,
                            "impact": 4.0}],
                 "practice_plan": [
                     {"focus": "Opening death, no trade", "rounds": [4, 7, 9, 15, 18],
                      "drill": "Entry + trade pairs -- never let first contact go unsupported."},
                     {"focus": "Lost the post-plant", "rounds": [11, 14, 20],
                      "drill": "Default post-plants: crossfires on the bomb, save util for the retake."},
                 ]},
                {"id": "B", "start_side": "T", "name": "Carl's team",
                 "players": ["Carl", "Dave"], "won": 13, "lost": 11, "trade_pct": 26.0,
                 "entry": {"attempts": 14, "won": 8, "wr": 57.1},
                 "post_plant": {"n": 6, "wr": 66.7},
                 "retake": {"n": 5, "wr": 60.0},
                 "loss_reasons": [
                     {"reason": "Lost the gunfights", "count": 4, "rounds": [1, 3, 5, 21]},
                 ],
                 "economy": {"full": {"rounds": 18, "win_pct": 55.6}},
                 "top_death_zones": [{"zone": "B", "side": "T", "deaths": 6}],
                 "roles": [],
                 "practice_plan": []},
            ]
        },
        "team_play": {},
        "insights": {
            "111": [
                {"round": 4, "tick": 5000, "type": "untraded_opening_death", "severity": 3,
                 "polarity": "issue", "text": "R4: you took the opening death and weren't traded."},
            ],
        },
        "benchmarks": {"kast": 70, "adr": 80},
        "meta": {"analytics_version": 7},
    }


def _recurring():
    """Shape from goals.recurring_mistakes()."""
    return {
        "player": "111", "matches": 5,
        "recurring": [
            {"type": "untraded_opening_death", "label": "Untraded opening deaths",
             "suggest_metric": "untraded_opening_death", "matches_present": 4,
             "matches_total": 5, "total": 12, "recent": 3, "series": [3, 2, 3, 4, 0],
             "trend": "steady", "suggested_target": 2.0},
            {"type": "low_utility", "label": "Low utility damage", "suggest_metric": "udr",
             "matches_present": 3, "matches_total": 5, "total": 5, "recent": 1,
             "series": [1, 1, 1, 1, 1], "trend": "steady", "suggested_target": None},
        ],
    }


# --- build_summary: realistic data --------------------------------------------
def test_build_summary_basic_shape():
    r = cs.build_summary(_analytics())
    assert isinstance(r, dict)
    assert r["source"] == "heuristic"
    assert r["ai"] is False
    assert isinstance(r["text"], str) and len(r["text"]) > 20
    assert isinstance(r["bullets"], list) and r["bullets"]
    assert isinstance(r["review_rounds"], list)
    assert all(isinstance(n, int) for n in r["review_rounds"])
    assert isinstance(r["utility_focus"], list)


def test_build_summary_picks_team_by_my_side():
    # CT-start team (A) lost 13 -> score line should reflect a loss
    r = cs.build_summary(_analytics(), my_side="CT")
    assert "11-13" in r["text"]
    assert "lost" in r["text"].lower()
    # T-start team (B) won 13-11
    r2 = cs.build_summary(_analytics(), my_side="T")
    assert "13-11" in r2["text"]


def test_build_summary_picks_team_by_player():
    # Alice (111) is on the CT-start team that lost
    r = cs.build_summary(_analytics(), player_steamid="111")
    assert "11-13" in r["text"]


def test_review_rounds_come_from_loss_reasons():
    r = cs.build_summary(_analytics(), my_side="CT")
    # the top loss reason rounds (4,7,9,15,18) should seed the review list
    assert 4 in r["review_rounds"]
    assert len(r["review_rounds"]) <= 4
    # round numbers must be ints, no dups
    assert len(r["review_rounds"]) == len(set(r["review_rounds"]))


def test_loss_reasons_surface_in_prose_and_bullets():
    r = cs.build_summary(_analytics(), my_side="CT")
    assert "opening death" in r["text"].lower()
    assert any("Opening death" in b for b in r["bullets"])


def test_utility_focus_populated():
    r = cs.build_summary(_analytics(), player_steamid="111")
    # Alice has a low Util dmg/rd focus item
    assert r["utility_focus"]
    assert any("util" in u.lower() for u in r["utility_focus"])


def test_recurring_drives_biggest_fix():
    r = cs.build_summary(_analytics(), player_steamid="111", recurring=_recurring())
    # recurring untraded openings (4 matches) should become the biggest fix
    txt = r["text"].lower()
    assert "recurring" in txt or "untraded opening" in txt
    assert any("Biggest fix" in b for b in r["bullets"])


# --- robustness: must never throw on missing/empty/partial input --------------
def test_build_summary_empty_dict():
    r = cs.build_summary({})
    assert isinstance(r, dict)
    assert isinstance(r["text"], str) and r["text"]      # still produces *some* prose
    assert r["review_rounds"] == []
    assert r["utility_focus"] == []
    assert r["source"] == "heuristic" and r["ai"] is False


def test_build_summary_none_and_non_dict():
    for bad in (None, [], "nope", 42):
        r = cs.build_summary(bad)
        assert isinstance(r, dict) and isinstance(r["text"], str) and r["text"]


def test_build_summary_partial_no_econ_no_teams():
    partial = {"n_rounds": 16, "have_econ": False, "players": [],
               "team_coaching": {"teams": []}, "team": {}, "rounds": [], "round_cards": []}
    r = cs.build_summary(partial)
    assert isinstance(r["text"], str) and r["text"]
    assert "16 rounds" in r["text"]
    assert r["review_rounds"] == []


def test_build_summary_team_no_loss_reasons():
    a = _analytics()
    for t in a["team_coaching"]["teams"]:
        t["loss_reasons"] = []
    r = cs.build_summary(a, my_side="CT")
    assert isinstance(r["text"], str) and r["text"]      # falls back to weak areas / fix


def test_build_summary_garbage_nested_types():
    # keys present but wrong types -> still no throw
    a = {"n_rounds": "x", "players": "nope", "team": [], "team_coaching": {"teams": "bad"},
         "rounds": {}, "round_cards": None}
    r = cs.build_summary(a)
    assert isinstance(r, dict) and isinstance(r["text"], str)


# --- AI gate ------------------------------------------------------------------
def test_ai_enabled_false_when_unset(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AI_SUMMARY_ENABLED", raising=False)
    assert cs.ai_enabled() is False


def test_ai_enabled_false_with_key_but_no_flag(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-xxx")
    monkeypatch.delenv("AI_SUMMARY_ENABLED", raising=False)
    assert cs.ai_enabled() is False      # key alone must NOT enable it


def test_ai_enabled_false_with_flag_but_no_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("AI_SUMMARY_ENABLED", "1")
    assert cs.ai_enabled() is False


def test_ai_enabled_true_when_both_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-xxx")
    monkeypatch.setenv("AI_SUMMARY_ENABLED", "true")
    assert cs.ai_enabled() is True
    monkeypatch.setenv("AI_SUMMARY_ENABLED", "0")
    assert cs.ai_enabled() is False      # falsey flag -> off


# --- enhance_summary: must be a no-op (NO network) when AI disabled ------------
def test_enhance_returns_heuristic_unchanged_when_disabled(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AI_SUMMARY_ENABLED", raising=False)
    base = cs.build_summary(_analytics(), my_side="CT")
    out = cs.enhance_summary(_analytics(), base)
    assert out is base                    # unchanged object, not even copied
    assert out["source"] == "heuristic" and out["ai"] is False


def test_enhance_makes_no_network_call_when_disabled(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AI_SUMMARY_ENABLED", raising=False)

    def _boom(*a, **k):
        raise AssertionError("network must NOT be called when AI is disabled")

    monkeypatch.setattr(cs.urllib.request, "urlopen", _boom)
    base = cs.build_summary(_analytics())
    out = cs.enhance_summary(_analytics(), base)
    assert out["ai"] is False


def test_enhance_swallows_network_error_when_enabled(monkeypatch):
    # AI "enabled", but the HTTP call fails -> must return the heuristic unchanged, no raise.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-xxx")
    monkeypatch.setenv("AI_SUMMARY_ENABLED", "1")

    def _boom(*a, **k):
        raise cs.urllib.error.URLError("no network in tests")

    monkeypatch.setattr(cs.urllib.request, "urlopen", _boom)
    base = cs.build_summary(_analytics())
    out = cs.enhance_summary(_analytics(), base)
    assert out["source"] == "heuristic" and out["ai"] is False
    assert out["text"] == base["text"]


def test_enhance_uses_ai_text_on_success(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-xxx")
    monkeypatch.setenv("AI_SUMMARY_ENABLED", "1")
    captured = {}

    class _Resp:
        def __init__(self, body):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["body"] = req.data
        import json as _j
        payload = _j.dumps({"content": [{"type": "text", "text": "AI coaching prose."}]})
        return _Resp(payload.encode("utf-8"))

    monkeypatch.setattr(cs.urllib.request, "urlopen", _fake_urlopen)
    base = cs.build_summary(_analytics())
    out = cs.enhance_summary(_analytics(), base)
    assert out["ai"] is True
    assert out["source"] == "ai"
    assert out["text"] == "AI coaching prose."
    # structured-only prompt: the raw analytics dict must NOT be shoved into the request body
    assert captured["timeout"] == cs.AI_TIMEOUT_S
    assert captured["url"] == cs.ANTHROPIC_URL
    assert b"frames" not in (captured["body"] or b"")


# --- coaching_summary convenience --------------------------------------------
def test_coaching_summary_runs_heuristic_when_ai_off(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AI_SUMMARY_ENABLED", raising=False)
    r = cs.coaching_summary(_analytics(), my_side="CT")
    assert r["source"] == "heuristic" and r["ai"] is False
    assert "11-13" in r["text"]
