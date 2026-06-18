# Workstream B — Pro Tactics & Team Play → App-Detectable Signals

Research for **CS2DemoPlayer** (local Flask + demoparser2 coaching tool). Date: 2026-06-15.
Goal: convert how pros/coaches/analysts review TEAM CS2 into signals measurable from the
already-parsed data (per-tick positions/velocity/view-angles/HP/armor/money/weapon/inventory/
team/is_alive; round boundaries+winner+reason; kills; damages; bomb plant/defuse; grenade
trajectories+detonations w/ thrower+xyz; bullet shots).

> **Context — what the app ALREADY computes** (don't rebuild, EXTEND): KAST, ADR, opening duels,
> trades (1s+5s/600u, traded%, trade *opportunities*), clutches (1vX), win-prob swing
> (logistic 0.9/man, -1.1 plant; Opening/Trading/Firepower split), heuristic roles (T Entry/
> Lurker/Support/Rifler/AWP; CT Anchor/Rotator/Rifler/AWP), buy classification (5 buckets),
> per-side splits, post-plant WR + retake WR (team_coaching), team loss taxonomy, mistake
> detectors (mid-round K/D leak, isolated death >1000u, bad save, dry_opening = first death w/
> no friendly flash on enemy in prior 2.5s, predictable death zone ≥4×, clumping 2+ die within
> 4s & ≤350u, eco_discipline, moving_shots), nade library + actual-vs-lineup match.
> **The signals below are mostly NEW TEAM-LEVEL layers on top of this individual base.**

---

## ★ TOP DETECTABLE SIGNALS TO BUILD NOW (ranked) ★

Ranked by (coaching value × how cleanly the parsed data supports it × low overlap with existing).

| # | Signal | One-line detectable definition | Diff | Why it's top |
|---|--------|-------------------------------|------|--------------|
| 1 | **Default-shape & commit-timing classifier** | Cluster T positions at freeze-end+5s into a split label (2-1-2 / 3-1-1 / stack), then detect the **commit tick** = when ≥3 players' velocity vectors converge toward one site; report "default→execute time". | M | Names the team's *plan* every round — nothing else does. Feeds anti-strat, fakes, pacing. Pure positions/velocity. |
| 2 | **Trade-window grade (0.5–1.0s refrap) + un-tradeable-peek flag** | For each death, was a teammate close enough (≤500–600u, path-reachable) AND did a refrag land ≤1.0s? Flag deaths where **no teammate was in trade range at all** ("peeked alone"). | S | Pros' #1 rule: "if you can't trade, don't peek." App has trades at 5s — tighten to the *pro* 1.0s window + the spacing precondition. Direct upgrade to existing trade model. |
| 3 | **Execute synchronization score** | On a committed site take, measure spread of (entry-contact tick) vs (support flash detonations) vs (smokes blooming). Reward flashes popping within ±1s of entry; penalize "smoke landed after first death". | M | Coaches judge executes by *timing not volume*. Uses detonation events + first-contact tick the app already has. High differentiation. |
| 4 | **Crossfire detection (post-plant & site hold)** | At a snapshot (plant tick / first contact), count pairs of teammates whose view-cones cover the same chokepoint from ≥~30° different positions and aren't on the same line. Output "crossfire set up: Y/N" + who was solo. | M | Single most-cited positioning concept (hard/soft/layered). View-angles + positions make it computable. Drives post-plant + CT-hold cards. |
| 5 | **Map-control timing windows (per-map key area)** | Did a team win/lose control of a named area (e.g. Banana, Mid, Long) by a time threshold? = first player to enter zone X and survive Y sec, before clock T. | M | Pros think in timing windows (Banana<1:30, Long~10s). Uses last_place_name + clock. Becomes per-map benchmark cards. |
| 6 | **Spacing / clumping over a whole push (not just deaths)** | Track squad spread (mean pairwise distance of alive teammates) through the round; flag "too tight" (<~350u sustained → one nade kills 2) and "too loose / desync" (entry >~700u ahead with no trailer). | S | Generalizes the existing death-time clumping detector to *continuous* spacing — the real signal pros watch. Positions only. |
| 7 | **Over-extension after a kill (entry isolation)** | After a player gets a frag, did they advance >~250–400u deeper while the nearest teammate stayed >600u back, then die untraded? | S | Refrag's #1 entry mistake. Distances + kill ticks already present. Individual card with replay jump. |
| 8 | **Post-plant geometry & "playing the clock"** | At plant: are Ts spread to cover bomb from 2+ angles (crossfire), is ≥1 player with sightline to the bomb, and did they hold for the 40s fuse vs over-peek? Flag "stacked one angle" + "gave up time advantage". | M | Leetify/Refrad afterplant pillars; the app has post-plant WR but not *why*. Bomb xyz + positions + plant tick. |
| 9 | **Retake discipline (man-count + tap-the-bomb + util overlap)** | On CT retakes: flag retaking at a man-DISADVANTAGE; detect "tapped the bomb" (defuse started early to bait); detect **duplicated utility** (2 same-type nades land ≤~150u within 2s = wasted). | M | Concrete pro rules ("don't retake 3v5", "tap bomb ASAP", "two smokes same spot = waste"). All event-derivable. |
| 10 | **Anti-strat / tendency report (cross-round patterns)** | Aggregate per team over the demo: default-shape frequency, first-area-contested frequency, A:B site-hit ratio by buy type, pistol-round pattern, fake frequency. Output "predictable: hits A on 5/7 full buys". | M | This is literally what opponents' analysts build. Reuses #1+#5 outputs across rounds. Massive value for a *team* tool. |

---

## FULL CONCEPT → SIGNAL CATALOGUE

Each entry: **Concept** / **Source** / **Detectable signal (from parsed data, with threshold)** /
**Coaching output** / **Difficulty (S/M/L/XL)** / **Priority (now/next/later)** / **Confirmed|Inferred**.

---

### A. DEFAULTS (T & CT)

**A1 — T default shape (spread to control areas, gather info, stay flexible).**
Pros: a default = players spread across key areas, watch for CT aggression/mistakes, DON'T commit;
common splits **2-1-2, 2-2-1, 3-1-1**; decision window ~**30–45s** before execute.
- Source: refrag.gg/blog/what-is-a-default-cs2 ; csgo-guides.com/gameplay/strategy ; switchbladegaming.com/cs2/maps-guide
- **Signal:** at freeze-end + ~5s, take alive T positions → assign each to nearest named zone
  (`last_place_name` or radar-quadrant) → reduce to a split signature (count per macro-area
  A/Mid/B). Classify: balanced default (≈2-1-2 / 2-2-1), heavy split (3-1-1), or stack (≥3 one area).
  Threshold: macro-area = 3 buckets per map; "stack" if ≥3 players in one bucket at T+5s.
- **Coaching:** round-card label "Default: 2-1-2 (info)"; team report "default variety" (low variety = predictable).
- Difficulty: **M** · Priority: **now** · **Confirmed** (concept) / Inferred (exact bucketing)

**A2 — Default → trigger → finish (when to convert default into execute).**
Pros: default holds, then a **trigger** (a pick / detected weakness / time threshold) flips to a
coordinated hit; "Default → Trigger → Finish"; example default duration ~**20s** before grouped execute.
- Source: boosteria.org/guides/cs2-communication-guide-calls-trades-mid-round-plans ; csgo-guides.com/gameplay/strategy
- **Signal:** detect **commit tick** = first tick where ≥3 alive Ts have velocity vectors pointing
  toward the same site centroid AND inter-player distance to that site is decreasing for ≥1.5s.
  "Default duration" = commit_tick − freeze_end. Tie a *trigger* to it: was there a T kill or a
  CT seen (enemy in a teammate's view-cone) in the 3s before commit? If commit with no trigger →
  "blind commit / forced timing".
- **Coaching:** "Avg default→execute 24s (healthy)"; flag "committed with no info trigger ×N".
- Difficulty: **M** · Priority: **now** · **Confirmed**

**A3 — CT default setup (3-1-1 / 2-1-2, rotate on info, hold flexible).**
Pros: solid CT default e.g. **3-1-1** (3 Long, 1 B, 1 Mid) for flexible rotations; one anchor per site
+ rotators; "rotate early, read buy & movement, rotate before the attack hits."
- Source: boosteria.org/guides/hold-sites-ct-cs2-setups-rotations-2026 ; csgo-guides.com/gameplay/strategy ; hotspawn.com/.../how-to-play-ct-side-in-cs2
- **Signal:** mirror A1 for CT side at freeze-end → CT split signature; identify the **anchor**
  (lowest total displacement over round) and **rotators** (highest). Flag "double-anchor / no rotator"
  if 2 players both move <~300u all round (slow to help).
- **Coaching:** "CT default 2-1-2"; "B had no rotator support 4 rounds (anchor died alone)".
- Difficulty: **M** · Priority: **next** · **Confirmed** (concept)

---

### B. EXECUTES

**B1 — Synchronized execute (utility timed to movement, not just accurate).**
Pros: "strong utility is *synchronized with movement*"; flashes pop AS entry moves (early flash =
defender recovers); "a smoke that lands after your teammates already died is too late." Example A-Mirage
execute = 5 smokes (CT/Jungle/Stairs/Connector/Ticket) before/at the take.
- Source: (search) farmskins execute guide ; cs2hype utility guides ; counter-strike.io mastering-utility
- **Signal:** on a committed site take (from A2 commit tick), compute three timestamps: (a) first
  entry-contact = first shot/damage between an attacking T and a defending CT inside/near the site;
  (b) support **flash detonation** ticks; (c) **smoke bloom** ticks (smoke detonate event + ~ to full).
  Score = fraction of support flashes detonating within **±1.0s** of entry contact, minus penalty for
  any execute smoke detonating *after* the first attacker death. Count smokes used on the take.
- **Coaching:** "Execute sync 2/3 flashes on-time"; "B exec: smoke bloomed 1.8s after entry died — late util".
- Difficulty: **M** · Priority: **now** · **Confirmed**

**B2 — Util-supported entry vs DRY peek (no util before contact).**
Pros: top entry mistake = entering without flash/smoke/molly = "pure gambling"; "don't swing before
the flash pops / smoke blooms." (App already has `dry_opening` = first death w/ no friendly flash in
prior 2.5s — extend to *any* entry contact, include smokes.)
- Source: refrag.gg/blog/4-of-the-worst-cs2-entry-fragging-mistakes ; boostroom how-to-stop-dying-first
- **Signal:** for each round's first attacking contact, check if ANY friendly utility (flash detonated
  on a covering angle, OR smoke blooming on the contested sightline) landed in the **2.5s** before
  contact. None → "dry entry". Aggregate per team: dry-entry rate.
