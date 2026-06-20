"""Per-map CT / T round-winrate for a player or team, computed from already-parsed demos.

PURE + dependency-free. Operates ONLY on the `analytics` dict that analytics.analyze()
produces (the dict merged into each demo JSON under data["analytics"]). It never parses a
.dem, touches the DB, or reads a file -- the caller supplies the analytics dict(s) and the
map name, and wires routes/UI. Nothing here raises on partial/missing input.

--------------------------------------------------------------------------------------------
DATA SOURCE (investigated 2026-06-20 against all 11 cached demos in cache/_ana/)
--------------------------------------------------------------------------------------------
What IS persisted in the analytics dict and used here:
  * analytics["rounds"][i]["winner"]  -> "CT" | "T" | ""  (the SIDE that won that round)
  * analytics["rounds"][i]["num"]     -> 1-based round number
  * analytics["team_coaching"]["teams"][j]:
        "start_side" -> "CT" | "T"   (the side this team played in round 1)
        "players"    -> [display names]   (NOTE: names, not steamids)
        "won"/"lost" -> match totals (used only to cross-check; not split by side)
  * analytics["players"][k]: {"steamid", "name", "sides": {"ct":{"rounds":N}, "t":{"rounds":N}}}
        -> steamid<->name map; the per-side ROUND COUNTS were used to validate the derivation
           below (they are totals only, so they can't tell us which rounds were CT vs T).

What is NOT persisted (so it is DERIVED, not read):
  * The per-round side for each team / player (team_by_round is computed internally in
    analytics.py and dropped from the output). We reconstruct it from start_side + round
    number using the half/OT swap rules below.
  * The map name (lives at demo-JSON top level data["map"], NOT inside analytics). The caller
    passes it via `map_name=`; we also fall back to analytics.get("map") if present.

APPROACH (option (a) from the brief): for the target team we know its round-1 side
(start_side); CS2's fixed side-swap schedule then tells us its side in every round, and
rounds[].winner tells us which side won -- so a round is a "CT win for this team" iff the team
was on CT that round AND winner == "CT" (symmetrically for T).

SIDE-SWAP RULES (MR12 regulation + MR3 overtime) -- validated to reconcile EXACTLY (both the
won/lost totals AND the per-side round counts from players[].sides) across every cached demo,
including the 30-round and asymmetric 28-round overtime matches:
  * Regulation rounds 1..12  -> team is on  start_side
  * Regulation rounds 13..24 -> team is on  flip(start_side)        (halftime swap after R12)
  * Overtime  (round > 24, MR3 = 3 rounds per OT half):
        block = (round - 25) // 3      # 0-based OT half index
        block even -> flip(start_side) ; block odd -> start_side
    i.e. teams KEEP their regulation-second-half sides into the first OT half (no swap at the
    R24->R25 boundary), then swap every 3 OT rounds. This matched ground truth on all OT demos;
    the "naive" assumption of a swap entering OT did NOT reconcile.

LIMITATIONS (documented, never invented):
  * Requires analytics["team_coaching"]["teams"] with a usable start_side. Demos parsed before
    team_coaching existed, or with empty teams, yield no side data -> match_side_winrates
    returns None and aggregate_side_winrates SKIPS the match (it is not counted as 0 rounds).
  * Non-standard formats (e.g. MR15 = 30 regulation rounds, or custom OT lengths) would have
    different swap points than the MR12/MR3 assumed here. The cached corpus is all MR12; a demo
    whose round count can't be explained by MR12+MR3 still produces output, but its side split
    in the unexplained tail may be unreliable (the totals still come from rounds[].winner).
  * With steamid=None there is no "my team" marker in a pure analytics dict, so we fall back to
    the team that started CT (team "A"); pass an explicit steamid for a specific player.
"""

# Small-sample thresholds for aggregate_side_winrates (flag a map as thin evidence).
SMALL_SAMPLE_MIN_ROUNDS = 50
SMALL_SAMPLE_MIN_GAMES = 3

