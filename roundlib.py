"""Pure, dependency-free round-construction + weapon helpers (shared by parser.py + analytics.py).

Keeping these out of the pandas/demoparser2 modules makes them unit-testable in isolation
and guarantees parser and analytics agree on what a "round"/"buy"/"utility weapon" is.
"""
import re


def norm_weapon(w):
    """Normalize a demoparser2 weapon string: lowercase, drop the 'weapon_' prefix.

    demoparser2 emits names like 'weapon_ak47', 'ak47', 'hegrenade', 'inferno', 'molotov',
    'inc_grenade' depending on event/version -- this gives one canonical spelling.
    """
    s = (w or "").lower().strip()
    return s[7:] if s.startswith("weapon_") else s


def _wkey(w):
    """norm_weapon with separators stripped, for tolerant substring matching."""
    return re.sub(r"[^a-z0-9]", "", norm_weapon(w))


def is_util_damage_weapon(w):
    """True if a player_hurt weapon is grenade damage (HE / molotov / incendiary fire).

    Covers demoparser2 spelling variants: 'weapon_hegrenade', 'hegrenade', 'molotov',
    'inferno' (the fire entity), 'incgrenade', 'inc_grenade', 'incendiary'. Used for UDR --
    matching only the old exact set ('hegrenade','inferno','molotov','incgrenade') silently
    undercounted any build that prefixed 'weapon_' or spelled incendiary differently.
    """
    s = _wkey(w)
    return any(tok in s for tok in ("hegren", "molotov", "inferno", "incgren", "incendiary"))


def is_he_damage_weapon(w):
    """True ONLY for HE-grenade damage (not fire). Splits the combined util-damage set so
    'HE damage per HE' can be measured separately from molotov/incendiary fire damage."""
    return "hegren" in _wkey(w)


def is_fire_damage_weapon(w):
    """True ONLY for molotov/incendiary FIRE damage ('inferno' is the fire entity). The
    complement of is_he_damage_weapon within is_util_damage_weapon."""
    s = _wkey(w)
    return any(tok in s for tok in ("molotov", "inferno", "incgren", "incendiary"))


def winner_str(wr):
    """Normalize a round_end winner (str 'CT'/'T'/'TERRORIST' or int 3/2) to 'CT'/'T'/''."""
    if isinstance(wr, str):
        u = wr.strip().upper()
        return "CT" if u in ("CT", "3") else ("T" if u in ("T", "TERRORIST", "2") else "")
    try:
        wi = int(wr)
        return "CT" if wi == 3 else ("T" if wi == 2 else "")
    except (TypeError, ValueError):
        return ""


def pair_rounds(starts, freezes, end_rows):
    """Build rounds by TICK RANGE, anchored on valid round_end rows.

    A real round is defined by a round_end that carries a genuine winner; its start is the
    latest round_start strictly before that end (and after the previous round's end). This
    drops warmup/restart starts and null/timeout end rows that naive index-pairing misaligns.

    Args:
        starts:   iterable of round_start ticks
        freezes:  iterable of round_freeze_end ticks
        end_rows: list of dicts, each with 'tick' and 'winner' (optional 'reason')
    Returns:
        list of {num, start, freeze_end, end, winner, reason} in tick units, num from 1.
    """
    starts = sorted(int(s) for s in starts)
    freezes = sorted(int(f) for f in freezes)
    valid = [(int(e["tick"]), winner_str(e.get("winner")), str(e.get("reason", "")))
             for e in end_rows if e.get("tick") is not None and winner_str(e.get("winner"))]
    valid.sort()
    rounds, prev_end = [], -1
    for et, winner, reason in valid:
        cand = [s for s in starts if prev_end < s < et]
        st = cand[-1] if cand else max(prev_end + 1, 0)
        fz = next((f for f in freezes if f >= st), st)
        rounds.append({"num": len(rounds) + 1, "start": st, "freeze_end": fz,
                       "end": et, "winner": winner, "reason": reason})
        prev_end = et
    # fallback: no winner-bearing end rows -> best-effort index pairing
    if not rounds and starts:
        for i, st in enumerate(starts):
            e = end_rows[i] if i < len(end_rows) else {}
            fz = next((f for f in freezes if f >= st), st)
            rounds.append({"num": i + 1, "start": st, "freeze_end": fz,
                           "end": int(e["tick"]) if e.get("tick") is not None else st,
                           "winner": winner_str(e.get("winner")),
                           "reason": str(e.get("reason", ""))})
    return rounds


