// admin_callouts.js -- self-contained admin Callout Editor.
//
// Public contract:
//   window.AdminCallouts = { open(container) }
//   open(container) renders the whole editor inside `container` (an empty <div>).
//   Safe to call repeatedly -- it clears + re-inits the container each time.
//
// No framework, no build step. Pure DOM + canvas 2D. Styles live in static/css/callouts.css.
//
// Coordinate math MUST match radar2d.js exactly so dots map to correct world coords:
//   world -> radar-pixel:  rx = (wx - pos_x)/scale ;  ry = (pos_y - wy)/scale     (0..size)
//   radar-pixel -> world:  wx = pos_x + rx*scale ;     wy = pos_y - ry*scale
//   canvas (display S px) <-> radar-pixel (size px):    cx = rx*(S/size) ; rx = cx*(size/S)
// Canvas is a fixed square; we render at devicePixelRatio for crispness but all hit-testing
// is done in CSS pixels.
//
// NOTE on image URLs: the API returns image paths relative to the `maps/` namespace
// (e.g. calibration.image = "de_mirage.png", ref_image = "maps/callout_ref/<map>.png"),
// but Flask serves them under the static mount. app.js itself loads radar images as
// `static/maps/<image>`. So we resolve everything through static/maps/ (see _mapsUrl).

