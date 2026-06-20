// analytics.js -- renders the coaching/analytics panel from demo.analytics.
// Overview scoreboard, skill radar, benchmark-colored stat tiles, insight feed
// (click -> jump replay to the round/tick), and map-zone K/D.

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

let APP = null, SEL = 0;   // SEL = selected analytics-player index
let VIEW = "report";       // "report" | "player" | "team" -- Report is the landing page

export async function openAnalytics(app) {
  APP = app;
  const A = app.demo && app.demo.analytics;
  $("analyticsPanel").classList.add("show");
  $("toggleAnalytics").classList.add("on");
  if (!A) { renderNoAnalytics(app); return; }   // older demo with no analytics -> basic scoreboard fallback
  // map steamid -> replay index for jump-to-replay
  app._sidToIdx = {};
  app.demo.players.forEach((p, i) => app._sidToIdx[p.steamid] = i);
  // configured team roles (override the inferred role when set) -- best effort
  APP._roleMap = {};
  try { ((await fetch("api/team").then(r => r.json())).players || []).forEach(p => { if (p.role) APP._roleMap[p.steamid] = p.role; }); } catch (e) { /* offline */ }
  // default selection (no hardcoded user): last-viewed player if present in this demo,
  // otherwise whoever most needs coaching (most insight flags, tie-break lowest rating).
  SEL = pickDefaultPlayer(A);
  APP.myTeamId = resolveMyTeam(A);   // which side is "yours" for THIS demo (per-demo, no roster needed)
  render();
}

// Fallback when app.demo.analytics is missing (demo parsed before the analytics pipeline existed).
// We can't show coaching, but the top-level demo JSON still has players + kill events, so render a
// basic K/D scoreboard from demo.statsUpTo() (final cumulative K/A/D) rather than a dead-end message.
function renderNoAnalytics(app) {
  const note = `<div class="empty">Analytics were not computed for this demo. Re-upload the file to generate coaching data. Showing basic scoreboard data only.</div>`;
  const d = app && app.demo;
  const players = (d && d.players) || [];
  if (!players.length) { $("analyticsBody").innerHTML = note; return; }
  // statsUpTo(Infinity) -> final cumulative {k,a,d} per player index (same source the replay scoreboard uses)
  const stats = (d && typeof d.statsUpTo === "function") ? d.statsUpTo(Infinity) : players.map(() => ({ k: 0, a: 0, d: 0 }));
  const rowFor = (tm) => players
    .map((p, i) => ({ p, s: stats[i] || { k: 0, a: 0, d: 0 }, i }))
    .filter(x => x.p.team === tm)
    .sort((a, b) => b.s.k - a.s.k)
    .map(({ p, s }) => {
      const kd = s.d ? (s.k / s.d) : s.k;   // avoid divide-by-zero (0 deaths -> show raw kills as K/D)
      return `<tr><td class="nm">${esc(p.name)}</td><td>${s.k}</td><td>${s.d}</td><td>${kd.toFixed(2)}</td></tr>`;
    }).join("");
  const head = `<tr><th>Player</th><th>K</th><th>D</th><th>K/D</th></tr>`;
  const ct = rowFor(3), t = rowFor(2);
  const body = (ct || t)
    ? `<table class="sb"><thead>${head}</thead>
         <tbody class="ct">${ct}</tbody>
         <tbody class="sep"><tr><td colspan="4"></td></tr></tbody>
         <tbody class="t">${t}</tbody></table>`
    // no team info (team==0/unset for every player) -> one flat table, no CT/T split
    : `<table class="sb"><thead>${head}</thead><tbody>${
        players.map((p, i) => {
          const s = stats[i] || { k: 0, a: 0, d: 0 };
          const kd = s.d ? (s.k / s.d) : s.k;
          return `<tr><td class="nm">${esc(p.name)}</td><td>${s.k}</td><td>${s.d}</td><td>${kd.toFixed(2)}</td></tr>`;
        }).join("")
      }</tbody></table>`;
  $("analyticsBody").innerHTML = `${note}<div class="card wide"><div class="card-h">Scoreboard <em>basic data</em></div>${body}</div>`;
}

const SEL_KEY = "cs2dp_selected_steamid";
function pickDefaultPlayer(A) {
  const saved = localStorage.getItem(SEL_KEY);
  if (saved) {
    const i = A.players.findIndex(p => p.steamid === saved);
    if (i >= 0) return i;
  }
  let best = 0, bestScore = -Infinity;
  A.players.forEach((p, i) => {
    const flags = ((A.insights && A.insights[p.steamid]) || []).filter(i => i.polarity !== "good").length;
    const score = flags * 100 - (p.hltv || 0);   // most-flagged first, then lowest rating
    if (score > bestScore) { bestScore = score; best = i; }
  });
  return best;
}
function rememberPlayer(A) {
  const p = A.players[SEL];
  if (p) { try { localStorage.setItem(SEL_KEY, p.steamid); } catch {} }
}
// "Your team" for the loaded demo -- an explicit, per-demo choice (team id "A"/"B"), NOT a saved
// roster. Defaults to the team of the default-selected player so behaviour is unchanged out of the
// box; flippable in the Team view and remembered per demo. No team config required.
function myTeamKey() {
  const d = APP.demo;
  return "cs2dp_myteam_" + ((d && d.raw && d.raw.source_sha1) || (d && d.map) || "x");
}
function resolveMyTeam(A) {
  const tc = (A.team_coaching && A.team_coaching.teams) || [];
  if (!tc.length) return null;
  let saved = null;
  try { saved = localStorage.getItem(myTeamKey()); } catch {}
  if (saved && tc.some(t => t.id === saved)) return saved;          // remembered pick for this demo
  const sel = A.players[SEL];                                       // else: the selected player's team
  const mine = sel && tc.find(t => (t.players || []).includes(sel.name));
  return mine ? mine.id : tc[0].id;
}
function setMyTeam(id) {
  APP.myTeamId = id;
  try { localStorage.setItem(myTeamKey(), id); } catch {}
  render();
}
export function closeAnalytics(app) {
  $("analyticsPanel").classList.remove("show");
  $("toggleAnalytics").classList.remove("on");
}

