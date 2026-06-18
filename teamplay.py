"""Team spacing & trade-network analysis (#43).

Two team-level views, built from the same canonical events as the rest of analytics:

  * TRADE NETWORK -- a directed "who trades for whom" graph. When a player dies to an enemy
    and a teammate kills that enemy within the trade window, that's a trade edge trader->victim.
    Per player we surface traded% (how often their death gets avenged), trades made for others,
    and untraded deaths; per team we surface the strongest trade pairs and the weakest links
    (players who keep dying untraded -- the spacing/support problems to fix in review).

  * SPACING -- how far from support each death happened. For every death we measure the victim's
    distance to their NEAREST alive teammate (from the replay frames). Far = isolated/over-extended
    (no trade was possible); close clusters = clumped (one nade/spray can wipe multiple). The team
    summary reports avg support distance, isolated- and clumped-death counts, and worst-spaced players.

Generic by design: the two teams are simply the sides as they started round 1 (they persist across
the halftime swap), identified only from the demo -- no roster config, no specific players.
Trade/spacing denominators count only deaths to a real enemy (suicides/teamkills/bomb are skipped).
"""
import math

TRADE_WINDOW_S = 5.0     # seconds: teammate must kill the killer within this to count as a trade
ISO_DIST = 1000.0        # units (~10m): farther than this from every teammate at death = isolated
CLUMP_DIST = 350.0       # units: two same-team deaths this close...
CLUMP_WINDOW_S = 4.0     # ...and this close in time = clumped (bad spacing the other way)


def _teams(team_by_round):
    """The two teams as sets of steamids, by their round-1 side (persist across half-swap)."""
    base = team_by_round.get(1, {})
    return {"A": set(s for s, t in base.items() if t == 3),    # started CT
            "B": set(s for s, t in base.items() if t == 2)}    # started T


def _is_enemy_kill(d, tbr, vteam):
    """A death we can attribute: real killer, on the enemy side, not a self/teamkill."""
    atk = d.get("atk")
    return bool(atk) and atk != d.get("vic") and tbr.get(atk) not in (None, vteam)


def build_trade_network(deaths_by_round, team_by_round, names, tickrate,
                        trade_window_s=TRADE_WINDOW_S):
    """Directed trade graph + per-player traded%/trades-made + per-team weak links."""
    teams = _teams(team_by_round)
    trade_ticks = int(trade_window_s * tickrate)
    pl = {}                      # sid -> accumulators (only tradeable enemy-kill deaths)
    edges = {}                   # (trader_sid, victim_sid) -> count

    def P(sid):
        return pl.setdefault(sid, {"deaths": 0, "traded": 0, "untraded": 0, "trades_made": 0})

    for rnum, dlist in deaths_by_round.items():
        dl = sorted(dlist, key=lambda x: x["tick"])
        tbr = team_by_round.get(rnum, {})
        for i, d in enumerate(dl):
            vic, atk, tk = d.get("vic"), d.get("atk"), d["tick"]
            vteam = tbr.get(vic)
            if not vic or vteam not in (2, 3) or not _is_enemy_kill(d, tbr, vteam):
                continue
            P(vic)["deaths"] += 1
            trader = None
            for d2 in dl[i + 1:]:
                if d2["tick"] - tk > trade_ticks:
                    break
                # a teammate of the victim kills the killer within the window = a trade
                if (d2.get("vic") == atk and d2.get("atk") and d2["atk"] != vic
                        and tbr.get(d2["atk"]) == vteam):
                    trader = d2["atk"]
                    break
            if trader:
                P(vic)["traded"] += 1
                P(trader)["trades_made"] += 1
                edges[(trader, vic)] = edges.get((trader, vic), 0) + 1
            else:
                P(vic)["untraded"] += 1

    out = {}
    for tid, sids in teams.items():
        if not sids:
            continue
        players = []
        for sid in sids:
            a = pl.get(sid, {"deaths": 0, "traded": 0, "untraded": 0, "trades_made": 0})
            dn = a["deaths"]
            players.append({
                "steamid": sid, "name": names.get(sid, sid),
                "deaths": dn, "traded": a["traded"], "untraded": a["untraded"],
                "trades_made": a["trades_made"],
                "traded_pct": round(100.0 * a["traded"] / dn, 1) if dn else None})
        # worst-traded first (None sorts last)
        players.sort(key=lambda p: (p["traded_pct"] is None,
                                    p["traded_pct"] if p["traded_pct"] is not None else 9e9))
        team_edges = sorted(
            ({"trader_sid": tr, "trader": names.get(tr, tr),
              "victim_sid": vc, "victim": names.get(vc, vc), "count": c}
             for (tr, vc), c in edges.items() if tr in sids and vc in sids),
            key=lambda e: -e["count"])
        tot_d = sum(p["deaths"] for p in players)
        tot_tr = sum(p["traded"] for p in players)
        weak = [{"name": p["name"], "steamid": p["steamid"],
                 "traded_pct": p["traded_pct"], "untraded": p["untraded"]}
                for p in players if p["deaths"] >= 4 and p["traded_pct"] is not None
                and p["traded_pct"] < 40]
        out[tid] = {
            "players": players,
            "edges": team_edges[:12],
            "team_traded_pct": round(100.0 * tot_tr / tot_d, 1) if tot_d else None,
            "weak_links": weak,
        }
    return out


