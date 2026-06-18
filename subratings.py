"""Skill-pillar sub-ratings (Aim / Utility / Positioning) with letter bands.

This is the Leetify/Skybox-style "what are you good at" breakdown, and it is
DELIBERATELY DIFFERENT from context_rating.py:

  * context_rating  = one overall, LOBBY-RELATIVE production score (~1.0 = lobby avg).
  * subratings      = three named skill pillars, each scored 0-100 against ABSOLUTE
                      rank benchmarks, so "B / Solid Aim" means the same thing in
                      every demo (a single player in a single match is meaningful).

Each input metric is mapped through a transparent piecewise-linear curve anchored
at three points -- (weak, good, elite) in metric units -> (40, 70, 92) in score
points -- then clamped to [0, 100]. "good" is the FACEIT-10 / solid-level target
(the same BENCH the rest of the app uses), and it lands at 70 = a "B / Solid" band.

Everything is generic: nothing assumes a roster, a specific player, or >1 demo.
Missing inputs (e.g. counter-strafe on an old parse, or a thin opening-duel sample)
are dropped and the remaining weights renormalize, so a pillar degrades gracefully
instead of lying. Pillars expose their per-metric breakdown so the UI can show the
"why" (value vs benchmark) rather than an opaque number.
"""

# Score targets the three metric anchors map to. good -> 70 == top of "Solid".
S_WEAK, S_GOOD, S_ELITE = 40.0, 70.0, 92.0

# Bands: (min_score, letter, label). First match wins (descending).
BANDS = [
    (90, "S", "Elite"),
    (80, "A", "Excellent"),
    (68, "B", "Solid"),
    (52, "C", "Average"),
    (38, "D", "Below avg"),
    (0,  "F", "Weak"),
]

# Per-pillar metric config. anchors = (weak, good, elite) in METRIC UNITS.
# For "lower is better" metrics the anchors DECREASE (weak > good > elite); the
# interpolator handles either direction. weight = relative importance in the pillar.
AIM = [
    ("counter_strafe", "Counter-strafe", (30, 55, 80), 0.26, "%"),
    ("adr",            "ADR",            (55, 80, 105), 0.24, ""),
    ("hs_pct",         "Headshot",       (25, 50, 70),  0.22, "%"),
    ("kpr",            "Kills/round",    (0.50, 0.68, 0.95), 0.16, ""),
    ("open_wr",        "Opening WR",     (40, 52, 65),  0.12, "%"),
]
UTILITY = [
    ("udr",        "Util dmg/round",     (3, 8, 16),       0.45, ""),
    ("flashes_pr", "Enemy flashes/round", (0.20, 0.50, 1.0), 0.30, ""),
    ("util_pr",    "Util thrown/round",  (1.5, 3.5, 6.0),  0.25, ""),
]
POSITIONING = [
    ("kast",       "KAST",               (55, 70, 82),     0.34, "%"),
    ("traded_pct", "Traded-death",       (8, 20, 35),      0.26, "%"),
    ("dpr",        "Deaths/round",       (0.80, 0.64, 0.50), 0.22, ""),   # lower better
    ("open_d_pr",  "Opening deaths/round", (0.22, 0.12, 0.05), 0.18, ""), # lower better
]


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _interp(v, weak, good, elite):
    """Map a metric value through the (weak,good,elite)->(40,70,92) piecewise line.

    Works whether the anchors increase (higher=better) or decrease (lower=better).
    Linear within each segment, linearly extrapolated past the ends, clamped 0..100.
    """
    incr = elite >= weak

    def seg(a, sa, b, sb):
        if b == a:
            return sa
        return sa + (v - a) * (sb - sa) / (b - a)

    if incr:
        s = seg(weak, S_WEAK, good, S_GOOD) if v <= good else seg(good, S_GOOD, elite, S_ELITE)
    else:
        s = seg(weak, S_WEAK, good, S_GOOD) if v >= good else seg(good, S_GOOD, elite, S_ELITE)
    return clamp(s, 0.0, 100.0)


def band_for(score):
    if score is None:
        return ("--", "Not enough data")
    for lo, letter, label in BANDS:
        if score >= lo:
            return (letter, label)
    return ("F", "Weak")


def _derived(p):
    """Per-player derived inputs the pillars need that aren't stored verbatim."""
    rp = max(1, p.get("rounds_played") or 1)
    return {
        "flashes_pr": p.get("enemy_flashed", 0) / rp,
        "open_d_pr": p.get("open_d", 0) / rp,
    }


def _pillar(p, config, derived, drop):
    """Score one pillar. `drop` = set of metric keys to omit (thin sample / missing)."""
    metrics, num, wsum = [], 0.0, 0.0
    for key, label, (weak, good, elite), weight, unit in config:
        if key in drop:
            continue
        val = derived.get(key, p.get(key))
        if val is None:
            continue
        sc = _interp(float(val), weak, good, elite)
        metrics.append({"key": key, "label": label, "value": round(float(val), 2),
                        "good": good, "unit": unit, "score": round(sc)})
        num += sc * weight
        wsum += weight
    if wsum <= 0:
        return None
    return metrics, num / wsum


def compute_subratings(players):
    """Return {steamid: {aim, utility, positioning}} skill-pillar breakdowns.

    Each pillar = {score:int, band:str, label:str, confidence:str, metrics:[...]}.
    Returns {} for an empty roster. Pillars that can't be computed are None.
    """
    out = {}
    for p in players:
        sid = p.get("steamid")
        if sid is None:
            continue
        rp = p.get("rounds_played") or 0
        der = _derived(p)

        # decide which metrics to drop for THIS player (missing / too-thin sample)
        aim_drop = set()
        if p.get("counter_strafe") is None:               # old parse w/o per-shot velocity
            aim_drop.add("counter_strafe")
        if (p.get("open_k", 0) + p.get("open_d", 0)) < 4:  # opening WR not meaningful yet
            aim_drop.add("open_wr")
        if p.get("kills", 0) < 4:
            aim_drop.add("hs_pct")
        pos_drop = set()
        if p.get("deaths", 0) < 4:
            pos_drop.add("traded_pct")

        pillars = {}
        for name, cfg, drop in (("aim", AIM, aim_drop),
                                ("utility", UTILITY, set()),
                                ("positioning", POSITIONING, pos_drop)):
            res = _pillar(p, cfg, der, drop)
            if res is None:
                pillars[name] = {"score": None, "band": "--", "label": "Not enough data",
                                 "confidence": "low", "metrics": []}
                continue
            metrics, score = res
            # utility: penalize team-flashing (own-team blinds are a real, common mistake)
            if name == "utility" and rp:
                pen = clamp(p.get("team_flashed", 0) / rp * 30.0, 0.0, 15.0)
                score = clamp(score - pen, 0.0, 100.0)
            letter, label = band_for(score)
            conf = "med" if rp >= 8 else "low"
            pillars[name] = {"score": round(score), "band": letter, "label": label,
                             "confidence": conf, "metrics": metrics}
        out[sid] = pillars
    return out


if __name__ == "__main__":
    import json
    demo = [{
        "steamid": "A", "name": "tester", "rounds_played": 24, "kills": 22, "deaths": 16,
        "adr": 88.0, "kast": 74.0, "kpr": 0.92, "dpr": 0.67, "hs_pct": 58.0,
        "counter_strafe": 61.0, "open_k": 6, "open_d": 4, "open_wr": 60.0, "traded_pct": 31.0,
        "udr": 9.4, "util_pr": 3.8, "enemy_flashed": 14, "team_flashed": 2,
    }]
    print(json.dumps(compute_subratings(demo), indent=2))
