"""Stage 5 data-isolation tests: per-user + per-team demo visibility, delete permissions, and the
end-to-end enforcement through the Flask endpoints (anon blocked, users scoped). Temp DB, no parsing."""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db   # noqa: E402


def _p(sid, name):
    return {"steamid": sid, "name": name, "kills": 10, "deaths": 10, "kd": 1.0,
            "adr": 75, "kast": 70, "hltv": 1.0, "open_wr": 50, "traded_pct": 20, "udr": 20}


def _match(sha, mp, players):
    return {"source_sha1": sha, "map": mp, "version": 14, "duration": 1800.0,
            "rounds": [{"score_ct": 13, "score_t": 7}],
            "analytics": {"version": 9, "n_rounds": 20, "players": players}}


def _tmpdb(tmp_path):
    db.DB_PATH = str(tmp_path / "iso.sqlite")
    db.migrate()


def _seed(tmp_path):
    """Two users; demo A owned by u1, B owned by u2, C ownerless (legacy/local)."""
    _tmpdb(tmp_path)
    u1 = db.upsert_user("76561190000000001", "U1")
    u2 = db.upsert_user("76561190000000002", "U2")
    db.index_demo(_match("a" * 40, "de_a", [_p("1", "A")]), "a" * 16, owner_user_id=u1,
                  created_at="2026-06-01T00:00:00")
    db.index_demo(_match("b" * 40, "de_b", [_p("2", "B")]), "b" * 16, owner_user_id=u2,
                  created_at="2026-06-02T00:00:00")
    db.index_demo(_match("c" * 40, "de_c", [_p("3", "C")]), "c" * 16, owner_user_id=None,
                  created_at="2026-06-03T00:00:00")
    return u1, u2


def _scope(uid, ownerless=True):
    # ownerless=True mirrors local/AUTH_REQUIRED=0 (legacy demos visible); False = locked-down site
    return {"uid": uid, "team_ids": db.team_ids_for_user(uid), "ownerless": ownerless}


# ---- per-user visibility ----------------------------------------------------
def test_open_scope_sees_everything(tmp_path):
    _seed(tmp_path)
    assert {m["map"] for m in db.list_matches(scope=None)} == {"de_a", "de_b", "de_c"}


def test_user_sees_own_plus_ownerless_not_others(tmp_path):
    u1, u2 = _seed(tmp_path)
    maps1 = {m["map"] for m in db.list_matches(scope=_scope(u1))}
    maps2 = {m["map"] for m in db.list_matches(scope=_scope(u2))}
    assert maps1 == {"de_a", "de_c"}                  # own + ownerless, NOT de_b
    assert maps2 == {"de_b", "de_c"}


def test_accessible_and_can_delete(tmp_path):
    u1, u2 = _seed(tmp_path)
    s1 = _scope(u1)
    assert db.accessible("a" * 40, s1) and db.accessible("c" * 40, s1)
    assert not db.accessible("b" * 40, s1)            # u1 can't see u2's demo
    assert db.can_delete("a" * 40, s1)                # owner can delete
    assert db.can_delete("c" * 40, s1)                # ownerless deletable
    assert not db.can_delete("b" * 40, s1)            # not owner -> no delete


def test_ownerless_hidden_when_locked_down(tmp_path):
    # AUTH_REQUIRED=1 simulation (ownerless=False): unclaimed demos must NOT leak to a logged-in user
    u1, _ = _seed(tmp_path)
    s = _scope(u1, ownerless=False)
    assert {m["map"] for m in db.list_matches(scope=s)} == {"de_a"}   # only own; de_c (ownerless) hidden
    assert db.accessible("a" * 40, s) and not db.accessible("c" * 40, s)
    assert not db.can_delete("c" * 40, s)                # can't delete an unclaimed demo either
    ok = db.visible_predicate(s)
    assert ok("a" * 40) and not ok("c" * 40)


def test_all_players_and_trends_scoped(tmp_path):
    u1, _ = _seed(tmp_path)
    sids = {p["steamid"] for p in db.all_players(scope=_scope(u1))}
    assert sids == {"1", "3"}                         # players from de_a + de_c only, not "2"
    assert db.player_trends("2", scope=_scope(u1))["n_matches"] == 0   # u2's player hidden
    assert db.player_trends("1", scope=_scope(u1))["n_matches"] == 1