function render() {
  const A = APP.demo.analytics;
  // Free gets the Report + a LIMITED Player view (their own player + the shallower cards, with a
  // "Pro analytics" teaser below). Team/Data deep-dives stay Pro. Dormant while TIERS_ENABLED=0 ->
  // entitled() is always true (and the sample previews Pro), so nothing locks.
  const proAA = !!(window.App && App.entitled && App.entitled("advancedAnalytics"));
  const canGoal = !!(window.App && App.entitled && App.entitled("goals"));
  const canDeaths = !!(window.App && App.entitled && App.entitled("threeD"));
  if (!proAA && (VIEW === "team" || VIEW === "data")) VIEW = "report";
  // Free player view is pinned to the logged-in user's own player (no analyzing others).
  const myStid = (window.App && App.me && App.me.user && App.me.user.steam_id_64) || null;
  const selfIdx = myStid ? A.players.findIndex(p => String(p.steamid) === String(myStid)) : -1;
  if (!proAA && VIEW === "player" && selfIdx >= 0) SEL = selfIdx;
  const sel = A.players[SEL];
  const tdLock = proAA ? "" : ' <span class="pro-lock">PRO</span>';
  const head = `<div class="an-head">
      <div class="an-title">Coaching Report <span>| ${esc(APP.demo.map)} | ${A.n_rounds} rounds</span></div>
      <div class="an-tabs">
        <button class="antab ${VIEW === "report" ? "on" : ""}" data-view="report">Report</button>
        <button class="antab ${VIEW === "player" ? "on" : ""}" data-view="player">Player</button>
        <button class="antab ${VIEW === "team" ? "on" : ""} ${proAA ? "" : "antab-pro"}" data-view="team">Team${tdLock}</button>
        <button class="antab ${VIEW === "data" ? "on" : ""} ${proAA ? "" : "antab-pro"}" data-view="data">Data${tdLock}</button>
      </div>
      <select id="anPlayerSel" class="an-sel" ${VIEW === "player" ? "" : 'style="display:none"'}></select>
      <button id="anShare" class="an-share" title="Copy a Discord-friendly summary (and download a .txt)">&#8682; Share</button>
    </div>`;
  const playerBody = proAA ? `
    ${focusCard(sel, null, canGoal)}
    <div class="an-grid">
      <div class="an-col">
        ${playerCard(sel, A.benchmarks, canDeaths)}
        ${breakdownCard(sel)}
        ${insightsCard(sel, A)}
      </div>
      <div class="an-col">
        ${skillsCard(sel)}
        ${radarCard(sel, A.benchmarks)}
        ${contextCard(sel)}
        ${zonesCard(sel)}
      </div>
    </div>
    ${trainingPlanCard(sel)}
    ${teamCard(A)}
    ${roundsCard(A)}
    ${overviewCard(A)}`
  : `
    ${focusCard(sel, 3, canGoal)}
    <div class="an-grid">
      <div class="an-col">${playerCard(sel, A.benchmarks, canDeaths)}</div>
      <div class="an-col">${skillsCard(sel)}${radarCard(sel, A.benchmarks)}</div>
    </div>
    ${proAnalyticsTeaser()}`;
  $("analyticsBody").innerHTML = head + (VIEW === "report" ? reportView(A)
    : VIEW === "team" ? teamView(A, sel) : VIEW === "data" ? dataHealthView(A) : playerBody);

  // view tabs: free can open Report + Player; Team/Data gate to Pro (upsell, stay put)
  $("analyticsBody").querySelectorAll("[data-view]").forEach(el =>
    el.onclick = () => {
      const v = el.dataset.view;
      if (!proAA && (v === "team" || v === "data")) { window.App && App._upsell && App._upsell("advancedAnalytics"); return; }
      VIEW = v; render();
    });
  const ptc = $("analyticsBody").querySelector(".pt-cta");   // "See Pro" on the free teaser
  if (ptc) ptc.onclick = () => window.App && App._upsell && App._upsell("advancedAnalytics");
  if ($("anShare")) $("anShare").onclick = () => shareCoaching(A);   // #52 Discord-friendly export
  $("analyticsBody").querySelectorAll("[data-myteam]").forEach(el =>
    el.onclick = () => setMyTeam(el.dataset.myteam));

  // player selector (player view only). Free is locked to its own player; Pro can pick anyone.
  const ps = $("anPlayerSel");
  if (ps) {
    if (!proAA && VIEW === "player") {
      ps.innerHTML = `<option>${esc(sel ? sel.name : "You")}</option>`;
      ps.disabled = true;
      ps.title = "Free shows your own player — Pro analyzes any player";
      ps.onchange = null;
    } else {
      ps.disabled = false;
      ps.title = "";
      ps.innerHTML = A.players.map((p, i) =>
        `<option value="${i}" ${i === SEL ? "selected" : ""}>${esc(p.name)} -- ${p.hltv.toFixed(2)}</option>`).join("");
      ps.onchange = (e) => { SEL = +e.target.value; rememberPlayer(A); render(); };
    }
  }

  // wire jumps + row clicks
  $("analyticsBody").querySelectorAll("[data-jump]").forEach(el => {
    el.onclick = () => {
      const [sid, round, tick] = el.dataset.jump.split("|");
      jump(sid, round === "null" ? null : +round, tick === "null" ? null : +tick);
    };
  });
  $("analyticsBody").querySelectorAll("[data-selsid]").forEach(el => {
    el.onclick = () => { SEL = A.players.findIndex(p => p.steamid === el.dataset.selsid); rememberPlayer(A); render(); };
  });
  // clickable player name -> open that player's detail (Player view). Free can only open their own
  // (others -> upsell), mirroring the per-match player picker's Pro gate.
  $("analyticsBody").querySelectorAll("[data-pview]").forEach(el => {
    el.onclick = (e) => {
      e.stopPropagation();
      const sid = el.dataset.pview;
      const i = A.players.findIndex(p => String(p.steamid) === String(sid));
      if (i < 0) return;
      const proAA = !!(window.App && App.entitled && App.entitled("advancedAnalytics"));
      const myStid = (window.App && App.me && App.me.user && App.me.user.steam_id_64) || null;
      if (!proAA && String(sid) !== String(myStid)) { window.App && App._upsell && App._upsell("advancedAnalytics"); return; }
      SEL = i; rememberPlayer(A); VIEW = "player"; render();
    };
  });
  // "+ Goal" on a coaching fix -> open the Practice Goals modal prefilled
  $("analyticsBody").querySelectorAll("[data-gsid]").forEach(el => {
    el.onclick = () => APP.makeGoalFromInsight({
      player: el.dataset.gsid, area: el.dataset.garea,
      title: el.dataset.gtitle, drill: el.dataset.gdrill,
    });
  });
  // round-number pill -> seek replay to that round's start
  $("analyticsBody").querySelectorAll("[data-jumpr]").forEach(el => {
    el.onclick = () => {
      const r = A.rounds.find(x => x.num === +el.dataset.jumpr);
      if (r) { APP.t = r.start_t; APP.playing = true; closeAnalytics(APP); }
    };
  });
  // round-card "watch" -> seek replay to that time and close the panel
  $("analyticsBody").querySelectorAll("[data-jumpt]").forEach(el => {
    el.onclick = () => { APP.t = parseFloat(el.dataset.jumpt); APP.playing = true; closeAnalytics(APP); };
  });
  // #41 "note this round" -> save a round-tagged note (opens the Review panel with it added)
  $("analyticsBody").querySelectorAll("[data-noteround]").forEach(el => {
    el.onclick = () => APP.addEntityNote({ entity: "round", ref: "R" + el.dataset.noteround,
      round: +el.dataset.noteround, t: +el.dataset.notet,
      promptText: `Note for round ${el.dataset.noteround}:` });
  });
  // #48 "watch deaths" -> close panel, run a 3D death-review session for that player (by steamid)
  $("analyticsBody").querySelectorAll("[data-watchd]").forEach(el => {
    el.onclick = () => {
      const idx = APP.demo.players.findIndex(p => String(p.steamid) === String(el.dataset.watchd));
      if (idx < 0) return;
      closeAnalytics(APP);
      APP.startDeathReview(idx, el.dataset.wname);
    };
  });
  // #62b "show these spots on the map" -> close panel, plot the player's death/kill spots on 2D
  $("analyticsBody").querySelectorAll("[data-posmap]").forEach(el => {
    el.onclick = () => {
      const idx = APP.demo.players.findIndex(p => String(p.steamid) === String(el.dataset.posmap));
      if (idx < 0) return;
      closeAnalytics(APP);
      APP.showPositionsOnMap(idx, el.dataset.pname);
    };
  });
  // per-callout action hub -> hand off to review / utility / goals / notes
  $("analyticsBody").querySelectorAll("[data-callout-act]").forEach(b => b.onclick = (e) => {
    e.stopPropagation();
    const zone = b.dataset.zone;
    const act = b.dataset.calloutAct;
    if (typeof APP === "undefined") return;
    const map = { deaths: "reviewDeathsAtCallout", utility: "showUtilityToCallout",
      throws: "findThrowsAtCallout", goal: "createGoalForCallout", note: "addNoteAtCallout" };
    const fn = map[act];
    if (fn && typeof APP[fn] === "function") APP[fn](zone);
    else if (act === "utility" && APP.openLibraryAtCallout) APP.openLibraryAtCallout(zone); // fallback
  });
}

// --- P3 coaching cards ------------------------------------------------------
function focusCard(p, limit, canGoal) {
  let items = p.focus || [];
  if (!items.length) return "";
  const capped = limit && items.length > limit;
  if (limit) items = items.slice(0, limit);
  const rows = items.map((f, i) => {
    const jump = f.round != null
      ? `<button class="ijump" data-jump="${p.steamid}|${f.round}|${f.tick}">\u25b6 R${f.round}</button>` : "";
    const goal = canGoal ? `<button class="igoal" title="Turn this into a tracked practice goal"
      data-gsid="${esc(p.steamid)}" data-garea="${esc(f.area || "")}"
      data-gtitle="${esc(f.detail || "")}" data-gdrill="${esc(f.fix || "")}">+ Goal</button>` : "";
    return `<div class="fix sev${f.severity}">
      <div class="fixn">${i + 1}</div>
      <div class="fixb"><div class="fixt">${esc(f.detail)}</div>
        <div class="fixfix">-> ${esc(f.fix)}</div></div><div class="fixbtns">${jump}${goal}</div></div>`;
  }).join("");
  // when free (limited), tell them the full list is behind Pro
  const more = capped ? `<div class="fix-more">Full fix list in <b>Pro</b></div>` : "";
  const title = limit ? "Top fixes" : `Top fixes for ${esc(p.name)}`;
  return `<div class="card fixcard"><div class="card-h">${title} <em>do these first</em></div>${rows}${more}</div>`;
}

// Shown at the bottom of the FREE player view so users see there's a deeper analysis behind Pro.
function proAnalyticsTeaser() {
  const items = [
    ["&#9733;", "Full <b>what-to-fix</b> list", "every flag with fixes, not just your top 3"],
    ["&#9678;", "<b>Positions</b> &amp; map zones", "your K/D by callout, side, and opening duels"],
    ["&#9633;", "<b>Impact</b> &amp; <b>context</b> ratings", "how much each round swung on you"],
    ["&#9654;", "<b>Weekly training plan</b>", "drills tailored to your weak spots"],
    ["&#9707;", "<b>Round-by-round</b> breakdown", "why each round went the way it did"],
    ["&#9783;", "<b>Team review</b> + any player", "analyze teammates and opponents, not just you"],
  ];
  const rows = items.map(([ic, h, sub]) =>
    `<div class="pt-row"><span class="pt-ic">${ic}</span><div><div class="pt-h">${h}</div><div class="pt-d">${sub}</div></div></div>`).join("");
  return `<div class="card pro-teaser">
    <div class="card-h">Unlock the full analysis <span class="pro-lock">PRO</span></div>
    <div class="pt-sub">You're seeing your own snapshot. Pro opens the whole coaching report:</div>
    <div class="pt-grid">${rows}</div>
    <button class="btn primary pt-cta">See Pro &rarr;</button></div>`;
}

function breakdownCard(p) {
  const b = p.impact_breakdown || {};
  const ents = Object.entries(b);
  if (!ents.length) return "";
  const max = Math.max(1, ...ents.map(([, v]) => Math.abs(v)));
  const bars = ents.sort((a, b) => b[1] - a[1]).map(([k, v]) => {
    const w = Math.abs(v) / max * 100, pos = v >= 0;
    return `<div class="bdrow"><div class="bdl">${k}</div>
      <div class="bdtrack"><i class="${pos ? "pos" : "neg"}" style="width:${w.toFixed(0)}%"></i></div>
      <div class="bdv ${pos ? "good" : "bad"}">${v > 0 ? "+" : ""}${v}</div></div>`;
  }).join("");
  const cl = p.clutch || {};
  const clutchTxt = cl.attempts ? `Clutches ${cl.won}/${cl.attempts} won` : "No clutch situations";
  return `<div class="card"><div class="card-h">Impact breakdown <em>win-prob contribution</em></div>
    <div class="bdtotal">Impact <b class="${p.impact_score >= 0 ? "good" : "bad"}">${p.impact_score > 0 ? "+" : ""}${p.impact_score}</b>
      | round-swing ${p.round_swing}</div>
    ${bars}
    <div class="sub">${clutchTxt} | transparent approximate model, not official Leetify.</div></div>`;
}

