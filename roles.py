"""Multi-label role model + role-based coaching (#49).

The old detector assigned ONE role per side by ranking players on a single signal (most AWP,
most opening, etc.). Real players blend roles -- an entry who also supports, a lurker who
sometimes AWPs. This module instead scores EVERY role from raw per-side signals and emits
WEIGHTED labels (summing to 1) with a confidence, plus a primary label for back-compat.

Signals (per player, per side, from the replay frames + box-score), all turned into a
side-relative 0..1 before scoring so "most/least" is meaningful within the team:
  awp_frac  -- share of alive time holding an AWP (absolute, /0.4 -> AWP carry)
  open_part -- opening-duel involvement (open_k + open_d)
  cdist     -- avg distance from the team centroid (spread / lurk)
  move      -- avg movement per frame (mobility: rotate/space-take vs anchor)
  util_pr   -- utility thrown per round (support)
  flashes   -- enemies flashed (support / flash assists)

Everything is transparent and GENERIC: scores are relative to the four/five players on the
side in THIS demo, no roster or per-name assumptions. Labelled approximate like the rest.
"""

T_ROLES = ("AWP", "Entry", "Lurker", "Support", "Spacetaker")
CT_ROLES = ("AWP", "Anchor", "Rotator", "Support", "Rifler")

# Per-role coaching: what the role is judged on + a one-line drill cue + the key box-score metric.
ROLE_GUIDE = {
    "AWP":     {"metric": "open_wr", "watch": "Hold angles and get the opening pick, but don't die first with the AWP -- it's your team's most expensive gun.",
                "drill": "1v1 AWP prefires on your common hold angles; practise the quick-scope re-peek."},
    "Entry":   {"metric": "open_wr", "watch": "You take first contact -- win your opening duels and make sure a teammate is right behind to trade.",
                "drill": "Prefire + crosshair-placement on entry paths; review every opening death for support distance."},
    "Lurker":  {"metric": "kast",    "watch": "You play away from the team for picks and info -- time your lurk with the hit and don't get caught out of position.",
                "drill": "Watch your lurk timings vs the round's execute; practise late-round 1vX situations."},
    "Support": {"metric": "udr",     "watch": "You set up the team with utility -- land the flashes/smokes that get teammates in, and avoid team-flashing.",
                "drill": "Lineup practice for your team's standard executes; pop-flash timing for entries."},
    "Spacetaker": {"metric": "adr",  "watch": "You take map space with the team -- trade aggression for impact and stay trade-able.",
                "drill": "Crossfire + trade drills with a teammate; review fights you took without a trade."},
    "Anchor":  {"metric": "kast",    "watch": "You hold a site solo -- delay and survive for the retake; don't over-peek into a lost fight.",
                "drill": "1vN site-hold scenarios; practise falling back and saving for the retake."},
    "Rotator": {"metric": "kast",    "watch": "You read the round and rotate -- time your rotations off info, don't leave your area too early.",
                "drill": "Review rotations vs where the round actually went; mid-round info reads."},
    "Rifler":  {"metric": "adr",     "watch": "Balanced role -- win your gunfights and stay with the team's structure.",
                "drill": "Aim/recoil routine; review solo fights for better positioning."},
}


def _norm(vals):
    """Side-relative min-max -> {i: 0..1}. Flat input -> 0.5 (no signal to discriminate on)."""
    if not vals:
        return {}
    lo, hi = min(vals.values()), max(vals.values())
    if hi - lo < 1e-9:
        return {i: 0.5 for i in vals}
    return {i: (v - lo) / (hi - lo) for i, v in vals.items()}


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def assign_side_roles(side, signals, min_alive=30):
    """signals: {i: {awp_frac, open_part, cdist, move, util_pr, flashes, alive}} for one side.
    side: 2 (T) or 3 (CT). Returns {i: {primary, labels:[{role,weight}], confidence}}.
    Players with too few alive frames are skipped (returned absent)."""
    valid = {i: s for i, s in signals.items() if s.get("alive", 0) >= min_alive}
    if not valid:
        return {}
    aggr = _norm({i: s.get("open_part", 0) for i, s in valid.items()})
    spread = _norm({i: s.get("cdist", 0) for i, s in valid.items()})
    mobility = _norm({i: s.get("move", 0) for i, s in valid.items()})
    util = _norm({i: s.get("util_pr", 0) for i, s in valid.items()})
    flash = _norm({i: s.get("flashes", 0) for i, s in valid.items()})
    awp = {i: _clamp(valid[i].get("awp_frac", 0) / 0.4) for i in valid}

    out = {}
    for i in valid:
        if side == 2:                       # T side
            aff = {
                "AWP": awp[i],
                "Entry": 0.7 * aggr[i] + 0.3 * mobility[i],
                "Lurker": 0.8 * spread[i] + 0.2 * (1 - aggr[i]),
                "Support": 0.6 * util[i] + 0.4 * flash[i],
                "Spacetaker": 0.5 * mobility[i] + 0.5 * aggr[i] * (1 - spread[i]),
            }
        else:                               # CT side
            aff = {
                "AWP": awp[i],
                "Anchor": 0.8 * (1 - mobility[i]) + 0.2 * (1 - spread[i]),
                "Rotator": 0.8 * mobility[i] + 0.2 * spread[i],
                "Support": 0.6 * util[i] + 0.4 * flash[i],
                "Rifler": 0.45,             # everyone is a bit of a rifler (baseline)
            }
        # a committed AWPer should read as primarily AWP
        if awp[i] > 0.6:
            aff["AWP"] += 0.6
        tot = sum(aff.values()) or 1.0
        weights = sorted(((r, w / tot) for r, w in aff.items() if w > 0),
                         key=lambda kv: -kv[1])
        labels = [{"role": r, "weight": round(w, 2)} for r, w in weights if w >= 0.18][:3]
        if not labels:
            labels = [{"role": weights[0][0], "weight": round(weights[0][1], 2)}]
        # renormalize the kept labels so the displayed weights sum to ~1
        ssum = sum(l["weight"] for l in labels) or 1.0
        for l in labels:
            l["weight"] = round(l["weight"] / ssum, 2)
        gap = labels[0]["weight"] - (labels[1]["weight"] if len(labels) > 1 else 0.0)
        conf = "low" if valid[i]["alive"] < 120 else ("high" if gap > 0.2 else "med")
        out[i] = {"primary": labels[0]["role"], "labels": labels, "confidence": conf}
    return out


def role_coaching(primary_role, player, bench):
    """A role-aware coaching note: what the role is judged on + how this player measures up."""
    g = ROLE_GUIDE.get(primary_role)
    if not g:
        return None
    metric = g["metric"]
    val = player.get(metric)
    target = bench.get({"open_wr": "open_wr", "udr": "udr", "kast": "kast", "adr": "adr"}.get(metric, metric))
    verdict = None
    if val is not None and target:
        verdict = "above" if val >= target else "below"
    return {"role": primary_role, "watch": g["watch"], "drill": g["drill"],
            "metric": metric, "value": val, "target": target, "verdict": verdict}


def label_str(labels):
    """Compact 'Entry 60% · Support 25%' string for a labels list."""
    return " · ".join(f"{l['role']} {round(l['weight'] * 100)}%" for l in (labels or []))
