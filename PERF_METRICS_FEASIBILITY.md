# Performance-Metrics Feasibility Audit (vs Leetify benchmark data)

**Date:** 2026-06-20 · **Scope:** the 15 `LEETIFY_METRIC_FIELDS` now loaded as benchmark data
(March 2026 Leetify snapshot) vs what VantageGG can actually compute per-player from a parsed demo.

## TL;DR

- The benchmark data is loaded and correct. The **danger is the comparison wiring**, not the data.
- `benchmarks.compare()` matches **by exact key name** (`benchmarks.py:368`). Leetify keys (`avg_*`)
  and analytics keys (`counter_strafe`, `hs_pct`, `smokes`, …) **don't overlap**, so **right now every
  perf metric renders "unavailable"** — no misleading deltas are shown yet. The risk appears only when
  someone builds the player→Leetify key map for the #117 perf-compare panel.
- **The fix is a *curated* map**: wire ONLY definitionally-compatible pairs; everything else stays
  unavailable. That keeps the no-fake-data rule intact and prevents apples-to-oranges deltas.
- **Safe to compare today / with tiny additive changes:** utility throw counts, HE-damage-per-HE,
  true headshot accuracy (hitgroup), flash hit-foe/friend + blind duration (with a threshold caveat).
- **Unsafe / not comparable:** counter-strafe (different definition AND ~2× scale), spray accuracy
  (approximate), spotted accuracy (no visibility data), pre-aim (experimental), reaction time (no
  visibility start). Do **not** map these to Leetify until methodology is aligned or data is sourced.

---

## Ground truth: what the demo pipeline exposes

**Parser event surface (`parser.py`):**
| Source | Parsed? | Fields used | Notes |
|---|---|---|---|
| `fire_bullets` | ✅ | `user_steamid`, `origin_x/y/z`, `angles_x` (pitch), `angles_y` (yaw), per-shot `vel` | `vel` is position-delta-derived (raw velocity prop is garbage at fire ticks). **No `weapon` captured** (available in the event; just not pulled). |
| `player_hurt` | ✅ | `attacker/user_steamid`, `dmg_health`, **`hitgroup`**, `weapon`, `tick` | `hitgroup` is captured into `replay["damages"][].hg` but **analytics ignores it** (only reads `dmg_health`). |
| `player_blind` | ✅ (analytics only) | `attacker/user_steamid`, `blind_duration` | Parsed in `analytics.py:1053`, **not** in the replay JSON. Gated at **≥1.1s** for counting. |
| `player_death` | ✅ | `headshot` (bool), `weapon`, positions | `headshot` here = the KILL was a headshot. |
| grenade detonations + `parse_grenades()` trajectories | ✅ | thrower, type, path | Throw counts come from trajectories. |
| per-tick position / view angles | ✅ but **sampled @16fps** | `X/Y/Z`, `yaw`, `pitch`, `flash_duration`, … | 16fps = ~62ms granularity; too coarse for "moment-before-contact" pre-aim. |
| **spotted / visibility / FOV mask** | ❌ **not parsed anywhere** | — | This is the blocker for spotted-accuracy, reaction-time, and reliable pre-aim. |

**What analytics already computes per player (`analytics.py`):**
| Field | Meaning | Where |
|---|---|---|
| `counter_strafe` | % of shots (≥5, w/ `vel`) fired while < 85 u/s | `_attach_aim` `:984-1000` |
| `hs_pct` | **headshot KILLS / kills** | `:1284` |
| `enemy_flashed` | count of enemy blinds **≥1.1s** | `:1141-1161` |
| `team_flashed` | count of team blinds ≥1.1s | same |
| `blind_time` (→`avg_blind`) | **sum** of enemy blind durations ≥1.1s; only `avg_blind` is exposed | `:1162`, `:1292` |
| `smokes`,`flashes_thrown`,`hes`,`molotovs` | throw counts from grenade trajectories | `_attach_roles_util` `:1668-1672` |
| `udr` | util damage/round (**HE + molly combined**) | `:1285` |
| — | **no** overall accuracy, spray accuracy, spotted accuracy, pre-aim, reaction time | — |

---

## Feasibility matrix