function teamCard(A) {
  const t = A.team;
  if (!t) return "";
  const areas = (t.top_areas || []).map(a => `<span class="tchip">${esc(a.area)} <b>${a.players}</b></span>`).join("") || "--";
  const buys = Object.entries(t.buy_outcomes || {})
    .map(([k, v]) => `${k} <b class="${v.win_pct >= 50 ? "good" : "bad"}">${v.win_pct}%</b> (${v.rounds})`).join(" | ") || "--";
  const plan = (t.practice_plan || []).slice(0, 5).map((p, i) =>
    `<div class="planrow"><b>${i + 1}.</b> ${esc(p.focus)} <span class="pmut">(${p.players} players)</span> -- ${esc(p.drill)}</div>`).join("")
    || `<div class="empty">No items.</div>`;
  return `<div class="card"><div class="card-h">Team review <em>${A.n_rounds} rounds</em></div>
    <div class="sub" style="margin-top:0">Most common focus areas: ${areas}</div>
    <div class="sub">Buy-type win rate: ${buys}</div>
    <div class="planbox"><div class="planh">Practice plan -- next session</div>${plan}</div></div>`;
}

function roundsCard(A) {
  const cards = A.round_cards || [];
  if (!cards.length) return "";
  const rows = cards.map(rc => {
    const wc = rc.winner === "CT" ? "ct" : rc.winner === "T" ? "t" : "";
    const buy = rc.buy_ct ? `CT ${rc.buy_ct} | T ${rc.buy_t}` : "";
    return `<div class="rcard ${wc}">
      <div class="rctop"><span class="rcn">R${rc.round}</span><span class="rcw ${wc}">${rc.winner || "?"}</span>
        <button class="ijump" data-jumpt="${rc.watch_t}">\u25b6</button></div>
      <div class="rcs">${esc(rc.summary)}</div>
      <div class="rcmeta">${buy}</div></div>`;
  }).join("");
  return `<div class="card"><div class="card-h">Round breakdown <em>why each round went that way</em></div>
    <div class="rcgrid">${rows}</div></div>`;
}

// --- Match Report (the loop's hinge) ----------------------------------------
// One scannable page assembled from existing analytics, scoped to your side (app.myTeamId).
// Findings reuse the panel's existing handlers: data-jumpr/data-jumpt = Watch, data-gsid = + Goal.
const POS_LABEL = {
  multikills: "Multi-kill rounds", good_openings: "Strong opening duels",
  good_utility: "Effective utility", high_impact: "High-impact rounds",
  good_trades: "Good trading", clutch: "Clutch won", good_save: "Smart saves",
};
// #70 data-trust / parse-health: what's reliable vs missing vs approximate in THIS demo.
function dataHealthView(A) {
  const meta = A.meta || {};
  const players = A.players || [];
  const p0 = players[0] || {};
  const flash = players.some(p => (p.enemy_flashed || 0) > 0);
  const geo = !!(APP.view3d && (APP.view3d._cfg || APP.view3d.calibrated));
  const baked = !!(p0.subratings && p0.subratings.aim);
  const posBaked = Array.isArray(p0.position_stats) && p0.position_stats.length > 0;
  const teamPlay = !!(A.team_play && Object.keys(A.team_play).length);
  const row = (s, label, detail) => {
    const cls = s === true ? "ok" : s === false ? "bad" : "warn";
    const ic = s === true ? "&#10003;" : s === false ? "&#10007;" : "~";
    return `<div class="dh-row ${cls}"><span class="dh-i">${ic}</span>
      <span class="dh-l">${esc(label)}</span><span class="dh-d">${esc(detail || "")}</span></div>`;
  };
  return `<div class="card">
    <div class="card-h">Data health <em>what's trustworthy in this demo</em></div>
    ${row(true, "Parse", `${A.n_rounds} rounds · ${(APP.demo.frames || []).length} frames · ${players.length} players · ${A.tickrate || 64} tick`)}
    ${row(!!A.have_econ, "Economy / buy types", A.have_econ ? "equip values present — buy classification valid" : "no equip data — buy types unavailable on this demo")}
    ${row(flash, "Flash / blind data", flash ? "player_blind recorded — enemy-flash & flash-assist stats valid"
      : "this demo did not record player_blind — enemy-flash, flash-assist & team-flash stats are blank (util quality scores on damage only)")}
    ${row(geo, "3D map geometry", geo ? "calibrated for " + esc(APP.demo.map) : "no verified geometry for this map — 3D falls back to the radar floor")}
    ${row(baked ? true : "warn", "Advanced player stats", baked ? "skill bands / roles / util baked into this cache" : "computed live in the browser (older cache) — re-upload the demo to bake them in")}
    ${row(posBaked ? true : "warn", "Per-callout breakdown", posBaked ? "baked" : "using flat zone K/D (re-upload to get the per-side/opening table)")}
    ${row(teamPlay ? true : "warn", "Team trade-network / spacing", teamPlay ? "available" : "re-upload to compute")}
    <div class="dh-note"><b>Exact:</b> ${esc((meta.exact || []).join(", ") || "--")}</div>
    <div class="dh-note"><b>Approximate:</b> ${esc((meta.approx || []).join(", ") || "--")}</div>
    ${meta.note ? `<div class="dh-note pmut">${esc(meta.note)}</div>` : ""}
  </div>`;
}

// #52 Discord-friendly share: build a markdown summary of the current view, copy + download .txt
function shareReportText(A) {
  const tc = (A.team_coaching && A.team_coaching.teams) || [];
  const myId = (APP.myTeamId && tc.some(t => t.id === APP.myTeamId)) ? APP.myTeamId : (tc[0] || {}).id;
  const me = tc.find(t => t.id === myId) || tc[0] || {};
  const opp = tc.find(t => t !== me) || {};
  const mine = A.players.filter(p => (me.players || []).includes(p.name));
  const L = [`**${APP.demo.map} — ${me.name || "My team"} ${me.won}-${me.lost} vs ${opp.name || "?"}**  (${A.n_rounds} rounds)`, ""];
  L.push("__Top fixes__");
  ((me.practice_plan || []).slice(0, 3)).forEach((p, i) => L.push(`${i + 1}. ${p.focus}${p.drill ? " — " + p.drill : ""}`));
  if (!(me.practice_plan || []).length) L.push("- clean game, no recurring mistakes");
  L.push("", "__Players__");
  mine.forEach(p => L.push(`• ${p.name}: ${(p.hltv || 0).toFixed(2)} rating · ${Math.round(p.adr || 0)} ADR · ${Math.round(p.kast || 0)}% KAST · ${(p.kd || 0).toFixed(2)} K/D · open ${Math.round(p.open_wr || 0)}%`));
  return L.concat(["", "_via VantageGG_"]).join("\n");
}
function sharePlayerText(p, A) {
  const sr = (p.subratings && p.subratings.aim) ? p.subratings : computeSubratingsJS(p);
  const band = (k) => (sr[k] && sr[k].score != null ? `${sr[k].band} (${sr[k].score})` : "--");
  const role = roleLabels(p.t_roles) || p.t_role;
  const f = (p.focus || [])[0];
  const L = [`**${p.name} — ${APP.demo.map}**  (${A.n_rounds} rounds)`,
    `${(p.hltv || 0).toFixed(2)} rating · ${p.kills}/${p.assists}/${p.deaths} · ${Math.round(p.adr || 0)} ADR · ${Math.round(p.kast || 0)}% KAST · ${(p.hs_pct || 0)}% HS`,
    `Aim ${band("aim")} · Utility ${band("utility")} · Positioning ${band("positioning")}`];
  if (role && role !== "--") L.push(`Role (T): ${role}`);
  if (f) L.push(`Fix: ${f.detail || f.area}${f.fix ? " — " + f.fix : ""}`);
  return L.concat(["", "_via VantageGG_"]).join("\n");
}
function coachingShareText(A) {
  return VIEW === "player" ? sharePlayerText(A.players[SEL], A) : shareReportText(A);
}
// side-effect-free hook for the self-test harness
window.__coachingShareText = () => { try { return coachingShareText(APP.demo.analytics); } catch (e) { return ""; } };
function shareCoaching(A) {
  const text = coachingShareText(A);
  try { if (navigator.clipboard) navigator.clipboard.writeText(text); } catch (e) { /* clipboard may be blocked */ }
  try {
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url; a.download = `${(APP.demo.map || "cs2")}_${VIEW}.txt`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) { /* download unavailable */ }
  const b = $("anShare");
  if (b) { const o = b.innerHTML; b.innerHTML = "✓ copied"; setTimeout(() => { b.innerHTML = o; }, 1400); }
  return text;
}

