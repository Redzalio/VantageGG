"""Cross-match tendency / anti-strat / repeated-pattern detection (#44).

Everything else in the app analyses ONE demo. This aggregates a player ACROSS the whole library
(keyed by steamid) to surface what they do REPEATEDLY -- the patterns an opponent could scout, or
that a 5-stack should vary: a role they always play, a callout they keep dying in, an opening
habit, a buy they keep losing. Patterns only surface when they recur (>= half the player's matches,
min 2), so a one-off isn't called a tendency.

Pure + GENERIC: takes the same match records as goals._matches (each {analytics, map, created_at,
key, sha}); no roster config. Robust to the heterogeneous library -- old caches expose single
`ct_role`/`t_role` + `zones`, newer ones add `ct_roles`/`position_stats`; this reads whatever's there.
"""
from collections import Counter


def _primary_role(p, side):
    """The player's main role on a side: first multi-label role if present, else the single role."""
    labels = p.get(side + "_roles")
    if labels:
        r = labels[0].get("role")
        if r and r != "--":
            return r
    r = p.get(side + "_role")
    return r if r and r != "--" else None


def _player_in(analytics, steamid):
    for p in (analytics.get("players") or []):
        if str(p.get("steamid")) == steamid:
            return p
    return None


def cross_tendencies(matches, steamid, min_matches=2):
    """Aggregate one player across `matches` (newest-first records w/ .analytics). Returns
    {steamid, name, n_matches, maps, roles, tendencies:[{kind, severity, text, evidence}]}.
    `tendencies` is empty until the player has >= min_matches in the library."""
    steamid = str(steamid)
    played = []
    for rec in matches:
        p = _player_in(rec.get("analytics") or {}, steamid)
        if p is not None:
            played.append((rec, p))
    n = len(played)
    name = played[0][1].get("name") if n else steamid
    maps = sorted({rec.get("map") for rec, _ in played if rec.get("map")})
    role_ct, role_t = Counter(), Counter()
    zone_agg = {}        # callout -> {k, d, dm (matches with a death), km}
    open_part, open_wr = [], []
    buy_agg = {}         # buy type -> {rounds, wsum (win%*rounds), m (matches)}

    for _, p in played:
        rc, rt = _primary_role(p, "ct"), _primary_role(p, "t")
        if rc:
            role_ct[rc] += 1
        if rt:
            role_t[rt] += 1
        for z, v in (p.get("zones") or {}).items():
            za = zone_agg.setdefault(z, {"k": 0, "d": 0, "dm": 0, "km": 0})
            k, d = int(v.get("k", 0)), int(v.get("d", 0))
            za["k"] += k
            za["d"] += d
            za["dm"] += 1 if d > 0 else 0
            za["km"] += 1 if k > 0 else 0
        rp = max(1, p.get("rounds_played") or 1)
        ot = (p.get("open_k") or 0) + (p.get("open_d") or 0)
        open_part.append(ot / rp)
        if ot >= 3:
            open_wr.append(float(p.get("open_wr") or 0))
        for bt, b in (p.get("buys") or {}).items():
            ba = buy_agg.setdefault(bt, {"rounds": 0, "wsum": 0.0, "m": 0})
            br = int(b.get("rounds", 0))
            ba["rounds"] += br
            ba["wsum"] += float(b.get("win_pct", 0)) * br
            ba["m"] += 1

    out = {"steamid": steamid, "name": name, "n_matches": n, "maps": maps,
           "roles": {"ct": dict(role_ct), "t": dict(role_t)}, "tendencies": []}
    if n < min_matches:
        return out

    thr = max(2, (n + 1) // 2)        # "recurring" = at least half the matches (min 2)
    tend = []

    for side, ctr in (("T", role_t), ("CT", role_ct)):
        if ctr:
            role, c = ctr.most_common(1)[0]
            if c >= thr:
                tend.append({"kind": "role", "side": side, "severity": 1,
                             "text": f"On {side} you play {role} in {c}/{n} matches"
                                     + (" -- predictable, mix it up." if side == "T" else "."),
                             "evidence": dict(ctr)})

    # recurring death spots (anti-strat): dying there across multiple matches, net-negative
    deaths = 0
    for z, za in sorted(zone_agg.items(), key=lambda kv: (-kv[1]["dm"], kv[1]["k"] - kv[1]["d"])):
        if za["dm"] >= thr and za["d"] >= 3 and za["d"] > za["k"] and deaths < 3:
            tend.append({"kind": "death_spot", "severity": 2, "zone": z,
                         "text": f"You keep dying at {z}: {za['k']}-{za['d']} across {za['dm']} matches "
                                 f"-- change your angle or timing there.",
                         "evidence": za})
            deaths += 1
    # a recurring strong spot (positive)
    for z, za in sorted(zone_agg.items(), key=lambda kv: kv[1]["d"] - kv[1]["k"]):
        if za["km"] >= thr and za["k"] >= za["d"] + 3:
            tend.append({"kind": "strong_spot", "severity": 0, "zone": z,
                         "text": f"You consistently win {z}: {za['k']}-{za['d']} across {za['km']} matches "
                                 f"-- keep taking it.",
                         "evidence": za})
            break

    if len(open_part) >= min_matches:
        part = sum(open_part) / len(open_part)
        wr = (sum(open_wr) / len(open_wr)) if open_wr else None
        if part >= 0.22 and wr is not None:
            if wr < 48:
                tend.append({"kind": "opening", "severity": 2,
                             "text": f"You take a lot of opening duels (~{round(part * 100)}% of rounds) but win "
                                     f"only {round(wr)}% -- pick better fights or have a trade set up.",
                             "evidence": {"participation": round(part, 2), "win_pct": round(wr)}})
            elif wr >= 55:
                tend.append({"kind": "opening", "severity": 0,
                             "text": f"Reliable opener: ~{round(part * 100)}% opening involvement at {round(wr)}% "
                                     f"win -- your team should play off your entries.",
                             "evidence": {"participation": round(part, 2), "win_pct": round(wr)}})

    for bt, ba in buy_agg.items():
        if ba["m"] >= thr and ba["rounds"] >= 4:
            wr = ba["wsum"] / ba["rounds"] if ba["rounds"] else 0
            if wr < 30:
                tend.append({"kind": "buy", "severity": 1,
                             "text": f"You keep losing {bt} buys: {round(wr)}% round win across {ba['m']} matches.",
                             "evidence": {"buy": bt, "win_pct": round(wr), "rounds": ba["rounds"]}})

    tend.sort(key=lambda t: -t["severity"])
    out["tendencies"] = tend
    return out