# Standard competitive format assumed for the swap schedule.
_REG_ROUNDS = 24     # regulation rounds (MR12: 12 + 12)
_REG_HALF = 12       # rounds before the halftime swap
_OT_HALF_LEN = 3     # rounds per overtime half (MR3)


def _flip(side):
    return "T" if side == "CT" else "CT"


def side_for_round(start_side, rnum, reg_rounds=_REG_ROUNDS, reg_half=_REG_HALF,
                   ot_half_len=_OT_HALF_LEN):
    """Which side ("CT"/"T") a team that started on `start_side` is on in round `rnum` (1-based).

    Implements the MR12 + MR3-overtime swap schedule validated in this module's docstring.
    Returns None if start_side isn't "CT"/"T" or rnum isn't a positive int.
    """
    if start_side not in ("CT", "T"):
        return None
    try:
        rnum = int(rnum)
    except (TypeError, ValueError):
        return None
    if rnum < 1:
        return None
    if rnum <= reg_half:
        return start_side
    if rnum <= reg_rounds:
        return _flip(start_side)
    # overtime: keep regulation-2nd-half side for the first OT half, then swap every ot_half_len.
    block = (rnum - reg_rounds - 1) // max(1, ot_half_len)
    return _flip(start_side) if block % 2 == 0 else start_side


def _norm_side(winner):
    """Normalize a persisted round winner to 'CT' | 'T' | None (drops '', null, junk)."""
    if not isinstance(winner, str):
        return None
    u = winner.strip().upper()
    if u in ("CT", "3"):
        return "CT"
    if u in ("T", "TERRORIST", "2"):
        return "T"
    return None


def _teams(analytics):
    """The team_coaching teams list, or [] if absent/malformed."""
    if not isinstance(analytics, dict):
        return []
    tc = analytics.get("team_coaching")
    if not isinstance(tc, dict):
        return []
    teams = tc.get("teams")
    return teams if isinstance(teams, list) else []


def _name_for_steamid(analytics, steamid):
    """Display name for a steamid via analytics['players'], or None."""
    if steamid is None:
        return None
    target = str(steamid)
    for p in (analytics.get("players") or []):
        if isinstance(p, dict) and str(p.get("steamid")) == target:
            return p.get("name")
    return None


def _pick_team(analytics, steamid):
    """Choose the team_coaching team for `steamid` (or a default team when None).

    Returns the team dict, or None if no usable team exists / the steamid can't be matched.
    Matching is by display name (team_coaching stores names, not steamids), resolved through
    analytics['players']. With steamid=None we fall back to the team that started CT (id 'A'),
    or the first team if no CT-starter is present -- documented in the module docstring.
    """
    teams = [t for t in _teams(analytics)
             if isinstance(t, dict) and t.get("start_side") in ("CT", "T")]
    if not teams:
        return None
    if steamid is None:
        for t in teams:
            if t.get("start_side") == "CT":
                return t
        return teams[0]
    name = _name_for_steamid(analytics, steamid)
    if name is None:
        return None
    for t in teams:
        members = t.get("players")
        if isinstance(members, list) and name in members:
            return t
    return None


def _map_name(analytics, map_name):
    """Resolve the map: explicit arg wins, else analytics.get('map'), else 'unknown'."""
    if map_name:
        return map_name
    if isinstance(analytics, dict):
        m = analytics.get("map")
        if m:
            return m
    return "unknown"