function reportView(A) {
  const tc = (A.team_coaching && A.team_coaching.teams) || [];
  if (!tc.length) return `<div class="empty">No team data for this demo — use the Player tab for individual coaching.</div>`;
  const myId = (APP.myTeamId && tc.some(t => t.id === APP.myTeamId)) ? APP.myTeamId : tc[0].id;
  const me = tc.find(t => t.id === myId) || tc[0];
  const opp = tc.find(t => t !== me);
  const mine = A.players.filter(p => (me.players || []).includes(p.name));
  // The Report itself is FREE coaching. The Pro-feature *actions* in it (track as a goal -> Goals;
  // watch deaths in 3D) are simply HIDDEN for users who can't use them, so the free view isn't
  // cluttered with PRO badges. (The chokepoints openGoals/startDeathReview still gate as a backstop.)
  const canGoal = !!(APP.entitled && APP.entitled("goals"));
  const canDeaths = !!(APP.entitled && APP.entitled("threeD"));

  const pick = `<div class="myteam-pick"><span class="mt-lbl">Your team:</span>${tc.map(t =>
    `<button class="mt-btn ${t.id === myId ? "on" : ""}" data-myteam="${esc(t.id)}">`
    + `${esc(t.name)} <span class="pmut">(${esc(t.start_side)} start)</span></button>`).join("")}</div>`;

  // 1) Top 3 fixes (team practice plan) -> proof-round pills (Watch) + Make goal
  const fixes = (me.practice_plan || []).slice(0, 3).map(p =>
    `<div class="rp-fix"><div class="rp-fix-h"><b>${esc(p.focus)}</b>
      ${canGoal ? `<button class="rp-goal" data-gsid="" data-garea="${esc(p.focus)}" data-gtitle="${esc(p.focus)}" data-gdrill="${esc(p.drill || "")}">+ Goal</button>` : ""}</div>
      <div class="rp-sub">${esc(p.drill || "")}</div>
      ${(p.rounds || []).length ? `<div class="rp-rounds">watch ${roundPills(p.rounds)}</div>` : ""}</div>`).join("")
    || `<div class="empty">No recurring team mistakes this match — clean game.</div>`;

  // 2) What went well (good-polarity insights for your players, top 3 distinct)
  const mySids = new Set(mine.map(p => String(p.steamid)));
  const seen = new Set(); const pos = [];
  for (const [sid, lst] of Object.entries(A.insights || {})) {
    if (!mySids.has(String(sid))) continue;
    const nm = (mine.find(p => String(p.steamid) === String(sid)) || {}).name;
    for (const x of (lst || [])) {
      if (x.polarity !== "good") continue;
      const k = x.type + "|" + nm; if (seen.has(k)) continue; seen.add(k);
      pos.push(`<div class="rp-pos">&#10003; <b>${esc(POS_LABEL[x.type] || (x.type || "").replace(/_/g, " "))}</b> &mdash; ${esc(nm || "")}</div>`);
    }
  }
  const posHtml = pos.slice(0, 3).join("") || `<div class="empty">Upload more demos to surface what's working.</div>`;

  // 3) Key rounds (top 5 by round impact = biggest swing); reuse data-jumpt = seek+close
  const rc = A.round_cards || [], rounds = A.rounds || [];
  const imp = (c, i) => { const r = rounds[i] || rounds.find(x => x.num === c.round) || {}; return typeof r.impact === "number" ? r.impact : 0; };
  const keyRounds = rc.map((c, i) => ({ c, v: imp(c, i) })).sort((a, b) => b.v - a.v).slice(0, 5).map(({ c }) =>
    `<div class="rp-round"><div class="rp-round-h"><span class="rcn">R${c.round}</span>
      <span class="round">${esc(c.winner || "?")} won &middot; ${esc(c.buy_ct || "?")}/${esc(c.buy_t || "?")}</span>
      <button class="ijump" data-jumpt="${c.watch_t}">&#9654; watch</button>
      <button class="rp-note" data-noteround="${c.round}" data-notet="${c.watch_t}" title="Add a note for this round">&#9998;</button></div>
      <div class="rp-sub">${esc(c.summary || "")}</div></div>`).join("") || `<div class="empty">—</div>`;

  // 4) Your players row -> + Goal from each player's top focus
  const prow = mine.map(p => {
    const f = (p.focus || [])[0];
    const goal = (f && canGoal) ? `<button class="rp-goal sm" data-gsid="${esc(String(p.steamid))}" data-garea="${esc(f.area || "")}" data-gtitle="${esc(f.detail || f.area || "")}" data-gdrill="${esc(f.fix || "")}">+ Goal</button>` : "";
    const reviewCell = canDeaths ? `<td><button class="rp-watchd" data-watchd="${esc(String(p.steamid))}" data-wname="${esc(p.name)}"
        title="Watch all of ${esc(p.name)}'s deaths in 3D">&#9654; deaths</button></td>` : "";
    return `<tr><td class="rp-pn"><span class="an-name-lnk" data-pview="${esc(String(p.steamid))}" title="View ${esc(p.name)}'s detail">${esc(p.name)}</span></td><td title="CT: ${roleLabels(p.ct_roles) || esc(p.ct_role || "--")}  |  T: ${roleLabels(p.t_roles) || esc(p.t_role || "--")}">${esc(p.ct_role || p.t_role || "--")}</td>
      <td>${(p.hltv || 0).toFixed(2)}</td><td>${Math.round(p.adr || 0)}</td><td>${Math.round(p.kast || 0)}%</td>
      <td>${(p.kd || 0).toFixed(2)}</td><td>${Math.round(p.open_wr || 0)}%</td>
      <td>${p.counter_strafe != null ? p.counter_strafe + "%" : "--"}</td>
      <td class="rp-fixcell" title="${f ? esc(f.fix || "") : ""}">${f ? esc(f.area) : "--"} ${goal}</td>
      ${reviewCell}</tr>`;
  }).join("");

  // 5) Utility summary (your team)
  const sum = k => mine.reduce((s, p) => s + (p[k] || 0), 0);
  const udr = mine.length ? mine.reduce((s, p) => s + (p.udr || 0), 0) / mine.length : 0;
  const util = `Smokes <b>${sum("smokes")}</b> &middot; Flashes <b>${sum("flashes_thrown")}</b> &middot; HE <b>${sum("hes")}</b> &middot; Molotov <b>${sum("molotovs")}</b> &middot; <b>${udr.toFixed(0)}</b> util dmg/round`;

  return pick
    + `<div class="rp-score"><b>${esc(me.name)}</b> <span class="big ${me.won >= me.lost ? "good" : "bad"}">${me.won}-${me.lost}</span>
        <span class="round">vs ${esc(opp ? opp.name : "?")} &middot; ${esc(APP.demo.map)} &middot; ${A.n_rounds} rounds &middot; started ${esc(me.start_side)}</span></div>
      <div class="rp-cols">
        <div class="rp-card"><div class="rp-h">Top 3 things to fix <em>practice these</em></div>${fixes}</div>
        <div class="rp-card"><div class="rp-h">What went well <em>keep doing it</em></div>${posHtml}</div>
      </div>
      <div class="rp-card"><div class="rp-h">Key rounds <em>biggest swings &mdash; watch these</em></div>${keyRounds}</div>
      <div class="rp-card"><div class="rp-h">Your players</div>
        <table class="rp-table"><thead><tr><th>Player</th><th>Role</th><th>Rating</th><th>ADR</th><th>KAST</th><th>K/D</th><th>Open%</th><th title="counter-strafe %: shots fired while stopped (trigger discipline)">C-strafe</th><th>Top fix</th>${canDeaths ? "<th>Review</th>" : ""}</tr></thead>
        <tbody>${prow}</tbody></table></div>
      <div class="rp-card"><div class="rp-h">Utility</div><div class="rp-util">${util}</div></div>`;
}

// --- P4 team view -----------------------------------------------------------
function teamView(A, sel) {
  const tc = A.team_coaching && A.team_coaching.teams;
  if (!tc || !tc.length) return `<div class="empty">No team data for this demo.</div>`;
  const myId = (APP.myTeamId && tc.some(t => t.id === APP.myTeamId)) ? APP.myTeamId
    : ((tc.find(t => (t.players || []).includes(sel.name)) || tc[0]).id);
  const mine = tc.find(t => t.id === myId);
  const ordered = mine ? [mine, ...tc.filter(t => t !== mine)] : tc;
  // explicit per-demo "which side is mine" picker (drives the highlight here + the Match Report)
  const pick = `<div class="myteam-pick"><span class="mt-lbl">Your team:</span>${tc.map(t =>
    `<button class="mt-btn ${t.id === myId ? "on" : ""}" data-myteam="${esc(t.id)}" title="${esc((t.players || []).join(", "))}">`
    + `${esc(t.name)} <span class="pmut">(${esc(t.start_side)} start)</span></button>`).join("")}</div>`;
  const tp = A.team_play || {};
  return pick + ordered.map(t => teamCardFull(t, myId, tp[t.id])).join("");
}

function roundPills(rounds) {
  return (rounds || []).slice(0, 10).map(rn => `<button class="rpill" data-jumpr="${rn}">${rn}</button>`).join("");
}

