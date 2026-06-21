"""Squad auto-detection + roster curation: teammates from 2+ shared matches become the squad,
one-off players become add-suggestions, and add/remove overrides stick (via /api/squad)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db   # noqa: E402


def _p(sid, name):
    return {"steamid": sid, "name": name, "kills": 10, "deaths": 10, "kd": 1.0, "adr": 75,
            "kast": 70, "hltv": 1.0, "open_wr": 50, "traded_pct": 20, "udr": 20}


def _match(sha, players):
    return {"source_sha1": sha, "map": "de_x", "version": 14, "duration": 1800.0,
            "rounds": [{"score_ct": 13, "score_t": 7}],
            "analytics": {"version": 9, "n_rounds": 20, "players": players}}


def _match_team(sha, roster):
    """roster: [(sid, name, team)]. Builds analytics.players AND the replay player list (which carries
    `team` 2=T/3=CT) so index_demo records each player's roster team for same-team squad detection."""
    m = _match(sha, [_p(sid, name) for sid, name, _ in roster])
    m["players"] = [{"steamid": sid, "name": name, "team": team} for sid, name, team in roster]
    return m


def _scope(uid):
    return {"uid": uid, "team_ids": db.team_ids_for_user(uid), "ownerless": False}


def test_squad_detection_threshold_and_curation(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    import app
    db.DB_PATH = str(tmp_path / "sq.sqlite")
    db.migrate()
    u1 = db.upsert_user("1", "Me")                      # account SteamID "1"
    # 3 matches: teammate "2" in all 3; one-off "3" in just one
    db.index_demo(_match("a" * 40, [_p("1", "Me"), _p("2", "Mate")]), "a" * 16, owner_user_id=u1)
    db.index_demo(_match("b" * 40, [_p("1", "Me"), _p("2", "Mate")]), "b" * 16, owner_user_id=u1)
    db.index_demo(_match("c" * 40, [_p("1", "Me"), _p("2", "Mate"), _p("3", "Rando")]), "c" * 16, owner_user_id=u1)
    you, det = db.squad_for(u1, _scope(u1))
    assert you["steamid"] == "1"
    assert {x["steamid"]: x["shared"] for x in det} == {"2": 3, "3": 1}
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = u1
    sq = c.get("/api/squad").get_json()
    assert sq["available"] is True and sq["you"]["steamid"] == "1"
    assert {p["steamid"] for p in sq["squad"]} == {"2"}          # >=2 shared -> auto squad
    assert {p["steamid"] for p in sq["candidates"]} == {"3"}     # one-off -> suggestion
    # ADD the one-off
    sq2 = c.post("/api/squad", json={"steamid": "3", "name": "Rando", "action": "add"}).get_json()
    assert "3" in {p["steamid"] for p in sq2["squad"]}
    assert any(p["steamid"] == "3" and p["pinned"] for p in sq2["squad"])
    # REMOVE the auto teammate
    sq3 = c.post("/api/squad", json={"steamid": "2", "action": "remove"}).get_json()
    assert "2" not in {p["steamid"] for p in sq3["squad"]}
    assert "2" in {p["steamid"] for p in sq3["candidates"]}      # removable -> back to a suggestion


def test_squad_scoped_to_own_matches(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    import app
    db.DB_PATH = str(tmp_path / "sq2.sqlite")
    db.migrate()
    u1 = db.upsert_user("1", "Me")
    u2 = db.upsert_user("9", "Other")
    db.index_demo(_match("a" * 40, [_p("1", "Me"), _p("2", "Mate")]), "a" * 16, owner_user_id=u1)
    db.index_demo(_match("b" * 40, [_p("1", "Me"), _p("2", "Mate")]), "b" * 16, owner_user_id=u1)
    db.index_demo(_match("z" * 40, [_p("9", "Other"), _p("5", "Stranger")]), "z" * 16, owner_user_id=u2)
    you, det = db.squad_for(u1, _scope(u1))
    assert {x["steamid"] for x in det} == {"2"}        # only u1's own matches count, not u2's


def test_squad_excludes_opponents_by_team(tmp_path, monkeypatch):
    """With roster team data present, only same-team co-players (teammates) enter the squad --
    opponents you've faced 2+ times are excluded even though they share the same matches."""
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    import app  # noqa: F401
    db.DB_PATH = str(tmp_path / "sqteam.sqlite")
    db.migrate()
    u1 = db.upsert_user("1", "Me")
    roster = [("1", "Me", 2), ("2", "Mate", 2), ("3", "Opp1", 3), ("4", "Opp2", 3)]
    db.index_demo(_match_team("a" * 40, roster), "a" * 16, owner_user_id=u1)
    db.index_demo(_match_team("b" * 40, roster), "b" * 16, owner_user_id=u1)
    you, det = db.squad_for(u1, _scope(u1))
    assert you["steamid"] == "1"
    # only the same-team teammate; opponents 3 & 4 excluded despite sharing both matches
    assert {x["steamid"]: x["shared"] for x in det} == {"2": 2}


def test_squad_null_team_falls_back_to_all(tmp_path, monkeypatch):
    """Demos indexed before the team column existed (no replay player list -> NULL team) keep the old
    'anyone in your matches' behavior, so existing libraries don't suddenly lose their squad."""
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    import app  # noqa: F401
    db.DB_PATH = str(tmp_path / "sqnull.sqlite")
    db.migrate()
    u1 = db.upsert_user("1", "Me")
    # _match (no replay players list) -> team stays NULL for everyone
    db.index_demo(_match("a" * 40, [_p("1", "Me"), _p("2", "Mate")]), "a" * 16, owner_user_id=u1)
    db.index_demo(_match("b" * 40, [_p("1", "Me"), _p("2", "Mate")]), "b" * 16, owner_user_id=u1)
    you, det = db.squad_for(u1, _scope(u1))
    assert {x["steamid"]: x["shared"] for x in det} == {"2": 2}


def test_squad_unavailable_in_local_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    import app
    db.DB_PATH = str(tmp_path / "sq3.sqlite")
    db.migrate()
    sq = app.app.test_client().get("/api/squad").get_json()
    assert sq["available"] is False                     # no account -> pickers fall back to all players
