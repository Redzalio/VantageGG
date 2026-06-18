# B2 — Roles, Positions & Player Archetypes (deep)

**Research agent B2 · CS2DemoPlayer · 2026-06-15**
Workstream: convert higher-level CS2 role theory into **app-detectable, fluid, multi-label** role inference + role-aware coaching, replacing the current rigid one-role-per-side heuristic.

> Confidence tags: **Confirmed** = stated in cited public sources and/or directly computable from the parsed fields. **Inferred** = my synthesis / proposed threshold not lifted from a single source (tune on real demos). Difficulty: S/M/L/XL. Priority: now / next / later.

---

## 0) PROPOSED ROLE MODEL — BUILD-NOW SIGNALS (shortlist)

The single biggest upgrade: **stop assigning exactly one role per player per side.** Roles are tendencies measured over a sample of rounds, they overlap, and they shift by round context. Model a player as a **vector of role-affinity scores per side**, surface 1–3 labels with confidence, and tag *behaviour by phase* rather than forcing musical-chairs.

These are the highest-value, lowest-risk signals to build first. All are computable from already-parsed data (positions/velocity, kills w/ attacker+victim+assister+tick, grenade detonations w/ thrower+xyz, bomb events, `last_place_name`, money/inventory). Each maps to a role-affinity, not a hard class.