function teamCardFull(t, myId, tp) {
  const isMine = t.id === myId;
  const stat = (lbl, val, unit, good) =>
    `<div class="tstat"><div class="tsv ${good == null ? "" : good ? "good" : "bad"}">${val == null ? "--" : val + unit}</div>
      <div class="tsl">${lbl}</div></div>`;
  const lr = (t.loss_reasons || []).filter(r => r.reason !== "Lost on an eco/save");
  const maxlr = Math.max(1, ...lr.map(r => r.count));
  const lossRows = lr.map(r => `<div class="lrrow">
      <div class="lrl">${esc(r.reason)} <b>${r.count}</b></div>
      <div class="lrtrack"><i style="width:${(r.count / maxlr * 100).toFixed(0)}%"></i></div>
      <div class="lrr">${roundPills(r.rounds)}</div></div>`).join("") || `<div class="empty">No recurring loss pattern.</div>`;
  const econ = Object.entries(t.economy || {}).map(([k, v]) =>
    `<span class="tchip">${k} <b class="${v.win_pct >= 50 ? "good" : "bad"}">${v.win_pct}%</b> <span class="pmut">(${v.rounds})</span></span>`).join("") || "--";
  const zones = (t.top_death_zones || []).map(z =>
    `<span class="tchip">${esc(z.zone)} <span class="${z.side}">${z.side.toUpperCase()}</span> <b>${z.deaths}</b></span>`).join("") || "--";
  const roles = (t.roles || []).map(r =>
    `<div class="rolerow"><b>${esc(r.name)}</b> <span class="ct">${esc(r.ct)}</span> / <span class="t">${esc(r.t)}</span>
      <span class="pmut">open ${r.open_wr}% | impact ${r.impact}</span></div>`).join("");
  const plan = (t.practice_plan || []).map((p, i) =>
    `<div class="planrow"><b>${i + 1}.</b> ${esc(p.focus)} -- ${esc(p.drill)} ${roundPills(p.rounds)}</div>`).join("")
    || `<div class="empty">No recurring issues -- solid team game.</div>`;
  const pp = t.post_plant || {}, rt = t.retake || {};
  return `<div class="card teamcard ${isMine ? "mine" : ""}">
    <div class="card-h">${esc(t.name)} ${isMine ? "<em>your team</em>" : ""}
      <span class="big ${t.won >= t.lost ? "good" : "bad"}">${t.won}-${t.lost}</span></div>
    <div class="roleline">Started ${t.start_side} | ${esc(t.players.join(", "))}</div>
    <div class="tstats">
      ${stat("Entry WR", t.entry.wr, "%", t.entry.wr >= 50)}
      ${stat("Trade %", t.trade_pct, "%", t.trade_pct >= 20)}
      ${stat("Post-plant", pp.wr, "%", pp.wr == null ? null : pp.wr >= 55)}
      ${stat("Retake", rt.wr, "%", rt.wr == null ? null : rt.wr >= 35)}
    </div>
    <div class="tsec"><div class="tsech">Why rounds were lost</div>${lossRows}</div>
    <div class="tsec"><div class="tsech">Economy -- win% by buy</div>${econ}</div>
    <div class="tsec"><div class="tsech">Where you die most</div>${zones}</div>
    ${teamPlayBlock(tp)}
    <div class="tsec"><div class="tsech">Roles</div>${roles}</div>
    <div class="planbox"><div class="planh">Practice plan -- next session</div>${plan}</div>
  </div>`;
}

// #43 trade network + spacing. tp = analytics.team_play[teamId] (may be undefined on old caches).
function teamPlayBlock(tp) {
  if (!tp || !tp.players) {
    return `<div class="tsec"><div class="tsech">Trade network &amp; spacing</div>
      <div class="empty">Re-upload this demo to compute trade network &amp; spacing.</div></div>`;
  }
  const m = (u) => (u == null ? "--" : (u / 100).toFixed(1) + "m");   // ~100 units per metre
  const sp = tp.spacing;
  const spById = {};
  if (sp) (sp.players || []).forEach((p) => { spById[p.steamid] = p; });
  const rows = (tp.players || []).map((p) => {
    const tpct = p.traded_pct;
    const barCls = tpct == null ? "" : (tpct >= 50 ? "good" : (tpct < 35 ? "bad" : ""));
    const bar = tpct == null ? ""
      : `<span class="tn-bar"><i class="${barCls}" style="width:${Math.max(3, Math.min(100, tpct))}%"></i></span>`;
    const s = spById[p.steamid];
    const iso = s && s.isolated ? ` <span class="bad" title="isolated deaths (no teammate nearby)">${s.isolated} solo</span>` : "";
    return `<div class="tn-row">
      <span class="tn-name">${esc(p.name)}</span>
      <span class="tn-traded ${barCls}">${tpct == null ? "--" : tpct + "%"}</span>
      ${bar}
      <span class="tn-sub pmut">${p.traded}/${p.deaths} traded &middot; made ${p.trades_made}</span>
      <span class="tn-dist" title="avg distance to nearest teammate at death">${s ? m(s.avg_support_dist) : "--"}${iso}</span>
    </div>`;
  }).join("");
  const edges = (tp.edges || []).slice(0, 6).map((e) =>
    `<span class="tchip">${esc(e.trader)} -&gt; ${esc(e.victim)} <b>${e.count}</b></span>`).join("") || "--";
  const weak = (tp.weak_links || []).map((w) =>
    `<span class="tchip bad">${esc(w.name)} <b>${w.traded_pct}%</b> <span class="pmut">(${w.untraded} untraded)</span></span>`).join("");
  const tt = tp.team_traded_pct;
  const spLine = sp
    ? `<div class="sub">Avg support distance ${m(sp.avg_support_dist)} &middot; ${sp.isolated_deaths} isolated &middot; ${sp.clumped_deaths} clumped deaths</div>`
    : `<div class="sub pmut">Spacing needs replay frames (re-upload to compute).</div>`;
  return `<div class="tsec"><div class="tsech">Trade network &amp; spacing
      ${tt != null ? `<span class="pmut">team traded ${tt}%</span>` : ""}</div>
    <div class="tn-grid">${rows}</div>
    ${spLine}
    <div class="sub">Trade links: ${edges}</div>
    ${weak ? `<div class="sub">Weak links: ${weak}</div>` : ""}</div>`;
}

// --- cards ------------------------------------------------------------------
function tile(label, val, bench, higherBetter = true, fmt = (v) => v) {
  let cls = "neu";
  if (bench != null) {
    const better = higherBetter ? val >= bench : val <= bench;
    const margin = Math.abs(val - bench) / (Math.abs(bench) || 1);
    cls = margin < 0.04 ? "neu" : (better ? "good" : "bad");
  }
  return `<div class="tile ${cls}"><div class="tv">${fmt(val)}</div>
    <div class="tl">${label}</div>${bench != null ? `<div class="tb">vs ${fmt(bench)}</div>` : ""}</div>`;
}

// #49 multi-label roles: "Entry 60% · Support 25%" (falls back to the single primary role)
function roleLabels(labels) {
  return (labels || []).map((l) => `${esc(l.role)} ${Math.round(l.weight * 100)}%`).join(" · ");
}
function roleText(p, side) {
  const labels = p[side + "_roles"];
  if (labels && labels.length) {
    const conf = p[side + "_role_conf"] === "low" ? ' <span class="pmut">(low&nbsp;conf)</span>' : "";
    return roleLabels(labels) + conf;
  }
  return esc(p[side + "_role"] || "--");
}
function roleCoachNote(p) {
  const rc = p.role_coaching;
  if (!rc) return "";
  const pct = /wr|kast/i.test(rc.metric) ? "%" : "";
  const ev = (rc.value != null && rc.target)
    ? ` <span class="${rc.verdict === "above" ? "good" : "bad"}" title="your ${rc.metric} vs a solid target">${rc.metric} ${rc.value}${pct} vs ${rc.target}${pct}</span>` : "";
  return `<div class="rolecoach"><b>As ${esc(rc.role)}</b> &mdash; ${esc(rc.watch)}
    <div class="rc-drill">&rarr; ${esc(rc.drill)}${ev}</div></div>`;
}

// #50 two-tier util rating. Server bakes p.util_rating; mirror it here for older caches.
// KEEP IN SYNC WITH utilrating.py (anchors/weights/verdict). Reuses srInterp/srBand from #47.
const UR_VOLUME = [1.5, 3.5, 6.0], UR_UDR = [3, 8, 16], UR_CONV = [0.4, 0.9, 1.6], UR_BLIND = [0.6, 1.1, 1.8], UR_MINFLASH = 3;
function urVerdict(v, q) {
  const hv = v >= 60, hq = q >= 60;
  if (hv && hq) return "Impactful — you throw a lot of utility and it lands.";
  if (hv && !hq) return "High volume, low impact — tighten your lineups and timing.";
  if (hq && !hv) return "Efficient — few nades but effective; you could throw more.";
  return "Underutilizing utility — learn your team's standard lineups.";
}
function computeUtilRatingJS(p, hasFlash) {
  const rp = Math.max(1, p.rounds_played || 1);
  let util_pr = p.util_pr;
  if (util_pr == null) util_pr = ((p.smokes || 0) + (p.flashes_thrown || 0) + (p.hes || 0) + (p.molotovs || 0)) / rp;
  const udr = +(p.udr || 0), ft = +(p.flashes_thrown || 0), ef = +(p.enemy_flashed || 0), ab = +(p.avg_blind || 0), tf = +(p.team_flashed || 0);
  const volume = srInterp(+util_pr, ...UR_VOLUME);
  const comps = [[srInterp(udr, ...UR_UDR), 0.5]];
  let flash_conv = null;
  if (hasFlash && ft >= UR_MINFLASH) { flash_conv = ef / ft; comps.push([0.6 * srInterp(flash_conv, ...UR_CONV) + 0.4 * srInterp(ab, ...UR_BLIND), 0.5]); }
  const qden = comps.reduce((a, c) => a + c[1], 0) || 1;
  let quality = comps.reduce((a, c) => a + c[0] * c[1], 0) / qden;
  quality = Math.max(0, quality - (hasFlash ? Math.min(15, tf / rp * 30) : 0));
  const vb = srBand(volume), qb = srBand(quality);
  return { volume: { score: Math.round(volume), band: vb[0], label: vb[1] },
    quality: { score: Math.round(quality), band: qb[0], label: qb[1] },
    util_pr: Math.round(util_pr * 100) / 100, udr: Math.round(udr * 10) / 10,
    flash_conv: flash_conv != null ? Math.round(flash_conv * 100) / 100 : null,
    team_flashed: tf, flash_data: !!hasFlash, verdict: urVerdict(volume, quality) };
}
function utilRatingBlock(p) {
  // flash data is available if any player blinded an enemy (some demos don't emit player_blind)
  const hasFlash = (APP.demo.analytics.players || []).some((pp) => (pp.enemy_flashed || 0) > 0);
  const u = (p.util_rating && p.util_rating.volume) ? p.util_rating : computeUtilRatingJS(p, hasFlash);
  const tier = (lbl, d) => `<span class="ur-tier"><span class="ur-tl">${lbl}</span>
    <span class="sk-band b-${d.band}">${d.band}</span><span class="ur-ts">${d.score}</span></span>`;
  const flashTxt = u.flash_data === false ? " &middot; flash data n/a in this demo"
    : (u.flash_conv != null ? ` &middot; ${u.flash_conv} blinds/flash` : "");
  return `<div class="ur-box">
    <div class="ur-h">Utility rating ${tier("Volume", u.volume)}${tier("Quality", u.quality)}</div>
    <div class="ur-verdict">${esc(u.verdict)}</div>
    <div class="sub">${u.util_pr}/round &middot; ${u.udr} dmg/round${flashTxt}${u.team_flashed ? ` &middot; ${u.team_flashed} team-flashes` : ""}</div>
  </div>`;
}