# ---- team sharing -----------------------------------------------------------
def test_team_share_makes_demo_visible_to_members(tmp_path):
    u1, u2 = _seed(tmp_path)
    team = db.create_team("Squad", u1)                # u1 owns
    assert db.join_team(team["invite_code"], u2)["role"] == "member"
    # before sharing: u2 cannot see de_a
    assert "de_a" not in {m["map"] for m in db.list_matches(scope=_scope(u2))}
    assert db.set_demo_team("a" * 40, team["id"], u1) is True
    # after sharing: u2 sees de_a, but still cannot delete it (not owner)
    assert "de_a" in {m["map"] for m in db.list_matches(scope=_scope(u2))}
    assert db.accessible("a" * 40, _scope(u2)) and not db.can_delete("a" * 40, _scope(u2))


def test_set_demo_team_rejects_non_owner_and_foreign_team(tmp_path):
    u1, u2 = _seed(tmp_path)
    team = db.create_team("Squad", u1)
    assert db.set_demo_team("a" * 40, team["id"], u2) is False   # u2 not the demo owner
    # u1 owns de_a but isn't a member of u2's private team -> can't share into it
    t2 = db.create_team("Other", u2)
    assert db.set_demo_team("a" * 40, t2["id"], u1) is False
    assert db.set_demo_team("a" * 40, None, u1) is True          # unshare always ok for owner


def test_teams_for_user_invite_code_owner_only(tmp_path):
    u1, u2 = _seed(tmp_path)
    team = db.create_team("Squad", u1)
    db.join_team(team["invite_code"], u2)
    owner_view = next(t for t in db.teams_for_user(u1) if t["id"] == team["id"])
    member_view = next(t for t in db.teams_for_user(u2) if t["id"] == team["id"])
    assert "invite_code" in owner_view and owner_view["member_count"] == 2
    assert "invite_code" not in member_view            # members don't get the join code


# ---- end-to-end enforcement through the Flask endpoints ----------------------
def test_endpoints_block_anon_and_scope_users(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "1")           # force login
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    import app
    u1, u2 = _seed(tmp_path)                            # also (re)points db.DB_PATH at the temp
    c = app.app.test_client()
    # anonymous + AUTH_REQUIRED -> 401 on data endpoints
    assert c.get("/api/matches").status_code == 401
    assert c.get("/api/library").status_code == 401
    # logged in as u1 -> only own + ownerless
    with c.session_transaction() as s:
        s["uid"] = u1
    maps = {m["map"] for m in c.get("/api/matches").get_json()}
    assert maps == {"de_a"}                             # only u1's own; de_b (u2) AND de_c (ownerless) hidden
    # u1 cannot GET or DELETE u2's demo
    assert c.get("/api/demo/" + "b" * 40).status_code == 404
    assert c.delete("/api/demo/" + "b" * 40).status_code in (403, 404)


