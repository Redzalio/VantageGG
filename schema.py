"""Shared schema versions for the CS2 demo player.

Kept in their own tiny module (no heavy deps) so app.py can validate caches without
importing pandas / demoparser2. Bump these to invalidate caches:

  SCHEMA_VERSION    -- replay JSON shape (frames/events/players). Bump => full re-parse.
  ANALYTICS_VERSION -- analytics/insights shape & formulas. Bump => recompute analytics
                      only (the cached replay is reused, no demo re-parse).
"""
SCHEMA_VERSION = 14     # + per-frame clip (m_iClip1) + reload (is_in_reload) for the FP ammo HUD
ANALYTICS_VERSION = 11  # v11: Leetify-comparable perf metrics -- true headshot ACCURACY (hitgroup),
                        # HE-damage-per-HE (split from molotov/fire), overall accuracy, ungated flash
                        # per-game (hit foe/friend + total blind duration), + per-metric quality flags.
                        # NEEDS the raw demo (weapon-on-hit, hitgroup, player_blind aren't in the cached
                        # replay), so it is NOT registered in analytics_migrations -- old caches flag
                        # stale and a re-upload recomputes them. See PERF_METRICS_FEASIBILITY.md.
                        # v10: per-round econ verdict (econ_verdict/econ_note) on round_cards -- this is
                        # cache-derivable, so analytics_migrations upgrades v9->v10 in place (no .dem).