// #51 auto weekly training plan: rank this player's weaknesses (aim/util/positioning/opening/role/
// worst-callout) and turn the worst few into verb-led drills spread across the week. Synthesizes
// fields already available client-side (reuses the #47/#50 fallbacks), so it works on any cache.
const TRAIN_DAYS = ["Mon", "Tue", "Thu", "Sat"];
function buildTrainingPlan(p) {
  const map = (APP.demo && APP.demo.map) ? APP.demo.map.replace(/^de_/, "") : "this map";
  const sr = (p.subratings && p.subratings.aim) ? p.subratings : computeSubratingsJS(p);
  const hasFlash = (APP.demo.analytics.players || []).some((pp) => (pp.enemy_flashed || 0) > 0);
  const ur = (p.util_rating && p.util_rating.volume) ? p.util_rating : computeUtilRatingJS(p, hasFlash);
  const cand = [];   // {sev (lower=worse), area, action (verb-led), why}
  // Aim
  if (sr.aim && sr.aim.score != null) {
    const cs = p.counter_strafe;
    if (cs != null && cs < 55)
      cand.push({ sev: sr.aim.score, area: "Aim", why: `counter-strafe ${cs}% (aim ${sr.aim.band})`,
        action: "Drill counter-strafing — 50 stop-and-shoot wall reps, then a deathmatch where you fully stop before every shot." });
    else if (sr.aim.score < 60)
      cand.push({ sev: sr.aim.score, area: "Aim", why: `aim ${sr.aim.score}/100 (${sr.aim.band})`,
        action: "Warm up with two deathmatch games focused on crosshair placement at head height before you queue." });
  }
  // Utility
  if (ur.quality && ur.quality.score < 55)
    cand.push({ sev: ur.quality.score, area: "Utility", why: `util quality ${ur.quality.score}/100 (${ur.udr} dmg/round)`,
      action: `Learn three smoke/molly lineups for ${map}'s common stack spots and throw each ten times in a private server.` });
  else if (ur.volume && ur.volume.score < 45)
    cand.push({ sev: ur.volume.score, area: "Utility", why: `low util volume (${ur.util_pr}/round)`,
      action: "Add one smoke or flash to your default on every execute this week." });
  // Positioning / trading
  if (sr.positioning && sr.positioning.score != null && sr.positioning.score < 60) {
    if ((p.traded_pct != null && p.traded_pct < 50))
      cand.push({ sev: sr.positioning.score, area: "Positioning", why: `traded on ${p.traded_pct}% of deaths`,
        action: `Drill crossfires with a teammate on ${map} and refrag immediately after first contact so your deaths get traded.` });
    else
      cand.push({ sev: sr.positioning.score, area: "Positioning", why: `positioning ${sr.positioning.score}/100`,
        action: "Re-watch your deaths and play five rounds staying within trade range of a teammate." });
  }
  // Opening duels
  const ot = (p.open_k || 0) + (p.open_d || 0);
  if (ot >= 4 && (p.open_wr || 0) < 48)
    cand.push({ sev: p.open_wr || 0, area: "Opening", why: `opening win ${p.open_wr}% (vs ~52)`,
      action: `Prefire ${map}'s common entry angles, then review every opening death for what beat you.` });
  // Worst callout (from #62 position_stats)
  const worst = (p.position_stats || []).filter((r) => (r.k + r.d) >= 3 && r.kd < 0.7)
    .sort((a, b) => a.kd - b.kd)[0];
  if (worst)
    cand.push({ sev: 30 + worst.kd * 20, area: "Map", why: `${worst.zone}: ${worst.k}-${worst.d}`,
      action: `Review your ${worst.zone} deaths and change your angle or timing at that spot.` });
  // Role-specific drill (verb-led, straight from the role coach)
  if (p.role_coaching && p.role_coaching.drill && p.role_coaching.verdict === "below")
    cand.push({ sev: 58, area: `${p.role_coaching.role} role`, why: `${p.role_coaching.metric} below target`,
      action: p.role_coaching.drill.charAt(0).toUpperCase() + p.role_coaching.drill.slice(1) });

  cand.sort((a, b) => a.sev - b.sev);
  return cand.slice(0, 4).map((c, i) => ({ ...c, day: TRAIN_DAYS[i] || "Sun" }));
}
function trainingPlanCard(p) {
  const plan = buildTrainingPlan(p);
  if (!plan.length)
    return `<div class="card"><div class="card-h">Weekly training plan</div>
      <div class="rp-pos">&#10003; No glaring weakness this match — keep your routine and review close rounds.</div></div>`;
  const rows = plan.map((it) => `<div class="tp-row">
      <span class="tp-day">${it.day}</span>
      <div class="tp-body"><div class="tp-area">${esc(it.area)} <span class="pmut">${esc(it.why)}</span></div>
        <div class="tp-act">${esc(it.action)}</div></div></div>`).join("");
  return `<div class="card tpcard"><div class="card-h">Weekly training plan <em>${esc(p.name)} &middot; do these</em></div>${rows}</div>`;
}

function playerCard(p, b, canDeaths) {
  const multi = Object.entries(p.multi || {}).map(([k, v]) => `${k}Kx${v}`).join("  ") || "--";
  const watch = canDeaths === false ? "" : `<button class="rp-watchd hdr" data-watchd="${esc(String(p.steamid))}" data-wname="${esc(p.name)}"
        title="Watch all of ${esc(p.name)}'s deaths in 3D">&#9654; watch ${p.deaths} deaths</button>`;
  return `<div class="card">
    <div class="card-h">${esc(p.name)} <span class="big ${p.hltv >= b.hltv ? "good" : "bad"}">${p.hltv.toFixed(2)}</span> <em>HLTV 2.0-equiv</em>${watch}</div>
    <div class="roleline">${(APP._roleMap || {})[p.steamid] ? `<span class="cfgrole" title="from team config">${esc(APP._roleMap[p.steamid])}</span> ` : ""}Role -- <span class="ct">CT: ${roleText(p, "ct")}</span> | <span class="t">T: ${roleText(p, "t")}</span></div>
    ${roleCoachNote(p)}
    <div class="tiles">
      ${tile("K / A / D", `${p.kills} / ${p.assists} / ${p.deaths}`, null)}
      ${tile("ADR", p.adr, b.adr)}
      ${tile("KAST", p.kast + "%", b.kast, true, v => v)}
      ${tile("K/D", p.kd, b.kd)}
      ${tile("HS%", p.hs_pct + "%", b.hs, true, v => v)}
      ${tile("KPR", p.kpr, b.kpr)}
      ${tile("DPR", p.dpr, b.dpr, false)}
      ${tile("Util dmg/rd", p.udr, b.udr)}
      ${tile("Opening W:L", `${p.open_k}:${p.open_d}`, null)}
      ${tile("Opening win%", p.open_wr + "%", b.open_wr, true, v => v)}
      ${tile("Traded death%", p.traded_pct + "%", b.trade_pct, true, v => v)}
      ${tile("Enemies flashed", p.enemy_flashed, null)}
      ${tile("Smokes thrown", p.smokes ?? 0, null)}
      ${tile("Flashes thrown", p.flashes_thrown ?? 0, null)}
      ${tile("Mollies thrown", p.molotovs ?? 0, null)}
      ${tile("HE thrown", p.hes ?? 0, null)}
      ${tile("Util / round", p.util_pr ?? 0, null)}
    </div>
    ${utilRatingBlock(p)}
    ${sideBuyBlock(p)}
    <div class="sub">Multi-kills: ${multi} | Avg blind inflicted ${p.avg_blind}s | Team-flashes ${p.team_flashed}</div>
  </div>`;
}

