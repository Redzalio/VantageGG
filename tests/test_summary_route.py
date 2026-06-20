"""Bug 2 regression: GET /api/reviews/<id>/summary?side=CT vs ?side=T must return DIFFERENT,
team-focused coaching summaries (the report's team picker drives the summary). Local mode + temp
cache; heuristic only (no AI key)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SHA = "abc1230000000000000000000000000000000000"   # alnum -> resolves via <sha[:16]>.json cache path


def _demo():
    return {
        "source_sha1": SHA, "map": "de_dust2",
        "analytics": {
            "version": 9, "n_rounds": 24, "have_econ": True, "players": [],
            "round_cards": [], "rounds": [], "insights": {},
            "team_coaching": {"teams": [
                {"id": "A", "start_side": "CT", "name": "CT-start team", "players": ["Al", "Bo"],
                 "won": 11, "lost": 13, "trade_pct": 19.0,
                 "loss_reasons": [
                     {"reason": "Opening death, no trade", "count": 5, "rounds": [4, 7, 9, 15, 18]},
                     {"reason": "Lost the post-plant", "count": 3, "rounds": [11, 14, 20]}],
                 "practice_plan": [{"focus": "Opening death, no trade", "rounds": [4, 7, 9],
                                    "drill": "Entry + trade pairs."}],
                 "top_death_zones": [{"zone": "Mid", "side": "CT", "deaths": 8}], "roles": []},
                {"id": "B", "start_side": "T", "name": "T-start team", "players": ["Ca", "Da"],
                 "won": 13, "lost": 11, "trade_pct": 26.0,
                 "loss_reasons": [
                     {"reason": "Lost the gunfights", "count": 4, "rounds": [1, 3, 5, 21]}],
                 "practice_plan": [], "top_death_zones": [{"zone": "B", "side": "T", "deaths": 6}],
                 "roles": []},
            ]},
            "team": {}, "team_play": {}, "benchmarks": {}, "meta": {},
        },
    }


def _client(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)       # local/open mode -> accessible() is True
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)   # heuristic only, deterministic
    import app
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / (SHA[:16] + ".json")).write_text(json.dumps(_demo()), encoding="utf-8")
    monkeypatch.setattr(app, "CACHE", str(cache))
    return app.app.test_client()


def test_summary_differs_by_side(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    ct = c.get("/api/reviews/" + SHA + "/summary?side=CT")
    t = c.get("/api/reviews/" + SHA + "/summary?side=T")
    assert ct.status_code == 200 and t.status_code == 200
    ct_text, t_text = ct.get_json()["text"], t.get_json()["text"]
    assert ct_text and t_text
    assert ct_text != t_text                                 # the picker actually changes the summary
    # each summary should describe its own team's record (11-13 for CT-start, 13-11 for T-start)
    assert "11-13" in ct_text and "13-11" in t_text
