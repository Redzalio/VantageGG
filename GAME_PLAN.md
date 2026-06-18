# CS2 Demo Player — Game Plan & Roadmap

*Owner: Zalio · Created 2026-06-15 · The canonical plan. We execute this step-by-step.*

---

## 0. Vision

**Primary goal (now):** the best learning tool for my CS2 team — a 5-stack that reviews demos together and improves; works solo and when we fill with randoms. Runs **locally / one host on a LAN/VPN**.

**Possible later (NOT now):** release it to other people as a hosted website. We are **not** building auth / multi-user / hosting infra yet. We keep the code *releasable* (quality bar in §2) so that door stays open, but the roadmap focus is the learning tool — **the release/website layer (Milestone 4–5) is deferred** until we decide to go full website.

**North Star — the learning loop** (from the research synthesis):
> insights → bookmarks → review playlist → **match report names the 3 fixes** → fixes become measurable **goals** → next demos are **graded** against them → repeated mistakes surface → durable agreements become **playbooks** the app checks adherence to.

We've built the **back half** of this loop. The plan below builds the **front half** (the hinge + review surface), the **depth** (mechanics, team intelligence, playbook), and the **release layer** (auth, polish, docs, scale).

**Generic by design (the core rule):** the app works for **anyone who drops in demo files** — no team/roster setup, not configured around any specific players. Every demo's analysis comes from the demo itself. "Your side" is a **per-demo pick** (auto-defaulted, flippable). Cross-match features track players **by steamid** = "the players in your own uploaded library," so a 5-stack's shared improvement emerges naturally (your teammates recur across your uploads) and randoms are handled because nothing assumes a fixed roster. Any saved team config (names/roles) stays **strictly optional**, never required. (Task #38 — built/verified first.)

---

## 1. Where it stands (already shipped)

- 2D + 3D replay; real 3D geometry for 9 maps (anubis, dust2, train, mirage, vertigo, nuke, overpass, ancient, inferno).
- Analytics engine: per-player stats, HLTV-2.0-equiv + transparent Context Rating, impact/round-swing, roles (basic), insight feed w/ evidence + confidence, team coaching, buy/side splits, round cards, mistake detectors.
- **Practice Goals** — persistent, match-aware: 15 metrics, scope by map/player/**side/role/buy**, verdicts (fixed/improving/still-happening/insufficient), rolling 3/5/10 windows, notes/status, insight→goal hook.
- **Recurring-mistake detection** across matches (per player) with one-click "+ Goal".
- Cross-match **Trends** + team config; **Bookmarks + auto review queues**.
- Nade library (CSNADES import, demo throws, off-book tagging, videos, 3D lineups) + utility search now **side-coloured + CT/T filterable**.
- Recent fixes: last-round viewable, real-time bullet impacts, minimap utility+size, smokes occluded by walls, 2D trajectory hover→POV, review/util panel scroll.
- 98 Python tests passing. JSON-on-disk persistence (atomic, stdlib).

---

## 2. The quality bar ("overall great", because we're releasing)

Applied to **every** task from here, not a separate phase:
- Verify in the browser (preview tools); **zero console errors** before "done".
- Tests where logic is non-trivial (Python now; **add a JS smoke harness — #54**).
- Real **empty / error / loading** states — no dead ends for a new user.
- Performance budget respected (no frame drops, big-demo safe).
- After a parse-output change: **bump SCHEMA_VERSION** so caches auto-refresh (#55).
- Each coaching claim shows **what happened + why + exact round/tick + confidence + observed-vs-inferred**.
- Current UI target is **1920x1080 desktop review**. 1280x720 and mobile/tablet responsive polish are deferred until the app is closer to public/stranger-facing use.

---

## 3. Priorities

| Tier | Meaning | Tasks |
|---|---|---|
| **P0** | The spine — everything hangs off these | #38, #39 |
| **P1** | Core learning value (worth using *and* releasing) | #40, #42, #43, #46, #47, #48, #49, #51, #64, #65 |
| **P2** | Depth & team identity | #41, #44, #45, #50, #52 |
| **Ongoing** | Quality track — keeps it good (and releasable later) | #54, #55, #56 |
| **Deferred** | Release/website layer — only if/when we go full website (no auth/hosting now) | #53, #57, #58, #59, #60 |

---

## 4. Milestones (what each unlocks)

### Milestone 1 — "The Loop Works" (private)
*Goal: my stack can run review → fix → goal → track, end to end, in one place.*
- **#38** Generic team handling — any dropped demo, per-demo side pick, no roster config — *foundation*
- **#39** Match Report page + [Make goal]/[Build playlist]/[Watch] — *the hinge*
- **#40** Review Session playlists (auto-pause coach flow)
- **#42** Round-filter engine + Search page
- **#64** Desktop review UI polish — make the 1920x1080 review surface faster to scan
- **#65** Whole-match timeline redesign — unclump Match mode into lanes/filters/key moments
- **Done when:** after a demo, we open one page, see the 3 fixes, click into the exact rounds in a guided playlist, and turn a fix into a tracked goal — without hunting across panels or wasting desktop space.

### Milestone 2 — "The Great Coaching Tool"
*Goal: it diagnoses root causes most tools paywall, for the team and each player.*
- **#43** Spacing & trade-network ("who dies near support but untraded")
- **#46** Deep aim-mechanics (counter-strafe %, crosshair placement, time-to-damage…)
- **#47** Aim/Util/Positioning sub-ratings + bands *(needs #46)*
- **#48** Death Review 3D + "watch all my deaths"
- **#63** First-person crouch — POV camera drops to crouch eye-height when the spectated player ducks (needs a parser field → batch the re-parse with #46)
- **#62** Per-position (callout) performance breakdown — kills/deaths/K-D/opening duels per map area, by side ("strong on A, dies at Mid as T"); also unblocks location-level patterns in #44
- **#49** Multi-label role model + role-based coaching
- **#51** Coaching-ends-in-a-verb + auto weekly training plan
- **Done when:** every player gets a role-correct, mechanic-aware, position-aware verdict ("aim Good, positioning Poor, dies at Mid → here's the drill"), and the team sees its trade/spacing failures on the map.

### Milestone 3 — "Team Identity & Depth"
*Goal: turn one-off review into durable team patterns.*
- **#41** Notes & tags (round/tick/player/callout/util)
- **#44** Tendency / anti-strat / repeated-pattern detection
- **#45** Team Playbook + adherence checking
- **#50** Utility-quality signals + two-tier util rating
- **#61** Auto-detect consistent utility → promote to the nade library (your auto-built lineup book: if you throw the same smoke from the same spot to the same place repeatedly, it becomes a saved lineup — what/where-from/where-to)
- **#52** Exports / Discord-friendly shareables
- **Done when:** the app can say "we hit A at 0:18 on full-buys", "we missed jungle smoke on this exec", your repeated throws show up as saved lineups, and we can send "tonight's 5 rounds" to the group chat.

### Milestone 4 — "Release-Ready" — ⏸ DEFERRED (only if we go full website)
*Not building this now. Listed so it's ready to pick up if/when we decide to make it a public/hosted website.*
- **#53** Multi-user hosting: auth (Steam/Discord) + per-team data separation + upload ownership + delete controls + rate limits + **background parse job queue** + DB migration
- **#57** Onboarding + UX polish for strangers (the empty/error/loading-state polish that just makes it pleasant is folded into the per-feature quality bar — done as we go; the stranger-facing onboarding flow is the deferred part)
- **#58** Docs, in-app help, landing page, deploy runbook
- **#59** Performance & scale pass (concurrency) — *big-demo perf still matters now and is covered by the quality bar; the many-concurrent-users part is the deferred bit*

### Milestone 5 — "Launch & Iterate" — ⏸ DEFERRED
- **#60** Private beta → public launch checklist. Revisit only if we decide to release. Decide free vs paid then.

---

## 5. Step-by-step execution order

We go down this list. Roughly one feature per working session; big ones (#45, #53) span several.

1. **#38** Generic team handling — any dropped demo, no roster config ← *start here*
2. **#39** Match Report page (the hinge)
3. **#40** Review Session playlists
4. **#42** Round-filter engine + Search page
5. **#54** Frontend smoke-test harness *(lock in the loop before the UI rewrites land on top)*
6. **#64** Desktop review UI polish *(right sidebar/topbar/report/review-panel scan speed)*
7. **#65** Whole-match timeline redesign *(separate lanes, key-moment default, filters, tooltips, zoom/range)*
8. **#46** Deep aim-mechanics → **#47** sub-ratings + bands
9. **#43** Spacing & trade-network
10. **#48** Death Review (3D)
11. **#63** First-person crouch (POV eye-height) — *batch its re-parse with #46*
12. **#62** Per-position (callout) performance breakdown
13. **#49** Role model + role-based coaching
14. **#51** Coaching-ends-in-a-verb + training plan
15. **#50** Utility-quality signals
16. **#61** Auto-detect consistent utility → nade library (auto lineup book)
17. **#44** Tendency / anti-strat detection (uses #62's position attribution)
18. **#41** Notes & tags
19. **#45** Team Playbook + adherence *(big)*
20. **#52** Exports / shareables (Discord summary, "tonight's 5 rounds")
21. **#55** Schema/reparse/ops hardening — cheap hygiene, do alongside
22. **#56** de_cache + maps — *opportunistic, anytime you start playing a map*

**⏸ Deferred until/unless we decide to go full website:** #53 (auth/DB/job-queue/hosting), #57 (stranger onboarding), #58 (landing page), #59 (multi-user scale), #60 (public beta/launch). We keep the code releasable via the quality bar (§2), but build none of this now. For the stack today, "one host runs it on a VPN/LAN and we all connect" is the deployment — no infra needed.

*Re-orderable. The whole value (Milestones 1–3) ships without any of the deferred infra.*

---

## 6. Release-readiness checklist (gate for public)

- [ ] Auth (Steam or Discord — **decide**) + sessions
- [ ] Team membership / invite codes + per-team data separation
- [ ] Upload ownership + "delete my demo/data" path
- [ ] Background parse queue (no request-blocking) + progress UI
- [ ] DB (migrated off JSON-on-disk where concurrency matters)
- [ ] Rate limits + video-URL domain allowlist + upload size caps
- [ ] Storage cleanup policy (auto-evict old .dem/caches)
- [ ] Error monitoring + readable failed-parse logs
- [ ] Privacy note + data-handling statement
- [ ] Onboarding, empty/error states, responsive
- [ ] Docs + landing + one-command deploy
- [ ] Frontend smoke tests + Python tests green
- [ ] Load-tested at target concurrency
- [ ] Beta-tested with the 5-stack

---

## 7. Definition of "great" (acceptance criteria)

After uploading a demo, a team can clearly answer:
- What are the **3 biggest things to fix**? Which **exact rounds** prove them?
- What should **each player** do differently (role-correct)?
- Which **drills** before next match?
- Did we **improve** vs our last demos?
- Are we **following our playbook**? Which utility was **missing / late / wrong / off-book**?
- Are our **deaths tradeable**?
- Are we losing on **aim / spacing / utility / economy / decision-making**?
- What did we do **well** to repeat?

If it answers these clearly, it's a coach, not a viewer.

---

## 8. Open decisions (needed before the marked tasks)

1. **Auth provider** — Steam or Discord? (gates #53)
2. **Hosting model** — one host on a VPN for the stack (defers #53) vs hosted SaaS for the public (needs all of Milestone 4)?
3. **Free vs paid** — affects #53/#59 (limits, storage, abuse) and what "launch" means.
4. **Storage budget** — how many demos/teams to retain; drives the cleanup policy.

None of these block Milestones 1–3. We can build all the value first and decide these before Milestone 4.

---

## 9. How we work (operating notes)

- Verify every change in the browser preview; **zero console errors** before calling it done.
- Run `pytest` (98 green today) after backend changes; add JS smoke tests (#54).
- After a parse-output change: re-parse caches (`tools/reparse_all.py`) **and** bump `SCHEMA_VERSION` (#55) so they auto-refresh.
- Big features → partition by file ownership; verify via Python/preview, not the shared :8770.
- Don't delete uploads/cache/3D assets. Don't scrape csnades.gg.
- Keep `.dem` files for now (needed for in-place re-parse); revisit a delete toggle once schema stabilizes.
- The running server loads the parser at startup → new uploads need a restart to pick up a parser change (fix this in #55).

---

## 10. Added QoL / UI details

These are folded into the roadmap without replacing the original milestones.

### #64 Desktop review UI polish

Target: 1920x1080 desktop review. Do not spend current effort on 1280x720 or mobile/tablet layout unless it blocks desktop use.

- Collapse or repurpose the right sidebar. Move Controls into a Help/Shortcuts modal; make Kill Feed a compact overlay or collapsible panel.
- Group the topbar into clear areas: Data, Review, Tools, View. Hide or demote Load Sample once a real demo is loaded.
- Make Analytics wider on desktop, using the available right-side space instead of leaving a large empty area.
- Add sticky Analytics subnav: Top Fixes, Player, What to Fix, Team Review, Round Breakdown, Match Overview.
- Make Round Breakdown filtered/collapsible by default: Key rounds, Lost rounds, Full-buy losses, Player deaths, All.
- Promote Review queues into a guided Review Session UI with session types: mistakes, deaths, utility, team rounds, positives.
- Make review queue items easier to scan: queue title, moment count, Start/expand actions, clearer category icons.
- Unify replay jump buttons into one dark style with play icon + round label.
- Add action toasts: bookmark saved/deleted, goal created, lineup saved, import complete, copied summary, jumped to round.
- Add a command palette (Ctrl+K): jump to round/player, search lineups/bookmarks/queues, open latest demo, open map status, open goals/trends.
- Improve first screen for desktop return visits: recent demos, resume last review session, open unresolved goals, Upload stays primary.
- Keep modals consistent: title, close button, width, Escape-to-close, focus return.

### #65 Whole-match timeline redesign

Problem: Match mode currently draws too many markers into one thin scrubber, so it becomes a clumped barcode.

- Split Match mode into separate lanes: bookmarks/insights, kills/deaths, utility, bomb events, goals/review moments.
- Default Match mode to key moments, not all events.
- Add filters: Kills, Utility, Bomb, Insights, Bookmarks, All.
- Add hover tooltips with round, time, player, event, swing/reason, and confidence when available.
- Show round winner and buy type as a clearer background band behind the match timeline.
- Add an obvious half-swap marker.
- Add range select/zoom so a few rounds can be expanded without leaving Match mode.
- Let selected ranges become a Review Session.
- Keep All Events available, but never make it the default view.

### Added future QoL features

These sit behind the current loop unless they directly support #39/#40/#42/#64/#65.

**Already covered by committed tasks — build as acceptance details there, NOT separately:**
- Event rail / next-important-moment controls (kills, deaths, plants, utility, clutches, bookmarks, insight flags, high-swing) → part of **#65** (timeline) + a nav control.
- Share/copy outputs (Discord summary, player/team report, round list, goal progress, util mistakes, JSON bundle) → **#52** (exports).
- Multi-round pattern view (overlay filtered rounds → repeated routes/timings/executes/mistakes) → **#44** (aggregation overlay).
- Saved review sessions (date/title, demo ids, moments, notes, goals, completion) → fold into **#40** (review playlists).
- Death heatmap / danger zones (by player/side/buy/phase/traded) → **#62** (per-position) + **#43** (spacing deaths).

**Net-new — tracked as their own candidate tasks (behind the loop):**
- **#66** CS2 practice-position export (`setpos`/`setang`) — current tick / death spot / nade origin + aim.
- **#67** Manual comms/audio sync — upload audio, set offset, play while scrubbing, bookmark comms.
- **#68** Strategy-board saved drawings — arrows / numbered routes / util markers, attached to sessions/playbook.
- **#69** Local import conveniences — watch folder, batch import, per-file parse progress.
- **#70** Data-trust / parse-health panel — versions, stale state, map/rounds/tickrate, missing fields, reparse button (UI for #55).

Deferred responsive polish:
- 1280x720 scoreboard clipping.
- Mobile/tablet topbar and stage overflow.
- Mobile roster drawer and mobile-specific Help/Shortcuts behavior.

---

## Appendix — Task index

**Foundation:** #38 generic team handling (no roster config) · #39 Match Report
**Team loop:** #40 review playlists · #41 notes & tags · #42 filter+search
**Team intel:** #43 spacing/trades · #44 tendency/anti-strat · #45 playbook+adherence
**Player depth:** #46 aim-mechanics · #47 sub-ratings · #48 death review · #63 first-person crouch · #62 per-position breakdown · #49 role model · #50 util-quality · #51 coaching-to-verb
**Utility:** #61 auto-detect consistent utility → nade library (auto lineup book)
**Share:** #52 exports
**Ongoing/quality:** #54 frontend tests · #55 schema/ops · #56 de_cache/maps
**Desktop UX:** #64 right-sidebar collapse/help modal · grouped topbar · wider analytics dashboard · sticky analytics subnav · filtered/collapsible round breakdown · guided review-session queue UI · easier queue scanning · unified dark replay-jump buttons · action toasts
**Timeline UX:** #65 whole-match timeline lanes for key moments/bookmarks-insights/kills/utility/bomb · default key-moments view instead of all events · event filters · hover tooltips · clearer round winner/buy background · range select/zoom for building review sessions
**Candidates (behind the loop):** #66 setpos export · #67 comms sync · #68 strategy-board drawings · #69 import conveniences · #70 parse-health panel
**⏸ Deferred (future full-website only):** #53 hosting(auth/DB/jobs) · #57 onboarding/UX · #58 docs/landing · #59 perf/scale · #60 beta→launch
