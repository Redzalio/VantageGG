"""Per-position (callout) performance breakdown (#62).

Expands the flat zone K/D into a per-callout table that splits kills/deaths by SIDE (CT/T) and
flags opening-duel involvement, so a player can see where on the map they win and where they keep
dying -- per side -- and which positions they keep taking (or losing) the opening fight in.

Kills are credited at the VICTIM's callout (the enemy's location when you killed them) and deaths
at the victim's own callout, matching the existing zone K/D. The opening duel is the round's first
death (its killer gets an open-kill at that callout, its victim an open-death).

Pure + GENERIC: works from the demo's own death records + per-round team map; no roster assumptions.
Callout names come from last_place_name in the parse (server-side only), so the frontend falls back
to the flat zone K/D for caches parsed before this field existed.
"""
from collections import defaultdict

SIDE = {3: "ct", 2: "t"}


def build_position_stats(D, deaths_by_round, team_by_round, roster):
    """D: death records [{round, tick, atk, vic, place}]. Returns {sid: [row,...]} sorted by activity.

    Each row: {zone, k, d, kd, ct_k, ct_d, t_k, t_d, open_k, open_d}.
    """
    acc = {s: defaultdict(lambda: {"ct_k": 0, "ct_d": 0, "t_k": 0, "t_d": 0,
                                   "open_k": 0, "open_d": 0}) for s in roster}
    # the round's opening duel = its earliest death (same dict objects as in D)
    openers = {}
    for rnum, dl in deaths_by_round.items():
        ds = sorted(dl, key=lambda x: x["tick"])
        if ds:
            openers[rnum] = ds[0]

    for d in D:
        place = d.get("place")
        if not place:
            continue
        rnum, atk, vic = d.get("round"), d.get("atk"), d.get("vic")
        is_open = openers.get(rnum) is d
        if atk and atk in roster and atk != vic:
            sd = SIDE.get(team_by_round.get(rnum, {}).get(atk))
            if sd:
                acc[atk][place][sd + "_k"] += 1
            if is_open:
                acc[atk][place]["open_k"] += 1
        if vic and vic in roster:
            sd = SIDE.get(team_by_round.get(rnum, {}).get(vic))
            if sd:
                acc[vic][place][sd + "_d"] += 1
            if is_open:
                acc[vic][place]["open_d"] += 1

    out = {}
    for sid, zones in acc.items():
        rows = []
        for z, v in zones.items():
            k = v["ct_k"] + v["t_k"]
            dd = v["ct_d"] + v["t_d"]
            rows.append({"zone": z, "k": k, "d": dd,
                         "kd": round(k / dd, 2) if dd else float(k),
                         "ct_k": v["ct_k"], "ct_d": v["ct_d"], "t_k": v["t_k"], "t_d": v["t_d"],
                         "open_k": v["open_k"], "open_d": v["open_d"]})
        rows.sort(key=lambda r: -(r["k"] + r["d"]))   # most-contested positions first
        out[sid] = rows
    return out


def attach(out_players, D, deaths_by_round, team_by_round):
    """Bake position_stats onto each player (keyed by steamid)."""
    roster = {p["steamid"] for p in out_players}
    stats = build_position_stats(D, deaths_by_round, team_by_round, roster)
    for p in out_players:
        p["position_stats"] = stats.get(p["steamid"], [])