(function () {
  "use strict";

  // ---- tiny helpers (no app globals) --------------------------------------
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function slug(s) {
    return String(s || "").toLowerCase().trim()
      .replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || "callout";
  }
  function el(tag, cls, html) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  async function getJSON(url) {
    var r = await fetch(url, { cache: "no-store", headers: { "Accept": "application/json" } });
    if (r.status === 403) { var e = new Error("forbidden"); e.code = 403; throw e; }
    if (!r.ok) { var e2 = new Error("HTTP " + r.status); e2.code = r.status; throw e2; }
    return r.json();
  }
  async function postJSON(url, body) {
    var r = await fetch(url, {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify(body || {})
    });
    if (r.status === 403) { var e = new Error("forbidden"); e.code = 403; throw e; }
    var data = null;
    try { data = await r.json(); } catch (_) { /* ignore */ }
    if (!r.ok) { var e2 = new Error((data && data.error) || ("HTTP " + r.status)); e2.code = r.status; throw e2; }
    return data || {};
  }

  // Resolve an API-supplied image path to the actual served URL under static/maps/.
  // The API gives e.g. "de_mirage.png" (calibration.image) or "maps/callout_ref/de_mirage.png"
  // (ref_image). Flask serves both beneath static/, so normalise to static/maps/<rest>.
  function mapsUrl(p) {
    if (!p) return "";
    var s = String(p).replace(/^\/+/, "");
    if (s.indexOf("static/") === 0) return s;          // already absolute-to-static
    if (s.indexOf("maps/") === 0) return "static/" + s; // ref_image style: maps/callout_ref/...
    return "static/maps/" + s;                          // bare filename: de_mirage.png
  }

  // side colors pulled from the live theme (fall back to radar2d's literals)
  function themeColor(varName, fallback) {
    try {
      var v = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
      return v || fallback;
    } catch (_) { return fallback; }
  }

  // =========================================================================
  // Editor instance
  // =========================================================================
  function Editor(container) {
    this.root = container;
    this.coverage = [];          // [{map,count,...}]
    this.curMap = null;          // map id string
    this.payload = null;         // /api/admin/callouts/<map> response
    this.callouts = [];          // working copy [{id,name,aliases[],side,world:{x,y},boundary,notes,sort_order,learned}]
    this.selId = null;           // selected callout id
    this.cal = null;             // calibration {image,pos_x,pos_y,scale,size}
    this.img = null;             // radar Image
    this.imgReady = false;
    this.S = 720;                // canvas CSS size (square)
    this.dpr = Math.min(2, window.devicePixelRatio || 1);
    this.mode = "move";          // "move" | "drawBoundary"
    this.drag = null;            // {kind:'center'|'vertex', id, idx}
    this.unmapped = [];          // unmapped_learned ghosts
    this.refImage = null;        // ref image path
    this.dirty = false;
    this.colCT = themeColor("--ct", "#5b9bd5");
    this.colT = themeColor("--t", "#d8a13a");
    this.colAcc = themeColor("--accent", "#e8743b") || "#e8743b";
    this._raf = 0;
  }

  Editor.prototype.mount = function () {
    var root = this.root;
    root.innerHTML = "";
    root.classList.add("cae-root");

    // --- toolbar ---
    var tb = el("div", "cae-toolbar");
    var grp = el("div", "cae-tb-group");
    grp.appendChild(el("span", "cae-label", "Map"));
    this.mapSel = el("select", "cae-select");
    this.mapSel.innerHTML = '<option value="">Select a map…</option>';
    grp.appendChild(this.mapSel);
    this.mapBadge = el("span", "");        // managed/seed badge holder
    grp.appendChild(this.mapBadge);
    tb.appendChild(grp);

    var spacer = el("div", "cae-grow");
    tb.appendChild(spacer);

    this.btnIngest = el("button", "cae-btn cae-sm", "Ingest demo samples");
    this.btnRevert = el("button", "cae-btn cae-sm cae-danger", "Revert to default");
    this.btnSave = el("button", "cae-btn cae-primary", "Save");
    this.btnIngest.disabled = this.btnRevert.disabled = this.btnSave.disabled = true;
    tb.appendChild(this.btnIngest);
    tb.appendChild(this.btnRevert);
    tb.appendChild(this.btnSave);
    root.appendChild(tb);

    this.status = el("div", "cae-status", "");
    root.appendChild(this.status);

    // --- coverage table ---
    this.cov = el("div", "cae-coverage");
    var ch = el("div", "cae-cov-head");
    ch.innerHTML = '<b>Coverage</b><span class="cae-side-sub cae-cov-toggle">hide</span>';
    this.covBody = el("div", "cae-cov-body");
    this.cov.appendChild(ch);
    this.cov.appendChild(this.covBody);
    root.appendChild(this.cov);
    var self = this;
    ch.addEventListener("click", function () {
      var hidden = self.covBody.style.display === "none";
      self.covBody.style.display = hidden ? "" : "none";
      ch.querySelector(".cae-cov-toggle").textContent = hidden ? "hide" : "show";
    });

    // --- main split ---
    var main = el("div", "cae-main");

    // left: canvas column
    var left = el("div", "cae-canvas-wrap");
    var ctools = el("div", "cae-canvas-tools");
    this.btnDraw = el("button", "cae-btn cae-sm", "Draw boundary");
    this.btnFinish = el("button", "cae-btn cae-sm", "Finish");
    this.btnClearB = el("button", "cae-btn cae-sm", "Clear boundary");
    this.btnRef = el("button", "cae-btn cae-sm", "Reference labels");
    this.btnDraw.disabled = this.btnFinish.disabled = this.btnClearB.disabled = true;
    ctools.appendChild(this.btnDraw);
    ctools.appendChild(this.btnFinish);
    ctools.appendChild(this.btnClearB);
    ctools.appendChild(this.btnRef);
    left.appendChild(ctools);

    this.canvasBox = el("div", "cae-canvas-box");
    this.canvas = el("canvas", "cae-canvas cae-mode-move");
    this.canvasEmpty = el("div", "cae-canvas-empty", "Pick a map to start editing callouts.");
    this.canvasBox.appendChild(this.canvas);
    this.canvasBox.appendChild(this.canvasEmpty);
    left.appendChild(this.canvasBox);

    this.hint = el("div", "cae-hint",
      "<b>Move mode:</b> drag a dot to set its world position; drag a boundary vertex to reshape. " +
      "Click a learned ghost to snap. &nbsp;<b>Draw mode:</b> click to add polygon points, double-click or Finish to end.");
    left.appendChild(this.hint);

    // reference image holder (separate, not aligned)
    this.refBox = el("div", "cae-ref");
    this.refBox.hidden = true;
    this.refBox.innerHTML = '<div class="cae-ref-head">Simple Radar reference (official names) — framing differs from the editor radar; for reading names only. Click to enlarge.</div>';
    this.refImgEl = el("img", "");
    this.refImgEl.alt = "callout reference";
    this.refBox.appendChild(this.refImgEl);
    left.appendChild(this.refBox);

    main.appendChild(left);

    // right: sidebar
    var side = el("div", "cae-side");
    var sh = el("div", "cae-side-head");
    sh.innerHTML = '<b>Callouts</b>';
    this.btnAdd = el("button", "cae-btn cae-sm", "+ Add callout");
    this.btnAdd.disabled = true;
    sh.appendChild(this.btnAdd);
    side.appendChild(sh);
    this.sideSub = el("div", "cae-side-sub", "");
    side.appendChild(this.sideSub);

    this.list = el("div", "cae-list");
    side.appendChild(this.list);

    // per-callout edit form
    this.edit = el("div", "cae-edit");
    this.edit.hidden = true;
    this.edit.innerHTML =
      '<label class="cae-field"><span>Name</span><input class="cae-input" data-f="name" type="text"></label>' +
      '<label class="cae-field"><span>Aliases (comma-separated)</span><input class="cae-input" data-f="aliases" type="text"></label>' +
      '<div class="cae-row2">' +
        '<label class="cae-field"><span>Side</span>' +
          '<select class="cae-select" data-f="side"><option value="both">both</option><option value="t">T</option><option value="ct">CT</option></select>' +
        '</label>' +
        '<label class="cae-field"><span>Sort order</span><input class="cae-input" data-f="sort_order" type="number" step="1"></label>' +
      '</div>' +
      '<label class="cae-field"><span>Notes</span><textarea class="cae-textarea" data-f="notes"></textarea></label>' +
      '<div class="cae-coords" data-f="coords">—</div>' +
      '<div class="cae-edit-actions">' +
        '<button class="cae-btn cae-sm" data-a="snap" hidden>Snap to learned</button>' +
        '<button class="cae-btn cae-sm cae-danger" data-a="del">Delete callout</button>' +
      '</div>';
    side.appendChild(this.edit);

    main.appendChild(side);
    root.appendChild(main);

    // learned + unmapped panels (below the split)
    this.learnPanel = el("div", "cae-learn");
    this.learnPanel.hidden = true;
    root.appendChild(this.learnPanel);

    // lightbox
    this.lightbox = el("div", "cae-lightbox");
    this.lightbox.innerHTML = '<div class="cae-lightbox-cap">Reference labels — click anywhere to close</div>';
    this.lbImg = el("img", "");
    this.lightbox.appendChild(this.lbImg);
    document.body.appendChild(this.lightbox);
    var lb = this.lightbox;
    this.lightbox.addEventListener("click", function () { lb.classList.remove("cae-show"); });

    this._bind();
    this._sizeCanvas();
  };

  Editor.prototype._sizeCanvas = function () {
    // CSS size comes from layout; pick the rendered width (square via aspect-ratio)
    var rect = this.canvasBox.getBoundingClientRect();
    var s = Math.round(rect.width || this.S);
    if (s < 80) s = this.S;        // not laid out yet -> fall back
    this.S = s;
    this.canvas.width = Math.round(s * this.dpr);
    this.canvas.height = Math.round(s * this.dpr);
    this.draw();
  };

  // ---- coordinate transforms (CSS px <-> world) ---------------------------
  Editor.prototype.worldToCanvas = function (wx, wy) {
    var c = this.cal, k = this.S / c.size;
    var rx = (wx - c.pos_x) / c.scale;
    var ry = (c.pos_y - wy) / c.scale;
    return [rx * k, ry * k];
  };
  Editor.prototype.canvasToWorld = function (cx, cy) {
    var c = this.cal, k = c.size / this.S;
    var rx = cx * k, ry = cy * k;
    return [c.pos_x + rx * c.scale, c.pos_y - ry * c.scale];
  };

  // ---- bindings -----------------------------------------------------------
  Editor.prototype._bind = function () {
    var self = this;

    this.mapSel.addEventListener("change", function () {
      if (this.value) self.loadMap(this.value);
    });

    this.btnSave.addEventListener("click", function () { self.save(); });
    this.btnRevert.addEventListener("click", function () { self.revert(); });
    this.btnIngest.addEventListener("click", function () { self.ingest(); });
    this.btnAdd.addEventListener("click", function () { self.addCallout(); });

    this.btnDraw.addEventListener("click", function () { self.setMode("drawBoundary"); });
    this.btnFinish.addEventListener("click", function () { self.setMode("move"); });
    this.btnClearB.addEventListener("click", function () {
      var c = self.cur(); if (!c) return;
      c.boundary = null; self.markDirty(); self.draw();
    });
    this.btnRef.addEventListener("click", function () {
      if (!self.refImage) return;
      self.lbImg.src = mapsUrl(self.refImage);
      self.lightbox.classList.add("cae-show");
    });

    // edit-form field changes
    this.edit.addEventListener("input", function (e) {
      var f = e.target.getAttribute && e.target.getAttribute("data-f");
      if (!f) return;
      var c = self.cur(); if (!c) return;
      if (f === "name") c.name = e.target.value;
      else if (f === "aliases") c.aliases = e.target.value.split(",").map(function (s) { return s.trim(); }).filter(Boolean);
      else if (f === "side") c.side = e.target.value;
      else if (f === "notes") c.notes = e.target.value;
      else if (f === "sort_order") c.sort_order = parseInt(e.target.value, 10) || 0;
      self.markDirty();
      if (f === "name") { self.renderList(); self.draw(); }
    });
    this.edit.addEventListener("click", function (e) {
      var a = e.target.getAttribute && e.target.getAttribute("data-a");
      if (a === "del") { self.deleteCallout(self.selId); }
      else if (a === "snap") { self.snapToLearned(self.selId); }
    });

    // canvas pointer interactions
    this.canvas.addEventListener("pointerdown", function (e) { self._onDown(e); });
    this.canvas.addEventListener("pointermove", function (e) { self._onMove(e); });
    this.canvas.addEventListener("pointerup", function (e) { self._onUp(e); });
    this.canvas.addEventListener("pointercancel", function (e) { self._onUp(e); });
    this.canvas.addEventListener("dblclick", function (e) {
      e.preventDefault();
      if (self.mode === "drawBoundary") self.setMode("move");
    });

    // resize -> re-fit canvas
    this._onResize = function () {
      clearTimeout(self._rzT);
      self._rzT = setTimeout(function () { self._sizeCanvas(); }, 120);
    };
    window.addEventListener("resize", this._onResize);

    // warn on unload if dirty (passive; no app coupling)
    this._onUnload = function (e) {
      if (self.dirty) { e.preventDefault(); e.returnValue = ""; }
    };
    window.addEventListener("beforeunload", this._onUnload);
  };

  Editor.prototype.destroy = function () {
    if (this._onResize) window.removeEventListener("resize", this._onResize);
    if (this._onUnload) window.removeEventListener("beforeunload", this._onUnload);
    if (this.lightbox && this.lightbox.parentNode) this.lightbox.parentNode.removeChild(this.lightbox);
  };

  // ---- status helper ------------------------------------------------------
  Editor.prototype.setStatus = function (msg, kind) {
    this.status.textContent = msg || "";
    this.status.className = "cae-status" + (kind === "ok" ? " cae-ok" : kind === "err" ? " cae-err" : "");
  };

  Editor.prototype.markDirty = function () {
    this.dirty = true;
    this.setStatus("Unsaved changes", "");
  };

  // ---- load coverage / maps ----------------------------------------------
  Editor.prototype.start = async function () {
    this.setStatus("Loading coverage…");
    try {
      var data = await getJSON("/api/callouts");
      this.coverage = (data && data.coverage) || [];
      this.renderCoverage();
      this.fillMapPicker();
      this.setStatus("");
    } catch (e) {
      if (e.code === 403) { this.show403(); return; }
      this.setStatus("Failed to load coverage: " + e.message, "err");
    }
  };

  Editor.prototype.show403 = function () {
    this.root.innerHTML = "";
    var box = el("div", "cae-403",
      "<b>Admin access required.</b><br>The callout editor is only available to admin accounts. " +
      "Sign in with an admin account to manage callouts.");
    this.root.appendChild(box);
  };

  Editor.prototype.fillMapPicker = function () {
    var maps = this.coverage.slice().sort(function (a, b) {
      return String(a.map).localeCompare(String(b.map));
    });
    var html = '<option value="">Select a map…</option>';
    for (var i = 0; i < maps.length; i++) {
      var m = maps[i];
      var tag = m.managed ? " ●" : "";
      html += '<option value="' + esc(m.map) + '">' + esc(m.map) + tag + "</option>";
    }
    this.mapSel.innerHTML = html;
    if (this.curMap) this.mapSel.value = this.curMap;
  };

  Editor.prototype.renderCoverage = function () {
    var rows = this.coverage.slice().sort(function (a, b) {
      return String(a.map).localeCompare(String(b.map));
    });
    var self = this;
    if (!rows.length) {
      this.covBody.innerHTML = '<div class="cae-empty">No maps reported.</div>';
      return;
    }
    var html = '<table class="cae-cov-table"><thead><tr>' +
      "<th>Map</th><th>Callouts</th><th>World</th><th>Boundary</th><th>Samples</th><th>State</th>" +
      "</tr></thead><tbody>";
    for (var i = 0; i < rows.length; i++) {
      var m = rows[i];
      var cur = m.map === this.curMap ? " cae-cur" : "";
      html += '<tr class="cae-cov-row' + cur + '" data-map="' + esc(m.map) + '">' +
        '<td class="cae-cov-map">' + esc(m.map) + "</td>" +
        "<td>" + (m.count | 0) + "</td>" +
        "<td>" + (m.with_world | 0) + "</td>" +
        "<td>" + (m.with_boundary | 0) + "</td>" +
        "<td>" + (m.samples | 0) + "</td>" +
        "<td>" + (m.managed ? '<span class="cae-badge cae-managed">managed</span>' : '<span class="cae-badge cae-seed">seed</span>') + "</td>" +
        "</tr>";
    }
    html += "</tbody></table>";
    this.covBody.innerHTML = html;
    var trs = this.covBody.querySelectorAll(".cae-cov-row");
    for (var j = 0; j < trs.length; j++) {
      trs[j].addEventListener("click", function () {
        var map = this.getAttribute("data-map");
        self.mapSel.value = map;
        self.loadMap(map);
      });
    }
  };

  // ---- load a single map --------------------------------------------------
  Editor.prototype.loadMap = async function (map) {
    this.curMap = map;
    this.selId = null;
    this.mode = "move";
    this.dirty = false;
    this.setStatus("Loading " + map + "…");
    try {
      var p = await getJSON("/api/admin/callouts/" + encodeURIComponent(map));
      this.payload = p;
      this.cal = p.calibration || null;
      this.refImage = p.ref_image || null;
      this.unmapped = (p.unmapped_learned || []).slice();
      // deep-ish copy of callouts into a working array
      this.callouts = (p.callouts || []).map(function (c) {
        return {
          id: c.id,
          name: c.name || c.id,
          aliases: (c.aliases || []).slice(),
          side: c.side || "both",
          world: c.world ? { x: c.world.x, y: c.world.y } : null,
          boundary: c.boundary ? c.boundary.map(function (pt) { return [pt[0], pt[1]]; }) : null,
          notes: c.notes || "",
          sort_order: c.sort_order != null ? c.sort_order : 0,
          learned: c.learned ? { x: c.learned.x, y: c.learned.y, n: c.learned.n } : null
        };
      });

      // enable controls
      this.btnSave.disabled = this.btnRevert.disabled = this.btnIngest.disabled = false;
      this.btnAdd.disabled = !this.cal;

      // reference toggle
      if (this.refImage) {
        this.btnRef.style.display = "";
        this.refImgEl.src = mapsUrl(this.refImage);
        this.refBox.hidden = false;
      } else {
        this.btnRef.style.display = "none";
        this.refBox.hidden = true;
      }

      // map badge
      this.mapBadge.innerHTML = p.managed
        ? '<span class="cae-badge cae-managed">managed</span>'
        : '<span class="cae-badge cae-seed">seed' + (p.seed_count != null ? " · " + p.seed_count : "") + "</span>";

      // load radar image (if calibrated)
      this.imgReady = false;
      this.img = null;
      if (this.cal && this.cal.image) {
        this._loadImage(this.cal.image);
        this.canvasEmpty.style.display = "none";
      } else {
        this.canvasEmpty.textContent =
          "No radar calibration for this map. You can still edit names, aliases, side and notes in the list, but canvas placement is disabled.";
        this.canvasEmpty.style.display = "";
      }

      this.renderCoverage();   // re-highlight current row
      this.fillMapPicker();
      this.renderList();
      this.renderLearned();
      this.selectCallout(this.callouts.length ? this.callouts[0].id : null);
      this.setMode("move");
      this._sizeCanvas();
      this.setStatus("Loaded " + map + " (" + this.callouts.length + " callouts)", "ok");
    } catch (e) {
      if (e.code === 403) { this.show403(); return; }
      this.setStatus("Failed to load " + map + ": " + e.message, "err");
    }
  };

  Editor.prototype._loadImage = function (src) {
    var self = this;
    var im = new Image();
    im.onload = function () {
      if (self.img !== im) return;       // a newer load superseded this one
      self.imgReady = true;
      self.draw();
    };
    im.onerror = function () {
      if (self.img !== im) return;
      self.imgReady = false;
      self.canvasEmpty.textContent = "Radar image failed to load: " + mapsUrl(src);
      self.canvasEmpty.style.display = "";
      self.draw();
    };
    this.img = im;
    im.src = mapsUrl(src);
  };

  // ---- callout helpers ----------------------------------------------------
  Editor.prototype.cur = function () {
    var id = this.selId;
    for (var i = 0; i < this.callouts.length; i++) if (this.callouts[i].id === id) return this.callouts[i];
    return null;
  };

  Editor.prototype.selectCallout = function (id) {
    this.selId = id;
    this.renderList();
    this.renderEdit();
    // boundary tools only valid in move mode + calibrated
    var has = !!this.cur() && !!this.cal;
    this.btnDraw.disabled = !has;
    this.btnClearB.disabled = !has || !(this.cur() && this.cur().boundary);
    this.draw();
  };

  Editor.prototype.addCallout = function () {
    if (!this.cal) return;
    // place at map center (world from canvas center)
    var w = this.canvasToWorld(this.S / 2, this.S / 2);
    var base = "new_callout", id = base, n = 1;
    while (this._idTaken(id)) { id = base + "_" + (++n); }
    var c = {
      id: id, name: "New callout", aliases: [], side: "both",
      world: { x: w[0], y: w[1] }, boundary: null, notes: "",
      sort_order: (this.callouts.length ? Math.max.apply(null, this.callouts.map(function (x) { return x.sort_order || 0; })) + 1 : 0),
      learned: null
    };
    this.callouts.push(c);
    this.markDirty();
    this.renderList();
    this.selectCallout(id);
    this.setStatus("Added — drag the dot to position it", "");
  };

  Editor.prototype._idTaken = function (id) {
    for (var i = 0; i < this.callouts.length; i++) if (this.callouts[i].id === id) return true;
    return false;
  };

  Editor.prototype.deleteCallout = function (id) {
    var c = null;
    for (var i = 0; i < this.callouts.length; i++) if (this.callouts[i].id === id) { c = this.callouts[i]; break; }
    if (!c) return;
    if (!window.confirm('Delete callout "' + c.name + '"? (Removed when you Save.)')) return;
    this.callouts = this.callouts.filter(function (x) { return x.id !== id; });
    this.markDirty();
    var next = this.callouts.length ? this.callouts[0].id : null;
    this.renderList();
    this.selectCallout(next);
  };

  Editor.prototype.snapToLearned = function (id) {
    var c = null;
    for (var i = 0; i < this.callouts.length; i++) if (this.callouts[i].id === id) { c = this.callouts[i]; break; }
    if (!c || !c.learned) return;
    c.world = { x: c.learned.x, y: c.learned.y };
    this.markDirty();
    this.renderEdit();
    this.draw();
    this.setStatus('Snapped "' + c.name + '" to learned centroid', "");
  };

  Editor.prototype.createFromUnmapped = function (zone) {
    if (!this.cal) return;
    var id = slug(zone.zone), base = id, n = 1;
    while (this._idTaken(id)) { id = base + "_" + (++n); }
    var c = {
      id: id, name: zone.zone, aliases: [zone.zone], side: "both",
      world: { x: zone.x, y: zone.y }, boundary: null, notes: "",
      sort_order: (this.callouts.length ? Math.max.apply(null, this.callouts.map(function (x) { return x.sort_order || 0; })) + 1 : 0),
      learned: { x: zone.x, y: zone.y, n: zone.n }
    };
    this.callouts.push(c);
    // remove from the unmapped list so it doesn't double up
    this.unmapped = this.unmapped.filter(function (z) { return z !== zone; });
    this.markDirty();
    this.renderList();
    this.renderLearned();
    this.selectCallout(id);
    this.setStatus('Created "' + zone.zone + '" from learned zone', "");
  };

  // ---- sidebar rendering --------------------------------------------------
  Editor.prototype.renderList = function () {
    var self = this;
    this.list.innerHTML = "";
    if (!this.callouts.length) {
      this.list.appendChild(el("div", "cae-empty", this.curMap ? "No callouts yet. Use “+ Add callout”." : "Pick a map."));
      this.sideSub.textContent = "";
      return;
    }
    this.sideSub.textContent = this.callouts.length + " callout" + (this.callouts.length === 1 ? "" : "s");
    var sorted = this.callouts.slice().sort(function (a, b) {
      return (a.sort_order || 0) - (b.sort_order || 0) || String(a.name).localeCompare(String(b.name));
    });
    for (var i = 0; i < sorted.length; i++) {
      (function (c) {
        var item = el("div", "cae-item" + (c.id === self.selId ? " cae-sel" : ""));
        var sideCls = c.side === "ct" ? "cae-s-ct" : c.side === "t" ? "cae-s-t" : "";
        var hasW = c.world && c.world.x != null;
        item.innerHTML =
          '<span class="cae-item-name">' + esc(c.name) + (hasW ? "" : ' <span class="cae-coords">(no pos)</span>') + "</span>" +
          (c.learned ? '<span class="cae-item-flag" title="has demo-learned centroid">L</span>' : "") +
          '<span class="cae-item-side ' + sideCls + '">' + esc(c.side || "both") + "</span>" +
          '<button class="cae-item-del" title="delete">×</button>';
        item.addEventListener("click", function (e) {
          if (e.target.classList.contains("cae-item-del")) { self.deleteCallout(c.id); return; }
          self.selectCallout(c.id);
        });
        self.list.appendChild(item);
      })(sorted[i]);
    }
  };

  Editor.prototype.renderEdit = function () {
    var c = this.cur();
    if (!c) { this.edit.hidden = true; return; }
    this.edit.hidden = false;
    this.edit.querySelector('[data-f="name"]').value = c.name || "";
    this.edit.querySelector('[data-f="aliases"]').value = (c.aliases || []).join(", ");
    this.edit.querySelector('[data-f="side"]').value = c.side || "both";
    this.edit.querySelector('[data-f="notes"]').value = c.notes || "";
    this.edit.querySelector('[data-f="sort_order"]').value = c.sort_order || 0;
    var coords = this.edit.querySelector('[data-f="coords"]');
    if (c.world && c.world.x != null) {
      coords.textContent = "world: " + Math.round(c.world.x) + ", " + Math.round(c.world.y) +
        (c.boundary ? "  ·  boundary: " + c.boundary.length + " pts" : "");
    } else {
      coords.textContent = this.cal ? "no position — drag on canvas" : "no position (map not calibrated)";
    }
    var snap = this.edit.querySelector('[data-a="snap"]');
    if (c.learned) { snap.hidden = false; snap.textContent = "Snap to learned (n=" + (c.learned.n | 0) + ")"; }
    else { snap.hidden = true; }
  };

  Editor.prototype.renderLearned = function () {
    var self = this;
    var withLearned = this.callouts.filter(function (c) { return c.learned; });
    if (!this.unmapped.length && !withLearned.length) { this.learnPanel.hidden = true; return; }
    this.learnPanel.hidden = false;
    this.learnPanel.innerHTML = "";

    if (this.unmapped.length) {
      this.learnPanel.appendChild(el("div", "cae-learn-h",
        "Unmapped learned zones (" + this.unmapped.length + ") — from demo data, no matching callout"));
      var wrap = el("div", "cae-ghosts");
      for (var i = 0; i < this.unmapped.length; i++) {
        (function (z) {
          var g = el("div", "cae-ghost");
          g.innerHTML = '<span class="cae-ghost-name">' + esc(z.zone) + "</span>" +
            '<span class="cae-ghost-n">n=' + (z.n | 0) + "</span>" +
            '<button class="cae-btn cae-sm" ' + (self.cal ? "" : "disabled") + '>+ create</button>';
          g.querySelector("button").addEventListener("click", function () { self.createFromUnmapped(z); });
          wrap.appendChild(g);
        })(this.unmapped[i]);
      }
      this.learnPanel.appendChild(wrap);
    }

    if (withLearned.length) {
      var h2 = el("div", "cae-learn-h", "Callouts with learned centroids (" + withLearned.length + ")");
      h2.style.marginTop = this.unmapped.length ? "12px" : "0";
      this.learnPanel.appendChild(h2);
      var wrap2 = el("div", "cae-ghosts");
      for (var j = 0; j < withLearned.length; j++) {
        (function (c) {
          var g = el("div", "cae-ghost");
          g.innerHTML = '<span class="cae-ghost-name">' + esc(c.name) + "</span>" +
            '<span class="cae-ghost-n">n=' + (c.learned.n | 0) + "</span>" +
            '<button class="cae-btn cae-sm">Snap</button>';
          g.querySelector("button").addEventListener("click", function () { self.selectCallout(c.id); self.snapToLearned(c.id); });
          wrap2.appendChild(g);
        })(withLearned[j]);
      }
      this.learnPanel.appendChild(wrap2);
    }
  };

  // ---- canvas drawing -----------------------------------------------------
  Editor.prototype.draw = function () {
    if (!this._raf) {
      var self = this;
      this._raf = requestAnimationFrame(function () { self._raf = 0; self._draw(); });
    }
  };

  Editor.prototype._draw = function () {
    var ctx = this.canvas.getContext("2d");
    var W = this.canvas.width, H = this.canvas.height;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, W, H);
    if (!this.cal) return;
    ctx.scale(this.dpr, this.dpr);     // draw in CSS px from here on

    // radar image
    if (this.imgReady && this.img) {
      ctx.imageSmoothingEnabled = true;
      ctx.drawImage(this.img, 0, 0, this.S, this.S);
    } else {
      ctx.fillStyle = "#05080c";
      ctx.fillRect(0, 0, this.S, this.S);
    }

    var self = this;

    // unmapped-learned ghosts (distinct: dashed cyan diamonds)
    for (var u = 0; u < this.unmapped.length; u++) {
      var z = this.unmapped[u];
      var pz = this.worldToCanvas(z.x, z.y);
      this._ghostMarker(ctx, pz[0], pz[1], this.colCT, z.zone + " (n=" + (z.n | 0) + ")", true);
    }

    // boundaries first (under dots)
    for (var i = 0; i < this.callouts.length; i++) {
      var c = this.callouts[i];
      if (!c.boundary || c.boundary.length < 2) continue;
      var sel = c.id === this.selId;
      ctx.beginPath();
      for (var k = 0; k < c.boundary.length; k++) {
        var p = this.worldToCanvas(c.boundary[k][0], c.boundary[k][1]);
        if (k) ctx.lineTo(p[0], p[1]); else ctx.moveTo(p[0], p[1]);
      }
      if (c.boundary.length >= 3) ctx.closePath();
      ctx.fillStyle = sel ? "rgba(232,116,59,0.14)" : "rgba(255,240,180,0.07)";
      if (c.boundary.length >= 3) ctx.fill();
      ctx.strokeStyle = sel ? this.colAcc : "rgba(255,240,180,0.5)";
      ctx.lineWidth = sel ? 2 : 1.2;
      ctx.stroke();
      // vertices (selected callout only, draggable)
      if (sel) {
        for (var v = 0; v < c.boundary.length; v++) {
          var vp = this.worldToCanvas(c.boundary[v][0], c.boundary[v][1]);
          ctx.beginPath(); ctx.arc(vp[0], vp[1], 4, 0, 7);
          ctx.fillStyle = "#fff"; ctx.fill();
          ctx.lineWidth = 1.5; ctx.strokeStyle = this.colAcc; ctx.stroke();
        }
      }
    }

    // learned centroid ghosts for mapped callouts (faint amber rings)
    for (var L = 0; L < this.callouts.length; L++) {
      var cc = this.callouts[L];
      if (!cc.learned) continue;
      var lp = this.worldToCanvas(cc.learned.x, cc.learned.y);
      ctx.beginPath(); ctx.arc(lp[0], lp[1], 7, 0, 7);
      ctx.strokeStyle = "rgba(255,225,120,0.85)"; ctx.lineWidth = 1.5;
      ctx.setLineDash([3, 3]); ctx.stroke(); ctx.setLineDash([]);
      ctx.beginPath(); ctx.arc(lp[0], lp[1], 1.6, 0, 7);
      ctx.fillStyle = "rgba(255,225,120,0.9)"; ctx.fill();
    }

    // callout centers + labels
    ctx.textAlign = "center"; ctx.textBaseline = "alphabetic";
    for (var j = 0; j < this.callouts.length; j++) {
      var co = this.callouts[j];
      if (!co.world || co.world.x == null) continue;
      var sp = this.worldToCanvas(co.world.x, co.world.y);
      var isSel = co.id === this.selId;
      var col = co.side === "ct" ? this.colCT : co.side === "t" ? this.colT : "#ffe7b0";
      var r = isSel ? 6 : 4.5;
      // selection ring
      if (isSel) {
        ctx.beginPath(); ctx.arc(sp[0], sp[1], r + 4, 0, 7);
        ctx.strokeStyle = this.colAcc; ctx.lineWidth = 2; ctx.stroke();
      }
      ctx.beginPath(); ctx.arc(sp[0], sp[1], r, 0, 7);
      ctx.fillStyle = col; ctx.fill();
      ctx.lineWidth = 1.4; ctx.strokeStyle = "rgba(0,0,0,0.75)"; ctx.stroke();
      // label
      ctx.font = (isSel ? "700 " : "600 ") + "11px Inter, system-ui, sans-serif";
      ctx.lineWidth = 3; ctx.strokeStyle = "rgba(0,0,0,0.85)";
      ctx.strokeText(co.name, sp[0], sp[1] - r - 4);
      ctx.fillStyle = isSel ? "#fff" : "rgba(255,245,210,0.96)";
      ctx.fillText(co.name, sp[0], sp[1] - r - 4);
    }

    // draw-mode hint overlay
    if (this.mode === "drawBoundary") {
      ctx.fillStyle = "rgba(232,116,59,0.92)";
      ctx.font = "700 12px Inter, system-ui, sans-serif";
      ctx.textAlign = "left";
      ctx.fillText("Drawing boundary — click to add points, double-click / Finish to end", 10, 20);
    }
  };

  Editor.prototype._ghostMarker = function (ctx, x, y, color, label, dashed) {
    ctx.save();
    ctx.translate(x, y);
    ctx.beginPath();
    ctx.moveTo(0, -6); ctx.lineTo(6, 0); ctx.lineTo(0, 6); ctx.lineTo(-6, 0); ctx.closePath();
    ctx.fillStyle = "rgba(91,155,213,0.18)";
    ctx.fill();
    ctx.strokeStyle = color; ctx.lineWidth = 1.4;
    if (dashed) ctx.setLineDash([3, 2]);
    ctx.stroke(); ctx.setLineDash([]);
    ctx.restore();
    ctx.font = "600 10px Inter, system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.lineWidth = 3; ctx.strokeStyle = "rgba(0,0,0,0.85)";
    ctx.strokeText(label, x, y + 16);
    ctx.fillStyle = "rgba(180,215,255,0.95)";
    ctx.fillText(label, x, y + 16);
  };

  // ---- canvas pointer logic ----------------------------------------------
  Editor.prototype._evtPos = function (e) {
    var rect = this.canvas.getBoundingClientRect();
    return [
      (e.clientX - rect.left) * (this.S / rect.width),
      (e.clientY - rect.top) * (this.S / rect.height)
    ];
  };

  // nearest hit (vertex of selected callout, then any callout center, then ghosts) within threshold
  Editor.prototype._hit = function (cx, cy) {
    var thr = 10;
    // selected callout vertices first (so reshaping wins over moving)
    var sel = this.cur();
    if (sel && sel.boundary) {
      for (var v = 0; v < sel.boundary.length; v++) {
        var vp = this.worldToCanvas(sel.boundary[v][0], sel.boundary[v][1]);
        if (Math.hypot(cx - vp[0], cy - vp[1]) <= thr) return { kind: "vertex", id: sel.id, idx: v };
      }
    }
    // callout centers
    var best = null, bd = thr;
    for (var i = 0; i < this.callouts.length; i++) {
      var c = this.callouts[i];
      if (!c.world || c.world.x == null) continue;
      var p = this.worldToCanvas(c.world.x, c.world.y);
      var d = Math.hypot(cx - p[0], cy - p[1]);
      if (d <= bd) { bd = d; best = { kind: "center", id: c.id }; }
    }
    if (best) return best;
    // learned centroid ghosts (snap)
    for (var L = 0; L < this.callouts.length; L++) {
      var cc = this.callouts[L];
      if (!cc.learned) continue;
      var lp = this.worldToCanvas(cc.learned.x, cc.learned.y);
      if (Math.hypot(cx - lp[0], cy - lp[1]) <= thr) return { kind: "learnedGhost", id: cc.id };
    }
    // unmapped ghosts (create)
    for (var u = 0; u < this.unmapped.length; u++) {
      var z = this.unmapped[u];
      var zp = this.worldToCanvas(z.x, z.y);
      if (Math.hypot(cx - zp[0], cy - zp[1]) <= thr) return { kind: "unmapped", zone: z };
    }
    return null;
  };

  Editor.prototype._onDown = function (e) {
    if (!this.cal) return;
    e.preventDefault();
    var pos = this._evtPos(e), cx = pos[0], cy = pos[1];

    if (this.mode === "drawBoundary") {
      var c = this.cur();
      if (!c) { this.setMode("move"); return; }
      if (!c.boundary) c.boundary = [];
      var w = this.canvasToWorld(cx, cy);
      c.boundary.push([w[0], w[1]]);
      this.btnClearB.disabled = false;
      this.markDirty();
      this.renderEdit();
      this.draw();
      return;
    }

    var hit = this._hit(cx, cy);
    if (!hit) return;
    if (hit.kind === "unmapped") { this.createFromUnmapped(hit.zone); return; }
    if (hit.kind === "learnedGhost") { this.selectCallout(hit.id); this.snapToLearned(hit.id); return; }

    // select + begin dragging
    if (this.selId !== hit.id) this.selectCallout(hit.id);
    this.drag = hit;
    try { this.canvas.setPointerCapture(e.pointerId); } catch (_) {}
  };

  Editor.prototype._onMove = function (e) {
    if (!this.cal) return;
    var pos = this._evtPos(e), cx = clamp(pos[0], 0, this.S), cy = clamp(pos[1], 0, this.S);

    if (!this.drag) {
      // cursor affordance in move mode
      if (this.mode === "move") {
        var hit = this._hit(pos[0], pos[1]);
        this.canvas.style.cursor = hit ? (hit.kind === "vertex" || hit.kind === "center" ? "grab" : "pointer") : "default";
      }
      return;
    }
    var c = null;
    for (var i = 0; i < this.callouts.length; i++) if (this.callouts[i].id === this.drag.id) { c = this.callouts[i]; break; }
    if (!c) { this.drag = null; return; }
    var w = this.canvasToWorld(cx, cy);
    if (this.drag.kind === "center") {
      c.world = { x: w[0], y: w[1] };
    } else if (this.drag.kind === "vertex" && c.boundary && c.boundary[this.drag.idx]) {
      c.boundary[this.drag.idx] = [w[0], w[1]];
    }
    this.canvas.style.cursor = "grabbing";
    this.markDirty();
    this.draw();
  };

  Editor.prototype._onUp = function (e) {
    if (this.drag) {
      this.drag = null;
      this.renderEdit();
      try { this.canvas.releasePointerCapture(e.pointerId); } catch (_) {}
      this.canvas.style.cursor = "grab";
    }
  };

  Editor.prototype.setMode = function (mode) {
    this.mode = mode;
    var draw = mode === "drawBoundary";
    this.btnDraw.classList.toggle("cae-on", draw);
    this.btnFinish.disabled = !draw;
    this.canvas.classList.toggle("cae-mode-move", !draw);
    this.canvas.style.cursor = draw ? "crosshair" : "default";
    var c = this.cur();
    this.btnClearB.disabled = !c || !c.boundary || !this.cal;
    this.draw();
  };

  // ---- server actions -----------------------------------------------------
  Editor.prototype._serialize = function () {
    return this.callouts.map(function (c) {
      return {
        id: c.id,
        name: c.name,
        aliases: c.aliases || [],
        side: c.side || "both",
        world: c.world ? { x: c.world.x, y: c.world.y } : null,
        boundary: c.boundary && c.boundary.length ? c.boundary.map(function (p) { return [p[0], p[1]]; }) : null,
        notes: c.notes || "",
        sort_order: c.sort_order || 0
      };
    });
  };

  Editor.prototype.save = async function () {
    if (!this.curMap) return;
    this.btnSave.disabled = true;
    this.setStatus("Saving…");
    try {
      var res = await postJSON("/api/admin/callouts/" + encodeURIComponent(this.curMap),
        { callouts: this._serialize() });
      this.dirty = false;
      this.setStatus("Saved " + (res && res.saved != null ? res.saved : this.callouts.length) + " callouts", "ok");
      // refresh coverage figures + managed badge
      try {
        var cov = await getJSON("/api/callouts");
        this.coverage = (cov && cov.coverage) || this.coverage;
        this.renderCoverage();
        this.fillMapPicker();
      } catch (_) {}
      this.mapBadge.innerHTML = '<span class="cae-badge cae-managed">managed</span>';
    } catch (e) {
      if (e.code === 403) { this.setStatus("Not authorized (admin only).", "err"); }
      else this.setStatus("Save failed: " + e.message, "err");
    } finally {
      this.btnSave.disabled = false;
    }
  };

  Editor.prototype.revert = async function () {
    if (!this.curMap) return;
    if (!window.confirm("Revert " + this.curMap + " to its built-in seed callouts? This discards all overrides for this map.")) return;
    this.setStatus("Reverting…");
    try {
      var res = await postJSON("/api/admin/callouts/" + encodeURIComponent(this.curMap) + "/revert", {});
      this.setStatus("Reverted (" + (res && res.removed != null ? res.removed : "?") + " overrides removed)", "ok");
      await this.loadMap(this.curMap);
    } catch (e) {
      if (e.code === 403) { this.setStatus("Not authorized (admin only).", "err"); }
      else this.setStatus("Revert failed: " + e.message, "err");
    }
  };

  Editor.prototype.ingest = async function () {
    if (!this.curMap) return;
    this.btnIngest.disabled = true;
    this.setStatus("Ingesting demo samples… (this can take a moment)");
    try {
      var res = await postJSON("/api/admin/callouts/" + encodeURIComponent(this.curMap) + "/ingest", {});
      var msg = "Ingested: scanned " + (res.scanned | 0) +
        ", with samples " + (res.with_samples | 0) +
        ", folded " + (res.folded | 0) +
        ", learned zones " + (res.learned_zones | 0);
      this.setStatus(msg, "ok");
      await this.loadMap(this.curMap);   // reload to pick up new learned/unmapped data
    } catch (e) {
      if (e.code === 403) { this.setStatus("Not authorized (admin only).", "err"); }
      else this.setStatus("Ingest failed: " + e.message, "err");
    } finally {
      this.btnIngest.disabled = false;
    }
  };

  // =========================================================================
  // Public API
  // =========================================================================
  var _current = null;
  window.AdminCallouts = {
    open: function (container) {
      if (!container) { console.warn("AdminCallouts.open: no container"); return; }
      // tear down a previous instance (safe to call repeatedly)
      if (_current) { try { _current.destroy(); } catch (_) {} _current = null; }
      var ed = new Editor(container);
      ed.mount();
      ed.start();
      _current = ed;
      return ed;
    }
  };
})();
