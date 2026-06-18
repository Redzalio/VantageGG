# CS2 Demo Analytics -- Implementation Spec
*Distilled from research (Leetify glossary, HLTV rating reverse-engineering, demoparser2 docs, awpy source, pro coaching content) + a study of Skybox Edge's live UI. This is the build reference for the analytics layer. Audience: FACEIT 10 / Premier 25k+.*

## Coaching thesis (drives the whole product)
At this level **mechanics are ~comparable to pros; everything *around* the duel is not.** Grade the **context of each fight** -- right place, right time, with support + utility, and what happened after -- not the aim. Lead every report with the **avoidable-death breakdown** and the **KAST / traded-death% / utility** deltas vs pro benchmarks; that's where a 25k player has the most room.

> The killer diagnostic: for every death, classify **Mechanical / Strategic / Decision / Unavoidable** and answer **"could this death have been avoided?"** The avoidable:unavoidable ratio is itself a skill metric.

---

## 1. demoparser2 -- exact API & field names (CONFIRMED)
```python
from demoparser2 import DemoParser
p = DemoParser("match.dem")
p.parse_header()         # dict[str,str]; NO tickrate/duration key (assume 64 tick)
p.list_game_events()     # events present in THIS demo (round_start/round_end may be absent--parse directly)
p.list_updated_fields()  # tick props present in THIS demo (gate wanted_props on this)
p.parse_event(name, player=[...], other=[...])   # player=tick-props enriched per actor; keyword-only
p.parse_events([names], player=[...], other=[...])
p.parse_ticks(wanted_props, players=[...], ticks=[...])  # always returns tick, steamid(uint64), name
p.parse_grenades()       # trajectories: X,Y,Z,tick,grenade_type,thrower_steamid,entity_id
p.parse_player_info()    # steamid,name,team_number per player
```
**Event column mechanic:** actors come prefixed `user_` (victim/primary), `attacker_`, `assister_`. Add `player=["X","Y","Z","health",...]` -> `user_X`, `attacker_X`, etc. `other=` is for game-state props (unprefixed).

### Events we use + native fields
- **player_death**: `attacker_steamid, assister_steamid, weapon, headshot, penetrated, noscope, attackerblind, thrusmoke, assistedflash, hitgroup`. (No XYZ unless `player=["X","Y","Z"]`.)
- **player_hurt**: `attacker_steamid, weapon, dmg_health, dmg_armor, health(after), hitgroup`. <- **use for ADR/util-damage** (not player_death).
- **weapon_fire**: `weapon, silenced` (actor=user_). One row/shot -> spray/shots-fired.
- **player_blind**: `attacker_steamid(flasher), blind_duration`, victim=user_. <- canonical flash source (enemy + team flashes).
- **bomb_planted / bomb_defused / bomb_begindefuse**: `site`, actor=user_.
- **smokegrenade_detonate / flashbang_detonate / hegrenade_detonate / inferno_startburn**: lowercase `x,y,z,entityid`. *inferno has NO thrower* -> join via parse_grenades by entity/pos/time.
- **round_start / round_end / round_freeze_end / round_officially_ended**: parse round_start & round_end **directly** (not via batch). round_end: `winner`(2=T,3=CT), `reason`(int map below), filter rows with non-null winner.

### Tick props we use (exact strings)
`X Y Z` (UPPERCASE), `pitch yaw` (aim; no "view_angle"), `velocity velocity_X velocity_Y velocity_Z`, `health`, `armor_value`, `has_helmet has_defuser`, `balance` (current money), `current_equip_value round_start_equip_value`, `active_weapon_name active_weapon_ammo total_ammo_left`, `is_scoped zoom_lvl`, `is_walking ducking`, `flash_duration`, `is_alive`, `is_defusing`, `in_bomb_zone in_buy_zone which_bomb_zone`, `spotted approximate_spotted_by`, `accuracy_penalty aim_punch_angle shots_fired`, `team_num`(2=T,3=CT,1=spec,0=none) `team_name`, `last_place_name`(callout), `ping fov`, game-state: `game_time total_rounds_played is_freeze_period is_warmup_period is_match_started is_bomb_planted round_win_reason`.
- **Different from earlier guesses:** money=`balance`; armor=`armor_value`; spotted-by=`approximate_spotted_by`. **No `is_planting`** (use bomb events). No `total_hits/total_shots` (compute from events).

