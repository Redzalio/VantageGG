"""
analytics.py -- compute the coaching/analytics layer from a CS2 .dem.

Emits an `analytics` dict (merged into the demo JSON by app.py) with, per player:
core stats (K/A/D, ADR, KAST, HLTV 2.0-equiv, Impact, KPR/DPR, HS%, UDR), opening
duels, trades (5s window), multi-kills, flashes, plus a Leetify-style rating
decomposition, role inference, map-zone K/D, benchmark comparison, and an
auto-generated "what you did wrong" insight feed (each with round+tick deep-link).

Standalone:  python analytics.py <demo.dem> [out.json]

Field names validated against a real CS2 demo (see ANALYTICS_SPEC.md Sec 1).
"""
import json
import math
import sys
from collections import defaultdict

import pandas as pd
from demoparser2 import DemoParser

from schema import ANALYTICS_VERSION   # bump to invalidate cached analytics only (shared module)
import context_rating                                                              # transparent rating
import subratings                                                                  # Aim/Util/Pos pillars
import teamplay                                                                    # trade network + spacing
import roles                                                                       # multi-label role model
import utilrating                                                                  # two-tier util rating
import positions                                                                   # per-callout breakdown
from roundlib import classify_buy, is_util_damage_weapon, pair_rounds, winner_str  # pure helpers

TICKRATE = 64
TRADE_WINDOW = 5.0           # seconds (loose, Leetify default)
TRADE_TICKS = int(TRADE_WINDOW * TICKRATE)
TRADE_TICKS_1S = int(1.0 * TICKRATE)   # strict trade window (HLTV/Leetify "fast trade")
ISO_DIST = 1000.0            # units: "isolated" if nearest teammate farther than this
TRADE_DIST = 600.0          # units: a trade was geometrically possible within this

# HLTV 2.0 regression (awpy-verified); label "2.0-equiv".
def hltv2(kast, kpr, dpr, impact, adr):
    return (0.00738764 * kast + 0.35912389 * kpr - 0.5329508 * dpr
            + 0.2372603 * impact + 0.0032397 * adr + 0.15872723)

def impact_rating(kpr, apr):
    return 2.13 * kpr + 0.42 * apr - 0.41


def credit_damage(hits, max_hp=100.0):
    """Credit each hit only up to the victim's REMAINING HP this round -- the engine's "damage
    done" definition. `player_hurt.dmg_health` is the *rolled* weapon damage, so summing it
    overshoots on killing blows / overkill / multi-attacker rounds (validated: that ran ADR ~5%
    above the official scoreboard). Process ONE ROUND's hits; yields (hit, credited) in tick order.
    hits: dicts with at least 'tick', 'vic', 'dmg'. Any hit (incl. teammate/self) still depletes
    the victim's HP so later hits credit correctly; the caller decides what to attribute."""
    hp = {}
    for h in sorted(hits, key=lambda x: x["tick"]):
        rem = hp.get(h["vic"], max_hp)
        credited = max(0.0, min(float(h.get("dmg") or 0), rem))
        hp[h["vic"]] = rem - credited
        yield h, credited

# FACEIT-10 / pro benchmarks (solid-level target) for percentile-ish flags.
BENCH = {"hltv": 1.05, "adr": 80, "kast": 70, "kpr": 0.68, "dpr": 0.64,
         "hs": 50, "open_wr": 52, "udr": 8, "trade_pct": 20, "kd": 1.1}


def _sid(v):
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return None
        return str(int(v))
    except (ValueError, TypeError):
        s = str(v)
        return s if s and s != "nan" else None


def _df(v):
    """Normalize demoparser outputs to a DataFrame.

    Some event APIs return a DataFrame on most demos but a plain list on others.
    Analytics code expects DataFrame helpers like itertuples/sort_values.
    """
    if v is None:
        return pd.DataFrame()
    if hasattr(v, "itertuples"):
        return v
    if isinstance(v, list):
        return pd.DataFrame(v)
    return pd.DataFrame(v)


def build_rounds(parser):
    """Return [{num,start,freeze_end,end,winner,reason}] in tick units (robust pairing)."""
    def ev(name):
        try:
            return _df(parser.parse_event(name))
        except Exception:
            return pd.DataFrame()
    rs = ev("round_start"); rfe = ev("round_freeze_end"); rend = ev("round_end")
    starts = [int(t) for t in rs["tick"]] if len(rs) else []
    freezes = [int(t) for t in rfe["tick"]] if len(rfe) else []
    end_rows = rend.sort_values("tick").to_dict("records") if len(rend) else []
    return pair_rounds(starts, freezes, end_rows)


def round_of(tick, rounds):
    r = None
    for rd in rounds:
        if tick >= rd["start"]:
            r = rd
        else:
            break
    return r


def _side_avg_equip(rnum, team, team_by_round, econ_by_round):
    es = _side_equips(rnum, team, team_by_round, econ_by_round)
    return (sum(es) / len(es)) if es else 0.0


def _side_equips(rnum, team, team_by_round, econ_by_round):
    """Per-player freeze-end equip values for one side that round (for buy-shape modifiers)."""
    return [econ_by_round.get(rnum, {}).get(s, {}).get("equip", 0)
            for s, t in team_by_round.get(rnum, {}).items() if t == team]


# Buy-shape modifier thresholds (freeze-end equip $, APPROXIMATE -- see CS2_ECONOMY_REFERENCE.md).
_HERO_EQUIP = 3000      # a player at/above this has a real rifle/AWP-level kit
_MIXED_SPREAD = 2500    # equip gap (richest - poorest) this large inside a team = a broken/mixed buy


def _is_mixed(equips, is_pistol):
    """Broken team buy: someone bought up while a teammate saved (big equip spread). Not on pistols."""
    return (not is_pistol) and len(equips) >= 2 and (max(equips) - min(equips)) >= _MIXED_SPREAD


def _is_hero(team_buy, equips, is_pistol):
    """Lone hero/saved weapon: team is eco/light overall but >=1 player has a rifle/AWP-level kit."""
    return (not is_pistol) and team_buy in ("eco", "light") and any(e >= _HERO_EQUIP for e in equips)


def compute_round_buys(rounds, team_by_round, econ_by_round, have_econ):
    """Per-round buy type for each side (pistol/eco/force/full + anti-eco), half/OT aware.

    Pistol rounds = round 1 plus any round where the sides flipped from the prior round AND
    both teams are on low equipment (a real reset, not an OT full-buy half). Returns
    {rnum: {ct,t,equip_ct,equip_t,anti_eco_ct,anti_eco_t,pistol}}.
    """
    n = len(rounds)
    pistols = {1}
    base = team_by_round.get(1, {})
    for rnum in range(2, n + 1):
        cur = team_by_round.get(rnum, {})
        common = [s for s in base if s in cur]
        flipped = bool(common) and sum(1 for s in common if base[s] != cur[s]) >= 0.6 * len(common)
        low = (max(_side_avg_equip(rnum, 2, team_by_round, econ_by_round),
                   _side_avg_equip(rnum, 3, team_by_round, econ_by_round)) < 1500) if have_econ else flipped
        if flipped and low:
            pistols.add(rnum)
        if flipped:
            base = cur
    out = {}
    for rd in rounds:
        rnum = rd["num"]
        pis = rnum in pistols
        eqc = _side_equips(rnum, 3, team_by_round, econ_by_round)
        eqt = _side_equips(rnum, 2, team_by_round, econ_by_round)
        ec = (sum(eqc) / len(eqc)) if eqc else 0.0
        et = (sum(eqt) / len(eqt)) if eqt else 0.0
        # CT full costs more than T full (kits + costlier util) -> side-aware threshold
        bc = classify_buy(ec, pis, side="CT") if have_econ else ("pistol" if pis else "unknown")
        bt = classify_buy(et, pis, side="T") if have_econ else ("pistol" if pis else "unknown")
        out[rnum] = {"ct": bc, "t": bt, "equip_ct": round(ec), "equip_t": round(et),
                     "pistol": pis,
                     "anti_eco_ct": bc == "full" and bt in ("eco", "pistol"),
                     "anti_eco_t": bt == "full" and bc in ("eco", "pistol"),
                     # buy-shape modifiers (only when we have econ): team buy discipline signals
                     "mixed_ct": have_econ and _is_mixed(eqc, pis),
                     "mixed_t": have_econ and _is_mixed(eqt, pis),
                     "hero_ct": have_econ and _is_hero(bc, eqc, pis),
                     "hero_t": have_econ and _is_hero(bt, eqt, pis)}
    return out


def _player_buy(rnum, sid, team_by_round, econ_by_round, round_buy):
    """A single player's buy label that round (their own equip; pistol + side from the round)."""
    team = team_by_round.get(rnum, {}).get(sid)
    pis = round_buy.get(rnum, {}).get("pistol", False)
    eq = econ_by_round.get(rnum, {}).get(sid, {}).get("equip")
    side = "CT" if team == 3 else ("T" if team == 2 else None)
    return classify_buy(eq, pis, side=side)


def _eco_factor(roster, team_by_round, econ_by_round, n_rounds):
    """Per-player mean (enemy avg equip / own equip) over rounds played -- the eco-difficulty
    each player faced. ~1.0 neutral; >1 = consistently out-gunned. Drives the Context Rating's
    eco adjustment. Returns {sid: factor}; 1.0 when equip data is unavailable."""
    fac = {}
    for sid in roster:
        ratios = []
        for rnum in range(1, n_rounds + 1):
            team = team_by_round.get(rnum, {}).get(sid)
            if team not in (2, 3):
                continue
            mine = econ_by_round.get(rnum, {}).get(sid, {}).get("equip", 0)
            if mine <= 0:
                continue
            enemy_team = 2 if team == 3 else 3
            ee = [e.get("equip", 0) for s2, e in econ_by_round.get(rnum, {}).items()
                  if team_by_round.get(rnum, {}).get(s2) == enemy_team and e.get("equip", 0) > 0]
            if not ee:
                continue
            ratios.append((sum(ee) / len(ee)) / mine)
        fac[sid] = round(sum(ratios) / len(ratios), 3) if ratios else 1.0
    return fac