function sideBuyBlock(p) {
  const s = p.sides || {};
  const side = (x, lbl, cls) => x && x.rounds
    ? `<span class="${cls}"><b>${lbl}</b> ${x.k}-${x.d} | KD ${x.kd} | ADR ${x.adr} | KAST ${x.kast}%</span>`
    : `<span class="${cls}"><b>${lbl}</b> --</span>`;
  const buys = p.buys || {};
  const order = ["pistol", "full", "force", "light", "eco"];
  const buyTxt = order.filter(k => buys[k]).map(k =>
    `${k} ${buys[k].rounds}r <span class="${buys[k].win_pct >= 50 ? "good" : "bad"}">${buys[k].win_pct}%W</span>`).join(" | ") || "--";
  const t = p.trades || {};
  const tradeTxt = (t.trade_k_5s != null)
    ? `Trades: ${t.trade_k_5s} made (${t.trade_k_1s} fast) | ${t.traded_d_5s} traded${t.avg_trade_dist ? ` | avg ${t.avg_trade_dist}u` : ""}`
    : "";
  return `<div class="splitrow">${side(s.ct, "CT", "ct")} ${side(s.t, "T", "t")}</div>
    <div class="sub">Buys -- ${buyTxt}</div>
    ${tradeTxt ? `<div class="sub">${tradeTxt}</div>` : ""}`;
}

// --- #47 skill pillars (Aim/Utility/Positioning) + bands --------------------
// The server bakes p.subratings in for fresh parses (see subratings.py); for older
// caches that predate that field we recompute it here from the same player stats.
// KEEP IN SYNC WITH subratings.py (anchors/weights/bands are mirrored exactly).
const SR_AIM = [
  ["counter_strafe", "Counter-strafe", [30, 55, 80], 0.26, "%"],
  ["adr", "ADR", [55, 80, 105], 0.24, ""],
  ["hs_pct", "Headshot", [25, 50, 70], 0.22, "%"],
  ["kpr", "Kills/round", [0.50, 0.68, 0.95], 0.16, ""],
  ["open_wr", "Opening WR", [40, 52, 65], 0.12, "%"],
];
const SR_UTIL = [
  ["udr", "Util dmg/round", [3, 8, 16], 0.45, ""],
  ["flashes_pr", "Enemy flashes/round", [0.20, 0.50, 1.0], 0.30, ""],
  ["util_pr", "Util thrown/round", [1.5, 3.5, 6.0], 0.25, ""],
];
const SR_POS = [
  ["kast", "KAST", [55, 70, 82], 0.34, "%"],
  ["traded_pct", "Traded-death", [8, 20, 35], 0.26, "%"],
  ["dpr", "Deaths/round", [0.80, 0.64, 0.50], 0.22, ""],
  ["open_d_pr", "Opening deaths/round", [0.22, 0.12, 0.05], 0.18, ""],
];
const SR_BANDS = [[90, "S", "Elite"], [80, "A", "Excellent"], [68, "B", "Solid"],
  [52, "C", "Average"], [38, "D", "Below avg"], [0, "F", "Weak"]];

function srInterp(v, weak, good, elite) {
  const incr = elite >= weak;
  const seg = (a, sa, b, sb) => (b === a ? sa : sa + (v - a) * (sb - sa) / (b - a));
  let s;
  if (incr) s = v <= good ? seg(weak, 40, good, 70) : seg(good, 70, elite, 92);
  else s = v >= good ? seg(weak, 40, good, 70) : seg(good, 70, elite, 92);
  return Math.max(0, Math.min(100, s));
}
function srBand(score) {
  if (score == null) return ["--", "Not enough data"];
  for (const [lo, l, lab] of SR_BANDS) if (score >= lo) return [l, lab];
  return ["F", "Weak"];
}
function srPillar(p, cfg, der, drop) {
  const metrics = []; let num = 0, w = 0;
  for (const [key, label, [weak, good, elite], weight, unit] of cfg) {
    if (drop.has(key)) continue;
    const val = der[key] != null ? der[key] : p[key];
    if (val == null) continue;
    const sc = srInterp(+val, weak, good, elite);
    metrics.push({ key, label, value: Math.round(+val * 100) / 100, good, unit, score: Math.round(sc) });
    num += sc * weight; w += weight;
  }
  if (w <= 0) return null;
  return [metrics, num / w];
}
function computeSubratingsJS(p) {
  const rp = Math.max(1, p.rounds_played || 1);
  const der = { flashes_pr: (p.enemy_flashed || 0) / rp, open_d_pr: (p.open_d || 0) / rp };
  const aimDrop = new Set();
  if (p.counter_strafe == null) aimDrop.add("counter_strafe");
  if (((p.open_k || 0) + (p.open_d || 0)) < 4) aimDrop.add("open_wr");
  if ((p.kills || 0) < 4) aimDrop.add("hs_pct");
  const posDrop = new Set();
  if ((p.deaths || 0) < 4) posDrop.add("traded_pct");
  const res = {};
  for (const [name, cfg, drop] of [["aim", SR_AIM, aimDrop],
    ["utility", SR_UTIL, new Set()], ["positioning", SR_POS, posDrop]]) {
    const r = srPillar(p, cfg, der, drop);
    if (!r) { res[name] = { score: null, band: "--", label: "Not enough data", confidence: "low", metrics: [] }; continue; }
    let [metrics, score] = r;
    if (name === "utility" && rp) {
      const pen = Math.max(0, Math.min(15, (p.team_flashed || 0) / rp * 30));
      score = Math.max(0, Math.min(100, score - pen));
    }
    const [band, label] = srBand(score);
    res[name] = { score: Math.round(score), band, label, confidence: rp >= 8 ? "med" : "low", metrics };
  }
  return res;
}

function skillsCard(p) {
  const sr = (p.subratings && p.subratings.aim) ? p.subratings : computeSubratingsJS(p);
  const order = [["aim", "Aim"], ["utility", "Utility"], ["positioning", "Positioning"]];
  const pill = ([key, lbl]) => {
    const d = sr[key];
    if (!d) return "";
    const band = d.band || "--";
    const score = d.score == null ? "--" : d.score;
    const bars = (d.metrics || []).map(m => {
      const w = Math.max(2, Math.min(100, m.score));
      const tone = m.score >= 68 ? "good" : (m.score < 45 ? "bad" : "");
      return `<div class="sk-m"><span class="sk-ml">${esc(m.label)}</span>
        <span class="sk-bar"><i class="${tone}" style="width:${w}%"></i></span>
        <span class="sk-mv" title="solid target ${m.good}${m.unit || ""}">${m.value}${m.unit || ""}</span></div>`;
    }).join("");
    const conf = d.confidence === "low"
      ? `<span class="sk-conf" title="thin sample -- treat as directional">low confidence</span>` : "";
    return `<div class="sk-pillar">
      <div class="sk-h"><span class="sk-name">${lbl}</span>
        <span class="sk-band b-${band}" title="${esc(d.label || "")}">${band}</span>
        <span class="sk-score">${score}</span>${conf}</div>
      <div class="sk-ms">${bars || '<div class="sub">Not enough data</div>'}</div></div>`;
  };
  return `<div class="card skillscard">
    <div class="card-h">Skill breakdown <em>Aim / Utility / Positioning</em></div>
    ${order.map(pill).join("")}
    <div class="sub">0-100 vs a solid (FACEIT-10) target: B (~70) = on benchmark, S = elite. From this match only -- directional.</div>
  </div>`;
}

function evidenceText(ic) {
  // turn the machine-readable evidence dict into a short human "why this was flagged" line
  const e = ic.evidence;
  if (!e || typeof e !== "object") return "";
  if (e.event === "opening_death")
    return `${e.victim} died first to ${e.attacker}; no trade within ${e.trade_window_s}s`;
  if (e.metric === "trade_opp_pct")
    return `${e.chances} deaths with a teammate in range, ${e.traded} traded / ${e.failed} not`;
  if (e.metric && e.value != null) {
    let s = `${e.metric} = ${e.value}`;
    if (e.threshold != null) s += ` (flagged below ${e.threshold})`;
    else if (e.benchmark != null) s += ` (target ~${e.benchmark})`;
    if (e.sample != null) s += `, n=${e.sample}`;
    return s;
  }
  return e.note || "";
}

function insightRow(p, ic) {
  const conf = ic.confidence || "med";
  const jumpable = ic.round != null;
  const why = evidenceText(ic);
  const mark = ic.polarity === "good" ? "+" : (ic.severity >= 3 ? "x" : ic.severity === 2 ? "!" : "-");
  return `<div class="insight sev${ic.severity}${ic.polarity === "good" ? " good" : ""}">
    <div class="ix">${mark}</div>
    <div class="itext">${esc(ic.text)}
      <span class="conf conf-${conf}" title="${esc(ic.confidence_reason || "confidence")}">${conf}</span>
      ${why ? `<div class="iwhy">Why: ${esc(why)}</div>` : ""}</div>
    ${jumpable ? `<button class="ijump" data-jump="${p.steamid}|${ic.round}|${ic.tick}">\u25b6 R${ic.round}</button>` : ""}
  </div>`;
}

function insightsCard(p, A) {
  const all = (A.insights[p.steamid] || []);
  const issues = all.filter(i => i.polarity !== "good").sort((a, b) => b.severity - a.severity);
  const goods = all.filter(i => i.polarity === "good");
  const issuesHtml = issues.length ? issues.map(ic => insightRow(p, ic)).join("")
    : `<div class="empty">No major mistakes flagged -- clean game.</div>`;
  const goodsHtml = goods.length
    ? `<div class="card-h" style="margin-top:12px">What went right <em>${goods.length}</em></div>`
      + goods.map(ic => insightRow(p, ic)).join("")
    : "";
  const note = A.meta ? `<div class="sub" style="margin-top:8px">Ratings, roles & buy types are transparent approximations -- not official HLTV/Leetify values.</div>` : "";
  return `<div class="card"><div class="card-h">What to fix <em>${issues.length} flags</em></div>${issuesHtml}${goodsHtml}${note}</div>`;
}

