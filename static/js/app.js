// app.js -- controller: playback clock, upload, spectator, input, HUD.
import { Cs2Demo } from "./demo.js";
import { Radar2D } from "./radar2d.js";
import { View3D } from "./view3d.js";
import { openAnalytics, closeAnalytics } from "./analytics.js";

const $ = (id) => document.getElementById(id);
let _activeUploads = 0;   // in-flight demo upload requests (beforeunload guard: don't lose them)
// True while any tracked parse job is still queued/parsing/analyzing (set by _trackJobs' poll).
function _hasActiveJobs() {
  const live = App._liveJobs;
  if (!live) return false;
  for (const st of live.values()) if (["queued", "parsing", "analyzing"].includes(st)) return true;
  return false;
}
const fmt = (s) => {
  s = Math.max(0, s);
  const m = Math.floor(s / 60), sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
};
const WEAP = (w) => (w || "").replace(/^weapon_/, "").replace(/_silencer$/, "-s");
// First-person HUD weapon readout: clean, uppercase, HUD-style (no icons). p.weapon is a
// lowercase name like "ak-47" / "m4a1-s" / "smoke grenade" / "butterfly knife" / "c4 explosive".
const fpWeaponName = (w) => {
  let s = (w || "").toLowerCase().replace(/^weapon_/, "");
  if (!s) return "--";
  if (s.includes("knife") || s === "bayonet") return "KNIFE";
  if (s.startsWith("c4")) return "C4";
  s = s.replace("high explosive grenade", "he grenade")
       .replace("incendiary grenade", "incendiary")
       .replace("smoke grenade", "smoke")
       .replace("decoy grenade", "decoy")
       .replace("zeus x27", "zeus");
  return s.toUpperCase();
};
const DEFAULT_3D_HINT = "Click to look around | <b>WASD</b> move | <b>E/Q</b> up/down | <b>Space</b> play/pause | <b>Shift</b> fast | <b>Esc</b> release mouse";

// Pro billing periods -- the single source for both the landing pricing card and the in-app upgrade
// modal. `mo` = effective per-month, `bill` = how it's charged. When Stripe goes live, add a `price`
// (Stripe Price ID) per period and point the upgrade CTA at checkout for the selected key.
// Fallback only -- the live prices come from the server (me.pricing, editable in the admin panel).
const PRO_PLANS = {
  monthly: { key: "monthly", label: "Monthly", months: 1, mo: "$10", bill: "billed monthly", save_pct: 0 },
  q:       { key: "q", label: "3-Monthly", months: 3, mo: "$9", bill: "$27 billed every 3 months · save 10%", save_pct: 10 },
  h:       { key: "h", label: "6-Monthly", months: 6, mo: "$8.50", bill: "$51 billed every 6 months · save 15%", save_pct: 15 },
  year:    { key: "year", label: "Yearly", months: 12, mo: "$8", bill: "$96 billed yearly · save 20%", save_pct: 20 },
};

// Fixed grenade rack for the loadout: one dedicated slot per type, lit when held.
const NADE_ORDER = ["flash", "smoke", "he", "molotov", "decoy"];
const NADE_NAME = { flash: "Flashbang", smoke: "Smoke", he: "HE Grenade", molotov: "Molotov / Incendiary", decoy: "Decoy" };
const NADE_ICON = {
  flash: '<svg viewBox="0 0 16 16"><g stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M8 1.5V4.5M8 11.5V14.5M1.5 8H4.5M11.5 8H14.5M3.5 3.5l2 2M10.5 10.5l2 2M12.5 3.5l-2 2M5.5 10.5l-2 2"/></g><circle cx="8" cy="8" r="2" fill="currentColor"/></svg>',
  smoke: '<svg viewBox="0 0 16 16" fill="currentColor"><circle cx="5.4" cy="9.4" r="3.1"/><circle cx="10.6" cy="9.4" r="3.1"/><circle cx="8" cy="6.8" r="3.4"/></svg>',
  he: '<svg viewBox="0 0 16 16" fill="currentColor"><rect x="6.6" y="1.4" width="2.8" height="2.6" rx="0.5"/><circle cx="8" cy="9.4" r="4.7"/></svg>',
  molotov: '<svg viewBox="0 0 16 16" fill="currentColor"><path d="M8 1.5C5 4.5 4 6.5 4 9a4 4 0 0 0 8 0c0-1.4-.6-2.7-1.5-3.7.1 1.2-.6 1.9-1.3 1.9-1 0-1.5-1-1-2.4.3-1 .6-2.3-.2-3.3z"/></svg>',
  decoy: '<svg viewBox="0 0 16 16"><circle cx="6.4" cy="9.6" r="4.1" fill="currentColor"/><rect x="5" y="1.6" width="2.6" height="2.4" rx="0.5" fill="currentColor"/><g stroke="currentColor" stroke-width="1.3" fill="none" stroke-linecap="round"><path d="M11.4 6.3a3.4 3.4 0 0 1 0 4.8"/><path d="M13 5a5.8 5.8 0 0 1 0 7.4"/></g></svg>',
};

// Weapon -> silhouette shape. Icon-only in the loadout (exact name is the hover title).
const GUN_SHAPE = (raw) => {
  const w = (raw || "").toLowerCase();
  if (/\bak-?47\b/.test(w)) return "ak";
  if (/m4a1|m4a4|galil|famas/.test(w)) return "rifle";
  if (/\baug\b|sg ?553|sg553/.test(w)) return "scoped";
  if (/awp|ssg ?08|scout/.test(w)) return "awp";
  if (/scar-?20|g3sg1/.test(w)) return "autosniper";
  if (/desert eagle|deagle|\br8\b|revolver/.test(w)) return "deagle";
  if (/dual|berett|elite/.test(w)) return "dualies";
  if (/p90/.test(w)) return "p90";
  if (/mac-?10|mp9|mp7|mp5|ump|bizon/.test(w)) return "smg";
  if (/nova|xm1014|mag-?7|sawed/.test(w)) return "shotgun";
  if (/m249|negev/.test(w)) return "lmg";
  if (/c4|bomb/.test(w)) return "c4";
  return "pistol";   // glock/usp/p2000/p250/five-seven/cz/tec-9 + sensible fallback
};
const SHAPE_SVG = {
  ak: '<svg viewBox="0 0 26 12" fill="currentColor"><rect x="3" y="4.6" width="19" height="2.2"/><rect x="1.5" y="4.2" width="3" height="3" rx=".4"/><rect x="9" y="6.4" width="1.7" height="3"/><path d="M12 6.4h2.5l-.5 3.6q-1.4.3-2.2-.5z"/><rect x="20" y="5" width="4.5" height="1.2"/></svg>',
  rifle: '<svg viewBox="0 0 26 12" fill="currentColor"><rect x="3" y="4.6" width="19" height="2.2"/><rect x="1.5" y="4.2" width="3" height="3" rx=".4"/><rect x="9" y="6.4" width="1.7" height="3"/><rect x="12" y="6.4" width="2.3" height="3.2" rx=".3"/><rect x="20" y="5" width="4.5" height="1.2"/></svg>',
  scoped: '<svg viewBox="0 0 26 12" fill="currentColor"><rect x="3" y="5" width="18" height="2.2"/><rect x="1.5" y="4.6" width="3" height="3" rx=".4"/><rect x="9" y="7" width="1.7" height="2.6"/><rect x="12" y="7" width="2.2" height="2.8" rx=".3"/><rect x="8" y="2.8" width="7" height="1.6" rx=".8"/></svg>',
  awp: '<svg viewBox="0 0 26 12" fill="currentColor"><rect x="2" y="5.2" width="23" height="1.7"/><rect x="1" y="4.6" width="4" height="3.4" rx=".5"/><rect x="8.5" y="6.8" width="1.6" height="3"/><rect x="9" y="2.6" width="8" height="1.6" rx=".8"/></svg>',
  autosniper: '<svg viewBox="0 0 26 12" fill="currentColor"><rect x="2" y="5" width="21" height="2"/><rect x="1" y="4.5" width="3.5" height="3.2" rx=".5"/><rect x="9" y="7" width="1.7" height="2.8"/><rect x="12" y="7" width="2.2" height="3" rx=".3"/><rect x="8" y="3" width="8" height="1.5" rx=".7"/></svg>',
  deagle: '<svg viewBox="0 0 26 12" fill="currentColor"><rect x="7" y="3.8" width="11" height="2.8" rx=".4"/><path d="M9 6.4h3.2l-.8 4.2h-3z"/></svg>',
  pistol: '<svg viewBox="0 0 26 12" fill="currentColor"><rect x="8" y="4" width="9" height="2.4" rx=".4"/><path d="M9.5 6.2h2.8l-.7 3.8h-2.6z"/></svg>',
  dualies: '<svg viewBox="0 0 26 12" fill="currentColor"><rect x="2" y="4.2" width="7" height="2.2" rx=".3"/><path d="M3 6.2h2.4l-.6 3.4h-2.2z"/><rect x="14" y="4.2" width="7" height="2.2" rx=".3"/><path d="M15 6.2h2.4l-.6 3.4h-2.2z"/></svg>',
  smg: '<svg viewBox="0 0 26 12" fill="currentColor"><rect x="4" y="4.6" width="14" height="2.2"/><rect x="2.5" y="4.3" width="2.5" height="2.8" rx=".4"/><rect x="8" y="6.6" width="1.6" height="3.4"/><rect x="6" y="6.6" width="1.3" height="2.4"/><rect x="16" y="5" width="3.5" height="1.2"/></svg>',
  p90: '<svg viewBox="0 0 26 12" fill="currentColor"><path d="M3 6.6q0-2.2 3-2.2h12l2.5 1.6v1.6h-3l-1 2h-9q-4.5 0-4.5-3z"/><rect x="7" y="3.2" width="9" height="1.7" rx=".6"/></svg>',
  shotgun: '<svg viewBox="0 0 26 12" fill="currentColor"><rect x="3" y="4.6" width="19" height="2.6"/><rect x="1.5" y="4.4" width="3" height="3.2" rx=".4"/><rect x="9" y="7" width="1.7" height="2.6"/><rect x="13" y="7.2" width="5" height="1.2" rx=".4"/></svg>',
  lmg: '<svg viewBox="0 0 26 12" fill="currentColor"><rect x="3" y="4.2" width="19" height="2.6"/><rect x="1.5" y="3.8" width="3" height="3.4" rx=".4"/><rect x="9" y="6.8" width="1.7" height="2.8"/><rect x="11.5" y="6.8" width="4.5" height="3.6" rx=".4"/><rect x="20" y="4.6" width="4.5" height="1.3"/></svg>',
  c4: '<svg viewBox="0 0 26 12" fill="currentColor"><rect x="9" y="2.6" width="8" height="7" rx="1"/><rect x="11" y="1.2" width="4" height="1.6" rx=".4"/></svg>',
};
const GUN_SVG = (w) => SHAPE_SVG[GUN_SHAPE(w)] || SHAPE_SVG.pistol;
// Armor icon: shield = kevlar; shield + helmet dome = kevlar + helmet. Kit = defuse kit.
const ARMOR_SVG = {
  vest: '<svg viewBox="0 0 16 16" fill="currentColor"><path d="M8 2 L13 4 V8 C13 11.5 8 14 8 14 C8 14 3 11.5 3 8 V4 Z"/></svg>',
  full: '<svg viewBox="0 0 16 16" fill="currentColor"><path d="M8 6 L12.4 7.6 V10.2 C12.4 12.8 8 14.8 8 14.8 C8 14.8 3.6 12.8 3.6 10.2 V7.6 Z"/><path d="M4.4 5.6 a3.6 3.6 0 0 1 7.2 0 Z"/></svg>',
};
const KIT_SVG = '<svg viewBox="0 0 16 16" fill="currentColor"><rect x="3" y="6.5" width="10" height="6" rx="1"/><path d="M6 6.5 V5 a2 2 0 0 1 4 0 V6.5" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>';