def compute_splits(D, dmg_acc, kast_rounds, deaths_by_round, roster, team_by_round,
                   round_buy, econ_by_round, round_winner, n_rounds, tickrate):
    """Per-player CT/T side splits, buy-type splits, and deeper trade metrics.

    Returns {sid: {"sides": {...}, "buys": {...}, "trades": {...}}}. All from the same
    canonical events as the totals, so splits reconcile with the headline numbers.
    """
    SIDE = {3: "ct", 2: "t"}
    side_acc = {s: {"ct": {"k": 0, "d": 0, "dmg": 0.0, "kast": 0, "rounds": 0},
                    "t": {"k": 0, "d": 0, "dmg": 0.0, "kast": 0, "rounds": 0}} for s in roster}
    buy_acc = {s: defaultdict(lambda: {"rounds": 0, "k": 0, "d": 0, "won": 0}) for s in roster}
    trades = {s: {"traded_d_1s": 0, "traded_d_5s": 0, "trade_k_1s": 0, "trade_k_5s": 0,
                  "dist_sum": 0.0, "dist_n": 0} for s in roster}

    # rounds played + buy bucket (per side / per buy)
    for rnum in range(1, n_rounds + 1):
        for sid, team in team_by_round.get(rnum, {}).items():
            if sid not in roster:
                continue
            sd = SIDE.get(team)
            if sd:
                side_acc[sid][sd]["rounds"] += 1
            bt = _player_buy(rnum, sid, team_by_round, econ_by_round, round_buy)
            b = buy_acc[sid][bt]
            b["rounds"] += 1
            if sd and round_winner.get(rnum) == sd:
                b["won"] += 1

    # kills / deaths per side + per buy
    for d in D:
        rnum, atk, vic = d["round"], d["atk"], d["vic"]
        if atk and atk in roster and atk != vic:
            sd = SIDE.get(team_by_round.get(rnum, {}).get(atk))
            if sd:
                side_acc[atk][sd]["k"] += 1
            buy_acc[atk][_player_buy(rnum, atk, team_by_round, econ_by_round, round_buy)]["k"] += 1
        if vic and vic in roster:
            sd = SIDE.get(team_by_round.get(rnum, {}).get(vic))
            if sd:
                side_acc[vic][sd]["d"] += 1
            buy_acc[vic][_player_buy(rnum, vic, team_by_round, econ_by_round, round_buy)]["d"] += 1

    # damage per side (clamped per round/attacker/victim, matching ADR)
    for (rnum, atk, vic), dv in dmg_acc.items():
        if atk in roster:
            sd = SIDE.get(team_by_round.get(rnum, {}).get(atk))
            if sd:
                side_acc[atk][sd]["dmg"] += min(dv, 100.0)

    # KAST per side
    for sid in roster:
        for rnum in kast_rounds.get(sid, ()):
            sd = SIDE.get(team_by_round.get(rnum, {}).get(sid))
            if sd:
                side_acc[sid][sd]["kast"] += 1

    # deeper trades: 1s (strict) + 5s (loose) windows + average trade distance
    for rnum, dl in deaths_by_round.items():
        dl = sorted(dl, key=lambda x: x["tick"])
        for d in dl:
            vic, atk, tk = d["vic"], d["atk"], d["tick"]
            if not vic or vic not in roster:
                continue
            vteam = team_by_round.get(rnum, {}).get(vic)
            for d2 in dl:
                if d2["tick"] <= tk:
                    continue
                dt = d2["tick"] - tk
                if dt > TRADE_TICKS:
                    break
                if d2["vic"] == atk and d2["atk"] and team_by_round.get(rnum, {}).get(d2["atk"]) == vteam:
                    trades[vic]["traded_d_5s"] += 1
                    if d2["atk"] in roster:
                        trades[d2["atk"]]["trade_k_5s"] += 1
                    if dt <= TRADE_TICKS_1S:
                        trades[vic]["traded_d_1s"] += 1
                        if d2["atk"] in roster:
                            trades[d2["atk"]]["trade_k_1s"] += 1
                    if d.get("vx") is not None and d2.get("ax") is not None and d2["atk"] in roster:
                        trades[d2["atk"]]["dist_sum"] += math.hypot(d2["ax"] - d["vx"], d2["ay"] - d["vy"])
                        trades[d2["atk"]]["dist_n"] += 1
                    break

    out = {}
    for sid in roster:
        sides = {}
        for sd in ("ct", "t"):
            a = side_acc[sid][sd]
            rp = a["rounds"]
            sides[sd] = {"rounds": rp, "k": a["k"], "d": a["d"],
                         "adr": round(a["dmg"] / rp, 1) if rp else 0,
                         "kast": round(100.0 * a["kast"] / rp, 1) if rp else 0,
                         "kd": round(a["k"] / a["d"], 2) if a["d"] else a["k"]}
        buys = {}
        for bt, b in buy_acc[sid].items():
            if b["rounds"]:
                buys[bt] = {"rounds": b["rounds"], "k": b["k"], "d": b["d"],
                            "kpr": round(b["k"] / b["rounds"], 2),
                            "win_pct": round(100.0 * b["won"] / b["rounds"], 1)}
        t = trades[sid]
        out[sid] = {"sides": sides, "buys": buys,
                    "trades": {"trade_k_1s": t["trade_k_1s"], "trade_k_5s": t["trade_k_5s"],
                               "traded_d_1s": t["traded_d_1s"], "traded_d_5s": t["traded_d_5s"],
                               "avg_trade_dist": round(t["dist_sum"] / t["dist_n"]) if t["dist_n"] else None}}
    return out


# ============================ P3: Leetify-style coaching ======================
# A transparent, fully-explainable layer. NOT the official Leetify/HLTV model -- every
# number here is derived from a simple model we control and is labelled "approx".

def _winprob_ct(ct_alive, t_alive, bomb_planted):
    """Approximate P(CT wins) from man-advantage + bomb state (transparent logistic)."""
    if ct_alive <= 0:
        return 0.0
    if t_alive <= 0:
        return 1.0
    x = 0.9 * (ct_alive - t_alive) - (1.1 if bomb_planted else 0.0)
    return 1.0 / (1.0 + math.exp(-x))


def compute_round_swing(deaths_by_round, team_by_round, rounds, plant_by_round, roster):
    """Per-kill win-probability swing -> per-player impact, swing-by-category, round stories.

    Walks each round's kills + bomb plant in order, recomputing an approximate win
    probability after each event and attributing the change to the actor. Returns:
      swing[sid]          net win-prob points the player shifted toward their side
      cat[sid][category]  that swing split into Opening / Trading / Firepower
      story[rnum]         top swing moments (for the round card)
      rimpact[rnum]       total swing magnitude in the round (how decisive it was)
    """
    swing = {s: 0.0 for s in roster}
    cat = {s: defaultdict(float) for s in roster}
    story, rimpact = {}, {}
    for rd in rounds:
        rnum = rd["num"]
        tbr = team_by_round.get(rnum, {})
        alive = {2: sum(1 for t in tbr.values() if t == 2),
                 3: sum(1 for t in tbr.values() if t == 3)}
        if alive[2] == 0 or alive[3] == 0:
            continue
        dl = sorted(deaths_by_round.get(rnum, []), key=lambda x: x["tick"])
        opening_tick = dl[0]["tick"] if dl else None
        opening_atk = dl[0]["atk"] if dl else None
        events = [(d["tick"], "kill", d) for d in dl]
        pt = plant_by_round.get(rnum)
        if pt is not None:
            events.append((pt, "plant", None))
        events.sort(key=lambda e: e[0])
        planted = False
        p_before = _winprob_ct(alive[3], alive[2], planted)
        moments, recent, total_abs = [], [], 0.0
        for tick, kind, d in events:
            if kind == "plant":
                planted = True
            else:
                vt = tbr.get(d["vic"])
                if vt in (2, 3) and alive[vt] > 0:
                    alive[vt] -= 1
            p_after = _winprob_ct(alive[3], alive[2], planted)
            delta = p_after - p_before          # +ve favours CT
            total_abs += abs(delta)
            if kind == "kill" and d["atk"] and d["atk"] in roster and d["atk"] != d["vic"]:
                at = tbr.get(d["atk"])
                s_for = delta if at == 3 else -delta     # toward killer's side
                swing[d["atk"]] += s_for
                is_open = (tick == opening_tick and d["atk"] == opening_atk)
                is_trade = any((tick - rt) <= TRADE_TICKS and rvt == at and rv != d["vic"]
                               for (rt, rv, rvt) in recent)
                c = "Opening" if is_open else ("Trading" if is_trade else "Firepower")
                cat[d["atk"]][c] += s_for
                if is_open and d["vic"] in roster:    # opening death drags the victim's Opening down
                    vt2 = tbr.get(d["vic"])
                    cat[d["vic"]]["Opening"] += (delta if vt2 == 3 else -delta)
                moments.append({"tick": tick, "type": "kill", "atk": d["atk"], "vic": d["vic"],
                                "swing": round(abs(delta) * 100, 1), "for": "ct" if at == 3 else "t", "cat": c})
            elif kind == "plant":
                moments.append({"tick": tick, "type": "plant", "swing": round(abs(delta) * 100, 1)})
            if kind == "kill":
                recent.append((tick, d["vic"], tbr.get(d["vic"])))
            p_before = p_after
        story[rnum] = sorted(moments, key=lambda m: -m["swing"])[:3]
        rimpact[rnum] = round(total_abs * 100, 1)
    return swing, cat, story, rimpact


def compute_clutches(deaths_by_round, team_by_round, round_winner, roster, n_rounds):
    """Detect 1vX clutch situations (last player alive on their side) + outcome."""
    out = {s: {"won": 0, "lost": 0, "attempts": 0, "by_x": defaultdict(lambda: [0, 0])} for s in roster}
    for rnum in range(1, n_rounds + 1):
        tbr = team_by_round.get(rnum, {})
        if not tbr:
            continue
        alive = {2: set(), 3: set()}
        for sid, tm in tbr.items():
            if tm in (2, 3):
                alive[tm].add(sid)
        clutcher = {2: None, 3: None}
        for d in sorted(deaths_by_round.get(rnum, []), key=lambda x: x["tick"]):
            vt = tbr.get(d["vic"])
            if vt in (2, 3) and d["vic"] in alive[vt]:
                alive[vt].discard(d["vic"])
                enemy = 3 if vt == 2 else 2
                if len(alive[vt]) == 1 and clutcher[vt] is None and len(alive[enemy]) >= 1:
                    clutcher[vt] = (next(iter(alive[vt])), len(alive[enemy]))
        for sd in (2, 3):
            if not clutcher[sd]:
                continue
            sid, x = clutcher[sd]
            if sid not in roster:
                continue
            won = round_winner.get(rnum) == ("ct" if sd == 3 else "t")
            c = out[sid]
            c["attempts"] += 1
            c["by_x"][x][0 if won else 1] += 1
            c["won" if won else "lost"] += 1
    return out


_CAT_FIX = {
    "Opening": "Stop taking dry opening duels -- peek with a flash/teammate or let the entry go first.",
    "Trading": "Play closer to teammates so you trade their deaths (and yours get traded).",
    "Firepower": "Win more of the fights you take: crosshair placement + pre-aim common angles.",
    "Clutch": "Slow down in clutches -- isolate 1v1s, use sound, play for time/bomb.",
    "Utility": "Throw more (and better) utility -- lineups for common smokes/flashes before contact.",
}
_DRILL = {
    "KAST": "Trade/refrag drills: hold angles within 600u of a teammate; aim_botz for consistency.",
    "Opening win%": "Prefire/peek practice on this map's common angles; review your entry timings.",
    "Traded death%": "Team spacing review -- replay your untraded deaths and reposition.",
    "ADR": "Spray-control + utility-damage routine (recoil_master, HE/molly lineups).",
    "Util dmg/rd": "Learn 2-3 HE/molly lineups per side for common stack spots.",
    "Opening": "Entry/support roles drill: who peeks first, who trades.",
    "Trading": "5-man spacing scrims; never peek more than one angle from support.",
    "Firepower": "Aim routine (aim_botz/recoil) + 1v1 prefire on map angles.",
    "Clutch": "1vX clutch scenarios on this map; practice time/bomb management.",
    "Utility": "Build a team utility book (default smokes/flashes) and rep executes.",
}