function contextCard(p) {
  const c = p.context;
  if (!c || !c.sub) return "";
  const subs = [["Kills", "kills"], ["Damage", "damage"], ["Survival", "survival"],
    ["KAST", "kast"], ["Multi", "multi"], ["Swing", "swing"]];
  const bar = (v) => {
    const w = Math.max(4, Math.min(100, (v / 1.8) * 100));
    const col = v >= 1.15 ? "#7fd27f" : v >= 0.9 ? "var(--accent)" : "#e87878";
    return `<div class="cx-bar"><i style="width:${w}%;background:${col}"></i></div>`;
  };
  const rows = subs.map(([lbl, k]) =>
    `<div class="cx-row"><span class="cx-l">${lbl}</span>${bar(c.sub[k])}<span class="cx-v">${c.sub[k]}</span></div>`).join("");
  const eco = c.eco_factor > 1.05 ? `faced ${c.eco_factor}x enemy equip (out-gunned)`
    : c.eco_factor < 0.95 ? `faced ${c.eco_factor}x enemy equip (better-armed)` : "even economy";
  return `<div class="card"><div class="card-h">Context rating <em>HLTV 3.0-inspired</em></div>
    <div class="cx-top"><span class="cx-big">${c.context_rating}</span>
      <span class="sub">eco-adjusted, lobby-relative (1.0 = match avg). ${eco}.</span></div>
    ${rows}
    <div class="sub" style="margin-top:6px">Transparent approximation of HLTV's 6 sub-ratings -- not the official Rating 3.0.</div></div>`;
}

function radarCard(p, b) {
  // 6 axes normalized so benchmark sits at 0.62 of the radius
  const axes = [
    ["Rating", p.hltv, b.hltv], ["ADR", p.adr, b.adr], ["KAST", p.kast, b.kast],
    ["Opening", p.open_wr, b.open_wr], ["Trades", p.traded_pct, b.trade_pct], ["Utility", p.udr, b.udr],
  ];
  const cx = 130, cy = 120, R = 92, n = axes.length;
  const pt = (i, r) => {
    const ang = -Math.PI / 2 + i * 2 * Math.PI / n;
    return [cx + Math.cos(ang) * r, cy + Math.sin(ang) * r];
  };
  const norm = (v, bench) => Math.max(0.05, Math.min(1.08, (v / (bench / 0.62 || 1))));
  const grid = [0.25, 0.5, 0.75, 1].map(g =>
    `<polygon points="${axes.map((_, i) => pt(i, R * g).join(",")).join(" ")}" class="rg"/>`).join("");
  const benchPoly = axes.map((_, i) => pt(i, R * 0.62).join(",")).join(" ");
  const playerPoly = axes.map((a, i) => pt(i, R * norm(a[1], a[2])).join(",")).join(" ");
  const labels = axes.map((a, i) => { const [x, y] = pt(i, R + 16); return `<text x="${x}" y="${y}" class="rl">${a[0]}</text>`; }).join("");
  return `<div class="card"><div class="card-h">Skill profile <em>you vs FACEIT-10</em></div>
    <svg viewBox="0 0 260 250" class="radar">
      ${grid}
      <polygon points="${benchPoly}" class="rbench"/>
      <polygon points="${playerPoly}" class="rplayer"/>
      ${labels}
    </svg>
    <div class="rkey"><span class="k1">You</span> <span class="k2">FACEIT-10 target</span></div>
  </div>`;
}

function zonesCard(p) {
  // #62: richer per-callout table (side split + opening) when baked; else the flat zone K/D.
  if (p.position_stats && p.position_stats.length) return positionsCard(p);
  const zs = Object.entries(p.zones || {}).map(([z, v]) => ({ z, k: v.k, d: v.d, n: v.k + v.d }))
    .filter(x => x.n >= 2).sort((a, b) => b.n - a.n).slice(0, 9);
  if (!zs.length) return `<div class="card"><div class="card-h">Map zones</div><div class="empty">Not enough data.</div></div>`;
  const rows = zs.map(x => {
    const diff = x.k - x.d;
    const cls = diff > 0 ? "good" : diff < 0 ? "bad" : "neu";
    return `<div class="zrow"><span class="zn">${esc(x.z)}</span>
      <span class="zkd ${cls}">${x.k}-${x.d}</span>
      <span class="zbar"><i class="${cls}" style="width:${Math.min(100, x.n * 8)}%"></i></span></div>`;
  }).join("");
  return `<div class="card"><div class="card-h">Map zones <em>kills-deaths by area</em></div>${rows}${posMapBtn(p)}</div>`;
}

// #62b: "see these spots on the map" -- plots the player's death/kill positions on the 2D radar.
function posMapBtn(p) {
  return `<button class="pz-mapbtn" data-posmap="${p.steamid}" data-pname="${esc(p.name)}"
    title="Plot where you died (red x) and got kills (green) on the map">&#128205; Show these spots on the map</button>`;
}

// #62 per-position (callout) breakdown: K-D by area, split CT/T, with opening-duel involvement.
function positionsCard(p) {
  const rows = (p.position_stats || []).filter(r => (r.k + r.d) >= 2).slice(0, 10).map(r => {
    const diff = r.k - r.d;
    const cls = diff > 0 ? "good" : diff < 0 ? "bad" : "neu";
    const side = [];
    if (r.ct_k || r.ct_d) side.push(`<span class="ct">CT ${r.ct_k}-${r.ct_d}</span>`);
    if (r.t_k || r.t_d) side.push(`<span class="t">T ${r.t_k}-${r.t_d}</span>`);
    const open = (r.open_k || r.open_d)
      ? `<span class="pz-open" title="opening duels won-lost here">${r.open_k}-${r.open_d}</span>` : "";
    return `<tr><td class="pz-zn">${esc(r.zone)}</td>
      <td class="pz-kd ${cls}">${r.k}-${r.d}</td>
      <td class="pz-side">${side.join(" ")}</td>
      <td class="pz-open-c">${open}</td>
      <td class="pz-acts"><button class="pz-act" data-callout-act="deaths" data-zone="${esc(r.zone)}" title="Review deaths at ${esc(r.zone)}">Deaths</button><button class="pz-act" data-callout-act="utility" data-zone="${esc(r.zone)}" title="Show saved utility to ${esc(r.zone)}">Utility</button><button class="pz-act" data-callout-act="throws" data-zone="${esc(r.zone)}" title="Find team throws landing at ${esc(r.zone)}">Throws</button><button class="pz-act" data-callout-act="goal" data-zone="${esc(r.zone)}" title="Create a practice goal for ${esc(r.zone)}">Goal</button><button class="pz-act" data-callout-act="note" data-zone="${esc(r.zone)}" title="Add a review note at ${esc(r.zone)}">Note</button></td></tr>`;
  }).join("");
  if (!rows) return `<div class="card"><div class="card-h">Positions</div><div class="empty">Not enough data.</div></div>`;
  return `<div class="card"><div class="card-h">Positions <em>K-D by callout &middot; side &middot; opening</em></div>
    <table class="pz-table"><thead><tr><th>Callout</th><th>K-D</th><th>By side</th><th title="opening duels won-lost">Open</th><th>Actions</th></tr></thead>
    <tbody>${rows}</tbody></table>${posMapBtn(p)}</div>`;
}

function overviewCard(A) {
  // group by replay team (stable) using demo.players team
  const teamOf = {};
  APP.demo.players.forEach(p => teamOf[p.steamid] = p.team);
  const rowsFor = (tm) => A.players.filter(p => teamOf[p.steamid] === tm)
    .sort((a, b) => b.hltv - a.hltv).map(p => `
      <tr data-selsid="${p.steamid}" class="${p.steamid === A.players[SEL].steamid ? "sel" : ""}">
        <td class="nm">${esc(p.name)}</td><td>${p.hltv.toFixed(2)}</td>
        <td>${p.kills}/${p.assists}/${p.deaths}</td><td>${p.adr}</td><td>${p.kast}%</td>
        <td>${p.udr}</td><td>${p.open_k}:${p.open_d}</td><td>${p.traded_pct}%</td></tr>`).join("");
  const head = `<tr><th>Player</th><th>HLTV</th><th>K/A/D</th><th>ADR</th><th>KAST</th><th>UDR</th><th>Open</th><th>Trd%</th></tr>`;
  return `<div class="card wide"><div class="card-h">Match overview <em>click a player</em></div>
    <table class="sb"><thead>${head}</thead>
      <tbody class="ct">${rowsFor(3)}</tbody>
      <tbody class="sep"><tr><td colspan="8"></td></tr></tbody>
      <tbody class="t">${rowsFor(2)}</tbody></table></div>`;
}

// --- jump to replay ---------------------------------------------------------
function jump(sid, round, tick) {
  closeAnalytics(APP);
  if (APP.view3d && APP.view3d.active) APP.exit3D();
  const idx = APP._sidToIdx[sid];
  if (tick != null) APP.t = tick / (APP.demo.analytics.tickrate || 64);
  else if (round != null) {
    const r = APP.demo.rounds.find(x => x.number === round);
    if (r) APP.t = r.freeze_end_t ?? r.start_t;
  }
  APP.t = Math.max(0, Math.min(APP.demo.duration, APP.t - 3));  // start a moment before
  if (idx != null && idx >= 0) APP.setSpectate(idx);
  APP.playing = true; $("playPause").textContent = "\u23f8";
}