| # | Signal (measurable definition) | Feeds role(s) | Replaces / fixes | Diff | Prio | C/I |
|---|---|---|---|---|---|---|
| S1 | **Opening-duel involvement rate** = (open_k + open_d) / rounds_alive_on_side. Already have open_k/open_d. | Entry (T), AWP-aggro, Anchor (CT, as the one *taking* first contact on defence) | Current uses raw `open_k+open_d` max as "Entry" — normalize per round & per side | S | now | Confirmed |
| S2 | **First-contact order within team** = rank of player's first-engagement tick vs teammates', per round, averaged. Lowest mean rank = goes in first. | Entry (T), Anchor/first-peeker (CT) | New — distinguishes "in first" from "fragged a lot" | M | now | Confirmed |
| S3 | **Teammate distance at first contact** (median nearest-teammate XY at the tick of player's first duel). Far = isolated-by-design (lurk/AWP); near = paired (entry/support/trade). | Lurker vs Entry/Support separation | New — current `cdist` is whole-round avg, not contact-time | M | now | Confirmed |
| S4 | **Traded-death rate** = your deaths that a teammate avenged ≤ ~3s & ~500u / your deaths. + **trade-opportunity conversion** (how often you refrag a teammate's killer when you had the chance). | Entry quality (high traded-death = "dying useful"), Support/refragger, Trade fragger | New — turns "open_d" from a negative into a *role fit* judged on its own yardstick | M | now | Confirmed |
| S5 | **Support-flash timing** = flashes you threw where an enemy was blinded ≥1.1s AND a teammate took a duel within ~2s after. Per round. | Support (as a TASK), entry-enabler | Current only counts `enemy_flashed` total; add the *before-a-teammate's-contact* window | M | now | Confirmed |
| S6 | **Pre-execute util** = smokes+molotovs you detonated in the 5s before your team's first site contact / map-control push. | Support, IGL-support, exec-caller | New | M | next | Confirmed |
| S7 | **AWP profile** = fraction of alive-ticks holding AWP + AWP opening kills + AWP deaths-while-holding. | AWPer (and whether aggro vs passive via where the AWP duels happen) | Current uses awp-frac>0.25 then steals one slot; make it multi-label & keep it even if they also entry/anchor | S | now | Confirmed |
| S8 | **CT hold-vs-rotate split** = per round, classify each CT as *held* one zone (low XY displacement, stayed near their T1 site) vs *rotated* (crossed to the other half before/at first contact). Aggregate → anchor% vs rotator%. | Anchor vs Rotator (the key CT fix) | Current uses whole-match avg `move` min/max — misclassifies; this is per-round and zone-aware | M | now | Confirmed |
| S9 | **T late-lurk / flank pressure** = alive past first contact + far from team centroid + map-zone on the *opposite* half from where the team's bomb-carrier/mass is, in 20–45s window. | Lurker, late closer | Sharpens current `cdist`-max lurker | M | next | Confirmed |
| S10 | **Bomb relationship** = carry time, plant rate, and median distance-to-bomb post-plant. | Entry/space-taker (often carries), post-plant anchor, lurker (rarely carries) | New | S | next | Confirmed |
| S11 | **Clutch/last-alive** = times you were the last alive on your side (1vX), and conversion by X. | Closer/clutcher, Lurker (overlaps), Anchor (CT retake closer) | New — judged on its OWN benchmark (1v1≈elite 70%+, 1v2≈20-35%, 1v3<15%) | M | next | Confirmed |
| S12 | **Isolated-by-design vs by-mistake** = combine S3+S9+S4: far-from-team death is *by design* if (role-affinity = lurk/AWP) OR (it was traded / it cut a rotation / it happened in a save). Otherwise flag as positioning mistake. | Coaching gate (don't scold a lurker for lurking) | New — fixes the current blanket "died isolated" insight punishing lurkers | L | next | Inferred |

**Fluidity rules baked in (the non-negotiables):**
1. **Multi-label per side.** Output up to 3 roles with weights (e.g., CT: `AWP 0.6 / Rotator 0.5 / Closer 0.3`). Never force one.
2. **Per-phase tags, not just per-match.** Tag each round's behaviour (opening / mid / post-plant / retake / clutch) and let "all-rounder/flex" be **earned** = the player's modal role *changes* across phases/economies with positive impact, NOT a fallback when unsure.
3. **Confidence is mandatory.** Low sample or flat distribution → `low` confidence, and the UI says "tendency" not "role".
4. **Side-split always.** A player can be CT-anchor + T-entry. Store `roles.t[]` and `roles.ct[]` separately, plus a combined "playstyle" summary.
5. **"Uncertain" is a real output**, distinct from "flex." If signals are flat and sample is thin → label `Undetermined (low data)`, never default to flex/all-rounder.

---

## 1) ROLE RESEARCH — each role's job, how it varies, how analysts ID it

Sources are pooled at the bottom. All roles below are **Confirmed** as roles; per-side/phase nuance and measurement mapping noted inline.

### 1.1 Entry fragger (primarily T)
- **Job:** First man into contested space / onto the site; clear the first angle, kill (or at least damage/displace) the CT anchor, *and get traded.* The canonical success statement: "if you can path into a bombsite, kill the Anchor, and then get traded, you've done your job." So an entry's death is not a failure if it was useful.
- **Side variance:** A T-side concept. On CT there is no true "entry"; the closest analogue is the aggressive first-peeker / info-aggressor who takes the opening duel for map control (e.g., mid control). Often the entry is also the team's star rifler (fight-heavy overlap).
- **Phase variance:** Most active on **executes** and **defaults that turn into a hit**. On full saves/ecos the entry role evaporates (everyone lurks/stacks for a pick).
- **Analyst ID:** High opening-duel involvement, goes in *first* (first-contact order), high opening *attempts* (win or lose), **high traded-death rate** (dies but team trades), close to a teammate at contact (paired, not isolated). → S1, S2, S4, S3.

### 1.2 Second entry / refragger / trade fragger
- **Job:** The player right behind the entry, using the entry's info to **trade the first kill** or take the next piece of space if the entry survived. "Supports the main entry from behind… often trades the first kill."
- **Side variance:** Mostly T (paired with entry on executes); on CT it's the rotator/teammate who refrags the anchor's killer.
- **Analyst ID:** High **trade-opportunity conversion** (refrags the teammate's killer when given the chance), second-in first-contact order, tight teammate distance, kills shortly after a teammate's death/contact. → S4, S2, S3.

### 1.3 Support (a TASK more than a fixed person)
- **Job:** Utility for the team — flashes for the entry, exec/default smokes & molotovs, lineup knowledge — and being the **second man in a duel to pick up the trade**. Frequently described as "unsung hero / glue." **Crucially, sources stress it's often picked up by a player who also has another role (IGL or AWPer), and that every player throws support util** — i.e., support is a *task distributed across the team*, not one permanent class.
- **Side variance:** Both sides. T: enabling util before/at exec. CT: defensive util (smokes to deny pushes, molotovs to delay) + retake flashes.
- **Phase variance:** Heaviest at **exec** (enabling util) and **retake** (clearing util). On defaults, support = map-control smokes.
- **Analyst ID:** **Support-flash count where a teammate then takes a duel** (S5), pre-exec smokes/mollys (S6), flash assists (enemy blinded ≥1.1s then dies — Leetify's exact rule), high trade-pickup, often *lower* personal opening involvement. → S5, S6, S4. Treat as a **per-round task score**, attributable to several players.

### 1.4 AWPer
- **Job:** Wields the AWP; either **passive** (hold long angles, catch rotates, anchor a site) or **aggressive** (take map-control picks, aggressive opening shots). Team economy is built around getting this player the AWP.
- **Side variance:** T-AWP = opening pick on a long angle then often falls back / lurks; CT-AWP = holds a long sightline (e.g., mid) and is frequently *also* the site anchor or the rotator who carries the AWP across.
- **Modern nuance:** Pure passive AWPers are being phased out; **hybrid AWPers (ZywOo, m0NESY) rifle when needed** — so AWP must be a *multi-label affinity*, not a mutually-exclusive class.
- **Analyst ID:** Fraction of alive-time holding AWP; AWP opening kills vs AWP deaths; *where* the AWP duels occur (forward = aggro, deep = passive). → S7, plus S1/S8 to detect aggro-AWP-entry vs passive-AWP-anchor.

### 1.5 Lurker (primarily T)
- **Job:** Play the part of the map the team is **not** hitting; cut rotations, hold the flank, take isolated picks, and **close out late round / post-plants.** Needs patience and timing ("when to pop, when to pull the trigger"). A bad lurker "looks like they're baiting, saving a lot, and finding no impact" — so the role must be judged on *late-round impact and rotation-cutting*, not raw frags.
- **Side variance:** A T-side role. CT "lurk" ≈ an off-angle/aggressive info player, rarer.
- **Phase variance:** Defined by the **mid-to-late** window: alive past first contact, separated from the pack, on the opposite half.
- **Map variance:** Big maps with isolated lanes favour lurkers (Inferno apps/banana, Mirage B/palace, mid lanes). Lurk path = opposite site / connector / flank routes.
- **Analyst ID:** Alive past first contact + far from centroid (at contact, S3) + zone on opposite half from bomb-mass (S9) + **flank kills / kills on rotating enemies** (victim was moving toward the other site) + frequent last-alive (S11) + carries bomb rarely (S10). → S3, S9, S11, S10.

### 1.6 Anchor (CT)
- **Job:** The "arch-enemy of the entry." **Hold one bombsite**, throw defensive util, delay, **stay alive long enough for rotations**, fall back rather than die on a static angle, and be **tradeable**. "Ten seconds of staying alive lets the rest of your team win the round." Often it only takes "his one kill" to define the round.
- **Side variance:** Pure CT role.
- **Phase/setup variance:** In a 2-1-2 / 1-3-1 etc., 1–2 players anchor a site while others rotate. The anchor is the one who *eats the execute*.
- **Analyst ID (the key fix):** **Stayed in/near one site** (low cross-map displacement), **took early first contact on defence** (CT opening involvement S1 on their site), threw defensive util at chokes, **is tradeable / dies after delaying** (so for anchors a traded death is GOOD), retake/save behaviour when their site isn't hit. Distinguish from rotator by **per-round hold-vs-rotate classification (S8)**, not whole-match average movement. → S8, S1, S5(def-util), S4.

### 1.7 Rotator (CT)
- **Job:** The reinforcement. "Anchor is tradeable; **rotator stays alive and arrives with impact.**" First to leave their area when the other site is pressured/executed; needs timing & info. Creates crossfires on arrival.
- **Side variance:** Pure CT role; complements the anchor.
- **Analyst ID:** **Crosses to the other half before/at first contact (S8)**, **higher survival**, kills *after arriving* at a site that's under pressure (kill location ≠ their start zone), rotates on triggers (bomb spotted / multiple enemies). Higher movement is a hint but **must be per-round + zone-aware** (current global-avg `move` misclassifies a rotator who happened to die early). → S8 (rotate%), survival, kill-zone vs start-zone.

### 1.8 Star rifler / all-rounder / flex
- **Job (star rifler):** Highest mechanical impact, gets premium positions both sides, dictates rounds; often *also* the entry. (NiKo, donk, ZywOo.)
- **Job (flex/all-rounder) — define carefully:** A player whose **role usefully changes by round context** — entry on one round, lurk the next, support on a save, AWP on a buy. Modern CS is trending toward flex; rigid specialists get exploited ("pure AWPers get isolated and rushed; dedicated supports become easy targets"). Sources explicitly: "roles are not rules… players swap roles dependent on game conditions"; primary + secondary roles; "every player expected to understand the basics of every role."
- **CRITICAL for this app:** Flex/all-rounder must be an **earned, positive** label = *measured role variance across phases/economies WITH impact*, NOT the bucket we drop a player in when detection is uncertain. Uncertain → `Undetermined (low data)`.
- **Analyst ID:** Compute role-affinity vector **per phase & per economy bucket**; flag flex when the modal role differs across ≥2 contexts AND the player's rating/impact is ≥ lobby-average in those contexts (so it's "flexes usefully," not "wanders aimlessly"). → entropy/spread of the affinity vector + impact gate. (Inferred thresholds.)

### 1.9 IGL (in-game leader)
- **Job:** Strategy, defaults, mid-round calls, economy, post-plant positioning, morale. "Loudest voice." Stats often suffer because cognition is spent leading; "a shooting captain is a rare sight." Usually *also* plays support or a safer rifler/lurk slot.
- **Analyst ID (hard from a demo — mostly Inferred):** No direct "calling" signal in parsed data. Weak proxies: **lower opening involvement + lower frag share but central/safe positioning + decent KAST + frequently the one who calls util** (high pre-exec smoke usage), and **survival-skewed** play. Honest stance: **label IGL `low`-confidence at best, or omit**, unless the user tags it. Don't over-claim.

### 1.10 Closer / late-round / clutcher
- **Job:** Wins 1vX and tight late rounds; patience, composure, info, positioning. Overlaps lurker (T) and retake-anchor (CT).
- **Benchmarks (Confirmed, public):** Most pros ~**20–25% in 1v2, <10% in 1v3+**; elite ~double (device 36% 1v2 / 17% 1v3; NiKo 34%/—; m0NESY 33%/14%). Best 1v1 closers win **70%+**. Use these as the **closer-specific yardstick** — never judge a closer by ADR.
- **Analyst ID:** Last-alive frequency (S11), clutch attempts & wins by X, time survived in clutches, save discipline when clutch is unwinnable. → S11, S10, save rate.

### Cross-cutting variance (how ALL roles shift)
- **T vs CT:** entry/lurker/refragger are T-centric; anchor/rotator are CT-centric; AWP/support/IGL/flex span both. Always side-split.
- **Map & bombsite:** lurk lanes, anchor difficulty, AWP angles, default setups all differ by map and which site. Use `last_place_name` zones to make signals map-aware (which half / which site).
- **Economy:** on eco/force, roles collapse (everyone stacks/lurks for picks or saves); only judge entry/support/anchor on **buy rounds**. (xref context_rating eco_factor already in the app.)
- **Default vs execute vs retake vs post-plant:** support util peaks at exec & retake; lurker/closer defined late; anchor defined at the execute it eats; entry defined at the hit. → phase tagging (S-phase) is essential.
- **Structured team vs pug:** roles are crisp & assigned in scrims/pro; **muddy in matchmaking/pug** (people freelance). The model must degrade gracefully → lower confidence, lean on per-round phase tags rather than asserting a season-long role.

---

## 2) PROPOSED ROLE-DETECTION MODEL FOR THIS APP

### 2.1 Data flow (fits existing `_attach_roles_util` / `analyze()`)
Reuse `replay.frames` (sampled positions w/ weapon+alive+team), `replay.grenades` (thrower+type+xyz+arc), kills `D` (atk/vic/ass/hs/wpn/tick), bomb events, `last_place_name`, money/inventory, and the already-derived per-player fields (`open_k`,`open_d`,`traded_pct`,`enemy_flashed`,`kast`,`adr`,`swing`). No new parse pass required for S1–S8, S10, S11. S9/S12 want zone/half lookups (derive a per-map "which half/site" from `last_place_name` + radar coords — small map metadata file, xref existing radar assets).

### 2.2 Per-round phase segmentation (new, enables everything)
For each round derive phase boundaries from data already present:
- `opening` = round start → first kill tick (have first_kill_t already).
- `mid` = first kill → bomb plant (or round end if no plant).
- `post_plant` = plant → end (have plant_t).
- `retake` (CT) = CT alive & bomb planted & CTs moving toward bomb.
- `clutch` = from the tick a side drops to 1 alive (compute from per-frame alive counts).
Tag every kill/death/util/movement sample with its phase. **(Diff M, prio now, Confirmed-computable.)**

### 2.3 Signal → role-affinity mapping (multi-label, weighted)
Compute each signal as a **per-side z-score within the lobby** (same lobby-relative philosophy as context_rating.py). Then accumulate weighted contributions into a role-affinity vector per side. Illustrative weights (Inferred — tune on demos):

**T side**
- Entry ← +S1(opening involve) +S2(goes-in-first) +S4(traded-death rate) +near-teammate@contact(S3 low). −far@contact.
- Refragger/Trade ← +S4 trade-opp conversion +S2(2nd in) +near@contact.
- Support(task score) ← +S5(enabling flashes) +S6(pre-exec util) +flash-assists; can be high for multiple players.
- Lurker ← +S3(far@contact) +S9(opposite-half late) +flank/rotation-cut kills +S11(last-alive) −bomb-carry(S10).
- AWP ← +S7(awp hold/kills). Aggro-AWP if S7 high AND S1 high & forward duels; passive otherwise.
- Star/Flex ← high impact (rating ≥ lobby avg) AND affinity entropy high across phases (earned flex).

**CT side**
- Anchor ← +S8(hold% high) +S1(took first contact on own site) +S5(def util) + tradeable(S4 traded-death good). 
- Rotator ← +S8(rotate% high) +survival +kill-zone≠start-zone +arrives-under-pressure.
- AWP ← +S7 (often overlaps anchor or rotator — keep as separate label).
- Support(task) ← +S5/S6 def util + retake flashes.
- Closer ← +S11 clutch freq & conversion (own benchmark) + retake closes.
- Flex ← affinity entropy high + impact gate.

### 2.4 Output schema (replace single `ct_role`/`t_role` strings)
```jsonc
{
  "steamid": "765...",
  "roles": {
    "t":  [ {"role":"Entry",  "score":0.81, "confidence":"high"},
            {"role":"Support","score":0.42, "confidence":"med"} ],
    "ct": [ {"role":"AWP",     "score":0.66, "confidence":"high"},
            {"role":"Rotator", "score":0.51, "confidence":"med"},
            {"role":"Closer",  "score":0.28, "confidence":"low"} ],
    "flex": {"is_flex": true, "evidence":"entry on buys, lurk on saves, +impact"},
    "summary": "T entry / CT aggressive AWP who rotates"
  },
  "role_signals": { "open_involve":1.3, "traded_death_rate":0.62, "hold_pct":0.2, "rotate_pct":0.8,
                    "support_flash_pr":0.4, "awp_hold_frac":0.55, "clutch": {"1v1":[3,2],"1v2":[2,0]} },
  "phase_roles": { "opening":"Entry", "mid":"Trade", "post_plant":"Closer" }
}
```
Backward-compat: keep `t_role`/`ct_role` = the top label string so existing UI/coaching keeps working while the richer object rolls out. **(Diff M.)**

### 2.5 Confidence labels (criteria)
- **high:** ≥ ~12 rounds played that side **and** top affinity ≥ 1.0 z above the player's own 2nd role **and** the defining signal is itself ≥ ~1σ above lobby mean. (Clear, well-sampled.)
- **med:** ≥ 8 rounds, top affinity leads 2nd by ≥ ~0.5σ, or signal moderately above lobby.
- **low:** < 8 rounds that side, OR affinities within ~0.5σ of each other (flat), OR defining signal near lobby mean. UI must render low as *"tendency"* with a hover explaining thin data.
- **Undetermined (low data):** < ~5 rounds that side or no usable frames — explicitly NOT "flex." (Inferred thresholds; expose as constants to tune.)

### 2.6 Why this beats the current detector (explicit)
1. Current code does **forced argmax musical-chairs** — exactly one Entry/Lurker/Support/Anchor/Rotator per side even when nobody fits; mine is **multi-label + can leave a role empty**.
2. Current uses **whole-match averages** (`cdist`, `move`) → a rotator who dies early reads as an anchor; mine uses **per-round, phase- & zone-aware** classification (S8).
3. Current treats an opening **death as just `open_d`**; mine reads **traded-death rate** so a useful entry/anchor death is scored as *role success*, not a mistake (also fixes the blanket "died isolated" insight via S12).
4. Current has **no confidence and no economy/phase context**; mine attaches confidence, restricts buy-only judgements, and tags behaviour per phase.
5. **Flex is earned**, not a fallback; **"Undetermined"** exists for thin data. Honest about IGL being mostly undetectable from demos.

---

## 3) ROLE-SPECIFIC COACHING CARDS + BENCHMARKS

> Each role judged on its OWN yardstick. Benchmarks: **Confirmed** ones are cited; ranges marked *(Inferred)* are reasonable targets to tune. Pro reference points: KAST strong >72%, elite >75%; ADR good >80, elite >90; rating 1.10 strong, 1.20 elite. Clutch: 1v1 elite 70%+, 1v2 ~20–35%, 1v3 <15%.

### Entry (T) — Diff M, prio now, Confirmed
- **Measure on:** opening-duel win% on buys, traded-death% (want HIGH), space gained (teammates' survival into mid after your contact), first-contact order.
- **Benchmarks:** opening-duel win ~**50%+** is solid for a dedicated entry *(Inferred)*; **traded-death ≥ ~50–60%** = dying useful *(Inferred)*; you SHOULD have a low survival% — don't punish it.
- **Coach:** "Take the first duel with a flash or a trade behind you — your job is space + a traded death, not a high K/D." Flag dry, untraded opening deaths (S4 low + S5 no enabling flash) — already partly in the app's "dry opening death" insight; gate it to entries.

### Refragger / Trade (T) — Diff M, prio next, Confirmed
- **Measure on:** trade-opportunity conversion%, time-to-trade, distance kept to entry.
- **Benchmarks:** convert ≥ ~60% of trade chances *(Inferred)*; be within ~500u of the entry at contact.
- **Coach:** "Stay one angle behind the entry; you exist to punish the entry's killer."

### Support (task — applies to several players) — Diff M, prio now, Confirmed
- **Measure on:** enabling flashes (blind ≥1.1s then teammate duels), pre-exec smokes/mollys, flash-assist kills, low unused-util-on-death.
- **Benchmarks (Confirmed-ish):** top supports avg **>2.5s enemy flash-blind/round** and **~5–10 HE dmg/round**; Leetify counts a flash as "leading to a kill" only if the enemy was blind ≥1.1s and then died.
- **Coach:** "Throw flashes that pop as a teammate peeks, not on cooldown. Don't die with $800 of util in the bag." (Unused-util-on-death is a direct Leetify-style stat the app can compute from inventory-at-death.)

### AWPer — Diff S–M, prio now, Confirmed
- **Measure on:** AWP opening-pick rate, AWP K vs AWP deaths-while-holding, multi-kill AWP rounds, *forward vs deep* duel location for aggro/passive split.
- **Benchmarks:** positive AWP-kill : AWP-death while holding *(Inferred)*; opening-pick conversion the main lever.
- **Coach (split by style):** aggro — "you're dying with the AWP forward; have a teammate trade you or fall back after the pick"; passive — "you're holding too deep for no picks; take an aggressive opening angle early then rotate."

### Lurker (T) — Diff M, prio next, Confirmed
- **Measure on:** flank/rotation-cut kills, late-round (mid/post-plant) impact, last-alive conversion, **save discipline** — but ALSO a *baiting* guard: if far-from-team + low late impact + high saves → flag "lurking without impact."
- **Benchmarks:** ≥ ~1 rotation-cut/flank kill per few rounds *(Inferred)*; positive late-round +/-.
- **Coach (good):** "Your lurk timing cut N rotations — keep it." **(bad):** "You're isolated and saving without late impact — lurk with a purpose: cut a rotate or take the post-plant, or play closer."

### Anchor (CT) — Diff M, prio now, Confirmed
- **Measure on:** hold% of one site, **delay achieved** (time alive after first contact on your site), traded-death% (want HIGH — being tradeable is the job), 1-kill-then-trade rounds, defensive util used.
- **Benchmarks:** survive/delay long enough for a rotation; "his one kill" can be enough — **do not judge anchors by raw frags or survival alone.**
- **Coach:** "You're dying with no info / no trade — get a shoulder peek, drop one, and fall back to your second angle so the rotate arrives." (This is exactly where the current 'died isolated' insight wrongly fires — gate it OFF for anchors who got traded.)

### Rotator (CT) — Diff M, prio now, Confirmed
- **Measure on:** rotate%, survival, impact-on-arrival (kills at the pressured site after crossing), rotate timing (early vs late vs first-contact).
- **Benchmarks:** higher survival than anchors; arrive in time (kill within a few seconds of the pressured site's first contact) *(Inferred)*.
- **Coach:** "Rotate on the trigger (bomb/multiple), arrive alive with util — you reinforce, you don't trade 1-for-1 at the choke."

### Closer / Clutcher — Diff M, prio next, Confirmed benchmarks
- **Measure on:** clutch win% **by X** vs the public benchmarks; time survived; unwinnable-save discipline.
- **Benchmarks (Confirmed):** 1v1 elite **70%+**; 1v2 ~**20–35%**; 1v3 **<15%**.
- **Coach:** "You're rushing 1v2s — isolate to 1v1s, use time/util, take fights one at a time." Praise above-benchmark conversion.

### Flex / all-rounder — Diff L, prio later, Inferred
- **Measure on:** role variance across phases/economies + impact gate.
- **Coach (positive):** "You shift roles by round state and stay impactful — that's modern flex; lean into it." Never auto-assign when unsure.

### IGL — Diff L, prio later, Inferred (low confidence)
- **Measure on:** proxies only (lower frag share + survival-skew + high pre-exec util + central position). 
- **Coach:** keep generic / opt-in; don't over-claim. Offer "tag yourself as IGL" so coaching adapts (expect lower personal stats, weight team-impact).

---

## 4) ROLE-SPECIFIC "WHAT WENT RIGHT" POSITIVES

Pair each negative with a role-appropriate positive so good role-play is *rewarded*, not flagged. (The app already has some positives like "Strong entrying" — extend per role.)

- **Entry:** "Great entrying — N opening duels taken, M won, and X% of your deaths were traded. You created space your team converted." (Confirmed-computable.)
- **Refragger:** "You converted Y% of your trade chances — your entry was rarely left unpunished."
- **Support:** "You enabled N teammate duels with timed flashes and threw exec util before contact, and rarely died with util in the bag." 
- **AWP (aggro):** "Aggressive AWP paid off — N opening picks; you got out or got traded after most." **(passive):** "Patient AWP — you caught N rotators on your angle."
- **Lurker:** "Your lurk cut N rotations / closed N post-plants — high-value off-pack play."
- **Anchor:** "You delayed the execute (avg Xs alive after contact) and were traded — textbook anchoring; the round was winnable on the retake."
- **Rotator:** "You rotated on time and arrived alive — N kills reinforcing the pressured site."
- **Closer:** "Above-benchmark clutching — N/M won including a 1vX; calm and isolated well."
- **Flex:** "You played 3 different roles across phases and stayed above lobby-average impact — genuinely flexible."

---

## 5) BUILD SEQUENCE (feature backlog with tags)

| Feature | Diff | Prio | C/I |
|---|---|---|---|
| Phase segmentation (opening/mid/post-plant/retake/clutch) per round | M | now | Confirmed |
| Multi-label role-affinity vector + confidence + schema (S1,S2,S4,S5,S7,S8) | M | now | Confirmed |
| CT per-round hold-vs-rotate classifier (S8) — fixes anchor/rotator | M | now | Confirmed |
| Traded-death rate + trade-opp conversion (S4) reused across roles | M | now | Confirmed |
| Gate existing "died isolated"/"dry opening" insights by role (S12) | M | next | Inferred |
| Zone/half map metadata from last_place_name (enables S9/S12) | M | next | Confirmed |
| Lurker late-impact + bait-guard (S9,S11,S10) | M | next | Confirmed |
| Clutch/closer module + public benchmarks (S11) | M | next | Confirmed |
| Pre-exec util & support-task score (S6) | M | next | Confirmed |
| Earned-flex detector (entropy + impact gate) | L | later | Inferred |
| IGL proxy (opt-in tag) | L | later | Inferred |
| Per-role coaching cards + positives wired to detector output | M | next | Confirmed |

---

## Sources
- Refrag — CS2 Team Roles Explained: https://refrag.gg/blog/cs2-team-roles-explained/ (entry/support/lurker/AWP/anchor/IGL/star; "support often picked up by another role"; entry = kill-anchor-then-get-traded)
- Leetify — Understanding Roles: https://leetify.com/blog/understanding-roles-in-csgo/ ("roles are not rules… swap by game conditions"; IGL usually also support/rifle)
- Leetify — Stats Glossary: https://leetify.com/blog/leetify-stats-glossary/ (EXACT defs: trade kill opp/attempt/success, traded-death opp/attempt/success, flash ≥1.1s→kill rule, enemies-flashed/flash cap 5, HE dmg/HE, unused util on death, TTD, counter-strafe, spray, crosshair placement, CT smokes-that-stopped-a-push)
- Scope.gg — CS:GO Roles: https://scope.gg/guides/csgo_roles_en/ (entry/support/lurker/AWP/IGL/anchor; "some players do several roles")
- TalkEsport — Why Flexible Players Dominate Modern CS2: https://www.talkesport.com/news-app-trends/why-flexible-players-dominate-modern-cs2/ (rigid specialists get exploited; NiKo/ropz/ZywOo hybrids; flex = future)
- Boosteria — Site Anchoring Guide: https://boosteria.org/guides/cs2-site-anchoring-guide-hold-fall-back-rotate-correctly ("anchor tradeable; rotator stays alive & arrives with impact"; delay/fallback; rotate timing)
- esports.gg — All roles in CS2: https://esports.gg/news/counter-strike-2/all-roles-in-cs2/ ; thespike all-roles guide: https://www.thespike.gg/counter-strike-2/beginner-guides/all-cs2-roles-and-positions-guide (roles fluid; primary/secondary)
- BLAST.tv anchor & lurker guides: https://blast.tv/article/cs2-anchor-guide , https://blast.tv/article/cs2-lurker-guide ; cs2hype how-to-play-each-role: https://cs2hype.com/guides/how-to-play-each-role-in-cs2-entry-support-awper-lurker-igl
- Second-entry/refragger: bitskins https://bitskins.com/blog/cs2-team-roles-explained/ , thespike guide ("second entry supports main entry, trades the first kill")
- Lurker timing/paths: thunderpick https://thunderpick.io/blog/how-to-play-as-a-lurker-in-counter-strike-2 ; ESTNN https://estnn.com/cs2-lurker-guide/ (unpredictable timing; cut rotates; Mirage B / Inferno apps)
- IGL responsibilities: cs2guide.net https://cs2guide.net/competitive-play/igl-role-explained/ ; csgo-guides IGL https://csgo-guides.com/roles/igl (defaults/mid-round/economy/morale; shooting captain rare)
- Clutch benchmarks: HLTV "Using clutch stats effectively" https://www.hltv.org/news/35531/using-clutch-stats-effectively ; MGT clutch kings (device 36%/17%, NiKo 34%, m0NESY 33%/14%, 1v1 70%+)
- KAST/ADR/rating benchmarks: cs2bet stats-explained https://www.cs2bet.io/guides/cs2-stats-explained/ ; pley.gg https://pley.gg/cs2/cs2-stats-2/ (KAST>72/75, ADR>80/90, rating 1.10/1.20; support >2.5s flash-blind, 5–10 HE dmg/round)

*Inferred items (tune on real demos): all numeric z-score weights in §2.3; confidence round-count thresholds (§2.5); entry/refragger/lurker/rotator target ranges marked (Inferred) in §3; flex entropy+impact gate; IGL proxy.*
