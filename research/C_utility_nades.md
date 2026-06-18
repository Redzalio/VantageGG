# Workstream C — Utility & Nade Learning (research)

Research date: 2026-06-15. Scope: how the best CS2 products handle utility **quality**, timing, lineups, practice, and comparing **actual** match utility to an **intended playbook** — translated into signals THIS app (CS2DemoPlayer) can compute from already-parsed data.

**What the app already parses (verified in `parser.py` / `analytics.py`)** — every signal below builds on these, so feasibility is high:
- Detonation events per nade: `{type, player(thrower), x,y,z, t, round}`; smokes carry `end_t = t+18s`, mollies `end_t = t+7s` (parser.py L324-344).
- Grenade **trajectories**: `grenades[] = {type, thrower, round, t0, t1, pts:[[t,x,y,z]...]}` — `pts[0]`=throw, `pts[-1]`=land (parser.py L348-459).
- `player_blind` flash events with `blind_duration`; analytics already buckets enemy-vs-team flashed at the **1.1s** threshold and tracks `blind_time` (analytics.py L1013-1036) — **same threshold Leetify uses**.
- `player_hurt` damage events incl. util-weapon flagging (`is_util_damage_weapon`); per-player `util_dmg`/`udr` already computed (analytics.py L997-1010, L1158).
- Per-player utility fields already in the output JSON: `udr`, `enemy_flashed`, `avg_blind`, `team_flashed` (analytics.py L1149-1166).
- Dry-opening-death detector already checks **killer_blinded** + friendly support-flash in prior 2.5s (analytics.py L688-717).
- Nade **library** schema (`nades.py`): per-lineup map/side/type/name/throw_callout/target_callout/`throw_pos`/`land_pos`/movement/technique[]/aim/video/tags/strat_group; `from_demo()` already mines candidate lineups from real throw coords.

So this app is ~80% of the way to a best-in-class utility layer: it counts and places nades; the gap is **judging quality and comparing to an intended playbook**. That gap is the shortlist.

---

## ⚡ UTILITY-QUALITY SIGNALS — BUILD NOW (shortlist)

All are computable from data already in the demo JSON; difficulty is the analytics/UI work only.