def build_breakdown(out_players, swing, swing_cat, clutches):
    """Attach a transparent 'rating gained & lost' breakdown + clutch record per player."""
    for p in out_players:
        sid = p["steamid"]
        cat = swing_cat.get(sid, {})
        cl = clutches.get(sid, {})
        p["round_swing"] = round(swing.get(sid, 0.0) * 100, 1)   # net win-prob pts contributed
        util_pts = min(p.get("udr", 0) / 2.0, 8.0) + p.get("enemy_flashed", 0) * 0.4 - p.get("team_flashed", 0) * 0.6
        b = {
            "Opening": round(cat.get("Opening", 0.0) * 100, 1),
            "Trading": round(cat.get("Trading", 0.0) * 100, 1),
            "Firepower": round(cat.get("Firepower", 0.0) * 100, 1),
            "Clutch": round(cl.get("won", 0) * 4.0 - cl.get("lost", 0) * 1.0, 1),
            "Utility": round(util_pts, 1),
        }
        p["impact_breakdown"] = b
        p["impact_score"] = round(sum(b.values()), 1)
        p["clutch"] = {"won": cl.get("won", 0), "lost": cl.get("lost", 0),
                       "attempts": cl.get("attempts", 0),
                       "by_x": {str(k): cl["by_x"][k] for k in sorted(cl.get("by_x", {}))}}


def build_focus(out_players, insights_by_sid, bench):
    """Top-5 actionable focus areas per player (benchmark gaps + worst impact + worst mistakes)."""
    for p in out_players:
        items = []
        gaps = [
            ("KAST", p["kast"], bench["kast"], "%"),
            ("Opening win%", p["open_wr"], bench["open_wr"], "%"),
            ("Traded death%", p["traded_pct"], bench["trade_pct"], "%"),
            ("ADR", p["adr"], bench["adr"], ""),
            ("Util dmg/rd", p["udr"], bench["udr"], ""),
        ]
        for name, val, bm, unit in gaps:
            if bm and val < bm * 0.9:
                items.append({"area": name, "value": val, "benchmark": bm, "unit": unit,
                              "severity": 3 if val < bm * 0.7 else 2,
                              "detail": f"{name} {val}{unit} vs ~{bm}{unit} target",
                              "fix": _DRILL.get(name, "Review relevant rounds.")})
        b = p.get("impact_breakdown", {})
        if b:
            area, vmin = min(b.items(), key=lambda kv: kv[1])
            if vmin < 0:
                items.append({"area": area, "value": vmin, "benchmark": None, "unit": "",
                              "severity": 3 if vmin < -6 else 2,
                              "detail": f"{area} is your biggest impact drain ({vmin})",
                              "fix": _CAT_FIX.get(area, "Review these rounds in replay.")})
        issues_only = [i for i in insights_by_sid.get(p["steamid"], []) if i.get("polarity") != "good"]
        for ic in sorted(issues_only, key=lambda x: -x["severity"])[:2]:
            items.append({"area": "Mistake", "value": None, "benchmark": None, "unit": "",
                          "severity": ic["severity"], "detail": ic["text"][:140],
                          "fix": "Watch the flagged round.",
                          "round": ic.get("round"), "tick": ic.get("tick"),
                          "confidence": ic.get("confidence", "med")})
        seen, uniq = set(), []
        for it in sorted(items, key=lambda x: -x["severity"]):
            k = it["area"] + it["detail"][:30]
            if k in seen:
                continue
            seen.add(k)
            uniq.append(it)
        p["focus"] = uniq[:5]


# Buy-strength tiers for the economy verdict (low number = poorer buy). "light" sits between a
# true eco and a force; "force"/"full" are the real-investment tiers. Unknown/pistol carry no story.
_BUY_RANK = {"eco": 0, "light": 1, "force": 2, "full": 3}


def _econ_verdict(buy_lose, buy_win, pistol):
    """Why the LOSING side's economy did (or didn't) explain the loss, given both sides' buy labels.

    Returns (verdict_key, note_str) where note_str is a short human line, or (None, None) when
    there's no economy story to tell (missing/unknown data, pistols, or an even matchup). Verdicts:
      eco_loss        -- loser ecoed into the winner's real buy (expected; not a mistake)
      anti_force_loss -- loser half/force-bought into the winner's full (over-committed an eco)
      lost_full_v_eco -- loser full-bought and STILL lost to an eco/lighter buy (notable; flag it)
      even            -- comparable buys, no economy excuse either way
    """
    # No story without econ on both sides, and pistols are a fair reset (no buy advantage).
    if pistol or buy_lose in (None, "unknown") or buy_win in (None, "unknown"):
        return None, None
    rl, rw = _BUY_RANK.get(buy_lose), _BUY_RANK.get(buy_win)
    if rl is None or rw is None:
        return None, None
    # Loser invested at least as much as the winner.
    if rl >= rw:
        if buy_lose == "full" and rw <= _BUY_RANK["light"]:
            # full-buy beaten by an eco/light buy -> the opposite of an economy excuse.
            return "lost_full_v_eco", "Lost a full-buy to an eco"
        return "even", None
    # Loser was out-bought. How badly under-invested were they?
    if buy_lose == "eco":
        return "eco_loss", "Lost an eco"
    # loser bought light/force into a richer buy (typically a full) -> an anti-force that didn't pay.
    return "anti_force_loss", "Anti-forced (half-buy vs full)"


def build_round_cards(rounds, round_story, deaths_by_round, team_by_round, plant_by_round,
                      round_buy, defuse_rounds, names, tickrate):
    """Per-round 'why won/lost' narrative with a watch-in-replay timestamp."""
    cards = []
    for rd in rounds:
        rnum = rd["num"]
        dl = sorted(deaths_by_round.get(rnum, []), key=lambda x: x["tick"])
        bits = []
        if dl and dl[0]["atk"]:
            o = dl[0]
            side = "CT" if team_by_round.get(rnum, {}).get(o["atk"]) == 3 else "T"
            bits.append(f"{names.get(o['atk'], '?')} ({side}) took first blood vs {names.get(o['vic'], '?')}")
        rb = round_buy.get(rnum, {})
        if rb.get("anti_eco_ct") or rb.get("anti_eco_t"):
            bits.append("anti-eco")
        elif rb.get("pistol"):
            bits.append("pistol round")
        if rb.get("mixed_ct") or rb.get("mixed_t"):
            bits.append("mixed/broken buy")
        if rb.get("hero_ct") or rb.get("hero_t"):
            bits.append("hero/saved weapon")
        if plant_by_round.get(rnum) is not None:
            bits.append("bomb defused" if rnum in defuse_rounds else "bomb planted")
        story = round_story.get(rnum, [])
        if story and story[0]["type"] == "kill":
            m = story[0]
            bits.append(f"{names.get(m['atk'], '?')} swung it +{m['swing']}%")
        # Economy verdict for the LOSING side: was this round lost before it started (eco /
        # anti-force) or is it a notable loss (full-buy beaten by an eco)? Guarded against
        # missing econ -- buy_ct/buy_t are "unknown"/None when have_econ was False.
        win = winner_str(rd.get("winner"))
        if win == "CT":
            buy_lose, buy_win = rb.get("t"), rb.get("ct")
        elif win == "T":
            buy_lose, buy_win = rb.get("ct"), rb.get("t")
        else:
            buy_lose = buy_win = None
        econ_verdict, econ_note = _econ_verdict(buy_lose, buy_win, rb.get("pistol", False))
        summary = "; ".join(bits) + "." if bits else "(no recorded duels)"
        # Fold the note into summary only when it adds something the bits don't already say
        # (the "anti-eco" bit already covers an anti-force/eco loss in human terms).
        if econ_note and "anti-eco" not in summary:
            summary = summary[:-1] + " — " + econ_note + "." if summary.endswith(".") \
                else summary + " — " + econ_note + "."
        cards.append({
            "round": rnum, "winner": rd["winner"], "reason": rd["reason"],
            "buy_ct": rb.get("ct"), "buy_t": rb.get("t"),
            "summary": summary,
            "econ_note": econ_note, "econ_verdict": econ_verdict,
            "watch_t": round(rd.get("freeze_end", rd["start"]) / tickrate, 2),
            "moments": story,
        })
    return cards


def build_team_review(out_players, round_buy, round_winner, n_rounds):
    """Team-level focus areas + a 3-5 item practice plan + buy-type outcomes."""
    from collections import Counter
    area_count = Counter()
    for p in out_players:
        for f in p.get("focus", []):
            if f.get("area") and f["area"] != "Mistake":
                area_count[f["area"]] += 1
    top_areas = [{"area": a, "players": c} for a, c in area_count.most_common(5)]
    plan = [{"focus": a, "players": c, "drill": _DRILL.get(a, "Review relevant rounds together.")}
            for a, c in area_count.most_common(5)]
    # team buy-type outcomes (did we convert ecos / win our full buys?)
    buys = {}
    for rnum in range(1, n_rounds + 1):
        rb = round_buy.get(rnum, {})
        w = round_winner.get(rnum)
        for side in ("ct", "t"):
            bt = rb.get(side)
            if not bt or bt == "unknown":
                continue
            d = buys.setdefault(bt, {"rounds": 0, "won": 0})
            d["rounds"] += 1
            if w == side:
                d["won"] += 1
    buy_summary = {bt: {"rounds": d["rounds"], "win_pct": round(100.0 * d["won"] / d["rounds"], 1)}
                   for bt, d in buys.items()}
    return {"top_areas": top_areas, "practice_plan": plan, "buy_outcomes": buy_summary}


# ----- P4: per-team coaching -------------------------------------------------
def _team_loss_reason(buy, lost_open, opening_traded, max_adv, planted, side):
    """Pick ONE primary reason a team lost a round (priority order, most-actionable first)."""
    if buy in ("eco", "pistol"):
        return "Lost on an eco/save"
    if max_adv >= 2:
        return "Threw a 2+ man advantage"
    if lost_open and not opening_traded:
        return "Opening death, no trade"
    if max_adv >= 1:
        return "Lost with a man up"
    if side == "t" and planted:
        return "Lost the post-plant"
    if side == "ct" and planted:
        return "Failed the retake"
    if buy == "full":
        return "Lost an even full-buy"
    return "Lost the gunfights"