def test_local_mode_endpoints_open(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    import app
    _seed(tmp_path)
    c = app.app.test_client()
    maps = {m["map"] for m in c.get("/api/matches").get_json()}
    assert maps == {"de_a", "de_b", "de_c"}            # local mode: everything visible (unchanged)


# ---- dashboard (Stage 7) ----------------------------------------------------
def test_dashboard_shape_local_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    import app
    _seed(tmp_path)
    d = app.app.test_client().get("/api/dashboard").get_json()
    assert d["match_count"] == 3
    assert {m["map"] for m in d["matches"]} == {"de_a", "de_b", "de_c"}
    assert all("id" in m for m in d["matches"])                # cards need an id to open the replay
    for k in ("matches", "match_count", "active_jobs", "open_goals", "open_goal_count", "me"):
        assert k in d


def test_dashboard_me_stats_for_logged_in_player(tmp_path, monkeypatch):
    # "Your form" card: the signed-in user's own averages, matched by SteamID == their demo steamid
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    import app
    _tmpdb(tmp_path)
    uid = db.upsert_user("111", "Alice")                       # their SteamID == player "111"
    db.index_demo(_match("a" * 40, "de_a", [_p("111", "Alice")]), "a" * 16,
                  owner_user_id=uid, created_at="2026-06-01T00:00:00")
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid
    me = c.get("/api/dashboard").get_json()["me"]
    assert me and me["n_matches"] == 1 and me["steamid"] == "111" and "hltv" in (me["averages"] or {})


def test_local_logged_in_sees_own_plus_ownerless_not_others(tmp_path, monkeypatch):
    # the user's current mode: AUTH_REQUIRED=0, signed in -> own + legacy ownerless, but NOT another user's
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    import app
    u1, _ = _seed(tmp_path)
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = u1
    maps = {m["map"] for m in c.get("/api/matches").get_json()}
    assert maps == {"de_a", "de_c"}                     # own + ownerless legacy; de_b (u2) stays hidden


def test_dashboard_scoped_and_blocks_anon(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    import app
    u1, _ = _seed(tmp_path)
    c = app.app.test_client()
    assert c.get("/api/dashboard").status_code == 401          # anon + AUTH_REQUIRED
    with c.session_transaction() as s:
        s["uid"] = u1
    d = c.get("/api/dashboard").get_json()
    assert {m["map"] for m in d["matches"]} == {"de_a"} and d["match_count"] == 1   # ownerless de_c hidden


# ---- public landing page (Stage 8): server-side first-paint view selection ---
def test_index_landing_for_logged_out_when_auth_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://demos.example.com")
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    import app
    db.DB_PATH = str(tmp_path / "land.sqlite")
    db.migrate()
    html = app.app.test_client().get("/").get_data(as_text=True)
    assert 'class="on-landing"' in html and 'id="landing"' in html
    assert "Sign in through Steam" in html


def test_index_dashboard_in_local_mode(monkeypatch):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    import app
    html = app.app.test_client().get("/").get_data(as_text=True)
    assert 'class="on-dashboard"' in html               # pure local: no landing wall


# ---- Free upload cap (Pro/admin/tiers-off = unlimited) ----------------------
def test_upload_allowance_unlimited_when_tiers_off(monkeypatch):
    import app
    monkeypatch.setattr(app, "TIERS_ENABLED", False)
    assert app.upload_allowance({"id": 1, "tier": "free"})["unlimited"] is True


def test_free_upload_blocked_at_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_STEAM_IDS", "")
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    import app
    import appconfig
    monkeypatch.setattr(app, "TIERS_ENABLED", True)
    monkeypatch.setattr(appconfig, "free_upload_limit", lambda: 2)   # admin-settable cap
    db.DB_PATH = str(tmp_path / "q.sqlite")
    db.migrate()
    uid = db.upsert_user("111", "Free")                       # free tier, 2 owned demos = at the cap
    db.index_demo(_match("a" * 40, "de_a", [_p("1", "A")]), "a" * 16, owner_user_id=uid)
    db.index_demo(_match("b" * 40, "de_b", [_p("2", "B")]), "b" * 16, owner_user_id=uid)
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid
    me = c.get("/api/me").get_json()["upload_quota"]
    assert me["used"] == 2 and me["limit"] == 2 and me["unlimited"] is False
    r = c.post("/api/upload", data={"file": (io.BytesIO(b"demo"), "m.dem")},
               content_type="multipart/form-data")
    assert r.status_code == 403 and "Free plan holds" in r.get_json()["error"]


def test_index_dashboard_when_logged_in(tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://demos.example.com")
    import app
    db.DB_PATH = str(tmp_path / "land2.sqlite")
    db.migrate()
    uid = db.upsert_user("76561190000000009", "X")
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid
    html = c.get("/").get_data(as_text=True)
    assert 'class="on-dashboard"' in html               # signed in -> dashboard, not the marketing page


# ---- dashboard analytics: roster scoping + match endpoint -------------------
def test_dashboard_analytics_local_mode_all_players(tmp_path, monkeypatch):
    """Local/no-auth: roster_mode=local_all, all players visible."""
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    import app
    _tmpdb(tmp_path)
    db.index_demo(_match("a" * 40, "de_a", [_p("S1", "Alice"), _p("OPP", "Opp")]),
                  "a" * 16, owner_user_id=None, created_at="2026-06-01T00:00:00")
    d = app.app.test_client().get("/api/dashboard/analytics").get_json()
    assert d["roster_mode"] == "local_all"
    sids = {p["steamid"] for p in d["players"]}
    assert "S1" in sids and "OPP" in sids
    assert "form" in d["overview"] and "map_stats" in d["overview"] and "next_focus" in d["overview"]


def test_dashboard_analytics_personal_squad_filters_opponents(tmp_path, monkeypatch):
    """Personal workspace: auto-squad (shared>=2) included; one-off opponents excluded."""
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    import app
    _tmpdb(tmp_path)
    uid = db.upsert_user("MYSID", "Me")
    # Two demos: MYSID + TEAMMATE → shared=2 (auto-squad); RAND1/RAND2 appear once each
    db.index_demo(_match("a" * 40, "de_a", [_p("MYSID", "Me"), _p("TEAMMATE", "T"), _p("RAND1", "R1")]),
                  "a" * 16, owner_user_id=uid, created_at="2026-06-01T00:00:00")
    db.index_demo(_match("b" * 40, "de_b", [_p("MYSID", "Me"), _p("TEAMMATE", "T"), _p("RAND2", "R2")]),
                  "b" * 16, owner_user_id=uid, created_at="2026-06-02T00:00:00")
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid
    d = c.get("/api/dashboard/analytics").get_json()
    assert d["roster_mode"] == "personal_squad"
    sids = {p["steamid"] for p in d["players"]}
    assert "MYSID" in sids      # you
    assert "TEAMMATE" in sids   # shared=2 → auto-squad
    assert "RAND1" not in sids  # shared=1 → not in squad
    assert "RAND2" not in sids  # shared=1 → not in squad


def test_dashboard_analytics_team_workspace_mode(tmp_path, monkeypatch):
    """Team workspace: roster_mode=team; endpoint returns 403 for non-member team."""
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    import app
    _tmpdb(tmp_path)
    u1, u2 = _seed(tmp_path)
    team = db.create_team("Squad", u1)
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = u1
    d = c.get(f"/api/dashboard/analytics?workspace=team:{team['id']}").get_json()
    assert d["roster_mode"] == "team"
    # u2 cannot access u1's team workspace
    with c.session_transaction() as s:
        s["uid"] = u2
    r = c.get(f"/api/dashboard/analytics?workspace=team:{team['id']}")
    assert r.status_code == 403


def test_dashboard_analytics_match_endpoint_auth(tmp_path, monkeypatch):
    """analytics/match/<id>: 401 when blocked, 404 for unknown demo."""
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    import app
    _tmpdb(tmp_path)
    c = app.app.test_client()
    assert c.get("/api/dashboard/analytics/match/nope").status_code == 401
    # Logged in but demo doesn't exist
    u1, _ = _seed(tmp_path)
    with c.session_transaction() as s:
        s["uid"] = u1
    assert c.get("/api/dashboard/analytics/match/nonexistent").status_code == 404


def test_dashboard_analytics_form_windows(tmp_path, monkeypatch):
    """Overview form windows present with correct structure."""
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    import app
    _tmpdb(tmp_path)
    for i, (sha, mp) in enumerate([("a" * 40, "de_a"), ("b" * 40, "de_b"), ("c" * 40, "de_a")]):
        db.index_demo(_match(sha, mp, [_p("S1", "A")]), sha[:16], owner_user_id=None,
                      created_at=f"2026-06-0{i + 1}T00:00:00")
    d = app.app.test_client().get("/api/dashboard/analytics").get_json()
    ov = d["overview"]
    assert set(ov["form"].keys()) >= {"last3", "last5", "all", "delta3"}
    assert len(ov["map_stats"]) == 2   # de_a x2, de_b x1
    maps = {r["map"] for r in ov["map_stats"]}
    assert "de_a" in maps and "de_b" in maps