| # | Signal | One-line definition (computable) | Inputs already parsed | Diff | Conf |
|---|--------|----------------------------------|----------------------|------|------|
| 1 | **Flash → kill (real flash assist)** | enemy killed while blind ≥1.1s, kill within blind window; credit the flasher (incl. flasher's own kills); exclude half-blind. | `player_blind`(dur), deaths(D) | M | Confirmed (Leetify def) |
| 2 | **Pop-flash quality** | flash where time(throw→detonation) is short AND an enemy is blinded ≥1.1s within ~1s of the detonation, near a peeking teammate. Score = blind enemies / (detonation-after-throw seconds). | trajectory `pts`(throw t→land t), `flashbang_detonate`, `player_blind`, frames | M | Confirmed (1.6s fuse; good pop ≈ blind on sight) |
| 3 | **Team-flash rate (already partly there)** | teammates blinded ≥1.1s per flash thrown; flag players >X. Already counted — surface as a **rate** + per-round deep-link. | `player_blind` team side | S | Confirmed |
| 4 | **Smoke-on-target (playbook match)** | detonation xyz within radius (~150–250u) of a **library lineup's `land_pos`** for that map → "executed lineup X". Else "off-book smoke." | detonations, `nades` library `land_pos` | M | Inferred (our own schema) |
| 5 | **CT smoke stopped a push** | CT smoke where an enemy was within **800u** of the smoke bloom (land xyz) during its lifetime → counts as a "blocking" smoke. | smoke detonation+`end_t`, frames (enemy pos) | M | Confirmed (Leetify exact 800u) |
| 6 | **Utility before first contact (timing)** | per round: was the player's util thrown **before** the round's first kill / before they took contact? % of util thrown pre-contact vs post-contact, split by round phase (pistol/eco, pre-plant, post-plant). | detonations t, first-death tick per round | S | Confirmed |
| 7 | **Molly/HE forced damage or delay** | molly/HE that dealt damage (already have util_dmg) OR whose fire/blast overlapped a choke an enemy then avoided (area denial = enemy near edge then retreats). Start with the damage half. | `player_hurt` util dmg, molly `end_t`+xyz, frames | S (dmg) / L (denial) | Confirmed (dmg) / Inferred (denial) |
| 8 | **Unused utility on death** | avg \$ value of nades still held when the player died (carrying util to the grave). | deaths + loadouts at death tick | M | Confirmed (Leetify exact stat) |
| 9 | **Two-tier Utility Rating (Quantity × Quality)** | Quantity = nades/round vs 3.0 baseline, `x^(2/3)` rescale, cap 100. Quality = z-scored combo of #1-#8 vs this match's avg. Combine via **geometric mean**. | all of the above | M | Confirmed (Leetify formula) |

> Recommended first cut (1 evening each): **#1, #3, #6** (pure event math, no library, no frames) → then **#4, #5, #8** (need library/frames) → then **#2, #9** (composite).

---

## Detailed findings

### A. Utility-QUALITY signals (judge the nade, don't just count it)

#### 1. Real flash assist (flash → kill), not scoreboard assists
- **Source:** https://leetify.com/blog/leetify-stats-glossary/ , https://leetify.com/blog/utility-ratings/
- **Observed:** Leetify "Flashbangs leading to kills" = avg enemies killed *while affected by your flash*, **includes the flasher's own kills** after flashing, **excludes half-blind** (<1.1s). This is richer than the in-game flash-assist stat.
- **Why it helps:** rewards flashes that actually create kills, the single best proxy for "good flash." Distinguishes a flasher-support player from someone padding flash counts.
- **Fits THIS app (signal):** for each flash detonation, find enemies with `blind_duration ≥ 1.1` in `player_blind`; for each, if that enemy dies within the blind window (death tick ≤ blind start + duration*64) to *any* of the flasher's team, credit the **flasher** a "flash→kill". Per-player `flash_to_kill` count + `flash_to_kill_per_flash`. The death list `D` and `player_blind` are both already in `analyze()`.
- **Difficulty:** M. **Priority:** now. **Confirmed.**

#### 2. Pop-flash quality (throw→detonation→peek timing)
- **Source:** https://dotesports.com/counter-strike/news/how-to-pop-flash-in-cs2 , https://cs2guide.net/guides/cs2-advanced-utility-guide/ , https://boosteria.org/guides/cs2-grenade-throws-guide-smoke-lineups-flash-basics
- **Observed:** flash fuse is **1.6s**; a "pop" detonates ~0.2s after entering sight so the enemy can't turn. Coaching rule: entry flash must **detonate as the teammate peeks** — "flashing in 2…1…, swing on 1." A flash 1s late blinds your own entry.
- **Why it helps:** separates a genuine pop-flash (enemy blinded with no reaction time, teammate swings into it) from a lazy high-arc flash the enemy turns from. Directly coachable.
- **Fits THIS app (signal):** per flash, `det_lead = detonation_t − throw_t` (from trajectory `pts[0][0]`→`flashbang_detonate.t`). Quality high when (a) `det_lead` short / it's a quick bounce, (b) ≥1 enemy blinded ≥1.1s within ~1.0s of detonation, AND (c) a teammate is within ~600u of an enemy (peek) at detonation (frames already give positions; you already use the 600u "trade distance"). Output `popflash_score` + flag "blinded yourself instead" when a teammate is the only one blinded ≥1.1s near a peek.
- **Difficulty:** M. **Priority:** next. **Confirmed (mechanics) / Inferred (exact score weights).**

#### 3. Team-flash rate + "self-blind on entry"
- **Source:** https://leetify.com/blog/utility-ratings/ (Friends-Flashed-per-Flash is an **inverted** quality input)
- **Observed:** Leetify penalizes teammates-flashed-per-flash and HE-team-damage-per-HE as quality negatives.
- **Why it helps:** team-flashing is one of the most common low-elo utility mistakes and is 100% detectable.
- **Fits THIS app (signal):** you already count `team_flashed` (≥1.1s) per player and even raise an insight at ≥3. Upgrade to a **rate** (`team_flashed / flashes_thrown`), and add a sharper variant: team-flash that immediately precedes that teammate's death (blinded teammate dies within ~3s while blind) → "you flashed X to death." Also surface HE/molly **team damage** per nade (you already attribute util dmg; just don't currently split friendly).
- **Difficulty:** S. **Priority:** now. **Confirmed.**