_TEAM_DRILL = {
    "Threw a 2+ man advantage": "Play closes/retakes slow and together; don't refrag into a stack.",
    "Lost with a man up": "When up a player, trade in pairs and take map control with utility, not aim.",
    "Opening death, no trade": "Entry + trade pairs -- never let first contact go unsupported.",
    "Lost the post-plant": "Default post-plants: crossfires on the bomb, save utility for the retake.",
    "Failed the retake": "Coordinated retakes -- flash/util together, hit on a count, trade the first death.",
    "Lost an even full-buy": "Rep set executes and defaults; win full-buys on structure, not pure aim.",
    "Lost on an eco/save": "Commit fully or save fully -- stack a site on eco, don't half-force.",
    "Lost the gunfights": "Aim + crosshair-placement routine; pick better fights with utility.",
}


def build_team_coaching(out_players, deaths_by_round, team_by_round, round_buy, round_winner,
                        plant_by_round, defuse_rounds, names, n_rounds):
    """Per-team review: loss taxonomy, entry, economy, post-plant/retake, death zones, plan.

    Teams are defined by round-1 sides (they persist across the halftime swap). Everything is
    computed from the same canonical events as the rest of analytics.
    """
    base = team_by_round.get(1, {})
    rosters = {"A": [s for s, t in base.items() if t == 3],   # started CT
               "B": [s for s, t in base.items() if t == 2]}   # started T
    pbysid = {p["steamid"]: p for p in out_players}
    teams = []
    for tid, sids in rosters.items():
        if not sids:
            continue
        sidset = set(sids)
        won = lost = entry_att = entry_won = deaths_total = 0
        pp_n = pp_w = rt_n = rt_w = 0
        loss = defaultdict(lambda: {"count": 0, "rounds": []})
        econ = defaultdict(lambda: {"rounds": 0, "won": 0})
        zones = defaultdict(int)
        for rnum in range(1, n_rounds + 1):
            tbr = team_by_round.get(rnum, {})
            present = [s for s in sidset if s in tbr]
            if not present:
                continue
            side_num = tbr.get(present[0])
            if side_num not in (2, 3):
                continue
            side, enemy_num = ("ct", 2) if side_num == 3 else ("t", 3)
            win = round_winner.get(rnum) == side
            won += win
            lost += not win
            buy = round_buy.get(rnum, {}).get(side) or "unknown"
            e = econ[buy]; e["rounds"] += 1; e["won"] += win
            dl = sorted(deaths_by_round.get(rnum, []), key=lambda x: x["tick"])
            if dl and (dl[0]["atk"] in sidset or dl[0]["vic"] in sidset):
                entry_att += 1
                entry_won += dl[0]["atk"] in sidset
            alive = {2: sum(1 for t in tbr.values() if t == 2),
                     3: sum(1 for t in tbr.values() if t == 3)}
            max_adv = alive[side_num] - alive[enemy_num]
            for d in dl:
                if d["vic"] in sidset:
                    deaths_total += 1
                    if d.get("place"):
                        zones[(d["place"], side)] += 1
                vt = tbr.get(d["vic"])
                if vt in (2, 3) and alive[vt] > 0:
                    alive[vt] -= 1
                max_adv = max(max_adv, alive[side_num] - alive[enemy_num])
            planted = plant_by_round.get(rnum) is not None
            if planted and side == "t":
                pp_n += 1; pp_w += win
            elif planted and side == "ct":
                rt_n += 1; rt_w += win
            if not win:
                lost_open = bool(dl) and dl[0]["vic"] in sidset
                op_traded = False
                if lost_open:
                    ft, atk = dl[0]["tick"], dl[0]["atk"]
                    op_traded = any(d2["tick"] > ft and d2["tick"] - ft <= TRADE_TICKS
                                    and d2["vic"] == atk and tbr.get(d2["atk"]) == side_num for d2 in dl)
                reason = _team_loss_reason(buy, lost_open, op_traded, max_adv, planted, side)
                loss[reason]["count"] += 1
                loss[reason]["rounds"].append(rnum)
        traded = sum(pbysid.get(s, {}).get("traded_d", 0) for s in sidset)
        loss_list = sorted(({"reason": k, **v} for k, v in loss.items()), key=lambda x: -x["count"])
        econ_out = {k: {"rounds": v["rounds"], "win_pct": round(100 * v["won"] / v["rounds"], 1)}
                    for k, v in econ.items() if v["rounds"]}
        top_zones = sorted(({"zone": z, "side": sd, "deaths": n} for (z, sd), n in zones.items()),
                           key=lambda x: -x["deaths"])[:6]
        roster = sorted((pbysid[s] for s in sids if s in pbysid),
                        key=lambda p: -p.get("impact_score", 0))
        roles = [{"name": p["name"], "ct": p.get("ct_role", "--"), "t": p.get("t_role", "--"),
                  "open_wr": p.get("open_wr", 0), "impact": p.get("impact_score", 0)} for p in roster]
        plan = []
        for lr in loss_list[:3]:
            if lr["reason"] == "Lost on an eco/save":
                continue
            plan.append({"focus": lr["reason"], "rounds": lr["rounds"][:6],
                         "drill": _TEAM_DRILL.get(lr["reason"], "Review these rounds together.")})
        full = econ_out.get("full", {})
        if full.get("rounds", 0) >= 6 and full.get("win_pct", 100) < 45:
            plan.append({"focus": "Full-buy conversion", "rounds": [],
                         "drill": "Your full-buys underperform -- rep set executes/defaults."})
        teams.append({
            "id": tid, "start_side": "CT" if tid == "A" else "T",
            "name": (roster[0]["name"] + "'s team") if roster else ("Team " + tid),
            "players": [p["name"] for p in roster],
            "won": won, "lost": lost,
            "trade_pct": round(100 * traded / deaths_total, 1) if deaths_total else 0,
            "entry": {"attempts": entry_att, "won": entry_won,
                      "wr": round(100 * entry_won / entry_att, 1) if entry_att else 0},
            "post_plant": {"n": pp_n, "wr": round(100 * pp_w / pp_n, 1) if pp_n else None},
            "retake": {"n": rt_n, "wr": round(100 * rt_w / rt_n, 1) if rt_n else None},
            "loss_reasons": loss_list,
            "economy": econ_out,
            "top_death_zones": top_zones,
            "roles": roles,
            "practice_plan": plan[:5],
        })
    return {"teams": teams}


# ----- P7: advanced high-level insights --------------------------------------
def build_advanced_insights(insights, D, deaths_by_round, team_by_round, round_buy,
                            econ_by_round, replay, flash_events, n_rounds, tickrate):
    """Dry-peek, predictable spots, clumping, economy discipline, aim (moving-shot %).
    Merges into the per-steamid `insights` dict. All transparent/heuristic -> confidence-labelled."""
    frames = (replay or {}).get("frames", [])
    sr = (replay or {}).get("sample_rate", 8)
    rp = (replay or {}).get("players", [])
    fw = int(2.5 * tickrate)

    def add(sid, ins):
        insights.setdefault(sid, []).append(ins)

    def frame_at(ts):
        return frames[max(0, min(len(frames) - 1, int(round(ts * sr))))] if frames else None

    # 1) dry opening deaths -- lost the round's first duel with (a) no friendly support flash in the
    #    prior 2.5s AND (b) the actual killer NOT blinded. Both checks => high-confidence "dry peek".
    dry = defaultdict(list)
    blind_ticks = int(1.5 * tickrate)
    for rnum, dl in deaths_by_round.items():
        if not dl:
            continue
        first = min(dl, key=lambda d: d["tick"])
        vic, killer = first["vic"], first["atk"]
        if not vic or not killer:
            continue
        vt = team_by_round.get(rnum, {}).get(vic)
        supported = any(0 <= first["tick"] - fe["tick"] <= fw
                        and team_by_round.get(rnum, {}).get(fe["fl"]) == vt
                        and team_by_round.get(rnum, {}).get(fe["vic"]) != vt
                        for fe in flash_events)
        killer_blinded = any(fe["vic"] == killer and 0 <= first["tick"] - fe["tick"] <= blind_ticks
                             for fe in flash_events)
        if not supported and not killer_blinded:
            dry[vic].append((rnum, first["tick"]))
    for sid, lst in dry.items():
        if len(lst) >= 3:
            add(sid, {"round": lst[0][0], "tick": lst[0][1], "type": "dry_opening", "severity": 2,
                      "confidence": "high", "polarity": "issue",
                      "text": f"Dry opening deaths x{len(lst)} -- you took the round's first duel with no "
                              f"support flash, against an un-flashed enemy. Pop a flash (or get flashed in) "
                              f"before peeking.",
                      "evidence": {"count": len(lst), "rounds": [r for r, _ in lst][:8],
                                   "note": "round's first death; no friendly flash in the prior 2.5s AND "
                                           "the killer was not blinded at the kill"}})

    # 2) predictable death spots -- same callout + side repeatedly
    spot = defaultdict(lambda: defaultdict(list))
    for d in D:
        vic, pl = d["vic"], d.get("place")
        if not vic or not pl:
            continue
        side = "CT" if team_by_round.get(d["round"], {}).get(vic) == 3 else "T"
        spot[vic][(pl, side)].append(d["round"])
    for sid, places in spot.items():
        worst = max(places.items(), key=lambda kv: len(kv[1]), default=None)
        if worst and len(worst[1]) >= 4:
            (pl, side), rs = worst
            add(sid, {"round": rs[0], "tick": None, "type": "predictable", "severity": 2,
                      "confidence": "high", "polarity": "issue",
                      "text": f"Predictable: you died at {pl} ({side}) {len(rs)}x -- they're pre-aiming you "
                              f"there. Vary your timing/position/peek.",
                      "evidence": {"place": pl, "side": side, "count": len(rs), "rounds": rs[:8]}})

    # 3) clumping / bad spacing -- 2+ teammates die within 4s and ~350u. Attribute a CAUSE:
    #    same enemy killed both / one nade got both / same spray window -> says *why* it was bad.
    clump = defaultdict(lambda: {"n": 0, "round": None, "causes": set()})
    for rnum, dl in deaths_by_round.items():
        d2 = [d for d in dl if d.get("vx") is not None]
        for i, a in enumerate(d2):
            at = team_by_round.get(rnum, {}).get(a["vic"])
            for b in d2[i + 1:]:
                if b["vic"] == a["vic"] or team_by_round.get(rnum, {}).get(b["vic"]) != at:
                    continue
                if not (abs(b["tick"] - a["tick"]) <= 4 * tickrate
                        and math.hypot(a["vx"] - b["vx"], a["vy"] - b["vy"]) <= 350):
                    continue
                if is_util_damage_weapon(a.get("weapon")) or is_util_damage_weapon(b.get("weapon")):
                    cause = "one nade caught both"
                elif a["atk"] and a["atk"] == b["atk"]:
                    cause = "same enemy killed both"
                else:
                    cause = "same spray window"
                for s in (a["vic"], b["vic"]):
                    clump[s]["n"] += 1
                    clump[s]["causes"].add(cause)
                    if clump[s]["round"] is None:
                        clump[s]["round"] = rnum
    for sid, info in clump.items():
        if info["n"] >= 3:
            causes = sorted(info["causes"])
            add(sid, {"round": info["round"], "tick": None, "type": "clumping", "severity": 2,
                      "confidence": "med", "polarity": "issue",
                      "text": f"Bad spacing x{info['n']} -- you died bunched with a teammate "
                              f"({', '.join(causes)}). Hold angles apart so one spray or nade can't kill two.",
                      "evidence": {"count": info["n"], "causes": causes,
                                   "note": "2+ teammates died within 4s and ~350u of each other"}})

    # 4) economy discipline -- bought (full/force) while the team was on eco
    broke = defaultdict(list)
    for rnum in range(1, n_rounds + 1):
        rb = round_buy.get(rnum, {})
        for sid, team in team_by_round.get(rnum, {}).items():
            side = "ct" if team == 3 else "t"
            if rb.get(side) == "eco" and econ_by_round.get(rnum, {}).get(sid, {}).get("equip", 0) > 2500:
                broke[sid].append(rnum)
    for sid, rs in broke.items():
        if len(rs) >= 3:
            add(sid, {"round": rs[0], "tick": None, "type": "eco_discipline", "severity": 1,
                      "confidence": "med", "polarity": "issue",
                      "text": f"Broke eco x{len(rs)} -- you bought while the team saved. Buy together so the "
                              f"team contests with full utility, not piecemeal.",
                      "evidence": {"count": len(rs), "rounds": rs[:8],
                                   "note": "bought >$2500 on a team eco round"}})

    # 5) aim approximation -- % of damage dealt while moving (counter-strafe proxy; 8-fps, low confidence)
    if frames and rp:
        dmg = replay.get("damages", [])
        mv = defaultdict(lambda: [0, 0])
        for d in dmg:
            atk = d.get("atk")
            if atk is None or atk < 0 or atk >= len(rp):
                continue
            f0, f1 = frame_at(d["t"] - 1.0 / sr), frame_at(d["t"])
            if not f0 or not f1:
                continue
            p0 = f0["players"][atk] if atk < len(f0["players"]) else None
            p1 = f1["players"][atk] if atk < len(f1["players"]) else None
            if not p0 or not p1:
                continue
            v = math.hypot(p1["x"] - p0["x"], p1["y"] - p0["y"]) * sr
            mv[rp[atk]["steamid"]][1] += 1
            if v > 90:
                mv[rp[atk]["steamid"]][0] += 1
        for sid, (m, tot) in mv.items():
            if tot >= 20 and m / tot > 0.35:
                add(sid, {"round": None, "tick": None, "type": "moving_shots", "severity": 2,
                          "confidence": "low", "polarity": "issue",
                          "text": f"~{round(100 * m / tot)}% of your damage was dealt while moving (approx) -- "
                                  f"counter-strafe to a stop before firing for accuracy.",
                          "evidence": {"metric": "moving_damage_pct", "value": round(100 * m / tot),
                                       "sample": tot, "note": "8-fps movement approximation; low confidence"}})


