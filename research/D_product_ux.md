# Workstream D — Product / UX: The Team Learning Loop
*Research agent D · 2026-06-15 · for CS2DemoPlayer (Flask + demoparser2; vanilla-JS + Three.js) at C:\Users\USER\CS2DemoPlayer*

**Mandate:** design how this app turns its existing analytics into *behavior change* for a small structured team — the loop from **review a demo → what we practice next → measured improvement**. This doc is a build spec, not a feature wishlist. Every item is tagged: **Source** · **Observed** · **Why it drives improvement** · **Fit** · **Difficulty (S/M/L/XL)** · **Priority (now/next/later)** · **Confirmed / Inferred**.

> **Design constraint honored throughout:** match the app's existing **JSON-on-disk, stdlib-only, atomic-write** persistence style (see `nades.py`, `teams.py`, `practiceplan.py`, `library.py`). New state = small JSON files in the project dir (or `cache/`), each with a tiny load/save module and a thin Flask route. No DB. No new heavy deps. All identity via stable hashed ids like the existing `_nid()` / practice-item-hash pattern.

---

## ★ LEARNING-LOOP — BUILD-NOW SEQUENCE (ordered so each piece builds on the last)

This is the shortlist. Build top-to-bottom; each step makes the next one cheap.

1. **Bookmarks & Notes data layer** (`reviews.py` + `/api/bookmarks`) — **S/M, now.** The atom everything else is made of. A bookmark = `{demo_id, round, tick, player?, category, polarity, text, author}`. Mirrors `nades.py` exactly. **Auto-seed it from the insights engine you already have** (every insight already carries `round`/`tick`/`type`/`severity`/`polarity`/`confidence` — convert each into a system-authored bookmark). Manual add from the replayer ("B" key → note at current tick).
2. **Review Playlists** (`/api/playlists`, same module) — **M, now.** A playlist = an ordered list of bookmark refs (or raw `{demo_id,round,tick}` clips). **Auto-queues** are just saved filters over bookmarks+insights (top thrown rounds / failed retakes / untraded deaths / dry opens / good team rounds). Player presses ▶ → replayer walks the queue, jumping to each `watch_t`/`tick`. You already have round+tick deep-linking (`round_cards[].watch_t`, insight `tick`); a playlist is the multi-clip wrapper around it.
3. **Match Report (one polished page)** (`/api/report/<demo_id>`) — **M, now.** Mostly a *layout* over data you already compute: score/map (library row), 3 biggest team fixes (`team_coaching.top_areas` / `team_review`), 3 positives (positive-polarity insights), top-5 rounds to review (rank `round_cards` by `|swing|`/severity), utility summary, role/player notes, the auto-generated practice plan. Add **"Start review playlist"** + **"Send fixes to Goals"** buttons → this is where the loop turns from *read* to *act*.
4. **Practice Goals** (`goals.py` + `/api/goals`) — **M, now.** Generalizes the existing `practice.json` done-map into trackable, **measurable** goals: `{text, metric, target, scope(player/team), source_demo, status, created, due}`. A goal is born from a Match Report fix or a focus area in one click. This is the "what we practice next" node.
5. **Goal-aware Progress Tracking** (extend `matchindex.py`) — **M/L, next.** You already compute per-player trend series + first/second-half deltas. Add: rolling **last 3/5/10** windows, **per-map / side / role** splits, **focus-item improvement** (did the metric behind each open goal move?), **repeated-mistake detection** (same insight `type` recurring across N demos), and **sample-size warnings** (`n < k` → "directional only"). Feeds goal cards a ✓/✗/→ verdict.
6. **Recurring Goals / Review Sessions** (`sessions.py` + `/api/sessions`) — **M, next.** A lightweight cadence object: a dated review session bundles {a demo, its playlist, notes taken, goals set/checked}. Recurring goals = goals with `cadence` that re-evaluate every session. This makes the loop a *habit*, not a one-off.
7. **Playbooks / Defaults + adherence check** (`playbook.py` + `/api/playbook`) — **L, later.** Team's agreed defaults (anchor X never re-peeks without flash; A-site execute uses these 3 smokes; eco = stack B). Then a **"did we follow it?"** pass cross-checks new demos against the rule (uses the same detectors that power insights). Highest-value, but depends on everything above and on map/zone work.

**Loop in one sentence:** *insights auto-become bookmarks → bookmarks compile into a review playlist → the match report names 3 fixes → fixes become measurable goals → next demos are graded against those goals → goals live inside recurring review sessions → durable agreements become playbooks the app checks adherence to.*