### Gotchas
1. **Cast every `*steamid` to uint64** (float loses precision).
2. Position is **uppercase X/Y/Z** on ticks; lowercase only on grenade detonate events.
3. Tickrate not in header -> **assume 64** (1 tick = 15.625 ms; sec->ticks = `round(sec*64)`). Duration = `max(tick)/64`.
4. **Filter warmup/timeouts:** keep ticks where `is_match_started` AND NOT (`is_warmup_period` or any timeout). Bucket ticks into rounds via round_start/freeze_end -> round_officially_ended.
5. round_end `reason` ints: 1 bomb detonated | 7 defused | 8 T eliminated(CT win) | 9 CT eliminated(T win) | 12 time/target saved | 19 T planted. `winner` 2=T/3=CT.
6. hitgroup: 1 head, 2 chest, 3 stomach, 4/5 arms, 6/7 legs, 8 neck.
7. **Parse once, reuse**: one parse_ticks(all props) + one parse_events(all). Sample positions every 8-16 ticks for spread/heatmaps; full res for duels.

---

## 2. Metrics & formulas (implementable)
- **ADR** = Sum `player_hurt.dmg_health` (clamp to victim HP for overkill, document) / rounds.
- **KAST%** = rounds with Kill OR Assist OR Survived OR Traded-death, / rounds.
- **KPR/DPR/APR** = kills/deaths/assists per round. **K/D**, **+/-**.
- **HS%** = headshot kills / kills. (Leetify HS-accuracy = head-hits/all-hits, AWP excluded -- separate.)
- **Impact** ~ `2.13*KPR + 0.42*APR - 0.41` (awpy-verified).
- **HLTV Rating 2.0** (regression, awpy-verified -- label "2.0-equiv", exact HLTV is private):
  `0.0073*KAST + 0.3591*KPR - 0.5329*DPR + 0.2372*Impact + 0.0032*ADR + 0.1587`
  (full precision: KAST 0.00738764, KPR 0.35912389, DPR -0.5329508, Impact 0.2372603, ADR 0.0032397, intercept 0.15872723). KAST as % value to match the fit; validate on a known match.
- **UDR (utility dmg/round)** = Sum dmg from `weapon  in  {hegrenade, inferno/molotov/incgrenade}` / rounds.
- **Opening/entry duel** = first player_death of round (by tick). Opening win% = opening_kills/(opening_kills+opening_deaths). Opening attempt% = participated/rounds. Track per side.
- **Trades (Leetify 3x2):** trade window **5 s (320t) loose, 1 s (64t) strict**, trade range <= ~600u. Trade-kill = you kill the killer of a teammate within window. Traded-death = a teammate kills your killer within window after you die. Report opportunities/attempts/successes both directions.
- **Multikills** 2K-5K = rounds with N kills by a player. **Clutches** 1vX = last alive vs X -> win%. Build alive-count timeline from player_death.
- **Flash metrics** (player_blind): enemies flashed/flash (cap 5), avg blind duration (count only `blind_duration >= 1.1s` as "real"), teammates flashed/flash, flash-assists.
- **Economy:** equip value from `current_equip_value` at freeze-end (team-average). Buy types (roundlib.classify_buy, soft thresholds on an approximate input): eco <$1000, light <$2400, force <full, full = side-aware floor **T $3900 / CT $4300** (CT needs kits + costlier util, so a lone M4+armor reads as force). Per-round buy-shape modifiers: anti-eco, **mixed** (team equip spread >=$2500), **hero** (team eco/light but a player has a >=$3000 rifle/AWP). Loss ladder 1400->1900->2400->2900->3400 (counter starts at 1/half -> pistol loser gets 1900). Win $3250 / bomb $3500 / plant +$300 / **plant-loss +$600 (CS2, was $800)** / **CT +$50 per T killed (CS2; not reconstructed per-player here)**. **Unused-util-on-death** = $ value of nades held at death (Leetify). **All verified values + sources + date in `docs/CS2_ECONOMY_REFERENCE.md`.** Limitations: low-util and no-kit-CT need a freeze-end inventory parse (not yet); bonus rounds need prior-round carryover (not yet).

### Benchmarks (flag under/over; calibrate "expected" to ingested pool)
| Metric | FACEIT-10 solid | Pro/elite |
|---|---|---|
| HLTV 2.0 | 1.00-1.10 | 1.15-1.30+ |
| ADR | 75-85 | 90+ (100+ elite) |
| KAST% | 68-72 | 70-83 |
| KPR / DPR | .65-.72 / .62-.66 | .80+ / <=.60 |
| HS% (rifle) | 45-55 | 55-65 |
| Opening win% | 50-52 | 55-65 (entries) |
| UDR | 6-8 | 8-10+ |
| Counter-strafe% | 70-80 | 85+ |
Role context matters -- grade entries on opening/trade, support/anchor on util/KAST, not raw frags. **Use Rating-3.0 logic spirit: discount eco-farm frags** (raw ADR/frags over-credit ecos = the FACEIT stat-pad habit).