def compute_trade_opportunities(D, replay, roster, team_of, tickrate, trade_ticks):
    """Trade *opportunity* metrics (not just results): for each death, was there a realistic
    chance to trade -- a living teammate within TRADE_DIST of the victim at the moment of death?
    And was that chance converted (attacker dies to a teammate within the trade window)?

    Needs the replay frames for teammate positions. Returns per-sid:
      {chances, traded, failed, pct}  where pct = traded / chances (None if no chances).
    A high `failed` with a high `chances` = teammates in range but not refragging -> spacing/timing.
    """
    import bisect
    res = {s: {"chances": 0, "traded": 0, "failed": 0, "pct": None} for s in roster}
    frames = (replay or {}).get("frames") or []
    if not frames or not D:
        return res
    ftimes = [f["t"] for f in frames]
    # frame players are indexed by the REPLAY's roster order, which may differ from analytics'
    # roster -> map steamid -> frame index via replay["players"] (fall back to roster idx).
    ridx = {p.get("steamid"): i for i, p in enumerate((replay or {}).get("players") or [])}
    frame_idx = lambda sid: ridx.get(sid, roster.get(sid))   # noqa: E731
    by_round = defaultdict(list)
    for d in D:
        by_round[d["round"]].append(d)
    for d in D:
        vic, atk = d["vic"], d["atk"]
        if vic not in res or d.get("vx") is None or d.get("vy") is None:
            continue
        vteam = team_of(vic, d["round"])
        if vteam not in (2, 3):
            continue
        t = d["tick"] / tickrate
        i = bisect.bisect_left(ftimes, t)
        cand = [j for j in (i - 1, i, i + 1) if 0 <= j < len(frames)]
        if not cand:
            continue
        fr = frames[min(cand, key=lambda j: abs(ftimes[j] - t))]["players"]
        chance = False
        for sid2 in roster:
            if sid2 == vic or team_of(sid2, d["round"]) != vteam:
                continue
            j = frame_idx(sid2)
            p = fr[j] if (j is not None and j < len(fr)) else None
            if not p or not p.get("alive"):
                continue
            if math.hypot(p["x"] - d["vx"], p["y"] - d["vy"]) <= TRADE_DIST:
                chance = True
                break
        if not chance:
            continue
        res[vic]["chances"] += 1
        traded = any(d2["tick"] > d["tick"] and d2["tick"] - d["tick"] <= trade_ticks
                     and d2["vic"] == atk and d2["atk"] and team_of(d2["atk"], d["round"]) == vteam
                     for d2 in by_round[d["round"]])
        res[vic]["traded" if traded else "failed"] += 1
    for s in res:
        c = res[s]["chances"]
        res[s]["pct"] = round(100.0 * res[s]["traded"] / c, 1) if c else None
    return res


# threshold ~34% of the ~250 u/s run speed: a shot fired below it is "counter-strafed" / stopped.
COUNTER_STRAFE_STOP = 85.0


def _attach_aim(players, replay):
    """Aim-mechanics from per-shot data. Counter-strafe %: of a player's bullet shots that carry a
    velocity sample, the share fired while effectively stopped (speed < COUNTER_STRAFE_STOP) -- i.e.
    good trigger discipline. Only attached when shots have velocity (new parses); old demos skip it."""
    agg = {}   # roster idx -> [shots_with_vel, shots_stopped]
    for e in (replay.get("events") or []):
        if e.get("type") != "shot" or "vel" not in e:
            continue
        a = agg.setdefault(e.get("player"), [0, 0])
        a[0] += 1
        if e["vel"] < COUNTER_STRAFE_STOP:
            a[1] += 1
    for p in players:
        a = agg.get(p.get("index"))
        if a and a[0] >= 5:                      # need a few shots for the % to mean anything
            p["counter_strafe"] = round(100.0 * a[1] / a[0], 1)
            p["shots"] = a[0]


