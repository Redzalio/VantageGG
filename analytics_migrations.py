"""In-place, .dem-free analytics migrations.

`analytics.analyze()` is parser-driven (it re-reads the .dem), so under `KEEP_DEM=0` we can't recompute
stale analytics from scratch — there's no raw demo to feed it. But many ANALYTICS_VERSION bumps only
ADD fields that are DERIVABLE from data already in the cache (e.g. a per-round economy verdict from the
round's buy types). Those are registered here as one-step transforms and applied lazily when a demo is
served, so existing demos pick up the improvement with no re-upload and no extra storage.

Bumps that genuinely need the raw demo (new parser fields, re-read events/ticks) are deliberately NOT
registered: such a cache stops at its old version and the library's existing "outdated" flag invites a
re-upload. `migrate()` only advances a cache past versions it actually has a transform for.

Each `MIGRATIONS[v]` upgrades an analytics dict from v-1 to v IN PLACE and returns True if it changed
anything. Everything is idempotent and guarded — never raises on partial/missing data.
"""
import analytics
from schema import ANALYTICS_VERSION


def _v10_econ_backfill(a):
    """v9 -> v10: add econ_verdict/econ_note to each round card. Derivable from the card's winner +
    buy_ct/buy_t, which v9 caches already store (same logic as analytics.build_round_cards)."""
    cards = a.get("round_cards")
    if not isinstance(cards, list):
        return False
    changed = False
    for c in cards:
        if not isinstance(c, dict) or "econ_verdict" in c:   # already migrated (idempotent)
            continue
        win = analytics.winner_str(c.get("winner"))
        bc, bt = c.get("buy_ct"), c.get("buy_t")
        if win == "CT":
            buy_lose, buy_win = bt, bc
        elif win == "T":
            buy_lose, buy_win = bc, bt
        else:
            buy_lose = buy_win = None
        verdict, note = analytics._econ_verdict(buy_lose, buy_win, False)
        c["econ_verdict"], c["econ_note"] = verdict, note
        changed = True
    return changed


# target_version -> transform that upgrades an analytics dict from (target_version - 1)
MIGRATIONS = {10: _v10_econ_backfill}


def migrate(data):
    """Upgrade data['analytics'] in place toward ANALYTICS_VERSION using only cache-derivable transforms.
    Returns True if anything changed. Safe + idempotent; never touches the raw demo. Stops at the first
    version gap with no registered transform (that cache needs a real re-parse)."""
    if not isinstance(data, dict):
        return False
    a = data.get("analytics")
    if not isinstance(a, dict):
        return False
    v = a.get("version")
    if not isinstance(v, int):
        return False
    changed = False
    while v < ANALYTICS_VERSION and (v + 1) in MIGRATIONS:
        try:
            if MIGRATIONS[v + 1](a):
                changed = True
        except Exception:
            break                                  # a bad transform must never break serving
        v += 1
        a["version"] = v                           # only advance past steps we have a transform for
    return changed
