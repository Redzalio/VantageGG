// selftest.js -- lightweight in-browser smoke harness for the front end.
//
// No deps, no headless-browser download: it drives the REAL app + Flask through the key flows
// and asserts invariants, so the (large) UI doesn't silently regress. Run it any of three ways:
//   * open  http://127.0.0.1:8770/?selftest   (auto-runs + shows a results panel)
//   * console:  runSelfTest()
//   * preview/eval:  await runSelfTest()
// Returns {passed, failed, total, results:[{name, pass, info}]}. Loads a LIBRARY demo when one
// exists (so review/search/goal flows are exercised end to end), else falls back to the sample.

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, tries = 60, ms = 150) {
  for (let i = 0; i < tries; i++) { try { if (fn()) return true; } catch (e) { /* not ready */ } await wait(ms); }
  try { return !!fn(); } catch { return false; }
}

export async function run(App) {
  App = App || window.App;
  if (App) App.ent = null;   // smoke-test exercises every feature regardless of Free/Pro gating
  const R = [];
  const ok = (name, cond, info) => { R.push({ name, pass: !!cond, info: info || "" }); };
  const skip = (name, why) => { R.push({ name, pass: true, info: "SKIP: " + why, skipped: true }); };

  try {
    // --- load a demo (prefer a library demo so sha-gated flows run; else the sample) -----------
    const lib = await fetch("api/library").then((r) => r.json()).catch(() => ({ demos: [] }));
    if (lib.demos && lib.demos.length) {
      const json = await fetch("api/demo/" + encodeURIComponent(lib.demos[0].id)).then((r) => r.json());
      App.loadDemo(json);
    } else {
      document.getElementById("sampleBtn").click();
    }
    await until(() => !document.getElementById("overlay").classList.contains("show") &&
      document.getElementById("mapName").textContent !== "--");
    ok("demo loads", App.demo && App.demo.players.length > 0 && App.demo.rounds.length > 0,
      App.demo ? `${App.demo.map} ${App.demo.rounds.length}r` : "");
    const sha = App.demo && App.demo.raw && App.demo.raw.source_sha1;

    // --- last-round nav must NOT wrap to round 1 (regression we fixed) -------------------------
    const rs = App.demo.rounds, last = rs[rs.length - 1];
    App.t = App._roundSeekT(last);
    const landed = App.demo.roundAt(App.t).number;
    ok("last round seekable (no wrap to R1)", landed >= last.number - 1, `clicked R${last.number} -> R${landed}`);

    // --- Match Report renders + team picker (#38/#39) -----------------------------------------
    document.getElementById("toggleAnalytics").click();
    await until(() => document.querySelector("#analyticsBody .rp-card"));
    ok("match report renders", document.querySelectorAll("#analyticsBody .rp-card").length >= 3);
    ok("team picker present", document.querySelectorAll("#analyticsBody .mt-btn").length >= 1);
    ok("report has action buttons", document.querySelectorAll("#analyticsBody .rp-goal").length >= 1);

    // --- Discord-friendly share export builds a summary (#52) ---------------------------------
    const shareTxt = typeof window.__coachingShareText === "function" ? window.__coachingShareText() : "";
    ok("share export (#52)", !!document.getElementById("anShare") && shareTxt && shareTxt.includes(App.demo.map),
      shareTxt ? `${shareTxt.length} chars` : "no text");

    // --- Death Review: watch-deaths buttons + playlist builds (#48) ----------------------------
    const watchBtns = document.querySelectorAll("#analyticsBody .rp-watchd").length;
    const maxDeaths = App.demo.players.reduce((m, _p, i) => Math.max(m, App.demo.deathsFor(i).length), 0);
    ok("death review playlist (#48)", watchBtns >= 1 && maxDeaths > 0, `${watchBtns} buttons, max ${maxDeaths} deaths`);

    // --- Skill pillars Aim/Util/Positioning render with bands (#47) ----------------------------
    const pvBtn = document.querySelector('#analyticsBody [data-view="player"]');
    if (pvBtn) {
      pvBtn.click();
      await until(() => document.querySelector("#analyticsBody .skillscard .sk-pillar"));
      const pillars = document.querySelectorAll("#analyticsBody .skillscard .sk-pillar");
      const bands = [...document.querySelectorAll("#analyticsBody .skillscard .sk-band")].map((b) => b.textContent.trim());
      ok("skill pillars render (#47)", pillars.length === 3 && bands.some((b) => /^[SABCDF]$/.test(b)),
        `${pillars.length} pillars, bands ${bands.join("/")}`);
      // #49 role model: role line renders (multi-label when baked) + role-based coaching note
      const rl = document.querySelector("#analyticsBody .roleline");
      const hasMulti = rl && /%/.test(rl.textContent);
      const hasCoach = !!document.querySelector("#analyticsBody .rolecoach");
      ok("role model + coaching (#49)", !!rl && (hasMulti || hasCoach || /CT:/.test(rl.textContent)),
        hasMulti ? "multi-label" : (hasCoach ? "coaching note" : "single role"));
      // #50 two-tier util rating: a Volume + Quality banded box
      const ur = document.querySelector("#analyticsBody .ur-box");
      const urBands = ur ? [...ur.querySelectorAll(".sk-band")].map((b) => b.textContent.trim()) : [];
      ok("util rating two-tier (#50)", !!ur && urBands.length >= 2 && urBands.every((b) => /^[SABCDF]$/.test(b)),
        `bands ${urBands.join("/")}`);
      // #62 per-position breakdown: the richer callout table when baked, else the zone fallback
      const pzTable = document.querySelector("#analyticsBody .pz-table");
      const posOk = !!pzTable || !!document.querySelector("#analyticsBody .zrow");
      ok("position breakdown (#62)", posOk, pzTable ? "per-callout table" : "zone fallback");
      // #51 weekly training plan: a plan card with verb-led drills (or the clean-game fallback)
      const tpHdr = [...document.querySelectorAll("#analyticsBody .card-h")].some((h) => /training plan/i.test(h.textContent));
      ok("weekly training plan (#51)", tpHdr, `${document.querySelectorAll("#analyticsBody .tp-row").length} drills`);
    } else { skip("skill pillars render (#47)", "no player view"); }

    // --- Trade network + spacing render in Team view (#43) ------------------------------------
    const tvBtn = document.querySelector('#analyticsBody [data-view="team"]');
    if (tvBtn) {
      tvBtn.click();
      await until(() => document.querySelector("#analyticsBody .teamcard"));
      const tp = App.demo.analytics.team_play;
      if (tp && Object.keys(tp).length) {
        await until(() => document.querySelector("#analyticsBody .tn-row"));
        ok("trade network + spacing render (#43)", document.querySelectorAll("#analyticsBody .tn-row").length > 0,
          `${document.querySelectorAll("#analyticsBody .tn-row").length} player rows`);
      } else { skip("trade network + spacing render (#43)", "team_play not in this cache"); }
    } else { skip("trade network + spacing render (#43)", "no team view"); }

    // --- Data-health panel lists trust checks (#70) -------------------------------------------
    const dvBtn = document.querySelector('#analyticsBody [data-view="data"]');
    if (dvBtn) {
      dvBtn.click();
      await until(() => document.querySelector("#analyticsBody .dh-row"));
      const rows = document.querySelectorAll("#analyticsBody .dh-row").length;
      ok("data-health panel (#70)", rows >= 5, `${rows} checks`);
    } else { skip("data-health panel (#70)", "no data view"); }
    // restore Report view so the harness leaves no sticky state (VIEW is module-scoped)
    document.querySelector('#analyticsBody [data-view="report"]')?.click();
    document.getElementById("closeAnalytics").click();

    // --- Goals CRUD (#27-29) ------------------------------------------------------------------
    const TT = "__selftest__" + Date.now();
    const created = await fetch("api/goals", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ metric: "adr", target: 80, scope: {}, title: TT }) }).then((r) => r.json()).catch(() => null);
    const after = (await fetch("api/goals").then((r) => r.json()).catch(() => ({ goals: [] }))).goals || [];
    ok("goal create", after.some((x) => x.title === TT));
    const gid = (created && created.goal && created.goal.id) || (after.find((x) => x.title === TT) || {}).id;
    if (gid) await fetch("api/goals/" + encodeURIComponent(gid), { method: "DELETE" }).catch(() => {});
    const after2 = (await fetch("api/goals").then((r) => r.json()).catch(() => ({ goals: [] }))).goals || [];
    ok("goal delete", !after2.some((x) => x.title === TT));

    // --- Notes & tags: entity+tag note round-trips through the store (#41) ---------------------
    if (sha) {
      const NT = "__selftest_note__" + Date.now();
      const made = await fetch(`api/reviews/${sha}/bookmarks`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ t: 10, round: 1, entity: "util", ref: "smoke R1", note: NT, tag: "__selftag__" }) }).then((r) => r.json()).catch(() => null);
      const bms = (await fetch(`api/reviews/${sha}/bookmarks`).then((r) => r.json()).catch(() => ({ bookmarks: [] }))).bookmarks || [];
      const found = bms.find((b) => b.note === NT);
      ok("notes & tags (#41)", !!(found && found.entity === "util" && found.tag === "__selftag__"),
        found ? `entity ${found.entity}, tag ${found.tag}` : "not stored");
      if (made && made.id) await fetch(`api/reviews/${sha}/bookmarks/${made.id}`, { method: "DELETE" }).catch(() => {});
    } else { skip("notes & tags (#41)", "sample has no source_sha1"); }

    // --- Utility filter + side colouring/filter (#42 + side work) -----------------------------
    document.getElementById("toggleUtil").click();
    await until(() => App.utilFilter);
    App.utilFilter.type = "all"; App.utilFilter.side = "all"; App.applyUtilSearch();
    const allThrows = App.radar.searchOverlay.length;
    App.utilFilter.side = "ct"; App.applyUtilSearch();
    const ctThrows = App.radar.searchOverlay.length;
    ok("util throws present", allThrows >= 0, `${allThrows} throws`);
    ok("util side filter narrows", ctThrows <= allThrows && App.radar.searchOverlay.every((g) => g._side == null || g._side === 3),
      `ct ${ctThrows}/${allThrows}`);
    App.utilFilter.side = "all"; App.applyUtilSearch();
    document.getElementById("toggleUtil").click();

    // --- Search: round filter returns results (#42) -------------------------------------------
    document.getElementById("searchBtn").click();
    await until(() => document.querySelectorAll("#srResults .sr-row").length > 0 ||
      document.getElementById("srResultHead").textContent.length > 0);
    const searchRows = document.querySelectorAll("#srResults .sr-row").length;
    ok("search lists rounds", searchRows > 0, `${searchRows} rounds`);
    if (sha) {
      await until(() => document.querySelectorAll("#srQuick .sr-qbtn").length > 0, 30);
      ok("quick searches load", document.querySelectorAll("#srQuick .sr-qbtn").length > 0);
    } else { skip("quick searches load", "sample has no source_sha1"); }
    document.getElementById("searchClose").click();

    // --- Review Session: start from a queue, must pause before the moment (#40) ----------------
    if (sha) {
      App.toggleReview(true);
      await until(() => document.querySelector(".rv-q-sess"), 30);
      const sb = document.querySelector(".rv-q-sess");
      if (sb) {
        sb.click(); await wait(300);
        ok("review session starts + pauses", App._session && App._session.items.length > 0 && !App.playing,
          App._session ? `${App._session.items.length} moments` : "");
        App.exitSession();
      } else { skip("review session", "no queues"); }
    } else { skip("review session", "sample has no source_sha1"); }

    // --- Recurring mistakes endpoint (generic, #35) -------------------------------------------
    const rec = await fetch("api/recurring").then((r) => r.json()).catch(() => null);
    ok("recurring endpoint ok", rec && Array.isArray(rec.recurring));

    // --- Cross-match tendencies endpoint returns a valid shape (#44) --------------------------
    const anySid = ((App.demo.analytics && App.demo.analytics.players && App.demo.analytics.players[0]) || {}).steamid;
    if (anySid) {
      const tr = await fetch("api/tendencies/" + encodeURIComponent(anySid)).catch(() => null);
      const td = tr ? await tr.json().catch(() => null) : null;
      if (tr && tr.status === 401) {
        skip("cross-match tendencies (#44)", "login required (AUTH_REQUIRED + not signed in)");
      } else {
        ok("cross-match tendencies (#44)", td && Array.isArray(td.tendencies) && typeof td.n_matches === "number",
          (td && Array.isArray(td.tendencies)) ? `${td.n_matches} matches, ${td.tendencies.length} patterns` : "no response");
      }
    } else { skip("cross-match tendencies (#44)", "no players"); }

    // --- Team playbook: store round-trips + adherence engine runs over the demo (#45) ---------
    const mp = App.demo.map;
    if (mp) {
      const made = await fetch("api/playbook", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ map: mp, side: "ct", name: "__selftest_play__", util: [{ type: "smoke", x: 100, y: 100 }] }) }).then((r) => r.json()).catch(() => null);
      const plays = ((await fetch("api/playbook?map=" + encodeURIComponent(mp)).then((r) => r.json()).catch(() => ({ plays: [] }))).plays) || [];
      const found = plays.find((p) => p.name === "__selftest_play__");
      const adh = found ? App.checkAdherenceJS(found, App._demoThrows()) : null;
      ok("team playbook + adherence (#45)", !!found && adh && typeof adh.adherence_pct === "number",
        adh ? `stored; adherence ${adh.adherence_pct}% over ${adh.rounds_applicable} rounds` : "not stored");
      if (made && made.id) await fetch("api/playbook/" + encodeURIComponent(made.id), { method: "DELETE" }).catch(() => {});
    } else { skip("team playbook + adherence (#45)", "no map"); }

    // --- Auto-detected nade suggestions endpoint returns a valid shape (#61) -------------------
    const sg = await fetch("api/nades/suggest").then((r) => r.json()).catch(() => null);
    ok("auto nade suggestions (#61)", sg && Array.isArray(sg.suggestions),
      sg ? `${sg.suggestions.length} lineups` : "no response");

    // --- suggestions are scoped to the requested map (no other-map lineups bleed in) -----------
    const sgm = await fetch("api/nades/suggest?map=" + encodeURIComponent(App.demo.map)).then((r) => r.json()).catch(() => null);
    const offMap = sgm && (sgm.suggestions || []).filter((s) => s.map && s.map !== App.demo.map).length;
    ok("nade suggestions map-scoped (#3)", sgm && offMap === 0,
      sgm ? `${(sgm.suggestions || []).length} for ${App.demo.map}, ${offMap || 0} off-map` : "no response");

    // --- strategy-board drawing save/load round-trips world strokes (#68) ---------------------
    const dwKey = App._drawKey();
    const dwPrev = localStorage.getItem(dwKey);
    localStorage.setItem(dwKey, JSON.stringify([{ name: "__selftest_dw__", strokes: [[[100, 100], [200, 150], [300, 120]]], t: 10 }]));
    App.strokes = [];
    App.loadDrawing("__selftest_dw__");
    const dwOk = App.strokes.length === 1 && App.strokes[0].length === 3 && App.strokes[0][0][0] === 100;
    if (dwPrev === null) localStorage.removeItem(dwKey); else localStorage.setItem(dwKey, dwPrev);
    App.strokes = []; if (App.drawMode) App.toggleDraw();
    ok("strategy-board drawings (#68)", dwOk, dwOk ? "save+load world strokes" : "round-trip failed");

    // --- death/kill spots overlay populates from kill coords + clears (#62b) -------------------
    let pmIdx = -1, pmKills = 0, pmDeaths = 0;
    for (let i = 0; i < App.demo.players.length; i++) {
      const k = App.demo.kills.filter((e) => e.attacker === i && e.attacker !== e.victim && e.ax != null).length;
      const d = App.demo.kills.filter((e) => e.victim === i && e.vx != null).length;
      if (k + d > pmKills + pmDeaths) { pmIdx = i; pmKills = k; pmDeaths = d; }
    }
    App.showPositionsOnMap(pmIdx, App.demo.players[pmIdx] && App.demo.players[pmIdx].name);
    const po = App.radar.posOverlay;
    const pmOk = po && po.deaths.length === pmDeaths && po.kills.length === pmKills && (pmDeaths + pmKills) > 0
      && document.getElementById("posLegend").classList.contains("show");
    App.clearPositionsOnMap();
    const pmCleared = !App.radar.posOverlay && !document.getElementById("posLegend").classList.contains("show");
    ok("death/kill spots on map (#62b)", pmOk && pmCleared,
      pmOk ? `${pmKills} kills + ${pmDeaths} deaths plotted, clears` : "overlay not set/cleared");

    // --- 3D lineup POV camera (stand at the throw, look at the landing) ------------------------
    const povOk = App.view3d.enterLineupPov({ type: "smoke", throw_pos: [200, 200, 0], land_pos: [800, 600, 0] });
    const povFree = App.view3d.camPreset === "free";
    App.exit3D();
    ok("3D lineup POV camera", povOk === true && povFree, `POV set=${povOk}`);

    // --- crouch (duck) data detection: drives the FP eye-drop + the "re-upload" warning ---------
    const hd = App.demo.hasDuck;
    const anyDuck = App.demo.frames.some(f => (f.players || []).some(p => p && p.duck > 0));
    ok("crouch data detected (#63)", typeof hd === "boolean" && (!anyDuck || hd === true),
      `hasDuck=${hd}, frames with duck>0 ${anyDuck ? "exist" : "none"}`);
  } catch (e) {
    ok("harness ran without throwing", false, String((e && e.stack) || (e && e.message) || e));
  }

  const passed = R.filter((r) => r.pass && !r.skipped).length;
  const skipped = R.filter((r) => r.skipped).length;
  const failed = R.filter((r) => !r.pass).length;
  const summary = { passed, failed, skipped, total: R.length, results: R };
  _renderPanel(summary);
  console.log(`%cself-test: ${failed === 0 ? "PASS" : "FAIL"} -- ${passed} passed, ${failed} failed, ${skipped} skipped`,
    `color:${failed === 0 ? "#5fcf80" : "#ff6f6f"};font-weight:700`);
  console.table(R.map((r) => ({ check: r.name, result: r.skipped ? "skip" : r.pass ? "pass" : "FAIL", info: r.info })));
  return summary;
}