def analyze(parser, tickrate=TICKRATE, replay=None):
    # `replay` = parse_demo() output (frames/grenades). If given, role detection &
    # utility stats reuse it (no extra parsing). If None, we parse it ourselves.
    rounds = build_rounds(parser)
    n_rounds = len(rounds)
    if not n_rounds:
        raise RuntimeError("no rounds parsed")
    last_end = rounds[-1]["end"]

    # --- roster + per-round team via ticks at each freeze_end -----------------
    fz_ticks = sorted({r["freeze_end"] for r in rounds})
    # team + economy at each freeze-end (equip/balance props vary by build -> degrade safely)
    try:
        tdf = parser.parse_ticks(
            ["team_num", "last_place_name", "current_equip_value", "balance"], ticks=fz_ticks)
        have_econ = True
    except Exception:
        tdf = parser.parse_ticks(["team_num", "last_place_name"], ticks=fz_ticks)
        have_econ = False
    roster, names = {}, {}
    team_by_round = defaultdict(dict)   # round_num -> {sid: team_num}
    econ_by_round = defaultdict(dict)   # round_num -> {sid: {"equip":int,"money":int}}
    for row in tdf.itertuples(index=False):
        d = row._asdict()
        sid = _sid(d.get("steamid"))
        if sid is None:
            continue
        names[sid] = str(d.get("name"))
        if sid not in roster:
            roster[sid] = len(roster)
        rd = round_of(int(d["tick"]) + 1, rounds)
        if rd:
            team_by_round[rd["num"]][sid] = int(d.get("team_num") or 0)
            if have_econ:
                econ_by_round[rd["num"]][sid] = {
                    "equip": int(d.get("current_equip_value") or 0),
                    "money": int(d.get("balance") or 0)}
    players = [{"steamid": s, "name": names.get(s, s), "index": i}
               for s, i in sorted(roster.items(), key=lambda kv: kv[1])]

    def team_at(sid, rnum):
        return team_by_round.get(rnum, {}).get(sid, 0)

    # --- per-round buy classification (per side), pistol/half-aware --------------
    round_buy = compute_round_buys(rounds, team_by_round, econ_by_round, have_econ)

    # --- events --------------------------------------------------------------
    deaths = _df(parser.parse_event("player_death", player=["X", "Y", "last_place_name"]))
    hurts = _df(parser.parse_event("player_hurt"))
    try:
        blinds = _df(parser.parse_event("player_blind"))
    except Exception:
        blinds = pd.DataFrame()

    # flash events (tick, flasher sid, blinded sid) for the dry-peek detector
    flash_events = []
    for r in blinds.itertuples(index=False):
        b = r._asdict()
        fl, vic = _sid(b.get("attacker_steamid")), _sid(b.get("user_steamid"))
        if fl and vic and float(b.get("blind_duration") or 0) >= 0.7:
            flash_events.append({"tick": int(b["tick"]), "fl": fl, "vic": vic})

    # bomb plant/defuse ticks per round (for round-swing + round cards)
    plant_by_round, defuse_rounds = {}, set()
    for ename, target in (("bomb_planted", plant_by_round), ("bomb_defused", defuse_rounds)):
        try:
            bdf = _df(parser.parse_event(ename))
        except Exception:
            continue
        if not len(bdf):
            continue
        for r in bdf.itertuples(index=False):
            rd = round_of(int(r._asdict()["tick"]), rounds)
            if not rd:
                continue
            if ename == "bomb_planted":
                plant_by_round.setdefault(rd["num"], int(r._asdict()["tick"]))
            else:
                defuse_rounds.add(rd["num"])

    # normalize deaths
    D = []
    for r in deaths.itertuples(index=False):
        d = r._asdict()
        rd = round_of(int(d["tick"]), rounds)
        if not rd:
            continue
        D.append({
            "tick": int(d["tick"]), "round": rd["num"],
            "atk": _sid(d.get("attacker_steamid")), "vic": _sid(d.get("user_steamid")),
            "ast": _sid(d.get("assister_steamid")),
            "weapon": str(d.get("weapon") or ""), "hs": bool(d.get("headshot")),
            "vx": d.get("user_X"), "vy": d.get("user_Y"),
            "ax": d.get("attacker_X"), "ay": d.get("attacker_Y"),
            "place": str(d.get("user_last_place_name") or ""),
        })
    D.sort(key=lambda x: x["tick"])

    # per-player accumulators
    P = {s: defaultdict(float) for s in roster}
    for s in roster:
        P[s]["multi"] = defaultdict(int)
        P[s]["zones"] = defaultdict(lambda: [0, 0])   # place -> [kills, deaths]
        P[s]["insights"] = []
        P[s]["round_kills"] = defaultdict(int)

    # ADR via player_hurt -- credit each hit only up to the victim's remaining HP that round
    # (engine definition; see credit_damage). dmg_acc holds CREDITED (HP-capped) damage and is
    # reused for the side splits, so they're capped too (they previously summed the raw value).
    dmg_acc = defaultdict(float)   # (round, atk, vic) -> credited dmg
    if len(hurts):
        by_rnd = defaultdict(list)
        for r in hurts.itertuples(index=False):
            h = r._asdict()
            rd = round_of(int(h["tick"]), rounds)
            if not rd:
                continue
            by_rnd[rd["num"]].append({"tick": int(h["tick"]),
                                      "atk": _sid(h.get("attacker_steamid")),
                                      "vic": _sid(h.get("user_steamid")),
                                      "dmg": float(h.get("dmg_health") or 0),
                                      "util": is_util_damage_weapon(h.get("weapon"))})
        for rnum, hits in by_rnd.items():
            for h, credited in credit_damage(hits):   # depletes victim HP for ALL hits...
                atk, vic = h["atk"], h["vic"]
                if atk is None or vic is None or atk == vic:
                    continue
                if team_at(atk, rnum) == team_at(vic, rnum):
                    continue                            # ...but team/self damage isn't attributed
                dmg_acc[(rnum, atk, vic)] += credited
                if h["util"]:
                    P[atk]["util_dmg"] += credited
    for (rnum, atk, vic), dv in dmg_acc.items():
        if atk in P:
            P[atk]["dmg"] += dv                          # already HP-capped; no further clamp

    # flashes
    if len(blinds):
        flash_count = defaultdict(int); enemy_flashed = defaultdict(int)
        blind_time = defaultdict(float); team_flashed = defaultdict(int)
        for r in blinds.itertuples(index=False):
            b = r._asdict()
            fl = _sid(b.get("attacker_steamid")); vic = _sid(b.get("user_steamid"))
            dur = float(b.get("blind_duration") or 0)
            if fl is None or vic is None:
                continue
            rd = round_of(int(b["tick"]), rounds)
            rnum = rd["num"] if rd else -1
            if fl == vic:
                continue
            if team_at(fl, rnum) == team_at(vic, rnum):
                if dur >= 1.1:
                    team_flashed[fl] += 1
            else:
                if dur >= 1.1:
                    enemy_flashed[fl] += 1
                    blind_time[fl] += dur
        for s in roster:
            P[s]["enemy_flashed"] = enemy_flashed.get(s, 0)
            P[s]["blind_time"] = blind_time.get(s, 0.0)
            P[s]["team_flashed"] = team_flashed.get(s, 0)

    # kills / deaths / assists / opening / trades / multikills / zones
    deaths_by_round = defaultdict(list)
    for d in D:
        deaths_by_round[d["round"]].append(d)

    for d in D:
        if d["atk"] and d["atk"] in P and d["atk"] != d["vic"]:
            P[d["atk"]]["kills"] += 1
            if d["hs"]:
                P[d["atk"]]["hs"] += 1
            P[d["atk"]]["round_kills"][d["round"]] += 1
            if d["place"]:
                P[d["atk"]]["zones"][d["place"]][0] += 1
        if d["vic"] and d["vic"] in P:
            P[d["vic"]]["deaths"] += 1
            if d["place"]:
                P[d["vic"]]["zones"][d["place"]][1] += 1
        if d["ast"] and d["ast"] in P:
            P[d["ast"]]["assists"] += 1

    # opening duels + trades + KAST per round
    kast_rounds = defaultdict(set)   # sid -> set(rounds satisfying KAST)
    for rnum, dl in deaths_by_round.items():
        dl_sorted = sorted(dl, key=lambda x: x["tick"])
        # opening duel = first death
        if dl_sorted:
            first = dl_sorted[0]
            if first["atk"] and first["atk"] in P:
                P[first["atk"]]["open_k"] += 1
            if first["vic"] and first["vic"] in P:
                P[first["vic"]]["open_d"] += 1
        # trades
        for i, d in enumerate(dl_sorted):
            vic, atk, t = d["vic"], d["atk"], d["tick"]
            if not vic or vic not in P:
                continue
            traded = False
            for d2 in dl_sorted:
                if d2["tick"] <= t or d2["tick"] - t > TRADE_TICKS:
                    continue
                if d2["vic"] == atk and d2["atk"] and team_at(d2["atk"], rnum) == team_at(vic, rnum):
                    traded = True
                    if d2["atk"] in P:
                        P[d2["atk"]]["trade_k"] += 1
                    break
            if traded:
                P[vic]["traded_d"] += 1

    # KAST + rounds actually played. Skip rounds a player wasn't on a team (sub / disconnect /
    # late join) -- otherwise "didn't die -> survived" wrongly credits KAST for rounds they were
    # never in, and dividing every rate by n_rounds deflates a player who only played some rounds.
    rounds_played = {s: 0 for s in roster}
    for s, idx in roster.items():
        for rnum in range(1, n_rounds + 1):
            if team_at(s, rnum) not in (2, 3):
                continue
            rounds_played[s] += 1
            got = False
            # kill or assist this round?
            if P[s]["round_kills"].get(rnum, 0) > 0:
                got = True
            # died this round?
            died = any(d["vic"] == s for d in deaths_by_round.get(rnum, []))
            assisted = any(d["ast"] == s for d in deaths_by_round.get(rnum, []))
            if assisted:
                got = True
            if not died:
                got = True  # survived
            else:
                # traded death?
                for d in deaths_by_round.get(rnum, []):
                    if d["vic"] != s:
                        continue
                    for d2 in deaths_by_round.get(rnum, []):
                        if d2["tick"] > d["tick"] and d2["tick"] - d["tick"] <= TRADE_TICKS \
                           and d2["vic"] == d["atk"] and team_at(d2["atk"], rnum) == team_at(s, rnum):
                            got = True
            if got:
                kast_rounds[s].add(rnum)
            # multikills
        # finalize multikills
        for rnum, k in P[s]["round_kills"].items():
            if k >= 2:
                P[s]["multi"][min(k, 5)] += 1

    # --- side / buy-type / deeper-trade splits ------------------------------
    round_winner = {r["num"]: r["winner"].lower() for r in rounds if r["winner"]}
    splits = compute_splits(D, dmg_acc, kast_rounds, deaths_by_round, roster, team_by_round,
                            round_buy, econ_by_round, round_winner, n_rounds, tickrate)

    # --- P3: round-swing impact + clutches ----------------------------------
    swing, swing_cat, round_story, round_impact = compute_round_swing(
        deaths_by_round, team_by_round, rounds, plant_by_round, roster)
    clutches = compute_clutches(deaths_by_round, team_by_round, round_winner, roster, n_rounds)

    # --- assemble per-player output -----------------------------------------
    out_players = []
    for s, idx in sorted(roster.items(), key=lambda kv: kv[1]):
        a = P[s]
        # divide per-player rates by the rounds THIS player actually played (fall back to the
        # match total if team tracking was unavailable) -- fair to subs / late joiners.
        rp = rounds_played.get(s) or n_rounds
        kills, dths, ass = int(a["kills"]), int(a["deaths"]), int(a["assists"])
        kpr, dpr, apr = kills / rp, dths / rp, ass / rp
        adr = a["dmg"] / rp
        kast = 100.0 * len(kast_rounds[s]) / rp
        imp = impact_rating(kpr, apr)
        rating = hltv2(kast, kpr, dpr, imp, adr)
        open_k, open_d = int(a["open_k"]), int(a["open_d"])
        open_total = open_k + open_d
        zones = {z: {"k": v[0], "d": v[1]} for z, v in a["zones"].items()}
        out_players.append({
            "steamid": s, "name": names.get(s, s), "index": idx,
            "rounds_played": rounds_played.get(s, n_rounds),
            "kills": kills, "deaths": dths, "assists": ass,
            "kd": round(kills / dths, 2) if dths else kills,
            "adr": round(adr, 1), "kast": round(kast, 1),
            "hltv": round(rating, 2), "impact": round(imp, 2),
            "kpr": round(kpr, 2), "dpr": round(dpr, 2),
            "hs_pct": round(100.0 * a["hs"] / kills, 1) if kills else 0,
            "udr": round(a["util_dmg"] / rp, 1),
            "open_k": open_k, "open_d": open_d,
            "open_wr": round(100.0 * open_k / open_total, 1) if open_total else 0,
            "trade_k": int(a["trade_k"]), "traded_d": int(a["traded_d"]),
            "traded_pct": round(100.0 * a["traded_d"] / dths, 1) if dths else 0,
            "multi": {str(k): a["multi"][k] for k in sorted(a["multi"])},
            "enemy_flashed": int(a.get("enemy_flashed", 0)),
            "avg_blind": round(a.get("blind_time", 0) / max(1, a.get("enemy_flashed", 0)), 2),
            "team_flashed": int(a.get("team_flashed", 0)),
            "zones": zones,
            "sides": splits.get(s, {}).get("sides", {}),
            "buys": splits.get(s, {}).get("buys", {}),
            "trades": splits.get(s, {}).get("trades", {}),
        })

    # roles + utility usage (reuse replay frames/grenades; parse if not provided)
    if replay is None:
        try:
            import parser as _pp
            replay = _pp.parse_demo(parser)
        except Exception as e:
            print("  role/util: replay parse failed:", e); replay = None
    if replay:
        try:
            _attach_roles_util(out_players, replay, n_rounds)
        except Exception as e:
            print("  role/util compute failed:", e)
        try:
            _attach_aim(out_players, replay)
        except Exception as e:
            print("  aim-mechanics compute failed:", e)

    # trade *opportunity* metrics (needs replay frames for teammate positions at each death)
    try:
        topp = compute_trade_opportunities(D, replay, roster, team_at, tickrate, TRADE_TICKS)
        for op in out_players:
            op["trade_opp"] = topp.get(op["steamid"], {"chances": 0, "traded": 0,
                                                        "failed": 0, "pct": None})
    except Exception as e:
        print("  trade opportunities failed:", e)

    insights = build_insights(out_players, D, rounds, team_by_round, tickrate)
    if replay:
        try:
            _merge_position_insights(insights, D, rounds, team_by_round, replay, tickrate)
        except Exception as e:
            print("  position insights failed:", e)
    try:
        build_advanced_insights(insights, D, deaths_by_round, team_by_round, round_buy,
                                econ_by_round, replay, flash_events, n_rounds, tickrate)
    except Exception as e:
        print("  advanced insights failed:", e)
    _stamp_confidence(insights)

    # --- P3: rating breakdown, focus areas, round cards, team review ---------
    build_breakdown(out_players, swing, swing_cat, clutches)
    # HLTV-3.0-INSPIRED Context Rating: eco-adjusted, lobby-relative sub-ratings (transparent).
    try:
        for p in out_players:
            p["swing"] = round(swing.get(p["steamid"], 0.0), 1)
        eco_fac = _eco_factor(roster, team_by_round, econ_by_round, n_rounds)
        cr = context_rating.compute_context_rating(out_players, eco_fac)
        for p in out_players:
            p["context"] = cr.get(p["steamid"])
    except Exception as e:
        print("  context rating failed:", e)
    build_focus(out_players, insights, BENCH)
    # Aim/Utility/Positioning skill pillars + bands (absolute, benchmark-anchored; see subratings.py).
    # Additive + recomputable purely from these player fields, so the frontend also computes it
    # client-side for older caches that predate this field (no re-parse needed).
    try:
        sr = subratings.compute_subratings(out_players)
        for p in out_players:
            p["subratings"] = sr.get(p["steamid"])
    except Exception as e:
        print("  subratings failed:", e)
    # #50 two-tier util rating (volume x quality). Additive + recomputable from existing player
    # fields, so the frontend mirrors it for caches that predate this field (no re-parse needed).
    try:
        utilrating.attach(out_players)
    except Exception as e:
        print("  util rating failed:", e)
    # #62 per-callout breakdown (side split + opening involvement). Needs last_place_name (server
    # only), so the frontend falls back to the flat zone K/D for caches parsed before this field.
    try:
        positions.attach(out_players, D, deaths_by_round, team_by_round)
    except Exception as e:
        print("  position stats failed:", e)
    # learn callout centers: per-zone aggregate of victim death positions (place <-> vx,vy). Folded
    # into the global callout_samples table at index time so callouts sharpen as more demos arrive.
    try:
        import callout_learn
        position_samples = callout_learn.from_deaths(D)
    except Exception as e:
        print("  position samples failed:", e)
        position_samples = {}
    round_cards = build_round_cards(rounds, round_story, deaths_by_round, team_by_round,
                                    plant_by_round, round_buy, defuse_rounds, names, tickrate)
    team = build_team_review(out_players, round_buy, round_winner, n_rounds)
    team_coaching = build_team_coaching(out_players, deaths_by_round, team_by_round, round_buy,
                                        round_winner, plant_by_round, defuse_rounds, names, n_rounds)
    # #43 spacing & trade-network (per team). Reuses the replay frames if present (spacing);
    # the trade graph computes from deaths alone, so it works even on a frameless parse.
    try:
        team_play = teamplay.build_team_play(deaths_by_round, team_by_round, names, replay, tickrate)
    except Exception as e:
        print("  team play failed:", e)
        team_play = {}

    analytics = {
        "version": ANALYTICS_VERSION,
        "tickrate": tickrate, "n_rounds": n_rounds,
        "have_econ": have_econ,
        "players": out_players,
        "position_samples": position_samples,
        "rounds": [{"num": r["num"], "winner": r["winner"], "reason": r["reason"],
                    "start_t": round(r["start"] / tickrate, 2),
                    "end_t": round(r["end"] / tickrate, 2),
                    "buy_ct": round_buy.get(r["num"], {}).get("ct"),
                    "buy_t": round_buy.get(r["num"], {}).get("t"),
                    "equip_ct": round_buy.get(r["num"], {}).get("equip_ct"),
                    "equip_t": round_buy.get(r["num"], {}).get("equip_t"),
                    "impact": round_impact.get(r["num"], 0),
                    "pistol": round_buy.get(r["num"], {}).get("pistol", False)} for r in rounds],
        "round_cards": round_cards,
        "team": team,
        "team_coaching": team_coaching,
        "team_play": team_play,
        "insights": insights,
        "benchmarks": BENCH,
        "meta": {
            "analytics_version": ANALYTICS_VERSION,
            "exact": ["kills", "deaths", "assists", "adr", "kast", "hs_pct",
                      "open_k", "open_d", "trade_k", "traded_d", "sides", "multi"],
            "approx": ["hltv (2.0-equiv regression, not official HLTV)",
                       "impact", "roles (heuristic)",
                       "buy/eco (equip-value thresholds)" if have_econ else "buy/eco (UNAVAILABLE: no equip data in this demo)",
                       "zones (callout-based, not polygon-precise)",
                       "position insights (8-fps frames)"],
            "note": "Ratings, roles, buy types and zones are transparent approximations -- "
                    "not official HLTV/Leetify values. Treat them as directional.",
        },
    }
    return analytics


