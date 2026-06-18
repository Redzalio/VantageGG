# CS2 Demo Player

A local website to **upload a Counter-Strike 2 `.dem` file and replay it in 2D** --
a top-down radar view where you can scrub the timeline, jump between rounds,
**fly around the map** (pan/zoom), and **spectate** any player (click to follow,
cycle with N/P), with a live scoreboard, kill feed, bomb and grenade markers.

> **2D + 3D.** The default is a **2D radar replay**. There is also a **3D view**
> (toggle **3D**, or double-click the radar) that renders the **real CS2 map
> geometry** (extracted from the installed `.vpk`, untextured greyscale) with players
> as figures at their true positions. The 3D map is **spatially calibrated against
> real spawn data** -- see "3D map geometry & calibration" below. Maps without a
> verified transform fall back to flying over player positions and say so.
>
> **Replay overlays.** Players show a floating name/HP/**equipped-gun** label with a
> **crossed-eye icon when flashed** (3D and 2D), and a team-coloured **POV cone on the
> floor** (3D) / view cone (2D). The header carries a live **C4 / defuse timer** when the
> bomb is down, the left scoreboard shows each player's **full round loadout**, and the
> Utility panel has a **throw heatmap** toggle.

## Quick start

Double-click **`start.bat`** (first run creates the venv, installs deps, and
downloads radar images), then your browser opens at
**http://127.0.0.1:8770**.

Or manually:

```bat
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe fetch_radars.py      REM one-time: get radar images
.venv\Scripts\python.exe app.py
```

Then open http://127.0.0.1:8770 and either **Load sample** (built-in mock match)
or **Upload .dem** (drag-and-drop works too).

## Where to get a `.dem`

- In CS2: **Watch -> Your Matches -> Download**, files land in
  `...\Steam\steamapps\common\Counter-Strike Global Offensive\game\csgo\replays`
- Or from HLTV match pages, FACEIT, etc.

## Controls

| | |
|---|---|
| Space | play / pause |
| <- -> | seek 5s (+Shift = 1s) |
| `[` `]` | previous / next round |
| N / P | cycle spectated player |
| F | free camera |
| + / - | playback speed |
| 3D | toggle 3D view (or double-click the map) |
| C | cycle 3D camera (Fly / Follow / Overhead / Utility / Death) |
| drag = pan, wheel = zoom | click a player = spectate them |
| (in 3D) WASD move, E/Q up/down | Space still = play/pause |

## Coaching analytics & nade library

Open **Analytics** for a per-player report: HLTV-2.0-equiv rating, ADR/KAST/KPR/DPR, opening
duels, **trade opportunities** (was a teammate in refrag range when you died?), side/buy splits,
and an insight feed split into **What to fix** -- each with a "Why:" evidence line and a replay
deep-link -- and **What went right**. Rates use each player's *rounds actually played*. Ratings,
roles and buy types are transparent approximations, not official HLTV/Leetify values.

**Accuracy:** kills/deaths/assists and ADR are validated against the demo's own engine-tracked
scoreboard -- run `python tools/validate_stats.py <demo.dem> <cache.json>` to audit any match
(on the two test demos: K/D exact for all 20 players, ADR within ~0.5 of the official value).

The **Utility** panel is a CSNADES-style lineup library: browse by where a nade lands -> throw
spot -> video; **add/edit/delete** lineups (set throw + landing by clicking the map),
**favorite** the ones you run, upload clips, or pull accurate lineups from a demo's own throws.
The *Demo throws* tab tags each actual throw as matching a saved lineup, **"diff spot"** (right
landing, wrong setup), or **off-book**.

**Trends** (header button) is the cross-match view: pick a player (and optionally **filter by
map**) to see their rating / ADR / KAST / opening% / traded% across every cached demo, with
up/down arrows for the trend (first-half vs last-half) and the list of cached matches. It also
holds a **team config** (roster + roles, `team.json` -- configured roles show in the analytics
player card, overriding the inferred role) and a **practice plan**: the current match's top 5
team weaknesses as a checklist whose done-state persists server-side (`practice.json`) so you can
track them across sessions.

The single-match panel also has a per-player **Context rating** card -- a transparent,
HLTV-3.0-*inspired* breakdown into 6 eco-adjusted, lobby-relative sub-ratings (Kills / Damage /
Survival / KAST / Multi / Swing); it is explicitly **not** the official Rating 3.0.

The replay has **timeline marker layers** (Settings -> Timeline markers: kills / utility / bomb /
insight flags / key moments) and a **3D asset status** panel (click the header `3D` chip) listing
every map's geometry + verification.

## Hosting (private team)

Local use needs nothing extra. To host for a team see **[DEPLOY.md](DEPLOY.md)** -- Docker or
waitress, env-configurable data dirs, behind a reverse proxy (security note: no app auth yet, so
keep it on a LAN/VPN).

## Project layout

```
app.py            Flask server (serves UI, /api/upload, /api/sample); versioned cache validation
parser.py         .dem -> normalized JSON (demoparser2). `--probe` mode to inspect a demo
analytics.py      coaching metrics + insights (side/buy splits, trades, confidence)
roundlib.py       shared pure round/buy/weapon helpers (norm_weapon, is_util_damage_weapon)
matchindex.py     cross-match index + per-player trends ("am I getting better?")
context_rating.py HLTV-3.0-inspired, eco-adjusted Context Rating (transparent, lobby-relative)
teams.py          local team config (roster, roles) -- team.json
practiceplan.py   practice-plan done-state store -- practice.json
mapstatus.py      per-map 3D-asset status (geometry present/verified/spawns/size)
nades.py          local grenade-lineup library (CSNADES-style schema; add/edit/delete/favorite)
nades/            library.json (saved lineups) + videos/ (uploaded clips, size-capped)
schema.py         SCHEMA_VERSION + ANALYTICS_VERSION (dep-free; drives cache invalidation)
tests/            pytest suite for the pure analytics/round/cache/nades logic
mockgen.py        builds a current-schema mock sample (fallback for "Load sample")
fetch_radars.py   downloads radar PNGs + builds static/maps/maps.json (calibration)
static/           index.html, css, js (demo.js, radar2d.js, app.js, view3d.js), maps/, maps3d/
static/maps3d/    3D map meshes (<map>_full.glb), spawn anchors, transforms.json
cache/            parsed-demo JSON (cached by content hash) + sample.json
                  + <key>.meta.json sidecars (map/versions/rounds for cheap checks)
uploads/          uploaded .dem files (by hash)
wsgi.py           production WSGI entry (waitress/gunicorn) -- see DEPLOY.md
Dockerfile, docker-compose.yml, DEPLOY.md   private-team hosting (env config + volumes)
tools/            Source2Viewer-CLI.exe, gltfpack.exe + build/validate scripts:
                  build_map_geometry.py, finalize_transforms.py, asciiify.py, regen_sample.py,
                  validate_stats.py (audit K/D/A/ADR vs the demo's official scoreboard)
```

## 3D map geometry & calibration

The 3D view shows the **actual map mesh** (untextured, greyscale), aligned 1:1 with the
demo's player coordinates. Everything is derived from the **real installed CS2 map files**
(`...\csgo\maps\<map>.vpk`) -- nothing is eyeballed.

**What coordinate system things are in**
- **Demo player positions** are canonical **Source world units** (validated: at round-start,
  players sit 7-28 u from the map's real `info_player_*` spawn origins).
- **VRF-exported glTF** is **metres, Y-up**, with `glb = (wx, wz, -wy) * 0.0254`.
- The renderer keeps players canonical and transforms only the GLB:
  `three = world * S` with `S = 0.06`; since the world->three axis permutation `(x, z, -y)`
  equals VRF's, placing the GLB needs just **scale `S/0.0254 = 2.3622`** + a vertical offset
  to the demo floor. Per-map config lives in `static/maps3d/transforms.json`
  (`unitScale`, `axisMap`, `rotationDeg`, `translate`, `verified`, `validation`).

The renderer also applies a **90 deg rotation** about the up axis (`rotationDeg: 90` in
`transforms.json`): VRF's glTF export + three.js's GLTFLoader land the world mesh rotated 90 deg
relative to the demo's world frame. This was found empirically -- raycasting the mesh **in
three.js** (the real renderer) against the real spawns: 90 deg aligns all spawns; 0/180/270 deg do not.

**How alignment is validated** -- the authoritative check is **in-browser**: load a demo, open the
3D calibration overlay, and the spawn rings + players sit on the real spawn geometry. (Caveat:
`tools/validate_alignment.py` runs under *trimesh*, whose coordinate frame differs from three.js --
it only confirms the mesh *has* spawn-aligned floors under some orientation, it does **not** prove
the shipped render rotation. Don't trust a trimesh "pass" as proof the render is aligned.)

### Supported maps (verified 3D geometry)

Every active-duty + reserve map has verified, spawn-calibrated 3D geometry (rotation 90, floors
raycast-checked against the real `info_player_*` spawns *in three.js*). "Miss@wrong-rot" is how
many spawns fall off the mesh at the three wrong 90-degree rotations (the discriminator):

| Map | spawns on floor @ 90 | miss @ wrong rot (0/180/270) | notes |
|---|---|---|---|
| de_anubis   | 32/32 | -          | original |
| de_dust2    | 30/30 | -          | original |
| de_train    | 28/28 | -          | original |
| de_mirage   | 33/33 | 14/14/14   | |
| de_vertigo  | 30/30 | 11/14/14   | high-altitude map |
| de_nuke     | 40/40 | 24/26/4    | multi-level |
| de_overpass | 44/44 | 14/20/20   | |
| de_ancient  | 24/24 | 12/12/13   | spawns sit right on the floor (bias ~0) |
| de_inferno  | 40/40 | 37/38/40   | lots of terrain; 31/40 within 15u of median |

Maps without a verified transform fall back to flying over player positions and say so (the
header shows a per-map `3D` status chip: green `3D ok` vs grey `3D -`).

**Rebuild + verify geometry for a map** (needs the VPK installed). Two steps -- build, then
finalize the `verified` flag from the authoritative in-browser check:
```bat
:: 1. extract anchors, export+compress the world mesh, ship the GLB (provisional verified flag)
.venv\Scripts\python.exe tools\build_map_geometry.py de_mirage de_inferno de_nuke de_ancient de_overpass de_vertigo
:: 2. (in the running app) raycast each map's spawns in three.js -> tools\verify_results.json
:: 3. write the honest verified flag + validation from those results
.venv\Scripts\python.exe tools\finalize_transforms.py
```
Step 1 extracts spawn anchors, exports the world render mesh via `tools\Source2Viewer-CLI.exe`,
compresses it with `tools\gltfpack.exe` (`-cc -si 0.7 -slb` -- meshopt + **border-locked**
simplify, which preserves floors; plain `-si 0.5` drops them), runs a *trimesh* floor sanity
check (different coordinate frame -- NOT proof of render alignment), and writes
`static/maps3d/<map>_full.glb`, `<map>_anchors.json`, and a provisional `transforms.json` entry.
Step 3 overwrites `verified`/`validation` from the **three.js raycast** results: a map is
verified only when rotation 90 uniquely seats every spawn on the mesh.

**Calibration overlay** -- Settings (&#9881;) -> *Calibration overlay* draws the CS axes, GLB + player
bounds, real CT/T spawn rings, and round-1 freeze-time player dots, and logs diagnostics to the
console. Players at round start should stand inside the spawn rings.

### 3D review (P6)

With the map aligned, the 3D view is a real review tool -- everything is drawn at true world height:

- **Grenade arcs** -- the actual throw trajectory (with Z) drawn in 3D, plus a bright moving head.
- **Utility volumes** -- smokes as translucent spheres, molotovs as fire discs on the ground,
  flashes/HE as quick pops -- all synced to the playback clock at the real landing position.
- **Kill markers** -- an X at each recent death (at the victim's real height) + **bullet/hit traces**
  (attacker->victim lines, red = headshot) that flick during fights.
- **Camera presets** (the *Cam* button or **C**): *Fly* (free WASD), *Follow* (third-person on the
  spectated player), *Overhead* (tactical high-angle tracking the action), *Utility* (follows the
  in-flight nade / newest smoke), *Death* (cuts to the most recent death spot).
- **Nade-library lineups in 3D** -- selecting a lineup draws its throw->landing arc + landing volume
  in the 3D map (as well as on the 2D radar).

Grenade trajectory points carry Z as of schema v7 (`[t, x, y, z]`); re-upload a demo to get 3D arcs.

## Analytics

Per player: K/A/D, ADR, KAST, HLTV-2.0-equiv rating, impact, KPR/DPR, HS%, opening duels,
multi-kills, flashes, utility, role inference, and an auto "what you did wrong" feed (each card
deep-links to the round/tick). Plus (P2):

- **CT/T side splits** -- kills/deaths/KD/ADR/KAST per side (they reconcile with the totals).
- **Buy-type splits** -- per-player performance on pistol / eco / force / full rounds, with win%.
- **Per-round buy classification** -- each side labelled pistol/eco/light/force/full (+ anti-eco,
  **mixed/broken buy**, **hero/saved weapon**) from freeze-end equipment value. **Side-aware** (a full
  buy costs more on CT than T). Approximate by design -- see `docs/CS2_ECONOMY_REFERENCE.md` for the
  verified CS2 prices/rewards + limitations. Pistol rounds detected from side-swap + low-equip (MR12/MR15).
- **Deeper trades** -- 1s (fast) and 5s (loose) trade windows, traded-deaths, average trade distance.
- **Confidence labels** -- every insight is tagged high/med/low, and ratings/roles/buys/zones are
  explicitly marked as transparent approximations (see `analytics.meta`) -- never sold as exact.

P3 adds a **Leetify-style coaching layer** (all from a transparent, documented model -- not the
official Leetify/HLTV maths):

- **Top 5 fixes** per player -- the highest-severity benchmark gaps, impact drains and mistakes,
  each with a one-line drill and a jump-to-replay link, shown first.
- **Impact breakdown** -- a per-kill win-probability model (`_winprob_ct`: man-advantage + bomb
  state) attributes each kill's swing to the shooter, split into Opening / Trading / Firepower,
  plus Clutch and Utility, rendered as "gained & lost" bars summing to an Impact score.
- **Clutch detection** -- 1vX situations (last player alive) and their outcome, per player.
- **Round breakdown** -- a "why each round went that way" card per round (opening duel, pistol/
  anti-eco, plant/defuse, decisive swing) with a watch-in-replay button.
- **Team review** -- most common focus areas across the stack, buy-type win rates, and a 3-5 item
  **practice plan** for the next session.

P7 adds **advanced mistake detectors** (all heuristic, confidence-labelled, woven into the same
insight feed + Top Fixes, each with a replay jump):

- **Dry-peek** -- took the round's opening duel with no supporting flash.
- **Predictable** -- died at the same callout+side repeatedly (they're pre-aiming you).
- **Clumping / bad spacing** -- died bunched with a teammate (one spray/nade got both).
- **Economy discipline** -- bought while the team was on eco ("broke eco").
- **Aim (approx, low-confidence)** -- % of damage dealt while moving (counter-strafe proxy).
- Plus the earlier position detectors: isolated deaths, mid-round K/D leaks, save mistakes.

P4 adds a full **per-team coaching view** (toggle Player/Team at the top of the report). For each
team (defined by round-1 sides, persisting across the half-swap):

- **Round-loss taxonomy** -- every lost round bucketed into one primary reason (threw a man-advantage,
  opening death not traded, lost the post-plant, failed the retake, lost an even full-buy, eco/save...),
  each with the round numbers (click to jump to that round in the replay).
- **Entry WR, trade %, post-plant WR, retake WR** at a glance.
- **Economy** -- win rate by buy type; **death zones** -- where the team dies most, by side.
- **Roles** per player and a team **practice plan** (drills + the exact rounds to review).

Round construction is robust: rounds are built by tick range anchored on real `round_end` rows
(warmup/restart/null rows are dropped), shared between `parser.py` and `analytics.py` via
`roundlib.py`. Schema versions live in `schema.py` (`SCHEMA_VERSION`, `ANALYTICS_VERSION`); bumping
`ANALYTICS_VERSION` recomputes analytics from the cached replay without re-parsing the demo.

## Nade library

A local grenade-lineup library (Utility panel -> **Nade library** tab) modelled on CSNADES.gg's
fields but built as **your own open data** -- we don't scrape their site (their database/videos are
their content; their ToS + copyright apply). Instead:

- **Seeded** with common lineups, including real anubis coordinates pulled from the sample demo.
- **"+ from this demo"** extracts the actual grenades your team threw (real in-game world coords,
  deduped by landing) straight into the library.
- **"Import JSON..."** accepts a CSNADES-style array (`map`, `type`/`grenade`, `from`/`to`,
  `throw_pos`/`land_pos`, `technique`/`jumpthrow`, `video`, ...) -- paste an export you have rights to.
- Filter by type, click a lineup to **draw its throw + landing on the 2D radar**.

**Browse like CSNADES:** lineups group by **where they land** (target callout) -- pick a target
(e.g. *Mid Doors*), see the throw spots that hit it (*from Top Suicide*, *from T Spawn*...), pick one,
and **watch the recorded video**. **Add a lineup** (+ Add lineup): set type/side/callouts, click the
2D map to capture the **throw** then **landing** position, attach a **video** (paste a YouTube/mp4 URL
*or upload a clip*), and save. Uploaded clips are stored content-addressed under `nades/videos/` and
played back in-app (YouTube embeds; files play inline).

Stored in `nades/library.json` (+ `nades/videos/`); API: `/api/nades` (GET/POST/DELETE),
`/api/nades/import`, `/api/nades/video` (upload).

**Actual-vs-lineup** -- the *Demo throws* tab tags each thrown nade **~ <lineup>** when it lands near a
saved lineup, or **off-book** when it doesn't; the library list shows **thrown Nx** so you can see which
set pieces the team actually executes (and which saved lineups go unused).

## Tests

```bat
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest tests\ -q
```

Covers the pure logic: round pairing, buy classification, rating formulas, side/buy splits
(reconciliation), confidence stamping, and cache validation. No demo file required.

## Notes / troubleshooting

- **Map shows "no radar image":** the demo's map isn't in `static/maps/`. Re-run
  `fetch_radars.py` (it covers the active pool + many others) or drop in a
  `<map>.png` + calibration.
- **Parsing is slow:** large demos take 10-60s to decode the first time; results
  are cached by content hash so re-opening is instant.
- **Field-name issues on a specific demo:** run
  `python parser.py --probe your.dem` to print the exact tick columns / events
  `demoparser2` exposes for that file; `parser.py` degrades gracefully if an
  optional field is missing.

Radar images: [2mlml/cs2-radar-images](https://github.com/2mlml/cs2-radar-images).
Parser: [demoparser2](https://github.com/LaihoE/demoparser).