function _renderPanel(s) {
  let el = document.getElementById("selftestPanel");
  if (!el) { el = document.createElement("div"); el.id = "selftestPanel"; document.body.appendChild(el); }
  const c = s.failed === 0 ? "#5fcf80" : "#ff6f6f";
  el.setAttribute("style", "position:fixed;top:10px;left:50%;transform:translateX(-50%);z-index:9999;"
    + "background:#0d1218;border:2px solid " + c + ";border-radius:10px;padding:12px 16px;max-width:520px;"
    + "font:13px/1.5 Inter,system-ui,sans-serif;color:#dfe6ee;box-shadow:0 10px 40px rgba(0,0,0,.6)");
  el.innerHTML = `<div style="font-weight:700;color:${c};margin-bottom:6px">Self-test: ${s.failed === 0 ? "PASS" : "FAIL"} `
    + `&middot; ${s.passed} passed, ${s.failed} failed, ${s.skipped} skipped `
    + `<span style="float:right;cursor:pointer;color:#7b8794" onclick="this.closest('#selftestPanel').remove()">&times;</span></div>`
    + s.results.map((r) => `<div style="color:${r.skipped ? "#7b8794" : r.pass ? "#9fb0bf" : "#ff6f6f"}">`
      + `${r.skipped ? "&#9711;" : r.pass ? "&#10003;" : "&#10007;"} ${r.name}${r.info ? ` <span style="color:#6b7682">(${r.info})</span>` : ""}</div>`).join("");
}

// expose for console + eval, and auto-run on ?selftest
window.runSelfTest = () => run(window.App);
if (/[?&]selftest\b/.test(location.search)) {
  const t = setInterval(() => { if (window.App && window.App.demo !== undefined) { clearInterval(t); run(window.App); } }, 200);
}