# confidence by insight type: "high" = directly observed event; "med" = aggregate/heuristic
# threshold; "low" = small sample or 8-fps position approximation.
_CONFIDENCE = {
    "untraded_opening_death": "high", "team_flashes": "high",
    "untraded_despite_support": "high", "multikills": "high",
    "weak_opening_duels": "med", "low_traded_deaths": "med", "low_utility": "med",
    "good_openings": "high", "good_spacing": "med", "good_utility": "high", "high_impact": "high",
    "pos": "med",
}


_CONF_REASON = {
    "high": "directly observed in the demo events",
    "med": "aggregate threshold / heuristic",
    "low": "small sample or 8-fps position approximation",
}


def _stamp_confidence(insights):
    """Ensure every insight carries a confidence label + reason, a polarity, and an evidence
    dict so the UI can show *why* a card was flagged and never oversells a claim."""
    for lst in insights.values():
        for ins in lst:
            conf = ins.setdefault("confidence", _CONFIDENCE.get(ins.get("type"), "med"))
            ins.setdefault("confidence_reason", _CONF_REASON.get(conf, _CONF_REASON["med"]))
            ins.setdefault("polarity", "issue")     # "issue" | "good"
            ev = ins.setdefault("evidence", {})
            # backfill at least the deep-link locator so EVERY insight carries evidence
            if not ev:
                if ins.get("round") is not None:
                    ev["round"] = ins["round"]
                if ins.get("tick") is not None:
                    ev["tick"] = ins["tick"]
                ev["type"] = ins.get("type")


def build_insights(out_players, D, rounds, team_by_round, tickrate):
    """Per-player coaching cards. Each carries a machine-readable `evidence` dict + a `polarity`
    ("issue" = what to fix, "good" = what went right) so the UI can show *why* it was flagged
    and balance criticism with positives. Round+tick stay for deep-linking the replay."""
    by_round = defaultdict(list)
    for d in D:
        by_round[d["round"]].append(d)
    insights = defaultdict(list)
    name_of = {p["steamid"]: p["name"] for p in out_players}
    win_s = round(TRADE_TICKS / tickrate, 1)

    # --- round-specific issues (deep-linkable) ------------------------------
    for rnum, dl in by_round.items():
        dl = sorted(dl, key=lambda x: x["tick"])
        if not dl:
            continue
        first = dl[0]
        vic = first["vic"]
        if vic:
            tnum = team_by_round.get(rnum, {}).get(vic, 0)
            traded = any(d2["tick"] > first["tick"] and d2["tick"] - first["tick"] <= TRADE_TICKS
                         and d2["vic"] == first["atk"]
                         and team_by_round.get(rnum, {}).get(d2["atk"], -1) == tnum
                         for d2 in dl)
            if not traded:
                insights[vic].append({
                    "round": rnum, "tick": first["tick"], "type": "untraded_opening_death",
                    "severity": 3, "polarity": "issue",
                    "text": f"R{rnum}: you took the opening death and weren't traded -- "
                            f"a free man-advantage for the enemy.",
                    "evidence": {"event": "opening_death", "victim": name_of.get(vic, vic),
                                 "attacker": name_of.get(first["atk"], first["atk"]),
                                 "round": rnum, "tick": first["tick"], "trade_window_s": win_s,
                                 "note": "first death of the round; no teammate refragged in the window"}})

    # --- aggregate issues + what-went-right per player -----------------------
    for p in out_players:
        sid = p["steamid"]
        if p["open_d"] >= 4 and p["open_wr"] < 45:
            insights[sid].append({"round": None, "tick": None, "type": "weak_opening_duels",
                "severity": 2, "polarity": "issue",
                "text": f"Opening duels: {p['open_k']}-{p['open_d']} ({p['open_wr']}% win). "
                        f"You're losing the fights you take first -- peek with utility/support "
                        f"or let an entry lead.",
                "evidence": {"metric": "open_wr", "value": p["open_wr"], "threshold": 45,
                             "sample": p["open_k"] + p["open_d"], "benchmark": 52}})
        if p["deaths"] and p["traded_pct"] < 50 and p["deaths"] >= 8:
            insights[sid].append({"round": None, "tick": None, "type": "low_traded_deaths",
                "severity": 2, "polarity": "issue",
                "text": f"Only {p['traded_pct']}% of your deaths were traded -- you die in spots "
                        f"teammates can't punish. Tighten spacing (stay <~600u from a teammate).",
                "evidence": {"metric": "traded_pct", "value": p["traded_pct"], "threshold": 50,
                             "sample": p["deaths"], "benchmark": 20}})
        to = p.get("trade_opp") or {}
        if (to.get("chances") or 0) >= 4 and to.get("pct") is not None and to["pct"] < 30:
            insights[sid].append({"round": None, "tick": None, "type": "untraded_despite_support",
                "severity": 2, "polarity": "issue",
                "text": f"A teammate was in trade range for {to['chances']} of your deaths but only "
                        f"{to['pct']}% were refragged ({to['failed']} unpunished). Time your peeks "
                        f"with theirs so a death buys a trade.",
                "evidence": {"metric": "trade_opp_pct", "value": to["pct"], "threshold": 30,
                             "chances": to["chances"], "traded": to["traded"], "failed": to["failed"],
                             "note": "a living teammate was within 600u at your death but didn't refrag in time"}})
        if p["team_flashed"] >= 3:
            insights[sid].append({"round": None, "tick": None, "type": "team_flashes",
                "severity": 1, "polarity": "issue",
                "text": f"You blinded teammates {p['team_flashed']}x (>=1.1s). Re-aim pop-flashes "
                        f"so they pop over your teammates' peek, not into their face.",
                "evidence": {"metric": "team_flashed", "value": p["team_flashed"], "threshold": 3}})
        if p["udr"] < 5 and p["kills"] >= 8:
            insights[sid].append({"round": None, "tick": None, "type": "low_utility",
                "severity": 1, "polarity": "issue",
                "text": f"Utility damage {p['udr']}/round (pro ~8-10). Throw HE/molotov into known "
                        f"spots before contact to chip free damage.",
                "evidence": {"metric": "udr", "value": p["udr"], "threshold": 5, "benchmark": 8}})

        # what went right -- positives keep review balanced (severity 0, polarity "good")
        ot = p["open_k"] + p["open_d"]
        if ot >= 4 and p["open_wr"] >= 60:
            insights[sid].append({"round": None, "tick": None, "type": "good_openings",
                "severity": 0, "polarity": "good",
                "text": f"Strong entrying: {p['open_k']}-{p['open_d']} opening duels "
                        f"({p['open_wr']}% win). You win space for the team.",
                "evidence": {"metric": "open_wr", "value": p["open_wr"], "benchmark": 52, "sample": ot}})
        if p["deaths"] >= 6 and p["traded_pct"] >= 70:
            insights[sid].append({"round": None, "tick": None, "type": "good_spacing",
                "severity": 0, "polarity": "good",
                "text": f"Great spacing: {p['traded_pct']}% of your deaths got traded -- you die "
                        f"where teammates can punish.",
                "evidence": {"metric": "traded_pct", "value": p["traded_pct"], "benchmark": 20}})
        if p["udr"] >= 8:
            insights[sid].append({"round": None, "tick": None, "type": "good_utility",
                "severity": 0, "polarity": "good",
                "text": f"Useful utility: {p['udr']} util dmg/round (pro ~8-10). Keep chipping with nades.",
                "evidence": {"metric": "udr", "value": p["udr"], "benchmark": 8}})
        multi3 = sum(int(p["multi"].get(k, 0)) for k in ("3", "4", "5"))
        if multi3 >= 1:
            insights[sid].append({"round": None, "tick": None, "type": "multikills",
                "severity": 0, "polarity": "good",
                "text": f"{multi3} multi-kill round(s) (3k+). Big momentum swings -- replay them "
                        f"to see what set them up.",
                "evidence": {"metric": "multikills_3plus", "value": multi3, "multi": p["multi"]}})
        if p["kast"] >= 78 and p["hltv"] >= 1.15:
            insights[sid].append({"round": None, "tick": None, "type": "high_impact",
                "severity": 0, "polarity": "good",
                "text": f"Consistent impact: {p['kast']}% KAST, {p['hltv']} rating -- you "
                        f"contribute almost every round.",
                "evidence": {"metric": "kast", "value": p["kast"], "benchmark": 70, "rating": p["hltv"]}})
    return {sid: lst for sid, lst in insights.items()}