#### 4. Smoke-on-target — actual throw vs intended playbook lineup
- **Source:** our own library schema (`nades.py`); concept validated by every lineup product (csnades/cs2util/tracker store a `land_pos`/landing spot).
- **Observed:** all lineup sites store an explicit landing/target spot; NADR/annotations use a **DistanceThreshold** circle as the "did it land right" tolerance (see §C).
- **Why it helps:** this is the headline feature the brief asks for — *did the smoke land where the playbook wanted?* Turns the library from a reference into a **grading rubric**.
- **Fits THIS app (signal):** for each smoke/molotov detonation in a demo, find the nearest library lineup of same map+type (and side) and compute distance from detonation xyz to `land_pos`. If ≤ radius (default ~160u, expose per-lineup `DistanceThreshold`-style override) → "hit lineup «name»"; else "off-book / missed «name» by Nu." Aggregate: per-player and per-team **lineup-execution accuracy**, and a "playbook coverage" count (how many of your tagged execute smokes actually went up). `from_demo()` already does nearest-landing dedup, so the matching code exists.
- **Difficulty:** M. **Priority:** now (after the library has a few tagged execute lineups). **Inferred** (our schema, but mechanic is standard).

#### 5. CT smoke "stopped a push"
- **Source:** https://leetify.com/blog/leetify-stats-glossary/
- **Observed:** Leetify "[CT] Smokes that stopped a push" = % of CT smokes where an enemy was within **800 map units** of the smoke bloom location.
- **Why it helps:** measures *defensive* smoke value (slowing/blocking a take), which raw "smokes thrown" misses entirely. Exact number is public.
- **Fits THIS app (signal):** for each CT smoke, over its 18s lifetime (`end_t`), check frames for any enemy within 800u of the bloom (land xyz) → boolean "contested/stopped." Report CT `smoke_block_pct`. Cheap because smoke lifetime + enemy frames are already there.
- **Difficulty:** M. **Priority:** next. **Confirmed (exact 800u).**

#### 6. Utility timing by round phase (before vs after first contact)
- **Source:** https://boosteria.org/guides/cs2-demo-review-guide-pros-look-improve-fast , https://smartcoach.gg/ , https://leetify.com/blog/utility-ratings/ (timing is implicit in "support flash before the peek")
- **Observed:** demo-review best practice drills into *timing of site entries and utility deployment*; coaching repeatedly stresses util must precede contact ("fight designers… choose what info both teams see, and when").
- **Why it helps:** a smoke thrown after first blood is often wasted; flagging "X% of your util was thrown AFTER first contact" is a crisp, novel coaching line none of the counting tools give.
- **Fits THIS app (signal):** per round, get first-kill tick (already have `deaths_by_round`). For each nade detonation, label `pre_contact` if `t < first_kill_t` (and optionally pre-plant vs post-plant using `plant_by_round`, already parsed). Per-player `util_pre_contact_pct`; team-level "util dump after contact" flag. Pure event math — no frames.
- **Difficulty:** S. **Priority:** now. **Confirmed.**

#### 7. Molly/HE forced damage or area denial
- **Source:** https://refrag.gg/blog/getting-started-with-nadr-utility-hub/ (NADR bots **print exact HE/incendiary damage**), https://leetify.com/blog/leetify-stats-glossary/ (Damage-to-Enemies-per-HE)
- **Observed:** quality of HE/molly is judged primarily by **damage per nade**; pro tools surface exact molly/HE damage. Area-denial (forcing a retreat/rotation without damage) is the harder, narrative half.
- **Why it helps:** rewards mollies that flush/deny and HEs that chunk a stack, beyond "thrown count."
- **Fits THIS app (signal):** damage half is trivial — you already accumulate `util_dmg`; just split by nade type for **HE_dmg_per_he** and **molly_dmg_per_molly** and attribute each util-damage `player_hurt` to the nearest same-type detonation in time/space (so you can say "this molly did 47 across 2 enemies"). Denial half (later): molly whose fire-volume (`land xyz`, `end_t`) sat on a choke while an enemy was within ~150u then moved away (frames) → "area denial."
- **Difficulty:** S (damage) / L (denial). **Priority:** now (damage) / later (denial). **Confirmed (damage) / Inferred (denial).**

