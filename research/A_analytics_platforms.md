# Research A — Pro CS2 Analytics Platforms → Upgrades for CS2DemoPlayer

**Agent:** Research Agent A (overnight run)
**Date:** 2026-06-15
**Scope:** Public-facing features/workflows of Skybox EDGE, Leetify, SCOPE.GG, CS2.CAM, Noesis, Refrag (+ incidental: smartCoach, PRACC). Every feature is translated into a concrete, scoped upgrade for **CS2DemoPlayer** (local Flask + demoparser2 backend; vanilla-JS + Three.js frontend at `C:\Users\USER\CS2DemoPlayer`).

> **Context reminder — what this app ALREADY has** (so we don't recommend rebuilding it): .dem upload + .zip demo library w/ final scores; 2D radar replay (scrub, round nav, click-to-spectate, scoreboard w/ money/weapon/KDA, killfeed, grenade arcs, smoke/molly volumes, bullet traces, utility heatmaps, draw/telestrator); real-geometry 3D Three.js replay (player models, aim laser, POV cone, bullet impacts); analytics/coaching layer (HLTV-2.0-equivalent rating, KAST, ADR, opening duels, trades, clutches, multikills, utility stats, heuristic role detection, per-kill win-probability swing, per-round impact, "what went wrong/right" insight feed w/ replay deep-links, focus areas + drills, team coaching w/ loss taxonomy + practice plans, multi-demo trends).

---

## ★ TOP BUILD-NOW SHORTLIST FOR THIS APP (ranked)

These are the highest-leverage gaps where competitors are clearly ahead and the app already has the substrate (parser data, 2D/3D replay, win-prob model) to implement cheaply.

| # | Upgrade | Why it's #-ranked | Effort | Borrowed from |
|---|---|---|---|---|
| **1** | **Aim / Utility / Positioning sub-ratings + percentile benchmarks** (color-banded: Poor/Subpar/Average/Good/Great), rank-aware where appropriate | The single biggest "pro feel" gap. App has one composite rating; pros expect 3 axis-ratings + "where do I rank?" context. All raw stats already parsed. | **M** | Leetify |
| **2** | **Deep aim-mechanics stats**: crosshair placement (deg), time-to-damage, counter-strafe %, spray accuracy, spotted-accuracy, reaction time | These are the stats "you can't see in-game" that make a tool feel pro. demoparser2 exposes view angles + velocity + shots → all computable. Feeds #1. | **M–L** | Leetify, SCOPE.GG |
| **3** | **Bookmarks + Notes + Tags + Playlists** on rounds/moments, with shared/exportable lists; "queue of rounds to review" | Core review-workflow primitive every team tool has and this app lacks. Turns a player from passive scrubber into an organized reviewer/coach. Cheap given existing replay deep-links. | **S–M** | Skybox, CS2.CAM, PRACC, Noesis |
| **4** | **Multi-round overlay / aggregation mode** (stack N rounds — even across demos — into one 2D heat/position view; filter to buy rounds, T-side, specific player) | This is Noesis/CS2.CAM's killer feature: see *tendencies*, not single rounds. App has per-round heatmaps; aggregation across a filtered set is the leap to pattern-finding. | **M** | Noesis, CS2.CAM, SCOPE.GG |
| **5** | **Powerful round filters + saved "Routines"** (filter by player/weapon/event/outcome/economy/side/timing; save filter combos and re-run across demos) | Filters are the connective tissue that make #4, #6, #7 usable at scale. "Routines" = save once, re-run on every new demo. | **M** | CS2.CAM, Skybox, Noesis |
| **6** | **Strat / Playbook auto-detection** (cluster T executes & util by site/timing → name & catalog "our strats" and "their strats"; surface predictability) | The flagship anti-strat capability of Skybox Tier-1 & CS2.CAM Pro. Heuristic clustering on opening-positions + util-landing + timing is feasible locally and is huge for a *team* tool. | **L–XL** | Skybox (Tactic-spotter/Playbook), CS2.CAM (Playbook) |
| **7** | **Timing heatmap** (per strat/site: histogram of round-second when the team commits; bright = go-to timing) | High insight-per-effort: it's a 1-D histogram of "time of first contact/util/site-hit" the app can already derive. Direct feeder for #6 and anti-strat prep. | **S–M** | CS2.CAM |
| **8** | **Per-moment AI/heuristic coaching with "the habit to build"** — upgrade the insight feed so each flagged death/round states *what happened → win-prob swing → the one habit to fix*, in plain language | App already has insight feed + win-prob; reframing each item as a coachable habit (not just a stat) is the smartCoach/Refrag-Coach differentiator and is mostly a templating/UX change over existing data. | **S–M** | smartCoach, Refrag Coach, Leetify |
| **9** | **Voice-comm sync** (load a team VOIP/round audio track aligned to demo tick timeline; scrub plays comms) | Team-review staple (Skybox Tier-2, CS2.CAM team). For a *local small-team* tool this is a genuine pro feature competitors paywall heavily. Audio-file align to tick timeline. | **M** | Skybox, CS2.CAM |
| **10** | **Post-match reflection journal w/ win-rate correlation** (custom yes/no prompts — sleep, tilt, warmed up — correlated against W/L over time) | Cheap, sticky, unique among local tools; turns the app into a longitudinal self-coaching log. Pure metadata + correlation math on top of existing match library. | **S** | Leetify (Post-Match Journal) |

---

# FULL FINDINGS BY PLATFORM

Schema per feature: **Source · Observed · Why it helps · Fit for THIS app · Difficulty (S/M/L/XL) · Priority (now/next/later) · Confirmed/Inferred**

---

## 1. SKYBOX EDGE  (skybox.gg/edge)

> Positioning: the de-facto **pro/tier-1 team** anti-strat & prep suite ("85%+ of pro teams use EDGE" per their marketing — *Inferred/marketing claim*). Two paid tiers: **Tier 2 €350/mo** (replayer, team stats, voice comm, leaderboards, bookmarks/notes/playlists, public demo access, role-detection AI) and **Tier 1 €1,299/mo** (adds Tactic-spotter AI/Playbook, Tactic filters, Match Reports). Heavily team/coach-oriented and expensive — i.e. exactly the value a free *local* tool can democratize.

### 1.1 — 2D Replayer (advanced)
- **Source:** https://skybox.gg/edge/ , https://skybox.gg/pricing/
- **Observed:** Smooth top-down 2D demo replayer; explicitly renders **smoke *breaking/expansion* mechanics** (popup → bloom → fade), not just a static smoke disc.
- **Why it helps:** Accurate smoke timing/coverage is central to reading executes and retakes; a static circle misleads on when a smoke is actually blocking vision.
- **Fit for THIS app:** App already draws smoke/molly *volumes*. Upgrade: animate smoke through its real lifecycle (deploy delay → expand radius over ~the bloom window → start-to-fade → gone) keyed to grenade detonation tick, and show a subtle "fading" alpha in the last seconds. Same for molly burn footprint over its duration.
- **Difficulty:** S–M · **Priority:** next · **Confirmed**

### 1.2 — Team Statistics
- **Source:** skybox.gg/edge, /pricing
- **Observed:** Squad-level performance metrics (team-wide, not just per-player), gated by tier ("Pro Level Stats & Filters" at top tier).
- **Why it helps:** Teams need roster-level views (who trades for whom, side win-rates, economy conversion) to allocate roles and fix systemic issues, not just individual scorecards.
- **Fit for THIS app:** App has team coaching + loss taxonomy. Add a **Team Dashboard** aggregating the existing per-player metrics across the demo/library: team T/CT round-win %, eco/force/full conversion %, opening-duel win% as a team, trade-conversion matrix (who-trades-whom heatmap), site-hit distribution. Most numbers already exist per-round; this is an aggregation + presentation layer.
- **Difficulty:** M · **Priority:** now · **Confirmed**

### 1.3 — Voice Comm Integration
- **Source:** skybox.gg/edge, /pricing; search corroboration (Tier-2 feature; works with audio files)
- **Observed:** Load the team's in-game **voice comms aligned to the demo timeline**; scrub the replay and hear what was called. (CS2.CAM has the same; see 4.x.)
- **Why it helps:** Most CS losses are *communication/decision* failures invisible in stats. Hearing the call vs. seeing the execution is the core of real VOD review.
- **Fit for THIS app:** Add an audio track lane. UI: upload a `.wav/.mp3/.ogg` (per-player or single mixed track) + an offset/sync handle (anchor audio t=0 to a known tick, e.g. round-1 freeze-end). Backend stores file + offset; frontend plays/seeks the audio in lockstep with the replay clock (already have a master scrub clock). Waveform strip under the timeline is a nice-to-have.
- **Difficulty:** M · **Priority:** next · **Confirmed**

### 1.4 — Leaderboards
- **Source:** skybox.gg/edge, /pricing
- **Observed:** Rankings/position tables to compare players & teams; track performance over time. Tiered (Core/Advanced/Pro).
- **Why it helps:** Internal competition + visibility into who's improving; surfaces the weakest link to coach.
- **Fit for THIS app:** For a *small team* library: a **roster leaderboard** sortable by any tracked metric (rating, ADR, KAST, opening-win%, util quality, trade%) over a selectable window (last N demos / date range). Pure read over existing trend data.
- **Difficulty:** S–M · **Priority:** next · **Confirmed**

### 1.5 — Bookmark / Notes / Playlists  ★ (shortlist #3)
- **Source:** skybox.gg/edge, /pricing
- **Observed:** Bookmark moments, attach notes, and build playlists of clips/rounds for organized review.
- **Why it helps:** Converts ad-hoc scrubbing into a structured review session; lets a coach assemble "watch these 8 deaths" for a player; persists insight between sessions.
- **Fit for THIS app:** Add a `bookmarks` store (SQLite/JSON): `{demo_id, round, tick, player?, text, tags[]}`. From any replay frame, "Bookmark this moment" (auto-captures demo/round/tick + current camera). A **Notes panel** per round/demo. **Playlists** = ordered list of bookmark refs that auto-play one moment after another via existing deep-link jump. Export/import playlist as JSON so a coach can hand it to a player. This is the connective tissue for the whole coaching workflow.
- **Difficulty:** S–M · **Priority:** now · **Confirmed**

### 1.6 — Public Demo Access (pro match library)
- **Source:** skybox.gg/edge, /pricing
- **Observed:** Browse/analyze tournament & scrim demos hosted on the platform; instant on paid tiers vs. 1-month delay free.
- **Why it helps:** Study how pros play a map/strat; build anti-strats against upcoming opponents.
- **Fit for THIS app (scoped):** Don't host a library, but add a **"fetch by share-code / URL" importer** so users can pull a public GOTV/HLTV demo into the same analysis pipeline. Even simpler: a "paste a local folder of pro demos" batch-import so the trends/playbook tooling works over a studied-pro corpus. Mark anything requiring third-party hosting as out-of-scope.
- **Difficulty:** M (importer) · **Priority:** later · **Confirmed** (feature) / **Inferred** (our scoping)

### 1.7 — Role Detection AI
- **Source:** skybox.gg/edge, /pricing; cs2.cam comparison
- **Observed:** Automatic identification of players' **best roles**, "matching pro team methodologies."
- **Why it helps:** Validates/optimizes role assignment; spots a player mis-cast (e.g. an entry playing too passive).
- **Fit for THIS app:** App **already** has heuristic role detection (entry/lurker/support/AWP/anchor/rotator/IGL). Upgrades: (a) show *confidence* + the evidence behind each label (opening-duel rate, time-of-first-contact, util thrown, AWP buys, map-area occupancy) so it's explainable; (b) "**recommended role**" = compare a player's stat profile against the role they're currently playing and flag mismatch ("you're rostered support but your opening-duel rate is top-of-team → trial as entry"). Leverages existing per-player stats.
- **Difficulty:** S (explainability) / M (recommendation) · **Priority:** next · **Confirmed**

### 1.8 — Tactic-spotter AI / Playbook  ★ (shortlist #6)
- **Source:** skybox.gg/edge, /pricing; cs2.cam comparison (calls its Anti-Strat "the equivalent of Skybox Tier-1's Tactic-spotter + Veto Sim")
- **Observed:** **Auto-detect, study, and combine every strategy a team runs — and the strats opponents have used against them.** Tier-1 flagship.
- **Why it helps:** Anti-stratting manually across many demos is infeasible (their own testimonial says so). Auto-clustering exposes opponent tendencies + your own predictability.
- **Fit for THIS app:** Biggest-ticket upgrade. Heuristic pipeline (no ML needed to start): for each T round, build a feature vector {site hit, first-contact time, util landing zones (smokes/flashes/mollies bucketed to named areas), # players per split, weapons}; cluster (k-means/DBSCAN over the corpus or even rule-buckets) into named "strats" ("A split w/ CT smoke @ 0:20"). Output a **Playbook view**: list of detected strats per side/map with frequency, success%, and example-round deep-links. Run it on *opponent* demos for anti-strat. Start rule-based per-site, evolve to clustering.
- **Difficulty:** L–XL · **Priority:** next (start the timing/clustering substrate now) · **Confirmed**

### 1.9 — Tactic Filters  ★ (part of shortlist #5)
- **Source:** /pricing (Tier-1); /edge
- **Observed:** Filter demos/rounds by tactical approach (and economy — see Buy-Type below) for targeted review.
- **Why it helps:** Lets you isolate "show me only their A-executes on full-buy" — surgical review.
- **Fit for THIS app:** Build a unified **round-filter bar**: side, economy (eco/force/full/anti-eco), site hit, win/loss, contains-clutch, contains-opening-kill, specific player alive, specific weapon used, round-time bucket. Filters drive replay list, aggregation overlay, and stats. (See CS2.CAM Filters 4.10 + Routines 4.11 — same idea.)
- **Difficulty:** M · **Priority:** now · **Confirmed**

### 1.10 — Match Reports
- **Source:** /pricing (Tier-1); /edge
- **Observed:** Post-game analysis summaries breaking down team & individual performance per match.
- **Why it helps:** A scannable per-match digest a coach can read in 2 min before deciding what to deep-dive.
- **Fit for THIS app:** App has rich analytics but (likely) no single **printable/exportable Match Report**. Add a one-page report: scoreline, per-player rating/ADR/KAST/opening/util, top 3 "what went wrong" + top 3 "what went right" (from existing insight feed), economy summary, side splits, MVP/anchor. Export to PDF/HTML for sharing. Mostly assembly of data the app already computes. (See Leetify Match Report 2.3 for the consumer-grade version.)
- **Difficulty:** M · **Priority:** now · **Confirmed**

### 1.11 — Pattern Finder
- **Source:** /edge (named feature); search (top-tier only)
- **Observed:** "Identifies recurring tactical behaviors and strategies." (Closely related to Playbook 1.8.)
- **Why it helps:** Surfaces *predictability* — e.g. "after a 2nd-round win you always force-A."
- **Fit for THIS app:** A lightweight sibling of the Playbook: scan the corpus for **conditional patterns** ("on pistol-loss → 90% force next round", "player X lurks B 70% of T-rounds on Mirage", "first util always thrown to same spot"). Present as a "Tendencies / Predictability" list with confidence. Reuses the filter + aggregation engine.
- **Difficulty:** L · **Priority:** later · **Confirmed**

### 1.12 — Buy-Type Filters  ★ (folded into #5)
- **Source:** /edge (named feature)
- **Observed:** Sort/segment review by economy state (eco / force / full).
- **Why it helps:** Performance and tactics differ wildly by buy; analyzing them together is noise. Eco-frag value, force-buy success, anti-eco discipline are distinct skills.
- **Fit for THIS app:** App scoreboard already tracks money. Classify each team's round buy (eco/semi/force/full/anti-eco) from start-of-round equipment value, expose as a filter dimension *and* as stat splits ("your force-buy win% = 18%, eco-frag value = high"). Cheap and high-value.
- **Difficulty:** S–M · **Priority:** now · **Confirmed**

### 1.13 — Match Prep
- **Source:** /edge, /pricing
- **Observed:** Coach-oriented workspace to organize tactical info and review "officials and scrims" efficiently before a match.
- **Why it helps:** Consolidates anti-strat notes, opponent playbook, veto plan, and "things to do" into one prep doc per upcoming match.
- **Fit for THIS app:** A **Match Prep board**: pick an opponent (their demos in the library) → auto-pull their Playbook (1.8), Timing heatmaps (4.8), favored positions (SCOPE prematch 3.5), and let the coach pin bookmarks/notes + a veto plan into a single shareable page. Aggregator over other features — build last.
- **Difficulty:** L · **Priority:** later · **Confirmed**

### 1.14 — Veto Simulation
- **Source:** /edge (named); cs2.cam comparison (CS2.CAM has BO1/3/5 sim + live decision tree)
- **Observed:** Simulate the map-veto between two teams across BO1/BO3/BO5; predict likely picks/bans (CS2.CAM: with a live decision tree).
- **Why it helps:** Walk into the veto with a plan; understand which maps you're likely funneled into and prep those.
- **Fit for THIS app:** Needs per-team map win-rate + pick/ban history (from the library + manual entry). Build a **veto simulator**: input both teams' map prefs/winrates → step through ban/pick decision tree showing probable final map(s), then jump to prep for those maps. Lower priority for a small-team review tool unless they scrim a fixed pool.
- **Difficulty:** M–L · **Priority:** later · **Confirmed** (feature) / **Inferred** (data plumbing)

---

## 2. LEETIFY  (leetify.com)

> Positioning: the **consumer-grade rating + benchmark + auto-report** leader. Its public blog is unusually transparent about methodology — gold for matching/upgrading this app's rating layer. Note their **philosophy is explicitly win-probability/impact-based**, which *already aligns* with this app's per-kill win-prob swing — so the app is well placed to adopt Leetify-style sub-ratings.

### 2.1 — Leetify Rating (impact / win-probability, zero-sum)
- **Source:** https://leetify.com/blog/leetify-rating-explained/ , https://leetify.com/blog/introducing-leetify-rating/ , https://leetify.com/blog/leetify-rating-update-2026-02-25/
- **Observed:** Rating = **change in win probability** attributable to a player, NOT raw frags. Win-prob computed at round start from **economy tier (4 buckets, CT/T variants) + player counts**, recomputed after each kill, **using a model trained on pro HLTV match data**. Reward pool per kill is **distributed**: ~**35% killer / 30% all damage-dealers / 15% flash-assist / 20% the player whose death gets traded** (missing slices scale up). Sum of all players' ratings in a game = **0 (zero-sum)**. Clutch weighting was *reduced* in a Feb-2026 update (was over-rewarding 1v1 clutches). Example: two identical 5-kill rounds rated 8.94 vs 93.25 depending on situation.
- **Why it helps:** Captures contribution invisible to K/D — getting traded, dealing the damage someone else finishes, flashing for a kill, saving for next round. Far more honest "who actually won us the round."
- **Fit for THIS app:** App **already** computes per-kill win-prob swing + an HLTV-2.0-equivalent rating. The upgrade is to add a **second, Leetify-style "Impact Rating"** that (a) distributes each kill's win-prob delta across killer/damage/flash/trade per the public split, (b) is zero-sum-checkable per game (great for validating the model), and (c) explicitly credits "being traded" and "equipment saved." Present both ratings side-by-side (traditional vs impact) so users see the gap. The economy×playercount win-prob table can be calibrated from the user's own demo corpus if a pro table isn't available. **Do NOT copy any proprietary coefficients** — re-derive from the public *structure* only.
- **Difficulty:** M · **Priority:** now · **Confirmed**

### 2.2 — Aim / Utility / Positioning Sub-Ratings + Benchmarks  ★★ (shortlist #1)
- **Source:** https://leetify.com/blog/cs2-benchmarks/
- **Observed:** Three skill ratings — **Aim, Utility, Positioning** — each centered so **50 = playerbase average**. Presented with a **5-tier percentile color band**: **Poor (bottom 10%) / Subpar (10–30%) / Average (30–70%) / Good (70–90%) / Great (top 10%)**. Distinguishes **rank-dependent** stats (Time-to-Damage, spotted accuracy — benchmark shifts by rank) from **rank-independent** stats (the 3 ratings, ADR, K/D — same band at all ranks).
- **Why it helps:** A single composite rating hides *where* you're weak. Splitting into 3 axes + "what percentile am I" turns raw numbers into an actionable verdict ("your aim is Good but positioning is Poor → that's your bottleneck"). This is the #1 thing that makes a tool feel pro.
- **Fit for THIS app:** Compute three composite sub-ratings from stats the app already has/can derive (Aim ← crosshair placement, TTD, spotted-acc, spray, HS%, counter-strafe; Utility ← the util quality+quantity below; Positioning ← opening-duel context, traded%, time-alive, multi-vs-outnumbered, "caught off-angle" deaths). Render as **three color-banded gauges + a radar/spider chart**. For benchmarks: ship default bands derived from the user's own corpus to start, with a note they can be recalibrated; let *rank* tag a demo so rank-dependent stats can be banded fairly. This is the keystone — everything else (focus areas, drills) hangs off knowing which axis is weakest.
- **Difficulty:** M · **Priority:** now · **Confirmed**

### 2.3 — Automatic Match Report + Highlights
- **Source:** leetify.com; search ("celebrate best plays, compare with friends, see who carried"); https://leetify.com/blog/post-match-journal/ (references the Match Report)
- **Observed:** Auto-generated per-match report with the three ratings, "who carried," shareable best-play **highlights**, and friend comparison.
- **Why it helps:** Zero-effort digest after every game; the highlight reel makes review *fun* (retention).
- **Fit for THIS app:** Pairs with Skybox Match Report (1.10). Add **auto-highlight detection**: rank a match's moments by win-prob swing / multikill / clutch and surface the top 3–5 as one-click replays ("Your match highlights"). "Who carried" = sort the team by impact rating. The app's 3D replay makes a genuinely cool local highlight viewer.
- **Difficulty:** M · **Priority:** next · **Confirmed**

### 2.4 — Stats Glossary: deep mechanics stats  ★★ (shortlist #2)
- **Source:** https://leetify.com/blog/leetify-stats-glossary/
- **Observed (each with its public definition):**
  - **Accuracy (Enemy Spotted):** hits/shots *while an enemy was spotted* (filters out spray-into-smoke noise).
  - **Headshot Accuracy:** head-hits / all enemy-hits (excludes AWP).
  - **Proper Counter-Strafing:** % of rifle shots fired while velocity **< 34% of max** (the speed below which the first bullet is accurate).
  - **Spray Accuracy:** hits / shots during sprays of **3+ shots** at spotted enemies.
  - **Crosshair Placement:** angular distance (degrees) the crosshair must travel from enemy first-appearing to first hit — lower = better pre-aim.
  - **Time to Damage (TTD):** median time from spotting enemy to first damage (excludes plays >1s = deliberate hold), rank-dependent.
  - **Flashbangs leading to kills / Enemies flashed per flash / Teammates flashed per flash / Flash blind duration:** flash quality metrics (only counts blinds **>1.1s**, ignores half-blinds).
  - **Damage to enemies per HE; [CT] Smokes that stopped a push** (smoke thrown when enemy within an 800u radius); **Unused utility on death** ($ value of nades you died holding).
  - **Trade Kills / Traded Deaths** each broken into **Opportunities / Attempts / Successes** (not just a count — measures whether the *chance* to trade existed and was taken).
- **Why it helps:** These are the "stats you can't see in-game" (SCOPE's exact pitch too). They diagnose *mechanical* root causes: bad counter-strafe %, high TTD, sloppy crosshair placement, util dying in your pockets. Trade Opportunities/Attempts/Successes is a far better team-trading diagnostic than a raw trade count.
- **Fit for THIS app:** demoparser2 exposes per-tick **view angles, position, velocity, weapon-fire, hits/damage, flash duration** — enough to compute *all* of these. Concretely:
  - Counter-strafe % ← velocity at shot tick vs weapon max-speed threshold.
  - Crosshair placement / TTD ← diff between view-angle at first-visible tick and at first-hit tick + the time delta.
  - Spotted accuracy / spray accuracy ← gate shot/hit events on "enemy visible" + spray-length grouping.
  - Flash quality ← flash events + blind durations already needed for KAST/util.
  - **Trade Opportunities/Attempts/Successes** ← within the existing trade window logic, also count when a teammate was *positioned/alive to trade* (opportunity) and whether they fired (attempt). Upgrade the existing trade stat to this 3-part form.
  - Unused-utility-on-death ← sum nade $ in inventory at death tick.
  These feed the Aim/Utility sub-ratings (2.2). Build the parser-side extractors first; they unlock 2.2, 2.5, and the focus-areas engine.
- **Difficulty:** M–L · **Priority:** now · **Confirmed**

### 2.5 — Utility Rating: Quantity × Quality  ★
- **Source:** https://leetify.com/blog/utility-ratings/
- **Observed:** Util rating = **geometric mean of a Quantity Rating and a Quality Rating** (geo-mean favors *both* being high, punishes lopsided). **Quantity:** nades/round (ex-decoy) vs an expected **~3 nades/round** benchmark, with an **x^(2/3)** falloff, capped at 100. **Quality:** z-score blend of six metrics — flash-assist %, enemies-flashed/flash, friends-flashed/flash (penalty), avg blind time, HE dmg/nade, team-dmg/HE (penalty) — green pulls toward 100, red toward 0. Post-update, pros average **70+** (vs old 40–60), deliberately **rewarding high-volume-AND-effective** util over "minimal but perfect."
- **Why it helps:** Distinguishes a lurker who throws 1 perfect flash from a support spamming 5 nades a round — *and* says which is more valuable (volume that consistently interferes with enemies). Stops players gaming "quality" by barely throwing util.
- **Fit for THIS app:** App tracks util stats already. Restructure into the **Quantity × Quality (geo-mean)** form with the public metric list; show the two sub-scores + the per-metric green/red breakdown so a player sees *exactly* which util habit (e.g. flashing teammates) is dragging them down. Feeds the Utility axis of 2.2.
- **Difficulty:** M · **Priority:** now · **Confirmed**

### 2.6 — Roles (definitions + detection)
- **Source:** https://leetify.com/blog/understanding-roles-in-csgo/
- **Observed:** 5 core roles — **Support, AWPer, Lurker, Entry Fragger, IGL** (+ rifler). Each defined behaviorally: Entry = first into site, high opening-duel volume, "great aim + quick reaction, high risk"; Lurker = off-site stealth picks on isolated players, watches the *other* bomb site; Support = throws the execute util, learns lineups; AWPer = holds long angles, economy built around them; IGL = makes econ/rotate/push calls. "Roles are not rules" — fluid.
- **Why it helps:** Shared role vocabulary + behavioral signatures = the basis for detection and for spotting mis-cast players.
- **Fit for THIS app:** Confirms/extends the app's existing role heuristics. Use these behavioral signatures to (a) sharpen detection thresholds, (b) drive the "recommended role" mismatch check (1.7), (c) tailor drills per role ("as entry, your TTD must be elite"). No new data needed.
- **Difficulty:** S · **Priority:** next · **Confirmed**

### 2.7 — Post-Match Journal (win-rate correlation)  ★ (shortlist #10)
- **Source:** https://leetify.com/blog/post-match-journal/
- **Observed:** After each match, answer **custom yes/no prompts** about **out-of-game factors not on the Match Report** (sleep, caffeine, tilt, warmed-up, "had a toxic teammate"). System computes **statistical correlation w/ confidence interval** to win-rate ("toxic teammate converged to −46%"). Needs **dozens–hundreds** of entries for strong signal.
- **Why it helps:** Surfaces personal performance levers that no demo stat can — the meta-game of consistency. Sticky daily-log habit.
- **Fit for THIS app:** Trivial to add over the existing match library: a per-match **yes/no checklist** (user-editable prompts) stored in metadata; a **correlation view** (point-biserial / simple win-rate-given-yes vs -no with a CI) once enough matches logged. Pure metadata + stats, no parsing. Cheap, unique among *local* tools, strong retention.
- **Difficulty:** S · **Priority:** next · **Confirmed**

### 2.8 — Pro Hub (pro settings / crosshairs / benchmarks reference)
- **Source:** https://leetify.com/pro-hub/event/iem-cologne-major-2026 (search-surfaced)
- **Observed:** Per-event/pro pages with pro **stats, crosshairs, and config/settings**, plus crosshair share-codes.
- **Why it helps:** Players copy pro configs/crosshairs and benchmark themselves against pro stat lines.
- **Fit for THIS app (scoped):** Out of core scope (it's a content/DB feature), but a small win: if the app studies pro demos (1.6), auto-extract and display each pro's **crosshair/settings from the demo's convars** and their stat line as a **benchmark target** next to the user's. Low priority.
- **Difficulty:** M · **Priority:** later · **Confirmed** (feature) / **Inferred** (scoping)

---

## 3. SCOPE.GG  (scope.gg)

> Positioning: **solo-improvement** focus — "we point out your mistakes," progress over time, and a standout **prematch enemy-read** product. Strong on *longitudinal* tracking and *mistake* framing.

### 3.1 — Automatic Mistake Detection
- **Source:** https://scope.gg/ ("We'll analyze your matches and point out the mistakes")
- **Observed:** Auto-flags gameplay errors + actionable feedback.
- **Why it helps:** Players can't see their own mistakes; an explicit "here's what you did wrong" is the core coaching value.
- **Fit for THIS app:** App **already** has a "what you did wrong" insight feed — this validates the approach. Upgrade: ensure each flagged mistake names a **category** (over-peek, no-trade-setup, util wasted, bad rotate timing, eco over-aggression, off-angle death) so mistakes aggregate into trends (3.3) and map to drills.
- **Difficulty:** S (categorization layer) · **Priority:** next · **Confirmed**

### 3.2 — Map Performance / Position Practice Tips
- **Source:** scope.gg ("which map positions you should practice more"; K/D by T/CT side, avg kills per map)
- **Observed:** Per-map, per-side performance breakdown with "practice this position more" guidance.
- **Why it helps:** Routes practice to the maps/sides/positions where you actually lose value — efficient improvement.
- **Fit for THIS app:** Add a **per-map / per-side stat split** + a **death-location heatmap per map** with a "you repeatedly die here" callout (cluster your death positions; flag hot zones). Feeds focus-areas. App already has heatmaps — segment them by map/side and add the "lose value here" annotation.
- **Difficulty:** M · **Priority:** next · **Confirmed**

### 3.3 — Progress / Skill Tracking Over Time
- **Source:** scope.gg ("evaluate your skill and track how it changes"; **30-match trend, prev 15 vs current 15**)
- **Observed:** Trends each tracked stat over time and explicitly compares **rolling windows** (last 15 vs previous 15 matches) to show improving/declining.
- **Why it helps:** Improvement is invisible match-to-match; the rolling-window delta makes it concrete and motivating, and flags regressions early.
- **Fit for THIS app:** App has multi-demo trends. Add the **rolling-window delta view** (last N vs previous N) per stat/sub-rating with up/down arrows + sparkline, and "biggest improvement / biggest regression this window." Cheap over existing trend data; big motivational payoff.
- **Difficulty:** S–M · **Priority:** now · **Confirmed**

### 3.4 — Auto Clip Recording (highlights)
- **Source:** scope.gg ("record video clips of your best moments"; multi-kills/highlights)
- **Observed:** Auto-captures multikill/highlight moments as video clips.
- **Why it helps:** Easy sharing + review of peak plays; motivation.
- **Fit for THIS app:** Server-side video render is heavy, but the app's **3D replay can act as the clip player** — auto-build a "highlights" playlist (top win-prob swings / multikills / clutches) that plays in the 3D viewer (ties to 2.3 + bookmarks 1.5). For actual file export, a "record current 3D replay segment to webm via canvas capture" is a stretch-goal.
- **Difficulty:** M (playlist) / L (file export) · **Priority:** next (playlist) · **Confirmed**

### 3.5 — Prematch / Enemy Reads  ★
- **Source:** https://scope.gg/guides/prematch_scopegg_en/
- **Observed:** Before a match (5-min warmup), paste the lobby link → analyzes **opponents' last 5 matches** and surfaces: **likely CT positions** (color heatmap, warm = high probability), **weapons they'll use in those positions** by buy-type (full/eco/force), **aggression/push patterns & timings**, all **ranked per player by role (Sniper/Rifler)**.
- **Why it helps:** Walk in knowing where each defender likely sits, what they'll hold with, and when they push — convert that into util counters and entry plans. Pure information edge.
- **Fit for THIS app:** Mostly already feasible from the library: pick an opponent → aggregate their recent demos into **favored-position heatmaps per side/map**, **weapon-by-position**, **buy-type tendencies**, and **timing** (4.8). This is the core of **Match Prep (1.13)** and overlaps heavily with the **Playbook (1.8)** — build the opponent-aggregation engine once, surface it as both "enemy reads" and "their playbook." No live lobby link needed for a review tool; operate over imported opponent demos.
- **Difficulty:** M–L · **Priority:** next · **Confirmed**

### 3.6 — "Stats you can't find in-game" + Rank Assessment
- **Source:** scope.gg (ADR, HLTV rating, KAST, KPR, HS%, **first-bullet accuracy**, util usage; "does your stat line match your rank?")
- **Observed:** Surfaces advanced stats + tells you whether your performance is above/below your rank.
- **Why it helps:** Context — "am I overperforming/underperforming my rank?" guides whether to grind aim vs game-sense.
- **Fit for THIS app:** App has most of these. Add **first-bullet accuracy** (subset of 2.4) and, if the demo carries rank, a **"vs your rank" verdict** per stat (ties to rank-dependent benchmarks in 2.2).
- **Difficulty:** S · **Priority:** next · **Confirmed**

---

## 4. CS2.CAM  (cs2.cam)

> Positioning: the most **feature-dense team anti-strat** tool *and* the closest architectural sibling to this app (browser 2D viewer + 3D replay + private demos + desktop recorder). Its public comparison/wiki pages are the richest single feature list in this research. Team plans: **Essentials $79.99/mo**, **Professional $120/mo**.

### 4.1 — 2D Viewer (controls, indicators, events)
- **Source:** https://cs2.cam/en/wiki/introduction
- **Observed:** Top-down viewer w/ full playback controls, player indicators, real-time event viz, **paint/drawing tools**, and **notes**.
- **Why it helps:** The baseline review surface.
- **Fit for THIS app:** App already has this (incl. telestrator). Parity check: ensure playback supports variable speed (incl. 2x for kill-feed skimming — a known fast-review trick) and frame-step.
- **Difficulty:** S · **Priority:** later (parity) · **Confirmed**

### 4.2 — Private Demos + Team Collaboration
- **Source:** wiki; cs2.cam compare
- **Observed:** Upload GOTV/POV from MM/FACEIT/scrims for private analysis; **voice comms synchronized to the demo**; shared/tagged notes; team-visible playlists ("whole team works in one place").
- **Why it helps:** Privacy for scrims + a shared team workspace = real coaching loop.
- **Fit for THIS app:** Already local-private by nature (strength!). The gap is the *collaboration* primitives: shared notes/tags/playlists (1.5) + voice sync (1.3). For a single local install, "shared" = exportable/importable JSON the team passes around, or pointing the app at a shared folder.
- **Difficulty:** M · **Priority:** now (notes/tags) · **Confirmed**

### 4.3 — 3D Replay
- **Source:** wiki; compare ("full 3D replay of any round")
- **Observed:** Convert any round from 2D into a full 3D spatial replay.
- **Why it helps:** Spatial perspective for understanding angles/peeks/clears.
- **Fit for THIS app:** App **already** has real-geometry 3D replay — a genuine competitive strength (CS2.CAM gates this behind paid tiers). Lean into it: make 3D the home of highlights (3.4) and **death analysis (4.6)**.
- **Difficulty:** — (have it) · **Priority:** — · **Confirmed**

### 4.4 — Heatmaps (movement / death / utility, AWP-zoom detection)
- **Source:** wiki; search corroboration
- **Observed:** Movement, **death**, and utility heatmaps; **AWP-zoom detection**; frame-by-frame; custom colors. **Compare multiple rounds/matches** in one heatmap.
- **Why it helps:** Aggregated spatial tendencies; AWP-zoom detection isolates *where players actually hold/scope* vs just pass through.
- **Fit for THIS app:** App has utility heatmaps; add **death heatmaps** (3.2) and **AWP-zoom-position heatmap** (flag ticks where a player is scoped + stationary → "AWP hold spots"). Custom color ramps + the multi-round compare overlay (#4) round it out.
- **Difficulty:** M · **Priority:** next · **Confirmed**

### 4.5 — Grenade Mode (lineups, trajectories, impact)
- **Source:** wiki; compare (also "**grenade pattern search**")
- **Observed:** Dedicated util mode: trajectory viz, **lineups**, impact analysis, and **searching for specific grenade patterns** across demos.
- **Why it helps:** Study/steal util setups; find every instance of a given lineup to learn or counter it.
- **Fit for THIS app:** App draws grenade arcs already. Add a **grenade-only mode** (hide players, show all util of a filtered set with landing markers) + **"find this nade" search** (cluster grenades by thrower-position + landing-zone so you can pull "all smokes that landed on A-cross"). Pairs with Playbook/timing.
- **Difficulty:** M · **Priority:** next · **Confirmed**

### 4.6 — Death Analysis (cinematic, auto-cut)
- **Source:** wiki; compare ("cinematic frame-by-frame review of every death with auto-cut clips for VOD review")
- **Observed:** Auto-cuts a clip around **every death**, presented frame-by-frame/cinematically for VOD review.
- **Why it helps:** Deaths are where rounds are lost; a one-click "watch all my deaths, framed" loop is the fastest path to fixing positioning/peeking errors.
- **Fit for THIS app:** Strong fit given the 3D replay. Build a **Death Review mode**: list every death (filterable to a player) → click → 3D replay auto-seeks to ~3s pre-death, slows near the kill, shows the killer's angle + the victim's POV cone + crosshair-placement at death. Auto-build "all my deaths" as a playlist. High coaching ROI, reuses 3D + bookmarks + deep-links.
- **Difficulty:** M · **Priority:** now · **Confirmed**

### 4.7 — Anti-Strat Module / Playbook / Routines
- **Source:** wiki; compare
- **Observed:** Anti-Strat bundle = **Playbook** (auto-detected strats), **Routines** (saved filter combos re-run across demos), **Timing Heatmap**, **Veto Analysis**, opponent map stats + AI player stats.
- **Why it helps:** End-to-end opponent prep in one place.
- **Fit for THIS app:** This is the consolidated form of shortlist #5/#6/#7 — see those. "Routines" specifically = **save a filter set and apply it to any/every demo** (e.g. "their A-executes on full-buy") — implement as named, reusable filter presets.
- **Difficulty:** L (suite) · **Priority:** next · **Confirmed**

### 4.8 — Timing Heatmap  ★ (shortlist #7)
- **Source:** wiki; compare; search ("bright orange = go-to timing, dark brown = barely used")
- **Observed:** Per strat/site, a heatmap over the **seconds of the round** showing **when** the team tends to commit; color-codes go-to vs rare timings.
- **Why it helps:** Timing is half a strat. Knowing "they hit A at ~0:18 on full-buys" lets you pre-rotate/pre-util. Also exposes *your own* predictability.
- **Fit for THIS app:** Cheap, high-value: for a filtered set of rounds, histogram the **round-time of first map-contact / first-util / site-execution** and render as a 1-D heat strip per site. Derivable from data the app already has (event ticks → round time). Direct feeder for Playbook + Match Prep + enemy reads.
- **Difficulty:** S–M · **Priority:** now · **Confirmed**

### 4.9 — Recording Suite (Record Round / POV / Team / Browser)
- **Source:** wiki; compare; search
- **Observed:** Desktop app **launches CS2, downloads the demo, and auto-records each player's POV / the round / the whole team** in the background; also browser-based recording. Queue rounds/POVs and it renders them unattended.
- **Why it helps:** Produces real in-engine footage for film sessions without manual demo-driving — huge time saver for coaches.
- **Fit for THIS app (scoped):** Driving the real CS2 client is a big, fragile undertaking (out of scope for now). The *pragmatic local equivalent* = export from the app's own **3D replay** (canvas/WebGL capture to webm) for a queued playlist of rounds/deaths. Mark "drive real CS2 to record POV" as a possible future desktop-companion, but lead with 3D-replay export.
- **Difficulty:** L (3D export) / XL (drive CS2) · **Priority:** later · **Confirmed** (feature) / **Inferred** (scoping)

### 4.10 — Filters (players/weapons/events/outcomes) + Multiround Analysis  ★ (shortlist #4/#5)
- **Source:** wiki ("Find specific rounds instantly… by players, weapons, events, outcomes, and more"; "Analyze multiple rounds simultaneously to identify patterns")
- **Observed:** Powerful round search + simultaneous multi-round analysis to find tendencies.
- **Why it helps:** The backbone of pattern-finding and efficient review at scale.
- **Fit for THIS app:** See shortlist #4 (multi-round overlay/aggregation) and #5 (filters/Routines). Build the filter engine + a "select these rounds → aggregate" overlay. Foundational — many other features depend on it.
- **Difficulty:** M · **Priority:** now · **Confirmed**

### 4.11 — Strategic Board (tactical planner)
- **Source:** wiki; compare ("map floors, drawing layers, shared team notes for chalk-talk and pre-match prep")
- **Observed:** A standalone **chalkboard**: load map (with floor levels), draw strat layers, attach shared notes — for planning strats, not just reviewing demos.
- **Why it helps:** Lets a team *design and document* strats (not only analyze past ones); the planning counterpart to the Playbook.
- **Fit for THIS app:** Add a **Strat Board**: the app already has map radar images + a telestrator. Make a non-replay canvas per map where the team draws player paths/util in layers, names the strat, and saves it. Link a designed strat to real example rounds from the Playbook. Reuses the drawing tools on a static map.
- **Difficulty:** M · **Priority:** next · **Confirmed**

### 4.12 — FaceIt / matchmaking integration
- **Source:** compare ("browse and analyze your FaceIt matches without manual demo downloads")
- **Observed:** Auto-pull FACEIT match demos into the tool.
- **Why it helps:** Removes the manual download/upload chore → more demos actually get reviewed.
- **Fit for THIS app:** A **FACEIT demo importer** (auth + fetch recent match demos by the public API/URL into the upload pipeline). Nice convenience; respects the "don't bypass logins" rule (user authenticates themselves). Medium value for a small team that mostly reviews scrims.
- **Difficulty:** M · **Priority:** later · **Confirmed**

---

## 5. NOESIS  (noesis.gg)

> Positioning: **fast, focused 2D analytics** — "hours → seconds." Lean, serious-team tool (€9.99/mo); its differentiator is **speed + multi-round aggregation**, not breadth.

### 5.1 — 30-Second Upload-to-Analysis Workflow
- **Source:** https://www.noesis.gg/ ("Upload GOTV demos directly in the browser and be ready… in 30 seconds")
- **Observed:** Browser upload → analyzable in ~30s; emphasis on eliminating slow demo scrubbing.
- **Why it helps:** Friction kills review habits; fast parse = more demos reviewed.
- **Fit for THIS app:** Audit/optimize the Flask+demoparser2 parse path: parse **once**, cache a compact per-demo JSON (positions/events/stats) so re-opens are instant; show a progress bar; lazy-load 3D geometry. Make "upload → first frame" as fast as possible. Pure perf/UX work on existing pipeline.
- **Difficulty:** M · **Priority:** next · **Confirmed**

### 5.2 — Instant Temporal Navigation (no scrubbing)
- **Source:** noesis.gg ("instant temporal navigation, eliminating lengthy demo scrubbing")
- **Observed:** Jump instantly to any round/event rather than fast-forwarding a demo.
- **Why it helps:** Review speed.
- **Fit for THIS app:** App has round nav + deep-links. Add an **event index rail** (kills, util, bomb plant/defuse, clutch start) you can click to jump — "skip to next kill / next death / next util." Trivial over parsed event list; big speed win.
- **Difficulty:** S · **Priority:** now · **Confirmed**

### 5.3 — Multi-Round / Cross-Match Aggregation  ★ (shortlist #4)
- **Source:** noesis.gg ("Compare multiple rounds at once. Even from different matches!"); toolbox finds "movement patterns, utility strats, entry kills"
- **Observed:** Stack many rounds — across different matches — into one overlay to reveal tendencies (positions, util, entries).
- **Why it helps:** This is the conceptual leap from "watch one round" to "see what we *always* do" — the heart of pattern analysis.
- **Fit for THIS app:** See shortlist #4. Implement a "**multi-select rounds (via filters) → overlay**" mode for positions/deaths/util/entries on the 2D radar, working across the whole library. Highest-leverage single capability Noesis demonstrates.
- **Difficulty:** M · **Priority:** now · **Confirmed**

### 5.4 — Round Selection by Criteria
- **Source:** noesis.gg ("Filter and select specific rounds by criteria — e.g. buy rounds on a particular side")
- **Observed:** Pick rounds by buy-type/side/etc. for focused study.
- **Why it helps:** Cuts noise; pairs with aggregation.
- **Fit for THIS app:** Same as the filter engine (#5 / 4.10) — buy-type + side are must-have filter dimensions.
- **Difficulty:** — (covered by #5) · **Priority:** now · **Confirmed**

### 5.5 — Utility Inspection / Entry-Kill Toolbox
- **Source:** noesis.gg ("inspect utility," "entry kills," "movement patterns")
- **Observed:** Dedicated lenses for util usage and entry-kill situations.
- **Why it helps:** Util execution + entry success are the two highest-impact T-side levers.
- **Fit for THIS app:** App has util heatmaps + opening duels. Add an **"entries" lens** (filter to opening duels, overlay entry paths + outcome, success% by site/route) and a **util-inspection lens** (grenade-only mode 4.5). Reuses opening-duel data already computed.
- **Difficulty:** M · **Priority:** next · **Confirmed**

---

## 6. REFRAG  (refrag.gg)

> Positioning: primarily a **practice-server + training** ecosystem (Crossfire/Prefire/Recoil/Retakes/Scrim on on-demand servers), with an **analysis/coaching side** (Refrag Coach + 2D viewer + aim stats). For THIS app, the relevant lessons are the **coaching loop** and the **analysis→drill handoff**.

### 6.1 — Refrag Coach (weakness → personalized plan)  ★
- **Source:** https://refrag.gg/
- **Observed:** Analyzes matches to **"pinpoint your real in-game weaknesses"** and **builds a personalized training plan** with "clear, actionable feedback after every match."
- **Why it helps:** Closes the loop: analysis isn't useful unless it tells you *what to practice next*. The "after every match → here's your plan" cadence drives habit.
- **Fit for THIS app:** App already has focus-areas + drills + practice plans — Refrag Coach validates the model. Upgrade the handoff: from the weakest **sub-rating axis (2.2)** and the top recurring **mistake categories (3.1)**, auto-generate a ranked **"this week's training plan"** with specific drills (e.g. low counter-strafe% → counter-strafe routine; high TTD → reaction/prefire; util-quality red on flashes → flash-lineup practice). Make it regenerate each match and track whether the weak axis improves (ties to rolling-window 3.3).
- **Difficulty:** M · **Priority:** now · **Confirmed**

### 6.2 — 2D Demo Viewer + Detailed Aim Stats
- **Source:** refrag.gg (Competitor tier+)
- **Observed:** In-platform 2D demo review + detailed aim statistics.
- **Why it helps:** Keeps review + practice in one ecosystem.
- **Fit for THIS app:** App already has the 2D viewer; the **detailed aim stats** = exactly the 2.4 mechanics stats. Covered.
- **Difficulty:** — · **Priority:** — · **Confirmed**

### 6.3 — Drill library mapping (Crossfire/Prefire/Recoil/Bootcamp/Routines)
- **Source:** refrag.gg
- **Observed:** Named drills — **Crossfire** (aim), **Prefire** (pre-aim common angles), **Recoil Trainer** (spray control), **Bootcamp** (structured routines from weaknesses), **Routines/NADR** (personalized + nade practice).
- **Why it helps:** A concrete vocabulary of *what to practice* for each weakness.
- **Fit for THIS app:** The app can't run practice servers, but it can **recommend the right drill type + link to in-game practice configs / workshop maps** per detected weakness, and explain the drill. I.e. map each weak stat → a named drill recommendation (aim→prefire/crossfire, spray→recoil, util→nade routine). Pure mapping table feeding the training plan (6.1).
- **Difficulty:** S · **Priority:** next · **Confirmed**

---

## 7. INCIDENTAL FINDINGS (surfaced while searching — worth stealing)

### 7.1 — smartCoach: per-moment AI coaching ★ (feeds shortlist #8)
- **Source:** https://smartcoach.gg/ (search-surfaced)
- **Observed (Inferred from listing snippet):** **Round-by-round VOD review with AI coaching per moment** — for each key moment it states the **win-probability swing**, **what happened**, and **"the habit to build."**
- **Why it helps:** The "habit to build" framing turns a stat blip into a *behavior change* — the missing verb in most analytics.
- **Fit for THIS app:** App has the insight feed + per-kill win-prob. Reframe each flagged moment into the **three-line smartCoach pattern**: *win-prob swing → what happened → the one habit to build.* Mostly a templating/UX change over existing data; optionally enrich phrasing with a local LLM. High perceived value, low effort.
- **Difficulty:** S–M · **Priority:** now · **Inferred** (description from search; not directly fetched)

### 7.2 — Native CS2 demo workflow (marktick / 2x kill-feed skim)
- **Source:** switchbladegaming.com, tradeit.gg (search-surfaced)
- **Observed:** Pros bind **`demo_marktick` / `demo_gotomark`** to bookmark/return to ticks; skim kill-feed at **2x** to find the round-deciding early deaths fast. "3 binds cut review from 10 min to 3."
- **Why it helps:** Confirms bookmarks (1.5) + variable-speed + jump-to-event (5.2) are the highest-ROI review primitives — exactly what pros bind by hand.
- **Fit for THIS app:** Reinforces shortlist #3 and 5.2. Ensure keyboard shortcuts mirror these habits (key to bookmark current tick; key to jump next/prev death; speed toggle).
- **Difficulty:** S · **Priority:** now · **Confirmed**

### 7.3 — PRACC (team scrim/demo platform)
- **Source:** https://pracc.com/counter-strike (search-surfaced)
- **Observed (Inferred):** Bookmarks for later review, **timestamped commentary section**, labels + drawings, advanced demoviewer with **3D**, easy round selection.
- **Why it helps:** Another confirmation that **timestamped, labeled, shareable notes/commentary** is the team-review standard.
- **Fit for THIS app:** Reinforces notes/tags/bookmarks (1.5) — specifically add **timestamped commentary threads** per round (a coach types notes tied to ticks; player reads them while watching). Cheap extension of the notes store.
- **Difficulty:** S · **Priority:** next · **Inferred**

---

## CROSS-PLATFORM SYNTHESIS — what "pro" tools have that this app should prioritize

1. **Axis sub-ratings + percentile benchmarks** (Leetify) — *every* serious tool frames skill as Aim/Util/Positioning vs a percentile. App's biggest single gap. → #1/#2.
2. **Win-probability/impact rating with distributed credit** (Leetify) — app is *already* aligned (has win-prob swing); formalize a distributed, zero-sum impact rating. → #1 list, 2.1.
3. **Workflow primitives: filters → multi-round aggregation → bookmarks/notes/playlists → routines** (Noesis, CS2.CAM, Skybox, PRACC) — the scaffolding that makes everything usable at team scale. App has the data + replay; lacks the organizing layer. → #3/#4/#5.
4. **Anti-strat / Playbook / Timing / enemy-reads** (Skybox Tier-1, CS2.CAM Pro, SCOPE prematch) — the expensive, paywalled, team-defining capability a *free local* tool can uniquely democratize. → #6/#7, 3.5.
5. **Coaching that ends in a verb** (Refrag Coach, smartCoach) — analysis must output "the habit to build" + "this week's drills," regenerated each match and tracked over rolling windows. App has the pieces; tighten the loop. → #8, 6.1, 3.3.
6. **Death Review + 3D** (CS2.CAM) — the app's real-geometry 3D replay is a *strength competitors paywall*; build a one-click "watch all my deaths in 3D, framed" mode on top of it. → 4.6.
7. **Voice-comm sync + reflection journal** (Skybox/CS2.CAM; Leetify) — under-served in *local* tools; both are cheap given the app's existing timeline + match library and add a "real coaching" / longitudinal feel. → #9/#10.

## What I could NOT fully access (mark Inferred / re-check)
- **Leetify homepage, scope.gg/replay, refrag.gg, noesis.gg, smartcoach.gg root** are JS-heavy SPAs — WebFetch returned thin/empty bodies. Feature details were reconstructed from **Leetify's server-rendered blog posts** (high confidence, Confirmed), the **CS2.CAM comparison + wiki pages** (Confirmed), and **search-result snippets/reviews** for the rest (labeled Inferred where not directly fetched). 
- **smartCoach (7.1)** and **PRACC (7.3)** descriptions are from search snippets only → **Inferred**; verify on their sites before committing to exact wording.
- **Skybox exact tier-by-tier feature gating** was read from the pricing page but Skybox names some features only in marketing copy (Pattern Finder, Match Prep, Buy-Type filters) that didn't appear on the pricing table — treat those as **Confirmed-exist / tier-uncertain**.
- **bo3.gg comparison article** returned an empty body (bot-blocked); its content was partially recovered via search snippets only.
- No paywalled/login-gated content was accessed (per rules). All pricing figures are from public pricing pages or search snippets and may have changed.

---

*End of Research A.*
