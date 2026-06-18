"""Two-tier utility rating (#50): VOLUME (how much you throw) vs QUALITY (how well it lands).

A single util number hides the most useful coaching split: someone can throw a ton of utility
that does nothing, or a little that's devastating. So this rates the two tiers separately and
turns the pair into a plain-English verdict.

  * VOLUME  = utility thrown per round (engagement), scored vs a solid target.
  * QUALITY = how effective it is: util damage/round (HE/molly), plus flash effectiveness
              (enemy blinds per flash thrown + average blind duration) when there's a flash
              sample, minus a team-flash penalty (blinding your own team is a real mistake).

All inputs are box-score fields already present on every cached demo (util_pr, udr,
enemy_flashed, avg_blind, team_flashed, flashes_thrown, ...), so this needs no re-parse and the
frontend can recompute it for older caches. Scoring reuses subratings' transparent piecewise
curve (weak->40, good->70, elite->92) and letter bands, so a tier reads the same in every demo.
GENERIC: nothing here assumes a roster or specific player.
"""
import subratings

# (weak, good, elite) anchors in metric units for each util signal.
A_VOLUME = (1.5, 3.5, 6.0)      # utility thrown / round
A_UDR = (3, 8, 16)              # util damage / round
A_CONV = (0.4, 0.9, 1.6)       # enemy blinds per flash thrown
A_BLIND = (0.6, 1.1, 1.8)      # average blind duration inflicted (s)
MIN_FLASHES = 3                 # need a few flashes before flash-effectiveness means anything


def _g(p, k, d=0):
    v = p.get(k)
    return d if v is None else v


def compute_util_rating(p, has_flash=True):
    """Return {volume, quality, verdict, ...} for one player dict. Always computable.

    has_flash: whether this demo emitted player_blind data at all. Some demos (e.g. certain
    tournament recordings) don't, so enemy_flashed/avg_blind are 0 for EVERYONE -- that's
    'unavailable', not 'flashed nobody'. When False we score quality on util damage alone and
    skip the flash component + team-flash penalty (and flag it) instead of unfairly tanking it.
    """
    rp = max(1, p.get("rounds_played") or 1)
    util_pr = p.get("util_pr")
    if util_pr is None:
        util_pr = (_g(p, "smokes") + _g(p, "flashes_thrown") + _g(p, "hes") + _g(p, "molotovs")) / rp
    udr = float(_g(p, "udr"))
    ft = int(_g(p, "flashes_thrown"))
    ef = int(_g(p, "enemy_flashed"))
    ab = float(_g(p, "avg_blind"))
    tf = int(_g(p, "team_flashed"))

    volume = subratings._interp(float(util_pr), *A_VOLUME)

    comps = [(subratings._interp(udr, *A_UDR), 0.5)]   # damage always counts
    flash_conv = None
    if has_flash and ft >= MIN_FLASHES:
        flash_conv = ef / ft
        s = 0.6 * subratings._interp(flash_conv, *A_CONV) + 0.4 * subratings._interp(ab, *A_BLIND)
        comps.append((s, 0.5))
    qden = sum(w for _, w in comps) or 1.0
    quality = sum(s * w for s, w in comps) / qden
    penalty = min(15.0, tf / rp * 30.0) if has_flash else 0.0   # team-flashing drags quality down
    quality = max(0.0, quality - penalty)

    vb = subratings.band_for(volume)
    qb = subratings.band_for(quality)
    return {
        "volume": {"score": round(volume), "band": vb[0], "label": vb[1]},
        "quality": {"score": round(quality), "band": qb[0], "label": qb[1]},
        "util_pr": round(float(util_pr), 2), "udr": round(udr, 1),
        "flash_conv": round(flash_conv, 2) if flash_conv is not None else None,
        "avg_blind": round(ab, 2), "team_flashed": tf,
        "team_flash_penalty": round(penalty, 1),
        "flash_data": bool(has_flash),
        "verdict": _verdict(volume, quality),
    }


def _verdict(volume, quality):
    """Plain-English read of the volume x quality quadrant (60 ~ a 'decent' cutoff)."""
    hv, hq = volume >= 60, quality >= 60
    if hv and hq:
        return "Impactful — you throw a lot of utility and it lands."
    if hv and not hq:
        return "High volume, low impact — tighten your lineups and timing."
    if hq and not hv:
        return "Efficient — few nades but effective; you could throw more."
    return "Underutilizing utility — learn your team's standard lineups."


def attach(players):
    """Bake util_rating onto each player dict (server-side; frontend mirrors for old caches).

    Flash data is treated as available only if SOME player blinded an enemy -- if the whole
    lobby shows 0 enemy_flashed despite throwing flashes, the demo didn't emit player_blind.
    """
    has_flash = any(int(p.get("enemy_flashed") or 0) > 0 for p in players)
    for p in players:
        p["util_rating"] = compute_util_rating(p, has_flash=has_flash)