#### 8. Unused utility on death
- **Source:** https://leetify.com/blog/leetify-stats-glossary/
- **Observed:** Leetify "Unused Utility on Death" = avg \$ value of smoke/HE/flash/molly/decoy held at death.
- **Why it helps:** flags hoarders who die with a full kit — a clean, well-understood inefficiency stat.
- **Fits THIS app (signal):** at each death tick, read that player's grenade loadout (the parser already emits `loadouts`; if it doesn't snapshot at death tick, sample nearest frame's inventory or track give/throw deltas). Sum nade values (HE 300, flash 200, smoke 300, molly 400/600, decoy 50). Per-player `unused_util_value/death`.
- **Difficulty:** M (depends on loadout-at-tick availability). **Priority:** next. **Confirmed.**

#### 9. Composite two-tier Utility Rating
- **Source:** https://leetify.com/blog/utility-ratings/
- **Observed (exact methodology):**
  - **Quantity** = avg nades/round (excl. decoy) ÷ 3.0 baseline, expressed 0–1, rescaled `x^(2/3)` (exponential falloff), ×100, capped 100.
  - **Quality** = z-scores of six stats vs match avg/SD: flash-assist %, enemies-flashed/flash, friends-flashed/flash (inverted), avg blind time/flash, HE-dmg/HE, HE-team-dmg/HE (inverted); weighted sum → standard-normal CDF → 0–100.
  - **Overall** = **geometric mean** of the two (punishes imbalance).