# Buy buckets by freeze-end equipment value (Source $). See docs/CS2_ECONOMY_REFERENCE.md for
# the verified CS2 prices these are derived from (checked 2026-06-17). These are SOFT thresholds
# on an APPROXIMATE input: current_equip_value bundles gun+armor+util+kit and can't see intent,
# drops, or money held for next round. They keep "full-buy conversion" honest and split partial
# buys into light/force -- not a ground-truth economy ledger.
#
# A real CS2 full buy = rifle + armor + utility. It costs MORE on CT than T: AK ($2700) vs
# M4A4/M4A1-S ($2900), and CT also wants a defuse kit ($400) + more defensive util. So a lone
# M4 + armor with no kit/util (~$3900) should read as "force", not "full" -> CT uses a higher
# full-buy floor than T.
BUY_ECO = 1000          # < this = saving / pistol-only
BUY_LIGHT = 2400        # upgraded pistols / light armor / SMG, no rifle
BUY_FULL_T = 3900       # T full: AK + armor + some utility
BUY_FULL_CT = 4300      # CT full: M4 + armor + kit/utility (needs more than T)
BUY_FORCE = BUY_FULL_T  # back-compat alias (old name); the neutral full floor
BUY_ORDER = ["pistol", "full", "force", "light", "eco", "unknown"]


def _full_floor(side):
    """Freeze-end equip $ at/above which a team-average buy counts as a real full buy."""
    return BUY_FULL_CT if (side or "").upper() == "CT" else BUY_FULL_T


def classify_buy(equip_value, is_pistol=False, side=None):
    """Classify a buy from freeze-end equipment value (Source $).

    `side` ("CT"/"T"/None) raises the full-buy bar for CT (kits + costlier util). When None,
    uses the T/neutral floor (back-compatible with callers that don't pass a side).
    Returns one of: pistol | eco | light | force | full | unknown.
    """
    if is_pistol:
        return "pistol"
    if equip_value is None:
        return "unknown"
    if equip_value < BUY_ECO:
        return "eco"
    if equip_value < BUY_LIGHT:
        return "light"
    if equip_value < _full_floor(side):
        return "force"
    return "full"


# --- CS2 kill rewards (verified 2026-06-17; see docs/CS2_ECONOMY_REFERENCE.md) ---------------
# Most weapons pay $300; SMGs $600 (P90 is the $300 exception); shotguns $900 (XM1014 the $600
# exception); AWP & Zeus $100; knife $1500; grenade/fire $300. NOTE: the separate CS2 "CT gets
# +$50 per T killed" team bonus is NOT included here -- it isn't reconstructable per-player from
# our parsed data (documented limitation).
KILL_REWARD_DEFAULT = 300
_SMG_KEYS = ("mp9", "mac10", "mp7", "mp5sd", "ump45", "bizon")     # P90 handled as the $300 exception
_SHOTGUN_KEYS = ("nova", "sawedoff", "mag7")                       # XM1014 handled as the $600 exception


def kill_reward(weapon):
    """In-game $ a kill with `weapon` pays in CS2 competitive. Tolerant of demoparser2 spellings.

    Falls back to the $300 default for anything unrecognized (correct for rifles/pistols/LMGs).
    """
    w = _wkey(weapon)
    if not w:
        return KILL_REWARD_DEFAULT
    if "knife" in w or "bayonet" in w or "karambit" in w or "daggers" in w:
        return 1500
    if "taser" in w or "zeus" in w:
        return 100
    if "awp" in w:
        return 100
    if "xm1014" in w:
        return 600
    if "p90" in w:
        return 300
    if any(k in w for k in _SHOTGUN_KEYS):
        return 900
    if any(k in w for k in _SMG_KEYS):
        return 600
    return KILL_REWARD_DEFAULT   # rifles, pistols (incl CZ75 now $300), autosnipers, SSG, LMGs, nades