---

## 0. What this app ALREADY has (extend, do NOT rebuild)

Verified by reading the source. This is the foundation the loop sits on.

| Capability | Where | Loop role |
|---|---|---|
| Per-death/round/player **insights** with `round`, `tick`, `type`, `severity`, `polarity` (issue/good), `confidence`+reason, `evidence` | `analytics.py` `build_insights` / `build_advanced_insights` / `_stamp_confidence` | **Auto-source for bookmarks** (step 1) and **auto-queues** (step 2) |
| **Focus areas** per player (top-5) + benchmarks | `analytics.py` `build_focus`, `BENCH` | Seeds **Goals** (step 4) |
| **Team review**: `top_areas` + `practice_plan` + `buy_outcomes` | `build_team_review` | Match Report "3 fixes" + practice plan (step 3) |
| **Team coaching**: per-round **loss taxonomy** (`_team_loss_reason`) + practice plan | `build_team_coaching` | Match Report + auto-queues (thrown rounds, failed post-plant) |
| **Round cards**: per-round narrative + **`watch_t` replay deep-link** + swing `moments` | `build_round_cards`, `compute_round_swing` | **Playlist clips** + "top-5 rounds" (steps 2,3) |
| **Rating breakdown** (Leetify-style 9-category decomposition) | `build_breakdown` | Match Report body |
| **Multi-demo trends**: per-player series, averages, **1st-vs-2nd-half delta** | `matchindex.py` `player_trends` | Base for **Progress Tracking** (step 5) |
| **Practice-plan done-state** (hash-keyed) | `practiceplan.py` + `practice.json` | Generalize into **Goals** (step 4) |
| **Team roster + intended roles** | `teams.py` + `team.json` | Role-scoped goals/progress |
| **Nade library** full CRUD JSON-on-disk (id via `_nid` hash, atomic write, import) | `nades.py` | **The exact template** for `reviews.py` / `goals.py` |
| **Saved demo library** (id = `source_sha1`, score, map, rounds, dedupe) | `library.py` | `demo_id` keyspace for all new objects |
| 2D + 3D replay, scrub, round jump, click-spectate, scoreboard, killfeed, nade/bomb markers, bullet traces | `static/js`, `app.py` | The surface playlists/bookmarks drive |

**NET:** The app already does steps (1-auto), (2-data), and most of (3) and (5) at the *engine* level. **What's genuinely new is the persistence + UX that turns those engine outputs into saved, shared, trackable team artifacts** — bookmarks, playlists, the report page, goals, sessions, playbooks. We are wiring existing outputs into a loop, not computing new analytics (except the progress extensions in step 5).

---

## 1. END-TO-END TEAM LEARNING LOOP — UX flow + data read/write per step