### Aim diagnostics (differentiators; tick-derived)
- **Counter-strafing%** = shots fired at velocity <34% max speed (rifles) -- no visibility needed &#9989;
- **Time-to-kill** = first hurt -> death tick &#9989;. **Spray vs tap** = inter-shot gap <~0.2s &#9989;
- **Crosshair placement / Time-to-damage / reaction / spotted-accuracy** need "first saw enemy" -> use `spotted`/`approximate_spotted_by` bits or facing-cone+LOS; mark **approximate**.

---

## 3. Role inference (per side, per half; output top-2 w/ confidence)
Score each of 5 teammates on signals, z-score across team, assign max (AWPer/Entry usually distinct).
- **AWPer:** highest `active_weapon_name=="awp"` fraction; long-range kills (>1200u); isolated but stationary.
- **Entry (T):** lowest mean entry-order rank (first to contact); highest opening-duel participation; not AWP.
- **Support:** most flashes-for-teammates (player_blind enemy then teammate kills <=2s); most weapon drops (ownership transfer + balance drop); 2nd through choke.
- **Lurker (T):** highest mean dist-to-centroid & nearest-teammate (>900-1200u sustained); latest first-contact; kills off-axis from execute; backstab geometry (victim yaw faces away >110 deg); moves while isolated.
- **Anchor (CT):** lowest site-residency entropy (one site >75% live ticks); fewest rotations; low path length.
- **Rotator (CT):** high zone-crossing; arrives at resolving site after first contact.
- **IGL:** low-confidence from demo (no voice) -- consistent "safe" spot + throws execute-trigger util; flag for user confirmation.

Skybox taxonomy to mirror: **CT = Allround / Anchor / Rotation; T = Allround / Lurk / Half-Lurk / Pack.** Plus position labels from `last_place_name` (e.g. "B Anchor", "T Mid", "A Lurk").

---

## 4. Mistake detectors (each emits {round, tick, severity, evidence} -> deep-link viewer)
Calibrate **both** absolute thresholds AND intra-team z-score ("worst on your team at X").
1. **Untraded opening death** -- opening duel victim not traded <=1s strict/5s loose; check teammate <=600u at death (no one in range = isolation Sec 9, not trade-effort).
2. **Dry peek** -- initiated duel with no friendly flash on enemy in prior 2s AND held util >=200 AND no teammate <=600u. Report (a) process flag even if won, (b) dry death untraded.
3. **Bad spacing/clump** -- >=2 teammates <=250-300u; outcome-confirmed = one enemy 2+ kills in 3s on victims <300u apart, or one HE/molly hits >=2 teammates.
4. **Util wasted** -- unused-util-on-death (avg >$300/death, esp lost rounds/support); early empty util (detonate before freeze+15s, no enemy <=800u); panic util (thrown <=1s after taking damage, no follow-up).
5. **No flash before duel / team-flash** -- % duels initiated dry; team-flash via player_blind same-side, `blind_duration>=1.1s`, worse if flashed teammate dies <=3s; self-flash.
6. **Late/over-rotation (CT)** -- per-map zone polygons; late if arrive >5s after contact & site lost; died-mid-rotation (killed in connector); over-rotation (majority rotate to non-resolving site); info-less rotation (no damage/bomb info in 3s prior).
7. **Bad save** -- died holding rifle/AWP in lost round already lost (numbers down post-plant), esp far from objective; over-save (disengaged a winnable retake).
8. **Predictability** -- DBSCAN freeze-end & death positions per side+buytype (eps ~150u); >=40% same spot; timing variance <2s; >=2 deaths <=200u apart ("died here twice").
9. **Isolated/out-of-position death** -- nearest-teammate >900-1200u at death AND not the designated lurker/AWP; over-push-after-frag (kill then move from centroid, die <=5s untraded).
10. **Low conversion / AWP waste** -- opening-kill->round-loss rate; trade opp/att/succ; AWP death with 0 shots fired that duel; AWP no-reposition (killed <=3s within 150u of own shot).
11. **Economy** -- buy classification both teams; force-into-full / double-force spiral; missed drop (surplus >$2700 while teammate on pistol); hoarding.

### Constants
1u=1inch=2.54cm; 64 tick=15.625ms; trade 5s loose/1s strict, range <=600u; clump <=300u; isolation >900-1200u; half-blind <1.1s; useful-smoke <=800u to enemy; counter-strafe <34% max speed; full-buy ~$4.5-5k/player. Facing vector `(cos p*cos y, cos p*sin y, sin p)` (verify handedness on a clip); killed-from-behind = angle(victim_facing, victim->attacker) >110 deg.
**Visibility/LOS** is the hard dependency: tier 1 proximity+facing cone (cheap), tier 2 facing-cone+range, tier 3 raycast vs map mesh. Start tier 1-2; flag LOS-dependent metrics "approximate."

---

