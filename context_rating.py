"""Transparent, lobby-relative "Context Rating" for CS2 demos.

This is an HLTV-3.0-INSPIRED approximation -- it is NOT the official HLTV 3.0
rating. That formula is private/proprietary; this module makes no claim to
reproduce it. Instead it computes a deterministic, fully transparent score
that is RELATIVE TO THE LOBBY in the parsed demo: each player's eco-adjusted
production is normalized against the mean of the players supplied, so ~1.0
means "about average for this lobby".

Two things are kept deliberately separate so nothing is hidden:
  * raw stats (kills/round, ADR, survival %, KAST, multi-kills/round, swing)
    are exposed verbatim, with no economy adjustment, under "raw".
  * eco-adjusted sub-ratings (the inputs to the weighted rating) are exposed
    under "sub". The economy adjustment rewards production made while facing
    richer enemies (eco_factor > 1) and discounts production made while
    better-equipped, so an eco/force-buy frag is not treated like a gun-round
    frag.

Weights sum to 1.0; the final context_rating is their weighted sum.
"""
import math

WEIGHTS = {"kills": 0.25, "damage": 0.25, "survival": 0.12, "kast": 0.15, "multi": 0.08, "swing": 0.15}


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def compute_context_rating(players, eco_factor):
    """Compute lobby-relative context ratings. See module docstring for caveats.

    players: list of dicts with keys steamid, rounds_played, kills, deaths, adr,
        kast (0-100), multi (e.g. {"2":n,"3":n,...}), swing (may be 0/negative).
    eco_factor: {steamid: float} mean(enemy_avg_equip / your_equip) over played
        rounds (~1.0 neutral; >1 faced richer enemies). Missing -> 1.0.
    Returns {steamid: {"context_rating", "sub"{6}, "eco_factor", "raw"{6}}}.
    """
    if not players:
        return {}
    rows = {}
    for p in players:
        sid = p["steamid"]
        rp = max(1, p.get("rounds_played") or 1)
        m = p.get("multi") or {}
        kills_pr = p.get("kills", 0) / rp
        dmg = float(p.get("adr", 0.0))
        survival = max(0.0, 1 - p.get("deaths", 0) / rp)
        kast01 = float(p.get("kast", 0.0)) / 100.0
        multi_pr = (1 * m.get("2", 0) + 2 * m.get("3", 0) + 3 * m.get("4", 0) + 4 * m.get("5", 0)) / rp
        sw = float(p.get("swing", 0.0))
        e = clamp(float(eco_factor.get(sid, 1.0)) if eco_factor else 1.0, 0.6, 1.8)
        rows[sid] = {
            "kills_pr": kills_pr, "dmg": dmg, "survival": survival, "kast01": kast01,
            "multi_pr": multi_pr, "sw": sw, "e": e,
            "kills_adj": kills_pr * e, "dmg_adj": dmg * e, "survival_adj": survival * e,
            "multi_adj": multi_pr * e, "kast_adj": kast01 * (1 + (e - 1) * 0.5),
        }
    n = len(rows)
    mean = lambda k: sum(r[k] for r in rows.values()) / n
    mean_kills, mean_dmg, mean_surv = mean("kills_adj"), mean("dmg_adj"), mean("survival_adj")
    mean_kast, mean_multi, mean_sw = mean("kast_adj"), mean("multi_adj"), mean("sw")
    max_abs_sw = max(1e-9, max(abs(r["sw"]) for r in rows.values()))
    out = {}
    for sid, r in rows.items():
        sub = {
            "kills": clamp(r["kills_adj"] / mean_kills if mean_kills > 0 else 1.0, 0.4, 1.8),
            "damage": clamp(r["dmg_adj"] / mean_dmg if mean_dmg > 0 else 1.0, 0.4, 1.8),
            "survival": clamp(r["survival_adj"] / mean_surv if mean_surv > 0 else 1.0, 0.4, 1.8),
            "kast": clamp(r["kast_adj"] / mean_kast if mean_kast > 0 else 1.0, 0.4, 1.8),
            "multi": clamp(r["multi_adj"] / mean_multi if mean_multi > 0 else 1.0, 0.4, 1.8),
            "swing": clamp(1.0 + (r["sw"] - mean_sw) / max_abs_sw * 0.4, 0.4, 1.8),
        }
        rating = sum(sub[k] * WEIGHTS[k] for k in WEIGHTS)
        out[sid] = {
            "context_rating": round(rating, 2),
            "sub": {k: round(v, 2) for k, v in sub.items()},
            "eco_factor": round(r["e"], 2),
            "raw": {
                "kills_pr": round(r["kills_pr"], 2), "adr": round(r["dmg"], 1),
                "survival_pct": round(r["survival"] * 100, 1), "kast": round(r["kast01"] * 100, 1),
                "multi_pr": round(r["multi_pr"], 2), "swing": round(r["sw"], 2),
            },
        }
    return out


if __name__ == "__main__":
    import json
    example = [
        {"steamid": "A", "rounds_played": 20, "kills": 22, "deaths": 12, "adr": 95.0,
         "kast": 78.0, "multi": {"2": 4, "3": 1}, "swing": 3.2},
        {"steamid": "B", "rounds_played": 20, "kills": 14, "deaths": 15, "adr": 68.0,
         "kast": 65.0, "multi": {"2": 1}, "swing": -1.1},
    ]
    eco = {"A": 1.0, "B": 1.4}
    print(json.dumps(compute_context_rating(example, eco), indent=2))