- **Why it helps:** gives one defensible 0–100 number per player you can rank a roster by and trend across demos.
- **Fits THIS app (signal):** you have all six quality inputs (or close: flash-assist% from #1, enemies/friends flashed already counted, blind time already summed, HE dmg from #7). Compute z-scores **within the match** (10 players) like the app's existing benchmark approach, geometric-mean with the quantity rating. Label "approx, our model" exactly like the existing transparent rating layer.
- **Difficulty:** M. **Priority:** next. **Confirmed (formula public).**

---

### B. Lineup-library upgrades (toward an intended playbook)

#### B1. Playbook / execute grouping + per-round "execute fingerprint"
- **Source:** https://csnades.gg/ (grenade-type + location filters, SimpleRadar toggle, callouts overlay), https://www.cs2util.com/ ("Common spots" curated section, area/callout filters), https://refrag.gg/blog/getting-started-with-nadr-utility-hub/ (lineup overlay on a map per lineup)
- **Observed:** every lineup product browses by **map → grenade type → side → callout/area**, draws throw+landing on a 2D radar, toggles callouts/SimpleRadar. cs2util groups "common spots"; NADR overlays the chosen lineup on the map.
- **Why it helps:** lets a user define an **execute** (e.g. "Mirage A default": CT smoke + jungle smoke + stairs smoke + connector flash) as a named group, then the app grades a round against the whole execute (signal #4 at the set level): "you ran 3/4 of A-default; stairs smoke was 220u short."
- **Fits THIS app:** the schema already has `strat_group`. Add a Playbook view: group library lineups by `strat_group`, show all throw/landing dots on the radar at once (you already draw single lineups on 2D). Then a per-round "did this execute happen" matcher = run #4 over all members of a group in a short tick window.
- **Difficulty:** M. **Priority:** next. **Confirmed (UX pattern, not data).**

#### B2. Auto-tag demo-mined lineups with the player + outcome
- **Source:** https://refrag.gg/blog/grenade-finder-the-fastest-way-to-find-lineups/ (NADR's `.find` computes the throw angle from any spot to a chosen landing box), https://www.cs2util.com/ (each lineup stores **air time** + movement + mouse button)
- **Observed:** modern tools store *air time*, movement type (standing/walking/running/crouch), and jump-throw flag per lineup; NADR can back-solve a throw from a desired landing.
- **Why it helps:** when you "add from demo," you can auto-derive more than coords: **air time** = `t1−t0`; **throw technique** = infer jumpthrow if thrower's z rose sharply at release / running if horizontal velocity high at `pts[0]` (frames). Richer auto-lineups = better playbook with less manual entry.
- **Fits THIS app (signal):** extend `from_demo()` to set `air_time = round(g.t1−g.t0,2)`, and a heuristic `technique` (jumpthrow vs standing) from the first two trajectory points + the thrower's frame velocity at throw. Also stamp `thrower` name and whether that throw matched an existing lineup (so you can see "Player Y already has this smoke down").
- **Difficulty:** S–M. **Priority:** next. **Confirmed (fields) / Inferred (technique heuristic).**

#### B3. "Outdated lineup" reporting + favorites/collections
- **Source:** https://www.cs2util.com/ (Report button for invalid positions; Discord), https://yprac.com/ , https://csnades.gg/ (favorites/collections-style browsing)
- **Observed:** lineup sites let users flag stale lineups and favorite/collect them.
- **Why it helps:** keeps a local library trustworthy after map updates; favorites speed up review.
- **Fits THIS app:** schema already has `favorite`; add a `verified_on`/`stale` flag and a one-click "mark stale" in the lineup UI. Low value vs the quality signals — list as later.
- **Difficulty:** S. **Priority:** later. **Confirmed.**

---

### C. Practice-EXPORT from a demo (generate a routine for missed/wanted lineups)

This is highly feasible using **public CS2 commands only** — no proprietary anything.

#### C1. Per-lineup practice console block (setpos/setang + rethrow)
- **Source:** https://lineups.gg/guides/cs2-practice-commands/ , https://vredux.com/articles/cs2-teleport-command , https://www.ghostcap.com/cs2-practice-config , https://steamcommunity.com/sharedfiles/filedetails/?id=3404948190
- **Observed (all public):** `getpos` / `getpos_exact` print exact pos+ang; `setpos x y z` + `setang pitch yaw roll` teleport+aim to a spot; `sv_rethrow_last_grenade` repeats the last throw; `noclip` to fly; trajectory preview via `sv_grenade_trajectory 1` / `cl_grenadepreview 1` / `sv_grenade_trajectory_prac_pipreview 1`; allocate with `give weapon_smokegrenade|flashbang|hegrenade|molotov`. Practice-server one-liner: `sv_cheats 1; mp_warmup_end; mp_freezetime 0; sv_infinite_ammo 1; ammo_grenade_limit_total 5; bot_kick; mp_restartgame 1`.
- **Why it helps:** the user can take a lineup the app stored from a demo (which has exact `throw_pos`!) and **instantly position+aim** in a practice server. This is the bridge from "review" to "drill."
- **Fits THIS app (feature):** for any library lineup that has `throw_pos` (and we can store the thrower's view angle when mining from demo), generate a copyable block:
  `sv_cheats 1; setpos <tx> <ty> <tz>; setang <pitch> <yaw> 0; give weapon_<type>; cl_grenadepreview 1` — plus the aim/technique text. Add a "Copy practice setup" button on each lineup card and a "Generate practice config (.cfg)" for a whole `strat_group`. NOTE: we currently store `throw_pos` xyz but **not view angles**; add angle capture to demo-mining (frames have pitch/yaw at throw tick) to make `setang` exact — otherwise emit `setpos` only + the aim instruction.
- **Difficulty:** S (block) / M (angle capture). **Priority:** now (setpos block) / next (angles). **Confirmed.**

#### C2. Export to CS2's native annotation system (best-in-class)
- **Source:** https://github.com/kellran/annotations , https://github.com/ReneRebsdorf/CS2-annotations , https://steamcommunity.com/sharedfiles/filedetails/?id=3367125162 , https://steamcommunity.com/sharedfiles/filedetails/?id=3367477756
- **Observed:** CS2 has a **built-in lineup annotation system**: `annotation_create grenade [smoke|flash|he|fire|decoy] "label"`, `annotation_save <name>`, `annotation_load <name>`, `annotation_reload`. Files live in `…\game\csgo\annotations\` (one file per map, e.g. `de_mirage.txt`), each lineup = **3 nodes**: (1) **position** (boot icon, `Color` ct-blue/t-yellow), (2) **lineup/aim** (`Desc.Text` = aim + throw type e.g. "Middle click, jump throw"), (3) **destination** (`DistanceThreshold` = accuracy-circle radius). The text file is human-editable; after 2 good throws the helper hides, then 2 more to "graduate." Jan 21 2025 update added `workshop_annotation_submit` to publish to Workshop.
- **Why it helps:** this is the **native, free, in-game** way to drill lineups with on-screen helpers and pass/fail circles — far better UX than copy-pasting setpos. If the app can **emit a `de_<map>.txt` annotation file** from the library (or from a demo's missed lineups), the user drops it in the annotations folder and `annotation_load`s a custom drill set built from their own match.
- **Fits THIS app (feature):** write an exporter that converts library lineups (need throw pos+angle and land pos — both derivable from demo mining) into the 3-node annotation format with `DistanceThreshold` from our match radius. This is the single most differentiating practice feature available and is 100% public. Validate the exact node text against the two GitHub repos before shipping (their files are real samples).
- **Difficulty:** M (format is documented but fiddly; verify against sample files). **Priority:** next (high-value). **Confirmed.**

#### C3. "Practice this demo" routine = the round's missed/off-book util
- **Source:** composition of #4 (smoke-on-target), #2/#1 (bad flashes), https://refrag.gg/blog/getting-started-with-nadr-utility-hub/ (NADR `.nade <i>` teleports into a saved lineup; `.rethrow 0 1 2 3 4` re-throws a whole execute), https://yprac.com/ (custom workouts = "training routine with automatic setup + performance summary")
- **Observed:** Refrag/Yprac frame practice as **routines** auto-built from lineups; NADR can rethrow a whole execute at once and teleport per-lineup by index.
- **Why it helps:** closes the loop — the app already finds *what went wrong* (off-book smoke, team-flash, util dumped after contact); turn that directly into a **drill list**: "These 5 smokes missed their target / you never threw your A-exec mollies — here's a practice config."
- **Fits THIS app (feature):** after analysis, collect lineups that (a) the player threw but missed target (#4 fail), or (b) are in a `strat_group` the team ran but a member skipped, and emit either a setpos `.cfg` (C1) or an annotation file (C2) titled e.g. `practice_demo_<id>_mirage.txt`. One-click "Generate practice from this match."
- **Difficulty:** M (orchestrates C1/C2 + the quality signals). **Priority:** next. **Confirmed (pattern) / Inferred (our composition).**

#### C4. Practice-server starter config (generic, bundled)
- **Source:** https://www.ghostcap.com/cs2-practice-config , https://esportsrambles.com/blog/cs2-optimal-nade-practice-commands , https://swap.gg/blog/how-to-enable-grenade-trajectories-in-cs2
- **Observed:** standard copy-paste practice configs are universal and public (commands listed in C1).
- **Why it helps:** users practicing exported lineups need a ready practice server; bundling the config removes friction.
- **Fits THIS app:** ship a static "practice.cfg" + a one-paragraph "how to set up a nade practice server" help panel next to the export buttons. Trivial, no per-demo computation.
- **Difficulty:** S. **Priority:** later. **Confirmed.**

---

### D. UX patterns worth borrowing (structure only — no data copied)

- **Source:** https://csnades.gg/ , https://csnades.gg/mirage , https://www.cs2util.com/ , https://tracker.gg/cs2/lineups (403 to fetch; corroborated via search snippets — *Inferred*).
- **Observed patterns (consistent across all):**
  - Browse = **map picker → grenade-type filter (smoke/flash/molotov/HE/decoy) → side (CT/T) → callout/area filter**; cs2util adds a "Common spots" quick section.
  - **2D radar/minimap** with throw dot + landing dot + a connecting line; toggle **callouts** and **SimpleRadar** skin.
  - Lineup card fields: throw callout, target callout, **jump-throw yes/no**, **air time (s)**, **mouse button**, **movement type** (standing/walking/running/crouch), aim reference image, video, **copyable console command with coordinates** (cs2util).
  - **Report-outdated** button; favorites.
- **Why it helps / fits:** the app already draws single lineups on a 2D radar and stores most of these fields — adopting the map→type→side→callout filter bar and the air-time/movement/mouse-button fields (B2) brings the library UX to parity with the reference sites while keeping our own data. The "copyable console command" pattern is exactly C1.
- **Difficulty:** M (filter bar + a few schema fields). **Priority:** next. **Confirmed (structure) / Inferred (tracker.gg specifics).**

---

## Feasibility notes / gotchas
- **Frames are sampled** (`sample_rate ≈ 8/s`): the 800u-push (#5), peek-proximity in pop-flash (#2), and area-denial (#7) read enemy positions from frames — fine at smoke/molly timescales, but pop-flash peek-timing is near the sampling limit → label confidence "med," prefer the event-based parts (blind duration, det-lead) as the backbone.
- **Loadout-at-death** (#8): confirm `loadouts` snapshots grenades at the death tick; if not, reconstruct from give/throw events. Don't ship the \$-value stat until validated against a real demo (project's standing `--probe` discipline).
- **View angles for setpos drills** (C1/C2): we store `throw_pos` xyz but not pitch/yaw — add angle capture from the thrower's frame at the throw tick during demo mining; until then emit `setpos` + textual aim only.
- **Annotation file format** (C2): documented but node-structured — diff against the kellran / ReneRebsdorf sample `.txt` files before generating, and keep it behind a "beta export."
- **Policy:** all practice commands and the annotation format are **public Valve features / community-documented**; nothing here scrapes csnades.gg data — the library stays user-entered/imported, and grading uses the user's own lineups.

## Sources
- Leetify utility ratings (formulas): https://leetify.com/blog/utility-ratings/
- Leetify stats glossary (defs, 1.1s, 800u, unused-util): https://leetify.com/blog/leetify-stats-glossary/
- Leetify rating context: https://leetify.com/blog/what-is-leetify-rating/ , https://leetify.com/blog/cs2-benchmarks/
- CSNADES structure/UX: https://csnades.gg/ , https://csnades.gg/mirage
- CS2UTIL: https://www.cs2util.com/
- Tracker lineups (403; via search): https://tracker.gg/cs2/lineups
- Refrag NADR/Utility Hub: https://refrag.gg/ , https://refrag.gg/blog/getting-started-with-nadr-utility-hub/ , https://refrag.gg/blog/grenade-finder-the-fastest-way-to-find-lineups/ , https://wiki.refrag.gg/en/NADR
- Yprac: https://yprac.com/
- PRACC: https://pracc.com/counter-strike
- Practice commands: https://lineups.gg/guides/cs2-practice-commands/ , https://www.ghostcap.com/cs2-practice-config , https://esportsrambles.com/blog/cs2-optimal-nade-practice-commands , https://vredux.com/articles/cs2-teleport-command , https://swap.gg/blog/how-to-enable-grenade-trajectories-in-cs2
- CS2 annotation system: https://github.com/kellran/annotations , https://github.com/ReneRebsdorf/CS2-annotations , https://steamcommunity.com/sharedfiles/filedetails/?id=3367125162 , https://steamcommunity.com/sharedfiles/filedetails/?id=3367477756
- Flash/pop mechanics + demo review: https://dotesports.com/counter-strike/news/how-to-pop-flash-in-cs2 , https://cs2guide.net/guides/cs2-advanced-utility-guide/ , https://boosteria.org/guides/cs2-grenade-throws-guide-smoke-lineups-flash-basics , https://boosteria.org/guides/cs2-demo-review-guide-pros-look-improve-fast , https://smartcoach.gg/
