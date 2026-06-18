"""Shared schema versions for the CS2 demo player.

Kept in their own tiny module (no heavy deps) so app.py can validate caches without
importing pandas / demoparser2. Bump these to invalidate caches:

  SCHEMA_VERSION    -- replay JSON shape (frames/events/players). Bump => full re-parse.
  ANALYTICS_VERSION -- analytics/insights shape & formulas. Bump => recompute analytics
                      only (the cached replay is reused, no demo re-parse).
"""
SCHEMA_VERSION = 14     # + per-frame clip (m_iClip1) + reload (is_in_reload) for the FP ammo HUD
ANALYTICS_VERSION = 9   # CS2 economy pass: side-aware buy thresholds (CT full > T full), per-round
                        # mixed/hero buy-shape modifiers; verified prices/kill-rewards in
                        # docs/CS2_ECONOMY_REFERENCE.md (ADR/UDR HP-capping from v8 unchanged)