def match_side_winrates(analytics, *, steamid=None, map_name=None):
    """Per-side round tally for ONE match, for the team containing `steamid`.

    Args:
        analytics: one match's analytics dict (analytics.analyze() output).
        steamid:   the player whose team to report. None -> the team that started CT (see
                   module docstring); pass a steamid for a specific player.
        map_name:  map name override; falls back to analytics.get('map') then 'unknown'.

    Returns:
        {"map": str, "ct_rounds": int, "ct_won": int, "t_rounds": int, "t_won": int}
        for that team, or None if side data is unavailable (no team_coaching/start_side, the
        steamid can't be matched to a team, or there are no usable rounds). Never raises.
    """
    try:
        if not isinstance(analytics, dict):
            return None
        rounds = analytics.get("rounds")
        if not isinstance(rounds, list) or not rounds:
            return None
        team = _pick_team(analytics, steamid)
        if team is None:
            return None
        start_side = team.get("start_side")
        ct_rounds = ct_won = t_rounds = t_won = 0
        seen = False
        for r in rounds:
            if not isinstance(r, dict):
                continue
            rnum = r.get("num")
            side = side_for_round(start_side, rnum)
            if side is None:
                continue
            seen = True
            winner = _norm_side(r.get("winner"))
            if side == "CT":
                ct_rounds += 1
                if winner == "CT":
                    ct_won += 1
            else:
                t_rounds += 1
                if winner == "T":
                    t_won += 1
        if not seen or (ct_rounds + t_rounds) == 0:
            return None
        return {"map": _map_name(analytics, map_name),
                "ct_rounds": ct_rounds, "ct_won": ct_won,
                "t_rounds": t_rounds, "t_won": t_won}
    except Exception:
        # PURE + never-throw contract: any unexpected shape -> "unavailable".
        return None


def _wr(won, rounds):
    """Win% (won/rounds*100) to 1dp; 0.0 when rounds==0 (never divides by zero)."""
    if not rounds:
        return 0.0
    return round(100.0 * won / rounds, 1)


def aggregate_side_winrates(matches, *, steamid=None):
    """Aggregate per-side round winrates per map across many matches' analytics dicts.

    Args:
        matches:  iterable of per-match analytics dicts. Each may optionally be a 2-tuple
                  (analytics, map_name) to supply the map when it isn't inside the dict;
                  a bare dict uses analytics.get('map') / 'unknown'.
        steamid:  player whose team to aggregate (None -> started-CT team per match).

    Returns:
        {map: {ct_rounds, ct_won, ct_wr, t_rounds, t_won, t_wr, games, small_sample}}
        where *_wr is win% to 1dp, games = matches that contributed side data on that map, and
        small_sample is True when total rounds < SMALL_SAMPLE_MIN_ROUNDS or games <
        SMALL_SAMPLE_MIN_GAMES. Maps with 0 total rounds are omitted. Matches whose side data
        is unavailable are SKIPPED (not counted as a 0-round game). Never raises.
    """
    acc = {}
    try:
        iterable = matches if matches is not None else []
        for item in iterable:
            analytics, map_override = (item if isinstance(item, tuple) and len(item) == 2
                                       else (item, None))
            res = match_side_winrates(analytics, steamid=steamid, map_name=map_override)
            if res is None:
                continue  # unavailable -> skip entirely, do NOT count as zero
            mp = res.get("map") or "unknown"
            a = acc.setdefault(mp, {"ct_rounds": 0, "ct_won": 0,
                                    "t_rounds": 0, "t_won": 0, "games": 0})
            a["ct_rounds"] += res["ct_rounds"]
            a["ct_won"] += res["ct_won"]
            a["t_rounds"] += res["t_rounds"]
            a["t_won"] += res["t_won"]
            a["games"] += 1
    except Exception:
        # never-throw: return whatever we managed to accumulate before the bad item.
        pass

    out = {}
    for mp, a in acc.items():
        total_rounds = a["ct_rounds"] + a["t_rounds"]
        if total_rounds == 0:
            continue  # omit maps with no rounds
        out[mp] = {
            "ct_rounds": a["ct_rounds"], "ct_won": a["ct_won"],
            "ct_wr": _wr(a["ct_won"], a["ct_rounds"]),
            "t_rounds": a["t_rounds"], "t_won": a["t_won"],
            "t_wr": _wr(a["t_won"], a["t_rounds"]),
            "games": a["games"],
            "small_sample": (total_rounds < SMALL_SAMPLE_MIN_ROUNDS
                             or a["games"] < SMALL_SAMPLE_MIN_GAMES),
        }
    return out