| # | Leetify metric (key) | Current VGG field | Status | Safe to compare? |
|---|---|---|---|---|
| 1 | Counter Strafing (`avg_counter_strafing_good_ratio`) | `counter_strafe` | already computed (different def) | ❌ **No** — different definition + ~2× scale |
| 2 | HEs Thrown/game (`avg_he_thrown`) | `hes` | already computed | ✅ Yes (1 demo = 1 game) |
| 2 | Flashes Thrown/game (`avg_flashbang_thrown`) | `flashes_thrown` | already computed | ✅ Yes |
| 2 | Smokes Thrown/game (`avg_smoke_thrown`) | `smokes` | already computed | ✅ Yes |
| 2 | Molotovs Thrown/game (`avg_molotov_thrown`) | `molotovs` | already computed | ✅ Yes |
| 3 | Flashes Hit Foe/game (`avg_flashbang_hit_foe`) | `enemy_flashed` | already computed (≥1.1s gate) | ⚠️ Conditional — align threshold |
| 3 | Flashes Hit Friend/game (`avg_flashbang_hit_friend`) | `team_flashed` | already computed (≥1.1s gate) | ⚠️ Conditional |
| 3 | Flash Foe Avg Duration (`avg_flashbang_hit_foe_avg_duration`) | `avg_blind` | already computed (≥1.1s gate) | ⚠️ Conditional |
| 3 | Total Flash Blind Dur/game (`avg_total_flash_blind_duration`) | `blind_time` (not exposed) | feasible, small change | ⚠️ Conditional |
| 4 | HE Damage/HE (`avg_he_foes_damage_avg`) | `udr` (combined) | feasible, small analytics change | ✅ Yes (after HE split) |
| 5 | Headshot Accuracy (`avg_accuracy_head`) | `hs_pct` (= HS-kill %) | feasible, small change (hitgroup) | ❌ current field; ✅ after true-accuracy added |
| 6 | Spray Accuracy (`avg_spray_accuracy`) | — | feasible but approximate | ❌ No (approximate) |
| 7 | Spotted Accuracy (`avg_accuracy_enemy_spotted`) | — | unavailable (no visibility) | ❌ No |
| 8 | Pre-aim / Crosshair Placement (`avg_preaim`) | — | not reliable (no LOS, 16fps) | ❌ No (experimental) |
| 9 | Reaction Time (`avg_reaction_time`) | — | not reliable (no visibility start) | ❌ No |

Sanity-check on scale (loaded March-2026 numbers, confirms the mismatches):
- Counter Strafing (Leetify): **29–38%**. VGG `counter_strafe` band "good" ≈ **61** → different scale → unsafe.
- Headshot Accuracy (Leetify): **14–27%**. `hs_pct` (HS-kill %) typically **40–60%** → different metric → unsafe.
- HE Damage/HE: 14–27. Flashes thrown/game: ~6–10. Hit foe/game: ~5–9. Blind dur/game: ~10–18s.
- Reaction Time stored as **seconds** (0.52–0.78), Leetify native is ms. Pre-aim stored ~2.5–3.9 (degrees).

---

## Per-metric detail

### 1. Counter Strafing — already computed, UNSAFE to compare
- **Required data:** `fire_bullets` + per-shot horizontal speed (have it via `vel`).
- **Current formula:** `100 × (shots with vel < 85 u/s) / (shots with vel)`, min 5 shots (`analytics.py:984`).
- **Leetify definition:** "good counter-strafe ratio" — share of shots where the player properly
  released movement (key-release/velocity technique). Leetify's values sit at **~30%**, ours at **~60%**
  because we measure *"was effectively stopped"* not *"executed a proper counter-strafe."*
- **Limitations:** different definition; our `vel` is a 2-tick position delta (no sub-tick); 16fps.
- **Safe to compare?** **No.** Mapping `counter_strafe`→`avg_counter_strafing_good_ratio` would tell
  every user they're ~2× above benchmark. Keep our own metric with our own bands; do not benchmark it
  until we replicate Leetify's definition (and even then, calibrate against a known sample first).
- **Backend fields:** none. **UI:** keep "Counter-strafe %" as a standalone (no benchmark badge).
- **Tests:** assert the safe-compare map does **not** contain `counter_strafe`.

### 2. Utility thrown counts — already computed, SAFE
- **Required data:** grenade events/trajectories (have them).
- **Formula:** per-type throw count from `replay["grenades"]` grouped by thrower (`analytics.py:1612`).
  One demo = one game, so the match total IS the per-game value. (For multi-demo trends, average the
  per-game counts.)
- **Limitations:** counts come from **trajectory parsing**; a throw whose arc fails to parse is missed.
  In practice trajectories are reliable; Leetify counts from game events, so expect ±small differences.
- **Safe to compare?** **Yes.** `hes→avg_he_thrown`, `flashes_thrown→avg_flashbang_thrown`,
  `smokes→avg_smoke_thrown`, `molotovs→avg_molotov_thrown`.
- **Backend fields:** none new (already present). **UI:** "HEs/Flashes/Smokes/Molotovs per game."
- **Tests:** map present; per-game semantics asserted; divide-by-zero guarded (n_rounds≥1).