def build_spacing(deaths_by_round, team_by_round, names, replay, tickrate):
    """Per-team spacing-at-death from the replay frames: avg support distance, isolated &
    clumped death counts, worst-spaced players. Returns {} if no frames (graceful)."""
    frames = (replay or {}).get("frames") or []
    rp = (replay or {}).get("players") or []
    if not frames or not rp:
        return {}
    sr = replay.get("sample_rate", 8)
    sid_ridx = {p["steamid"]: i for i, p in enumerate(rp)}
    teams = _teams(team_by_round)
    teamof = {s: tid for tid, ss in teams.items() for s in ss}
    clump_ticks = int(CLUMP_WINDOW_S * tickrate)

    def frame_at(t_sec):
        return frames[max(0, min(len(frames) - 1, int(round(t_sec * sr))))]

    pl = {}                       # sid -> {n, dist_sum, iso}
    clumped = {"A": 0, "B": 0}

    def P(sid):
        return pl.setdefault(sid, {"n": 0, "dist_sum": 0.0, "iso": 0})

    for rnum, dlist in deaths_by_round.items():
        dl = sorted(dlist, key=lambda x: x["tick"])
        tbr = team_by_round.get(rnum, {})
        for j, d in enumerate(dl):
            vic = d.get("vic")
            vteam = tbr.get(vic)
            if not vic or vteam not in (2, 3) or d.get("vx") is None:
                continue
            fr = frame_at(d["tick"] / tickrate)
            vri = sid_ridx.get(vic, -1)
            nearest = 1e9
            for i, plr in enumerate(fr["players"]):
                if (not plr or i == vri or not plr.get("alive")
                        or plr.get("team") != vteam or plr.get("x") is None):
                    continue
                nearest = min(nearest, math.hypot(plr["x"] - d["vx"], plr["y"] - d["vy"]))
            if nearest < 1e8:
                a = P(vic)
                a["n"] += 1
                a["dist_sum"] += nearest
                if nearest > ISO_DIST:
                    a["iso"] += 1
            # clumped: another same-team death close in time AND space
            tid = teamof.get(vic)
            if tid:
                for d2 in dl:
                    if d2 is d or d2.get("vx") is None or teamof.get(d2.get("vic")) != tid:
                        continue
                    if abs(d2["tick"] - d["tick"]) <= clump_ticks and \
                            math.hypot(d2["vx"] - d["vx"], d2["vy"] - d["vy"]) <= CLUMP_DIST:
                        clumped[tid] += 1
                        break

    out = {}
    for tid, sids in teams.items():
        if not sids:
            continue
        players, tot_n, tot_sum, tot_iso = [], 0, 0.0, 0
        for sid in sids:
            a = pl.get(sid)
            if a and a["n"]:
                players.append({"steamid": sid, "name": names.get(sid, sid),
                                "avg_support_dist": round(a["dist_sum"] / a["n"]),
                                "deaths_measured": a["n"], "isolated": a["iso"]})
                tot_n += a["n"]
                tot_sum += a["dist_sum"]
                tot_iso += a["iso"]
            else:
                players.append({"steamid": sid, "name": names.get(sid, sid),
                                "avg_support_dist": None, "deaths_measured": 0, "isolated": 0})
        players.sort(key=lambda p: (p["avg_support_dist"] is None, -(p["avg_support_dist"] or 0)))
        out[tid] = {"players": players,
                    "avg_support_dist": round(tot_sum / tot_n) if tot_n else None,
                    "isolated_deaths": tot_iso,
                    "clumped_deaths": clumped[tid]}
    return out


def build_team_play(deaths_by_round, team_by_round, names, replay, tickrate):
    """Combine the trade network + spacing per team -> {"A": {...}, "B": {...}}.

    Each team: {trade_network fields..., "spacing": {...}}. `replay` may be None (no frames)
    -> the trade network still computes; spacing is simply absent.
    """
    trade = build_trade_network(deaths_by_round, team_by_round, names, tickrate)
    spacing = build_spacing(deaths_by_round, team_by_round, names, replay, tickrate)
    out = {}
    for tid, tn in trade.items():
        out[tid] = dict(tn)
        out[tid]["spacing"] = spacing.get(tid)
    return out