- **Coaching:** team card "Dry entries 6/14 T rounds — entries unsupported"; jump to round.
- Difficulty: **S** (extends existing dry_opening) · Priority: **now** · **Confirmed**

**B3 — Fake (util/noise at one site, then hit the other).**
Pros: fakes draw rotations then hit the other site; effective fake window ~**10–15s**; "execute A but
fake with two players, rotate rest to B."
- Source: csgo-guides.com/gameplay/strategy ; cs2hype dynamic-map-rotations
- **Signal:** detect a fake = utility thrown at site X (≥1–2 nades land near site X) by only 1–2
  players, while ≥3 players are simultaneously moving toward site Y, and the bomb plants at Y. Cross
  with CT response: did a CT rotate X→Y after the fake (a rotator's displacement spikes toward Y)?
  "Fake worked" if a CT left Y within ~5s of the fake util.
- **Coaching:** "Fake B → hit A drew 1 rotator (worked)"; T-report "fake usage 2 rounds".
- Difficulty: **L** (needs fake heuristic + CT-response correlation) · Priority: **next** · Inferred

---

### C. RETAKES (CT)

**C1 — Retake man-advantage discipline ("don't retake 3v5").**
Pros: evaluate man-count before retaking — a 3v5 retake "usually isn't worth it"; when up, play slow,
group, trade only; some sites (Inferno) notoriously hard to retake.
- Source: refrag.gg/blog/reclaim-control-5-tips ; 1v9.gg retake guide ; strafe.com how-to-play-retakes
- **Signal:** at bomb plant tick, count alive CT vs alive T. If CTs attempt a retake (a CT enters the
  planted-site zone post-plant) while at a man **disadvantage** (CT_alive < T_alive) → flag
  "retook at disadvantage". Tie to outcome (round lost?). Track retake attempts vs saves.
- **Coaching:** "Retook 3v4 and lost ×3 — save instead"; complements existing retake WR.
- Difficulty: **S** · Priority: **now** · **Confirmed**

**C2 — "Tap the bomb ASAP" (bait defuse to force peeks).**
Pros: start the defuse immediately on entry — the defuse sound forces Ts to react/peek, exposing them.
- Source: refrag.gg/blog/reclaim-control-5-tips
- **Signal:** on retakes, detect an **early defuse start** (defuse begin event within ~3s of first CT
  entering the site) even if interrupted. Reward presence; flag retakes where CTs cleared slowly and
  the bomb exploded with no defuse attempt while CTs were alive nearby.
- **Coaching:** "Good: tapped bomb to bait peek (round won)"; "No defuse pressure — bomb ran out 5v… ".
- Difficulty: **M** (needs defuse start/interrupt; check parser exposes it) · Priority: **next** · **Confirmed**

**C3 — Duplicated / wasted retake utility ("two smokes same spot = waste").**
Pros: "Two smokes or two incendiaries thrown at the same location is a complete waste"; coordinate util.
- Source: refrag.gg/blog/reclaim-control-5-tips
- **Signal:** in a retake window, find pairs of SAME-type detonations from different throwers landing
  **≤~150u apart within ~2s** → "duplicate util". Also flag overlapping flashes that blind teammates
  (cross with existing team-flash detector).
- **Coaching:** "2 mollies same spot on retake — wasted util"; team util-discipline metric.
- Difficulty: **S** (detonation events + distance) · Priority: **next** · **Confirmed**

**C4 — Don't take first contact alone on a retake (grouped entry).**
Pros: "avoid taking first contact alone" — solo contact reveals where the rest of the CTs are; wait
for coordinated contact.
- Source: refrag.gg/blog/reclaim-control-5-tips
- **Signal:** on retake, was the first CT to make contact >~600u from the next-nearest CT? → "solo
  retake contact". (Same machinery as trade-spacing C/B2 but scoped to post-plant CT.)
- **Coaching:** "Entered B retake alone, died untraded — group the retake."
- Difficulty: **S** · Priority: **next** · Inferred

---

### D. POST-PLANTS / AFTERPLANTS (T)

**D1 — Post-plant crossfire spread (cover bomb from 2+ angles).**
Pros: spread to force CTs to fight 2 players at once; "move to places where you can watch multiple
choke points"; the 4th player ready to hold the crossfire angle, not stuck in the doorway; afterplant
= **40s** fuse, T becomes the defender.
- Source: refrag.gg/blog/hold-the-line-5-tips ; (search) farmskins strategic-bombsite-takes ; cs2guide bomb-planting
- **Signal:** at plant tick (and +3s), cluster alive Ts; "spread/crossfire" if ≥2 alive Ts are
  >~250u apart AND their view-cones cover different approaches to the bomb (angular separation ≥~30°);
  "stacked one angle" if all alive Ts within ~250u / same sightline. Also: is ≥1 T holding a sightline
  to the bomb at all? Did they survive to the 40s fuse vs over-peek (left cover before 0:10 left)?
- **Coaching:** "Post-plant: stacked Newbox, no crossfire (lost retake)"; "Good: E-Box/Pizza crossfire held".
- Difficulty: **M** · Priority: **now** · **Confirmed**

**D2 — Plant position quality (plant where you can cover/see it).**
Pros: "a bad plant position can ruin an afterplant defense"; plant where visible from multiple cleared
angles; bomb placement dictates positioning.
- Source: refrag.gg/blog/hold-the-line-5-tips
- **Signal:** classify plant location (bomb xyz at plant) into known plant spots per site (default vs
  hidden/aggressive); check post-plant whether any T retained a sightline to that xyz. Flag plants
  with NO covering T sightline at +3s = "blind plant".
- **Coaching:** "Planted default but no one watched it — free defuse risk."
- Difficulty: **M** (needs per-site plant-spot reference; can start map-agnostic w/ sightline check) · Priority: **later** · **Confirmed**

**D3 — Post-plant clock management (play for the fuse / don't over-peek).**
Pros: with a man advantage post-plant "play passively, run the clock down"; activity ≠ control;
many CTs over-focus the entry and ignore the afterplant.
- Source: refrag.gg/blog/hold-the-line-5-tips ; boosteria.org/guides/cs2-positioning-fundamentals-2026
- **Signal:** in a man-advantage post-plant, did a T leave cover / wide-peek into the open while ahead
  (velocity into an exposed zone with >~20s left and no contact)? → "over-peeked a won post-plant".
  Conversely reward holding tight to the fuse.
- **Coaching:** "Up 3v1 post-plant, dry-peeked and lost — play the clock."
- Difficulty: **M** · Priority: **next** · Inferred

---

### E. MAP CONTROL & TIMING WINDOWS

**E1 — Key-area control by a timing window.**
Pros: control of map center enables site hits ("mid control creates site opportunities, not the
reverse"); concrete windows: Inferno Banana before **1:30**; Dust2 Long ~**10s**; first **15s** = info/space
phase; aggressive mid pushes "change the round in seconds."
- Source: (search) cs2hype map-control ; cs2pulse aggressive-mid lessons ; switchbladegaming maps-guide ; bo3.gg awp-positioning
- **Signal:** define per-map "key zones" (Mid/Banana/Long/Connector). For each round, who controlled
  zone Z first = first player to be inside Z and survive ≥~3s; record the **clock time** of control.
  Threshold idea: per-map target time (e.g. Banana control before 1:30 / ~15s elapsed). Compare T vs CT.
- **Coaching:** "Lost Banana control 6/12 T rounds (avg 0:18 late)"; per-map control-timing benchmark.
- Difficulty: **M** (needs per-map zone polygons) · Priority: **next** · **Confirmed**

**E2 — First-contact location & timing (where rounds open).**
Pros: rounds are shaped by where/when first contact happens; CT early aggression valuable only when
"survives, delays 15s, uses utility, gives info"; reading the first-contact point reveals the plan.
- Source: boostroom how-to-stop-dying-first ; cs2pulse aggressive-mid ; csgo-guides strategy
- **Signal:** per round, log first kill/damage **zone + clock**. Aggregate: heatmap + "T first contact
  at Mid in 0–10s on X% of rounds". CT aggression value = a CT who makes early contact, survives ≥15s,
  and threw util before dying.
- **Coaching:** "Your first contact is always Mid <10s — predictable / get baited"; "Good aggro: survived 17s + flashed".
- Difficulty: **S** (reuses kills + last_place_name + clock) · Priority: **now** · **Confirmed**

---

### F. TRADING & SPACING

**F1 — Pro trade window (0.5–1.0s refrag) + "if you can't trade, don't peek".**
Pros: real trade = refrag within **0.5–1.0s** of the teammate dying, before the enemy repositions;
spacing "close enough to refrag instantly, far enough not to get sprayed together"; "**If you can't
trade, you shouldn't be peeking together.**" (App has 1s+5s trades + 600u opportunity — sharpen to the
1.0s pro window + the *was a trade even possible* precondition.)
- Source: boosteria.org/guides/cs2-communication-guide-calls-trades-mid-round-plans ; bitskins entry-fragging ; cs2pulse entry-fragger
- **Signal:** for each death: (a) **trade-possible?** = was a teammate within ~500–600u AND with an
  unobstructed-ish path/sightline at death tick; (b) **traded?** = enemy killer died ≤**1.0s** later.
  Per team: trade-possible rate (spacing), and traded% within 1.0s (execution). Flag "un-tradeable
  peek" = death with NO teammate in range (the spacing failure pros hate).
- **Coaching:** "42% of your deaths were un-tradeable (peeked alone)"; "Trade execution 1.0s: 30%".
- Difficulty: **S** · Priority: **now** · **Confirmed**

**F2 — Continuous squad spacing (clumping vs desync).**
Pros: good spacing = "close enough to punish contact but not so close one spray solves both";
poor spacing mislabeled as "baiting" but is really desync; don't "line up / share the same flash fate."
- Source: egw.news/.../art-of-demo-review ; boosteria positioning-fundamentals ; boostroom how-to-stop-dying-first
- **Signal:** sample alive-teammate **mean pairwise distance** every ~0.5s through the round.
  "Too tight" = sustained (<~350u) for ≥2s with ≥3 alive (one HE/molly kills multiple — ties to
  existing clumping detector but continuous). "Desync/over-loose" = lead player >~700u ahead of the
  pack while pushing. Report per-round spacing band + flags.
- **Coaching:** "B push too tight (avg 280u) — one molotov for 2"; "Entry desync: 800u ahead, no trailer".
- Difficulty: **S** · Priority: **now** · **Confirmed**

**F3 — Over-extension after a frag.**
Pros: pushing too deep after a kill isolates you from traders; "hold the space you've earned," clear
next section with util first.
- Source: refrag.gg/blog/4-of-the-worst-cs2-entry-fragging-mistakes ; bitskins entry-fragging
- **Signal:** after a player's kill, track their displacement in the next ~3s; flag if they advance
  >~250–400u "into" enemy territory (toward enemy spawn / deeper into site) while nearest teammate is
  >600u back, AND they die untraded. (Sharper, kill-anchored version of the existing isolated-death.)
- **Coaching:** "Got entry then pushed deep alone → died untraded (×N). Hold the angle."
- Difficulty: **S** · Priority: **now** · **Confirmed**

**F4 — Crossfire setup (hard/soft/layered) on holds & post-plants.**
Pros: crossfire = if enemy aims at A, B kills them & vice-versa; **hard** (different directions),
**soft** (same lane, different depth), **layered** (timing/bait then second); the "two-man trade
triangle" (one holds contact, one watches the trade/escape).
- Source: boosteria.org/guides/cs2-positioning-fundamentals-2026 ; refrag afterplant tips
- **Signal:** at a snapshot (first site contact / plant), for each pair of nearby defenders, test if
  their view-cones both cover a common chokepoint cell while the players are spatially separated
  (≥~250u and angular separation ≥~30°) → crossfire pair. Round has a crossfire if ≥1 such pair on the
  contested approach. "Solo hold" = the defender of the contested angle has no crossfire partner.
- **Coaching:** "B held solo (no crossfire) 5 rounds — anchor died first each time"; "Good: Dark/Newbox crossfire".
- Difficulty: **M** (view-cone vs chokepoint geometry) · Priority: **next** · **Confirmed**

---

### G. ROLE RESPONSIBILITIES (high level → measurable)

**G1 — Role behavioral fingerprints (entry/support/lurk/anchor/rotator/AWP/IGL/star).**
Pros' role markers: **Entry** = first contact, high trade-FOR rate, trades life for space; **Support**
= throws most util/flashes-for-teammates, secondary in fights; **Lurker** = solitary, map extremity,
late activation, away from main push; **Anchor** = minimal rotation, long angle holds, survival
priority; **Rotator** = max movement between sites; **AWP** = high AWP-hold fraction; **IGL** = often
lower stats / leadership; **Star** = top frags, dictates own position.
- Source: refrag.gg/blog/cs2-team-roles-explained ; bitskins CS2-roles-101-entry-fragging ; blast.tv cs2-lurker-guide ; community.skin.club player-roles-cs2
- **Signal:** the app already heuristically labels roles — ADD measurable backing per role and a
  "role consistency" check: Entry = % rounds making first/early contact; Support = util-for-teammates
  count + flash-assist; Lurker = avg distance-to-team-centroid (top quartile) + late first-contact
  tick; Anchor = lowest displacement + longest single-angle dwell; Rotator = highest site-to-site
  displacement; AWP = awp-hold fraction. Flag **role conflict** (2 players both trying to entry; no one
  supporting).
- **Coaching:** "No dedicated support — 0 flashes-for-team on 8 executes"; "2 players both entried → no trade".
- Difficulty: **M** · Priority: **next** · **Confirmed**

**G2 — Lurk timing & cut-rotation value.**
Pros: lurker starts passive on map extremity, activates on team commitment, "perfect timing is when
the bomb plants — catch CTs running"; lurk should *connect to the round* (hitting B → A lurk stops
rotations).
- Source: blast.tv cs2-lurker-guide ; (search) thunderpick / clubtsp lurking ; cs2hype rotations
- **Signal:** identify the lurker (max distance-to-centroid). Measure lurk activation tick vs team
  commit tick / plant tick. "Good cut" = lurker gets a kill on a CT whose velocity was *toward* the
  bombsite (rotating) within ~5s of the team's hit/plant. "Dead weight lurk" = lurker died with no
  kill far from team before any team contact.
- **Coaching:** "Lurk cut a rotator on plant (great)"; "Lurk died early 60u from spawn — wasted man".
- Difficulty: **M** · Priority: **later** · **Confirmed**

---

### H. IGL WORKFLOWS & MID-ROUND CALLING

**H1 — Man-advantage discipline ("up one — slow down, group, trade only").**
Pros: highest-impact mid-round call when up a man = group, remove isolated duels, trade only; with man
DISadvantage, look for picks/equalizers.
- Source: boosteria.org/guides/cs2-communication-guide-calls-trades-mid-round-plans ; blog.cs2.ad best-igls
- **Signal:** after a kill creates a man advantage, measure team behavior in next ~10s: did the team
  **regroup** (mean pairwise distance shrinks / converges) and avoid isolated peeks, or did someone
  immediately take a solo un-tradeable duel and die (giving the number back)? Flag "threw the man
  advantage" = went from +1 to even within ~15s via an isolated death. (App already flags "threw 2+
  adv" at round level — add the +1 micro-version with the regroup check.)
- **Coaching:** "Up 5v4, X solo-peeked and died — should group/trade only (×N)."
- Difficulty: **M** · Priority: **next** · **Confirmed**

**H2 — Mid-round adaptation speed when the plan breaks.**
Pros: great IGLs adapt fast on broken plans; review = "how quickly do they react when the plan breaks";
over-rotate vs under-rotate; "mid-round mistakes disguise as unlucky timing."
- Source: egw.news art-of-demo-review ; boosteria demo-review-guide ; csgo-guides igl
- **Signal:** detect a "plan break" event (e.g. entry dies in first 10s, or man-disadvantage early) and
  measure **reaction latency** = time until the team makes a coherent new move (≥3 players' velocity
  re-converge on a new target zone). Long latency / scattered movement = slow adaptation.
- **Coaching:** "After losing entry you stalled 12s with no plan (lost map control)."
- Difficulty: **L** (defining "coherent new move" robustly) · Priority: **later** · Inferred

**H3 — Pacing: time of bomb-plant / commit (fast vs slow vs default).**
Pros: defaults take ~30–45s; fakes/splits have staggered timing; pacing (fast hit vs slow default vs
late commit) is a core IGL signature analysts read.
- Source: csgo-guides strategy ; refrag what-is-a-default ; boosteria communication-guide
- **Signal:** per round, log **commit tick** (A2) and **plant clock**. Distribution per team/buy-type:
  fast (<~15s), default (~15–45s), late (>~45s). Anti-strat value: "On full buys you plant at 0:35 ±5
  every time."
- **Coaching:** "Pacing too uniform — predictable timing"; benchmark vs healthy spread.
- Difficulty: **S** (once A2 exists) · Priority: **next** · **Confirmed**

---

### I. ANTI-STRAT / VETO / PREP

**I1 — Cross-round tendency report (the anti-strat the opponent builds on you).**
Pros: anti-strat = catalogue opponent's common executes, default setups, util patterns, timing
tendencies, predictable positions/rotations, pistol patterns, force-buy tendencies; "ban what they
love"; "experienced analysts spot subtle tendencies / repeating patterns."
- Source: cs2guide.net/.../team-anti-strat-preparation (403 — concept Inferred from search snippet) ;
  egw.news art-of-demo-review ; whatisesports map-veto-strategies ; bo3.gg best-maps-to-pick-and-ban
- **Signal:** aggregate the per-round outputs above into a **team tendency sheet**: default-shape
  histogram (A1), site-hit ratio A:B by buy type, first-area-contested frequency (E2), commit-pacing
  distribution (H3), fake frequency (B3), pistol-round approach, force-buy frequency + outcome.
  Predictability score = entropy of these distributions (low entropy = predictable).
- **Coaching:** "PREDICTABLE: full buys → A site 6/7 @ 0:35; B only on force." (Gold for a team tool.)
- Difficulty: **M** (mostly aggregation of other signals) · Priority: **next** · **Confirmed**

**I2 — Predictable position / death-zone repetition.**
Pros: "avoid holding identical positions every round — enemies pre-aim common spots"; predictable
re-peeks/positions get punished. (App already has `predictable` death-zone ≥4× — extend to *holding*
positions, not just deaths.)
- Source: boostroom how-to-stop-dying-first ; boosteria positioning-fundamentals
- **Signal:** per player, cluster their freeze-end / first-30s positions across rounds (same side);
  if a player occupies the same ~150u cell on ≥~40% of same-side rounds → "predictable setup". Also
  predictable re-peek = peeking the same angle twice in a round (existing concept) generalized.
- **Coaching:** "You anchor the exact same Pit spot every CT round — vary it."
- Difficulty: **S** (extends existing) · Priority: **next** · **Confirmed**

**I3 — Pistol & force-buy patterns (set plays).**
Pros: teams have "set patterns" on pistol rounds + force-buys; analysts study these specifically;
force budget ~$2,000–3,500 enables aggressive/rush plays.
- Source: egw.news art-of-demo-review ; csgo-guides strategy ; (search) cs.money economy-guide
- **Signal:** isolate pistol rounds (rounds 1 & 13 on MR12 — app already detects pistol via side-flip+
  low-equip) and force-buy rounds (buy bucket = force) → report their default-shape (A1), site hit,
  and outcome. "Pistol: stack A rush 2/2"; "Force buys: rush B 3/3, won 1."
- **Coaching:** "Your pistol round is identical both halves — vary the pistol exec."
- Difficulty: **S** (buy classification already exists) · Priority: **next** · **Confirmed**

**I4 — Veto / map prep (out of demo scope, note for completeness).**
Pros: veto = ban what opponents love, scout recent maps, don't run untested maps; pick a single veto
caller.
- Source: whatisesports map-veto-strategies ; bo3.gg best-maps-to-pick-and-ban ; foolstools veto-guide
- **Signal:** NOT per-demo detectable (no veto data in a .dem). **App angle:** if multiple demos are
  stored, aggregate **per-map win% + per-map tendency sheets** across a team's demos → a "scouting
  report" / veto helper. (Cross-demo feature, not single-round.)
- **Coaching:** "vs this opp: they're 78% on Mirage A-exec heavy — ban Mirage / prep A retake."
- Difficulty: **L** (multi-demo aggregation layer) · Priority: **later** · **Confirmed** (concept)

---

### J. ROUND-REVIEW METHOD & FREEZETIME / EARLY INFO

**J1 — "Process before outcome" / "the round before the fight."**
Pros: review the *setup* (positioning, util, timing, spacing) that created the duel, not the duel
itself; "utility either created safety/time/space/pressure/info — or it did not"; bad spacing gets
mislabeled as baiting. This is the framing the whole catalogue serves.
- Source: boosteria.org/guides/cs2-demo-review-guide-pros-look-improve-fast ; egw.news art-of-demo-review
- **Signal (UI/feed framing):** every death/loss card should surface the **preconditions** the app can
  now measure — was it tradeable (F1), spaced (F2), util-supported (B2), in a crossfire (F4), default
  vs commit (A2)? i.e. attach the "why the fight was bad" context, not just "you died."
- **Coaching:** card body = "Death context: un-tradeable + dry + over-extended" with one-click replay.
- Difficulty: **S** (composition of other signals into the existing insight feed) · Priority: **now** · **Confirmed**

**J2 — Freezetime / early-round info-gathering value.**
Pros: first 15s = info & space phase; a CT who "survives, delays 15s, uses util, gives info" is valuable
even without a kill; defaults exist to *get info before deciding*.
- Source: cs2pulse aggressive-mid lessons ; refrag what-is-a-default ; boostroom how-to-stop-dying-first
- **Signal:** "info trade" value = a player who, before dying early, (a) damaged/flashed an enemy or
  (b) spotted ≥1 enemy (enemy entered their view-cone) AND survived ≥~10–15s → credit "gave info /
  bought time" (reframes some early deaths as not-wasted). Pairs with A2 trigger detection.
- **Coaching:** "Lost mid but spotted 2 + delayed 16s — info trade, not a throw."
- Difficulty: **M** · Priority: **later** · Inferred

**J3 — Rotation correctness (over/under-rotate, rotate on info not contact).**
Pros: "rotate early, not late — waiting for contact means you're already outnumbered"; over-rotation
loses to fakes, under-rotation loses the real site; mental timer ~**7–10s** without contact suggests
commitment elsewhere; early rotation trigger = two quick teammate elims on one site.
- Source: cs2hype dynamic-map-rotations ; boosteria hold-sites-ct ; boosteria cs2-site-anchoring-guide
- **Signal (CT):** detect a CT rotation = a defender's sustained displacement from site X toward site Y.
  Grade vs where the bomb actually goes: **under-rotate** = too few CTs at the planted site at plant
  (e.g. 1 vs 3 attackers); **over-rotate** = ≥2 CTs left a site that turned out to be the real target
  (rotated into a fake). Timing: did the rotation start before/after first contact at the real site?
- **Coaching:** "Over-rotated to A on the B fake (×3) — read the default"; "B left undefended (1v4 at plant)".
- Difficulty: **M** · Priority: **next** · **Confirmed**

---

## METRIC-SPEC ANCHORS (definitions to reuse verbatim)

These give exact thresholds the app should adopt for consistency with how analysts measure:

- **HLTV Rating 3.0** (hltv.org/news/42485): six sub-ratings = Kills, Damage, Survival, KAST,
  Multi-Kills, **Round Swing** (replaces Impact). **Economy adjustment**: duel value scaled by
  equipment tier matchup (rifle-v-rifle ~48% WR → ~1.10 kill pts; vs unarmored → 0.54). **Round Swing**
  = win-prob delta per kill, credit split by final damage / damage share / flash assists / trade
  status; **trade denial = two kills within 5s**; **clutch 1v1 ~+50% swing vs opening ~+20%**; saving
  punished like a failed clutch. AWPer duels penalized (56–60% WR). → The app's win-prob-swing already
  mirrors this; ADD economy-adjusted duel weighting + the explicit trade-denial/clutch multipliers.
  *Confirmed.*
- **Leetify glossary** (leetify.com/blog/leetify-stats-glossary): counter-strafe = shots at **<34%
  max velocity**; time-to-damage = median spot→first-damage, excludes ≥1s; flash-to-kill = enemy
  killed while blinded **≥1.1s**; enemies/teammates-flashed = blinded ≥1.1s per flash; unused utility
  = nade value remaining on death; **[CT] smoke that stopped a push = % of CT smokes with an enemy
  within 800u**; crosshair placement = degrees moved spot→damage; accuracy(spotted) = hits/shots while
  an enemy is spotted. → adopt these exact numbers; the **800u smoke-stopped-push** and **unused-util-
  value** are easy team-util-quality wins. *Confirmed.*
- **Leetify Rating split** (from project memory, prior research): 35% killer / 30% damagers / 15%
  flash-assist / 20% traded-death. *Confirmed (memory).*
- **Economy reward context** for win-prob (cs.money / esportsinsider economy guides): plant +$300 to
  planter; **+$800 to whole T team if they lose AFTER a plant**; round win $3250 ($3500 on
  explosion/defuse). Useful when modeling buy reads / loss-bonus state. *Confirmed.*

---

## NOTES / CAVEATS
- **Map-zone polygons are the main enabler.** Signals A1/A3/E1/E2/F4/G2/J3 lean on named macro-areas.
  The app already pulls `last_place_name` per frame (callout) AND has per-map radar calibration +
  extracted 3D geometry — start with `last_place_name` macro-buckets; refine with hand-drawn polygons later.
- **View-cone signals (crossfire D1/F4, "spotted" J2)** need yaw/pitch (parsed) + a sightline/FOV
  approximation. A cheap version: 2D yaw cone (±~40°) toward a chokepoint cell, ignoring full LOS
  occlusion at first; the 3D collision mesh (already extracted per map) can later add true LOS raycasts.
- **Reuse, don't duplicate:** F1/F3/H1 sharpen existing trade/isolated-death/threw-advantage
  detectors (tighten windows to the pro 1.0s, add the trade-possible precondition, add the regroup
  check). A1/A2 are the new backbone that I1/H3/B3/J3 all consume.
- **`cs2guide.net` anti-strat page returned 403** — I1's anti-strat specifics are Inferred from the
  search snippet + corroborated by egw.news demo-review article (Confirmed at concept level).
- All thresholds (distances in Source units ~ "u", seconds) are **starting points to calibrate** on the
  real demos already in `uploads/` (de_anubis EthanN 30r, de_dust2 30r) — tune against eyeballed rounds.

## SOURCES
- HLTV data revolution — https://www.hltv.org/news/40128/why-were-still-waiting-for-counter-strikes-data-revolution
- HLTV Rating 3.0 — https://www.hltv.org/news/42485/introducing-rating-30
- Leetify stats glossary — https://leetify.com/blog/leetify-stats-glossary/
- Refrag — What is a default — https://refrag.gg/blog/what-is-a-default-cs2/
- Refrag — 4 worst entry mistakes — https://refrag.gg/blog/4-of-the-worst-cs2-entry-fragging-mistakes-that-you-probably-make/
- Refrag — 5 retake tips — https://refrag.gg/blog/reclaim-control-5-tips-to-improve-your-retakes-in-cs2/
- Refrag — 5 afterplant tips — https://refrag.gg/blog/hold-the-line-5-tips-to-improve-your-afterplant-play-in-cs2/
- Refrag — team roles — https://refrag.gg/blog/cs2-team-roles-explained/
- Boosteria — demo review guide — https://boosteria.org/guides/cs2-demo-review-guide-pros-look-improve-fast
- Boosteria — communication / trades / mid-round — https://boosteria.org/guides/cs2-communication-guide-calls-trades-mid-round-plans
- Boosteria — positioning fundamentals / crossfires — https://boosteria.org/guides/cs2-positioning-fundamentals-2026-angles-crossfires
- Boosteria — hold sites as CT / rotations — https://boosteria.org/guides/hold-sites-ct-cs2-setups-rotations-2026
- Boosteria — site anchoring — https://boosteria.org/guides/cs2-site-anchoring-guide-hold-fall-back-rotate-correctly
- Boostroom — stop dying first (positioning/timing) — https://boostroom.com/blog/how-to-stop-dying-first-in-cs2-positioning-timing-guide
- EGW — art of demo review (how teams study opponents) — https://egw.news/gaming/news/26282/the-art-of-demo-review-how-cs2-teams-study-their-o-LgEbAX8dv
- CSGO-Guides — strategy (defaults/executes/fakes/splits) — https://csgo-guides.com/gameplay/strategy
- CSGO-Guides — IGL — https://csgo-guides.com/roles/igl
- CS2Hype — dynamic map rotations — https://cs2hype.com/guides/dynamic-map-rotations-when-and-how-to-rotate-in-cs2
- BLAST.tv — lurker guide — https://blast.tv/article/cs2-lurker-guide
- Switchblade Gaming — maps guide / mid control — https://www.switchbladegaming.com/cs2/maps-guide/
- cs2guide.net — anti-strat prep (403, snippet only) — https://cs2guide.net/competitive-play/team-anti-strat-preparation/
- whatisesports — veto strategies — https://whatisesports.xyz/map-veto-strategies/
- bo3.gg — best maps to pick/ban — https://bo3.gg/articles/best-maps-to-pick-and-ban-in-premier-mode