## 5. Skybox Edge feature parity (studied in-browser) + our differentiators
**Match Overview:** per-team scoreboard = HLTV Rating, K/A/D, KD Diff, K/D, ADR, KAST, **UDR**, **Opening Duels (W:L +/-)**, dual role labels, side filter (Both/T/CT), round-by-round survivors strip (skull=elim/wrench=defuse/bomb).
**2D Replayer:** dots+names+facing, **per-player econ/loadout panel** (money/weapons/armor/kit), round nav + multi-round, timeline event markers, **killfeed, top scoreline, level-split (multi-level maps), freezetime toggle, flashed + damage-received FX, grenade trajectories, bullet traces, held-utility, dot size, draw/telestrator tool, pan/zoom**.
**Player Stats:** aggregate metrics each w/ **"Comp." benchmark**, role ID (best map+position w/ rating), buy-type perf (pistol/full/force), T/CT splits, trend sparklines, **11-axis role radar** (HLTV, ADR, Opening attempts, Opening KD, Trade kills/rd, KPR, DPR, Traded deaths/rd, UDR, Flash assists, KAST).
**Our differentiators (Skybox lacks):** **2D->3D fly-around** + an **auto "what-you-did-wrong" insight engine** (Leetify-style, with replay deep-links) -- and it's **free + offline**.

OSS blueprints: cs-demo-manager (akiver), cs2-meta-engine (Twoos123). Metric spec: Leetify glossary. awpy implements adr/kast/rating/impact/calculate_trades on demoparser2 -> cross-check.

---

## 5b. Leetify app model (studied in-browser -- the coaching-UX target)
- **Dashboard -> Focus Areas**: your top weaknesses as scored cards (e.g. Accuracy 28 "Subpar" -> Goal 36; Trade Fragging 42; HE Usage 42) each with plain-English coaching + a **skill radar** (Aim / Utility / Positioning) overlaying **YOU vs GOAL vs FACEIT 10**. Ratings are 0-100 sub-scores with labels Subpar/Average/Good/Great.
- **Match -> Your Match**: a **Match Identity** title ("The Utility Lover") + **Top-5 stats** (e.g. Enemies Flashed 40 TEAM BEST), and **This-Match vs Last-30** with **percentile** framing ("utility better than 77% of players"). Headline tiles: Leetify Rating, Aim, Utility, Trade Kill Opps, Kills, ADR, Opening Duel Attempts, Clutch Kills.
- **Match -> Rating Breakdown** (THE "what you did wrong" centerpiece): decompose the player's rating into **9 categories**, each + or -:
  **Opening Duels, Damage Assists, Saving, Traded, Flash Assists, Retakes (CT post-plant), Afterplants (T post-plant), Clutches, Mid-round K/D** (fights after the opening duel, before the plant). The most-negative category = the focus. Then **per-round**: side, WON/LOST, round rating + label (SUBPAR/AVERAGE/GOOD/GREAT) + the category contributions + a **"Watch in 2D Replay"** deep-link. (Mirror this: per-round rating-delta by category, biggest leak surfaced, click->jump replay.)
- **Match -> Map Zones**: zone K/D heatmap (green above benchmark / red below), filters **Upper/Lower | Overall/T/CT | Overall/Pre-plant/Post-plant | Me/Team | buy-type**; click a zone -> kill/death dots; % per zone = how often you play it. Build zones from **`last_place_name`** callouts (no manual polygons needed for v1).
- Also: Sessions, Maps, Aim (per-weapon + reaction/preaim), Utility, Leaderboards, "Ask AI", auto-Highlights per round. Pricing pressure = Pro gates much of it -> our free offline version is the play.

Our engine = Leetify's Rating-Breakdown decomposition + Focus Areas + Map Zones + percentile-vs-benchmark, PLUS Skybox's role radar, PLUS our 2D->3D + offline + free.

## 6. Build plan
1. **Parser v2** (`parser.py`): add player_hurt, weapon_fire, player_blind, grenades, bomb, economy ticks (balance/equip), spotted, last_place_name; emit per-round + per-player raw data. Validate field names on a real demo via `--probe`.
2. **Analytics engine** (`analytics.py`): rounds -> metrics (Sec 2), role inference (Sec 3), mistake detectors (Sec 4), benchmarks; output JSON (overview + per-player + insights[] each with round/tick deep-link).
3. **UI**: Overview tab (scoreboard + survivors strip), Player panel (radar + Comp benchmarks + buy-types + side splits), **Insights feed** ("what you did wrong" cards -> click -> jump replay to tick), heatmaps.
4. **Replayer upgrades** from Sec 5 (econ panel, nade trajectories, bullet traces, timeline events, draw tool, level-split, freezetime).
5. **2D->3D** (`view3d.js`, Three.js): click map -> 3D fly-around over textured ground + players in true 3D (Z); **de_train first** (its upper/lower levels showcase Z). True wall geometry not feasible offline -- document.
