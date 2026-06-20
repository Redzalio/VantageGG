"""Shared schema versions for the CS2 demo player.

Kept in their own tiny module (no heavy deps) so app.py can validate caches without
importing pandas / demoparser2. Bump these to invalidate caches:

  SCHEMA_VERSION    -- replay JSON shape (frames/events/players). Bump => full re-parse.
  ANALYTICS_VERSION -- analytics/insights shape & formulas. Bump => recompute analytics
                      only (the cached replay is reused, no demo re-parse).
"""
SCHEMA_VERSION = 14     # + per-frame clip (m_iClip1) + reload (is_in_reload) for the FP ammo HUD
ANALYTICS_VERSION = 10  # v10: per-round econ verdict (econ_verdict/econ_note) on round_cards. This is
                        # cache-derivable, so analytics_migrations upgrades v9 caches in place on serve
                        # (no .dem needed). v9 = CS2 economy pass: side-aware buy thresholds (CT full >
                        # T full) + mixed/hero buy modifiers (docs/CS2_ECONOMY_REFERENCE.md).