Target users: a small structured team (≤10, see `team.json`). One person (the IGL/coach) usually drives review; everyone can read playlists/notes/goals. Everything is local + offline + shared via the same on-disk JSON (single source of truth on the host machine; "shared" = same install, matching the app's current model).

```
(1) UPLOAD/REVIEW ──► (2) AUTO-DETECT ──► (3) PLAYLIST ──► (4) NOTES/BOOKMARKS
        ▲                                                          │
        │                                                          ▼
(7) PLAYBOOK ◄──── (6) COMPARE vs GOALS ◄──── (5) PRACTICE GOALS ◄─┘
   adherence              progress                set from fixes
```

| Step | What the user does | Reads | Writes |
|---|---|---|---|
| **1. Upload / review** | Drops `.dem`/`.zip`; opens it; lands on **Match Report** | `library.json`, `lib_<id>.json` | (parse cache, already handled) |
| **2. Auto-detect** | Sees "what went wrong/right" — no action needed | `analytics.insights`, `team_coaching`, `round_cards`, `breakdown` | **auto-bookmarks** into `reviews.json` (system-authored, `author:"auto"`) — idempotent per `(demo_id,type,round,tick)` |
| **3. Build playlist** | Clicks an **auto-queue** ("Failed retakes (4)") or hand-picks clips → saves "vs NaVi — Inferno fixes" | bookmarks + insights + `round_cards` (for `watch_t`) | a playlist in `reviews.json` (ordered clip refs) |
| **4. Notes / bookmarks** | In replayer, presses **B** at a tick → types a note, picks category/player; @teammate optional | current tick/round/spectated player from replayer state | a manual bookmark (`author:"<name>"`) |
| **5. Practice goals** | On report or a focus card, clicks **"Make this a goal"** → picks metric+target, scope, due | `focus`, `team_review.top_areas`, benchmarks | a goal in `goals.json` |
| **6. Compare vs goals** | Opens **Progress**; each open goal shows ✓/✗/→ vs target over last N demos; repeated mistakes surfaced | `goals.json` + `matchindex` trends (rolling N, splits) | goal `status`/`history` updates; flags repeated `insight.type` |
| **7. Playbook + adherence** | Records a default ("anchor never dry re-peeks"); after a new demo, sees "Followed 7/9 rounds" | `playbook.json` + the relevant detector over the new demo | playbook entries; per-demo adherence record |

**The two hinge moments** (where most teams' loops break, per the coaching sources): (a) **report → goal** (turning observation into a *specific rule*) and (b) **goal → next-demo verdict** (closing the loop with measurement). Both get dedicated one-click buttons so the loop can't silently die.

---

## 2. Component specs (schema'd)

All schemas are illustrative JSON matching the app's style. IDs are stable hashes (`b_…`, `pl_…`, `g_…`, `sess_…`, `pb_…`) like `nades._nid`.

### 2.1 Notes & Bookmarks — `reviews.py` → `reviews.json`
*The atom of the whole loop.*

- **Source:** [PRACC 2D replay](https://pracc.com/counter-strike) — "commentary section with **timestamps, labels and drawings**"; [CS2.CAM wiki](https://cs2.cam/en/wiki/introduction) — "**Team notes & mentions**… @mentions that notify teammates and jump right to the round"; [Skybox EDGE](https://skybox.gg/edge/) (playlists). **Observed:** timestamped, labeled, categorized annotations attached to specific rounds, shareable, with @mentions that deep-link. **Why it drives improvement:** the boosteria demo-review guide is explicit — vague notes ("aim bad") don't change behavior; **structured notes with a category + "what I should have done" + "practice action"** do. A timestamp+category turns a fleeting observation into a reusable, queryable artifact and into the seed of a goal. **Fit:** clone `nades.py` (load/normalize/add/update/delete/atomic-save). **Confirmed** (PRACC/CS2.CAM features public).

```jsonc
// reviews.json  →  { "bookmarks": [...], "playlists": [...] }
{
  "bookmarks": [
    {
      "id": "b_a1b2c3d4e5",            // sha1(demo_id|round|tick|author|text)[:10]
      "demo_id": "<source_sha1>",      // FK → library.json row
      "round": 14,                     // 1-based; required for "jump to round"
      "tick": 28160,                   // optional; null = whole-round bookmark
      "watch_t": 440.0,                // seconds = tick/tickrate, cached for the player
      "player": "7656119…",            // optional steamid (uint64 string) the note is about
      "category": "positioning",       // taxonomy below (matches boosteria buckets)
      "polarity": "issue",             // issue | good | neutral
      "text": "Anchor dry re-peeked connector with no flash; died untraded.",
      "fix": "Fall after one duel or call for flash before re-peek.",  // optional "what I should've done"
      "mentions": ["7656120…"],        // steamids to surface to / @
      "author": "auto",                // "auto" (from insights) | display name
      "insight_type": "untraded_opening_death", // set when auto-derived → enables repeated-mistake matching
      "created": "2026-06-15T21:30:00"
    }
  ]
}
```
- **Taxonomy** (reuse everywhere — bookmarks, goals, playbook): `positioning · crosshair · utility · timing · trade · economy · decision · communication · mechanics · clutch · post-plant · retake`. (Sourced from the [boosteria mistake buckets](https://boosteria.org/guides/cs2-demo-review-guide-pros-look-improve-fast) — confirmed list.)
- **Auto-seed rule (the magic):** on first open of a demo's report, walk `analytics.insights` → for each insight with a `round`, upsert a bookmark `author:"auto"`, mapping insight `type`→`category`, carrying `polarity`/`severity`/`confidence`. Idempotent by `(demo_id, insight_type, round, tick)` so re-opening never duplicates. **This is why the app gets a populated review surface for free.**
- **Replayer hook:** **B** = bookmark at current `(round, tick, spectated player)`; **N** = jump to next bookmark; bookmarks render as ticks on the existing timeline (you already draw event markers).
- **Difficulty S/M · Priority NOW · Confirmed.**

### 2.2 Review Playlists — same module → `reviews.json["playlists"]`
- **Source:** [Skybox EDGE](https://skybox.gg/edge/) ("playlist function… filter and rewatch gunrounds or pistol rounds"); [CS2.CAM](https://cs2.cam/en/wiki/introduction) ("save rounds into **Playlists** with **folders and share with teammates**"); [PRACC](https://pracc.com/counter-strike) ("multiple rounds view, even **across multiple matches**"). **Observed:** ordered, named, shareable collections of clips/rounds, often filtered by round type, spanning matches. **Why it drives improvement:** review time is the bottleneck (the "30-min routine" / "3 binds cut review 10→3 min" sources). A playlist makes a debrief *linear and finite* — the team watches the 6 rounds that matter instead of scrubbing 24. Auto-queues remove the curation tax entirely. **Fit:** a playlist is just an ordered list of clip refs over data you already deep-link to. **Confirmed.**

```jsonc
{
  "playlists": [
    {
      "id": "pl_77af0e21bd",
      "name": "vs NaVi — Inferno fixes",
      "scope": "team",                 // team | <steamid>
      "demo_ids": ["<sha1>"],          // 1+ demos (cross-match supported, like PRACC)
      "auto": "failed_retakes",        // null if hand-built; else an auto-queue key (below)
      "clips": [                       // ordered; each resolves to a replayer jump
        {"demo_id":"<sha1>","round":3,"tick":null,"bookmark_id":"b_…","label":"Lost retake B"},
        {"demo_id":"<sha1>","round":11,"tick":52280,"label":"Untraded entry"}
      ],
      "created":"2026-06-15T21:35:00","author":"coach"
    }
  ]
}
```
- **Auto-queue keys → existing data (no new analytics needed):**
  | Key | Built from | Confirmed/Inferred |
  |---|---|---|
  | `thrown_rounds` | `team_coaching` rounds where `_team_loss_reason ∈ {"Threw a 2+ man advantage","Lost with a man up"}` | Confirmed (concept widely cited) |
  | `failed_postplants` | T-side lost rounds with `plant_by_round` set + loss reason "Lost the post-plant" | Confirmed |
  | `failed_retakes` | CT-side lost rounds where bomb planted + not in `defuse_rounds` | Confirmed |
  | `untraded_deaths` | insights `type=="untraded_opening_death"` (+ untraded non-opening) | Confirmed (engine already emits) |
  | `dry_opening_deaths` | insights `type=="dry_opening"` | Confirmed (engine already emits) |
  | `good_team_rounds` | rounds with multiple positive-polarity insights / high `|swing|` won rounds | Confirmed |
  Each key = a pure function `(analytics) → [clip]`; the "playlist" is just that list saved with a name. **Auto-queues are live filters; saving one freezes it.**
- **Playback:** "▶ Play" feeds clips to the replayer one by one (jump to `tick`/`watch_t`, play to round end or next clip). Reuses existing round-jump + scrub.
- **Difficulty M · Priority NOW · Confirmed.**

### 2.3 Match Report (one polished page) — `/api/report/<demo_id>` (assembler, mostly no new compute)
- **Source:** [Leetify match report / "Your Match"](https://leetify.com/) (Match Identity, top-5 stats, this-match-vs-last-30 with percentile, rating breakdown); [Skybox EDGE "Match Reports"](https://skybox.gg/edge/); your own `ANALYTICS_SPEC.md` §5/§5b. **Observed:** a single scannable post-match page leading with the biggest leaks + best moments, a small set of headline stats vs a benchmark, a per-round rating decomposition each with "Watch in 2D Replay," and a clear "what to do next." **Why it drives improvement:** consolidates the loop's *diagnosis* into one place and — critically — puts the **action buttons** (start playlist, make goal) right next to the findings, so review flows into practice without a context switch (the hinge moment from §1). **Fit:** 90% layout over `analytics` you already compute; add only the action wiring. **Confirmed.**

**Layout (top→bottom):**
1. **Header:** map · final score (`library` row) · date · sides won (T/CT) · 1-line "match identity" (derive from dominant insight polarity/type, à la Leetify "The Utility Lover").
2. **3 biggest team fixes** (cards): from `team_coaching.top_areas` / `team_review.practice_plan` — each card → **[Make goal]** + **[Build playlist]**.
3. **3 best positives** (cards): top positive-polarity insights / best round swings — morale + "keep doing this."
4. **Top-5 rounds to review:** `round_cards` ranked by `max(|swing|, severity)`; each row = summary + **[Watch]** (`watch_t`) + **[+ Playlist]**. One-click **"Play all 5."**
5. **Utility summary:** UDR, enemies-flashed, unused-util-on-death, team-flash count (all already computed) vs `BENCH`.
6. **Role / player notes:** per-player mini-row — intended role (`teams.role_of`) vs inferred role, KAST/ADR/HLTV vs benchmark, that player's top focus + auto-bookmark count.
7. **Practice plan:** the generated plan (`team_review`/`team_coaching.practice_plan`) with done-checkboxes (existing `practiceplan` state) and a **"Promote all to Goals"** button.
8. **Footer actions:** Start review session (§2.6) · Export report (print-to-PDF via browser; no dep).
- **Difficulty M · Priority NOW · Confirmed.**

### 2.4 Practice Goals — `goals.py` → `goals.json`
*Generalizes `practice.json` from a done-checkbox into a measurable, trackable goal.*

- **Source:** [Leetify improvement loop](https://leetify.com/blog/leetify-guide-how-to-start-improving-in-csgo/) (identify weakness → train → **track the metric vs a previous period** → graduate → new goal); [Refrag Coach](https://refrag.gg/blog/announcing-refrag-coach-the-easiest-way-to-track-analyze-and-practice-for-cs2/) (analyze weakness → assign training → track); [boosteria](https://boosteria.org/guides/cs2-demo-review-guide-pros-look-improve-fast) (**"choose only two goals per session," "create rules not hopes,"** "graduate completed goals"). **Observed:** a small number of specific, measurable goals tied to a metric and a target, reviewed against later data, then retired. **Why it drives improvement:** this is the single highest-leverage finding across every source — improvement = *few, specific, measured* goals, not a long vague list. Specificity ("fall after one duel or call flash before re-peek") + a number target is what separates teams that improve from teams that re-watch forever. **Fit:** extend the existing `practiceplan` concept; reuse its hash-id + atomic-write. **Confirmed.**

```jsonc
// goals.json → { "goals": [...] }
{
  "goals": [
    {
      "id": "g_3f9c1a77b2",
      "text": "Anchors: never dry re-peek without flash support.",  // a RULE, not a hope
      "category": "positioning",
      "scope": "team",                 // team | <steamid>
      "metric": "untraded_opening_death_rate", // a tracked stat OR an insight-type rate; null = qualitative
      "direction": "down",             // up | down
      "baseline": 0.22,                // value at creation (from current demo / trend)
      "target": 0.10,
      "status": "active",              // active | met | missed | archived
      "source_demo": "<sha1>",         // provenance: which review spawned it
      "cadence": "weekly",             // null (one-off) | weekly | per_session  → recurring goals
      "created": "2026-06-15", "due": "2026-06-29",
      "history": [                     // appended by Progress each time a new demo is graded
        {"demo_id":"<sha1>","date":"2026-06-18","value":0.18}
      ]
    }
  ]
}
```
- **Creation paths:** (a) Match-report fix card → prefilled goal; (b) player focus card → goal; (c) manual. Enforce a soft cap / warning at **>3 active goals per scope** (boosteria "two per session" rule) so the team stays focused.
- **Metric vocabulary:** reuse `_STATS`/`_PLAYER_FIELDS` from `matchindex` (`hltv,adr,kast,open_wr,traded_pct,udr,kd`) **plus** insight-type *rates* (count of `type` per round, e.g. `dry_opening` per round) so qualitative process leaks become measurable.
- **Difficulty M · Priority NOW · Confirmed.**

### 2.5 Progress Tracking (goal-aware) — extend `matchindex.py`
- **Source:** [Leetify](https://leetify.com/) (this-match-vs-last-30, **percentile** framing, seasonal benchmark recalibration); [SCOPE.GG dashboard](https://scope.gg/cs2-dashboard/) ("My Progress," growth areas, performance trends); [Refrag](https://refrag.gg/) (real-time progress vs baseline ELO); your `ANALYTICS_SPEC.md` (trend sparklines, T/CT splits, buy-type splits). **Observed:** rolling-window comparisons, side/map/role splits, "is this specific weakness improving?", and progress-vs-a-baseline. **Why it drives improvement:** closes the loop — without a verdict, goals are just to-dos. The boosteria guide's core insight: **"the highest-value mistake is the one that repeats often"** → the app must *detect recurrence*, not just show one match. Sample-size honesty keeps the team from chasing noise (your `meta.note` already models this caution). **Fit:** you already have `player_trends` (series + averages + half-delta); add windows, splits, goal-grading, repeat-detection. **Confirmed.**

**Add to `matchindex.py` (returned shape additions):**
```jsonc
{
  "windows": { "last3": {...avg per stat...}, "last5": {...}, "last10": {...} },
  "splits": {                          // each split = same stat block
    "side":  { "T": {...}, "CT": {...} },
    "map":   { "de_inferno": {...}, "de_mirage": {...} },
    "role":  { "entry": {...}, "anchor": {...} }   // via teams.role_of + inferred role
  },
  "goal_progress": [                   // one per open goal touching this player/team
    { "goal_id":"g_…", "baseline":0.22, "target":0.10, "current":0.14,
      "n":4, "verdict":"improving",    // improving | met | regressed | flat
      "low_sample": false }            // true when n < 3  → UI shows "directional only"
  ],
  "repeated_mistakes": [               // recurrence detector (the high-value signal)
    { "type":"untraded_opening_death", "demos":5, "rounds_total":13,
      "trend":"up", "scope":"7656119…" }   // appears in ≥3 of last N demos
  ],
  "sample_warning": "Only 2 demos — trends are directional, not conclusive."  // when n<k
}
```
- **Rolling windows:** last 3/5/10 demos (you already sort by `created_at`); show alongside all-time.
- **Repeated-mistake detection:** count distinct demos each `insight.type` appears in across last N; flag types in **≥3 of last 5** (or ≥40%) as "recurring" → these auto-suggest new goals and auto-build a `repeated_mistakes` playlist. **This is the engine that makes the loop *compounding* rather than per-match.**
- **Sample-size warnings:** any aggregate with `n < 3` (per split) tagged `low_sample`; UI renders muted + "directional." Reuse the honesty tone already in `analytics.meta`.
- **Difficulty M/L · Priority NEXT · Confirmed (concept) / Inferred (exact split granularity).**

### 2.6 Recurring Goals / Review Sessions — `sessions.py` → `sessions.json`
- **Source:** [PRACC](https://pracc.com/counter-strike) / [skin.club scrim culture](https://community.skin.club/en/news/scrim-culture-in-counter-strike-how-pro-teams-improve-faster-by-golden) ("Scrims are **almost always followed by a debrief**… plan focus areas for upcoming days… **homework** is assigned"); [boosteria](https://boosteria.org/guides/cs2-demo-review-guide-pros-look-improve-fast) ("review consistently, not only after losses," "graduate completed goals and introduce new ones"); [wecoach](https://wecoach.gg/blog/article/how-to-coach-cs2) (structured debrief, not spontaneous). **Observed:** a repeated debrief cadence that bundles {watch → decide focus → assign homework → check last time's homework}. **Why it drives improvement:** turns the loop into a *ritual*. Without a recurring container, goals are set once and never re-checked. A session ties a demo+playlist+notes+goals into one dated record and **opens by reviewing the previous session's goals** — forcing the measurement step to actually happen. **Fit:** thin object linking existing ids. **Confirmed.**

```jsonc
// sessions.json → { "sessions": [...] }
{
  "sessions": [
    {
      "id": "sess_9c2a4f",
      "title": "Mon review — Inferno scrim L 11-13",
      "date": "2026-06-15",
      "demo_ids": ["<sha1>"],
      "playlist_ids": ["pl_77af0e21bd"],
      "bookmark_ids": ["b_a1b2c3d4e5"],     // notes captured this session
      "goals_set": ["g_3f9c1a77b2"],         // goals created here
      "goals_reviewed": [                     // last session's goals, graded now
        {"goal_id":"g_prev123","verdict":"met"}
      ],
      "homework": "Anchors: 20 min Yprac prefire on Inferno connector before next scrim.",
      "next_session": "2026-06-18"
    }
  ]
}
```
- **Recurring goals** = goals with `cadence` (§2.4); when a session opens, the app lists every recurring/active goal and pulls its current `goal_progress` for a fast ✓/✗ pass.
- **Session opener UX:** "Last time you set 3 goals → [met] [improving] [regressed]. New demo to review?" — this is the heartbeat of the loop.
- **Difficulty M · Priority NEXT · Confirmed.**

### 2.7 Playbooks / Defaults + adherence — `playbook.py` → `playbook.json`
- **Source:** [CS2.CAM](https://cs2.cam/en/wiki/introduction) (**Playbook** "strategy detector," **Strategic Board** tactical planner, **Routines** opponent patterns); [Skybox EDGE](https://skybox.gg/edge/) ("**Playbook & Match Prep**," "Veto Simulation"); [SCOPE.GG prematch](https://scope.gg/guides/prematch_scopegg_en/) (opponent position/buy/timing tendencies → plan counter-positions). **Observed:** a team's documented defaults/strats + tooling that detects whether play matched a known strategy, plus opponent-tendency prep. **Why it drives improvement:** the final maturity step — durable agreements ("our B-anchor setup," "eco = stack B," "default A execute") become checkable. The **adherence pass** ("we followed our default in 7/9 eco rounds") converts review into accountability, which is what actually changes habits. **Fit:** entries reuse the bookmark taxonomy + map zones; adherence reuses the same detectors that power insights. Heaviest item; depends on map/zone polygons (your `ANALYTICS_SPEC.md` flags zones as callout-based v1). **Confirmed (feature exists publicly) / Inferred (auto-adherence detection depth in this app).**

```jsonc
// playbook.json → { "defaults": [...] }
{
  "defaults": [
    {
      "id": "pb_4d8e",
      "map": "de_inferno",
      "side": "CT",
      "situation": "eco",               // eco | force | full | pistol | default-A | default-B | retake-B …
      "rule": "Stack B (3), A plays for picks + saves.",
      "category": "economy",
      "check": {                         // optional machine-checkable spec → adherence
        "type": "side_distribution",     // a detector key (reuses analytics primitives)
        "expect": {"site":"B","min_players":3}
      },
      "created":"2026-06-15","author":"coach"
    }
  ]
  // adherence written per demo: cache/adherence_<demo_id>.json
  // { "pb_4d8e": {"rounds_applicable":9,"followed":7,"violations":[{"round":6,"why":"only 2 at B"}]} }
}
```
- **Manual-first, auto-later:** ship as documented defaults you can attach to bookmarks/sessions; add the auto-adherence `check` for the few mechanically-detectable rules (site stacking, dry-peek rule, util-on-execute) once map zones firm up. Don't over-build detectors for fuzzy strats — let those be human-graded notes.
- **Difficulty L/XL · Priority LATER · Confirmed/Inferred.**

---

## 3. VOICE-COMM INTEGRATION — feasibility only (research, not a design)

The coaching sources are unanimous that **voice/comms are a top review signal** ([wecoach](https://wecoach.gg/blog/article/how-to-coach-cs2): "assess the clarity and usefulness of their calls"; EGW: "audio discipline… who's calling tempo"). CS2.CAM productizes it: **"Voice comms sync"** with **AI-assisted / manual / YouTube / team-recording** options + **timestamped @mentions** ([wiki](https://cs2.cam/en/wiki/introduction)). So it's valuable — but here's the hard feasibility for *this* app:

| Path | Feasibility | Notes |
|---|---|---|
| **Extract voice straight from the `.dem`** | **Source-dependent.** **Confirmed:** FACEIT / ESEA / SourceTV demos **contain** per-player voice (Opus); **Valve MM & Premier demos contain NO voice** ([steam/hellcase guides](https://hellcase.com/blog/guides/how-to-watch-cs2-demos/)). Tooling exists: **`akiver/csgo-voice-extractor`** (CLI → per-player WAV, Opus decode) and **`DandrewsDev/CS2VoiceData`** ([GitHub](https://github.com/akiver/csgo-voice-extractor)). `demoparser2` itself doesn't expose voice. | If the team plays **FACEIT/ESEA/SourceTV**, an optional post-parse step could shell out to a voice-extractor → per-player WAV → align to ticks (demo has timing). Adds a native `libopus` dep → keep **opt-in / behind a flag**, not core. |
| **Manual audio upload + timestamp sync** | **Easy.** Mirror the existing **nade-video upload** route (`/api/nades/video`: size cap, content-addressed, served from disk). Upload a session recording; user sets a **single sync offset** (anchor audio t=0 to a known round-start tick); player scrubs audio with the replay. | Lowest-risk first step. Reuses code you already have. **S/M.** |
| **Discord recording** | **External tooling.** Discord bots (e.g. Craig) or `CS2-GOTV-Discord` ([GitHub](https://github.com/K4ryuu/CS2-GOTV-Discord)) can capture comms; the app would just **import the resulting audio file** (→ the manual-upload path). No live Discord API integration needed. | Treat as "bring your own recording." |
| **Speech-to-text (later)** | **Deferred.** Local Whisper (`faster-whisper`) could transcribe an uploaded WAV → searchable, timestamped call log → auto-bookmarks ("anchor called rotate at 1:05"). Heavy dep (model + GPU-ish), so **clearly a "later."** | High coaching value (searchable calls), but out of scope for the build-now loop. |

**Recommendation (feasibility verdict):** **(1)** add the **manual audio-upload + one-offset sync** path now (cheap, reuses the nade-video pattern, works for *any* source incl. MM/Premier that lack in-demo voice); **(2)** offer **optional in-demo voice extraction** behind a flag for FACEIT/ESEA/SourceTV users via the existing OSS CLIs; **(3)** keep **STT** as a documented "later." Do **not** build live capture or a Discord integration — import a file instead. Every voice clip, once synced, becomes just another **bookmark** with an audio attachment — so it slots into the loop with zero new concepts.

---

## 4. Priority roll-up

| Component | Difficulty | Priority | New vs Extend | Confidence |
|---|---|---|---|---|
| Bookmarks & Notes (`reviews.py`) | S/M | **now** | New (auto-seeded from existing insights) | Confirmed |
| Review Playlists (+ auto-queues) | M | **now** | New (filters over existing data) | Confirmed |
| Match Report page | M | **now** | Extend (layout over existing analytics) | Confirmed |
| Practice Goals (`goals.py`) | M | **now** | Extend `practiceplan` | Confirmed |
| Progress Tracking (goal-aware) | M/L | **next** | Extend `matchindex` | Confirmed/Inferred |
| Recurring Goals / Sessions | M | **next** | New (links existing ids) | Confirmed |
| Manual audio upload + sync | S/M | **next** | Extend nade-video route | Confirmed |
| Playbooks + adherence | L/XL | **later** | New (depends on map zones) | Confirmed/Inferred |
| In-demo voice extraction (opt-in) | L | **later** | New (OSS CLI + libopus) | Confirmed |
| Speech-to-text call log | XL | **later** | New (local Whisper) | Inferred |

---

## 5. Sources
- Leetify — https://leetify.com/ , blog https://leetify.com/blog/ , improvement guide https://leetify.com/blog/leetify-guide-how-to-start-improving-in-csgo/
- SCOPE.GG — https://scope.gg/ , dashboard https://scope.gg/cs2-dashboard/ , prematch guide https://scope.gg/guides/prematch_scopegg_en/
- Skybox EDGE — https://skybox.gg/edge/
- CS2.CAM — https://cs2.cam/ , wiki https://cs2.cam/en/wiki/introduction
- Refrag — https://refrag.gg/ , Coach announcement https://refrag.gg/blog/announcing-refrag-coach-the-easiest-way-to-track-analyze-and-practice-for-cs2/ , routines https://wiki.refrag.gg/en/routines
- Yprac — https://yprac.com/ , practice maps https://yprac.com/aim-training-cs2-practice-maps
- PRACC — https://pracc.com/counter-strike , overview https://blog.pracc.com/a-general-overview-for-pracc-com/
- Demo-review workflow — boosteria https://boosteria.org/guides/cs2-demo-review-guide-pros-look-improve-fast , CSDB https://csdb.gg/guides/demo-guide/ , EGW "Art of Demo Review" https://egw.news/gaming/news/26282/the-art-of-demo-review-how-cs2-teams-study-their-o-LgEbAX8dv , wecoach https://wecoach.gg/blog/article/how-to-coach-cs2 , scrim culture https://community.skin.club/en/news/scrim-culture-in-counter-strike-how-pro-teams-improve-faster-by-golden
- Voice extraction — akiver/csgo-voice-extractor https://github.com/akiver/csgo-voice-extractor , DandrewsDev/CS2VoiceData https://github.com/DandrewsDev/CS2VoiceData , CS2-GOTV-Discord https://github.com/K4ryuu/CS2-GOTV-Discord , demo voice playback guide https://hellcase.com/blog/guides/how-to-watch-cs2-demos/

*Confidence convention: "Confirmed" = directly stated on a public page I fetched/searched; "Inferred" = behind login / thin public detail / my synthesis for this app's specifics. Public sources only; no proprietary schemas copied — all data models are original and match this app's existing JSON-on-disk style.*