### 3. Flash metrics — computed / small change, CONDITIONAL
- **Required data:** `player_blind` (have it).
- **Formulas (proposed, benchmark-comparable variants):**
  - `flashes_hit_foe_per_game` = count of enemy blinds (per game)
  - `flashes_hit_friend_per_game` = count of team blinds (per game)
  - `total_flash_blind_duration_per_game` = Σ enemy `blind_duration` (per game) — **expose `blind_time`**
  - `flash_foe_avg_duration` = `blind_time / enemy_flashed` (already `avg_blind`)
- **Key limitation — the 1.1s gate:** our existing fields only count blinds **≥1.1s** (a deliberate
  "meaningful flash" threshold for util rating). Leetify's "hit foe" almost certainly counts shorter
  blinds too, so our counts will **undercount**. Two clean options:
  1. **Preferred:** compute a *second, ungated* set of fields (`*_per_game` above) specifically for the
     benchmark, leaving the gated `enemy_flashed`/`avg_blind` untouched for util rating.
  2. Drop the gate (changes existing util ratings — **don't** do silently).
- **Safe to compare?** **Conditional** — only after the ungated `*_per_game` fields exist. Until then,
  do not map `enemy_flashed`→`avg_flashbang_hit_foe`.
- **Backend fields:** `flashes_hit_foe_per_game`, `flashes_hit_friend_per_game`,
  `total_flash_blind_duration_per_game`, `flash_foe_avg_duration`.
- **Tests:** ungated counts ≥ gated; per-game normalization; missing-`player_blind` demo → unavailable
  (not 0); the existing util-rating numbers are unchanged.

### 4. HE Damage / HE — small analytics change, SAFE
- **Required data:** `player_hurt` with `weapon` (have it).
- **Formula:** `he_dmg_per_he = (credited enemy damage from weapon == hegrenade) / max(1, hes)`.
  Split the existing `util_dmg` credit loop (`analytics.py:1133`) into `he_dmg` vs `molly_dmg` by
  weapon. **Exclude** molotov/incendiary/inferno from HE. Guard divide-by-zero.
- **Limitations:** HP-capped credit (engine definition) — matches our ADR convention, may differ a hair
  from Leetify's raw; per-HE denominator uses our trajectory-based `hes` count.
- **Safe to compare?** **Yes** — `he_dmg_per_he → avg_he_foes_damage_avg`.
- **Backend fields:** `he_dmg`, `molly_dmg`, `he_dmg_per_he`. **UI:** "HE damage per HE."
- **Tests:** molly damage excluded from HE; 0 HEs → no divide-by-zero (unavailable, not 0);
  combined `he_dmg + molly_dmg` ≈ old `util_dmg`.

### 5. Headshot Accuracy — small change, current field UNSAFE / true metric SAFE
- **The trap:** `hs_pct` is **headshot KILLS / kills**, NOT headshot accuracy. Leetify
  `avg_accuracy_head` = **head HITS / total HITS** (hitgroup). Different metric, ~2× different scale.
- **Required data:** `player_hurt.hitgroup` (captured in `replay.damages[].hg`; analytics must read it).
- **Formula:** `headshot_accuracy = head_hits / total_hits`, where `head_hits` = `player_hurt` rows
  vs enemies with `hitgroup == 1`. Min hit threshold (e.g. ≥20) for stability.
- **Limitations:** hits, not shots; multi-pellet shotgun hits inflate denominator slightly.
- **Safe to compare?** Current `hs_pct`: **No** (relabel UI as "HS Kill %", never benchmark it against
  `avg_accuracy_head`). New `headshot_accuracy`: **Yes**.
- **Backend fields:** `headshot_accuracy`, `head_hits`, `total_hits`. **UI:** keep "HS Kill %" for
  `hs_pct`; add "Head Accuracy" for the new field with the benchmark badge.
- **Tests:** `hs_pct` not mapped to `avg_accuracy_head`; `headshot_accuracy` uses hitgroup; min-sample
  → unavailable.

### 6. Spray Accuracy — feasible but APPROXIMATE, UNSAFE
- **Required data:** `fire_bullets` grouped into spray sequences (consecutive shots, same weapon,
  short gap) + `player_hurt` hit matching. **`weapon` is not currently captured on `fire_bullets`**
  (add it; confirm availability via `python parser.py --probe`).
- **Approx formula:** group shots within ~0.4s same weapon; spray = 3rd+ bullet; accuracy = hits in
  spray window / spray shots. Hit↔shot association is a time-window approximation, not exact.
- **Limitations:** no exact bullet→hit mapping; Leetify's exact spray definition is undocumented.
- **Safe to compare?** **No** until the definition is validated against a known sample. Could ship as a
  standalone "experimental" stat without a benchmark badge.
- **Tests:** if implemented, marked `approximate` in quality; not in safe-compare map.

### 7. Spotted Accuracy — UNAVAILABLE
- **Required data:** per-shot visibility (was an enemy spotted/visible to the shooter). **Not parsed and
  not reliably inferable** from 16fps positions without LOS/occlusion.
- **Do NOT** substitute overall accuracy (hits/all shots) — that's a different metric. Overall accuracy
  is worth shipping on its own, but must **not** be mapped to `avg_accuracy_enemy_spotted`.
- **Safe to compare?** **No.** Mark unavailable with reason "no visibility data."

### 8. Pre-aim / Crosshair Placement — NOT RELIABLE (experimental)
- **Required data:** view angle vs enemy position at moment before contact + LOS/occlusion. We have view
  angles (16fps + per-shot) and enemy positions (16fps) but **no occlusion** and coarse sampling.
- **Approx:** angular distance between crosshair and nearest enemy shortly before first contact —
  noisy without LOS (counts enemies through walls).
- **Safe to compare?** **No.** Experimental at best; never badge against `avg_preaim`.

### 9. Reaction Time / Time To Damage — NOT RELIABLE / UNAVAILABLE
- **Required data:** the moment an enemy became visible → first shot/damage. Requires visibility start,
  which we don't have. Proxies (first-spotted event, first-in-LOS) are unavailable/unreliable.
- **Safe to compare?** **No.** Mark unavailable with reason "no visibility/contact-time data."

---

## Implementation direction

1. **Additive only / old-cache safe.** Every new field follows the `counter_strafe` precedent: compute
   when the source event is present, **omit** otherwise (→ unavailable, never 0). No schema break;
   `analytics_migrations.py` can recompute on bump for old caches that carry the raw arrays.
2. **Curated safe-compare map** in `benchmarks.py` — the single gate:
   ```
   PERF_COMPARE_MAP = {                      # player field -> Leetify key  (SAFE pairs ONLY)
     "hes": "avg_he_thrown", "flashes_thrown": "avg_flashbang_thrown",
     "smokes": "avg_smoke_thrown", "molotovs": "avg_molotov_thrown",
     "he_dmg_per_he": "avg_he_foes_damage_avg",
     "headshot_accuracy": "avg_accuracy_head",
     "flashes_hit_foe_per_game": "avg_flashbang_hit_foe",
     "flashes_hit_friend_per_game": "avg_flashbang_hit_friend",
     "total_flash_blind_duration_per_game": "avg_total_flash_blind_duration",
     "flash_foe_avg_duration": "avg_flashbang_hit_foe_avg_duration",
   }
   # DELIBERATELY ABSENT: counter_strafe, hs_pct, spray, spotted, preaim, reaction_time
   ```
   A `perf_compare(player)` helper re-keys the player's safe fields into Leetify keys and calls
   `compare()`. Unsafe metrics never get a key → stay unavailable. **This is the whole safety story.**
3. **Quality flags** per metric: `exact | approximate | unavailable` + `sample_size` + `reason`,
   surfaced so the UI shows a clear unavailable state rather than zero.
4. **UI:** only render a benchmark badge for metrics in `PERF_COMPARE_MAP`. Relabel `hs_pct`→"HS Kill %".

## Recommended first pass (all SAFE + additive, no existing numbers change)
1. **HE damage / HE** (`he_dmg`, `he_dmg_per_he`) — split util damage by weapon.
2. **True headshot accuracy** (`headshot_accuracy`) via hitgroup; relabel `hs_pct` → "HS Kill %".
3. **Overall accuracy** (`accuracy` = hits/shots) — standalone stat, **no** Leetify badge.
4. **Ungated flash per-game fields** + expose **total blind duration**.
5. **`PERF_COMPARE_MAP` + `perf_compare()` + quality flags** — wires only the safe pairs.
- **Explicitly defer:** counter-strafe benchmarking (recalibrate first), spray (validate), spotted /
  pre-aim / reaction (need visibility data — a future parser addition if demoparser2 exposes a spotted
  mask).

## Safe vs unsafe comparison — summary
- **SAFE:** `avg_he_thrown`, `avg_flashbang_thrown`, `avg_smoke_thrown`, `avg_molotov_thrown`,
  `avg_he_foes_damage_avg`, `avg_accuracy_head` (new field), and flash hit-foe/friend + blind-duration
  (after the ungated per-game fields exist).
- **UNSAFE (do not map):** `avg_counter_strafing_good_ratio` (scale/def), `avg_spray_accuracy`
  (approximate), `avg_accuracy_enemy_spotted` (no data), `avg_preaim` (experimental),
  `avg_reaction_time` (no data). `hs_pct` must never be mapped to `avg_accuracy_head`.