const App = {
  demo: null,
  maps: {},
  radar: null,
  t: 0,
  playing: false,
  speed: 1,
  lastNow: 0,
  scrubbing: false,
  tlMode: "round",   // timeline scope: "round" (slider = current round) | "match" (whole demo)
  _tlRound: -1,      // round number the slider is currently scoped to (round mode rebasing)
  rows: {},          // idx -> {el, parent}

  async init() {
    this.radar = new Radar2D($("canvas"));
    // a 2nd radar drives the 3D-overlay minimap: whole-map fit, player dots + live utility
    // (smokes/mollies/in-flight nades) so you get a top-down util read while flying in 3D.
    this.miniRadar = new Radar2D($("miniCanvas"));
    Object.assign(this.miniRadar, { showNames: false, showTrajectories: true, showTraces: false, showUtil: true });
    this._miniOn = true;     // minimap enabled (settings toggle)
    this._miniZoom = 1;      // 1 = whole map; >1 zooms in + follows the spectated player / 3D cam
    this._miniSize = 1;      // minimap canvas size multiplier (settings slider); base 208px
    this.miniRadar._updateFollow = () => {};   // we drive cam/zoom ourselves (its auto-zoom would fight the slider)
    this.view3d = new View3D($("canvas3d"));
    this.loadSettings();   // apply saved prefs (overriding the defaults set above)
    this._applyMiniSize();   // size the minimap canvas from the saved/default multiplier
    window.addEventListener("resize", () => {
      this.radar.resize(); this.radar.fit(); this.view3d.resize(); this.resizeDraw();
      if (this.view3d.active && this.miniRadar.map) { this.miniRadar.resize(); this.miniRadar.fit(); }
    });
    // Warn before leaving while a demo is still uploading or parsing (don't silently lose the work).
    window.addEventListener("beforeunload", (e) => {
      if (_activeUploads > 0 || _hasActiveJobs()) { e.preventDefault(); return (e.returnValue = ""); }
    });
    this.maps = await fetch("static/maps/maps.json").then(r => r.json()).catch(() => ({}));
    this.initDraw();
    this.bindUI();
    this.loop();
    this.initAuth();      // resolves auth, then shows the landing (logged-out) or the dashboard
  },

  // Steam login chip. Hidden entirely unless the operator enabled auth (PUBLIC_BASE_URL /
  // AUTH_REQUIRED) -- so a local single-user install looks exactly as it did before.
  async initAuth() {
    const box = $("authBox");
    let me;
    try { me = await fetch("/api/me", { cache: "no-store" }).then(r => r.json()); }
    catch (e) { me = null; }
    this.me = me || {}; this.myTeams = (me && me.teams) || [];
    this.ent = (me && me.entitlements) || null;          // null/absent -> everything allowed
    this.isAdmin = !!(me && me.is_admin);
    this.isHelper = !!(me && (me.is_helper || me.is_admin));   // helpers see the admin panel too
    this._applyGates();                                  // PRO pills on locked buttons (no-op when entitled)
    const loggedIn = !!(me && me.authenticated && me.user);
    const wantLanding = !!(me && me.auth_enabled && !loggedIn);   // logged-out on an auth-enabled site
    if (!this.demo) { if (wantLanding) this.showLanding(); else this.showDashboard(); }
    this._renderAuthBox();
    this._handleBillingReturn();                          // ?checkout=success/cancel, ?portal=return
  },
  // Re-pull /api/me and re-apply entitlements/UI (after a checkout returns and the webhook lands).
  async _refreshMe() {
    let me;
    try { me = await fetch("/api/me", { cache: "no-store" }).then(r => r.json()); }
    catch (e) { return false; }
    this.me = me || {}; this.myTeams = (me && me.teams) || [];
    this.ent = (me && me.entitlements) || null;
    this.isAdmin = !!(me && me.is_admin);
    this.isHelper = !!(me && (me.is_helper || me.is_admin));
    this._applyGates();
    this._renderAuthBox();
    if (document.body.classList.contains("on-dashboard")) this.loadDashboard();
    return !!(me && me.tier === "pro");
  },
  // Stripe sends the user back to /?checkout=success|cancel (or /?portal=return). Toast + refresh; the
  // tier flips via the async webhook, so poll a few times until it lands.
  async _handleBillingReturn() {
    const q = new URLSearchParams(location.search);
    const checkout = q.get("checkout"), portal = q.get("portal");
    if (!checkout && !portal) return;
    history.replaceState(null, "", location.pathname);     // don't re-fire on refresh
    if (checkout === "cancel") { this._toast && this._toast("Checkout canceled — you weren't charged."); return; }
    if (portal === "return") { this._refreshMe(); return; }
    if (checkout === "success") {
      this._toast && this._toast("Payment received — unlocking Pro…");
      for (let i = 0; i < 6; i++) {                         // ~12s: wait for the webhook to grant Pro
        if (await this._refreshMe()) { this._toast && this._toast("You're Pro. Everything's unlocked — enjoy!"); return; }
        await new Promise(r => setTimeout(r, 2000));
      }
      this._toast && this._toast("Payment received. If Pro doesn't show in a minute, refresh the page.");
    }
  },
  // POST to create a Checkout Session for `period` and redirect to Stripe's hosted page.
  async _startCheckout(period) {
    try {
      const r = await fetch("/api/billing/checkout", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ period }) }).then(r => r.json());
      if (r && r.url) { window.location.href = r.url; return; }
      this._toast && this._toast((r && r.error) || "Couldn't start checkout.");
    } catch (e) { this._toast && this._toast("Couldn't reach checkout — try again."); }
  },
  // POST to open the Stripe Customer Portal (manage/cancel) and redirect.
  async _openPortal() {
    try {
      const r = await fetch("/api/billing/portal", { method: "POST" }).then(r => r.json());
      if (r && r.url) { window.location.href = r.url; return; }
      this._toast && this._toast((r && r.error) || "No subscription to manage yet.");
    } catch (e) { this._toast && this._toast("Couldn't open the billing portal."); }
  },
  // Renders the header auth chip from current this.me/isAdmin/etc. Split out of initAuth so the
  // admin "preview as free" lens can re-render it without re-fetching /api/me (which would reset the
  // override).
  _renderAuthBox() {
    const box = $("authBox");
    if (!box) return;
    const me = this.me || {};
    const loggedIn = !!(me.authenticated && me.user);
    if (!me.auth_enabled) { box.hidden = true; box.innerHTML = ""; return; }   // local mode: no login UI
    box.hidden = false;
    if (loggedIn) {
      const u = me.user;
      const av = u.avatar ? `<img class="av" src="${esc(u.avatar)}" alt="">` : "";
      // upgrade entry: "Go Pro" for a free user, a "Pro" chip otherwise (only when tiers are enforced)
      let planBtn = "";
      if (this._canUpgrade()) planBtn = `<button id="goProBtn" class="btn primary sm" title="Upgrade to Pro">Go Pro</button>`;
      else if (me.tiers_enabled && me.tier === "pro") planBtn = `<button id="planChip" class="who-plan" title="Your subscription">&#10022; Pro</button>`;
      box.innerHTML = `<button id="whoBtn" class="who-link" title="Account settings">${av}<span class="who">${esc(u.name || "Player")}</span></button>` +
        planBtn +
        (this.isHelper ? `<button id="adminBtn" class="btn ghost sm" title="${this.isAdmin ? "Admin panel" : "Helper panel"}">${this.isAdmin ? "Admin" : "Helper"}</button>` : "") +
        `<button id="teamsBtn" class="btn ghost sm ${this.entitled("teams") ? "" : "pro-locked"}" title="Teams & workspaces">Teams</button>` +
        `<button id="logoutBtn" class="btn ghost sm" title="Log out">Log out</button>`;
      if ($("whoBtn")) $("whoBtn").onclick = () => this.openAccount();
      if ($("goProBtn")) $("goProBtn").onclick = () => this.openUpgrade();
      if ($("planChip")) $("planChip").onclick = () => this.openUpgrade();
      if (this.isHelper) $("adminBtn").onclick = () => this.openAdmin();
      $("teamsBtn").onclick = () => this.entitled("teams") ? this.openTeams() : this._upsell("teams");
      $("logoutBtn").onclick = async () => {
        try { await fetch("/logout", { method: "POST" }); } catch (e) { /* ignore */ }
        location.reload();
      };
    } else {
      box.innerHTML = `<a class="btn primary sm steam" href="/login/steam">Sign in through Steam</a>`;
    }
  },
  // Admin "view as free user" lens: overrides the client-side entitlement/role state so the whole UI
  // gates exactly as it would for a free non-admin, WITHOUT switching accounts. Purely visual -- the
  // server still sees you as admin (so it's a UI preview, not a true free session). Reload clears it.
  togglePreviewFree(on) {
    if (on) {
      if (!this._realState) {
        this._realState = { ent: this.ent, isAdmin: this.isAdmin, isHelper: this.isHelper,
          tier: this.me.tier, tiers_enabled: this.me.tiers_enabled,
          is_admin: this.me.is_admin, is_helper: this.me.is_helper };
      }
      this.ent = { threeD: false, utility: false, advancedAnalytics: false, goals: false, teams: false };
      this.isAdmin = false; this.isHelper = false;
      this.me.tier = "free"; this.me.tiers_enabled = true;
      this.me.is_admin = false; this.me.is_helper = false;
      this._previewFree = true;
    } else if (this._realState) {
      const r = this._realState;
      this.ent = r.ent; this.isAdmin = r.isAdmin; this.isHelper = r.isHelper;
      this.me.tier = r.tier; this.me.tiers_enabled = r.tiers_enabled;
      this.me.is_admin = r.is_admin; this.me.is_helper = r.is_helper;
      this._realState = null; this._previewFree = false;
    } else { return; }
    if ($("adminModal")) $("adminModal").classList.remove("show");
    this._renderAuthBox();
    this._applyGates();
    this._updatePreviewBanner();
    if (document.body.classList.contains("on-dashboard")) this.loadDashboard();
    if ($("analyticsPanel") && $("analyticsPanel").classList.contains("show")) openAnalytics(this);
  },
  _updatePreviewBanner() {
    const el = $("adminPreviewBanner");
    if (el) el.hidden = !this._previewFree;
  },

  async _refreshTeams() {
    try { this.myTeams = (await fetch("/api/teams", { cache: "no-store" }).then(r => r.json())).teams || []; }
    catch (e) { /* keep last */ }
    return this.myTeams || [];
  },

  openTeams() {
    this.pausePlayback();
    $("teamsTitle").textContent = "Teams & workspaces";
    $("teamsModal").classList.add("show");
    this.renderTeams();
  },

  async renderTeams() {
    const teams = await this._refreshTeams();
    const myUid = this.me && this.me.user && this.me.user.id;
    const rows = teams.length ? teams.map(t => {
      const owner = t.role === "owner";
      const members = (t.members || []).map(m => {
        const tag = m.role === "owner" ? ` <span class="round">(owner)</span>`
          : (m.user_id === myUid ? ` <span class="round">(you)</span>` : "");
        const kick = (owner && m.role !== "owner")
          ? `<button class="tm-kick" data-tid="${t.id}" data-uid="${m.user_id}" data-name="${esc(m.name || "this member")}" title="Remove from team">&times;</button>` : "";
        return `<div class="tm-row"><span class="tm-name">${esc(m.name || "Player")}${tag}</span>${kick}</div>`;
      }).join("");
      const action = owner
        ? `<button class="team-disband btn sm" data-tid="${t.id}" data-name="${esc(t.name)}">Disband team</button>`
        : `<button class="team-leave btn sm" data-tid="${t.id}" data-name="${esc(t.name)}">Leave team</button>`;
      return `<div class="team-row"><div class="team-name">${esc(t.name)}<span class="team-role">${esc(t.role)}</span></div>`
        + `<div class="team-meta">${t.member_count} member${t.member_count === 1 ? "" : "s"}`
        + (t.invite_code ? ` &middot; <button class="team-copycode" data-code="${esc(t.invite_code)}">Copy invite code</button>` : "")
        + `</div><div class="team-members">${members}</div><div class="team-actions">${action}</div></div>`;
    }).join("")
      : `<div class="lib-empty">You're not in a team yet. Create one (you'll get an invite code to share), or join with a code a teammate gave you.</div>`;
    $("teamsBody").innerHTML = `<div class="teams-list">${rows}</div>`
      + `<div class="teams-forms">`
      + `<div class="tf-row"><input id="teamNewName" placeholder="New team name" maxlength="80"><button id="teamCreate" class="btn primary sm">Create</button></div>`
      + `<div class="tf-row"><input id="teamJoinCode" placeholder="Invite code"><button id="teamJoin" class="btn sm">Join</button></div>`
      + `</div>`;
    const after = () => { this.renderTeams(); if (document.body.classList.contains("on-dashboard")) this.loadDashboard(); };
    $("teamCreate").onclick = async () => {
      const name = $("teamNewName").value.trim(); if (!name) return;
      await fetch("/api/teams", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
      after();
    };
    $("teamJoin").onclick = async () => {
      const code = $("teamJoinCode").value.trim(); if (!code) return;
      const r = await fetch("/api/teams/join", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ invite_code: code }) }).then(r => r.json()).catch(() => null);
      if (r && r.error) { this._toast && this._toast(r.error); return; }
      after();
    };
    $("teamsBody").querySelectorAll(".tm-kick").forEach(b => b.onclick = async () => {
      const ok = await this.askConfirm("Remove member?",
        `<div class="cf-line">Remove <b>${esc(b.dataset.name)}</b> from the team?</div>`
        + `<div class="cf-line cf-mut">Demos they shared to this team go back to private.</div>`, "Remove");
      if (!ok) return;
      await fetch(`/api/teams/${b.dataset.tid}/remove`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: +b.dataset.uid }) }).catch(() => {});
      after();
    });
    $("teamsBody").querySelectorAll(".team-leave").forEach(b => b.onclick = async () => {
      const ok = await this.askConfirm("Leave team?",
        `<div class="cf-line">Leave <b>${esc(b.dataset.name)}</b>?</div>`
        + `<div class="cf-line cf-mut">You'll stop seeing demos shared with this team, and yours shared to it go back to private.</div>`, "Leave");
      if (!ok) return;
      await fetch(`/api/teams/${b.dataset.tid}/leave`, { method: "POST" }).catch(() => {});
      after();
    });
    $("teamsBody").querySelectorAll(".team-disband").forEach(b => b.onclick = async () => {
      const ok = await this.askConfirm("Disband team?",
        `<div class="cf-line">Disband <b>${esc(b.dataset.name)}</b> for everyone?</div>`
        + `<div class="cf-line cf-mut">The team is deleted and every demo shared to it reverts to private. This can't be undone.</div>`, "Disband");
      if (!ok) return;
      await fetch(`/api/teams/${b.dataset.tid}`, { method: "DELETE" }).catch(() => {});
      after();
    });
    $("teamsBody").querySelectorAll(".team-copycode").forEach(b => b.onclick = async () => {
      try { await navigator.clipboard.writeText(b.dataset.code); this._toast && this._toast("Invite code copied"); }
      catch (e) { this._toast && this._toast("Invite code: " + b.dataset.code); }   // clipboard blocked -> show it
    });
  },

  // Share one library demo with a team (owner-only; the API enforces it).
  shareDemo(id) {
    const teams = this.myTeams || [];
    if (!teams.length) { this.openTeams(); return; }
    $("teamsTitle").textContent = "Share this match with a team";
    const picks = teams.map(t => `<button class="btn sm team-share-pick" data-tid="${t.id}">${esc(t.name)}</button>`).join("");
    $("teamsBody").innerHTML = `<div class="lib-empty">Team members will see this match in their library. Only you (the owner) can share or unshare it.</div>`
      + `<div class="teams-list share-pick">${picks}<button class="btn ghost sm team-share-pick" data-tid="">Make private</button></div>`;
    $("teamsBody").querySelectorAll(".team-share-pick").forEach(b => b.onclick = async () => {
      const tid = b.dataset.tid ? parseInt(b.dataset.tid, 10) : null;
      const r = await fetch("/api/demo/" + encodeURIComponent(id) + "/team", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ team_id: tid }) }).then(r => r.json()).catch(() => null);
      this._toast && this._toast(r && r.ok ? (tid ? "Shared with team" : "Made private") : "Could not change sharing");
      $("teamsModal").classList.remove("show");
      if (r && r.ok && $("libraryModal").classList.contains("show")) this.openLibrary();   // refresh badges/chips (#23)
    });
    $("teamsModal").classList.add("show");
  },

  // ---- dashboard (landing view) -------------------------------------------
  showLanding() {                                       // public marketing page (logged-out visitors)
    document.body.classList.add("on-landing");
    document.body.classList.remove("on-dashboard");
    this.hideOverlay();
    this._initPricingToggle();
    this._updateProPreviewBanner();
  },
  // billing-period toggle on the pricing card. Presentational only -- billing isn't live yet, so this
  // just shows the per-month price + how it's billed; the CTA stays the honest "early access" message.
  // Live plans, keyed by period -- from the server (me.pricing, editable in the admin panel), or the
  // built-in fallback. One source for the landing card, the upgrade modal, and the upsell.
  _plans() {
    const arr = this.me && this.me.pricing;
    if (Array.isArray(arr) && arr.length) { const m = {}; arr.forEach(p => { m[p.key] = p; }); return m; }
    return PRO_PLANS;
  },
  // Update a toggle's price/bill (+ each segment's "-N%" save pill) for the chosen period.
  _applyPeriod(togEl, priceEl, billEl, key) {
    const plans = this._plans();
    const p = plans[key] || plans.monthly;
    if (priceEl) priceEl.innerHTML = `${p.mo}<span>/mo</span>`;
    if (billEl) billEl.textContent = p.bill;
    if (togEl) togEl.querySelectorAll(".lp-seg").forEach(s => {
      s.classList.toggle("active", s.dataset.period === key);
      const pill = s.querySelector(".lp-seg-save");
      if (pill) { const sp = (plans[s.dataset.period] || {}).save_pct; pill.hidden = !sp; if (sp) pill.textContent = `-${sp}%`; }
    });
    return p;
  },
  _initPricingToggle() {
    const tog = $("lpToggle");
    if (!tog || this._pricingWired) return;
    this._pricingWired = true;
    tog.querySelectorAll(".lp-seg").forEach(s => s.onclick = () => this._applyPeriod(tog, $("lpProPrice"), $("lpProBill"), s.dataset.period));
    this._applyPeriod(tog, $("lpProPrice"), $("lpProBill"), "monthly");
  },
  showDashboard() {
    document.body.classList.remove("on-landing");
    document.body.classList.add("on-dashboard");
    this.hideOverlay();
    this._updateProPreviewBanner();
    this.loadDashboard();
  },
  hideDashboard() { document.body.classList.remove("on-dashboard", "on-landing"); },
  // pick the right "home" view for the current auth state (landing if logged out on an auth-enabled site)
  goHome() {
    // close any full-screen overlay first, or it stays covering the dashboard (e.g. the analytics
    // panel was left visible after clicking the brand from inside analytics).
    if ($("analyticsPanel") && $("analyticsPanel").classList.contains("show")) closeAnalytics(this);
    ["libraryModal", "teamsModal", "goalsModal", "trendsModal", "adminModal", "upgradeModal", "searchModal"]
      .forEach(id => { const m = $(id); if (m) m.classList.remove("show"); });
    const me = this.me || {};
    if (me.auth_enabled && !(me.authenticated && me.user)) this.showLanding();
    else this.showDashboard();
  },

  // ---- dashboard workspace (Personal vs a team context) -------------------
  // The dashboard shows ONE context at a time. Default Personal; remembered in localStorage but always
  // re-validated against the teams the user is actually on (so a left/disbanded team falls back cleanly).
  _validWorkspace(ws) {
    if (ws && ws.indexOf("team:") === 0) {
      const id = +ws.slice(5);
      if ((this.myTeams || []).some(t => t.id === id)) return ws;
    }
    return "personal";
  },
  _currentWorkspace() {
    if (this.dashboardWorkspace == null) {
      let saved = "personal";
      try { saved = localStorage.getItem("cs2dp_dashboard_workspace") || "personal"; } catch (e) { /* ignore */ }
      this.dashboardWorkspace = saved;
    }
    return this._validWorkspace(this.dashboardWorkspace);
  },
  _workspaceName(ws) {
    if (ws && ws.indexOf("team:") === 0) {
      const t = (this.myTeams || []).find(x => x.id === +ws.slice(5));
      return t ? t.name : "Team";
    }
    return "Personal";
  },
  setWorkspace(ws) {
    this.dashboardWorkspace = this._validWorkspace(ws);
    try { localStorage.setItem("cs2dp_dashboard_workspace", this.dashboardWorkspace); } catch (e) { /* ignore */ }
    if (document.body.classList.contains("on-dashboard")) this.loadDashboard();
  },

  async loadDashboard() {
    const ws = this._currentWorkspace();
    this.dashboardWorkspace = ws;                          // persist the sanitized value
    let d = null, status = 0;
    try {
      const r = await fetch("/api/dashboard?workspace=" + encodeURIComponent(ws), { cache: "no-store" });
      status = r.status; d = await r.json();
    } catch (e) { d = null; }
    if (status === 403 && ws !== "personal") {             // team vanished / lost access -> fall back
      this.dashboardWorkspace = "personal";
      try { localStorage.setItem("cs2dp_dashboard_workspace", "personal"); } catch (e) { /* ignore */ }
      return this.loadDashboard();
    }
    const nm = this.me && this.me.user && this.me.user.name;   // welcome the signed-in user
    $("dashTitle").textContent = nm ? `Welcome back, ${nm}` : "CS2 Demo Review";
    this._renderDashSub(ws);
    this._renderDashQuick(d);
    if (!d || d.error) {                                  // auth required + not signed in
      $("dashEmpty").hidden = true;
      $("dashFocus").innerHTML = "";
      $("dashMatches").innerHTML = `<div class="dash-empty">Sign in through Steam (top-right) to see your demos.</div>`;
      $("dashMe").innerHTML = ""; $("dashGoalsList").innerHTML = "";
      return;
    }
    const matches = d.matches || [], empty = matches.length === 0;
    const showGoals = this.entitled("goals");             // Practice goals are Pro -> hide the section for free
    $("dashEmpty").hidden = !empty;                       // rich empty state instead of a dead dashboard
    $("dashMatchSec").style.display = empty ? "none" : "";
    $("dashGoalSec").style.display = (empty || !showGoals) ? "none" : "";
    this._renderDashFocus(d, empty);
    this._renderDashMe(empty ? null : d.me, matches.length);
    this._renderDashJobs(d.active_jobs || []);
    if (empty) this._renderDashEmpty();
    else {
      this._renderDashMatches(matches);
      if (showGoals) this._renderDashGoals(d.open_goals || []);
      else $("dashGoalsList").innerHTML = "";
    }
    clearTimeout(this._dashJobTimer);                    // live-refresh while parses run
    if ((d.active_jobs || []).length && document.body.classList.contains("on-dashboard")) {
      this._dashJobTimer = setTimeout(() => this.loadDashboard(), 2000);
    }
    this._maybeAutoTour();                                // first-time walkthrough (once per new user)
  },
  _lastReview() {
    try { return JSON.parse(localStorage.getItem("cs2dp_last_review") || "null"); } catch (e) { return null; }
  },
  // subtitle reflects the active workspace (left untouched for solo/local users with no teams)
  _renderDashSub(ws) {
    const sub = $("dashSub");
    if (!sub) return;
    if (ws.indexOf("team:") === 0) sub.textContent = `Team dashboard — ${this._workspaceName(ws)}.`;
    else if ((this.myTeams || []).length) sub.textContent = "Your personal matches and goals.";
    else sub.textContent = "Pick a match to review, or upload a new one.";
  },
  _renderDashQuick(d) {
    const el = $("dashQuick");
    if (!el) return;
    const parts = [];
    const last = this._lastReview();
    const ids = new Set(((d && d.matches) || []).map(m => m.id));
    if (last && last.id && ids.has(last.id)) {
      parts.push(`<button class="dq-continue" data-id="${esc(last.id)}">&#9654; Continue &mdash; ${esc(last.map || "last match")}</button>`);
    }
    parts.push(`<button class="btn ghost sm" data-q="analytics">Analytics</button>`);
    parts.push(`<button class="btn ghost sm" data-q="library">Library</button>`);
    if (this.entitled("goals")) parts.push(`<button class="btn ghost sm" data-q="goals">Goals</button>`);
    if (this.me && this.me.authenticated) parts.push(`<button class="btn ghost sm" data-q="teams">Teams</button>`);
    if (this.isHelper) parts.push(`<button class="btn ghost sm" data-q="admin">${this.isAdmin ? "Admin" : "Helper"}</button>`);
    if (this._canUpgrade()) parts.push(`<button class="btn primary sm" data-q="upgrade">&#10022; Go Pro</button>`);
    const q = this.me && this.me.upload_quota;            // Free demo cap (absent/unlimited -> no chip)
    if (q && !q.unlimited) parts.push(`<span class="dq-ws" title="Free plan demo limit — Pro is unlimited">Demos: <b>${q.used}/${q.limit}</b></span>`);
    // Workspace switcher: Personal + each team. A real <select> when there are teams (mobile-safe,
    // scales to many); a static chip when the user is solo (nothing to switch).
    const teams = this.myTeams || [], cur = this._currentWorkspace();
    if (teams.length) {
      const opt = (v, label) => `<option value="${esc(v)}"${v === cur ? " selected" : ""}>${esc(label)}</option>`;
      const opts = opt("personal", "Personal") + teams.map(t => opt("team:" + t.id, t.name)).join("");
      parts.push(`<span class="dq-ws dq-wsel" title="Switch dashboard workspace">Workspace: `
        + `<select id="dashWsSel" class="dq-wssel" aria-label="Dashboard workspace">${opts}</select></span>`);
    } else {
      parts.push(`<span class="dq-ws" title="Demos you can see">Workspace: <b>Personal</b></span>`);
    }
    el.innerHTML = parts.join("");
    const wsel = el.querySelector("#dashWsSel");
    if (wsel) wsel.onchange = () => this.setWorkspace(wsel.value);
    const c = el.querySelector(".dq-continue");
    if (c) c.onclick = () => this.viewLibraryDemo(c.dataset.id);
    el.querySelectorAll("[data-q]").forEach(b => b.onclick = () => {
      const q = b.dataset.q;
      if (q === "library") this.openLibrary();
      else if (q === "analytics") this.entitled("advancedAnalytics") ? this.openDashAnalytics() : this._upsell("advancedAnalytics");
      else if (q === "goals") this.entitled("goals") ? this.openGoals() : this._upsell("goals");
      else if (q === "teams") this.entitled("teams") ? this.openTeams() : this._upsell("teams");
      else if (q === "admin") this.openAdmin();
      else if (q === "upgrade") this.openUpgrade();
    });
  },
  _fmtMap(mp) {
    const raw = String(mp || "?").replace(/^de_/, "").replace(/^cs_/, "");
    const named = { dust2: "Dust2", ancient: "Ancient", anubis: "Anubis", mirage: "Mirage",
      inferno: "Inferno", nuke: "Nuke", overpass: "Overpass", vertigo: "Vertigo",
      train: "Train", cache: "Cache" };
    return named[raw.toLowerCase()] || raw.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
  },
  _scoreHtml(score) {
    const sp = String(score || "").split("-");
    return (sp.length === 2 && sp[0] !== "") ? `${esc(sp[0])}<span>:</span>${esc(sp[1])}` : `<span class="dm-noscore">--</span>`;
  },
  _renderDashFocus(d, empty) {
    const el = $("dashFocus");
    if (!el) return;
    if (empty || !d) { el.innerHTML = ""; return; }
    const matches = d.matches || [], latest = matches[0] || {};
    const goalCount = d.open_goal_count != null ? d.open_goal_count : ((d.open_goals || []).length);
    const jobs = (d.active_jobs || []).length;
    const latestId = esc(latest.id || latest.key || "");
    const latestMap = this._fmtMap(latest.map);
    const latestMeta = `${latest.rounds || 0} rounds${latest.created_at ? " - " + esc(this._fmtDate(latest.created_at)) : ""}`;
    el.innerHTML =
      `<button class="df-card df-primary" data-df="review" data-id="${latestId}">`
      + `<span class="df-k">Next review</span><b>${esc(latestMap)} <em>${this._scoreHtml(latest.score)}</em></b>`
      + `<small>${latestMeta}</small><i>Open replay</i></button>`
      + (this.entitled("goals")                          // Practice goals are Pro -> hide the card for free
        ? `<button class="df-card" data-df="goals"><span class="df-k">Practice focus</span>`
          + `<b>${goalCount ? goalCount + " open goal" + (goalCount === 1 ? "" : "s") : "No goals yet"}</b>`
          + `<small>${goalCount ? "Keep the review loop measurable." : "Create one from a recurring mistake."}</small><i>Open goals</i></button>`
        : "")
      + `<button class="df-card" data-df="upload"><span class="df-k">${jobs ? "Queue active" : "Add data"}</span>`
      + `<b>${jobs ? jobs + " parse job" + (jobs === 1 ? "" : "s") : "Upload another demo"}</b>`
      + `<small>${jobs ? "Processing runs in the background." : "More matches make trends useful."}</small><i>${jobs ? "View status" : "Upload demo"}</i></button>`;
    el.querySelectorAll("[data-df]").forEach(b => b.onclick = () => {
      const a = b.dataset.df;
      if (a === "review" && b.dataset.id) this.viewLibraryDemo(b.dataset.id);
      else if (a === "goals") this.entitled("goals") ? this.openGoals() : this._upsell("goals");
      else if (a === "upload") $("uploadBtn").click();
    });
  },
  _renderDashEmpty() {
    const ws = this._currentWorkspace();
    const isTeam = ws.indexOf("team:") === 0;
    const tName = this._workspaceName(ws);
    const el = $("dashEmpty");
    const head = isTeam ? `Upload or share a demo to ${esc(tName)}` : "Upload your first demo";
    const body = isTeam
      ? `Demos you upload from here go to <b>${esc(tName)}</b>, and any match a teammate shares with the team shows up for everyone on it. `
        + `You can also share an existing match to ${esc(tName)} from your library.`
      : `Drop a CS2 <b>.dem</b> (or a .zip of them) and you'll get a 2D replay, scoreboard, and basic stats. `
        + `The first parse of a full match takes ~30&ndash;60s and runs in the background &mdash; you don't have to wait on the page.`;
    el.innerHTML = `<h2>${head}</h2><p>${body}</p>`
      + `<div class="des-actions"><button class="btn primary" data-e="upload">Upload a .dem</button>`
      + `<button class="btn" data-e="sample">Load the sample match</button>`
      + ((this.me && this.me.authenticated) ? `<button class="btn ghost" data-e="team">Create or join a team</button>` : "")
      + `</div>`;
    el.querySelectorAll("[data-e]").forEach(b => b.onclick = () => {
      const e = b.dataset.e;
      if (e === "upload") $("uploadBtn").click();
      else if (e === "sample") $("sampleBtn").click();
      else if (e === "team") this.entitled("teams") ? this.openTeams() : this._upsell("teams");
    });
  },
  // The signed-in player's own analytics, two ways: their LATEST match (per-match, with arrows showing
  // how that game compared to their norm) and their AVERAGE across ALL their demos (with the
  // improving/declining trend). Both at the top of the dashboard.
  _renderDashMe(me, hasMatches) {
    const el = $("dashMe");
    if (!me || !me.n_matches) {
      // Never leave a blank gap when the user HAS demos but we couldn't auto-match their Steam
      // account to a player in them: still surface the analytics entry point.
      if (hasMatches) {
        el.classList.add("show");
        el.innerHTML = `<div class="mf-head"><span>Your analytics</span>`
          + `<button class="btn ghost sm" id="dashMeOpen">Open analytics</button></div>`
          + `<div class="mf-empty">We couldn't match your Steam account to a player in your demos yet. `
          + `Open analytics to pick your player and see per-match + all-demo stats.</div>`;
        const ob = $("dashMeOpen");
        if (ob) ob.onclick = () => this.entitled("advancedAnalytics") ? this.openTrends() : this._upsell("advancedAnalytics");
      } else { el.innerHTML = ""; el.classList.remove("show"); }
      return;
    }
    el.classList.add("show");
    const a = me.averages || {}, tr = me.trend || {};
    const series = me.series || [];
    const last = series[series.length - 1] || null;          // most-recent match (series is ASC by date)
    const arrow = (v) => (v == null || v === 0 || isNaN(v)) ? "" : (v > 0
      ? `<span class="mf-up" title="above your average">&#9650; ${Math.abs(v)}</span>`
      : `<span class="mf-dn" title="below your average">&#9660; ${Math.abs(v)}</span>`);
    const dn = (v, d) => (v == null) ? "&mdash;" : v;        // value-or-dash
    const tile = (val, label, t) => `<div class="mf-stat"><div class="mf-v">${val}</div>`
      + `<div class="mf-k">${label}${t !== undefined ? " " + arrow(t) : ""}</div></div>`;
    const n = me.n_matches;
    // per-match arrows = latest vs the player's own average (so a green up = a better-than-usual game)
    const d2 = (x, y) => (x == null || y == null) ? null : +(x - y).toFixed(2);
    const d0 = (x, y) => (x == null || y == null) ? null : Math.round(x - y);
    const latestStats = last ? `<div class="mf-stats">`
        + tile(dn(last.hltv), "Rating", d2(last.hltv, a.hltv))
        + tile(dn(last.kd), "K/D", d2(last.kd, a.kd))
        + tile(dn(last.adr), "ADR", d0(last.adr, a.adr))
        + tile(last.kast != null ? last.kast + "%" : "&mdash;", "KAST", d0(last.kast, a.kast))
        + `</div>`
      : `<div class="mf-empty">No match stats yet.</div>`;
    const avgStats = `<div class="mf-stats">`
      + tile(dn(a.hltv), "Rating", tr.hltv)
      + tile(dn(a.kd), "K/D")
      + tile(dn(a.adr), "ADR", tr.adr)
      + tile(a.kast != null ? a.kast + "%" : "&mdash;", "KAST", tr.kast)
      + `</div>`;
    const lastKey = last && (last.key || last.id);
    el.innerHTML = `<div class="mf-head"><span>Your analytics</span>`
      + `<button class="btn ghost sm" id="dashMeTrends">Full trends</button></div>`
      + `<div class="mf-groups">`
      + `<div class="mf-group${lastKey ? " mf-clickable" : ""}"${lastKey ? ` data-key="${esc(lastKey)}" title="Open this match's analytics"` : ""}>`
        + `<div class="mf-glabel">Latest match`
        + (last && last.map ? ` <i class="mf-sub">${esc(this._fmtMap(last.map))}</i>` : "")
        + (lastKey ? ` <span class="mf-go">view &rsaquo;</span>` : "") + `</div>${latestStats}</div>`
      + `<div class="mf-group"><div class="mf-glabel">Average <i class="mf-sub">${n} demo${n === 1 ? "" : "s"}</i></div>${avgStats}</div>`
      + `</div>`;
    $("dashMeTrends").onclick = () => this.entitled("advancedAnalytics") ? this.openTrends(me.steamid) : this._upsell("advancedAnalytics");
    if (lastKey) { const g = el.querySelector(".mf-group.mf-clickable"); if (g) g.onclick = () => this._openMatchAnalytics(lastKey); }
  },
  // load a specific demo and jump straight into its per-match analytics (from the dashboard / trends).
  _openMatchAnalytics(key) {
    if (!key) return;
    const tm = $("trendsModal"); if (tm) tm.classList.remove("show");
    this.viewLibraryDemo(key, { analytics: true });
  },

  // ---- first-time walkthrough -----------------------------------------------
  // A guided spotlight tour over real UI elements. Auto-runs once for new users (localStorage
  // flag), re-launchable from the "Tour" button. Steps whose target is missing/hidden are skipped
  // lazily in _tourShow, so the same step list adapts to dashboard vs replay vs 2D-vs-3D state.
  //
  // The replay walkthrough -- targets elements that only exist once a demo is loaded. Action steps
  // trigger real UI (enter 3D, open a panel); waitUntil auto-advances once the state flips, so the
  // tour stays in lockstep with what the user is actually looking at.
  _replayTourSteps() {
    return [
      { el: ".viewport", title: "The 2D radar", body: "This is the top-down replay. Dots are players &mdash; <b>green = CT</b>, <b>orange/red = T</b>. Pan with right-click drag (or touch), zoom with the scroll wheel." },
      { el: "#playPause", title: "Play the round", body: "Hit Play to watch the round unfold. Press <b>Space</b> to toggle playback at any time." },
      { el: "#timeline", title: "Scrub the timeline", body: "Drag the timeline to jump to any moment in the round." },
      { el: "#prevRound", title: "Jump between rounds", body: "Use the arrow buttons &mdash; or the <b>[</b> and <b>]</b> keys &mdash; to move round to round." },
      { el: "#toggle3d", title: "Go 3D", body: "Switch to 3D for a fly-around view of the exact same replay.",
        action: { label: "Enter 3D", fn: () => { if (this.entitled("threeD")) this.toggle3d(); else this._upsell("threeD"); } },
        waitUntil: () => !!(this.view3d && this.view3d.active) },
      // Centered (no el): #view3dHint is auto-hidden on calibrated maps (Mirage etc.), so we can't
      // reliably spotlight it. The body lists every control, so a centered card is the robust call.
      // onEnter still surfaces the hint bar when it IS available (uncalibrated / still-loading maps).
      { title: "Move around in 3D", body: "<b>WASD</b> to move, <b>E/Q</b> up/down, <b>Shift</b> to sprint. <b>Space</b> plays/pauses, <b>C</b> cycles camera modes (Fly / Follow / Overhead). Click the view to lock your mouse, <b>Esc</b> to release it.",
        onEnter: () => { const h = $("view3dHint"); if (h && this.view3d && this.view3d.active && h.innerHTML.trim()) h.classList.add("show"); } },
      { el: "#toggle3d", title: "Back to 2D", body: "Click <b>2D</b> to drop back to the radar view whenever you want.",
        action: { label: "Back to 2D", fn: () => { if (this.exit3D) this.exit3D(); else this.toggle3d(); } },
        waitUntil: () => !(this.view3d && this.view3d.active) },
      { el: "#toggleAnalytics", title: "Analytics", body: "Analytics turns the replay into patterns: K/D, ADR, KAST, openings, trades, and the mistakes you keep repeating. Let's open it and walk the tabs.",
        action: { label: "Open Analytics", fn: () => { const b = $("toggleAnalytics"); if (b) b.click(); } },
        // require the tab to be VISIBLE (panel actually open) -- closeAnalytics leaves the tab HTML in
        // the DOM, so a mere querySelector would be true from an earlier open and skip the walkthrough.
        waitUntil: () => { const t = document.querySelector('.antab[data-view="report"]'); return !!(t && t.offsetParent !== null); } },
      { el: '.antab[data-view="report"]', title: "Report — start here", body: "The <b>Report</b> is your match summary: the scoreline, the biggest round swings, who carried, and the headline takeaways. Read this first every match.",
        onEnter: () => this._tourAnalyticsView("report") },
      { el: '.antab[data-view="player"]', title: "Player — one player at a time", body: "The <b>Player</b> view breaks a single player down: <b>Aim</b>, <b>Utility</b> and <b>Positioning</b> sub-ratings, opening duels, and the exact rounds where they cost the team. Use the dropdown to switch players.",
        onEnter: () => this._tourAnalyticsView("player") },
      { el: '.antab[data-view="team"]', title: "Team — your five as a unit", body: "The <b>Team</b> view is spacing, trade efficiency, who's entrying, and repeated tendencies the enemy can read off you.",
        onEnter: () => this._tourAnalyticsView("team") },
      { el: '.antab[data-view="data"]', title: "Data — trust the numbers", body: "The <b>Data</b> view shows parse health: what the demo captured and how confident each stat is, so you know the analysis is solid.",
        onEnter: () => this._tourAnalyticsView("data") },
      { el: "#toggleUtil", title: "Utility", body: "Utility shows every smoke, flash, and molly thrown in the match &mdash; and lets you save lineups to your nade library.",
        onEnter: () => closeAnalytics(this),               // clear the analytics overlay before showing the viewport spotlight
        action: { label: "Open Utility", fn: () => { const b = $("toggleUtil"); if (b) b.click(); } } },
      { el: "#toggleReview", title: "Review", body: "Review is where you bookmark rounds, add notes, and queue practice sessions &mdash; perfect for fixing what Analytics finds.",
        action: { label: "Open Review", fn: () => $("toggleReview") && $("toggleReview").click() } },
      { title: "That's VantageGG", body: "The loop: <b>Watch</b> (2D/3D) &rarr; <b>Find patterns</b> (Analytics) &rarr; <b>Mark moments</b> (Review) &rarr; <b>Practice</b> (Utility). Replay this tour anytime from the <b>Tour</b> button up top." },
    ];
  },
  // The entry tour for the dashboard: load the sample, then flow straight into the replay
  // walkthrough. Because the load-sample step's waitUntil fires once `this.demo` exists, the
  // very next step (the first replay step) takes over naturally -- one continuous tour.
  _dashTourSteps() {
    const loadSampleStep = {
      title: "Welcome to VantageGG",
      body: "Upload a CS2 demo or load a sample match to get a 2D + 3D replay, deep stats, and coaching. Let's try it now.",
      action: { label: "Load sample & start walkthrough", fn: () => this.loadSample() },
      waitUntil: () => !!this.demo,
    };
    return [loadSampleStep, ...this._replayTourSteps()];
  },
  startTour(fromUser) {
    // If a demo is already on screen, jump straight into the replay walkthrough; otherwise
    // start at the dashboard entry step (which loads the sample, then flows into the replay).
    // NOTE: we do NOT pre-filter by visibility here. The dashboard tour deliberately keeps the
    // replay steps even though their targets are hidden right now -- the load-sample step's
    // waitUntil fires once the demo exists, and by then those targets are live. _tourShow does
    // the skipping lazily (per current visibility) so the flow stays correct as the page changes.
    const steps = (this.demo ? this._replayTourSteps() : this._dashTourSteps());
    // if every step is unreachable (e.g. somehow no targets and no centered steps), bail
    if (!steps.length) return;
    this._tour = { steps, i: 0, fromUser: !!fromUser };
    if (!this._tourEl) this._buildTourDom();
    this._tourEl.hidden = false;
    this._tourShow(0);
    window.addEventListener("keydown", this._tourKey);
    window.addEventListener("resize", this._tourReflow);
  },
  _buildTourDom() {
    const wrap = document.createElement("div");
    wrap.id = "tour"; wrap.className = "tour"; wrap.hidden = true;
    wrap.innerHTML = `<div class="tour-spot"></div>`
      + `<div class="tour-card"><div class="tour-step"></div>`
      + `<h3 class="tour-title"></h3><p class="tour-body"></p>`
      + `<button class="btn primary sm tour-action-btn"></button>`
      + `<div class="tour-actions"><button class="btn ghost sm tour-skip">Skip</button>`
      + `<div class="tour-nav"><button class="btn ghost sm tour-back">Back</button>`
      + `<button class="btn primary sm tour-next">Next</button></div></div></div>`;
    document.body.appendChild(wrap);
    this._tourEl = wrap;
    // advance one step forward; _tourShow skips any not-yet-visible steps and ends the tour
    // itself if it runs off the end, so a plain i+1 is safe even with lazy skipping.
    const advance = () => this._tourShow(this._tour.i + 1);
    wrap.querySelector(".tour-skip").onclick = () => this._tourEnd();
    wrap.querySelector(".tour-back").onclick = () => this._tourShow(this._tour.i - 1);
    wrap.querySelector(".tour-action-btn").onclick = () => {
      const step = this._tour && this._tour.steps[this._tour.i];
      if (!step) return;
      if (step.action && step.action.fn) { try { step.action.fn(); } catch (e) { /* tolerate */ } }
      // If the step also has a waitUntil, the action just KICKS OFF the work (often async, e.g.
      // loading the sample or entering 3D) -- the poller set up in _tourShow advances once the
      // condition flips. Advancing here too would race past the not-yet-ready replay steps.
      if (!step.waitUntil) advance();
    };
    wrap.querySelector(".tour-next").onclick = advance;
    this._tourKey = (e) => {
      if (!this._tour) return;
      if (e.key === "Escape") this._tourEnd();
      else if (e.key === "Enter" || e.key === "ArrowRight") { e.preventDefault(); wrap.querySelector(".tour-next").click(); }
      else if (e.key === "ArrowLeft") this._tourShow(this._tour.i - 1);
    };
    this._tourReflow = () => { if (this._tour) this._tourShow(this._tour.i); };
  },
  // is this step showable right now? a centered (no-el) step always is; an el step needs a
  // target that's actually rendered/visible (offsetParent != null catches display:none ancestors).
  _tourStepVisible(s) {
    if (!s || !s.el) return true;
    const t = document.querySelector(s.el);
    return !!(t && t.offsetParent !== null);
  },
  _tourShow(i) {
    const t = this._tour; if (!t) return;
    // tear down anything tied to the step we're leaving (poll loop + onLeave side-effects)
    this._tourClearWait();
    const from = t.i;
    const prev = t.steps[from];
    // direction of travel from the requested index (a request past the end means "advance off the
    // last step" -> end the tour). Don't pre-clamp the upper bound or that end signal is lost.
    const dir = i >= from ? 1 : -1;
    // lazily skip steps whose target isn't visible right now (e.g. replay controls while still on
    // the dashboard). Travel in the direction the user moved; if we run off the end going forward,
    // end the tour; off the start going back, snap to the first visible step.
    while (i >= 0 && i < t.steps.length && !this._tourStepVisible(t.steps[i])) i += dir;
    if (i < 0) { // nothing visible before here -- find the first visible step forward instead
      i = 0; while (i < t.steps.length && !this._tourStepVisible(t.steps[i])) i++;
    }
    if (i >= t.steps.length) { this._tourEnd(); return; }   // ran out going forward -> done
    if (prev && i !== from && prev.onLeave) { try { prev.onLeave(); } catch (e) { /* tolerate */ } }
    t.i = i;
    const step = t.steps[i], el = this._tourEl;
    // counter + first/last reflect only the steps showable right now (skipped ones don't count),
    // so "2 / 11" stays honest even though the underlying array is longer.
    const visIdx = t.steps.map((s, k) => this._tourStepVisible(s) ? k : -1).filter(k => k >= 0);
    const pos = visIdx.indexOf(i);
    const isFirst = pos <= 0;
    const isLast = pos === visIdx.length - 1;
    el.querySelector(".tour-step").textContent = `${pos + 1} / ${visIdx.length}`;
    el.querySelector(".tour-title").innerHTML = step.title;
    el.querySelector(".tour-body").innerHTML = step.body;
    el.querySelector(".tour-back").style.visibility = isFirst ? "hidden" : "";
    const nextBtn = el.querySelector(".tour-next");
    nextBtn.textContent = isLast ? "Done" : "Next";
    // action button: shown only when this step defines one, advances after running its fn
    const actBtn = el.querySelector(".tour-action-btn");
    if (step.action) { actBtn.textContent = step.action.label || "Continue"; actBtn.classList.add("show"); }
    else actBtn.classList.remove("show");
    // let the step open whatever it needs before we measure / spotlight its target
    if (step.onEnter) { try { step.onEnter(); } catch (e) { /* tolerate */ } }
    // waitUntil: hold Next disabled and poll until the world catches up, then auto-advance.
    // (advance to i+1 -- _tourShow skips forward past any not-yet-visible steps from there.)
    if (step.waitUntil) {
      nextBtn.disabled = true;
      if (!step.waitUntil()) {
        this._tourWait = setInterval(() => {
          if (!this._tour || this._tour.i !== i) { this._tourClearWait(); return; }
          let ok = false; try { ok = !!step.waitUntil(); } catch (e) { ok = false; }
          if (ok) {
            this._tourClearWait();
            nextBtn.disabled = false;
            if (isLast) this._tourEnd(); else this._tourShow(i + 1);
          }
        }, 300);
      } else { nextBtn.disabled = false; if (!isLast) { this._tourShow(i + 1); return; } }
    } else { nextBtn.disabled = false; }
    const spot = el.querySelector(".tour-spot"), card = el.querySelector(".tour-card");
    const target = step.el && document.querySelector(step.el);
    if (target) {
      target.scrollIntoView({ block: "nearest", inline: "nearest" });
      const r = target.getBoundingClientRect(), pad = 6;
      const cardH = Math.max(card.offsetHeight, 200);   // measure real height; fall back to 200
      const cardW = Math.max(card.offsetWidth, 320);
      el.style.background = "transparent";              // the spot's ring does the dimming
      spot.style.display = "";
      spot.style.left = (r.left - pad) + "px"; spot.style.top = (r.top - pad) + "px";
      spot.style.width = (r.width + pad * 2) + "px"; spot.style.height = (r.height + pad * 2) + "px";
      card.classList.remove("tour-center"); card.style.transform = ""; card.style.bottom = "";
      const left = Math.min(Math.max(8, r.left + r.width / 2 - cardW / 2), window.innerWidth - cardW - 8);
      card.style.left = left + "px";
      const spaceBelow = window.innerHeight - r.bottom, spaceAbove = r.top;
      if (spaceBelow >= cardH + 12) {
        card.style.top = (r.bottom + 12) + "px";           // fits below
      } else if (spaceAbove >= cardH + 12) {
        card.style.top = Math.max(8, r.top - cardH - 12) + "px"; // fits above
      } else {
        card.style.top = Math.max(8, Math.min(r.bottom + 12, window.innerHeight - cardH - 8)) + "px"; // cramped, clamped
      }
    } else {
      spot.style.display = "none";
      el.style.background = "rgba(4,8,12,.72)";          // full dim for centered steps
      card.classList.remove("tour-center"); card.style.transform = "";
      // Explicitly center via inline style so a stale top/left from a prior step never leaks through
      card.style.left = "50%"; card.style.top = "50%"; card.style.bottom = "";
      card.style.transform = "translate(-50%,-50%)";
    }
  },
  _tourClearWait() {
    if (this._tourWait) { clearInterval(this._tourWait); this._tourWait = null; }
  },
  // Tour helper: make sure the Analytics panel is open, then switch to a given tab. Re-opening is
  // async (openAnalytics fetches + renders), so when we have to open it we click the tab after a
  // short delay; when it's already open the tab is there and we click immediately. Pro-locked tabs
  // (Team/Data for a non-entitled user) are left alone so we never trip the upsell mid-tour -- the
  // spotlight + copy still teach what the tab does. The sample previews Pro, so on the tour they work.
  _tourAnalyticsView(view) {
    const open = $("analyticsPanel").classList.contains("show");
    if (!open) { this.pausePlayback(); openAnalytics(this); }
    const clickTab = () => {
      const t = document.querySelector(`.antab[data-view="${view}"]`);
      if (t && (view === "report" || view === "player" || !t.classList.contains("antab-pro"))) t.click();
    };
    if (open) clickTab(); else setTimeout(clickTab, 350);
  },
  _tourEnd() {
    this._tourClearWait();
    if (this._tourEl) this._tourEl.hidden = true;
    window.removeEventListener("keydown", this._tourKey);
    window.removeEventListener("resize", this._tourReflow);
    this._tour = null;
    try { localStorage.setItem("cs2dp_tour_v2", "done"); } catch (e) { /* private mode */ }
  },
  _maybeAutoTour() {
    if (this._tourChecked) return;
    this._tourChecked = true;
    let seen = null;
    try { seen = localStorage.getItem("cs2dp_tour_v2"); } catch (e) { seen = "done"; }   // private mode: don't nag
    if (!seen) setTimeout(() => { if (document.body.classList.contains("on-dashboard")) this.startTour(false); }, 700);
  },

  _renderDashJobs(list) {
    const el = $("dashJobs");
    if (!list.length) { el.innerHTML = ""; el.classList.remove("show"); return; }
    el.classList.add("show");
    el.innerHTML = `<div class="dj-title">Processing ${list.length} demo${list.length > 1 ? "s" : ""}&hellip;</div>`
      + list.map(j => `<div class="dj-row"><span class="dj-name">${esc(j.filename || "demo")}</span>`
        + `<span class="dj-status">${esc(j.status || "queued")}</span></div>`).join("");
  },
  _renderDashMatches(matches) {
    const el = $("dashMatches");
    if (!matches.length) {
      el.innerHTML = `<div class="dash-empty">No demos yet &mdash; <b>Upload a .dem</b> or load the sample to get started.</div>`;
      return;
    }
    el.innerHTML = matches.map(m => {
      const score = this._scoreHtml(m.score);          // index stores score as "ct-t"
      const mapName = this._fmtMap(m.map);
      const slug = String(m.map || "").toLowerCase().replace(/[^a-z0-9_]/g, "");   // radar art behind the card
      const bg = slug ? ` style="--mapimg:url('/static/maps/${slug}.png')"` : "";
      return `<button class="dmatch"${bg} data-id="${esc(m.id || m.key)}">`
        + `<div class="dm-top"><div class="dm-map">${esc(mapName)}</div><span class="dm-chip">Review</span></div>`
        + `<div class="dm-score">${score}</div>`
        + `<div class="dm-meta">${m.rounds || 0} rounds${m.created_at ? " &middot; " + esc(this._fmtDate(m.created_at)) : ""}</div>`
        + `</button>`;
    }).join("");
    el.querySelectorAll(".dmatch").forEach(b => b.onclick = () => this.viewLibraryDemo(b.dataset.id));
  },
  _renderDashGoals(goalsList) {
    const el = $("dashGoalsList");
    if (!goalsList.length) {
      el.innerHTML = `<div class="dash-empty">No open goals. <b>Goals</b> turn recurring mistakes into measurable targets.</div>`;
      return;
    }
    el.innerHTML = goalsList.map(g =>
      `<button class="dgoal"><span class="dg-title">${esc(g.title || g.metric)}</span><span class="dg-target">target ${esc(String(g.target))}</span></button>`).join("");
    el.querySelectorAll(".dgoal").forEach(b => b.onclick = () => this.openGoals());
  },

  // ---- entitlements (Pro gating; dormant while TIERS_ENABLED=0 -> entitled() always true) ----
  entitled(feature) {
    // the SAMPLE match is a free preview of the per-match Pro features, so people see what Pro does
    // (3D / utility / advanced analytics). Account features (goals, teams) are NOT previewable.
    if (this._sampleLoaded && (feature === "threeD" || feature === "utility" || feature === "advancedAnalytics")) return true;
    return !this.ent || this.ent[feature] !== false;
  },
  // mark locked Pro buttons with a small PRO pill (dormant when entitled -> no class added)
  _applyGates() {
    const map = { toggle3d: "threeD", toggleUtil: "utility", toggleTrends: "advancedAnalytics", goalsBtn: "goals" };
    for (const id in map) {
      const el = $(id);
      if (el) el.classList.toggle("pro-locked", !this.entitled(map[id]));
    }
    this._updateProPreviewBanner();
  },
  // banner shown only when a Free user is previewing Pro features on the sample match
  _updateProPreviewBanner() {
    const el = $("proPreviewBanner");
    if (!el) return;
    const me = this.me || {};
    // only a genuinely Free user (tiers on, not Pro/admin) previewing on the sample
    const freeUser = !!(me.tiers_enabled && me.tier && me.tier !== "pro");
    el.hidden = !(this._sampleLoaded && freeUser && !this._ppbDismissed
      && !document.body.classList.contains("on-dashboard")
      && !document.body.classList.contains("on-landing"));
  },
  // Clicking a locked feature opens the upgrade modal (with that feature called out). Falls back to a
  // toast if the modal isn't on the page for some reason.
  _upsell(feature) {
    if ($("upgradeModal")) { this.openUpgrade(feature); return; }
    const names = { threeD: "3D view", utility: "Utility & nade tools", advancedAnalytics: "Advanced analytics & trends",
                    goals: "Practice goals", teams: "Team workspaces" };
    if (this._toast) this._toast(`${names[feature] || "That"} is a Pro feature — $10/mo unlocks 3D, utility & advanced analytics.`);
  },
  // True for a genuine Free user under tier enforcement (admins/local/Pro resolve to "pro").
  _canUpgrade() {
    const me = this.me || {};
    return !!(me.tiers_enabled && me.tier && me.tier !== "pro");
  },
  // In-app subscription view -- the upgrade path for logged-in users (the landing card is logged-out only).
  // `highlightFeature` (optional) calls out the specific Pro feature the user just tried to use.
  openUpgrade(highlightFeature) {
    const modal = $("upgradeModal");
    if (!modal) { this._toast && this._toast("Upgrade isn't available here."); return; }
    this._upPeriod = this._upPeriod || "monthly";
    this._renderUpgrade(highlightFeature);
    modal.classList.add("show");
  },
  _renderUpgrade(highlightFeature) {
    const me = this.me || {};
    const isProNow = this.isAdmin || me.tier === "pro";
    const planEl = $("upPlan");
    if (planEl) {
      let txt = "Current plan: <b>Free</b>";
      if (this.isAdmin) txt = "Current plan: <b>Admin</b> — full access";
      else if (me.tier === "pro") {
        const ps = me.user ? this._proStatus(me.user) : { active: true, label: "indefinite" };
        txt = ps.active ? `Current plan: <b>Pro</b> · ${esc(ps.label)}` : "Current plan: <b>Free</b> (Pro lapsed)";
      }
      planEl.innerHTML = txt;
    }
    const hl = $("upHighlight");
    if (hl) {
      const names = { threeD: "3D replay", utility: "the utility &amp; nade tools", advancedAnalytics: "advanced analytics &amp; trends",
                      goals: "practice goals", teams: "team workspaces" };
      if (highlightFeature && names[highlightFeature]) { hl.hidden = false; hl.innerHTML = `Unlock <b>${names[highlightFeature]}</b> &mdash; and everything else in Pro.`; }
      else hl.hidden = true;
    }
    const billingOn = !!me.billing_enabled;
    const hasSub = !!(me.user && me.user.stripe_customer_id);   // a real Stripe customer -> can manage
    const manage = isProNow && hasSub && billingOn;             // Pro via Stripe -> "Manage subscription"
    const tog = $("upToggle");
    const apply = (key) => {
      this._upPeriod = key;
      const p = this._applyPeriod(tog, $("upPrice"), $("upBill"), key);
      const cta = $("upCta");
      if (cta) cta.textContent = manage ? "Manage subscription"
        : isProNow ? "You already have Pro" : `Get Pro — ${p.label}`;
    };
    tog.querySelectorAll(".lp-seg").forEach(s => s.onclick = () => apply(s.dataset.period));
    apply(this._upPeriod);
    const cta = $("upCta");
    if (cta) {
      cta.classList.toggle("disabled", isProNow && !manage);   // admins/comp Pro: nothing to buy/manage
      cta.onclick = () => {
        if (manage) { this._openPortal(); return; }
        if (isProNow) { this._toast && this._toast("You already have full Pro access."); return; }
        if (billingOn) { this._startCheckout(this._upPeriod); return; }
        this._toast && this._toast("Billing isn't live yet — approved testers get Pro free during early access.");
      };
    }
    const soon = document.querySelector("#upgradeModal .up-soon");
    if (soon) soon.hidden = billingOn;                         // drop the "not live yet" note once billing is on
  },

  // ---- account settings (off the profile button) --------------------------
  openAccount() {
    if (!$("accountModal")) return;
    this.renderAccount();
    $("accountModal").classList.add("show");
  },
  renderAccount() {
    const me = this.me || {}, u = me.user || {};
    const body = $("acctBody"); if (!body) return;
    const name = esc(u.name || "Player");
    // avatar, or a friendly monogram when Steam gave us no picture (common in early access)
    const initial = esc((u.name || "Player").trim().charAt(0).toUpperCase() || "?");
    const av = u.avatar ? `<img class="av acct-av" src="${esc(u.avatar)}" alt="">`
      : `<div class="acct-av acct-av-fallback">${initial}</div>`;
    const prof = u.steam_id_64 ? `https://steamcommunity.com/profiles/${esc(u.steam_id_64)}` : "";
    // plan block
    let planHtml;
    const q = me.upload_quota;
    const quotaHtml = q && !q.unlimited
      ? `<div class="acct-usage"><span>Demos used</span><b>${q.used}/${q.limit}</b></div>`
      : `<div class="acct-usage"><span>Demos</span><b>Unlimited</b></div>`;
    if (this.isAdmin) {
      planHtml = `<div class="acct-plan-card"><div class="acct-plan-main"><span class="acct-badge pro">ADMIN</span><div><b>Full access</b><small>All Pro tools, admin panel, and account controls.</small></div></div>${quotaHtml}</div>`;
    } else if (me.tier === "pro") {
      const ps = this._proStatus(u);
      planHtml = `<div class="acct-plan-card"><div class="acct-plan-main"><span class="acct-badge pro">PRO</span><div><b>${esc(ps.active ? ps.label : "expired")}</b><small>3D replay, utility tools, advanced trends, goals, teams, and playbook.</small></div></div>${quotaHtml}</div>`
        + `<div class="acct-mut">Billing isn't live yet — your Pro was granted while we're in early access. To change or cancel it, contact support.</div>`;
    } else {
      planHtml = `<div class="acct-plan-card"><div class="acct-plan-main"><span class="acct-badge free">FREE</span><div><b>Basic review</b><small>2D replay, match summary, basic analytics, and sample access.</small></div></div>${quotaHtml}</div>`
        + `<button class="btn primary sm acct-gopro">&#10022; Go Pro</button>`;
    }
    // support link (configurable via SUPPORT_CONTACT; email -> mailto, url -> link)
    const sc = (me.support_contact || "").trim();
    const supportHtml = sc
      ? `<a class="btn ghost sm" href="${/^https?:\/\//.test(sc) ? esc(sc) : "mailto:" + esc(sc)}" target="_blank" rel="noopener">Contact support</a>`
      : `<span class="acct-mut">Support contact will be added before public launch.</span>`;
    body.innerHTML =
      `<div class="acct-id">${av}<div class="acct-idr"><div class="acct-name">${name}</div>`
      + (prof ? `<a class="acct-steam" href="${prof}" target="_blank" rel="noopener">View Steam profile &#8599;</a>` : "") + `</div></div>`
      + `<div class="acct-sec"><label class="acct-lbl">Display name</label>`
      +   `<div class="acct-row"><input id="acctName" class="adm-search" maxlength="32" value="${name}" autocomplete="off">`
      +   `<button id="acctNameSave" class="btn sm">Save</button></div></div>`
      + `<div class="acct-sec"><label class="acct-lbl">Plan &amp; billing</label>${planHtml}</div>`
      + `<div class="acct-split"><div class="acct-sec"><label class="acct-lbl">Support</label>${supportHtml}</div>`
      + `<div class="acct-sec acct-logout-sec"><label class="acct-lbl">Session</label><button id="acctLogout" class="btn">Log out</button></div></div>`
      + `<details class="acct-danger"><summary><span class="acct-lbl acct-danger-lbl">Danger zone</span><span>Delete account and all demos</span></summary>`
      +   `<div class="acct-mut">Permanently delete your account and <b>all your data</b> — every demo you've uploaded and its analysis. This can't be undone.</div>`
      +   `<button id="acctDelete" class="btn acct-del-btn">Delete my account</button></details>`;
    $("acctNameSave").onclick = async () => {
      const v = $("acctName").value.trim();
      if (!v) { this._toast && this._toast("Name can't be empty"); return; }
      let r; try { r = await fetch("/api/account/name", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: v }) }); } catch (e) { r = null; }
      if (r && r.ok) {
        const j = await r.json().catch(() => ({}));
        const nm = j.name || v;
        if (this.me.user) this.me.user.name = nm;
        const w = document.querySelector(".who"); if (w) w.textContent = nm;   // refresh header without reload
        $("acctName").value = nm;
        this._toast && this._toast("Display name updated");
      } else { this._toast && this._toast("Couldn't update name"); }
    };
    const gp = body.querySelector(".acct-gopro");
    if (gp) gp.onclick = () => { $("accountModal").classList.remove("show"); this.openUpgrade(); };
    $("acctLogout").onclick = async () => {
      try { await fetch("/logout", { method: "POST" }); } catch (e) { /* ignore */ }
      location.reload();
    };
    $("acctDelete").onclick = async () => {
      const ok = await this.askConfirm("Delete your account?",
        `<div class="cf-line">This permanently deletes your account and <b>every demo you've uploaded</b> + its analysis.</div>`
        + `<div class="cf-line cf-mut">This cannot be undone.</div>`, "Delete everything");
      if (!ok) return;
      let r; try { r = await fetch("/api/account", { method: "DELETE" }); } catch (e) { r = null; }
      if (r && r.ok) { location.reload(); }
      else { this._toast && this._toast("Couldn't delete account — try again or contact support."); }
    };
  },

  // ---- admin / helper panel -----------------------------------------------
  // Metrics an operator actually watches: how many users, the Pro/Free split, total demos, and
  // growth/activity over time -- NOT a feed of individual demos or jobs. Admins also manage users
  // (grant Pro, promote to Helper, remove); Helpers get the read view + grant Pro only.
  openAdmin() {
    this.pausePlayback();
    const t = $("admTitle"); if (t) t.textContent = this.isAdmin ? "Admin" : "Helper";
    $("adminModal").classList.add("show");
    this.renderAdmin();
  },
  // tiny inline SVG sparkline from a [{date,count}] series; color is a CSS var like "var(--ct)"
  _spark(series, color) {
    const vals = (series || []).map(p => p.count || 0), n = vals.length;
    if (!n) return "";
    const max = Math.max(1, ...vals), W = 220, H = 38, pad = 3;
    const stepX = (W - pad * 2) / Math.max(1, n - 1);
    const xy = vals.map((v, i) => [pad + i * stepX, H - pad - (v / max) * (H - pad * 2)]);
    const line = xy.map(p => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
    const area = `${pad.toFixed(1)},${(H - pad).toFixed(1)} ${line} ${(W - pad).toFixed(1)},${(H - pad).toFixed(1)}`;
    const last = xy[n - 1];
    return `<svg class="adm-spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">`
      + `<polygon points="${area}" fill="${color}" opacity="0.13"></polygon>`
      + `<polyline points="${line}" fill="none" stroke="${color}" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"></polyline>`
      + (vals[n - 1] ? `<circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="2.4" fill="${color}"></circle>` : "")
      + `</svg>`;
  },
  async renderAdmin() {
    const body = $("adminBody");
    body.innerHTML = `<div class="lib-empty">Loading&hellip;</div>`;
    let ov, users, ops;
    try {
      [ov, users, ops] = await Promise.all([
        fetch("/api/admin/overview", { cache: "no-store" }).then(r => r.json()),
        fetch("/api/admin/users", { cache: "no-store" }).then(r => r.json()),
        fetch("/api/admin/ops", { cache: "no-store" }).then(r => r.json()).catch(() => null),
      ]);
    } catch (e) { body.innerHTML = `<div class="dash-empty">Admin data unavailable.</div>`; return; }
    if (!ov || ov.error) { body.innerHTML = `<div class="dash-empty">${esc((ov && ov.error) || "admin only")}</div>`; return; }
    const mb = b => b == null ? "0" : (b >= (1 << 30) ? (b / (1 << 30)).toFixed(1) + " GB" : Math.round(b / (1 << 20)) + " MB");
    const st = ov.storage || {};
    // headline metrics: who's on the site + the Pro/Free split + total demos. (No Players / 3D-maps.)
    const tiles = [["Users", ov.users], ["Pro", ov.pro_users], ["Free", ov.free_users != null ? ov.free_users : "—"],
                   ["Demos", ov.demos], ["Teams", ov.teams], ["Storage", mb((st.cache_bytes || 0) + (st.uploads_bytes || 0))]];
    const proPct = ov.users ? Math.round((ov.pro_users / ov.users) * 100) : 0;
    // growth / activity: new users + uploads in the last 7 / 30 days, with a 14-day trend line each.
    const growth =
      `<div class="adm-growth">`
      + `<div class="adm-gcard"><div class="adm-gtop"><span class="adm-glabel">New users</span>`
      +   `<span class="adm-gnums"><b>${ov.new_users_7d != null ? ov.new_users_7d : "—"}</b> 7d `
      +   `· <b>${ov.new_users_30d != null ? ov.new_users_30d : "—"}</b> 30d</span></div>`
      +   this._spark(ov.signups_14d, "var(--ct)")
      +   `<div class="adm-gfoot">last 14 days</div></div>`
      + `<div class="adm-gcard"><div class="adm-gtop"><span class="adm-glabel">Uploads</span>`
      +   `<span class="adm-gnums"><b>${ov.demos_7d != null ? ov.demos_7d : "—"}</b> 7d `
      +   `· <b>${ov.demos_30d != null ? ov.demos_30d : "—"}</b> 30d</span></div>`
      +   this._spark(ov.uploads_14d, "var(--accent)")
      +   `<div class="adm-gfoot">last 14 days</div></div>`
      + `</div>`;
    const cfg = ov.config || {};
    const onoff = v => v ? `<b class="adm-on">on</b>` : `<span class="adm-off">off</span>`;
    const cfgRows = [["Tiers enforced", onoff(cfg.tiers_enabled)], ["Login required", onoff(cfg.auth_required)],
      ["Free upload limit", cfg.free_upload_limit], ["Keep raw .dem", onoff(cfg.keep_dem)],
      ["Secure cookie (HTTPS)", onoff(cfg.session_cookie_secure)],
      ["Steam API key", cfg.steam_api_key ? `<b class="adm-on">set</b>` : `<span class="adm-off">missing</span>`],
      ["Public URL", esc(String(cfg.public_base_url || "?"))], ["Admins / Helpers", `${cfg.admins} / ${ov.helpers != null ? ov.helpers : 0}`],
      ["Schema / analytics", `v${cfg.schema_version} / v${cfg.analytics_version}`]];
    const cfgHtml = cfgRows.map(([k, v]) => `<div class="adm-cfg-row"><span>${k}</span><span>${v}</span></div>`).join("");
    // stash for the (client-side) user search + the per-row action handlers in _renderAdmUserList
    this._admCanManage = this.isAdmin;   // promote/remove are admin-only; helpers can only grant Pro
    this._admMeId = this.me && this.me.user && this.me.user.id;
    this._admUsers = (users && users.users) || [];
    body.innerHTML =
      `<div class="adm-toolbar"><button id="admPreviewFree" class="btn sm">&#128065; Preview as a free user</button>`
      +   `<span class="round">See the app gated like a non-admin &mdash; reload or "Exit preview" to return.</span></div>`
      + `<div class="adm-stats">${tiles.map(([k, v]) => `<div class="dstat"><div class="dstat-v">${v}</div><div class="dstat-k">${k}</div></div>`).join("")}</div>`
      + `<div class="adm-jobs">${proPct}% on Pro${cfg.tiers_enabled ? "" : ` · <span class="round">tiers OFF (everyone has full access)</span>`}</div>`
      + `<div class="dash-sec-head adm-uhead"><h2>Growth &amp; activity</h2></div>${growth}`
      + `<div class="dash-sec-head adm-uhead"><h2>Storage &amp; parsing</h2></div>${this._renderOps(ops)}`
      + (this.isAdmin
          ? `<div class="dash-sec-head adm-uhead"><h2>Free-plan limit</h2></div>`
            + `<div class="adm-limit"><label>Demos a free user can upload `
            +   `<input id="admFreeLimit" type="number" min="1" max="1000" value="${cfg.free_upload_limit}"></label>`
            + `<button id="admFreeLimitSave" class="btn primary sm">Save</button>`
            + `<span class="round" id="admFreeLimitMsg"></span></div>` : "")
      + `<div class="dash-sec-head adm-uhead"><h2>Users</h2>`
      +   `<input id="admUserSearch" class="adm-search" type="search" placeholder="Search name or SteamID…" autocomplete="off"></div>`
      + `<div class="adm-users" id="admUsers"></div>`
      + (this.isAdmin ? `<div class="dash-sec-head adm-uhead"><h2>Pricing</h2></div><div id="admPricing" class="adm-pricing"></div>` : "")
      + `<div class="dash-sec-head adm-uhead"><h2>Deployment</h2></div><div class="adm-cfg">${cfgHtml}</div>`;
    $("admPreviewFree").onclick = () => this.togglePreviewFree(true);
    this._bindOps();                       // wire the failed-job drilldown (19B)
    const flSave = $("admFreeLimitSave");  // admin-settable Free-plan upload limit
    if (flSave) flSave.onclick = async () => {
      const msg = $("admFreeLimitMsg"), val = parseInt($("admFreeLimit").value, 10);
      if (!(val >= 1)) { if (msg) msg.textContent = "Enter a whole number ≥ 1."; return; }
      const r = await fetch("/api/admin/config", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ free_upload_limit: val }) }).then(x => x.json()).catch(() => null);
      if (r && r.ok) { if (msg) msg.textContent = `Saved — free users can upload ${r.free_upload_limit} demos.`; this._refreshMe && this._refreshMe(); }
      else if (msg) msg.textContent = (r && r.error) || "Save failed.";
    };
    const search = $("admUserSearch");
    search.value = this._admFilter || "";
    search.oninput = () => { this._admFilter = search.value; this._renderAdmUserList(); };
    this._renderAdmUserList();
    if (this.isAdmin) this._renderAdminPricing();
  },
  // Admin "Storage & parsing" section: where the bytes go + upload/parse timing (from the jobs table).
  _renderOps(ops) {
    if (!ops || ops.error) return `<div class="dash-empty">Ops data unavailable.</div>`;
    const mb = b => b == null ? "0" : (b >= (1 << 30) ? (b / (1 << 30)).toFixed(2) + " GB" : Math.max(0, Math.round(b / (1 << 20))) + " MB");
    const st = ops.storage || [], total = ops.storage_total || 0, disk = ops.disk || {};
    const max = Math.max(1, ...st.map(s => s.bytes || 0));
    const bars = st.map(s => `<div class="ops-row"><span class="ops-l">${esc(s.label)}</span>`
      + `<span class="ops-bar"><i style="width:${Math.round((s.bytes || 0) / max * 100)}%"></i></span>`
      + `<span class="ops-v">${mb(s.bytes)}</span></div>`).join("");
    const diskLine = disk.total
      ? `<div class="ops-disk">Disk: <b>${mb(disk.used)}</b> used &middot; <b>${mb(disk.free)}</b> free of ${mb(disk.total)}`
        + `<span class="ops-bar wide"><i style="width:${Math.round(disk.used / disk.total * 100)}%"></i></span></div>`
      : "";
    const t = ops.timing || {};
    const secs = v => v == null ? "—" : (v >= 60 ? (v / 60).toFixed(1) + "m" : v + "s");
    const failN = t.failed || 0, failClick = failN > 0;
    const tiles = [["Parsed", t.parsed != null ? t.parsed : 0],
      ["Failed", `<span id="opsFailToggle" class="${failClick ? "ops-failclick" : ""}">${failN}${failClick ? " &#9656;" : ""}</span>`],
      ["Active", t.active != null ? t.active : 0], ["Workers", t.workers]];
    // upload (server receive+save) vs queue wait vs parse -- so a slow case is attributable (19A)
    const phase = (label, a) => {
      a = a || {};
      return `<div class="ops-phase"><div class="ops-phase-h">${label} <i class="round">n=${a.n || 0}</i></div>`
        + `<div class="ops-phase-v"><span>avg <b>${secs(a.avg)}</b></span><span>med ${secs(a.median)}</span><span>max ${secs(a.max)}</span></div></div>`;
    };
    const phases = `<div class="ops-phases">${phase("Upload (server)", t.upload)}${phase("Queue wait", t.queue)}${phase("Parse", t.parse)}</div>`;
    const recent = (t.recent || []).map(r =>
      `<div class="ops-jrow"><span class="ops-jf" title="${esc(r.filename || "?")}">${esc(r.filename || "?")}</span>`
      + `<span class="ops-js st-${esc(r.status || "")}">${esc(r.status || "")}</span>`
      + `<span class="ops-jsz">${mb(r.bytes)}</span>`
      + `<span class="ops-jd" title="upload / queue / parse">${secs(r.upload_s)} / ${secs(r.queue_s)} / ${secs(r.parse_s)}</span></div>`).join("");
    // 19B: failed-job drilldown -- hidden until the Failed tile is clicked; each row expands its error
    const fails = (t.failures || []).map(f =>
      `<div class="ops-fail"><div class="ops-fail-head"><span class="ops-jf">${esc(f.filename || "?")}</span>`
      + `<span class="ops-fail-who">${esc(f.who || "")}</span>`
      + `<span class="ops-fail-when">${esc(String(f.finished_at || f.created_at || "").replace("T", " "))}</span></div>`
      + `<div class="ops-fail-err">${esc(f.error || "(no error message recorded)")}</div></div>`).join("");
    const failPanel = `<div id="opsFails" class="ops-fails" hidden>${fails || `<div class="round">No failed jobs.</div>`}</div>`;
    return `<div class="ops-wrap">`
      + `<div class="ops-head">App data total: <b>${mb(total)}</b></div>${bars}${diskLine}`
      + `<div class="ops-cleanup"><button id="opsReclaim" class="btn ghost sm">Scan for reclaimable raw demos</button>`
      +   `<span id="opsReclaimMsg" class="round"></span></div>`
      + `<div class="ops-head ops-head2">Upload &amp; parse timing</div>`
      + `<div class="adm-stats ops-stats">${tiles.map(([k, v]) => `<div class="dstat"><div class="dstat-v">${v}</div><div class="dstat-k">${k}</div></div>`).join("")}</div>`
      + phases + failPanel
      + (recent ? `<div class="ops-head ops-head2">Recent jobs <i class="round">file &middot; status &middot; size &middot; upload/queue/parse</i></div><div class="ops-recent">${recent}</div>` : "")
      + `</div>`;
  },
  // Wire the admin ops section: clicking "Failed N" toggles the failure list; clicking a failure
  // expands its full error/stack (19B). Called by renderAdmin after the panel HTML is inserted.
  _bindOps() {
    const tog = $("opsFailToggle");
    if (tog && tog.classList.contains("ops-failclick")) {
      tog.style.cursor = "pointer";
      tog.onclick = () => { const p = $("opsFails"); if (p) p.hidden = !p.hidden; };
    }
    document.querySelectorAll("#opsFails .ops-fail").forEach(el => {
      const err = el.querySelector(".ops-fail-err");
      if (err) el.onclick = () => err.classList.toggle("full");
    });
    // orphaned/reclaimable raw .dem + stale upload temps -> scan, confirm, clean (never cache/stats)
    const rec = $("opsReclaim");
    if (rec) rec.onclick = async () => {
      const msg = $("opsReclaimMsg");
      const human = b => { const m = (b || 0) / (1 << 20); return m >= 1024 ? (m / 1024).toFixed(1) + " GB" : Math.round(m) + " MB"; };
      if (msg) msg.textContent = "Scanning…";
      const s = await fetch("/api/admin/orphans", { cache: "no-store" }).then(r => r.json()).catch(() => null);
      if (!s || s.error) { if (msg) msg.textContent = "Scan failed."; return; }
      const n = (s.n_dems || 0) + (s.n_temps || 0);
      if (!n) { if (msg) msg.textContent = "Nothing to reclaim."; return; }
      const ok = await this.askConfirm("Reclaim storage?",
        `<div class="cf-line">Delete <b>${n}</b> reclaimable file${n > 1 ? "s" : ""} (raw .dem + stale upload temps) to free <b>${human(s.total_bytes)}</b>?</div>`
        + `<div class="cf-line cf-mut">Never touches parsed replays you can still watch or your retained stats.</div>`, "Reclaim");
      if (!ok) { if (msg) msg.textContent = ""; return; }
      const r = await fetch("/api/admin/orphans/clean", { method: "POST" }).then(x => x.json()).catch(() => null);
      if (r && r.ok) { this._toast && this._toast(`Reclaimed ${r.removed} file(s), freed ${human(r.freed_bytes)}.`); if (msg) msg.textContent = `Freed ${human(r.freed_bytes)}.`; }
      else if (msg) msg.textContent = "Cleanup failed.";
    };
  },
  _money(cur, n) {                                       // mirror pricing.py _fmt_money
    return Math.abs(n - Math.round(n)) < 0.005 ? `${cur}${Math.round(n)}` : `${cur}${n.toFixed(2)}`;
  },
  async _renderAdminPricing() {
    const host = $("admPricing"); if (!host) return;
    host.innerHTML = `<div class="lib-empty">Loading&hellip;</div>`;
    let data;
    try { data = await fetch("/api/admin/pricing", { cache: "no-store" }).then(r => r.json()); }
    catch (e) { host.innerHTML = `<div class="dash-empty">Pricing unavailable.</div>`; return; }
    if (!data || data.error) { host.innerHTML = `<div class="dash-empty">${esc((data && data.error) || "admin only")}</div>`; return; }
    const cur = (data.config && data.config.currency) || "$";
    const prices = (data.config && data.config.prices) || {};
    const periods = data.periods || [];
    const rows = periods.map(p => {
      const total = prices[p.key] != null ? prices[p.key] : 0;
      return `<div class="adm-price-row"><span class="adm-price-label">${esc(p.label)} <i class="round">${p.months}mo</i></span>`
        + `<input class="adm-price-in" data-pk="${p.key}" data-months="${p.months}" type="number" min="0" step="0.01" value="${total}">`
        + `<span class="adm-price-hint" data-hint="${p.key}"></span></div>`;
    }).join("");
    host.innerHTML =
      `<div class="adm-price-top"><label>Currency <input id="apCur" class="adm-search adm-cur-in" value="${esc(cur)}" maxlength="3"></label>`
      + `<span class="round">Set the total charged per term — per-month &amp; savings update automatically. Billing isn't live yet, so this only changes what visitors see.</span></div>`
      + `<div class="adm-price-rows">${rows}</div>`
      + `<button id="apSave" class="btn primary sm">Save prices</button>`;
    const recompute = () => {
      const c = ($("apCur").value || "$").trim() || "$";
      const monthly = parseFloat((host.querySelector('.adm-price-in[data-pk="monthly"]') || {}).value) || 0;
      host.querySelectorAll(".adm-price-in").forEach(inp => {
        const total = parseFloat(inp.value) || 0, months = parseInt(inp.dataset.months, 10) || 1;
        let txt = `= ${this._money(c, months ? total / months : total)}/mo`;
        if (months > 1 && monthly > 0) {
          const save = Math.max(0, Math.round((1 - total / (monthly * months)) * 100));
          if (save > 0) txt += ` · save ${save}%`;
        }
        host.querySelector(`[data-hint="${inp.dataset.pk}"]`).textContent = txt;
      });
    };
    host.querySelectorAll(".adm-price-in").forEach(inp => inp.oninput = recompute);
    $("apCur").oninput = recompute;
    recompute();
    $("apSave").onclick = async () => {
      const c = ($("apCur").value || "$").trim() || "$";
      const out = {};
      host.querySelectorAll(".adm-price-in").forEach(inp => { out[inp.dataset.pk] = parseFloat(inp.value) || 0; });
      const res = await this._adminReq("/api/admin/pricing", "POST", { currency: c, prices: out });
      if (!res) return;
      try {
        const j = await fetch("/api/admin/pricing", { cache: "no-store" }).then(r => r.json());
        if (this.me) this.me.pricing = j.plans;            // live everywhere without a reload
        this._pricingWired = false; this._initPricingToggle();   // refresh the landing card too
      } catch (e) { /* non-fatal */ }
      this._toast && this._toast("Pricing saved.");
    };
  },
  // Pro status for a user row: {active, label}. Honors expiry (stored tier may say 'pro' past pro_until).
  _proStatus(u) {
    if (u.tier !== "pro") return { active: false, label: "" };
    if (!u.pro_until) return { active: true, label: "indefinite" };
    const end = new Date(u.pro_until), now = new Date();
    if (isNaN(end) || end < now) return { active: false, label: "expired" };
    const days = Math.ceil((end - now) / 86400000);
    const label = days <= 1 ? "<1 day left" : (days < 45 ? days + " days left" : Math.round(days / 30) + " months left");
    return { active: true, label };
  },
  // render (or re-render, on search) just the #admUsers list from this._admUsers, filtered by this._admFilter
  _renderAdmUserList() {
    const host = $("admUsers"); if (!host) return;
    const q = (this._admFilter || "").trim().toLowerCase();
    const all = this._admUsers || [];
    const list = q ? all.filter(u => ((u.name || "") + " " + (u.steam_id_64 || "")).toLowerCase().includes(q)) : all;
    const canManage = this._admCanManage, meId = this._admMeId;
    if (!all.length) { host.innerHTML = `<div class="dash-empty">No users yet.</div>`; return; }
    const rows = list.map(u => {
      const isHelper = u.role === "helper";
      const ps = this._proStatus(u);
      const badge = ps.active ? "pro" : "free";
      const name = u.name || u.steam_id_64 || "user";
      const proTime = ps.label ? `<span class="adm-protime${ps.active ? "" : " adm-protime-exp"}">${esc(ps.label)}</span>` : "";
      return `<div class="adm-u"><div class="adm-uinfo"><b>${esc(name)}</b>`
        + (isHelper ? ` <span class="adm-role">helper</span>` : "")
        + `<span class="round">${esc(u.steam_id_64 || "")} · ${u.demo_count} demo${u.demo_count === 1 ? "" : "s"}</span></div>`
        + `<span class="adm-tier adm-${badge}">${badge}</span>${proTime}`
        + (ps.active
            ? `<button class="btn sm adm-pro-btn" data-uid="${u.id}" data-name="${esc(name)}">Change</button>`
              + `<button class="btn sm adm-free-btn" data-uid="${u.id}">Make Free</button>`
            : `<button class="btn sm adm-pro-btn" data-uid="${u.id}" data-name="${esc(name)}">Make Pro</button>`)
        + (canManage && u.id !== meId ? `<button class="btn sm ghost adm-role-btn" data-uid="${u.id}" data-role="${isHelper ? "user" : "helper"}">${isHelper ? "Remove helper" : "Make helper"}</button>` : "")
        + (canManage && u.id !== meId ? `<button class="adm-del" title="Remove user" data-deluid="${u.id}" data-name="${esc(name)}">&#128465;</button>` : "")
        + `</div>`;
    }).join("");
    host.innerHTML = rows || `<div class="dash-empty">No users match “${esc(q)}”.</div>`;
    host.querySelectorAll(".adm-pro-btn").forEach(b => b.onclick = async () => {
      const months = await this._askProDuration(b.dataset.name);
      if (months === null) return;                     // cancelled
      if (await this._adminReq(`/api/admin/users/${b.dataset.uid}/tier`, "POST", { tier: "pro", months })) this.renderAdmin();
    });
    host.querySelectorAll(".adm-free-btn").forEach(b => b.onclick = async () => {
      if (await this._adminReq(`/api/admin/users/${b.dataset.uid}/tier`, "POST", { tier: "free" })) this.renderAdmin();
    });
    host.querySelectorAll(".adm-role-btn").forEach(b => b.onclick = async () => {
      if (await this._adminReq(`/api/admin/users/${b.dataset.uid}/role`, "POST", { role: b.dataset.role })) this.renderAdmin();
    });
    host.querySelectorAll(".adm-del").forEach(b => b.onclick = async () => {
      const ok = await this.askConfirm("Remove user?",
        `<div class="cf-line">Remove <b>${esc(b.dataset.name)}</b>?</div>`
        + `<div class="cf-line cf-mut">Their account + team memberships are deleted. Their demos are kept but become unowned.</div>`, "Remove");
      if (!ok) return;
      if (await this._adminReq(`/api/admin/users/${b.dataset.deluid}`, "DELETE")) this.renderAdmin();
    });
  },
  // admin write helper -- surfaces failures via a toast instead of silently swallowing them.
  // A 404 almost always means the running server predates this feature -> tell the user to restart.
  async _adminReq(url, method, body) {
    let res;
    try {
      const opt = { method };
      if (body !== undefined) { opt.headers = { "Content-Type": "application/json" }; opt.body = JSON.stringify(body); }
      res = await fetch(url, opt);
    } catch (e) {
      this._toast && this._toast("Network error — is the server running?");
      return false;
    }
    if (!res.ok) {
      const hint = res.status === 404 ? " — restart the server (start.bat) to load the latest update"
        : res.status === 403 ? " — admin only" : "";
      this._toast && this._toast(`That didn't go through (HTTP ${res.status})${hint}.`);
      return false;
    }
    return true;
  },
  // duration chooser for granting Pro. Resolves to months (int; 0 = indefinite) or null if cancelled.
  _askProDuration(name) {
    return new Promise(resolve => {
      const modal = $("proModal");
      $("proBody").innerHTML = `<div class="cf-line">Grant Pro to <b>${esc(name)}</b></div>`
        + `<div class="cf-line cf-mut">Pick a length — they get every Pro feature until it expires, or forever.</div>`;
      modal.classList.add("show");
      const btns = [...modal.querySelectorAll(".pro-dur")];
      const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); done(null); } };
      const done = (v) => {
        modal.classList.remove("show");
        btns.forEach(b => b.onclick = null);
        $("proCancel").onclick = $("proX").onclick = modal.onclick = null;
        window.removeEventListener("keydown", onKey, true);
        resolve(v);
      };
      btns.forEach(b => b.onclick = () => done(parseInt(b.dataset.months, 10) || 0));
      $("proCancel").onclick = () => done(null);
      $("proX").onclick = () => done(null);
      modal.onclick = (e) => { if (e.target.id === "proModal") done(null); };
      window.addEventListener("keydown", onKey, true);
    });
  },

  bindUI() {
    $("playPause").onclick = () => this.togglePlay();
    $("speed").onchange = (e) => { this.speed = parseFloat(e.target.value); };
    $("timeline").addEventListener("pointerdown", () => this.scrubbing = true);
    $("timeline").addEventListener("pointerup", () => this.scrubbing = false);
    $("timeline").addEventListener("input", (e) => { this.t = parseFloat(e.target.value); });
    $("prevRound").onclick = () => this.jumpRound(-1);
    $("nextRound").onclick = () => this.jumpRound(1);
    $("tlMode").onclick = () => this.toggleTlMode();
    $("freeCam").onclick = () => this.freeCamera();
    $("toggle3d").onclick = () => this.entitled("threeD") ? this.toggle3d() : this._upsell("threeD");
    $("camPreset").onclick = () => this.cycleCamPreset();
    $("toggleAnalytics").onclick = () => {
      if (!this.demo) return;
      if ($("analyticsPanel").classList.contains("show")) closeAnalytics(this);
      else { this.toggleUtil(false); this.toggleReview(false); this.pausePlayback(); openAnalytics(this); }   // only one panel open; pause while open
    };
    $("closeAnalytics").onclick = () => closeAnalytics(this);
    $("settingsBtn").onclick = () => $("settingsPop").classList.toggle("show");
    // persist replay settings on any change in the panel. "change" fires for checkboxes (on toggle)
    // AND range sliders (on release, after the live oninput handlers have applied the value) -- so we
    // save the final value without spamming localStorage on every drag tick.
    $("settingsPop").addEventListener("change", () => this.saveSettings());
    $("map3d").onclick = () => this.show3dStatus();
    $("m3Close").onclick = () => $("map3dStatus").classList.remove("show");
    $("map3dStatus").onclick = (e) => { if (e.target.id === "map3dStatus") $("map3dStatus").classList.remove("show"); };
    $("toggleTrends").onclick = () => this.entitled("advancedAnalytics") ? this.openTrends() : this._upsell("advancedAnalytics");
    $("trClose").onclick = () => $("trendsModal").classList.remove("show");
    $("trendsModal").onclick = (e) => { if (e.target.id === "trendsModal") $("trendsModal").classList.remove("show"); };
    $("daClose").onclick = () => $("dashAnalyticsModal").classList.remove("show");
    $("dashAnalyticsModal").onclick = (e) => { if (e.target.id === "dashAnalyticsModal") $("dashAnalyticsModal").classList.remove("show"); };
    $("daTrendsBtn").onclick = () => { $("dashAnalyticsModal").classList.remove("show"); this.entitled("advancedAnalytics") ? this.openTrends() : this._upsell("advancedAnalytics"); };
    $("teamsClose").onclick = () => $("teamsModal").classList.remove("show");
    $("teamsModal").onclick = (e) => { if (e.target.id === "teamsModal") $("teamsModal").classList.remove("show"); };
    $("adminClose").onclick = () => $("adminModal").classList.remove("show");
    $("adminModal").onclick = (e) => { if (e.target.id === "adminModal") $("adminModal").classList.remove("show"); };
    $("upClose").onclick = () => $("upgradeModal").classList.remove("show");
    $("upgradeModal").onclick = (e) => { if (e.target.id === "upgradeModal") $("upgradeModal").classList.remove("show"); };
    $("acctClose").onclick = () => $("accountModal").classList.remove("show");
    $("accountModal").onclick = (e) => { if (e.target.id === "accountModal") $("accountModal").classList.remove("show"); };
    $("ppbClose").onclick = () => { this._ppbDismissed = true; $("proPreviewBanner").hidden = true; };
    // banner "Go Pro": logged-in users get the in-app upgrade modal; logged-out users sign in first
    $("ppbGo").onclick = (e) => {
      if (this.me && this.me.authenticated) { e.preventDefault(); this.openUpgrade(); }
    };
    $("apbExit").onclick = () => this.togglePreviewFree(false);   // leave the admin "view as free" lens
    // dashboard (landing) navigation
    $("brandHome").onclick = () => this.goHome();
    $("dashUpload").onclick = () => $("uploadBtn").click();
    $("dashSample").onclick = () => $("sampleBtn").click();
    $("dashAllMatches").onclick = () => this.openLibrary();
    $("dashGoals").onclick = () => this.openGoals();
    $("landSample").onclick = () => $("sampleBtn").click();   // landing: let visitors try the sample
    $("goalsBtn").onclick = () => this.entitled("goals") ? this.openGoals() : this._upsell("goals");
    $("goalsClose").onclick = () => $("goalsModal").classList.remove("show");
    $("goalsModal").addEventListener("click", (e) => { if (e.target.id === "goalsModal") $("goalsModal").classList.remove("show"); });
    $("glNewBtn").onclick = () => this.toggleGoalForm();
    $("glRecurPlayer").onchange = () => this.renderRecurring($("glRecurPlayer").value);
    $("glRecurToggle").onclick = () => {
      const b = $("glRecurBody"), open = b.style.display !== "none";
      b.style.display = open ? "none" : "flex";
      $("glRecurToggle").innerHTML = (open ? "▸" : "▾") + " Recurring mistakes";
    };
    $("drawBtn").onclick = () => this.toggleDraw();
    $("drawSave").onclick = () => this.saveDrawing();
    $("drawClear").onclick = () => { this.strokes = []; this._redraw(); };
    $("toggleUtil").onclick = () => this.entitled("utility") ? this.toggleUtil() : this._upsell("utility");
    $("utilClose").onclick = () => this.toggleUtil(false);
    $("toggleReview").onclick = () => this.toggleReview();
    $("reviewClose").onclick = () => this.toggleReview(false);
    $("rvAdd").onclick = () => this.addBookmark();
    // guided review-session controls
    $("rsExit").onclick = () => this.exitSession();
    $("rsReveal").onclick = () => this.sessionReveal();
    $("rsPrev").onclick = () => this.sessionStep(-1);
    $("rsNext").onclick = () => this.sessionStep(1);
    $("rsSaveNote").onclick = () => this.sessionNote();
    $("rsNote").addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter") this.sessionNote(); });
    // search panel
    $("searchBtn").onclick = () => this.openSearch();
    $("searchClose").onclick = () => $("searchModal").classList.remove("show");
    $("searchModal").addEventListener("click", (e) => { if (e.target.id === "searchModal") $("searchModal").classList.remove("show"); });
    $("overlayClose").onclick = () => this.closeOverlay();
    // Universal "exit": Esc closes the topmost open modal / video / analytics / overlay, so no UI --
    // especially an error state -- can trap the user. Replay hotkeys ignore Escape, so this is safe.
    window.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if ($("confirmModal") && $("confirmModal").classList.contains("show")) return;   // has its own Esc
      const open = [...document.querySelectorAll(".nv-modal.show")];
      if (open.length) {
        open.sort((a, b) => (parseInt(getComputedStyle(b).zIndex) || 0) - (parseInt(getComputedStyle(a).zIndex) || 0));
        if (open[0].id === "nadeVideo") this.hideVideo(); else open[0].classList.remove("show");
        e.stopPropagation(); return;
      }
      if ($("analyticsPanel") && $("analyticsPanel").classList.contains("show")) {
        closeAnalytics(this); e.stopPropagation(); return;
      }
      if ($("overlay").classList.contains("show")) { this.closeOverlay(); e.stopPropagation(); }
    });
    $("srRun").onclick = () => this.runSearch();
    $("srSaveRoutine").onclick = () => this.saveRoutine();
    ["srWinner", "srBuyCt", "srBuyT"].forEach(id => $(id).onchange = () => this.runSearch());
    document.querySelectorAll(".uptab").forEach(b => b.onclick = () => this.setUtilMode(b.dataset.umode));
    $("libAdd").onclick = () => this.toggleAddForm();
    $("libFromDemo").onclick = () => this.libFromDemo();
    $("libImport").onclick = () => this.libImport();
    $("libSuggest").onclick = () => this.suggestNades();
    $("pbAdd").onclick = () => this.savePlayFromRound();
    $("nvClose").onclick = () => this.hideVideo();
    $("nadeVideo").addEventListener("click", (e) => { if (e.target.id === "nadeVideo") this.hideVideo(); });
    $("toggleNames").onclick = () => {
      this.radar.showNames = !this.radar.showNames;
      $("toggleNames").classList.toggle("on", this.radar.showNames);
      this.saveSettings();
    };
    $("sampleBtn").onclick = () => this.loadSample();
    $("uploadBtn").onclick = () => this.startUpload();
    $("fileInput").onchange = (e) => {
      if (e.target.files.length) this.upload(e.target.files, this._pendingUploadTeam);
      this._pendingUploadTeam = null;                   // consume the chosen destination
      e.target.value = "";                              // allow re-picking the same file(s)
    };
    $("libraryBtn").onclick = () => this.openLibrary();
    { const th = $("tourHelp"); if (th) th.onclick = () => this.startTour(true); }
    $("sideCollapse").onclick = () => this.setRightPanel(false);
    $("sideRestore").onclick = () => this.setRightPanel(true);
    if (localStorage.getItem("cs2dp_rpanel") === "0") this.setRightPanel(false);   // restore saved state
    $("libUpload").onclick = () => this.startUpload();
    $("libClose").onclick = () => $("libraryModal").classList.remove("show");
    $("libraryModal").addEventListener("click", (e) => {
      if (e.target.id === "libraryModal") $("libraryModal").classList.remove("show");
    });

    // drag & drop -- accept any number of .dem / .zip files
    const dz = document.body;
    ["dragover", "drop"].forEach(ev => dz.addEventListener(ev, e => e.preventDefault()));
    dz.addEventListener("dragover", () => $("dropHint").classList.add("show"));
    dz.addEventListener("dragleave", () => $("dropHint").classList.remove("show"));
    dz.addEventListener("drop", (e) => {
      $("dropHint").classList.remove("show");
      const fs = [...e.dataTransfer.files].filter(f => /\.(dem|zip)$/i.test(f.name));
      if (fs.length) {
        const ws = this._currentWorkspace();
        if (ws.indexOf("team:") === 0) this.upload(fs, +ws.slice(5));                   // team dashboard default
        else this._chooseUploadDest((dest) => this.upload(fs, dest.team_id));           // else ask Personal/Team
      }
    });

    // canvas mouse
    const c = $("canvas");
    let dragging = false, lastX = 0, lastY = 0, moved = 0;
    c.addEventListener("pointerdown", (e) => {
      dragging = true; moved = 0; lastX = e.clientX; lastY = e.clientY;
      c.setPointerCapture(e.pointerId);
    });
    c.addEventListener("pointermove", (e) => {
      if (dragging) {
        const dx = e.clientX - lastX, dy = e.clientY - lastY;
        moved += Math.abs(dx) + Math.abs(dy);
        this.radar.pan(dx, dy);
        lastX = e.clientX; lastY = e.clientY;
        return;
      }
      // not dragging -> hover-highlight a utility trajectory under the cursor (2D util mode)
      if (!this.demo || this.nadeCapture || this.view3d.active) { this._setNadeHover(null); return; }
      const r = c.getBoundingClientRect();
      this._setNadeHover(this.radar.pickNade(e.clientX - r.left, e.clientY - r.top));
    });
    c.addEventListener("pointerleave", () => this._setNadeHover(null));
    c.addEventListener("pointerup", (e) => {
      dragging = false;
      if (moved >= 4 || !this.demo) return;
      const r = c.getBoundingClientRect();
      if (this.nadeCapture) {                       // capturing a lineup throw/landing position
        this.captureNadePos(e.clientX - r.left, e.clientY - r.top);
        return;
      }
      const g = this.radar.pickNade(e.clientX - r.left, e.clientY - r.top);   // util trajectory?
      if (g) { this.confirmJumpUtil(g, true); return; }   // -> confirm -> jump to throw + thrower POV
      const idx = this.radar.pick(e.clientX - r.left, e.clientY - r.top, this.curState);
      if (idx >= 0) this.setSpectate(idx);          // otherwise: click -> spectate
    });
    c.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = c.getBoundingClientRect();
      this.radar.zoomAt(e.deltaY < 0 ? 1.15 : 1 / 1.15, e.clientX - r.left, e.clientY - r.top);
    }, { passive: false });
    c.addEventListener("dblclick", (e) => {     // double-click map -> enter 3D there
      if (!this.demo) return;
      const r = c.getBoundingClientRect();
      const [wx, wy] = this.radar.worldFromScreen(e.clientX - r.left, e.clientY - r.top);
      this.enter3D(wx, wy);
    });

    // keyboard
    window.addEventListener("keydown", (e) => {
      if (!this.demo) return;
      if (/^(input|textarea|select)$/i.test((e.target && e.target.tagName) || "")) return;   // typing, not a shortcut
      // 3D movement keys are consumed by the fly-cam; Space stays global (play/pause everywhere).
      if (this.view3d.active && ["w", "a", "s", "d", "q", "e"].includes(e.key.toLowerCase())) return;
      switch (e.key) {
        case " ": e.preventDefault(); this.togglePlay(); break;
        case "ArrowRight": this.t = Math.min(this.demo.duration, this.t + (e.shiftKey ? 1 : 5)); break;
        case "ArrowLeft": this.t = Math.max(0, this.t - (e.shiftKey ? 1 : 5)); break;
        case "]": this.jumpRound(1); break;
        case "[": this.jumpRound(-1); break;
        case "1": case "2": case "3": case "4": case "5":
        case "6": case "7": case "8": case "9": this.spectateSlot(+e.key); break;
        case "0": this.spectateSlot(10); break;   // 1-5 = CT top->bottom, 6-0 = T top->bottom
        case "f": this.freeCamera(); break;
        case "c": this.cycleCamPreset(); break;
        case "v": this.togglePOV(); break;
        case "+": case "=": this.bumpSpeed(1); break;
        case "-": this.bumpSpeed(-1); break;
      }
    });
  },

  // --- loading --------------------------------------------------------------
  // #20: keep the last-viewed demo's parsed JSON in memory for 5 minutes so bouncing around the app
  // (dashboard <-> library <-> replay, or re-opening the sample) doesn't re-download + re-parse a big
  // payload every time. In-memory, not sessionStorage -- a parsed demo can be tens of MB.
  _cachedDemo(id) {
    const c = this._demoCache;
    return (c && c.id === id && (Date.now() - c.ts) < 5 * 60 * 1000) ? c.json : null;
  },
  _cacheDemo(id, json) { this._demoCache = { id, json, ts: Date.now() }; },

  async loadSample() {
    const hit = this._cachedDemo("__sample__");
    if (hit) { this.loadDemo(hit, true); return; }         // reuse within the 5-min window (no 72MB refetch)
    this.showOverlay("Loading sample demo...");
    try {
      const json = await fetch("api/sample?t=" + Date.now()).then(r => {
        if (!r.ok) throw new Error("sample not found");
        return r.json();
      });
      this._cacheDemo("__sample__", json);
      this.loadDemo(json, true);                          // sample -> unlock Pro features for preview
    } catch (err) {
      this.showOverlay("Could not load sample: " + err.message, true);
    }
  },

  // Upload one OR many files (.dem and/or .zip). The server parses + saves each to
  // the library and returns a per-file result list {demos:[{id,name,map,score,ok}|...]}.
  // Each file uploads in its OWN request (sequentially), so a batch of big demos never exceeds the
  // per-request size cap -- each just enqueues a parse job. The single parse worker drains the queue.
  // Click any "Upload" entry point -> ask where the demos should go (Personal or one of the user's
  // teams), THEN open the file picker. Solo users (no teams) skip the prompt and go straight to it.
  // onPick({team_id}) is called from within the choosing click so the file-picker gesture is preserved.
  startUpload() {
    const ws = this._currentWorkspace();
    if (ws.indexOf("team:") === 0) {                      // a team dashboard -> default straight to it
      this._pendingUploadTeam = +ws.slice(5);
      $("fileInput").click();
      return;
    }
    this._chooseUploadDest((dest) => {                    // Personal dashboard -> chooser (Personal/team)
      this._pendingUploadTeam = dest.team_id;             // read back in fileInput.onchange
      $("fileInput").click();
    });
  },
  // Destination chooser (reuses the confirm modal). Calls onPick({team_id:number|null}) on a choice;
  // does nothing if cancelled (X / backdrop / Esc) so the upload is simply abandoned.
  _chooseUploadDest(onPick) {
    const teams = this.myTeams || [];
    if (!teams.length) { onPick({ team_id: null }); return; }   // no teams -> personal, no prompt
    const modal = $("confirmModal");
    $("cfTitle").textContent = "Upload to…";
    const btns = '<button class="btn primary cf-dest" data-tid="">Personal library</button>'
      + teams.map(t => `<button class="btn cf-dest" data-tid="${t.id}">${esc(t.name)}</button>`).join("");
    $("cfBody").innerHTML = '<div class="cf-line cf-mut">Choose where these demos go. A team upload is '
      + 'visible to everyone on that team; you can still move it later from the library.</div>'
      + `<div class="cf-dests">${btns}</div>`;
    $("cfYes").style.display = "none";                    // the destination buttons replace Yes
    $("cfNo").textContent = "Cancel";
    modal.classList.add("show");
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); done(null); } };
    const done = (picked) => {
      modal.classList.remove("show");
      $("cfYes").style.display = "";                      // restore for the normal Yes/No confirm
      $("cfNo").textContent = "No";
      $("cfNo").onclick = modal.onclick = null;
      window.removeEventListener("keydown", onKey, true);
      if (picked) onPick(picked);
    };
    $("cfBody").querySelectorAll(".cf-dest").forEach(b => b.onclick = () =>
      done({ team_id: b.dataset.tid ? parseInt(b.dataset.tid, 10) : null }));
    $("cfNo").onclick = () => done(null);
    modal.onclick = (e) => { if (e.target.id === "confirmModal") done(null); };
    window.addEventListener("keydown", onKey, true);
  },

  async upload(files, teamId = null) {
    const list = files instanceof FileList ? [...files]
      : Array.isArray(files) ? files : [files];
    if (!list.length) return;
    this._upStrip("uploading", { pct: 0, label: list.length === 1 ? "Uploading…" : `Uploading ${list.length} files…` });
    const queued = [];
    for (let i = 0; i < list.length; i++) {
      const f = list[i];
      const prefix = list.length === 1 ? "" : `(${i + 1}/${list.length}) `;
      let res;
      try {
        const up = await this._prepUpload(f, prefix);     // gzip the .dem in-browser if we can
        res = await this._uploadOne(up.blob, up.name, prefix, teamId);
      }
      catch (e) { this._upStrip("hide"); this.showOverlay(`Upload failed -- ${f.name} (is the server running?)`, true); return; }
      if (res.error) { this._upStrip("hide"); this.showOverlay(res.upsell ? res.error : ("Server error: " + res.error), true); return; }
      if (res.jobs) queued.push(...res.jobs);
      else if (res.demos) this.onUploaded(res.demos);     // legacy ?sync response (not used by default)
    }
    if (queued.length) this._trackJobs(queued);           // poll all enqueued jobs together
  },

  // Compress a .dem in the browser before upload. CS2 demos are already internally compressed so
  // gzip only buys ~1.5x, but that's ~30% off the (bandwidth-bound) transfer for free. Uses the
  // native CompressionStream -- no library. Falls back to the raw file if unsupported, not a .dem,
  // too small to bother, or if compression didn't actually shrink it. Returns {blob, name}.
  async _prepUpload(file, prefix) {
    const isDem = /\.dem$/i.test(file.name || "");
    if (!isDem || typeof CompressionStream === "undefined" || !file.stream || file.size < 4 * 1024 * 1024) {
      return { blob: file, name: file.name };
    }
    try {
      const total = file.size; let read = 0;
      this._upStrip("uploading", { pct: 0, label: "Compressing…" });
      const counter = new TransformStream({
        transform: (chunk, ctrl) => {
          read += chunk.byteLength;
          const pct = Math.round(read / total * 100);
          this._upStrip("uploading", { pct, label: `Compressing… ${pct}%` });
          ctrl.enqueue(chunk);
        },
      });
      const stream = file.stream().pipeThrough(counter).pipeThrough(new CompressionStream("gzip"));
      const blob = await new Response(stream).blob();
      if (blob.size && blob.size < file.size * 0.95) return { blob, name: file.name + ".gz" };
    } catch (e) { /* any failure -> upload the raw file below */ }
    return { blob: file, name: file.name };
  },

  // POST a single file (or compressed blob) to /api/upload with progress. Resolves to the JSON
  // response ({jobs:[...]} ok, or {error,upsell} on a non-200); rejects only on a network failure.
  _uploadOne(blob, name, prefix, teamId = null) {
    return new Promise((resolve, reject) => {
      const fd = new FormData();
      fd.append("files", blob, name);
      if (teamId != null) fd.append("team_id", teamId);   // upload destination: a team (else personal)
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "api/upload");
      xhr.upload.onprogress = (e) => {
        if (!e.lengthComputable) return;
        const pct = Math.round(e.loaded / e.total * 100);
        if (pct >= 100) this._upStrip("uploading", { pct: 100, label: "Queued for parsing…" });
        else this._upStrip("uploading", { pct, label: `Uploading… ${pct}%` });
      };
      xhr.onload = () => {
        _activeUploads = Math.max(0, _activeUploads - 1);
        let j = null; try { j = JSON.parse(xhr.responseText); } catch (e) { /* non-JSON */ }
        if (xhr.status !== 200) resolve({ error: (j && j.error) || xhr.statusText || ("HTTP " + xhr.status), upsell: !!(j && j.upsell) });
        else resolve(j || {});
      };
      xhr.onerror = () => { _activeUploads = Math.max(0, _activeUploads - 1); reject(new Error("network")); };
      _activeUploads++;
      xhr.send(fd);
    });
  },

  // Poll background parse jobs from an async upload; drive the overlay; on completion open the
  // single new demo (or the library for a batch) and surface any failures. (Stage 3 job queue.)
  _trackJobs(jobList) {
    const ok = (jobList || []).filter(j => j && j.id && j.ok !== false);
    const bad = (jobList || []).filter(j => j && j.ok === false);
    if (!ok.length) {
      const f = bad[0];
      this._upStrip("failed", { label: "Upload rejected" + (f && f.error ? ` — ${f.error}` : ""), autohide: 9000 });
      return;
    }
    // The strip stays up through parsing (non-blocking) so the user can keep browsing the app.
    // The parser has no sub-progress, so we show an ESTIMATED % + ETA: each job's est_total_s (from
    // file size, server-calibrated) vs how long it's actually been parsing. A 1s ticker advances the
    // countdown smoothly between the slower status polls.
    const n = ok.length;
    if (document.body.classList.contains("on-dashboard")) this.loadDashboard();
    const ids = new Set(ok.map(j => j.id));
    const est = {};                 // jobId -> estimated total parse seconds
    const seen = {};                // jobId -> ms when first observed working (elapsed base)
    const doneIds = new Set();
    let stage = "Parsing", finished = false;
    this._jobDismissed = false;
    this._liveJobs = new Map();      // jobId -> latest status (beforeunload guard reads this)
    const DEFAULT_EST = 40;         // sec, when the server couldn't size the file
    const fmtEta = (s) => s >= 60 ? `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}` : `${Math.round(s)}s`;
    const render = () => {
      if (this._jobDismissed) return;
      const now = Date.now();
      let totalEst = 0, doneEst = 0, working = 0;
      ids.forEach(id => {
        const e = est[id] || DEFAULT_EST;
        totalEst += e;
        if (doneIds.has(id)) { doneEst += e; return; }
        working++;
        if (seen[id]) doneEst += Math.min((now - seen[id]) / 1000, e * 0.99);   // cap < 100% until done
      });
      const pct = Math.min(99, Math.max(1, Math.round(doneEst / (totalEst || 1) * 100)));
      const eta = Math.max(0, Math.round(totalEst - doneEst));
      const tail = eta > 0 ? ` · ~${fmtEta(eta)} left` : " · finishing…";
      this._upStrip("parsing", { pct, label: `${stage} ${working} demo${working > 1 ? "s" : ""}… ${pct}%${tail}` });
    };
    clearInterval(this._jobTick);
    this._upStrip("parsing", { pct: 1, label: `Parsing ${n} demo${n > 1 ? "s" : ""}…` });
    this._jobTick = setInterval(() => { if (!finished) render(); }, 1000);   // smooth between polls
    const poll = async () => {
      const all = await fetch("api/jobs").then(r => r.json()).then(j => j.jobs || []).catch(() => null);
      if (!all) { setTimeout(poll, 3000); return; }
      const mine = all.filter(j => ids.has(j.id));
      const now = Date.now();
      mine.forEach(j => {
        this._liveJobs.set(j.id, j.status);
        if (j.est_total_s) est[j.id] = j.est_total_s;
        if (["parsing", "analyzing"].includes(j.status) && !seen[j.id]) seen[j.id] = now;
        if (j.status === "done") { doneIds.add(j.id); if (!seen[j.id]) seen[j.id] = now; }
      });
      const working = mine.filter(j => ["queued", "parsing", "analyzing"].includes(j.status));
      if (working.length) {
        stage = working.some(j => j.status === "analyzing") ? "Finishing"
          : working.some(j => j.status === "parsing") ? "Parsing" : "Queued";
        render();
        setTimeout(poll, 2000); return;
      }
      finished = true; clearInterval(this._jobTick); this._liveJobs.clear();   // nothing active -> drop the guard
      const done = mine.filter(j => j.status === "done");
      const failed = mine.filter(j => j.status === "failed");
      if (failed.length) console.warn("Parse failures:\n" +
        failed.map(j => `  ${j.filename}: ${(j.error || "").split("\n")[0]}`).join("\n"));
      if (done.length && !this._jobDismissed) {
        this._upStrip("done", { pct: 100, label: `✓ ${done.length} demo${done.length > 1 ? "s" : ""} ready${failed.length ? ` (${failed.length} failed)` : ""}`, autohide: 6000 });
      } else if (failed.length && !this._jobDismissed) {
        const f = failed[0];
        this._upStrip("failed", { label: `✕ Parse failed — ${(f.error || "").split("\n")[0] || "error"}`.slice(0, 90), autohide: 9000 });
      }
      if (done.length) this._toast && this._toast(`${done.length} demo${done.length > 1 ? "s" : ""} ready — open Library to view.`);
      if (document.body.classList.contains("on-dashboard")) this.loadDashboard();   // surface the new match
    };
    setTimeout(poll, 1500);
  },

  // Per-file upload results -> load a single new demo, or drop into the library
  // to pick from a batch. Failures are surfaced (overlay if all failed, else logged).
  onUploaded(demos) {
    const ok = demos.filter(d => d && d.ok);
    const bad = demos.filter(d => d && !d.ok);
    if (bad.length) {
      console.warn("Some uploads failed:\n" +
        bad.map(d => `  ${d.name || "file"}: ${d.error || "failed"}`).join("\n"));
    }
    if (!ok.length) {
      const first = bad[0];
      this.showOverlay("Upload failed -- " +
        (first ? `${first.name || "file"}: ${first.error || "could not parse"}` : "no demos parsed")
        + (bad.length > 1 ? ` (+${bad.length - 1} more)` : ""), true);
      return;
    }
    if (ok.length === 1) {
      this.viewLibraryDemo(ok[0].id);                 // single new demo -> open it
    } else {
      if (this.demo) this.hideOverlay();
      else this.showOverlay(`Saved ${ok.length} demos to your library.`
        + (bad.length ? ` (${bad.length} failed)` : ""), false);
      this.openLibrary();                              // batch -> let them choose
    }
  },

  // Open the library modal and (re)load its list from the server.
  openLibrary() {
    this.pausePlayback();
    // opening the library from the dashboard mirrors the active workspace (Personal -> Personal tab,
    // a team -> that team's tab); maps directly to the library filter keys ("personal" | "team:<id>")
    if (document.body.classList.contains("on-dashboard")) this._libFilter = this._currentWorkspace();
    $("libraryModal").classList.add("show");
    $("libFilters").innerHTML = "";
    $("libList").innerHTML = '<div class="lib-empty">Loading...</div>';
    fetch("api/library").then(r => r.json())
      .then(j => {
        this._libAll = (j && j.demos) || [];
        this._libTeams = (j && j.teams) || [];           // [{id,name}] for the Personal/Team split (#23)
        if (!this._libFilter) this._libFilter = "all";
        this.renderLibrary();
      })
      .catch(() => { $("libList").innerHTML =
        '<div class="lib-empty">Could not load the library (is the server running?)</div>'; });
  },

  // de_dust2 -> "Dust2", de_mirage -> "Mirage" (clean card titles instead of raw filenames, #21)
  _prettyMap(map) {
    if (!map) return "Unknown map";
    const m = String(map).replace(/^(de|cs|ar)_/i, "");
    return m ? m.charAt(0).toUpperCase() + m.slice(1) : "Unknown map";
  },
  // map -> loading-screen background image for the library card (null = no art, card stays solid). #21
  _mapBg(map) {
    const BG = {
      de_ancient: "de_ancient.jpg", de_anubis: "de_anubis.jpg", de_cache: "de_cache.png",
      de_dust2: "de_dust2.webp", de_mirage: "de_mirage.webp", de_inferno: "de_inferno.avif",
      de_nuke: "de_nuke.jpg", de_overpass: "de_overpass.webp",
    };
    const f = BG[String(map || "").toLowerCase()];
    // root-absolute: this feeds a CSS url() in a custom property, which resolves relative to the
    // STYLESHEET (static/css/) not the document -- a leading slash avoids static/css/static/... 404s.
    return f ? "/static/img/mapbg/" + f : null;
  },

  // #23: Personal vs Team library split. Filter chips appear only when the user is in a team
  // (solo players keep the flat list). Each card gets a team badge when it's shared with a team.
  renderLibrary() {
    const all = this._libAll || [];
    const teams = this._libTeams || [];
    const teamName = (id) => { const t = teams.find(x => x.id === id); return t ? t.name : "Team"; };
    const fb = $("libFilters");
    if (teams.length) {
      const f = this._libFilter || "all";
      const n = (pred) => all.filter(pred).length;
      const chips = [{ key: "all", label: "All", n: all.length },
                     { key: "personal", label: "Personal", n: n(d => d.personal) }]
        .concat(teams.map(t => ({ key: "team:" + t.id, label: t.name, n: n(d => (d.team_ids || []).includes(t.id)) })));
      fb.innerHTML = chips.map(c => '<button class="lib-chip' + (c.key === f ? ' on' : '')
        + '" data-f="' + esc(c.key) + '">' + esc(c.label)
        + '<span class="lib-chip-n">' + c.n + '</span></button>').join("");
      fb.querySelectorAll(".lib-chip").forEach(b =>
        b.onclick = () => { this._libFilter = b.dataset.f; this.renderLibrary(); });
      fb.style.display = "";
    } else {
      fb.innerHTML = ""; fb.style.display = "none";
    }
    const f = teams.length ? (this._libFilter || "all") : "all";
    let demos = all;
    if (f === "personal") demos = all.filter(d => d.personal);
    else if (f.indexOf("team:") === 0) { const id = +f.slice(5); demos = all.filter(d => (d.team_ids || []).includes(id)); }

    $("demoLibCount").textContent = all.length ? `(${all.length})` : "";
    const list = $("libList");
    if (!all.length) {
      list.innerHTML = '<div class="lib-empty">No saved demos yet. Upload one or more '
        + '<b>.dem</b> files (or a <b>.zip</b>) and they\'ll appear here.</div>';
      return;
    }
    if (!demos.length) {
      list.innerHTML = '<div class="lib-empty">No demos in this view.</div>';
      return;
    }
    list.innerHTML = demos.map(d => {
      const sc = d.score || { ct: 0, t: 0 };
      const when = this._fmtDate(d.date);
      const pretty = this._prettyMap(d.map);
      const bg = this._mapBg(d.map);
      const stale = d.stale
        ? '<span class="lib-stale" title="Parsed with an older app version -- re-upload to refresh">outdated</span>'
        : "";
      const tnames = (d.team_ids || []).map(teamName);
      const teamBadge = tnames.length
        ? '<span class="lib-team" title="Shared with ' + esc(tnames.join(", ")) + '">' + esc(tnames.join(" · ")) + '</span>'
        : "";
      const meta = [(d.rounds || 0) + ' rounds', when].filter(Boolean).join(' · ');
      const label = [pretty, when].filter(Boolean).join(' · ');   // for the confirm dialog, not the raw filename
      // background = the map's loading-screen art at 30% opacity (rendered in a ::before layer via
      // this CSS var); raw filename moves to the hover tooltip (#21: cards read like match history).
      const style = bg ? ' style="--cardbg:url(\'' + bg + '\')"' : '';
      return '<div class="lib-row' + (bg ? ' has-bg' : '') + '" data-id="' + esc(d.id) + '" data-label="' + esc(label) + '"'
        + ' title="' + esc(d.name || d.id) + '"' + style + '>'
        + '<div class="lib-mid"><div class="lib-name">' + esc(pretty) + stale + teamBadge + '</div>'
        +   '<div class="lib-meta">' + esc(meta) + '</div></div>'
        + '<div class="lib-score">' + sc.ct + '<span>:</span>' + sc.t + '</div>'
        + ((this.myTeams && this.myTeams.length)
            ? '<button class="lib-share btn ghost sm" title="Share this match with a team">Share</button>' : '')
        + '<button class="btn primary lib-view">View</button>'
        + '<button class="lib-del" title="Delete replay (keeps your compact stats)">&#128465;</button></div>';
    }).join("");
    list.querySelectorAll(".lib-row").forEach(row => {
      row.querySelector(".lib-view").onclick = () => this.viewLibraryDemo(row.dataset.id);
      const sh = row.querySelector(".lib-share");
      if (sh) sh.onclick = (e) => { e.stopPropagation(); this.shareDemo(row.dataset.id); };
      row.querySelector(".lib-del").onclick = (e) => {
        e.stopPropagation();
        this.deleteLibraryDemo(row.dataset.id, row.dataset.label || row.querySelector(".lib-name").textContent.trim(), row);
      };
    });
  },

  // Delete = remove the replay (raw .dem + parsed cache) and drop it from the library to free storage,
  // but the backend keeps a tiny compact .txt stats record so profile/goals/trends retain the match.
  async deleteLibraryDemo(id, name, rowEl) {
    const body = `<div class="cf-line">Delete <b>${esc(name || id)}</b>?</div>`
      + `<div class="cf-line cf-mut">This removes the replay from your library and frees storage, but keeps `
      + `compact stats for your profile, goals, and long-term analytics.</div>`;
    if (!(await this.askConfirm("Delete this replay?", body, "Delete"))) return;
    const res = await fetch("api/demo/" + encodeURIComponent(id), { method: "DELETE" })
      .then(r => r.json()).catch(() => null);
    if (!res || !res.ok) { this._toast && this._toast("Could not delete that demo."); return; }
    const mb = (res.freed_bytes || 0) / (1 << 20);
    const freed = res.freed_bytes ? ` — freed ${mb >= 1024 ? (mb / 1024).toFixed(1) + " GB" : Math.round(mb) + " MB"}` : "";
    // drop it from the cached list and re-render so the filter chips' counts stay accurate (#23)
    this._libAll = (this._libAll || []).filter(d => d.id !== id);
    this.renderLibrary();
    this._toast && this._toast(`Replay removed${freed}. Stats kept.`);
    if (document.body.classList.contains("on-dashboard")) this.loadDashboard();
  },

  viewLibraryDemo(id, opts) {
    opts = opts || {};
    $("libraryModal").classList.remove("show");
    const afterLoad = () => {                              // jump straight into per-match analytics if asked
      if (opts.analytics) {
        if (this.entitled("advancedAnalytics")) openAnalytics(this);
        else this._upsell("advancedAnalytics");
      }
    };
    const hit = this._cachedDemo(id);                      // #20: reuse within the 5-min window
    if (hit) { this.loadDemo(hit, false); afterLoad(); return Promise.resolve(); }
    this.showOverlay("Loading demo...", false, 0);
    return fetch("api/demo/" + encodeURIComponent(id))
      .then(r => { if (!r.ok) throw new Error("not found"); return r.json(); })
      .then(json => {
        this._cacheDemo(id, json);                         // remember for the 5-min reuse window
        this.loadDemo(json, false);                       // uploaded demo -> normal Free/Pro gating
        afterLoad();
      })
      .catch(() => this.showOverlay("Could not load that demo (it may have been removed).", true));
  },

  _fmtDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    try {
      return d.toLocaleString(undefined,
        { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
    } catch { return iso; }
  },

  loadDemo(json, isSample) {
    const mapMeta = this.maps[json.map];
    if (!mapMeta) {
      this.showOverlay(`No radar image for map "${json.map}". `
        + `Run fetch_radars.py or add ${json.map}.png to static/maps/.`, true);
      return;
    }
    const img = new Image();
    const lower = mapMeta.lower ? new Image() : null;
    let pending = 1 + (lower ? 1 : 0);
    const done = () => {
      if (--pending > 0) return;
      this.hideDashboard();                         // reveal the replay stage (it was hidden on the dashboard)
      this.demo = new Cs2Demo(json);
      this._sampleLoaded = !!isSample;                // sample = free preview of per-match Pro features
      this._applyGates();                             // refresh PRO pills for this demo (none on the sample)
      try { localStorage.setItem("cs2dp_last_review",      // for the dashboard "Continue" button
        JSON.stringify({ id: json.source_sha1 || json.id, map: json.map })); } catch (e) { /* ignore */ }
      this.clearPositionsOnMap();                   // drop any prior match's death/kill spots
      this._suggRaw = null; if (this.closeSuggest) this.closeSuggest();   // suggestions are per-demo
      this.radar.setMap(mapMeta, img, lower);
      this.miniRadar.setMap(mapMeta, img, lower);   // 3D-overlay minimap (re-fit on enter3D when sized)
      this.view3d.setMap(mapMeta, img);
      this.view3d.setDemo(this.demo);
      this.view3d.loadGeo(json.map);
      this.exit3D();
      // start at the first live round, not the warmup transition (where money is still warmup $)
      const r1 = this.demo.rounds[0];
      this.t = r1 ? (r1.freeze_end_t ?? r1.start_t ?? 0) : 0;
      this.playing = false; this.setSpectate(-1);
      this.buildScoreboard();
      this.buildRoundStrip();
      // timeline defaults to per-round scope; refreshTimeline() sets the slider min/max + markers
      this.tlMode = "round"; this._tlRound = -1;
      $("tlMode").textContent = "Round"; $("tlMode").classList.add("on");
      this.refreshTimeline();
      this.buildSettings();
      this.buildUtilSearch();
      this.radar.searchOverlay = [];
      $("utilPanel").classList.remove("show"); $("toggleUtil").classList.remove("on");
      $("mapName").textContent = json.map;
      this.update3dBadge(json.map);
      $("durLabel").textContent = fmt(this.demo.duration);
      $("hud").classList.add("ready");
      this.hideOverlay();
      this.togglePlay();
    };
    img.onload = done; img.onerror = () => this.showOverlay("Failed to load radar image", true);
    img.src = `static/maps/${mapMeta.image}`;
    if (lower) { lower.onload = done; lower.src = `static/maps/${mapMeta.lower.image}`; }
  },

  // --- playback -------------------------------------------------------------
  togglePlay() {
    if (!this.demo) return;
    if (this.t >= this.demo.duration - 0.01) this.t = 0;
    this.playing = !this.playing;
    $("playPause").textContent = this.playing ? "\u23f8" : "\u25b6";
  },
  // Pause the replay when the user opens a separate page/panel over it (analytics, trends, library,
  // goals, teams, admin) so it doesn't keep advancing in the background. No-op if already paused.
  pausePlayback() {
    if (this.playing) this.togglePlay();
  },
  bumpSpeed(dir) {
    const opts = [0.25, 0.5, 1, 2, 4, 8];
    let i = opts.indexOf(this.speed); if (i < 0) i = 2;
    i = Math.max(0, Math.min(opts.length - 1, i + dir));
    this.speed = opts[i]; $("speed").value = String(this.speed);
  },
  jumpRound(dir) {
    if (!this.demo) return;
    const rs = this.demo.rounds;
    const cur = this.demo.roundAt(this.t);
    let n = (cur ? cur.number : 1) + dir;
    n = Math.max(1, Math.min(rs.length, n));
    const r = rs.find(x => x.number === n);
    if (r) { this.t = this._roundSeekT(r); }
  },

  // --- spectator ------------------------------------------------------------
  setSpectate(idx) {
    this.radar.follow(idx);
    this.view3d.follow(idx);
    if (idx < 0) {
      $("specLabel").textContent = "Free cam";
      $("freeCam").classList.add("on");
    } else {
      $("specLabel").textContent = "Spectating: " + this.demo.players[idx].name;
      $("freeCam").classList.remove("on");
    }
    this.updateRowHighlight();
  },
  // Spectate by scoreboard slot: 1-5 = CT (top->bottom on the left board), 6-9,0 = T (0 = slot 10).
  // Uses the live DOM order of the scoreboard rows, so it always matches what the user sees.
  spectateSlot(slot) {
    if (!this.demo) return;
    const rows = [...$(slot <= 5 ? "sbCT" : "sbT").children];
    const row = rows[slot <= 5 ? slot - 1 : slot - 6];
    if (row && row.dataset.idx != null) this.setSpectate(+row.dataset.idx);
  },
  cycleSpectate(dir) {
    if (!this.demo) return;
    const st = this.curState || this.demo.stateAt(this.t);   // may be called before the first render frame
    if (!st) return;
    const alive = st.players
      .map((p, i) => (p && p.alive ? i : -1)).filter(i => i >= 0);
    if (!alive.length) return;
    let pos = alive.indexOf(this.radar.followIdx);
    pos = (pos + dir + alive.length) % alive.length;
    this.setSpectate(alive[pos]);
  },
  // F key / Free button -> true free camera. In 3D this must ALSO drop any scripted cam
  // preset (overhead/utility/death/follow), not just the follow target -- otherwise the
  // preset keeps driving the camera and view3d.update() skips manual fly (the "stuck" bug).
  freeCamera() {
    if (this.view3d.active) this.setCamPreset("fly");   // clears scripted preset + follow, button -> Fly
    else this.setSpectate(-1);                          // 2D: just free the camera / stop spectating
  },

  // --- 3D view --------------------------------------------------------------
  toggle3d() {
    if (!this.demo) return;
    if (this.view3d.active) this.exit3D();
    else { const [wx, wy] = this.radar.cameraWorldCenter(); this.enter3D(wx, wy); }
  },
  enter3D(wx, wy) {
    if (!this.demo) return;
    if (!this.entitled("threeD")) { this._upsell("threeD"); return; }   // 3D is Pro -- single chokepoint
    // ground height for the landing = nearest alive player's z (reliable + available before the GLB
    // loads; a flat zRef / un-loaded raycast was dropping the camera under multi-level maps)
    let gz = null, best = Infinity;
    const st = this.curState || this.demo.stateAt(this.t);
    if (st) for (const p of st.players) {
      if (p && p.alive) { const d = (p.x - wx) ** 2 + (p.y - wy) ** 2; if (d < best) { best = d; gz = p.z; } }
    }
    // keep the hint honest as the GLB streams in / finishes (big maps decode for a few seconds)
    this.view3d.onGeoStatus = () => { if (this.view3d.active) this._update3dHint(); };
    this.view3d.enterAt(wx, wy, gz);
    this.view3d.follow(this.radar.followIdx);
    this.setCamPreset("fp");   // land in first-person on a player; press F (or Free) for the fly-cam
    $("tr3d").classList.add("show");                 // show the minimap + kill-feed stack
    if (this.miniRadar.map) { this.miniRadar.resize(); this.miniRadar.fit(); }   // now that it has size
    $("toggle3d").classList.add("on"); $("toggle3d").textContent = "2D";
    this._update3dHint();   // manages its own show/hide (status only; hidden on a verified map)
  },
  _update3dHint() {
    // Only a transient STATUS line now (controls live in the right-sidebar "Controls" list):
    // show while the map mesh streams in, or to warn an uncalibrated map -- otherwise hidden.
    const hint = $("view3dHint");
    if (this.view3d.geoLoading) {
      hint.innerHTML = `<b>Loading 3D geometry...</b> the map mesh is streaming in`;
      hint.classList.add("show");
    } else if (this.view3d._cfg) {
      hint.classList.remove("show");   // verified map -> no bottom hint
    } else {
      hint.innerHTML = `<b>3D geometry unavailable or uncalibrated for this map.</b> Flying over player positions only.`;
      hint.classList.add("show");
    }
  },
  exit3D() {
    this.view3d.exit();
    this.view3d.clearLineup3D();
    $("toggle3d").classList.remove("on"); $("toggle3d").textContent = "3D";
    $("view3dHint").classList.remove("show");
    document.querySelector(".viewport").classList.remove("fp");   // hide the FP crosshair
    $("tr3d").classList.remove("show");                            // hide the minimap + kill-feed stack
  },
  // Per-map 3D status badge in the header -- honest signal of whether THIS map has
  // calibrated geometry (verified in-browser against real spawns) or is positions-only.
  async update3dBadge(map) {
    const el = $("map3d"); if (!el) return;
    let cfg = null;
    try { cfg = (await this.view3d._getTransforms())[map]; } catch (e) { /* offline */ }
    if (cfg && cfg.verified) {
      const v = cfg.validation || {};
      const ok = (v.ct?.within20 ?? 0) + (v.t?.within20 ?? 0);
      const n = (v.ct?.n ?? 0) + (v.t?.n ?? 0);
      el.textContent = "3D \u2713";
      el.className = "map3d ok";
      el.title = `Calibrated 3D geometry -- ${ok}/${n} spawns floor-verified. `
        + `Press 3D or double-click the map.`;
    } else {
      el.textContent = "3D --";
      el.className = "map3d none";
      el.title = "No calibrated 3D geometry for this map -- 3D shows player positions only. Click for all maps.";
    }
  },

  // detailed 3D-asset status across ALL maps (click the header 3D chip)
  async show3dStatus() {
    const data = await fetch("api/maps3d/status").then(r => r.json()).catch(() => null);
    if (!data) { $("m3Body").innerHTML = `<div class="empty">status unavailable</div>`; }
    else {
      const cur = this.demo ? this.demo.map : null;
      const ico = { verified: "\u2713", unverified: "~", "geometry-missing": "!", "transform-missing": "?" };
      const rows = data.maps.map(m => {
        const v = m.validation || {};
        const detail = m.status === "verified"
          ? `${m.spawns ?? "?"} spawns | ${esc(v.coplanar_within15u || "")} on floor | ${m.glb_mb ?? "?"} MB`
          : (m.glb_present ? `geometry present, ${m.glb_mb ?? "?"} MB` : "no GLB on disk");
        return `<div class="m3-row ${m.status}${m.map === cur ? " cur" : ""}">
          <span class="m3-ico">${ico[m.status] || "-"}</span>
          <span class="m3-map">${esc(m.map)}</span>
          <span class="m3-rot">rot ${m.rotation ?? "--"}</span>
          <span class="m3-det">${detail}</span></div>`;
      }).join("");
      $("m3Body").innerHTML = `<div class="m3-sum">${data.summary.verified}/${data.summary.total} `
        + `verified | ${data.summary.with_geometry} with geometry</div>${rows}`;
    }
    $("map3dStatus").classList.add("show");
  },

  // --- squad: auto-detected teammates that drive the Goals + Trends player pickers --------
  async _loadSquad(force) {
    if (this._squad && !force) return this._squad;
    try { this._squad = await fetch("/api/squad", { cache: "no-store" }).then(r => r.json()); }
    catch (e) { this._squad = { available: false, you: null, squad: [], candidates: [] }; }
    return this._squad;
  },
  // Build <option>s for a player <select>: your squad (you + teammates) first, everyone else under
  // an "All players" group -- so the picker leads with your squad instead of every random.
  _squadOptions(allPlayers, selSid, opts) {
    opts = opts || {};
    const selA = (sid) => String(sid) === String(selSid) ? " selected" : "";
    const o = (sid, label) => `<option value="${esc(String(sid))}"${selA(sid)}>${esc(label)}</option>`;
    const sq = this._squad;
    // For goals (opts.includeGroups): lead with group-average options that track YOUR players only --
    // "My squad (avg)" (auto-detected stack) and "Team: X (avg)" per team you created/joined. submitGoal
    // expands the __squad__ / __team_<id>__ sentinels to scope.group + a members[] steamid snapshot.
    // (No "whole team" -- analytics.players is all 10, so that averaged in opponents.)
    const squadAvg = (opts.includeGroups && sq && sq.available && sq.squad && sq.squad.length)
      ? `<option value="__squad__"${selA("__squad__")}>My squad (avg)</option>` : "";
    const teamAvg = (opts.includeGroups ? (opts.teams || []) : [])
      .filter(t => (t.members || []).some(m => m.steamid))
      .map(t => `<option value="__team_${t.id}__"${selA("__team_" + t.id + "__")}>Team: ${esc(t.name)} (shared)</option>`).join("");
    const head = squadAvg + teamAvg;
    if (sq && sq.available && ((sq.squad && sq.squad.length) || (sq.you && sq.you.steamid))) {
      const inSquad = new Set(), squadOpts = [];
      if (sq.you && sq.you.steamid) { squadOpts.push(o(sq.you.steamid, (sq.you.name || "You") + " (you)")); inSquad.add(String(sq.you.steamid)); }
      (sq.squad || []).forEach(p => { const s = String(p.steamid); if (!inSquad.has(s)) { squadOpts.push(o(p.steamid, p.name)); inSquad.add(s); } });
      const others = (allPlayers || []).filter(p => !inSquad.has(String(p.steamid)))
        .map(p => o(p.steamid, p.name + (p.n_matches ? ` (${p.n_matches})` : "")));
      return head + `<optgroup label="Your squad">${squadOpts.join("")}</optgroup>`
        + (others.length ? `<optgroup label="All players">${others.join("")}</optgroup>` : "");
    }
    return head + (allPlayers || []).map(p => o(p.steamid, p.name + (p.n_matches ? ` (${p.n_matches})` : ""))).join("");
  },
  _renderSquadPanel() {
    const el = $("trSquad");
    if (!el) return;
    const sq = this._squad;
    if (!sq || !sq.available) {
      el.innerHTML = `<div class="empty">Upload a couple of your own matches and your squad auto-builds from who you play with.</div>`;
      return;
    }
    const nm = (sid, inner, title) => `<span class="sqd-n lnk" data-sid="${esc(sid)}" title="${esc(title)}">${inner}</span>`;
    const you = (sq.you && sq.you.steamid) ? `<div class="sqd-row sqd-you">${nm(sq.you.steamid, "<b>" + esc(sq.you.name || "You") + "</b>", "View your trend")}<span class="round">you</span></div>` : "";
    const members = (sq.squad || []).map(p =>
      `<div class="sqd-row">${nm(p.steamid, esc(p.name), "View " + (p.name || "player") + "'s trend")}<span class="round">${p.shared} match${p.shared === 1 ? "" : "es"}</span>`
      + `<button class="sqd-btn sqd-rm" data-sid="${esc(p.steamid)}" data-name="${esc(p.name)}" title="Remove from squad">&times;</button></div>`).join("")
      || `<div class="empty">No regular teammates yet — play 2+ matches with someone.</div>`;
    const cands = (sq.candidates || []).slice(0, 8).map(p =>
      `<div class="sqd-row sqd-cand">${nm(p.steamid, esc(p.name), "View " + (p.name || "player") + "'s trend")}<span class="round">${p.shared} match${p.shared === 1 ? "" : "es"}</span>`
      + `<button class="sqd-btn sqd-add" data-sid="${esc(p.steamid)}" data-name="${esc(p.name)}" title="Add to squad">+</button></div>`).join("");
    el.innerHTML = you + members + (cands ? `<div class="sqd-sub">Suggestions</div>${cands}` : "");
    el.querySelectorAll(".sqd-add").forEach(b => b.onclick = () => this._curateSquad(b.dataset.sid, b.dataset.name, "add"));
    el.querySelectorAll(".sqd-rm").forEach(b => b.onclick = () => this._curateSquad(b.dataset.sid, b.dataset.name, "remove"));
    el.querySelectorAll(".sqd-n.lnk").forEach(s => s.onclick = () => this._showPlayerTrend(s.dataset.sid));   // #24
  },
  // #24: clicking a teammate's name focuses their profile/trend in the trends view.
  _showPlayerTrend(sid) {
    if (!sid) return;
    const sel = $("trPlayer");
    if (sel && [...sel.options].some(o => o.value === String(sid))) sel.value = String(sid);
    this.renderTrend(String(sid), ($("trMap") && $("trMap").value) || "all");
    this.renderTendencies(String(sid));
    const tb = $("trBody"); if (tb) tb.scrollIntoView({ block: "nearest", behavior: "smooth" });
  },
  async _curateSquad(steamid, name, action) {
    try {
      this._squad = await fetch("/api/squad", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ steamid, name, action }) }).then(r => r.json());
    } catch (e) { this._toast && this._toast("Couldn't update squad"); return; }
    this._renderSquadPanel();
    const trsel = $("trPlayer");                         // keep the trends picker in sync with the squad
    if (trsel && $("trendsModal").classList.contains("show")) {
      const cur = trsel.value;
      try { const players = await fetch("api/players").then(r => r.json()); trsel.innerHTML = this._squadOptions(players, cur); } catch (e) { /* keep */ }
    }
  },

  // --- cross-demo dashboard analytics (Overview / Players / Issues / Demos) ----
  async openDashAnalytics() {
    if (!this.entitled("advancedAnalytics")) { this._upsell("advancedAnalytics"); return; }
    this.pausePlayback();
    const ws = this._currentWorkspace();
    const modal = $("dashAnalyticsModal");
    modal.classList.add("show");
    // reset to overview tab
    modal.querySelectorAll(".da-tab").forEach(b => { b.classList.toggle("on", b.dataset.da === "overview"); });
    this._daView = "overview";
    $("daBody").innerHTML = `<div class="da-loading">Loading…</div>`;
    const data = await fetch(`/api/dashboard/analytics?workspace=${encodeURIComponent(ws)}`)
      .then(r => r.json()).catch(() => null);
    if (!data || data.error) {
      $("daBody").innerHTML = `<div class="da-empty">${data && data.error ? esc(data.error) : "Could not load analytics."}</div>`;
      return;
    }
    $("daMatchCount").textContent = data.match_count ? `${data.match_count} match${data.match_count === 1 ? "" : "es"}` : "";
    this._daData = data;
    this._renderDaView();
    modal.querySelectorAll(".da-tab").forEach(btn => btn.onclick = () => {
      modal.querySelectorAll(".da-tab").forEach(b => b.classList.remove("on"));
      btn.classList.add("on");
      this._daView = btn.dataset.da;
      this._renderDaView();
    });
  },
  _renderDaView() {
    const data = this._daData;
    if (!data) return;
    const el = $("daBody"), view = this._daView || "overview";
    if (view === "match-detail") {
      el.innerHTML = this._daMatchDetailHtml(this._daMatchData);
      el.querySelector(".da-back-btn")?.addEventListener("click", () => {
        this._daView = "demos"; this._renderDaView();
        const modal = $("dashAnalyticsModal");
        modal.querySelectorAll(".da-tab").forEach(b => b.classList.toggle("on", b.dataset.da === "demos"));
      });
      el.querySelector(".da-replay-btn")?.addEventListener("click", () => {
        $("dashAnalyticsModal").classList.remove("show");
        this._openMatchAnalytics(this._daMatchData?.key);
      });
      return;
    }
    if (view === "overview") el.innerHTML = this._daOverviewHtml(data);
    else if (view === "players") {
      el.innerHTML = this._daPlayersHtml(data);
      el.querySelectorAll(".da-player-row").forEach(row => {
        row.addEventListener("click", () => {
          const sid = row.dataset.sid;
          const detail = el.querySelector(`.da-player-detail[data-sid="${sid}"]`);
          if (detail) detail.classList.toggle("show");
        });
      });
    } else if (view === "issues") {
      el.innerHTML = this._daIssuesHtml(data);
      el.querySelectorAll(".gl-rc-goal").forEach(b => b.onclick = () => {
        $("dashAnalyticsModal").classList.remove("show");
        const t = b.dataset.target !== "" && b.dataset.target != null ? parseFloat(b.dataset.target) : undefined;
        this.makeGoalFromInsight({ player: b.dataset.player || "", metric: b.dataset.metric || "",
          title: b.dataset.title, area: b.dataset.area, ...(t != null && !isNaN(t) ? { target: t } : {}) });
      });
    } else {
      el.innerHTML = this._daDemosHtml(data);
      el.querySelectorAll(".da-open-analytics").forEach(b => b.onclick = () => this._openDaMatchDetail(b.dataset.key));
      el.querySelectorAll(".da-open-replay").forEach(b => b.onclick = () => {
        $("dashAnalyticsModal").classList.remove("show");
        this._openMatchAnalytics(b.dataset.key);
      });
    }
  },
  _daOverviewHtml(data) {
    const ov = data.overview || {}, form = ov.form || {}, avg = form.all || ov.averages || {};
    const l3 = form.last3 || {}, d3 = form.delta3 || {};
    const f = (v, unit = "") => v != null ? `${v}${unit}` : "—";
    const delta = (v) => {
      if (v == null) return "";
      const cls = v > 0 ? "da-delta-up" : v < 0 ? "da-delta-dn" : "da-delta-eq";
      const arrow = v > 0 ? "+" : "";
      return `<span class="${cls}">${arrow}${v}</span>`;
    };

    // Form table
    const formFields = ["hltv","adr","kast","open_wr","traded_pct"];
    const formLabels = ["Rating","ADR","KAST","Open%","Trade%"];
    const formUnits = ["","","% ","% ","% "];
    const formRows = [
      ["Last 3", form.last3 || {}, d3],
      ["Last 5", form.last5 || {}, null],
      ["All", avg, null],
    ].map(([label, row, deltas]) =>
      `<tr><td class="da-fl">${label}</td>${formFields.map((f2, i) => {
        const v = row[f2]; const d = deltas?.[f2];
        return `<td>${f(v, formUnits[i])}${d != null ? delta(d) : ""}</td>`;
      }).join("")}</tr>`).join("");
    const formTable = `
      <div class="da-section-head">Recent form</div>
      <div class="da-scroll"><table class="da-form-table">
        <thead><tr><th></th>${formLabels.map(l => `<th>${l}</th>`).join("")}</tr></thead>
        <tbody>${formRows}</tbody>
      </table></div>`;

    // Map stats table
    const mapStats = (ov.map_stats || []).slice(0, 6);
    const mapTable = mapStats.length ? `
      <div class="da-section-head">Maps</div>
      <div class="da-scroll"><table class="da-form-table">
        <thead><tr><th>Map</th><th>Demos</th><th>Rating</th><th>ADR</th><th>KAST</th><th>Open%</th></tr></thead>
        <tbody>${mapStats.map(m =>
          `<tr><td class="da-fl">${esc(m.map)}</td><td>${m.count}</td>
           <td>${f(m.hltv)}</td><td>${f(m.adr)}</td><td>${f(m.kast, "%")}</td><td>${f(m.open_wr, "%")}</td></tr>`
        ).join("")}</tbody>
      </table></div>` : "";

    // Next focus
    const focusItems = (ov.next_focus || []);
    const focusHtml = focusItems.length ? `
      <div class="da-section-head">Next review focus</div>
      <div class="da-focus-list">${focusItems.map(item => {
        const goalBtn = item.suggest_metric
          ? `<button class="da-focus-goal gl-rc-goal" data-player="" data-metric="${esc(item.suggest_metric)}"
               data-target="${item.suggested_target != null ? item.suggested_target : ""}"
               data-title="${esc(item.label)}" data-area="${esc(item.type)}">+ Goal</button>` : "";
        return `<div class="da-focus-item da-focus-${esc(item.type)}">
          <span class="da-focus-label">${esc(item.label)}</span>
          <span class="da-focus-detail">${esc(item.detail)}</span>
          ${goalBtn}
        </div>`;
      }).join("")}</div>` : "";

    // KPI bar
    const kpis = `<div class="da-kpis">
      <div class="da-kpi"><span class="da-kv">${data.match_count}</span><span class="da-kl">Matches</span></div>
      <div class="da-kpi"><span class="da-kv">${f(avg.hltv)}</span><span class="da-kl">Rating</span></div>
      <div class="da-kpi"><span class="da-kv">${f(avg.adr)}</span><span class="da-kl">ADR</span></div>
      <div class="da-kpi"><span class="da-kv">${f(avg.kast, "%")}</span><span class="da-kl">KAST</span></div>
      <div class="da-kpi"><span class="da-kv">${f(avg.open_wr, "%")}</span><span class="da-kl">Open%</span></div>
      <div class="da-kpi"><span class="da-kv">${f(avg.traded_pct, "%")}</span><span class="da-kl">Trade%</span></div>
    </div>`;

    // Wire goal buttons after render
    setTimeout(() => {
      $("daBody")?.querySelectorAll(".da-focus-goal").forEach(b => b.onclick = () => {
        $("dashAnalyticsModal").classList.remove("show");
        const t = b.dataset.target !== "" && b.dataset.target != null ? parseFloat(b.dataset.target) : undefined;
        this.makeGoalFromInsight({ player: b.dataset.player || "", metric: b.dataset.metric || "",
          title: b.dataset.title, area: b.dataset.area, ...(t != null && !isNaN(t) ? { target: t } : {}) });
      });
    }, 0);

    return `<div class="da-section">${kpis}${formTable}${mapTable}${focusHtml}</div>`;
  },
  _daPlayersHtml(data) {
    const players = data.players || [];
    if (!players.length) return `<div class="da-empty">No squad or team player data yet. Upload demos or configure your team.</div>`;
    const rosterNote = data.roster_mode === "personal_squad"
      ? `<div class="da-roster-note">Showing your squad only — ${data.roster_members?.length || 0} players</div>`
      : data.roster_mode === "team"
      ? `<div class="da-roster-note">Showing team members only — ${data.roster_members?.length || 0} players</div>`
      : "";
    const matches = data.matches || [];
    const rows = players.map(p => {
      const perMatch = matches.filter(m => m.players?.some(mp => String(mp.steamid) === String(p.steamid)));
      const matchRows = perMatch.slice(0, 8).map(m => {
        const mp = m.players?.find(mp2 => String(mp2.steamid) === String(p.steamid));
        if (!mp) return "";
        return `<tr class="da-detail-match-row">
          <td>${esc((m.created_at || "").slice(0,10))}</td>
          <td>${esc(m.map || "?")}</td>
          <td>${(mp.hltv || 0).toFixed(2)}</td>
          <td>${Math.round(mp.adr || 0)}</td>
          <td>${Math.round(mp.kast || 0)}%</td>
          <td>${(mp.kd || 0).toFixed(2)}</td>
        </tr>`;
      }).join("");
      const detail = perMatch.length ? `
        <tr class="da-player-detail" data-sid="${esc(String(p.steamid))}">
          <td colspan="9"><table class="da-detail-table">
            <thead><tr><th>Date</th><th>Map</th><th>Rating</th><th>ADR</th><th>KAST</th><th>K/D</th></tr></thead>
            <tbody>${matchRows}</tbody>
          </table></td>
        </tr>` : "";
      return `<tr class="da-player-row" data-sid="${esc(String(p.steamid))}">
        <td class="da-pname">${esc(p.name || p.steamid)} <span class="da-expand-hint">▸</span></td>
        <td>${p.n_matches}</td>
        <td>${(p.hltv || 0).toFixed(2)}</td><td>${Math.round(p.adr || 0)}</td>
        <td>${Math.round(p.kast || 0)}%</td><td>${(p.kd || 0).toFixed(2)}</td>
        <td>${Math.round(p.open_wr || 0)}%</td><td>${Math.round(p.traded_pct || 0)}%</td>
        <td>${(p.udr || 0).toFixed(1)}</td>
      </tr>${detail}`;
    }).join("");
    return `${rosterNote}<div class="da-scroll"><table class="da-table">
      <thead><tr><th>Player</th><th>Demos</th><th>Rating</th><th>ADR</th><th>KAST</th>
        <th>K/D</th><th>Open%</th><th>Trade%</th><th>UDR</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
  },
  _daIssuesHtml(data) {
    const rec = data.recurring || [];
    if (!rec.length) return `<div class="da-empty">No repeating issues found across your demos yet.</div>`;
    return `<div class="gl-recur-body">` + rec.map(it => {
      const goal = it.suggest_metric
        ? `<button class="gl-rc-goal" data-player="" data-metric="${esc(it.suggest_metric)}"
            data-target="${it.suggested_target != null ? it.suggested_target : ""}"
            data-title="${esc(it.label)}" data-area="${esc(it.type)}">+ Goal</button>` : "";
      return `<div class="gl-rc-row">
        <div class="gl-rc-info">
          <span class="gl-rc-label">${esc(it.label)}</span>
          <span class="gl-rc-meta">in <b>${it.matches_present}</b> of ${it.matches_total} matches &middot; ${it.total} total
            <span class="gl-rc-trend t-${it.trend}">${esc(it.trend)}</span></span>
        </div>
        ${this._recurSpark(it)}
        ${goal}
      </div>`;
    }).join("") + `</div>`;
  },
  _daDemosHtml(data) {
    const matches = data.matches || [];
    if (!matches.length) return `<div class="da-empty">No demos yet — upload a .dem to get started.</div>`;
    const rosterSids = new Set((data.roster_members || []).map(m => String(m.steamid)));
    return `<div class="da-demos">` + matches.map(m => {
      const date = (m.created_at || "").slice(0, 10);
      const allPlayers = m.players || [];
      const squadPlayers = rosterSids.size
        ? allPlayers.filter(p => rosterSids.has(String(p.steamid)))
        : allPlayers.slice(0, 5);
      const playerNames = (squadPlayers.length ? squadPlayers : allPlayers.slice(0, 3))
        .map(p => esc(p.name || "?")).join(", ");
      const key = esc(m.key || m.id);
      return `<div class="da-demo-row">
        <div class="da-demo-info">
          <b>${esc(m.map || "?")}</b>
          <span class="round">${m.rounds || 0}r${m.score ? " · " + esc(m.score) : ""} · ${esc(date)}</span>
          <span class="da-demo-players">${esc(playerNames)}</span>
        </div>
        <div class="da-demo-btns">
          <button class="btn ghost sm da-open-analytics" data-key="${key}">Analytics</button>
          <button class="btn ghost sm da-open-replay" data-key="${key}">Replay</button>
        </div>
      </div>`;
    }).join("") + `</div>`;
  },
  async _openDaMatchDetail(key) {
    const el = $("daBody");
    el.innerHTML = `<div class="da-loading">Loading match analytics…</div>`;
    const md = await fetch(`/api/dashboard/analytics/match/${encodeURIComponent(key)}`)
      .then(r => r.json()).catch(() => null);
    if (!md || md.error) {
      el.innerHTML = `<div class="da-empty">${md?.error || "Could not load match analytics."}</div>`;
      return;
    }
    this._daMatchData = md;
    this._daView = "match-detail";
    this._renderDaView();
  },
  _daMatchDetailHtml(md) {
    if (!md) return `<div class="da-empty">No match data.</div>`;
    const analytics = md.analytics || {};
    const players = analytics.players || [];
    const insights = analytics.insights || {};
    // Flatten issues/coaching across players
    const issues = [];
    const positives = [];
    for (const [sid, items] of Object.entries(insights)) {
      for (const it of (items || [])) {
        if (it.polarity === "issue" && it.label) issues.push(it.label);
        else if (it.polarity === "good" && it.label) positives.push(it.label);
      }
    }
    // Dedupe
    const topIssues = [...new Set(issues)].slice(0, 5);
    const topGood = [...new Set(positives)].slice(0, 3);

    const playerRows = players.slice(0, 10).map(p =>
      `<tr><td class="da-pname">${esc(p.name || p.steamid || "?")}</td>
       <td>${(p.hltv || 0).toFixed(2)}</td><td>${Math.round(p.adr || 0)}</td>
       <td>${Math.round(p.kast || 0)}%</td><td>${(p.kd || 0).toFixed(2)}</td>
       <td>${Math.round(p.open_wr || 0)}%</td></tr>`).join("");

    const issueHtml = topIssues.length
      ? `<div class="da-section-head">Key fixes</div><ul class="da-detail-issues">${topIssues.map(l => `<li>${esc(l)}</li>`).join("")}</ul>` : "";
    const goodHtml = topGood.length
      ? `<div class="da-section-head">What went well</div><ul class="da-detail-good">${topGood.map(l => `<li>${esc(l)}</li>`).join("")}</ul>` : "";
    const playerHtml = playerRows
      ? `<div class="da-section-head">Players</div>
         <div class="da-scroll"><table class="da-table">
           <thead><tr><th>Player</th><th>Rating</th><th>ADR</th><th>KAST</th><th>K/D</th><th>Open%</th></tr></thead>
           <tbody>${playerRows}</tbody>
         </table></div>` : "";

    return `<div class="da-match-header">
      <button class="btn ghost sm da-back-btn">← Back</button>
      <span class="da-match-title"><b>${esc(md.map || "?")}</b>
        ${md.score ? `<span class="round">${esc(md.score)}</span>` : ""}
        ${md.rounds ? `<span class="da-mutl">${md.rounds}r</span>` : ""}
        <span class="da-mutl">${(md.created_at || "").slice(0,10)}</span>
      </span>
      <button class="btn ghost sm da-replay-btn">Open replay</button>
    </div>
    <div class="da-match-body">${issueHtml}${goodHtml}${playerHtml}</div>`;
  },

  // --- multi-demo trends + team config (cross-match "am I getting better?") -
  async openTrends(focusSid) {
    this.pausePlayback();
    $("trendsModal").classList.add("show");
    const [players, matches, team] = await Promise.all([
      fetch("api/players").then(r => r.json()).catch(() => []),
      fetch("api/matches").then(r => r.json()).catch(() => []),
      fetch("api/team").then(r => r.json()).catch(() => ({ name: "", players: [] })),
    ]);
    await this._loadSquad(true);                         // refresh the squad each open
    const sel = $("trPlayer");
    sel.innerHTML = players.length
      ? this._squadOptions(players, focusSid || "")
      : `<option value="">no parsed matches yet</option>`;
    const maps = ["all", ...Array.from(new Set(matches.map(m => m.map).filter(Boolean))).sort()];
    $("trMap").innerHTML = maps.map(m => `<option value="${m}">${m === "all" ? "all maps" : esc(m)}</option>`).join("");
    // player change re-renders trend + tendencies; map change only filters the trend series
    sel.onchange = () => { this.renderTrend(sel.value, $("trMap").value); this.renderTendencies(sel.value); };
    $("trMap").onchange = () => this.renderTrend(sel.value, $("trMap").value);
    const want = (focusSid && players.some(p => String(p.steamid) === String(focusSid))) ? String(focusSid) : "";
    const sq = this._squad || {};
    const prefer = (sq.you && sq.you.steamid) || ((sq.squad || [])[0] || {}).steamid || (players[0] || {}).steamid || "";
    const def = want || prefer;                          // default the trend to YOU (or your top teammate)
    if (def) { sel.value = def; this.renderTrend(def, "all"); this.renderTendencies(def); }
    else { $("trBody").innerHTML = `<div class="empty">Upload a few demos, then come back to see your trend.</div>`; $("trTend").innerHTML = ""; }
    $("trMatchCount").textContent = matches.length ? `${matches.length}` : "";
    $("trMatches").innerHTML = matches.length
      ? matches.map(m => `<div class="tr-mrow"><b>${esc(m.map || "?")}</b> <span class="round">${esc((m.created_at || "").slice(0, 10))} | ${m.rounds}r${m.score ? " | " + esc(m.score) : ""}</span></div>`).join("")
      : `<div class="empty">No parsed matches cached yet.</div>`;
    this._renderSquadPanel();
    this.renderTeam(team);
    this.renderPlan();
  },
  renderTrend(steamid, mapFilter) {
    if (!steamid) return;
    fetch("api/trends/" + encodeURIComponent(steamid)).then(r => r.json()).then(t => {
      let series = (t && t.series) || [];
      if (mapFilter && mapFilter !== "all") series = series.filter(s => s.map === mapFilter);
      if (!series.length) { $("trBody").innerHTML = `<div class="empty">No matches for this player${mapFilter && mapFilter !== "all" ? " on " + esc(mapFilter) : ""}.</div>`; return; }
      const arr = v => (v == null || v === "") ? "" : (v > 0 ? `<span class="tr-up">+${v}</span>` : v < 0 ? `<span class="tr-dn">${v}</span>` : `<span class="round">0</span>`);
      // recompute averages + first-half-vs-last-half deltas from the (possibly map-filtered) series
      const mean = k => series.reduce((s, x) => s + (x[k] || 0), 0) / series.length;
      const rnd = (v, d) => Math.round(v * 10 ** d) / 10 ** d;
      const delta = k => { if (series.length < 2) return ""; const h = Math.ceil(series.length / 2); const f = series.slice(0, h), l = series.slice(-h); const dm = a => a.reduce((s, x) => s + (x[k] || 0), 0) / a.length; return rnd(dm(l) - dm(f), k === "hltv" ? 2 : 1); };
      const a = { hltv: rnd(mean("hltv"), 2), adr: rnd(mean("adr"), 1), kast: rnd(mean("kast"), 1), open_wr: rnd(mean("open_wr"), 1), traded_pct: rnd(mean("traded_pct"), 1) };
      const sum = `<div class="tr-sum">${series.length} match${series.length === 1 ? "" : "es"} | rating <b>${a.hltv}</b> ${arr(delta("hltv"))} | ADR ${a.adr} ${arr(delta("adr"))} `
        + `| KAST ${a.kast}% ${arr(delta("kast"))} | open ${a.open_wr}% ${arr(delta("open_wr"))} | traded ${a.traded_pct}% ${arr(delta("traded_pct"))}</div>`;
      const head = `<div class="tr-row tr-head"><span class="tr-m">map</span><span>rating</span><span>ADR</span><span>KAST</span><span>open</span><span>traded</span></div>`;
      const rows = series.slice().reverse().map(s => {
        const k = s.key || s.id || "";
        return `<div class="tr-row${k ? " tr-click" : ""}"${k ? ` data-key="${esc(k)}" title="Open this match's analytics"` : ""}>`
          + `<span class="tr-m">${esc(s.map || "?")} <i class="round">${esc((s.created_at || "").slice(5, 10))}</i></span>`
          + `<span>${s.hltv}</span><span>${s.adr}</span><span>${s.kast}%</span><span>${s.open_wr}%</span><span>${s.traded_pct}%</span></div>`;
      }).join("");
      const body = $("trBody");
      body.innerHTML = sum + `<div class="tr-hint">Click a match to open its per-match analytics &rsaquo;</div>` + head + rows;
      body.querySelectorAll(".tr-row.tr-click").forEach(r => { r.onclick = () => this._openMatchAnalytics(r.dataset.key); });
    }).catch(() => { $("trBody").innerHTML = `<div class="empty">trend unavailable</div>`; });
  },
  // #44 cross-match tendencies / repeated patterns for the selected player (anti-strat scouting)
  renderTendencies(steamid) {
    const el = $("trTend");
    if (!el) return;
    if (!steamid) { el.innerHTML = ""; return; }
    fetch("api/tendencies/" + encodeURIComponent(steamid)).then(r => r.json()).then(t => {
      const tend = (t && t.tendencies) || [];
      if (!tend.length) {
        el.innerHTML = `<div class="empty">${t && t.n_matches < 2
          ? "Need 2+ cached matches with this player to spot repeated patterns."
          : "No strong repeated patterns across these matches."}</div>`;
        return;
      }
      const cls = s => (s >= 2 ? "tend-bad" : s === 0 ? "tend-good" : "tend-neu");
      el.innerHTML = tend.map(x =>
        `<div class="tend-row ${cls(x.severity)}"><span class="tend-kind">${esc((x.kind || "").replace(/_/g, " "))}</span>`
        + `<span class="tend-txt">${esc(x.text)}</span></div>`).join("");
    }).catch(() => { el.innerHTML = `<div class="empty">tendencies unavailable</div>`; });
  },
  renderTeam(team) {
    this._team = team || { name: "", players: [], notes: "" };
    const roster = (this._team.players || []).map(p => `${p.steamid} ${p.name || ""} ${p.role || ""}`.trim()).join("\n");
    $("trTeam").innerHTML = `
      <input id="tmName" class="lf-in" placeholder="Team name" value="${esc(this._team.name || "")}">
      <textarea id="tmRoster" class="lf-in" rows="6" placeholder="One player per line:  steamid  name  role&#10;roles: igl entry support lurker awper anchor rotator">${esc(roster)}</textarea>
      <textarea id="tmNotes" class="lf-in" rows="2" placeholder="Team notes">${esc(this._team.notes || "")}</textarea>
      <div class="lf-row"><button id="tmSave" class="up-btn primary">Save team</button><span id="tmMsg" class="lf-msg"></span></div>`;
    $("tmSave").onclick = () => this.saveTeam();
  },
  async saveTeam() {
    const players = $("tmRoster").value.split("\n").map(l => l.trim()).filter(Boolean).map(l => {
      const p = l.split(/\s+/); return { steamid: p[0], name: p[1] || "", role: p[2] || "" };
    });
    const cfg = { name: $("tmName").value.trim(), players, notes: $("tmNotes").value.trim() };
    const saved = await fetch("api/team", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(cfg) })
      .then(r => r.json()).catch(() => null);
    $("tmMsg").textContent = saved ? "Saved \u2713" : "save failed";
    if (saved) this.renderTeam(saved);
  },
  _hash(s) { let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0; return (h >>> 0).toString(36); },
  async renderPlan() {
    const el = $("trPlan");
    const A = this.demo && this.demo.analytics;
    if (!A || !A.players) { el.innerHTML = `<div class="empty">Load a match to generate a practice plan.</div>`; return; }
    // aggregate the team's top focus areas this match -> up to 5 distinct drills
    const byArea = {};
    for (const p of A.players) for (const f of (p.focus || [])) {
      if (!byArea[f.area] || f.severity > byArea[f.area].sev)
        byArea[f.area] = { area: f.area, text: f.detail || f.area, drill: f.fix || "", sev: f.severity };
    }
    let items = Object.values(byArea).sort((a, b) => b.sev - a.sev).slice(0, 5);
    if (!items.length) { el.innerHTML = `<div class="empty">No major team weaknesses this match -- clean game.</div>`; return; }
    items = items.map(it => ({ ...it, id: "pl_" + this._hash(this.demo.map + "|" + it.area + "|" + it.text) }));
    const done = await fetch("api/practice").then(r => r.json()).catch(() => ({}));
    el.innerHTML = items.map(it => `<label class="pl-row ${done[it.id] ? "done" : ""}">
      <input type="checkbox" data-pid="${it.id}" ${done[it.id] ? "checked" : ""}>
      <span class="pl-t"><b>${esc(it.area)}</b> -- ${esc(it.text)} <i class="round">${esc(it.drill)}</i></span></label>`).join("");
    el.querySelectorAll("[data-pid]").forEach(cb => cb.onchange = () => this.togglePlanItem(cb.dataset.pid, cb.checked, cb));
  },
  async togglePlanItem(id, done, cb) {
    await fetch("api/practice", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id, done }) }).catch(() => {});
    cb.closest(".pl-row").classList.toggle("done", done);
  },

  // ---- Practice goals (persistent, match-aware) -----------------------------
  // A goal = a metric + target + scope the app GRADES across every uploaded match,
  // reporting current value + a verdict (fixed / improving / still happening / need more).
  async openGoals(prefill) {
    if (!this.entitled("goals")) { this._upsell("goals"); return; }   // chokepoint: all goal entry gates here
    this.pausePlayback();
    $("goalsModal").classList.add("show");
    $("goalsList").innerHTML = `<div class="gl-empty">Loading...</div>`;
    if (!this._goalMetrics) {
      const j = await fetch("api/goals/metrics").then(r => r.json()).catch(() => ({}));
      this._goalMetrics = j.metrics || [];
      this._scopeOpts = { sides: j.sides || [], buys: j.buys || [], roles: j.roles || [] };
    }
    if (!this._allPlayers) {     // cross-match players -> goals are graded across matches,
      this._allPlayers = await fetch("api/players").then(r => r.json()).catch(() => []);
      this._playerNames = {};    // so the picker isn't limited to the loaded demo
      for (const p of this._allPlayers) this._playerNames[String(p.steamid)] = p.name;
    }
    await this._loadSquad();     // squad drives the player picker (your teammates first, not everyone)
    await this.refreshGoals();   // also populates this._goalMaps (cheap -- sidecar-backed)
    await this.initRecurring();
    if (prefill) this.openGoalForm(prefill); else $("glForm").style.display = "none";
  },

  async refreshGoals() {
    const j = await fetch("api/goals").then(r => r.json()).catch(() => null);
    if (!j || !j.goals) { $("goalsList").innerHTML = `<div class="gl-empty">Could not load goals (is the server running?)</div>`; return; }
    // distinct maps for the scope dropdown (cached matches + the loaded demo)
    const ms = new Set(j.maps || []);
    if (this.demo && this.demo.map) ms.add(this.demo.map);
    this._goalMaps = Array.from(ms).sort();
    this.renderGoals(j.goals);
  },

  // After creating / status-changing / deleting a goal: refresh the modal list AND, if the
  // dashboard is behind it, its Practice Goals section -- otherwise it stayed stale until a reload.
  async _afterGoalChange() {
    await this.refreshGoals();
    if (document.body.classList.contains("on-dashboard")) this.loadDashboard();
  },

  renderGoals(goals) {
    $("goalsCount").textContent = goals.length ? `(${goals.length})` : "";
    const el = $("goalsList");
    if (!goals.length) {
      el.innerHTML = `<div class="gl-empty">No goals yet. Click <b>+ New goal</b> to set a target the app will grade
        across your uploaded matches &mdash; e.g. <b>ADR &ge; 85</b> on Mirage, or <b>untraded opening deaths &le; 5</b>.</div>`;
      return;
    }
    el.innerHTML = goals.map(g => this._goalRow(g)).join("");
    el.querySelectorAll(".gl-row").forEach(row => {
      const id = row.dataset.id;
      const sel = row.querySelector(".gl-stsel");
      if (sel) sel.onchange = () => this.setGoalStatus(id, sel.value);
      const notes = row.querySelector(".gl-notes");
      if (notes) notes.onchange = () => this.setGoalNotes(id, notes.value);   // fires on blur
      const del = row.querySelector(".gl-del");
      if (del) del.onclick = () => this.deleteGoal(id, row);
    });
  },

  _goalRow(g) {
    const p = g.progress || {};
    const m = (this._goalMetrics || []).find(x => x.key === g.metric) || {};
    const unit = p.unit || m.unit || "";
    const cmp = (p.better || m.better) === "low" ? "&le;" : "&ge;";
    const sc = g.scope || {};
    const scope = [];
    if (sc.map) scope.push(esc(sc.map));
    if (sc.group === "team") scope.push(esc(sc.label || "team"));
    else if (sc.group === "squad") scope.push("my squad");
    else scope.push(sc.player ? this._playerName(sc.player) : "all players (match)");
    if (sc.side) scope.push(sc.side === "ct" ? "CT side" : "T side");
    if (sc.buy) scope.push(esc(sc.buy) + " buys");
    if (sc.role) scope.push(esc(sc.role));
    const V = { fixed: "Fixed", improving: "Improving", still_happening: "Still happening",
                insufficient: "Need more matches", no_data: "No data yet", unknown_metric: "?" };
    const verdict = p.verdict || "no_data";
    const cur = (p.current != null) ? `${p.current}${unit}` : "&mdash;";
    const samp = p.samples ? `${p.samples} match${p.samples === 1 ? "" : "es"}` : "";
    const dim = (g.status === "fixed" || g.status === "ignored") ? " dim" : "";
    return `<div class="gl-row${dim}" data-id="${esc(g.id)}">
      <div class="gl-r-top">
        <span class="gl-status st-${g.status}">${esc(g.status)}</span>
        <span class="gl-r-title">${esc(g.title)}</span>
        ${g.team_id ? `<span class="gl-share" title="Shared with everyone on this team">shared</span>` : ""}
      </div>
      <div class="gl-meta">${esc(m.label || g.metric)} ${cmp} <b>${g.target}${unit}</b> &middot; ${scope.join(" &middot; ")}</div>
      <div class="gl-r-grade">
        <span>${(p.members && p.members.length) ? "team avg " : "now "}<span class="gl-cur">${cur}</span></span>
        <span class="gl-vbadge v-${verdict}">${V[verdict] || verdict}</span>
        ${samp ? `<span>${samp}</span>` : ""}
        ${this._goalSpark(p)}
      </div>
      ${this._goalMembers(p, unit)}
      ${this._goalWindows(p, unit)}
      ${g.drill ? `<div class="gl-drill"><b>Drill:</b> ${esc(g.drill)}</div>` : ""}
      <textarea class="gl-notes" rows="1" maxlength="1000"
        placeholder="Notes -- progress, what's working, when you drilled it...">${esc(g.notes || "")}</textarea>
      <div class="gl-r-actions">
        <label>status</label>
        <select class="gl-stsel">${["open", "drilling", "fixed", "ignored"]
          .map(s => `<option value="${s}" ${s === g.status ? "selected" : ""}>${s}</option>`).join("")}</select>
        <button class="gl-del">Delete</button>
      </div>
    </div>`;
  },

  // Per-member breakdown for a squad/team goal: each member's own current value (green if they meet
  // the target, red if not), a trend word, and a mini sparkline -- so you see who's above/below, not
  // just the team average. Members who haven't played a tracked match are omitted by the backend.
  _goalMembers(p, unit) {
    const ms = p.members || [];
    if (!ms.length) return "";
    const TR = { fixed: "on target", improving: "improving", still_happening: "needs work",
                 insufficient: "few games", no_data: "" };
    const rows = ms.map(mb => {
      const cur = (mb.current != null) ? `${mb.current}${unit}` : "&mdash;";
      return `<div class="glm-row">
        <span class="glm-name">${esc(mb.name)}</span>
        <span class="glm-val ${mb.meets ? "ok" : "bad"}">${cur}</span>
        <span class="glm-trend v-${mb.verdict}">${TR[mb.verdict] || ""}</span>
        ${this._goalSpark({ series: mb.series })}
      </div>`;
    }).join("");
    return `<div class="gl-members">${rows}</div>`;
  },

  // rolling 3 / 5 / 10-match averages (each shown only once that many matches exist),
  // green when the window meets the target. The "rolling trend check" readout.
  _goalWindows(p, unit) {
    const w = p.windows || {};
    const parts = ["3", "5", "10"].filter(k => w[k]).map(k =>
      `<span class="gl-win ${w[k].meets ? "ok" : ""}">${k}-match ${w[k].avg}${unit}</span>`);
    return parts.length ? `<div class="gl-wins">rolling avg ${parts.join("")}</div>` : "";
  },

  // tiny bar trend, oldest -> newest, accent on the latest match
  _goalSpark(p) {
    const s = (p.series || []).slice().reverse();
    if (s.length < 2) return "";
    const vals = s.map(x => x.value);
    const mn = Math.min(...vals), rng = (Math.max(...vals) - mn) || 1;
    const bars = s.map((x, i) => {
      const h = 3 + Math.round((x.value - mn) / rng * 19);
      return `<i class="${i === s.length - 1 ? "last" : ""}" style="height:${h}px" title="${esc(x.map || "")} ${x.value}"></i>`;
    }).join("");
    return `<span class="gl-spark" title="oldest → newest">${bars}</span>`;
  },

  _playerName(steamid) {
    const nm = (this._playerNames || {})[String(steamid)];
    if (nm) return esc(nm);
    const p = ((this.demo && this.demo.players) || []).find(x => String(x.steamid) === String(steamid));
    return p ? esc(p.name) : esc(String(steamid));
  },

  // ---- Recurring mistakes (cross-match repeated-mistake detection) -----------
  async initRecurring() {
    if (!this._recurInit) {
      const ps = this._allPlayers || [];
      $("glRecurPlayer").innerHTML = `<option value="">Whole team</option>` + ps.map(p =>
        `<option value="${esc(String(p.steamid))}">${esc(p.name)} (${p.n_matches})</option>`).join("");
      if (ps[0]) $("glRecurPlayer").value = String(ps[0].steamid);   // default most-active player
      this._recurInit = true;
    }
    await this.renderRecurring($("glRecurPlayer").value);
  },

  async renderRecurring(player) {
    const body = $("glRecurBody");
    body.innerHTML = `<div class="gl-rc-empty">Scanning your matches...</div>`;
    const q = player ? "?player=" + encodeURIComponent(player) : "";
    const data = await fetch("api/recurring" + q).then(r => r.json()).catch(() => null);
    if (!data || !data.recurring) { body.innerHTML = `<div class="gl-rc-empty">Could not load.</div>`; return; }
    if (!data.recurring.length) {
      body.innerHTML = `<div class="gl-rc-empty">No mistakes repeating across ${data.matches} match${data.matches === 1 ? "" : "es"}`
        + `${player ? " for this player" : ""} &mdash; or not enough matches yet.</div>`;
      return;
    }
    body.innerHTML = data.recurring.map(it => this._recurRow(it, data.player)).join("");
    body.querySelectorAll(".gl-rc-goal").forEach(b => b.onclick = () => {
      const t = b.dataset.target !== "" && b.dataset.target != null ? parseFloat(b.dataset.target) : undefined;
      this.makeGoalFromInsight({ player: b.dataset.player || "", metric: b.dataset.metric || "",
        title: b.dataset.title, area: b.dataset.area, ...(t != null && !isNaN(t) ? { target: t } : {}) });
    });
  },

  _recurRow(it, player) {
    return `<div class="gl-rc-row">
      <div class="gl-rc-info">
        <span class="gl-rc-label">${esc(it.label)}</span>
        <span class="gl-rc-meta">in <b>${it.matches_present}</b> of ${it.matches_total} matches &middot; ${it.total} total
          <span class="gl-rc-trend t-${it.trend}">${esc(it.trend)}</span></span>
      </div>
      ${this._recurSpark(it)}
      <button class="gl-rc-goal" data-player="${esc(player || "")}" data-metric="${esc(it.suggest_metric || "")}"
        data-target="${it.suggested_target != null ? it.suggested_target : ""}"
        data-title="${esc(it.label)}" data-area="${esc(it.type)}">+ Goal</button>
    </div>`;
  },

  _recurSpark(it) {
    const s = (it.series || []).slice().reverse();   // oldest -> newest
    if (s.length < 2) return `<span class="gl-spark"></span>`;
    const mx = Math.max(1, ...s);
    const bars = s.map((v, i) =>
      `<i class="${i === s.length - 1 ? "last" : ""}" style="height:${3 + Math.round(v / mx * 19)}px" title="${v}"></i>`).join("");
    return `<span class="gl-spark" title="oldest → newest occurrences">${bars}</span>`;
  },

  toggleGoalForm() {
    const f = $("glForm");
    if (f.style.display === "none" || !f.style.display) this.openGoalForm();
    else f.style.display = "none";
  },

  openGoalForm(prefill) {
    prefill = prefill || {};
    const f = $("glForm");
    f.style.display = "flex";
    const metrics = this._goalMetrics || [];
    const players = (this._allPlayers && this._allPlayers.length)   // cross-match roster
      ? this._allPlayers : ((this.demo && this.demo.players) || []);
    const maps = this._goalMaps || [];
    const opts = this._scopeOpts || { sides: [], buys: [], roles: [] };
    const sel = (v, want) => String(v) === String(want) ? " selected" : "";
    const metricOpts = metrics.map(m =>
      `<option value="${m.key}"${sel(m.key, prefill.metric)}>`
      + `${esc(m.label)} (${m.better === "low" ? "lower" : "higher"} better)</option>`).join("");
    const grp = prefill.scope && prefill.scope.group;     // default the picker to YOU (a personal goal)
    const selPlayer = grp === "squad" ? "__squad__"
      : (grp === "team" && prefill.scope.team_id) ? ("__team_" + prefill.scope.team_id + "__")
      : (prefill.player || (this._squad && this._squad.you && this._squad.you.steamid) || "");
    const playerOpts = this._squadOptions(players, selPlayer, { includeGroups: true, teams: this.myTeams });
    const mapOpts = `<option value="">All maps</option>` + maps.map(mp =>
      `<option value="${esc(mp)}"${sel(mp, prefill.map)}>${esc(mp)}</option>`).join("");
    const SIDE_LBL = { ct: "CT side", t: "T side" };
    const sideOpts = `<option value="">Both sides</option>` + opts.sides.map(s =>
      `<option value="${esc(s)}"${sel(s, prefill.side)}>${SIDE_LBL[s] || esc(s)}</option>`).join("");
    const buyOpts = `<option value="">Any buy</option>` + opts.buys.map(b =>
      `<option value="${esc(b)}"${sel(b, prefill.buy)}>${esc(b)} buys</option>`).join("");
    const roleOpts = `<option value="">Any role</option>` + opts.roles.map(r =>
      `<option value="${esc(r)}"${sel(r, prefill.role)}>${esc(r)}</option>`).join("");
    f.innerHTML = `
      <div class="gl-frow"><label>Metric</label><select id="glMetric">${metricOpts}</select></div>
      <div class="gl-frow"><label>Target</label><input id="glTarget" type="number" step="any"
        value="${prefill.target != null ? prefill.target : ""}" placeholder="e.g. 85"><span id="glCmp" class="gl-hint"></span></div>
      <div class="gl-frow"><label>Goal for</label><select id="glPlayer">${playerOpts}</select><span id="glScopeHint" class="gl-hint"></span></div>
      <div class="gl-frow"><label>Map</label><select id="glMap">${mapOpts}</select></div>
      <div class="gl-frow" id="glSideRow"><label>Side</label><select id="glSide">${sideOpts}</select></div>
      <div class="gl-frow" id="glBuyRow"><label>Buy</label><select id="glBuy">${buyOpts}</select><span id="glBuyHint" class="gl-hint"></span></div>
      <div class="gl-frow" id="glRoleRow"><label>Role</label><select id="glRole">${roleOpts}</select>
        <span class="gl-hint">only matches you played this role</span></div>
      <div class="gl-frow"><label>Title</label><input id="glTitle" maxlength="100" value="${esc(prefill.title || "")}"
        placeholder="optional &mdash; defaults to the metric name"></div>
      <div class="gl-frow"><label>Drill</label><textarea id="glDrill" maxlength="400"
        placeholder="optional &mdash; how you'll practice this (e.g. aim_botz 200 kills, prefire mirage)">${esc(prefill.drill || "")}</textarea></div>
      <div class="gl-form-actions"><button id="glCancel" class="btn">Cancel</button>
        <button id="glSave" class="btn primary">Create goal</button></div>`;
    const sync = () => this._syncGoalForm();
    $("glMetric").onchange = sync;
    $("glPlayer").onchange = sync;
    sync();
    $("glCancel").onclick = () => { f.style.display = "none"; };
    $("glSave").onclick = () => this.submitGoal(prefill);
  },

  // Show/hide the side/buy/role rows for the selected metric: side/buy only when the metric
  // supports that breakdown; role only when a specific player is chosen (it's a match filter).
  _syncGoalForm() {
    const m = (this._goalMetrics || []).find(x => x.key === $("glMetric").value) || {};
    const caps = m.scopes || [];
    const pv = $("glPlayer").value;
    const isTeam = pv.indexOf("__team_") === 0;
    const isGroup = pv === "__squad__" || isTeam;
    const hasPlayer = !!pv && !isGroup;    // squad/team = a group, not a single player -> no role filter
    const hint = $("glScopeHint");
    if (hint) hint.textContent = isTeam ? "Shared with everyone on this team (each member's progress shown)"
      : pv === "__squad__" ? "Personal — averages your stack, with each member's progress"
      : "Personal — only you can see this goal";
    $("glCmp").innerHTML = m.better === "low" ? "&le; target (lower is better)" : "&ge; target (higher is better)";
    $("glSideRow").style.display = caps.includes("side") ? "flex" : "none";
    $("glBuyRow").style.display = caps.includes("buy") ? "flex" : "none";
    $("glRoleRow").style.display = hasPlayer ? "flex" : "none";
    // a metric that REQUIRES a buy (e.g. round win %) -- default one in + flag it
    if (m.requires === "buy") {
      $("glBuyHint").textContent = "required for this metric";
      if (!$("glBuy").value) $("glBuy").value = (this._scopeOpts.buys || ["full"])[0];
    } else if ($("glBuyHint")) {
      $("glBuyHint").textContent = "";
    }
  },

  async submitGoal(prefill) {
    prefill = prefill || {};
    const metric = $("glMetric").value;
    const target = parseFloat($("glTarget").value);
    if (!metric || isNaN(target)) { $("glTarget").focus(); $("glTarget").style.borderColor = "#ff5a5a"; return; }
    const m = (this._goalMetrics || []).find(x => x.key === metric) || {};
    const caps = m.scopes || [];
    const scope = {};
    let shareTeamId = null;                             // a Team goal is SHARED with that team (team_id)
    const pl = $("glPlayer").value;
    if (pl === "__squad__") {                           // your auto-detected stack -> snapshot its steamids (personal)
      const sq = this._squad || {};
      const ids = [];
      if (sq.you && sq.you.steamid) ids.push(String(sq.you.steamid));
      (sq.squad || []).forEach(p => { if (p.steamid) ids.push(String(p.steamid)); });
      if (ids.length) { scope.group = "squad"; scope.members = ids; }
    } else if (pl.indexOf("__team_") === 0) {          // a team you created/joined -> its members + share with them
      const tid = parseInt(pl.slice(7, -2), 10);
      const team = (this.myTeams || []).find(t => t.id === tid);
      const ids = team ? (team.members || []).map(m => m.steamid).filter(Boolean).map(String) : [];
      if (ids.length) { scope.group = "team"; scope.members = ids; scope.label = "Team: " + team.name; shareTeamId = tid; }
    } else if (pl) { scope.player = pl; }
    const mp = $("glMap").value; if (mp) scope.map = mp;
    if (caps.includes("side") && $("glSide").value) scope.side = $("glSide").value;
    if (caps.includes("buy") && $("glBuy").value) scope.buy = $("glBuy").value;
    if (pl && pl.indexOf("__") !== 0 && $("glRole").value) scope.role = $("glRole").value;   // role = filter on a player
    const body = {
      metric, target, scope, team_id: shareTeamId,     // team_id set => shared team goal; null => personal
      title: $("glTitle").value.trim(),
      drill: $("glDrill").value.trim(),
      source_match_key: prefill.source_match_key || (this.demo && this.demo.raw ? this.demo.raw.source_sha1 : null),
    };
    await fetch("api/goals", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch(() => {});
    $("glForm").style.display = "none";
    await this._afterGoalChange();
  },

  async setGoalStatus(id, status) {
    await fetch("api/goals/" + encodeURIComponent(id),
      { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status }) }).catch(() => {});
    await this._afterGoalChange();
  },

  // notes save silently on blur -- they don't affect grading, so no re-render (keeps focus/scroll)
  async setGoalNotes(id, notes) {
    await fetch("api/goals/" + encodeURIComponent(id),
      { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ notes }) }).catch(() => {});
  },

  async deleteGoal(id, row) {
    await fetch("api/goals/" + encodeURIComponent(id), { method: "DELETE" }).catch(() => {});
    if (row) row.remove();
    await this._afterGoalChange();
  },

  // Hook for "make a goal from this insight/focus area" (called from analytics.js).
  // prefill: {player, map, area, title, drill}; metric is guessed from the text if absent.
  makeGoalFromInsight(prefill) {
    prefill = prefill || {};
    if (!prefill.metric) prefill.metric = this._guessMetric(`${prefill.area || ""} ${prefill.title || ""}`);
    $("analyticsPanel").classList.remove("show");
    $("toggleAnalytics").classList.remove("on");
    this.openGoals(prefill);
  },

  // Best-effort map from a coaching focus area / detail text to a trackable metric.
  _guessMetric(text) {
    const t = (text || "").toLowerCase();
    if (/\btrad/.test(t)) return "traded_pct";
    if (/opening|entry|first ?(blood|contact)|dry|peek/.test(t)) return "open_wr";
    if (/spacing|clump|stack|bunch/.test(t)) return "clumping";
    if (/predictable|same spot|repeat/.test(t)) return "predictable";
    if (/position|overextend|mid-?round|off ?angle|exposure/.test(t)) return "pos";
    if (/util|grenade|nade|flash|smoke|molly|molotov/.test(t)) return "udr";
    if (/kast|consist|surviv|impact each/.test(t)) return "kast";
    if (/damage|adr|output|trade.*damage/.test(t)) return "adr";
    return "hltv";
  },

  // 3D camera presets: Fly (free) | Follow (3rd-person) | POV (first-person) | Overhead | Utility | Death
  cycleCamPreset() {
    const order = ["fly", "follow", "fp", "overhead", "utility", "death"];
    this.setCamPreset(order[(order.indexOf(this._camPreset || "fly") + 1) % order.length]);
  },
  // V: quick first-person toggle. Grabs a player if none is spectated; fp <-> third-person follow.
  togglePOV() {
    if (!this.demo) return;
    if (this.radar.followIdx < 0) this.cycleSpectate(1);     // need a player to ride
    this.setCamPreset(this._camPreset === "fp" ? "follow" : "fp");
  },
  // lightweight transient toast (reused by a few actions)
  _toast(msg) {
    let t = document.getElementById("appToast");
    if (!t) { t = document.createElement("div"); t.id = "appToast"; t.className = "app-toast"; document.body.appendChild(t); }
    t.textContent = msg; t.classList.add("show");
    clearTimeout(this._toastTimer); this._toastTimer = setTimeout(() => t.classList.remove("show"), 2400);
  },
  // collapse / restore the right control panel (more room for the 2D/3D view); persisted.
  setRightPanel(open) {
    const collapsed = !open;
    $("stage").classList.toggle("r-collapsed", collapsed);
    $("sideRestore").classList.toggle("show", collapsed);
    try { localStorage.setItem("cs2dp_rpanel", collapsed ? "0" : "1"); } catch (e) { /* storage off */ }
    // the viewport just changed width -> re-fit the radar / 3D / draw layers (like a window resize)
    if (this.radar) { this.radar.resize(); this.radar.fit(); }
    if (this.view3d) this.view3d.resize();
    if (this.resizeDraw) this.resizeDraw();
    if (this.view3d && this.view3d.active && this.miniRadar && this.miniRadar.map) { this.miniRadar.resize(); this.miniRadar.fit(); }
  },
  setCamPreset(name) {
    // any 3D camera preset means entering 3D -> Pro. (Already in 3D = a Pro user cycling presets, allow.)
    if (!this.view3d.active && !this.entitled("threeD")) { this._upsell("threeD"); return; }
    this._camPreset = name;
    const desc = {
      fly: "Free fly -- WASD move, E/Q up/down",
      follow: "Third-person on the spectated player",
      fp: "First-person -- see exactly what the spectated player sees (with a crosshair)",
      overhead: "Tactical top-down tracking the action",
      utility: "Follows the in-flight nade / newest smoke",
      death: "Cuts to the most recent death",
    };
    const label = ({ fp: "POV" })[name] || (name[0].toUpperCase() + name.slice(1));
    $("camPreset").textContent = "Cam: " + label;
    $("camPreset").title = "3D camera (C to cycle) -- " + (desc[name] || name);
    $("camPreset").classList.toggle("on", name !== "fly");
    if (!this.view3d.active && this.demo) { const [wx, wy] = this.radar.cameraWorldCenter(); this.enter3D(wx, wy); }
    this.view3d.setCamPreset(["overhead", "utility", "death", "fp"].includes(name) ? name : "free");
    if ((name === "follow" || name === "fp") && this.radar.followIdx < 0) this.cycleSpectate(1);  // need a player
    if (name === "fly") this.setSpectate(-1);
    document.querySelector(".viewport").classList.toggle("fp", name === "fp" && this.view3d.active);  // crosshair
    // crouch (eye-drop) needs per-frame duck data; demos parsed before that field lack it -> tell the user once
    if (name === "fp" && this.demo && this.demo.hasDuck === false && this._duckWarnDemo !== this.demo) {
      this._duckWarnDemo = this.demo;
      this._toast && this._toast("This demo has no crouch data (parsed before crouch capture) — re-upload its .dem to see the first-person crouch.");
    }
  },
  // First-person HUD: in POV the player's own model/head-label are hidden, so this is the only
  // readout of who you're riding, their HP/armor, and what they're holding. Text only (no icons).
  _updateFpHud(state) {
    const hud = $("fpHud");
    const on = this._camPreset === "fp" && this.view3d.active && this.view3d.followIdx >= 0;
    const p = on && state ? state.players[this.view3d.followIdx] : null;
    if (!p || !p.alive) { hud.classList.remove("show"); this._setPovFlash(0); return; }
    hud.classList.add("show");
    hud.classList.toggle("ct", p.team === 3);
    hud.classList.toggle("t", p.team !== 3);
    const ar = Math.max(0, Math.round(p.armor || 0));
    $("fphHp").textContent = `${Math.max(0, Math.round(p.hp))} HP`
      + (ar > 0 ? ` · ${ar}${p.helmet ? "+H" : ""} AR` : "");
    $("fphWeap").textContent = fpWeaponName(p.weapon);
    // ammo: clip / mag-size for firearms (mag derived from the demo); "RELOADING" while reloading.
    const ammo = $("fphAmmo");
    const cap = this.demo.magCap ? this.demo.magCap(p.weapon) : 0;
    if (cap >= 5 && p.clip != null) {
      if (p.reload) { ammo.textContent = "RELOADING"; ammo.classList.add("reloading"); }
      else { ammo.innerHTML = `<span class="cur">${p.clip}</span> / ${cap}`; ammo.classList.remove("reloading"); }
    } else { ammo.textContent = ""; ammo.classList.remove("reloading"); }
    // scope badge: snipers/AUG/SG show a zoom indicator when scoped in (data straight from the demo) (#13)
    const scopeEl = $("fphScope");
    if (scopeEl) {
      const z = p.zoom || (p.scoped ? 1 : 0);
      if (z > 0) { scopeEl.hidden = false; scopeEl.textContent = z >= 2 ? "SCOPED ×2" : "SCOPED"; }
      else { scopeEl.hidden = true; }
    }
    // POV "blindness": a white veil scaled by how flashed the spectated player is (peaks ~40%).
    const peak = (this.demo.flashPeakAt && this.demo.flashPeakAt(this.view3d.followIdx, this.t)) || 5;
    this._setPovFlash(p.flash > 0 ? Math.min(1, p.flash / peak) * 0.4 : 0);
  },
  // White full-screen veil over the 3D view for the flash effect (lazy-created, opacity 0 = hidden).
  _setPovFlash(op) {
    let el = this._povFlashEl;
    if (!el) {
      const vp = document.querySelector(".viewport");
      if (!vp) return;
      el = document.createElement("div"); el.className = "pov-flash"; vp.appendChild(el);
      this._povFlashEl = el;
    }
    el.style.opacity = op > 0.01 ? op.toFixed(2) : "0";
  },

  // --- round strip / timeline markers --------------------------------------
  buildRoundStrip() {
    const el = $("roundStrip"); const rs = this.demo.rounds;
    el.innerHTML = rs.map(r => {
      const cls = r.winner === "CT" ? "ct" : r.winner === "T" ? "t" : "";
      return `<button class="rbx ${cls}" data-r="${r.number}" title="Round ${r.number} -- ${r.winner || "?"} (${r.reason || ""})">${r.number}</button>`;
    }).join("");
    el.querySelectorAll(".rbx").forEach(b => b.onclick = () => {
      const r = rs.find(x => x.number === +b.dataset.r);
      if (r) this.t = this._roundSeekT(r);
    });
    this._curRound = -1;
  },
  // seek target for a round, clamped to the playable range. The last round's freeze/start
  // can sit just past the final frame; without the clamp, t > duration wraps to 0 (round 1).
  _roundSeekT(r) {
    const tgt = r.freeze_end_t ?? r.start_t ?? 0;
    const lead = Math.max(r.start_t ?? 0, tgt - 2.0);    // start ~2s before round-go (clamp to round start)
    return Math.max(0, Math.min((this.demo.duration || 0) - 0.05, lead));
  },
  updateRoundStrip(rnum) {
    if (this._curRound === rnum) return;
    this._curRound = rnum;
    const el = $("roundStrip");
    el.querySelectorAll(".rbx").forEach(b => {
      const on = +b.dataset.r === rnum;
      b.classList.toggle("cur", on);
      if (on) b.scrollIntoView({ block: "nearest", inline: "center" });
    });
  },
  buildTimelineMarkers() {
    // Round start is the only key timeline marker (kept clean -- the old kills/utility/bomb/insight/
    // "key moments" layers were clutter). Distinct .rb line; drawn in BOTH modes (every round start in
    // match mode; just this round's start in round mode, where the window is one round).
    if (!this.demo) { $("tlMarkers").innerHTML = ""; return; }
    const b = this.tlBounds();
    const lo = b.min, hi = b.max, span = Math.max(0.001, hi - lo);
    const pct = t => ((t - lo) / span * 100).toFixed(2);
    let h = "";
    for (const r of this.demo.rounds)
      if (r.start_t >= lo - 0.001 && r.start_t <= hi + 0.001)
        h += `<i class="rb" title="Round ${r.number} start" style="left:${pct(r.start_t)}%"></i>`;
    $("tlMarkers").innerHTML = h;
  },

  // timeline scope bounds (seconds). Round mode = the current round's [start_t, end_t];
  // match mode = the whole demo. Falls back to whole match if there's no round at this t.
  tlBounds() {
    if (this.tlMode === "round" && this.demo) {
      const r = this.demo.roundAt(this.t);
      if (r) return { min: r.start_t ?? 0, max: r.end_t ?? this.demo.duration, round: r.number };
    }
    return { min: 0, max: (this.demo ? this.demo.duration : 1), round: -1 };
  },
  // push the current bounds onto the #timeline slider + rebuild markers for the window.
  refreshTimeline() {
    if (!this.demo) return;
    const b = this.tlBounds();
    const tl = $("timeline");
    tl.min = b.min; tl.max = b.max;
    this._tlRound = b.round;
    this.buildTimelineMarkers();
  },
  toggleTlMode() {
    if (!this.demo) return;
    this.tlMode = this.tlMode === "round" ? "match" : "round";
    const btn = $("tlMode");
    btn.textContent = this.tlMode === "round" ? "Round" : "Match";
    btn.classList.toggle("on", this.tlMode === "round");
    this.refreshTimeline();
    if (!this.scrubbing) $("timeline").value = this.t;
  },

  // --- settings (persisted to localStorage so prefs survive reloads) --------
  saveSettings() {
    try {
      const r = this.radar, v = this.view3d;
      localStorage.setItem("cs2dp_settings", JSON.stringify({
        showNames: r.showNames, showTrajectories: r.showTrajectories, showTraces: r.showTraces,
        showUtil: r.showUtil, dotSize: r.dotSize,
        showAim: v.showAim, showCone: v.showCone, xray: v.xray, trails: v.trails, showDeaths: v.showDeaths,
        impactsOn: v.impactsOn, labelScale: v.labelScale,
        miniOn: this._miniOn, miniZoom: this._miniZoom, miniSize: this._miniSize, tlLayers: this._tlLayers,
      }));
    } catch (e) { /* localStorage unavailable (private mode) -- ignore */ }
  },
  loadSettings() {
    let s;
    try { s = JSON.parse(localStorage.getItem("cs2dp_settings") || "null"); } catch (e) { return; }
    if (!s || typeof s !== "object") return;
    const r = this.radar, v = this.view3d;
    const b = (o, k, val) => { if (typeof val === "boolean") o[k] = val; };   // only apply real booleans
    b(r, "showNames", s.showNames); b(r, "showTrajectories", s.showTrajectories);
    b(r, "showTraces", s.showTraces); b(r, "showUtil", s.showUtil);
    if (typeof s.dotSize === "number") { r.dotSize = s.dotSize; if (this.miniRadar) this.miniRadar.dotSize = s.dotSize; }
    if (typeof s.labelScale === "number") v.labelScale = s.labelScale;
    b(v, "impactsOn", s.impactsOn);
    b(v, "showAim", s.showAim); b(v, "showCone", s.showCone); b(v, "xray", s.xray);
    b(v, "trails", s.trails); b(v, "showDeaths", s.showDeaths);
    if (typeof s.miniOn === "boolean") this._miniOn = s.miniOn;
    if (typeof s.miniZoom === "number") this._miniZoom = s.miniZoom;
    if (typeof s.miniSize === "number") this._miniSize = s.miniSize;
    if (s.tlLayers && typeof s.tlLayers === "object")
      this._tlLayers = Object.assign({ kills: true, utility: false, bomb: true, insights: false, swing: false }, s.tlLayers);
    $("toggleNames").classList.toggle("on", r.showNames);   // keep the header Names button in sync
  },
  buildSettings() {
    const r = this.radar;
    this._settings = [   // short labels; full meaning in the title tooltip
      ["Names", "showNames"], ["Trajectories", "showTrajectories"],
      ["Traces", "showTraces"], ["Utility", "showUtil"],
    ];
    // bullet-impact players grouped by side into a collapsible header (auto-open if any are selected)
    const teamGroup = (tm, label) => {
      const ps = this.demo.players.map((p, i) => [p, i]).filter(([p]) => p.team === tm);
      if (!ps.length) return "";
      const has = this.view3d.impactSet;
      const anyOn = ps.some(([, i]) => has && has.has(i));
      return `<div class="sp-team${anyOn ? " open" : ""}" data-team="${tm}"><span class="sp-tri">&#9656;</span> ${label}<span class="sp-tn">${ps.length}</span></div>`
        + `<div class="sp-team-body">`
        + ps.map(([p, i]) => `<label class="sp-row sp-imp"><input type="checkbox" data-imp="${i}" ${has && has.has(i) ? "checked" : ""}/> ${esc(p.name)}</label>`).join("")
        + `</div>`;
    };
    $("settingsPop").innerHTML = `<div class="sp-h">Replay settings</div>` +
      `<div class="sp-grid">` +
      this._settings.map((t, i) =>
        `<label class="sp-row"><input type="checkbox" data-set="${i}" ${r[t[1]] ? "checked" : ""}/> ${t[0]}</label>`).join("") +
      `<label class="sp-row" title="POV line from each head"><input type="checkbox" id="aimChk" ${this.view3d.showAim ? "checked" : ""}/> Aim laser</label>` +
      `<label class="sp-row" title="Floor view cone"><input type="checkbox" id="coneChk" ${this.view3d.showCone ? "checked" : ""}/> POV cone</label>` +
      `<label class="sp-row" title="See players through walls"><input type="checkbox" id="xrayChk" ${this.view3d.xray ? "checked" : ""}/> X-ray</label>` +
      `<label class="sp-row" title="Recent movement trails"><input type="checkbox" id="trailChk" ${this.view3d.trails ? "checked" : ""}/> Trails</label>` +
      `<label class="sp-row" title="Mark every death this round"><input type="checkbox" id="deathChk" ${this.view3d.showDeaths ? "checked" : ""}/> Mark deaths</label>` +
      `<label class="sp-row" title="2D minimap overlay in 3D"><input type="checkbox" id="miniChk" ${this._miniOn !== false ? "checked" : ""}/> Minimap</label>` +
      `</div>` +
      `<div class="sp-row sp-size">Dot size <input id="dotSizeSl" type="range" min="0.6" max="2" step="0.1" value="${r.dotSize}"/></div>` +
      `<div class="sp-row sp-size">Nameplate size <input id="nameScaleSl" type="range" min="0.5" max="2.2" step="0.1" value="${this.view3d.labelScale || 1}" title="size of the floating name/HP labels above players in 3D"/></div>` +
      `<div class="sp-row sp-size">Map zoom <input id="miniZoomSl" type="range" min="1" max="4" step="0.25" value="${this._miniZoom || 1}" title="minimap: 1 = whole map; higher zooms on the player"/></div>` +
      `<div class="sp-row sp-size">Map size <input id="miniSizeSl" type="range" min="1" max="2.4" step="0.1" value="${this._miniSize || 1}" title="how big the minimap is on screen"/></div>` +
      `<div class="sp-h sp-sub">Bullet impacts (3D) &middot; <span class="sp-exp">experimental</span></div>` +
      `<label class="sp-row" title="Raycast where each shot hits the map. Off by default — can be heavy on big demos."><input type="checkbox" id="impMaster" ${this.view3d.impactsOn ? "checked" : ""}/> Enable bullet impacts</label>` +
      `<label class="sp-row"><input type="checkbox" id="impAll"/> All players</label>` +
      (this.demo ? teamGroup(3, "Counter-Terrorists") + teamGroup(2, "Terrorists") : "");
    $("settingsPop").querySelectorAll("[data-set]").forEach((cb, i) =>
      cb.onchange = () => {
        const key = this._settings[i][1]; r[key] = cb.checked;
        if (key === "showNames") $("toggleNames").classList.toggle("on", cb.checked);
      });
    $("aimChk").onchange = (e) => { this.view3d.showAim = e.target.checked; };
    $("coneChk").onchange = (e) => { this.view3d.showCone = e.target.checked; };
    $("xrayChk").onchange = (e) => { this.view3d.xray = e.target.checked; };
    $("trailChk").onchange = (e) => { this.view3d.trails = e.target.checked; };
    $("deathChk").onchange = (e) => { this.view3d.showDeaths = e.target.checked; };
    $("dotSizeSl").oninput = (e) => {
      const v = parseFloat(e.target.value);
      r.dotSize = v;
      if (this.miniRadar) this.miniRadar.dotSize = v;   // minimap dots track it too (both are 2D dots)
    };
    $("nameScaleSl").oninput = (e) => { this.view3d.labelScale = parseFloat(e.target.value); };   // 3D nameplates only
    $("miniChk").onchange = (e) => { this._miniOn = e.target.checked; };
    $("miniZoomSl").oninput = (e) => { this._miniZoom = parseFloat(e.target.value); };
    $("miniSizeSl").oninput = (e) => { this._miniSize = parseFloat(e.target.value); this._applyMiniSize(); };
    // bullet-impacts: per-player + "all" -> drives view3d.impactSet (the 3D view reads it).
    // The experimental master flag gates whether ANY of it renders (#18) -- selections persist.
    const impMaster = $("impMaster");
    if (impMaster) impMaster.onchange = (e) => { this.view3d.impactsOn = e.target.checked; };
    const impSet = this.view3d.impactSet || (this.view3d.impactSet = new Set());
    const impAll = $("impAll");
    const syncImpAll = () => { if (impAll && this.demo) impAll.checked = impSet.size > 0 && impSet.size === this.demo.players.length; };
    $("settingsPop").querySelectorAll("[data-imp]").forEach(cb =>
      cb.onchange = () => { const i = +cb.dataset.imp; if (cb.checked) impSet.add(i); else impSet.delete(i); syncImpAll(); });
    if (impAll) {
      syncImpAll();
      impAll.onchange = () => {
        impSet.clear();
        if (impAll.checked && this.demo) this.demo.players.forEach((_, i) => impSet.add(i));
        $("settingsPop").querySelectorAll("[data-imp]").forEach(cb => { cb.checked = impAll.checked; });
        if (impAll.checked) $("settingsPop").querySelectorAll(".sp-team").forEach(h => h.classList.add("open"));
      };
    }
    // collapsible team headers for the bullet-impact player lists (click to expand/collapse)
    $("settingsPop").querySelectorAll(".sp-team").forEach(h => h.onclick = () => h.classList.toggle("open"));
  },

  // --- draw / telestrator (#68: WORLD-space strokes so they track pan/zoom + can be saved) ----
  initDraw() {
    const c = $("drawCanvas"); this.drawMode = false; this.drawing = false; this.strokes = [];
    this._drawCtx = c.getContext("2d");
    // capture each point in WORLD coords (so a saved drawing reloads onto the right map spots)
    const wpos = (e) => { const r = c.getBoundingClientRect(); return this.radar.worldFromScreen(e.clientX - r.left, e.clientY - r.top); };
    c.addEventListener("pointerdown", (e) => {
      if (!this.drawMode) return;
      this.drawing = true; this._stroke = [wpos(e)]; c.setPointerCapture(e.pointerId);
    });
    c.addEventListener("pointermove", (e) => {
      if (this.drawMode && this.drawing) { this._stroke.push(wpos(e)); this._redraw(); }
    });
    c.addEventListener("pointerup", () => {
      if (this._stroke && this._stroke.length > 1) this.strokes.push(this._stroke);
      this.drawing = false; this._stroke = null;
    });
    c.addEventListener("contextmenu", (e) => { if (this.drawMode) { e.preventDefault(); this.strokes = []; this._redraw(); } });
  },
  resizeDraw() {
    const c = $("drawCanvas"); const r = c.getBoundingClientRect();
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    c.width = Math.max(1, r.width * dpr); c.height = Math.max(1, r.height * dpr);
    this._drawDpr = dpr; this._redraw();
  },
  _redraw() {
    const c = $("drawCanvas"), ctx = this._drawCtx, dpr = this._drawDpr || 1;
    if (!ctx) return;
    ctx.clearRect(0, 0, c.width, c.height);
    ctx.strokeStyle = "#ff5b3b"; ctx.lineWidth = 3 * dpr; ctx.lineJoin = "round"; ctx.lineCap = "round";
    const all = this.strokes.concat(this.drawing && this._stroke ? [this._stroke] : []);
    for (const st of all) {
      ctx.beginPath();
      st.forEach((wp, i) => {            // world -> screen each frame (tracks the radar camera)
        const [sx, sy] = this.radar.worldToScreen(wp[0], wp[1]);
        i ? ctx.lineTo(sx * dpr, sy * dpr) : ctx.moveTo(sx * dpr, sy * dpr);
      });
      ctx.stroke();
    }
  },
  toggleDraw() {
    this.drawMode = !this.drawMode;
    $("drawBtn").classList.toggle("on", this.drawMode);
    $("drawCanvas").classList.toggle("active", this.drawMode);
    $("drawTools").classList.toggle("show", this.drawMode);
    if (this.drawMode) { this.resizeDraw(); this.renderDrawList(); }
  },
  // #68 saved strategy-board drawings -- per-demo, localStorage (world-coord strokes)
  _drawKey() { const sha = this.demo && this.demo.raw && this.demo.raw.source_sha1; return "cs2dp_draw_" + (sha || this.demo.map || "x"); },
  _savedDrawings() { try { return JSON.parse(localStorage.getItem(this._drawKey()) || "[]"); } catch (e) { return []; } },
  saveDrawing() {
    if (!this.strokes.length) { this._toast("Draw something first (then Save)"); return; }
    const name = (window.prompt("Name this drawing:", `Round ${this.demo.roundAt(this.t)?.number ?? ""}`.trim()) || "").trim();
    if (!name) return;
    const list = this._savedDrawings().filter(d => d.name !== name);
    list.push({ name, strokes: this.strokes, t: Math.round(this.t) });
    try { localStorage.setItem(this._drawKey(), JSON.stringify(list)); } catch (e) { this._toast("Save failed (storage full?)"); return; }
    this._toast(`Saved drawing "${name}"`); this.renderDrawList();
  },
  loadDrawing(name) {
    const d = this._savedDrawings().find(x => x.name === name);
    if (!d) return;
    this.strokes = d.strokes || [];
    if (!this.drawMode) this.toggleDraw(); else this._redraw();
    if (d.t != null) this.t = d.t;
  },
  deleteDrawing(name) {
    const list = this._savedDrawings().filter(d => d.name !== name);
    try { localStorage.setItem(this._drawKey(), JSON.stringify(list)); } catch (e) { /* ignore */ }
    this.renderDrawList();
  },
  renderDrawList() {
    const el = $("drawList"); if (!el) return;
    const list = this._savedDrawings();
    el.innerHTML = list.length
      ? list.map(d => `<span class="dw-item"><button class="dw-load" data-dw="${esc(d.name)}">${esc(d.name)}</button>`
        + `<button class="dw-del" data-dwdel="${esc(d.name)}" title="Delete">&times;</button></span>`).join("")
      : `<span class="pmut" style="font-size:11px">no saved drawings</span>`;
    el.querySelectorAll("[data-dw]").forEach(b => b.onclick = () => this.loadDrawing(b.dataset.dw));
    el.querySelectorAll("[data-dwdel]").forEach(b => b.onclick = () => this.deleteDrawing(b.dataset.dwdel));
  },

  // --- utility search (whole-demo) -----------------------------------------
  buildUtilSearch() {
    this.utilFilter = { type: "all", round: "all", player: "all", side: "all" };
    const types = [["all", "All"], ["smoke", "Smoke"], ["molotov", "Molotov"], ["flash", "Flash"], ["he", "HE"]];
    $("utilTypes").innerHTML = types.map(([k, l]) =>
      `<button class="chip ${k === "all" ? "on" : ""}" data-ut="${k}">${l}</button>`).join("");
    $("utilTypes").querySelectorAll(".chip").forEach(c => c.onclick = () => {
      this.utilFilter.type = c.dataset.ut;
      $("utilTypes").querySelectorAll(".chip").forEach(x => x.classList.toggle("on", x === c));
      this.applyUtilSearch();
    });
    // side filter chips (All / CT / T) -- show only the throws made on one side
    const sides = [["all", "Both sides"], ["ct", "CT"], ["t", "T"]];
    $("utilSide").innerHTML = sides.map(([k, l]) =>
      `<button class="chip side-chip ${k === "all" ? "on" : ""}" data-us="${k}">${l}</button>`).join("");
    $("utilSide").querySelectorAll(".chip").forEach(c => c.onclick = () => {
      this.utilFilter.side = c.dataset.us;
      $("utilSide").querySelectorAll(".chip").forEach(x => x.classList.toggle("on", x === c));
      this.applyUtilSearch();
    });
    $("utilRound").innerHTML = `<option value="all">All rounds</option>` +
      this.demo.rounds.map(r => `<option value="${r.number}">Round ${r.number}</option>`).join("");
    $("utilPlayer").innerHTML = `<option value="all">All players</option>` +
      this.demo.players.map((p, i) => `<option value="${i}">${esc(p.name)}</option>`).join("");
    $("utilRound").onchange = (e) => { this.utilFilter.round = e.target.value; this.applyUtilSearch(); };
    $("utilPlayer").onchange = (e) => { this.utilFilter.player = e.target.value; this.applyUtilSearch(); };
    // 3-state heatmap: off -> on (heatmap + throws) -> only (heatmap, throws hidden)
    const HEAT = ["off", "on", "only"], HEATLBL = { off: "off", on: "on", only: "heatmap only" };
    if (!HEAT.includes(this.utilHeat)) this.utilHeat = "off";
    const drawHeatBtn = () => {
      $("utilHeat").textContent = HEATLBL[this.utilHeat];
      $("utilHeat").classList.toggle("on", this.utilHeat !== "off");
    };
    drawHeatBtn();
    $("utilHeat").onclick = () => {
      this.utilHeat = HEAT[(HEAT.indexOf(this.utilHeat) + 1) % HEAT.length];
      drawHeatBtn();
      this.applyUtilSearch();
    };
  },
  applyUtilSearch() {
    const f = this.utilFilter;
    const res = this.demo.utilityThrows().filter(g => {
      if (!(f.type === "all" || g.type === f.type)) return false;
      if (!(f.round === "all" || g.round === +f.round)) return false;
      if (!(f.player === "all" || g.thrower === +f.player)) return false;
      // side the thrower was on AT throw time (flips at halftime) -> team-coloured trajectory +
      // side tag in the list, and the CT/T filter chip below
      g._side = g.thrower >= 0 ? this.demo.teamAtTime(g.thrower, g.t0) : null;
      if (f.side !== "all" && (g._side === 3 ? "ct" : g._side === 2 ? "t" : "") !== f.side) return false;
      return true;
    });
    const heatOn = this.utilHeat === "on" || this.utilHeat === "only";
    this.radar.searchOverlay = this.utilHeat === "only" ? [] : res;   // "only" hides projectiles/arcs
    this.radar.heatmap = heatOn;
    this.radar.heatmapPts = heatOn
      ? res.map(g => g.det_pos ? [g.det_pos[0], g.det_pos[1]]            // reliable detonation landing
          : (g.pts && g.pts.length) ? [g.pts[g.pts.length - 1][1], g.pts[g.pts.length - 1][2]] : null).filter(Boolean)
      : null;
    $("utilCount").textContent = `${res.length} throw${res.length === 1 ? "" : "s"} on map`;
    const col = { smoke: "#cfd3da", molotov: "#ff6a2b", flash: "#ffe27a", he: "#ff8a3d", decoy: "#99a3b0" };
    $("utilList").innerHTML = res.slice(0, 400).map((g, i) => {
      const pn = g.thrower >= 0 ? esc(this.demo.players[g.thrower].name) : "--";
      const m = this._matchLineup(g);
      const tag = (this.library && this.library.length)
        ? (m ? (m.quality === "exact"
              ? `<span class="lib-match" title="matches a saved lineup (landing + throw spot)">~ ${esc(m.n.name)}</span>`
              : `<span class="lib-loose" title="lands on a saved lineup but thrown from a different spot">~ ${esc(m.n.name)} (diff spot)</span>`)
           : `<span class="lib-off" title="no saved lineup near this landing">off-book</span>`) : "";
      const side = g._side === 3 ? "ct" : g._side === 2 ? "t" : "";
      const sideTag = side ? `<b class="side-${side}">${side.toUpperCase()}</b> ` : "";
      return `<div class="up-item" data-ui="${i}">
        <span class="ut" style="background:${col[g.type] || "#9aa"}"></span>
        <span class="un"><span class="${side ? "side-" + side : ""}">${pn}</span> <span style="color:var(--mut)">${g.type}</span> ${tag}</span>
        <span class="umeta">${sideTag}R${g.round} | ${fmt(g.t0)}</span>
        <button class="up-note" data-uin="${i}" title="Add a note for this utility">&#9998;</button></div>`;
    }).join("");
    $("utilList").querySelectorAll(".up-item").forEach(el => el.onclick = (e) => {
      if (e.target.classList.contains("up-note")) return;
      const g = res[+el.dataset.ui]; if (g) this.confirmJumpUtil(g);
    });
    $("utilList").querySelectorAll(".up-note").forEach(b => b.onclick = (e) => {
      e.stopPropagation();
      const g = res[+b.dataset.uin]; if (!g) return;
      const pn = g.thrower >= 0 ? this.demo.players[g.thrower].name : "unknown";
      this.addEntityNote({ entity: "util", ref: `${g.type} R${g.round}`, round: g.round, t: g.t0,
        player: g.thrower >= 0 ? g.thrower : -1, tag: "util",
        promptText: `Note this ${g.type} (${pn}, R${g.round}):` });
    });
  },
  // Clicking a demo throw asks first (in-app confirm, not window.confirm) so a stray click
  // never yanks the timeline. Shows who/what/round/time (+ lineup match if known).
  async confirmJumpUtil(g, pov) {
    const pn = g.thrower >= 0 ? this.demo.players[g.thrower].name : "unknown";
    const m = this._matchLineup ? this._matchLineup(g) : null;
    const tag = m ? (m.quality === "exact"
      ? ` &middot; matches lineup "${esc(m.n.name)}"`
      : ` &middot; lands like "${esc(m.n.name)}" (diff spot)`) : "";
    const known = g.thrower >= 0;
    const body = `<div class="cf-line"><b>${esc(pn)}</b> threw a <b>${esc(g.type)}</b></div>`
      + `<div class="cf-line cf-mut">Round ${g.round} &middot; throw at ${fmt(g.t0)}${tag}</div>`
      + (pov && known ? `<div class="cf-line cf-mut">Jumps here and watches from ${esc(pn)}'s first-person POV.</div>` : "");
    const title = (pov && known) ? "Go to this throw in their POV?" : "Move to this throw time?";
    const yes = (pov && known) ? "Yes, watch their POV" : "Yes, jump";
    if (await this.askConfirm(title, body, yes)) this.jumpUtil(g, pov);
  },
  jumpUtil(g, pov) {
    // Start ~1.75s before the throw so the wind-up is visible, but never seek back past the round
    // start (otherwise a throw early in a round jumps into the previous round's freezetime) (#15).
    const r = this.demo.roundAt ? this.demo.roundAt(g.t0) : null;
    const lead = g.t0 - 1.75;
    this.t = Math.max(0, r && r.start_t != null ? Math.max(r.start_t, lead) : lead);
    if (g.thrower >= 0) {
      this.setSpectate(g.thrower);
      if (pov) this.setCamPreset("fp");   // enter 3D + ride the thrower's first-person camera
    }
    this.playing = true; $("playPause").textContent = "\u23f8";   // jump then play so you watch the throw
  },
  // hover state for util-mode trajectory highlight (drives radar redraw + cursor)
  _setNadeHover(g) {
    g = g || null;
    if (this.radar._hoverNade === g) return;
    this.radar._hoverNade = g;
    $("canvas").style.cursor = g ? "pointer" : "";
  },
  // Generic in-app confirm modal -> Promise<bool>. Yes/No buttons, backdrop-click or Esc = No.
  askConfirm(title, bodyHtml, yesLabel = "Yes") {
    return new Promise(resolve => {
      const modal = $("confirmModal");
      $("cfTitle").textContent = title;
      $("cfBody").innerHTML = bodyHtml;
      $("cfYes").textContent = yesLabel;
      modal.classList.add("show");
      const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); done(false); } };
      const done = (v) => {
        modal.classList.remove("show");
        $("cfYes").onclick = $("cfNo").onclick = modal.onclick = null;
        window.removeEventListener("keydown", onKey, true);
        resolve(v);
      };
      $("cfYes").onclick = () => done(true);
      $("cfNo").onclick = () => done(false);
      modal.onclick = (e) => { if (e.target.id === "confirmModal") done(false); };
      window.addEventListener("keydown", onKey, true);
    });
  },
  toggleUtil(force) {
    if (!this.demo) return;
    const show = force === undefined ? !$("utilPanel").classList.contains("show") : force;
    if (show) { this.toggleReview(false); closeAnalytics(this); }   // only one panel open at a time
    $("utilPanel").classList.toggle("show", show);
    $("toggleUtil").classList.toggle("on", show);
    if (show) this.setUtilMode(this.utilMode || "throws");
    else { this.radar.searchOverlay = []; this.radar.heatmap = false; }
  },

  // --- review: bookmarks + auto-seeded queues (team learning loop) ----------
  _reviewDemoId() { return (this.demo && this.demo.raw && this.demo.raw.source_sha1) || null; },
  toggleReview(force) {
    if (!this.demo) return;
    const show = force === undefined ? !$("reviewPanel").classList.contains("show") : force;
    if (show) { this.toggleUtil(false); closeAnalytics(this); }   // only one panel open at a time
    $("reviewPanel").classList.toggle("show", show);
    $("toggleReview").classList.toggle("on", show);
    if (show) this.loadReview();
  },
  async loadReview() {
    const id = this._reviewDemoId();
    if (!id) { $("rvBookmarks").innerHTML = '<div class="rv-empty">No demo id for this replay.</div>'; return; }
    $("rvBookmarks").innerHTML = '<div class="rv-empty">Loading...</div>';
    $("rvQueues").innerHTML = "";
    const [bm, qz] = await Promise.all([
      fetch(`api/reviews/${id}/bookmarks`).then(r => r.json()).catch(() => ({ bookmarks: [] })),
      fetch(`api/reviews/${id}/queues`).then(r => r.json()).catch(() => ({ queues: [] })),
    ]);
    this.renderBookmarks((bm && bm.bookmarks) || []);
    this.renderQueues((qz && qz.queues) || []);
  },
  renderBookmarks(list) {
    this._bookmarks = list || [];
    $("rvBmCount").textContent = list.length ? `(${list.length})` : "";
    // tag autocomplete + filter chips from the tags in use
    const tags = [...new Set(list.map(b => b.tag).filter(Boolean))].sort();
    $("rvTagList").innerHTML = tags.map(t => `<option value="${esc(t)}">`).join("");
    const tf = $("rvTagFilter");
    if (tags.length) {
      const cur = this._bmTagFilter || "";
      tf.innerHTML = `<button class="rv-tagchip ${cur === "" ? "on" : ""}" data-tag="">all</button>`
        + tags.map(t => `<button class="rv-tagchip ${cur === t ? "on" : ""}" data-tag="${esc(t)}">${esc(t)}</button>`).join("");
      tf.querySelectorAll(".rv-tagchip").forEach(c => c.onclick = () => {
        this._bmTagFilter = c.dataset.tag; this.renderBookmarks(this._bookmarks);
      });
    } else { tf.innerHTML = ""; }
    const filtered = this._bmTagFilter ? list.filter(b => b.tag === this._bmTagFilter) : list;
    const el = $("rvBookmarks");
    if (!filtered.length) {
      el.innerHTML = `<div class="rv-empty">${list.length ? "No notes with that tag."
        : "No notes yet -- pause on a moment (spectate a player to tag them) and click <b>+ Note</b>, or use the note buttons on rounds &amp; utility."}</div>`;
      return;
    }
    el.innerHTML = filtered.map(b => {
      const who = b.player >= 0 && this.demo.players[b.player] ? esc(this.demo.players[b.player].name) : "";
      const tag = b.tag ? `<span class="rv-tag">${esc(b.tag)}</span>` : "";
      const ent = (b.entity && b.entity !== "tick" && b.entity !== "player")
        ? `<span class="rv-ent">${esc(b.entity)}${b.ref ? ": " + esc(b.ref) : ""}</span>` : "";
      return `<div class="rv-item" data-t="${b.t}" data-pl="${b.player}">
        <div class="rv-i-main"><span class="rv-i-t">R${b.round ?? "?"} &middot; ${fmt(b.t)}</span>`
        + (who ? ` <span class="rv-i-who">${who}</span>` : "") + ent + tag
        + `<button class="rv-del" data-del="${esc(b.id)}" title="Delete note">&times;</button></div>`
        + (b.note ? `<div class="rv-i-note">${esc(b.note)}</div>` : "")
        + (b.label ? `<div class="rv-i-note">${esc(b.label)}</div>` : "") + `</div>`;
    }).join("");
    el.querySelectorAll(".rv-item").forEach(it => it.onclick = (e) => {
      if (e.target.classList.contains("rv-del")) return;
      this.jumpReview(+it.dataset.t, +it.dataset.pl);
    });
    el.querySelectorAll(".rv-del").forEach(d => d.onclick = (e) => { e.stopPropagation(); this.deleteBookmark(d.dataset.del); });
  },
  renderQueues(queues) {
    const el = $("rvQueues");
    if (!queues.length) { el.innerHTML = '<div class="rv-empty">No auto queues (needs analytics).</div>'; return; }
    el.innerHTML = queues.map((q, qi) => {
      const items = q.items.map(it => {
        const who = it.player >= 0 && this.demo.players[it.player] ? esc(this.demo.players[it.player].name) : "";
        return `<div class="rv-q-item" data-t="${it.t}" data-pl="${it.player}" title="${esc(it.text || "")}">`
          + `<span class="rv-i-t">R${it.round ?? "?"}</span> ` + (who ? `<b>${who}</b> ` : "")
          + `<span class="rv-q-txt">${esc((it.text || "").slice(0, 90))}</span></div>`;
      }).join("");
      return `<div class="rv-q rv-q-${esc(q.polarity)}">
        <div class="rv-q-h" data-qi="${qi}"><span class="rv-q-tri">&#9656;</span> <span class="rv-q-lbl">${esc(q.label)}</span> <span class="rv-q-n">${q.items.length}</span>
          <button class="rv-q-sess" data-sess="${qi}" title="Guided review session -- step through these moments">&#9654; Session</button></div>
        <div class="rv-q-body" id="rvq${qi}" style="display:none">${items}</div></div>`;
    }).join("");
    el.querySelectorAll(".rv-q-h").forEach(h => h.onclick = () => {
      const body = $("rvq" + h.dataset.qi);
      const open = body.style.display === "none";
      body.style.display = open ? "" : "none";
      h.classList.toggle("open", open);
    });
    el.querySelectorAll(".rv-q-item").forEach(it => it.onclick = () => this.jumpReview(+it.dataset.t, +it.dataset.pl));
    el.querySelectorAll("[data-sess]").forEach(b => b.onclick = (e) => {
      e.stopPropagation();                       // don't toggle the collapse
      const q = queues[+b.dataset.sess];
      if (q) this.startSession(q.items, q.label);
    });
  },
  // ---- guided Review Session: step through a queue's moments, pausing BEFORE each key event
  // (the coach flow: pause -> "what should we do here?" -> Reveal plays it). Works in 2D + 3D.
  startSession(items, label) {
    if (!items || !items.length) return;
    this._session = { items: items.slice(), i: 0, label: label || "Review session" };
    this.toggleReview(false);                    // clear the panel so the viewport is in view
    $("reviewSession").classList.add("show");
    this.gotoSessionItem(0);
  },
  // #48 Death Review: watch every one of a player's deaths in 3D (3rd-person, over the shoulder),
  // driven by the same session runner. Each item pauses ~2s before the death so you see the lead-up.
  startDeathReview(idx, name) {
    if (!this.demo || idx == null || idx < 0) return;
    if (!this.entitled("threeD")) { this._upsell("threeD"); return; }   // 3D death review is Pro
    const items = this.demo.deathsFor(idx);
    const who = name || (this.demo.players[idx] ? this.demo.players[idx].name : "Player");
    if (!items.length) return;
    this.setCamPreset("follow");                 // enters 3D if needed + rides the spectated player
    this.startSession(items, `${who} — deaths (${items.length})`);
  },
  // #62b: plot a player's whole-match death/kill SPOTS on the 2D map -- the visual companion to the
  // per-callout K-D table, so "I keep dying at lower tunnels" is locatable. Coords come straight from
  // the kill events (victim pos for deaths, attacker pos for kills) -- works for any cached demo.
  showPositionsOnMap(idx, name) {
    if (!this.demo || idx == null || idx < 0) return;
    const deaths = [], kills = [];
    for (const k of this.demo.kills) {
      if (k.victim === idx && k.vx != null) deaths.push([k.vx, k.vy]);
      if (k.attacker === idx && k.attacker !== k.victim && k.ax != null) kills.push([k.ax, k.ay]);
    }
    const who = name || (this.demo.players[idx] ? this.demo.players[idx].name : "Player");
    if (!deaths.length && !kills.length) { this._toast && this._toast(`No located duels for ${who}`); return; }
    if (this.view3d.active) this.toggle3d();     // these spots live on the 2D map -- drop back to it
    this.setSpectate(-1);                        // free the camera...
    this.radar.fit();                            // ...and frame the whole map so every spot is in view
    this.radar.posOverlay = { name: who, deaths, kills };
    const lg = $("posLegend");
    if (lg) {
      lg.innerHTML = `<span class="pl-who">${esc(who)}</span>`
        + `<span class="pl-k"><i></i>${kills.length} kills</span>`
        + `<span class="pl-d"><i></i>${deaths.length} deaths</span>`
        + `<button class="pl-x" title="Hide spots">&times;</button>`;
      lg.querySelector(".pl-x").onclick = () => this.clearPositionsOnMap();
      lg.classList.add("show");
    }
  },
  clearPositionsOnMap() {
    this.radar.posOverlay = null;
    const lg = $("posLegend"); if (lg) { lg.classList.remove("show"); lg.innerHTML = ""; }
  },
  gotoSessionItem(i) {
    const s = this._session; if (!s) return;
    s.i = Math.max(0, Math.min(s.items.length - 1, i));
    const it = s.items[s.i];
    this.t = Math.max(0, (it.t || 0));           // it.t is already ~1.5s before the event
    this.playing = false; $("playPause").textContent = "▶";   // PAUSE before the moment
    if (it.player >= 0) this.setSpectate(it.player); else this.freeCamera();
    if (this.refreshTimeline) this.refreshTimeline();
    const who = it.player >= 0 && this.demo.players[it.player] ? this.demo.players[it.player].name + " — " : "";
    $("rsLabel").textContent = s.label;
    $("rsProgress").textContent = `${s.i + 1} / ${s.items.length}`;
    $("rsText").innerHTML = `<span class="rs-rn">R${it.round ?? "?"}</span> ${esc(who)}${esc(it.text || "")}`;
    $("rsNote").value = "";
    $("rsPrev").disabled = s.i === 0;
    $("rsNext").disabled = s.i === s.items.length - 1;
  },
  sessionReveal() { if (this._session) { this.playing = true; $("playPause").textContent = "⏸"; } },
  sessionStep(d) { if (this._session) this.gotoSessionItem(this._session.i + d); },
  exitSession() {
    this._session = null;
    $("reviewSession").classList.remove("show");
    this.playing = false; $("playPause").textContent = "▶";
  },
  async sessionNote() {
    const s = this._session; if (!s) return;
    const it = s.items[s.i];
    const id = this._reviewDemoId(); if (!id) return;
    const body = { t: it.t, round: it.round ?? null, player: it.player, note: $("rsNote").value || "", label: s.label };
    await fetch(`api/reviews/${id}/bookmarks`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch(() => {});
    $("rsNote").value = "";
    $("rsSaveNote").textContent = "✓ saved";
    setTimeout(() => { if ($("rsSaveNote")) $("rsSaveNote").textContent = "+ Note"; }, 1200);
  },

  // ---- Search: round-fact filters + saved routines + quick (analysis) searches -------------
  // A unified "find moments to review" surface. Round filters are objective + generic (any demo);
  // results jump to the replay or become a Review Session (#40); quick searches reuse the auto-queues.
  openSearch() {
    if (!this.demo) return;
    $("searchModal").classList.add("show");
    $("srMap").textContent = this.demo.map ? "(" + this.demo.map + ")" : "";
    // populate buy-bucket options from THIS demo's actual buckets (generic -- any classify_buy output)
    const rc = (this.demo.analytics || {}).round_cards || [];
    const ct = new Set(), t = new Set();
    for (const c of rc) { if (c.buy_ct) ct.add(c.buy_ct); if (c.buy_t) t.add(c.buy_t); }
    const ORD = ["pistol", "eco", "light", "force", "full", "anti_eco"];
    const opts = set => `<option value="">Any</option>` + ORD.filter(b => set.has(b))
      .concat([...set].filter(b => !ORD.includes(b))).map(b => `<option value="${b}">${b}</option>`).join("");
    const keep = (id, html) => { const e = $(id), v = e.value; e.innerHTML = html; e.value = v; };
    keep("srBuyCt", opts(ct)); keep("srBuyT", opts(t));
    this.renderRoutines();
    this.runSearch();
    this.loadQuickSearches();
  },
  _searchLabel() {
    const w = $("srWinner").value, bc = $("srBuyCt").value, bt = $("srBuyT").value;
    const parts = []; if (w) parts.push(w + " win"); if (bc) parts.push("CT " + bc); if (bt) parts.push("T " + bt);
    return parts.length ? parts.join(", ") : "All rounds";
  },
  runSearch() {
    const w = $("srWinner").value, bc = $("srBuyCt").value, bt = $("srBuyT").value;
    const A = this.demo.analytics || {};
    // round_cards carry winner/buy_ct/buy_t/watch_t/summary together (the top-level demo.rounds
    // don't have the buy buckets -- those are added by analytics).
    const items = [];
    for (const c of (A.round_cards || [])) {
      if (w && c.winner !== w) continue;
      if (bc && c.buy_ct !== bc) continue;
      if (bt && c.buy_t !== bt) continue;
      items.push({ round: c.round, t: c.watch_t || 0, player: -1, text: c.summary || ("Round " + c.round) });
    }
    $("srResultHead").innerHTML = items.length
      ? `<b>${items.length}</b> round${items.length === 1 ? "" : "s"} match <button id="srReviewAll" class="up-btn primary">&#9654; Review all as session</button>`
      : `<span class="round">No rounds match these filters.</span>`;
    $("srResults").innerHTML = items.map((it, i) =>
      `<div class="sr-row"><span class="sr-rn">R${it.round}</span><span class="sr-txt">${esc(it.text.slice(0, 120))}</span>`
      + `<button class="sr-watch" data-i="${i}" title="Watch this round">&#9654;</button></div>`).join("");
    $("srResults").querySelectorAll(".sr-watch").forEach(b => b.onclick = () => {
      const it = items[+b.dataset.i]; $("searchModal").classList.remove("show"); this.jumpReview(it.t, -1);
    });
    const ra = $("srReviewAll");
    if (ra) ra.onclick = () => { $("searchModal").classList.remove("show"); this.startSession(items, this._searchLabel()); };
  },
  _routines() { try { return JSON.parse(localStorage.getItem("cs2dp_routines") || "[]"); } catch { return []; } },
  saveRoutine() {
    const f = { winner: $("srWinner").value, buyCt: $("srBuyCt").value, buyT: $("srBuyT").value };
    if (!f.winner && !f.buyCt && !f.buyT) return;
    f.name = this._searchLabel();
    const rs = this._routines().filter(x => x.name !== f.name); rs.unshift(f);
    try { localStorage.setItem("cs2dp_routines", JSON.stringify(rs.slice(0, 12))); } catch {}
    this.renderRoutines();
  },
  renderRoutines() {
    const rs = this._routines();
    $("srRoutines").innerHTML = rs.length
      ? `<span class="round">Routines:</span> ` + rs.map((r, i) =>
        `<button class="sr-routine" data-r="${i}">${esc(r.name)}<span class="sr-rdel" data-del="${i}" title="remove">&times;</span></button>`).join("")
      : "";
    $("srRoutines").querySelectorAll(".sr-routine").forEach(b => b.onclick = (e) => {
      if (e.target.dataset.del != null) {
        const rs2 = this._routines(); rs2.splice(+e.target.dataset.del, 1);
        try { localStorage.setItem("cs2dp_routines", JSON.stringify(rs2)); } catch {}
        this.renderRoutines(); return;
      }
      const r = this._routines()[+b.dataset.r]; if (!r) return;
      $("srWinner").value = r.winner || ""; $("srBuyCt").value = r.buyCt || ""; $("srBuyT").value = r.buyT || "";
      this.runSearch();
    });
  },
  async loadQuickSearches() {
    const el = $("srQuick"), id = this._reviewDemoId();
    if (!id) { el.innerHTML = '<span class="round">Load a Library demo to get analysis-based quick searches.</span>'; return; }
    el.innerHTML = '<span class="round">Loading...</span>';
    const qz = await fetch(`api/reviews/${id}/queues`).then(r => r.json()).catch(() => ({ queues: [] }));
    const queues = (qz && qz.queues) || [];
    if (!queues.length) { el.innerHTML = '<span class="round">No analysis queues for this demo.</span>'; return; }
    el.innerHTML = queues.map((q, i) =>
      `<button class="sr-qbtn sr-q-${esc(q.polarity)}" data-q="${i}">${esc(q.label)} <b>${q.items.length}</b></button>`).join("");
    el.querySelectorAll("[data-q]").forEach(b => b.onclick = () => {
      const q = queues[+b.dataset.q]; $("searchModal").classList.remove("show"); this.startSession(q.items, q.label);
    });
  },
  jumpReview(t, player) {
    this.t = Math.max(0, t || 0);
    if (player >= 0) this.setSpectate(player);
    this.playing = true; $("playPause").textContent = "⏸";
    if (this.refreshTimeline) this.refreshTimeline();
  },
  async addBookmark() {
    const id = this._reviewDemoId(); if (!id) return;
    const r = this.demo.roundAt ? this.demo.roundAt(this.t) : null;
    const pl = this.radar.followIdx;
    // entity auto-detected: a spectated player -> "player", otherwise a moment in time -> "tick"
    const body = { t: this.t, round: r ? r.number : null, player: pl, note: $("rvNote").value || "",
      tag: ($("rvTag").value || "").trim(),
      entity: pl >= 0 ? "player" : "tick",
      ref: pl >= 0 && this.demo.players[pl] ? this.demo.players[pl].name : "" };
    const bm = await fetch(`api/reviews/${id}/bookmarks`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    }).then(r => r.json()).catch(() => null);
    if (bm && bm.id) { $("rvNote").value = ""; $("rvTag").value = ""; this.loadReview(); }
  },
  // #41 add a note attached to a specific entity (round / location / util / player) from anywhere
  // in the UI (round cards, util throws, ...). Opens the Review panel so the note is visible.
  async addEntityNote(opts) {
    const id = this._reviewDemoId();
    if (!id) { alert("Load a demo from your library (not the sample) to save notes."); return; }
    const note = (opts.prompt === false) ? (opts.note || "")
      : (window.prompt(opts.promptText || "Note:", opts.note || "") ?? null);
    if (note === null) return;                       // cancelled
    const body = { entity: opts.entity || "tick", ref: opts.ref || "",
      round: opts.round ?? null, t: opts.t ?? this.t, player: opts.player ?? -1,
      note, tag: opts.tag || opts.entity || "" };
    await fetch(`api/reviews/${id}/bookmarks`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch(() => {});
    this.toggleReview(true);
    this.loadReview();
  },
  async deleteBookmark(bid) {
    const id = this._reviewDemoId(); if (!id) return;
    await fetch(`api/reviews/${id}/bookmarks/${bid}`, { method: "DELETE" }).catch(() => {});
    this.loadReview();
  },

  // --- nade library (CSNADES-style) ----------------------------------------
  async setUtilMode(mode) {
    this.utilMode = mode;
    document.querySelectorAll(".uptab").forEach(b => b.classList.toggle("on", b.dataset.umode === mode));
    $("utilThrows").style.display = mode === "throws" ? "" : "none";
    $("utilLibrary").style.display = mode === "library" ? "" : "none";
    $("utilPlaybook").style.display = mode === "playbook" ? "" : "none";
    this.radar.searchOverlay = []; this.radar.heatmap = false;   // clear; each mode repopulates
    this.closeSuggest && this.closeSuggest();                    // open the library tab on a clean browse
    if ($("libImportBox")) $("libImportBox").style.display = "none";
    await this._ensureLibrary();            // load once so actual-vs-lineup matching works in both tabs
    if (mode === "library") this.buildLibrary();
    else if (mode === "playbook") this.loadPlaybook();
    else this.applyUtilSearch();
  },

  // #45 Team Playbook: normalize this demo's grenades to throws the adherence engine understands.
  _demoThrows() {
    return (this.demo.grenades || []).map(g => {
      const land = g.pts && g.pts.length ? g.pts[g.pts.length - 1] : null;   // [t,x,y]
      const r = this.demo.roundAt ? this.demo.roundAt(g.t0) : null;
      const side = g.thrower >= 0 ? this.demo.teamAtTime(g.thrower, g.t0) : null;
      return land ? { type: g.type, round: g.round != null ? g.round : (r ? r.number : null),
        side: side === 3 ? "ct" : side === 2 ? "t" : null, x: land[1], y: land[2] } : null;
    }).filter(Boolean);
  },
  // adherence engine mirror of playbook.check_adherence -- KEEP IN SYNC (match_dist 220, exec 0.6)
  checkAdherenceJS(play, throws, matchDist = 220, execFrac = 0.6) {
    const util = play.util || [];
    const out = { rounds_applicable: 0, rounds_executed: 0, adherence_pct: 0, elements: [] };
    if (!util.length) return out;
    const rounds = [...new Set(throws.filter(t => t.side === play.side && t.round != null).map(t => t.round))].sort((a, b) => a - b);
    if (!rounds.length) return out;
    const dist = (ax, ay, bx, by) => Math.hypot(ax - bx, ay - by);
    const byRound = {};
    for (const r of rounds) {
      const rt = throws.filter(t => t.round === r && t.side === play.side);
      const elems = util.map(u => rt.some(t => t.type === u.type && dist(t.x, t.y, u.x, u.y) <= matchDist));
      byRound[r] = { present: elems.filter(Boolean).length, elems };
    }
    const executed = rounds.filter(r => byRound[r].present / util.length >= execFrac);
    out.rounds_applicable = rounds.length;
    out.rounds_executed = executed.length;
    out.adherence_pct = Math.round(100 * executed.length / rounds.length);
    out.elements = util.map((u, i) => {
      const used = rounds.filter(r => byRound[r].elems[i]).length;
      return { type: u.type, used, of: rounds.length, used_pct: Math.round(100 * used / rounds.length) };
    });
    return out;
  },
  async loadPlaybook() {
    const map = this.demo.map || "";
    this._plays = await fetch("api/playbook?map=" + encodeURIComponent(map)).then(r => r.json()).then(d => d.plays || []).catch(() => []);
    // round picker = rounds that have utility, for "save round as play"
    const rounds = [...new Set((this.demo.grenades || []).map(g => g.round).filter(r => r != null))].sort((a, b) => a - b);
    $("pbRound").innerHTML = rounds.map(r => `<option value="${r}">R${r}</option>`).join("") || `<option value="">--</option>`;
    this.renderPlays();
  },
  renderPlays() {
    const plays = this._plays || [];
    $("pbCount").textContent = plays.length ? `(${plays.length})` : "";
    const el = $("pbList");
    if (!plays.length) { el.innerHTML = `<div class="rv-empty">No plays for ${esc(this.demo.map || "this map")} yet. Watch a round you ran well, pick it above, and save its utility as a play.</div>`; return; }
    const throws = this._demoThrows();
    el.innerHTML = plays.map(p => {
      const a = this.checkAdherenceJS(p, throws);
      const cls = a.adherence_pct >= 60 ? "good" : a.adherence_pct >= 30 ? "" : "bad";
      const elems = (a.elements || []).map(e =>
        `<span class="pb-el ${e.used_pct >= 60 ? "good" : e.used_pct < 30 ? "bad" : ""}">${esc(e.type)} ${e.used_pct}%</span>`).join("");
      return `<div class="pb-play">
        <div class="pb-h"><b>${esc(p.name)}</b> <span class="side-${p.side}">${p.side.toUpperCase()}</span>
          <span class="pb-adh ${cls}">${a.adherence_pct}%</span>
          <button class="rv-del" data-pbdel="${esc(p.id)}" title="Delete play">&times;</button></div>
        <div class="pb-sub">${a.rounds_executed}/${a.rounds_applicable} util rounds ran this · ${(p.util || []).length} nades</div>
        <div class="pb-els">${elems}</div></div>`;
    }).join("");
    el.querySelectorAll("[data-pbdel]").forEach(b => b.onclick = () => this.deletePlay(b.dataset.pbdel));
  },
  async savePlayFromRound() {
    const round = +$("pbRound").value, side = $("pbSide").value;
    if (!round) return;
    const throws = this._demoThrows().filter(t => t.round === round && t.side === side);
    if (!throws.length) { this.askConfirm && this.askConfirm("No utility", `<div class="cf-line">No ${side.toUpperCase()} utility thrown in round ${round}.</div>`, "OK"); return; }
    // de-dup near-identical landings (mirror playbook.play_from_throws)
    const util = [];
    for (const t of throws) {
      if (util.some(u => u.type === t.type && Math.hypot(u.x - t.x, u.y - t.y) <= 120)) continue;
      util.push({ type: t.type, x: +t.x.toFixed(1), y: +t.y.toFixed(1) });
    }
    const name = ($("pbName").value || "").trim() || `${side.toUpperCase()} R${round}`;
    const saved = await fetch("api/playbook", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ map: this.demo.map, side, name, util }) }).then(r => r.json()).catch(() => null);
    if (saved && saved.id) { $("pbName").value = ""; this.loadPlaybook(); }
  },
  async deletePlay(pid) {
    await fetch("api/playbook/" + encodeURIComponent(pid), { method: "DELETE" }).catch(() => {});
    this.loadPlaybook();
  },

  // #61 suggest lineups thrown consistently across the cached library -> one-click add to nades
  closeSuggest() { const el = $("libSuggestions"); el.innerHTML = ""; el.dataset.open = ""; $("libSuggest").classList.remove("on"); },
  async suggestNades() {
    const el = $("libSuggestions");
    if (el.dataset.open === "1") { this.closeSuggest(); return; }   // toggle: 2nd click closes -> back to library
    el.dataset.open = "1"; $("libSuggest").classList.add("on");
    const map = this.demo ? this.demo.map : "";
    const sha = (this.demo && this.demo.raw) ? (this.demo.raw.source_sha1 || "") : "";
    el.innerHTML = `<div class="sg-head"><b>Suggested lineups</b> <span class="round">this demo</span>`
      + `<button class="sg-close" title="Back to library">&times;</button></div><div class="rv-empty">Scanning this demo…</div>`;
    el.querySelector(".sg-close").onclick = () => this.closeSuggest();
    // scope strictly to THE demo being watched (sha), on this map -- not other demos on the same map
    this._suggRaw = (await fetch("api/nades/suggest?map=" + encodeURIComponent(map) + "&sha=" + encodeURIComponent(sha))
      .then(r => r.json()).catch(() => ({ suggestions: [] }))).suggestions || [];
    this._paintSuggestions();
  },
  // (re)render the cached suggestions, filtered by the library type chip (all/smoke/molotov/flash/he).
  // Called on open and whenever the type chip changes -- no re-fetch, so type filtering is instant.
  _paintSuggestions() {
    const el = $("libSuggestions");
    if (el.dataset.open !== "1") return;
    const mapn = ((this.demo && this.demo.map) || "").replace(/^de_/, "");
    const tf = this.libFilter || "all";
    const head = (body) => `<div class="sg-head"><b>Suggested lineups</b> `
      + `<span class="round">this demo${tf !== "all" ? " &middot; " + esc(tf) : ""}</span>`
      + `<button class="sg-close" title="Back to library">&times;</button></div>${body}`;
    const sugg = (this._suggRaw || []).filter(s => tf === "all" || s.type === tf);
    this._suggestions = sugg;          // addSuggestion()/row-click index into this filtered list
    const rows = sugg.length ? sugg.map((s, i) => `<div class="lib-sg" data-sg="${i}" title="Click to see this throw on the map">
      <span class="side-${s.side}">${s.side.toUpperCase()}</span> <b>${esc(s.type)}</b>
      <span class="round">${esc(s.name || "")} &middot; ${s.count}&times; thrown</span>
      <button class="up-btn sg-add" data-sg="${i}">+ add</button></div>`).join("")
      : `<div class="rv-empty">No repeated ${tf === "all" ? "utility" : esc(tf)} in this ${esc(mapn)} demo yet — a lineup shows up here once it's thrown 2+ times from the same spot.${tf !== "all" ? " (Type filter: " + esc(tf) + ".)" : ""}</div>`;
    el.innerHTML = head(rows);
    el.querySelector(".sg-close").onclick = () => this.closeSuggest();
    el.querySelectorAll(".lib-sg").forEach(row => row.onclick = (e) => {
      if (e.target.closest(".sg-add")) return;                  // the + add button has its own handler
      const s = this._suggestions[+row.dataset.sg];
      if (s && s.nade) this.showLibNade(s.nade);                // preview the throw -> land arc on the map
    });
    el.querySelectorAll(".sg-add").forEach(b => b.onclick = (e) => { e.stopPropagation(); this.addSuggestion(+b.dataset.sg, b); });
  },
  async addSuggestion(i, btn) {
    const s = (this._suggestions || [])[i];
    if (!s || !s.nade) return;
    const saved = await fetch("api/nades", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(s.nade) }).then(r => r.json()).catch(() => null);
    if (saved && saved.id) { btn.textContent = "✓ added"; btn.disabled = true; this.library = null; this._ensureLibrary(); }
  },
  async _ensureLibrary() {
    if (!this.library) this.library = await fetch("api/nades?t=" + Date.now()).then(r => r.json()).catch(() => []);
    return this.library;
  },
  // match an actual throw to a saved lineup by BOTH landing and throw position.
  // quality "exact" = lands AND thrown from the same spot; "landing" = lands right but from a
  // different spot ("close but wrong setup"). Returns {n, quality} or null. Pass a grenade g.
  _matchLineup(g) {
    const map = this.demo ? this.demo.map : null;
    if (!g || !g.pts || !g.pts.length) return null;
    const land = g.pts[g.pts.length - 1], thr = g.pts[0];
    let best = null, bd = 160;
    for (const n of (this.library || [])) {
      if (n.map !== map || n.type !== g.type || !n.land_pos) continue;
      const d = Math.hypot(n.land_pos[0] - land[1], n.land_pos[1] - land[2]);
      if (d < bd) { bd = d; best = n; }
    }
    if (!best) return null;
    let quality = "landing";
    if (best.throw_pos) {
      const td = Math.hypot(best.throw_pos[0] - thr[1], best.throw_pos[1] - thr[2]);
      quality = td <= 250 ? "exact" : "landing";   // same landing, from the same spot?
    }
    return { n: best, quality };
  },
  // how many of this match's actual throws land on a given lineup (reverse match)
  _lineupThrowCount(n) {
    if (!n.land_pos || !this.demo) return 0;
    let c = 0;
    for (const g of (this.demo.grenades || [])) {
      if (g.type !== n.type || !g.pts || !g.pts.length) continue;
      const l = g.pts[g.pts.length - 1];
      if (Math.hypot(l[1] - n.land_pos[0], l[2] - n.land_pos[1]) <= 160) c++;
    }
    return c;
  },
  async buildLibrary() {
    await this._ensureLibrary();
    this.libFilter = this.libFilter || "all";
    const types = [["all", "All"], ["smoke", "Smoke"], ["molotov", "Molotov"], ["flash", "Flash"], ["he", "HE"]];
    $("libTypes").innerHTML = types.map(([k, l]) =>
      `<button class="chip ${k === this.libFilter ? "on" : ""}" data-lt="${k}">${l}</button>`).join("");
    $("libTypes").querySelectorAll(".chip").forEach(c => c.onclick = () => {
      this.libFilter = c.dataset.lt; this.buildLibrary();
      if ($("libSuggestions").dataset.open === "1") this._paintSuggestions();   // keep suggestions in sync
    });
    this.applyLibrary();
  },
  applyLibrary() {
    const map = this.demo ? this.demo.map : null;
    const res = (this.library || []).filter(n =>
      (!map || n.map === map) && (this.libFilter === "all" || n.type === this.libFilter));
    $("libCount").textContent = `${res.length} lineup${res.length === 1 ? "" : "s"} | ${map || "--"} | pick where it lands`;
    // group by WHERE it lands (target callout) -- pick a target, then a throw spot
    const groups = {};
    for (const n of res) {
      const key = (n.target_callout || "").trim() || "Unlabelled";
      (groups[key] = groups[key] || []).push(n);
    }
    const col = { smoke: "#cfd3da", molotov: "#ff6a2b", flash: "#ffe27a", he: "#ff8a3d", decoy: "#99a3b0" };
    const open = this._libOpenTarget;
    const byId = {}; res.forEach(n => byId[n.id] = n);
    $("libBrowse").innerHTML = Object.keys(groups).sort().map(target => {
      const lines = groups[target].slice().sort((a, b) => (b.favorite ? 1 : 0) - (a.favorite ? 1 : 0));
      const dot = `<span class="ut" style="background:${col[lines[0].type] || "#9aa"}"></span>`;
      const spots = lines.map(n => {
        const tech = (n.technique || []).length ? ` | ${esc(n.technique.join(","))}` : "";
        const vid = n.video ? `<button class="ls-btn nv-play" title="Watch clip">\u25b6</button>` : "";
        const nopos = n.land_pos ? "" : ` <span class="novid">| set pos</span>`;
        const thrown = this._lineupThrowCount(n);
        const tc = thrown ? ` <span class="lib-thrown" title="thrown this match">thrown ${thrown}x</span>` : "";
        const star = `<button class="ls-btn ls-fav ${n.favorite ? "on" : ""}" title="Favorite">${n.favorite ? "\u2605" : "\u2606"}</button>`;
        return `<div class="lib-spot" data-id="${n.id}">
          <span class="ls-from">from <b>${esc(n.throw_callout || "?")}</b>${tech}${tc}${nopos}</span>
          <span class="ls-actions">${vid}${star}<button class="ls-btn ls-edit" title="Edit">\u270e</button><button class="ls-btn ls-del" title="Delete">x</button></span></div>`;
      }).join("");
      return `<div class="lib-target ${target === open ? "open" : ""}">
        <div class="lib-th" data-tg="${esc(target)}">${dot}<span class="lib-tn">${esc(target)}</span><span class="lib-tc">${lines.length}</span></div>
        <div class="lib-spots">${spots}</div></div>`;
    }).join("") || `<div class="up-empty">No lineups for this map yet. Add one, pull from this demo, or import.</div>`;
    $("libBrowse").querySelectorAll(".lib-th").forEach(el => el.onclick = () => {
      this._libOpenTarget = this._libOpenTarget === el.dataset.tg ? null : el.dataset.tg;
      this.applyLibrary();
    });
    $("libBrowse").querySelectorAll(".lib-spot").forEach(el => el.onclick = (e) => {
      const n = byId[el.dataset.id];
      if (!n) return;
      if (e.target.closest(".nv-play")) this.showVideo(n);
      else if (e.target.closest(".ls-fav")) this.toggleFavNade(n);
      else if (e.target.closest(".ls-edit")) this.editNade(n);
      else if (e.target.closest(".ls-del")) this.deleteNade(n);
      else this.showLibNadePov(n);            // draw on 2D, then offer the 3D throw POV
    });
  },
  showLibNade(n) {
    if (!n || !n.land_pos) return;            // nothing to draw without a landing position
    const pts = [];
    if (n.throw_pos) pts.push([0, n.throw_pos[0], n.throw_pos[1]]);
    pts.push([1, n.land_pos[0], n.land_pos[1]]);
    this.radar.searchOverlay = [{ type: n.type, pts }];
    this.view3d.showLineup3D(n);              // also draw the arc + landing volume in 3D
  },
  // Clicking a library lineup: draw it on the 2D map, then ASK whether to fly into 3D and view it
  // from the throw spot's first-person POV (so you can see how to line it up).
  async showLibNadePov(n) {
    this.showLibNade(n);                       // immediate 2D preview (+ the 3D lineup group)
    if (!(n && n.throw_pos && n.land_pos)) {   // no throw point saved -> nothing to stand at
      this._toast && this._toast("This lineup has no throw spot saved, so there's no 3D POV to show.");
      return;
    }
    const body = `<div class="cf-line"><b>${esc(n.name || n.type)}</b> &middot; ${esc((n.side || "").toUpperCase())}</div>`
      + `<div class="cf-line cf-mut">Fly into 3D and stand at the throw spot, looking toward where it lands.</div>`;
    if (!(await this.askConfirm("See this lineup in 3D?", body, "Yes, show in 3D"))) return;
    if (!this.view3d.active) this.enter3D(n.throw_pos[0], n.throw_pos[1]);   // sets up the 3D chrome
    this.setCamPreset("fly");                  // free-fly (don't ride a player); clears scripted cam
    this.view3d.showLineup3D(n);               // (re)draw the arc + landing volume
    this.view3d.enterLineupPov(n);             // place the camera at the throw, looking at the landing
  },

  // ---- add-lineup flow (set throw/land by clicking the map + attach a video) ----
  toggleAddForm() {
    const f = $("libAddForm");
    if (f.style.display !== "none") { f.style.display = "none"; this.nadeCapture = null; return; }
    this.buildAddForm(); f.style.display = "block";
  },
  buildAddForm(edit) {
    this._editingId = edit ? edit.id : null;
    this._na = { throw_pos: (edit && edit.throw_pos) || null, land_pos: (edit && edit.land_pos) || null };
    const types = ["smoke", "flash", "molotov", "he", "decoy"];
    $("libAddForm").innerHTML = `
      <input id="naName" class="lf-in" placeholder="Name (e.g. Mid Doors smoke)">
      <div class="lf-row">
        <select id="naType" class="lf-in">${types.map(t => `<option value="${t}">${t}</option>`).join("")}</select>
        <select id="naSide" class="lf-in"><option value="T">T</option><option value="CT">CT</option><option value="both">both</option></select>
      </div>
      <input id="naThrowCo" class="lf-in" placeholder="Throw from (e.g. Top Suicide)">
      <input id="naTargetCo" class="lf-in" placeholder="Lands at (e.g. Mid Doors)">
      <input id="naTech" class="lf-in" placeholder="Technique: jumpthrow, crouch, runthrow">
      <div class="lf-cap">
        <button id="naSetThrow" class="up-btn">Set throw</button><span id="naThrowPos" class="lf-pos">--</span>
        <button id="naSetLand" class="up-btn">Set landing</button><span id="naLandPos" class="lf-pos">--</span>
      </div>
      <input id="naVideoUrl" class="lf-in" placeholder="Video URL (YouTube or .mp4)">
      <label class="lf-file">or upload a clip <input type="file" id="naVideoFile" accept="video/*"></label>
      <textarea id="naAim" class="lf-in" placeholder="Aim point / notes"></textarea>
      <div class="lf-row"><button id="naSave" class="up-btn primary">Save lineup</button><button id="naCancel" class="up-btn">Cancel</button></div>
      <div id="naMsg" class="lf-msg"></div>`;
    if (edit) {   // prefill for editing an existing lineup
      $("naName").value = edit.name || "";
      $("naType").value = edit.type || "smoke";
      $("naSide").value = edit.side || "T";
      $("naThrowCo").value = edit.throw_callout || "";
      $("naTargetCo").value = edit.target_callout || "";
      $("naTech").value = (edit.technique || []).join(", ");
      $("naVideoUrl").value = (edit.video && !edit.video.startsWith("/nades/")) ? edit.video : "";
      $("naAim").value = edit.aim || "";
      if (edit.throw_pos) $("naThrowPos").textContent = `${edit.throw_pos[0]}, ${edit.throw_pos[1]}`;
      if (edit.land_pos) $("naLandPos").textContent = `${edit.land_pos[0]}, ${edit.land_pos[1]}`;
      $("naSave").textContent = "Update lineup";
    }
    $("naSetThrow").onclick = () => this.startCapture("throw_pos", "naThrowPos");
    $("naSetLand").onclick = () => this.startCapture("land_pos", "naLandPos");
    $("naSave").onclick = () => this.saveNade();
    $("naCancel").onclick = () => this.toggleAddForm();
  },
  editNade(n) {
    this.buildAddForm(n);
    $("libAddForm").style.display = "block";
    $("libAddForm").scrollIntoView({ block: "nearest" });
  },
  async deleteNade(n) {
    if (!confirm(`Delete lineup "${n.name || n.type}"? This can't be undone.`)) return;
    await fetch("api/nades/" + encodeURIComponent(n.id), { method: "DELETE" }).catch(() => {});
    this.library = null;
    await this.buildLibrary();
  },
  async toggleFavNade(n) {
    const fav = !n.favorite;
    await fetch("api/nades/" + encodeURIComponent(n.id) + "/favorite", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ favorite: fav }),
    }).catch(() => {});
    n.favorite = fav;            // optimistic local update
    this.applyLibrary();
  },
  startCapture(field, label) {
    this.nadeCapture = field; this._naLabel = label;
    const el = $(label); el.textContent = "click the map..."; el.classList.add("capturing");
  },
  captureNadePos(sx, sy) {
    const [wx, wy] = this.radar.worldFromScreen(sx, sy);
    this._na[this.nadeCapture] = [Math.round(wx), Math.round(wy), 0];   // z unknown from a 2D click
    const el = $(this._naLabel); el.textContent = `${Math.round(wx)}, ${Math.round(wy)}`; el.classList.remove("capturing");
    this.nadeCapture = null;
    const pts = [];
    if (this._na.throw_pos) pts.push([0, this._na.throw_pos[0], this._na.throw_pos[1]]);
    if (this._na.land_pos) pts.push([1, this._na.land_pos[0], this._na.land_pos[1]]);
    if (pts.length) this.radar.searchOverlay = [{ type: $("naType").value, pts }];
  },
  async saveNade() {
    const msg = $("naMsg");
    const entry = {
      map: this.demo ? this.demo.map : "", type: $("naType").value, side: $("naSide").value,
      name: $("naName").value.trim() || `${$("naType").value} -> ${$("naTargetCo").value.trim() || "?"}`,
      throw_callout: $("naThrowCo").value.trim(), target_callout: $("naTargetCo").value.trim(),
      technique: $("naTech").value.split(",").map(s => s.trim()).filter(Boolean),
      throw_pos: this._na.throw_pos, land_pos: this._na.land_pos,
      aim: $("naAim").value.trim(), video: $("naVideoUrl").value.trim(), source: "user",
    };
    if (!entry.map) { msg.textContent = "Load a demo first (it sets the map)."; return; }
    const file = $("naVideoFile").files[0];
    if (file) {
      msg.textContent = "Uploading video...";
      const fd = new FormData(); fd.append("video", file);
      const up = await fetch("api/nades/video", { method: "POST", body: fd }).then(r => r.json()).catch(() => ({}));
      if (!up.url) { msg.textContent = "Video upload failed: " + (up.error || "?"); return; }
      entry.video = up.url;
    }
    const editing = this._editingId;
    const url = editing ? "api/nades/" + encodeURIComponent(editing) : "api/nades";
    const r = await fetch(url, {
      method: editing ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(entry),
    }).then(r => r.json()).catch(() => ({ error: "save failed" }));
    if (r.error) { msg.textContent = r.error; return; }
    this.library = null; this._editingId = null; this._libOpenTarget = entry.target_callout || "Unlabelled";
    $("libAddForm").style.display = "none";
    await this.buildLibrary();
    $("libCount").textContent = (editing ? "Updated \u2713 " : "Saved \u2713 ") + (entry.name);
  },

  // ---- video modal ----------------------------------------------------------
  showVideo(n) {
    if (!n || !n.video) return;
    $("nvTitle").textContent = `${n.name || n.type} -- from ${n.throw_callout || "?"} -> ${n.target_callout || "?"}`;
    $("nvBody").innerHTML = this.videoEmbed(n.video);
    $("nvMeta").textContent = [n.side, (n.technique || []).join(", "), n.aim].filter(Boolean).join(" | ");
    $("nadeVideo").classList.add("show");
    if (n.land_pos) this.showLibNade(n);
  },
  hideVideo() { $("nadeVideo").classList.remove("show"); $("nvBody").innerHTML = ""; },
  videoEmbed(url) {
    const yt = url.match(/(?:youtu\.be\/|youtube\.com\/(?:watch\?v=|embed\/|shorts\/))([\w-]{6,})/);
    if (yt) return `<iframe class="nv-frame" src="https://www.youtube.com/embed/${yt[1]}" allow="autoplay; encrypted-media" allowfullscreen></iframe>`;
    return `<video class="nv-vid" src="${esc(url)}" controls autoplay></video>`;
  },
  demoNadeCandidates() {
    // trajectory points are [t, x, y, z] -- use world x=[1], y=[2], z=[3]
    const xyz = p => [p[1], p[2], p.length > 3 ? p[3] : 0];
    const out = [], seen = [];
    for (const t of ["smoke", "molotov", "flash", "he"]) {
      let n = 0;
      for (const g of (this.demo.grenades || [])) {
        if (g.type !== t || !g.pts || !g.pts.length) continue;
        const land = xyz(g.pts[g.pts.length - 1]), thr = xyz(g.pts[0]);
        if (seen.some(s => Math.abs(s[0] - land[0]) < 120 && Math.abs(s[1] - land[1]) < 120)) continue;
        seen.push(land);
        out.push({
          map: this.demo.map, type: t, side: "both",
          name: `${t} @ (${Math.round(land[0])}, ${Math.round(land[1])})`,
          throw_pos: [+thr[0].toFixed(1), +thr[1].toFixed(1), +thr[2].toFixed(1)],
          land_pos: [+land[0].toFixed(1), +land[1].toFixed(1), +land[2].toFixed(1)], source: "demo",
        });
        if (++n >= 12) break;
      }
    }
    return out;
  },
  async libFromDemo() {
    if (!this.demo) return;
    const cands = this.demoNadeCandidates();
    const r = await fetch("api/nades/import", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nades: cands, source: "demo" }),
    }).then(r => r.json()).catch(() => ({ added: 0 }));
    this.library = null;
    await this.buildLibrary();
    $("libCount").textContent = `+${r.added || 0} added from this demo | ${this.demo.map}`;
  },
  async libImport() {
    const box = $("libImportBox");
    if (box.style.display === "none") { box.style.display = "block"; box.focus(); return; }
    if (!box.value.trim()) { box.style.display = "none"; return; }   // open + empty -> toggle closed
    let items;
    try { items = JSON.parse(box.value); } catch { box.style.borderColor = "#e25555"; return; }
    box.style.borderColor = "";
    const body = Array.isArray(items) ? items : { nades: items, source: "csnades-import" };
    const r = await fetch("api/nades/import", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    }).then(r => r.json()).catch(() => ({ added: 0 }));
    box.value = ""; box.style.display = "none"; this.library = null;
    await this.buildLibrary();
    $("libCount").textContent = `+${r.added || 0} imported`;
  },

  // --- scoreboard -----------------------------------------------------------
  buildScoreboard() {
    $("sbCT").innerHTML = ""; $("sbT").innerHTML = ""; this.rows = {};
    for (let i = 0; i < this.demo.players.length; i++) {
      const p = this.demo.players[i];
      const el = document.createElement("div");
      el.className = "prow";
      el.dataset.idx = i;
      el.innerHTML = `
        <span class="pslot" title="Press this number to spectate"></span>
        <span class="dot" style="background:${this.demo.colorFor(i, p.team)}"></span>
        <span class="pname">${esc(p.name)}</span>
        <span class="pmoney"></span>
        <span class="loadout-top"></span>
        <span class="pkad"></span>
        <span class="loadout-nades"></span>
        <div class="hpbar"><i></i></div>`;
      el.onclick = () => this.setSpectate(i);
      this.rows[i] = { el, parent: null };
    }
  },

  updateScoreboard(state) {
    const stats = this.demo.statsUpTo(this.t);
    for (let i = 0; i < state.players.length; i++) {
      const p = state.players[i]; const row = this.rows[i];
      if (!p || !row) continue;
      const parent = p.team === 3 ? $("sbCT") : $("sbT");
      if (row.parent !== parent) { parent.appendChild(row.el); row.parent = parent; }
      row.el.querySelector(".dot").style.background = this.demo.colorFor(i, p.team);
      row.el.classList.toggle("dead", !p.alive);
      row.el.querySelector(".pkad").textContent = `${stats[i].k}/${stats[i].a}/${stats[i].d}`;
      const mEl = row.el.querySelector(".pmoney");
      if (mEl) mEl.textContent = p.money != null ? "$" + p.money : "";
      const hp = p.alive ? p.hp : 0;
      const bar = row.el.querySelector(".hpbar i");
      bar.style.width = hp + "%";
      bar.style.background = hp > 40 ? "#5fbf5f" : "#e25555";
      // loadout in two fixed icon rows: [guns|armor|kit] on top, [nade rack] below (updates on change)
      const lo = p.alive ? this.demo.loadoutAt(i, this.t) : null;
      const armorTier = (p.alive && p.armor > 0) ? (p.helmet ? 2 : 1) : 0;   // 2=kevlar+helmet, 1=vest only
      const parts = p.alive ? this._fmtLoadout(lo || [], p.kit, armorTier, p.weapon) : { top: "", nades: "" };
      const key = parts.top + "␟" + parts.nades;
      if (row._loHtml !== key) {
        row._loHtml = key;
        row.el.querySelector(".loadout-top").innerHTML = parts.top;
        row.el.querySelector(".loadout-nades").innerHTML = parts.nades;
      }
    }
    // slot numbers down the left of each board -> match the 1-0 spectate keys (CT 1-5, T 6-9,0)
    [...$("sbCT").children].forEach((el, i) => { const s = el.querySelector(".pslot"); if (s) s.textContent = i + 1; });
    [...$("sbT").children].forEach((el, i) => { const s = el.querySelector(".pslot"); if (s) s.textContent = (i + 6) % 10; });
    const sc = this.demo.scoreAt(this.t);
    $("scoreCT").textContent = sc.ct; $("scoreT").textContent = sc.t;
    const r = this.demo.roundAt(this.t);
    const r1 = this.demo.rounds[0];
    $("roundLabel").textContent = (r1 && this.t < (r1.start_t ?? 0)) ? "Warmup"
      : (r ? `Round ${r.number}/${this.demo.rounds.length}` : "");
    // round clock: CS-style 1:55 countdown from freeze-end. Hidden in warmup and once the bomb
    // is planted (the bomb banner shows the C4 timer then, exactly like in-game).
    const rc = $("roundClock");
    if (rc) {
      let txt = "";
      if (r && this.t >= (r.start_t ?? 0)) {
        const fe = r.freeze_end_t ?? r.start_t ?? 0;
        const be = this.demo.bombEventNear(this.t);
        if (be.planted && this.t >= be.planted.t) txt = "";          // bomb timer takes over
        else if (this.t < fe) txt = "1:55";                          // freeze time
        else txt = fmt(Math.max(0, 115 - (this.t - fe)));            // 115s = competitive round time
      }
      rc.textContent = txt;
    }
  },
  _fmtLoadout(weapons, kit, armorTier = 0, activeWeapon = "") {
    const isKnife = w => /knife|bayonet|karambit|daggers/i.test(w);
    const nadeType = w => {
      if (/flash/i.test(w)) return "flash";
      if (/smoke/i.test(w)) return "smoke";
      if (/molotov|incend/i.test(w)) return "molotov";
      if (/decoy/i.test(w)) return "decoy";
      if (/grenade|explos/i.test(w)) return "he";          // "High Explosive Grenade"
      return null;
    };
    const guns = [], nc = { flash: 0, smoke: 0, he: 0, molotov: 0, decoy: 0 };
    for (const w of weapons) {
      if (!w || isKnife(w)) continue;
      const nt = nadeType(w);
      if (nt) nc[nt]++; else guns.push(w);
    }
    // top icon row: guns (SVG silhouettes), then armor + kit (user PNG icons via CSS mask -> tintable)
    let top = guns.map(g => `<span class="gi" title="${esc(WEAP(g))}">${GUN_SVG(g)}</span>`).join("");
    if (armorTier === 2) top += `<span class="ai full" title="Kevlar + Helmet"><i class="ic"></i></span>`;
    else if (armorTier === 1) top += `<span class="ai vest" title="Kevlar (no helmet)"><i class="ic"></i></span>`;
    if (kit) top += `<span class="ai kit" title="Defuse kit"><i class="ic"></i></span>`;
    // bottom icon row: ONLY nades currently held (bought) -- thrown/used ones disappear. The one that's
    // SELECTED (the active weapon is that nade) glows (.sel). flash/smoke/he/molotov use the user's PNG
    // icons (masked -> currentColor tint); decoy keeps the inline SVG (no PNG provided).
    const activeType = nadeType(activeWeapon || "");
    const nades = NADE_ORDER.filter(ty => nc[ty] > 0).map(ty => {
      const n = nc[ty], sel = ty === activeType;
      const icon = ty === "decoy" ? NADE_ICON.decoy : `<i class="ic"></i>`;
      return `<span class="nslot ns-${ty} have${sel ? " sel" : ""}" title="${NADE_NAME[ty]}${n > 1 ? " x" + n : ""}">`
        + icon + (n > 1 ? `<i class="nx">${n}</i>` : "") + `</span>`;
    }).join("");
    return { top, nades };
  },
  updateBombBanner() {
    const banner = $("bombBanner");
    const be = this.demo.bombEventNear(this.t);
    if (!be.planted || this.t < be.planted.t) { banner.classList.remove("show"); return; }
    if (be.defused && this.t >= be.defused.t) {
      banner.textContent = "BOMB DEFUSED"; banner.className = "bomb-banner defused show"; return;
    }
    const c4 = Math.max(0, 40 - (this.t - be.planted.t));   // CS2 C4 detonation timer = 40s
    let defusing = null;                                     // live defuse countdown
    if (be.defused) {
      // prefer the REAL begin-defuse tick (when the CT actually got on the bomb);
      // fall back to deriving it from the kit for caches parsed before v9.
      const start = (be.begindefuse && be.begindefuse.t <= be.defused.t)
        ? be.begindefuse.t : this._defuseInfo(be.defused).start;
      if (this.t >= start && this.t < be.defused.t) defusing = be.defused.t - this.t;
    }
    if (defusing != null) {
      banner.textContent = `DEFUSING ${defusing.toFixed(1)}s   |   C4 ${c4.toFixed(0)}s`;
      banner.className = "bomb-banner defusing show";
    } else if (c4 > 0) {
      banner.textContent = `\u25cf BOMB  ${c4.toFixed(0)}s`;
      banner.className = "bomb-banner show";
    } else {
      banner.textContent = "BOMB EXPLODED"; banner.className = "bomb-banner exploded show";
    }
  },
  // derive the defuse window from the defuser's kit (5s with kit, 10s without), since the demo
  // only records the completed bomb_defused event, not the begin.
  _defuseInfo(defEvent) {
    if (defEvent._info) return defEvent._info;
    let dur = 10;
    try {
      const st = this.demo.stateAt(defEvent.t - 0.1), b = st.bomb;
      if (b) {
        let best = null, bd = Infinity;
        for (const p of st.players) {
          if (p && p.alive && p.team === 3) {
            const d = (p.x - b.x) ** 2 + (p.y - b.y) ** 2;
            if (d < bd) { bd = d; best = p; }
          }
        }
        if (best && best.kit) dur = 5;
      }
    } catch (e) { /* default 10s */ }
    defEvent._info = { dur, start: defEvent.t - dur };
    return defEvent._info;
  },

  updateRowHighlight() {
    for (const k in this.rows) {
      this.rows[k].el.classList.toggle("spec", +k === this.radar.followIdx);
    }
  },

  updateKillfeed() {
    const feed = this.demo.killFeed(this.t);   // all kills this round, until round ends
    const row = (k) => {
      const a = k.attacker != null && k.attacker >= 0 ? this.demo.players[k.attacker] : null;
      const v = this.demo.players[k.victim];
      const acol = a ? this.demo.colorFor(k.attacker, this.demo.teamAtTime(k.attacker, k.t)) : "#888";
      const vcol = this.demo.colorFor(k.victim, this.demo.teamAtTime(k.victim, k.t));
      const hs = k.headshot ? " *" : "";
      const an = a ? `<b style="color:${acol}">${esc(a.name)}</b>` : `<i>world</i>`;
      return `<div class="krow">${an}
        <span class="kw">${WEAP(k.weapon)}${hs}</span>
        <b style="color:${vcol}">${esc(v.name)}</b></div>`;
    };
    const compact = feed.slice(-6).reverse().map(row).join("");    // newest at top, last 6
    // on-map kill feed, top-right of the radar (2D); the 3D feed sits below the minimap.
    const kf = $("killfeed");
    kf.innerHTML = compact;
    kf.classList.toggle("show", !this.view3d.active && feed.length > 0);
    const k3 = $("killfeed3d");
    if (this.view3d.active && feed.length) { k3.innerHTML = compact; k3.classList.add("show"); }
    else k3.classList.remove("show");
  },
  // minimap on-screen size (settings "Map size"). 208px base * multiplier; re-fit if it's live.
  _applyMiniSize() {
    const mc = $("miniCanvas"); if (!mc) return;
    const px = Math.round(208 * (this._miniSize || 1));
    mc.style.width = px + "px"; mc.style.height = px + "px";
    if (this.view3d.active && this.miniRadar.map) { this.miniRadar.resize(); this.miniRadar.fit(); }
  },
  // 3D-overlay minimap: 2D radar with player dots. Zoom 1 = whole map (centred); >1 zooms in and
  // follows the spectated player (or the 3D camera in free cam). Enable/zoom set in settings.
  _renderMinimap() {
    const mc = $("miniCanvas");
    if (!mc) return;
    if (!this.view3d.active || !this.miniRadar.map || !this.curState || this._miniOn === false) {
      mc.style.display = "none";   // hide just the minimap; the kill feed below it stays
      return;
    }
    mc.style.display = "";
    const m = this.miniRadar;
    if (m.W <= 1 && mc.clientWidth > 1) { m.resize(); m.fit(); }   // heal a 0-size init (laid out now)
    const f = this._miniZoom || 1, c = (m.map.size || 1024) / 2;
    m.followIdx = this.radar.followIdx;        // highlight the spectated dot (its _updateFollow is no-op'd)
    m.zoom = m.fitZoom * f;
    if (f <= 1.001) {                          // whole map, centred
      m.camX = c; m.camY = c;
    } else if (this.radar.followIdx >= 0) {    // zoomed -> centre on the spectated player
      const p = this.curState.players[this.radar.followIdx];
      if (p) { m.camX = m.rxFromWorld(p.x); m.camY = m.ryFromWorld(p.y); } else { m.camX = c; m.camY = c; }
    } else {                                   // zoomed + free cam -> centre on where the 3D camera is (S = 0.06)
      const cam = this.view3d.camera;
      m.camX = m.rxFromWorld(cam.position.x / 0.06); m.camY = m.ryFromWorld(-cam.position.z / 0.06);
    }
    m.render(this.curState, this.demo);
  },

  // --- main loop ------------------------------------------------------------
  loop() {
    const step = (now) => {
      const dt = this.lastNow ? (now - this.lastNow) / 1000 : 0;
      this.lastNow = now;
      if (this.demo) {
        if (this.playing && !this.scrubbing) {
          this.t += dt * this.speed;
          if (this.t >= this.demo.duration) { this.t = this.demo.duration; this.togglePlay(); }
        }
        this.curState = this.demo.stateAt(this.t);
        if (this.view3d.active) { this.view3d.update(dt); this.view3d.render(this.curState); }
        else { this.radar.render(this.curState, this.demo); if (this.drawMode) this._redraw(); }
        this.updateScoreboard(this.curState);
        this._updateFpHud(this.curState);
        this.updateRoundStrip(this.curState.round);
        this.updateKillfeed();
        this._renderMinimap();
        // round-mode timeline: rebase the slider min/max + markers when t crosses into a new round
        if (this.tlMode === "round") {
          const rn = this.demo.roundAt(this.t);
          if (rn && rn.number !== this._tlRound) this.refreshTimeline();
        }
        if (!this.scrubbing) $("timeline").value = this.t;
        $("timeLabel").textContent = fmt(this.t);
        this.updateBombBanner();
      }
      if (!window.__freeze) requestAnimationFrame(step);   // __freeze: debug pause for screenshots
    };
    requestAnimationFrame(step);
  },

  resumeLoop() { window.__freeze = false; this.loop(); },

  // --- overlay --------------------------------------------------------------
  showOverlay(text, isError = false, pct = null) {
    const o = $("overlay"); o.classList.add("show");
    o.classList.toggle("error", isError);
    $("overlayText").textContent = text;
    $("overlayBar").style.width = pct == null ? "0%" : pct + "%";
    $("overlaySpin").style.display = isError ? "none" : "block";
  },
  setOverlay(text, pct, spin) {
    $("overlayText").textContent = text;
    if (pct != null) $("overlayBar").style.width = pct + "%";
  },
  hideOverlay() { $("overlay").classList.remove("show"); },
  // explicit exit from the overlay (X button / Esc): hide it and, if no replay is loaded behind it,
  // fall back to the dashboard so the user is never stranded on an error with no way out.
  closeOverlay() {
    this.hideOverlay();
    if (!this.demo) this.showDashboard();
  },
  // #19 site-wide, non-blocking upload/parse progress strip. state: uploading | parsing | done | failed | hide.
  // 'uploading' is determinate (opts.pct); 'parsing' is an honest indeterminate stage bar (the parser
  // emits no sub-percent, so we show stage + elapsed, not a fake countdown). done/failed auto-hide.
  _upStrip(state, opts = {}) {
    const el = $("upStrip"); if (!el) return;
    clearTimeout(this._upStripT);
    if (state === "hide") {
      el.hidden = true; document.body.classList.remove("upstrip-on");
      this._jobDismissed = true; clearInterval(this._jobTick);   // user closed it -> stop the % ticker
      return;
    }
    const fill = $("upStripFill"), lab = $("upStripLabel"), x = $("upStripX");
    el.hidden = false; document.body.classList.add("upstrip-on");
    // parsing is determinate now (estimated %); only fall back to the indeterminate sweep if we have no %
    el.className = "upstrip up-" + state + ((state === "parsing" && opts.pct == null) ? " indet" : "");
    if ((state === "uploading" || state === "parsing" || state === "done") && opts.pct != null)
      fill.style.width = Math.max(2, Math.min(100, opts.pct)) + "%";
    if (lab) lab.textContent = (opts.label || "")
      + (state === "parsing" && _activeUploads === 0 ? " · Parsing continues if you leave" : "");
    if (x) x.onclick = () => this._upStrip("hide");
    if (opts.autohide) this._upStripT = setTimeout(() => this._upStrip("hide"), opts.autohide);
  },
};

App.init();
window.App = App;   // expose for debugging/inspection

// One-time essential-cookies notice (no trackers, so it's informational). Shown until acknowledged.
(function cookieNotice() {
  const el = document.getElementById("cookieNotice");
  if (!el) return;
  let acked = false;
  try { acked = localStorage.getItem("cs2dp_cookie_ack") === "1"; } catch (e) { /* private mode */ }
  if (acked) return;
  el.hidden = false;
  const ok = document.getElementById("cookieOk");
  if (ok) ok.onclick = () => { el.hidden = true; try { localStorage.setItem("cs2dp_cookie_ack", "1"); } catch (e) {} };
})();
function esc(s) { return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