def _attach_roles_util(out_players, replay, n_rounds):
    """Heuristic per-side role detection + utility-thrown stats, reusing replay data."""
    rp = replay.get("players", [])
    frames = replay.get("frames", [])
    sid_of = [p.get("steamid") for p in rp]
    out_by_sid = {p["steamid"]: p for p in out_players}
    npl = len(rp)
    if not npl or not frames:
        return

    acc = {2: {}, 3: {}}
    for s in (2, 3):
        for i in range(npl):
            acc[s][i] = {"awp": 0, "alive": 0, "cdist": 0.0, "move": 0.0, "last": None}
    for fr in frames:
        ps = fr.get("players", [])
        for s in (2, 3):
            xs, ys, idxs = [], [], []
            for i, pl in enumerate(ps):
                if pl and pl.get("alive") and pl.get("team") == s:
                    xs.append(pl["x"]); ys.append(pl["y"]); idxs.append(i)
            if not idxs:
                continue
            cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
            for i in idxs:
                pl, a = ps[i], acc[s][i]
                a["alive"] += 1
                if "awp" in (pl.get("weapon") or "").lower():
                    a["awp"] += 1
                a["cdist"] += math.hypot(pl["x"] - cx, pl["y"] - cy)
                if a["last"] is not None:
                    a["move"] += math.hypot(pl["x"] - a["last"][0], pl["y"] - a["last"][1])
                a["last"] = (pl["x"], pl["y"])

    util = {}
    for g in replay.get("grenades", []):
        thr = g.get("thrower", -1)
        if thr is None or thr < 0 or thr >= npl:
            continue
        u = util.setdefault(sid_of[thr], {"smoke": 0, "flash": 0, "he": 0, "molotov": 0})
        if g.get("type") in u:
            u[g["type"]] += 1

    def openpart(i):
        p = out_by_sid.get(sid_of[i]); return (p["open_k"] + p["open_d"]) if p else 0

    def flashes(i):
        p = out_by_sid.get(sid_of[i]); return p.get("enemy_flashed", 0) if p else 0

    # #49 MULTI-LABEL roles: score every role from the raw per-side signals (see roles.py) and
    # attach weighted labels + a primary (back-compat) + a role-aware coaching note per player.
    role_assign = {2: {}, 3: {}}
    for s in (2, 3):
        sig = {}
        for i in range(npl):
            a = acc[s][i]
            if a["alive"] <= 0:
                continue
            uu = util.get(sid_of[i], {})
            sig[i] = {
                "awp_frac": a["awp"] / a["alive"],
                "open_part": openpart(i),
                "cdist": a["cdist"] / a["alive"],
                "move": a["move"] / a["alive"],
                "util_pr": sum(uu.values()) / max(1, n_rounds),
                "flashes": flashes(i),
                "alive": a["alive"],
            }
        role_assign[s] = roles.assign_side_roles(s, sig)

    for i in range(npl):
        p = out_by_sid.get(sid_of[i])
        if not p:
            continue
        tr, cr = role_assign[2].get(i), role_assign[3].get(i)
        if tr:
            p["t_role"], p["t_roles"], p["t_role_conf"] = tr["primary"], tr["labels"], tr["confidence"]
        if cr:
            p["ct_role"], p["ct_roles"], p["ct_role_conf"] = cr["primary"], cr["labels"], cr["confidence"]
        # signature role = the highest-weight label across both sides -> role-based coaching note
        cand = []
        if tr:
            cand.append((tr["labels"][0]["weight"], tr["primary"]))
        if cr:
            cand.append((cr["labels"][0]["weight"], cr["primary"]))
        if cand:
            rc = roles.role_coaching(max(cand)[1], p, BENCH)
            if rc:
                p["role_coaching"] = rc

    for p in out_players:
        u = util.get(p["steamid"], {"smoke": 0, "flash": 0, "he": 0, "molotov": 0})
        p["smokes"], p["flashes_thrown"] = u["smoke"], u["flash"]
        p["hes"], p["molotovs"] = u["he"], u["molotov"]
        p["util_pr"] = round(sum(u.values()) / max(1, n_rounds), 2)
        p.setdefault("t_role", "--"); p.setdefault("ct_role", "--")


_CHEAP = ("knife", "bayonet", "karambit", "dagger", "glove", "glock", "usp", "p2000",
          "p250", "five-seven", "fiveseven", "tec-9", "tec9", "cz75", "berettas",
          "deagle", "desert eagle", "r8 ", "zeus", "taser", "grenade", "molotov",
          "incendiary", "flashbang", "decoy", "c4", "bomb")


def _savable(weapon):
    w = (weapon or "").lower()
    return bool(w) and not any(c in w for c in _CHEAP)


def _merge_position_insights(insights, D, rounds, team_by_round, replay, tickrate):
    """Position/economy-based mistake detectors (mid-round K/D, isolated deaths, bad saves)."""
    frames, rp = replay.get("frames", []), replay.get("players", [])
    rrounds, revents = replay.get("rounds", []), replay.get("events", [])
    if not frames or not rp:
        return
    sr = replay.get("sample_rate", 8)
    sid_ridx = {p["steamid"]: i for i, p in enumerate(rp)}

    def frame_at(t_sec):
        return frames[max(0, min(len(frames) - 1, int(round(t_sec * sr))))]

    winner_by_round = {r["number"]: r.get("winner") for r in rrounds}
    plant_t = {}
    for e in revents:
        if e.get("type") != "bomb_planted":
            continue
        rn = e.get("round")
        if rn is None:
            for r in rrounds:
                if r["start_t"] <= e["t"] <= r["end_t"]:
                    rn = r["number"]; break
        if rn is not None:
            plant_t.setdefault(rn, e["t"])

    first_kill_t = {}
    for d in D:
        ts = d["tick"] / tickrate
        if d["round"] not in first_kill_t or ts < first_kill_t[d["round"]]:
            first_kill_t[d["round"]] = ts

    acc = {}
    for d in D:
        vic = d["vic"]
        if not vic or d["vx"] is None:
            continue
        ts, rn = d["tick"] / tickrate, d["round"]
        vteam = team_by_round.get(rn, {}).get(vic, 0)
        a = acc.setdefault(vic, {"iso": [], "mid": [], "save": []})
        fr, vri = frame_at(ts), sid_ridx.get(vic, -1)
        nearest = 1e9
        for i, pl in enumerate(fr["players"]):
            if not pl or i == vri or not pl.get("alive") or pl.get("team") != vteam:
                continue
            nearest = min(nearest, math.hypot(pl["x"] - d["vx"], pl["y"] - d["vy"]))
        if 1000 < nearest < 1e8:
            a["iso"].append((rn, d["tick"]))
        fk, pt = first_kill_t.get(rn), plant_t.get(rn)
        if fk is not None and ts > fk + 0.2 and (pt is None or ts < pt):
            a["mid"].append((rn, d["tick"]))
        side = "CT" if vteam == 3 else "T"
        if winner_by_round.get(rn) and winner_by_round[rn] != side:
            pf = frame_at(ts - 0.3)
            vp = pf["players"][vri] if 0 <= vri < len(pf["players"]) else None
            if vp and _savable(vp.get("weapon")):
                a["save"].append((rn, d["tick"]))

    def add(sid, lst, sev, text):
        insights.setdefault(sid, []).append(
            {"round": lst[0][0], "tick": lst[0][1], "type": "pos", "severity": sev, "text": text})

    for sid, a in acc.items():
        if len(a["mid"]) >= 6:
            add(sid, a["mid"], 3,
                f"Mid-round K/D leak: you died {len(a['mid'])}x in the mid-round (after the opening "
                f"kill, before the plant) -- your biggest source of lost rounds. After first contact, "
                f"regroup and trade with teammates instead of taking solo fights.")
        if len(a["iso"]) >= 4:
            add(sid, a["iso"], 2,
                f"Died isolated (no teammate within ~10m) {len(a['iso'])}x -- you're over-extending. "
                f"Play closer to support so your deaths get traded.")
        if len(a["save"]) >= 3:
            add(sid, a["save"], 2,
                f"Died holding a rifle/AWP in {len(a['save'])} lost rounds -- save the gun when the round "
                f"is gone to protect your team economy.")


def _num(v):
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return None
        return round(float(v), 1)
    except (ValueError, TypeError):
        return None


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__); sys.exit(1)
    src = args[0]
    dst = args[1] if len(args) > 1 else None
    p = DemoParser(src)
    a = analyze(p)
    print(f"rounds={a['n_rounds']} players={len(a['players'])}")
    for pl in sorted(a["players"], key=lambda x: -x["hltv"]):
        print(f"  {pl['name']:16} HLTV {pl['hltv']:.2f}  K/A/D {pl['kills']}/{pl['assists']}/{pl['deaths']}"
              f"  ADR {pl['adr']:.0f}  KAST {pl['kast']:.0f}%  open {pl['open_k']}:{pl['open_d']}"
              f"  trade% {pl['traded_pct']:.0f}  UDR {pl['udr']:.1f}  flashed {pl['enemy_flashed']}")
    ninsights = sum(len(v) for v in a["insights"].values())
    print(f"insights: {ninsights}")
    if dst:
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(a, f)
        print("wrote", dst)
