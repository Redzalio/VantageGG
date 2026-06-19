// radar2d.js -- top-down canvas renderer with pan/zoom/follow camera.
//
// Coordinate chain:
//   world (wx,wy)  -> radar pixel (rx,ry) on the 1024px source image:
//       rx = (wx - pos_x) / scale ;  ry = (pos_y - wy) / scale
//   radar pixel -> screen via camera {camX,camY (radar px at view center), zoom}:
//       sx = (rx - camX)*zoom + W/2 ;  sy = (ry - camY)*zoom + H/2

const CT = "#5b9bd5", T_ = "#d8a13a";

export class Radar2D {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.map = null;          // {pos_x,pos_y,scale,size,lower?}
    this.img = null;          // default Image
    this.imgLower = null;     // lower Image (or null)
    this.camX = 512; this.camY = 512; this.zoom = 1; this.fitZoom = 1;
    this.followIdx = -1;
    this.showNames = true;
    this.showTrajectories = true;
    this.showTraces = true;
    this.showUtil = true;
    this.dotSize = 1;
    this.heatmap = false;       // utility-throw heatmap overlay
    this.heatmapPts = null;     // [[wx,wy], ...] landing positions to heat-map
    this.posOverlay = null;     // #62b: {name, deaths:[[wx,wy]], kills:[[wx,wy]]} -- a player's death/kill spots
    this._recentDmg = new Set();
    this._camTarget = null;   // for smooth follow
    this._hoverNade = null;   // searchOverlay throw currently under the cursor (util mode)
    this.dpr = Math.min(2, window.devicePixelRatio || 1);
  }

  setMap(map, img, imgLower) {
    this.map = map; this.img = img; this.imgLower = imgLower || null;
    this.resize();
    this.fit();
  }

  resize() {
    const r = this.canvas.getBoundingClientRect();
    this.canvas.width = Math.max(1, Math.floor(r.width * this.dpr));
    this.canvas.height = Math.max(1, Math.floor(r.height * this.dpr));
    this.W = this.canvas.width; this.H = this.canvas.height;
  }

  fit() {
    if (!this.map) return;
    const s = this.map.size || 1024;
    this.fitZoom = Math.min(this.W, this.H) / s * 0.96;
    this.zoom = this.fitZoom;
    this.camX = s / 2; this.camY = s / 2;
  }

  // --- transforms -----------------------------------------------------------
  rxFromWorld(wx) { return (wx - this.map.pos_x) / this.map.scale; }
  ryFromWorld(wy) { return (this.map.pos_y - wy) / this.map.scale; }
  screenX(rx) { return (rx - this.camX) * this.zoom + this.W / 2; }
  screenY(ry) { return (ry - this.camY) * this.zoom + this.H / 2; }
  s2rx(sx) { return (sx - this.W / 2) / this.zoom + this.camX; }
  s2ry(sy) { return (sy - this.H / 2) / this.zoom + this.camY; }

  worldToScreen(wx, wy) {
    return [this.screenX(this.rxFromWorld(wx)), this.screenY(this.ryFromWorld(wy))];
  }
  worldFromScreen(sxCss, syCss) {
    const rx = this.s2rx(sxCss * this.dpr), ry = this.s2ry(syCss * this.dpr);
    return [this.map.pos_x + rx * this.map.scale, this.map.pos_y - ry * this.map.scale];
  }
  cameraWorldCenter() {
    return [this.map.pos_x + this.camX * this.map.scale, this.map.pos_y - this.camY * this.map.scale];
  }

  // --- camera control -------------------------------------------------------
  pan(dxScreen, dyScreen) {
    this.followIdx = -1; this._camTarget = null;
    this.camX -= dxScreen * this.dpr / this.zoom;
    this.camY -= dyScreen * this.dpr / this.zoom;
  }

  zoomAt(factor, sxCss, syCss) {
    const sx = sxCss * this.dpr, sy = syCss * this.dpr;
    const rxBefore = this.s2rx(sx), ryBefore = this.s2ry(sy);
    this.zoom = clamp(this.zoom * factor, this.fitZoom * 0.4, this.fitZoom * 12);
    // keep cursor world-point fixed
    this.camX = rxBefore - (sx - this.W / 2) / this.zoom;
    this.camY = ryBefore - (sy - this.H / 2) / this.zoom;
  }

  follow(idx) { this.followIdx = idx; }
  freeCam() { this.followIdx = -1; this._camTarget = null; }

  _updateFollow(state) {
    if (this.followIdx < 0) return;
    const p = state.players[this.followIdx];
    if (!p) return;
    const tx = this.rxFromWorld(p.x), ty = this.ryFromWorld(p.y);
    // ease toward target
    this.camX += (tx - this.camX) * 0.18;
    this.camY += (ty - this.camY) * 0.18;
    if (this.zoom < this.fitZoom * 1.6) this.zoom += (this.fitZoom * 2.2 - this.zoom) * 0.06;
  }

  _pickLayer(state) {
    if (!this.map.lower || !this.imgLower) return this.img;
    const thr = this.map.lower.altitude_max;
    let ref = null;
    if (this.followIdx >= 0 && state.players[this.followIdx]) {
      ref = state.players[this.followIdx].z;
    } else {
      // majority of alive players
      let below = 0, total = 0;
      for (const p of state.players) {
        if (p && p.alive) { total++; if (p.z <= thr) below++; }
      }
      return below > total / 2 ? this.imgLower : this.img;
    }
    return ref <= thr ? this.imgLower : this.img;
  }

  // --- main render ----------------------------------------------------------
  render(state, demo) {
    const ctx = this.ctx;
    this._demoRef = demo; this._tNow = state.t;   // for per-player flash-duration lookup
    this._updateFollow(state);
    ctx.clearRect(0, 0, this.W, this.H);
    if (!this.map || !this.img) return;

    // map image
    const s = this.map.size || 1024;
    const x0 = this.screenX(0), y0 = this.screenY(0);
    const layer = this._pickLayer(state);
    ctx.imageSmoothingEnabled = true;
    ctx.globalAlpha = 1;
    ctx.drawImage(layer, x0, y0, s * this.zoom, s * this.zoom);

    // utility-throw heatmap (toggle) -- density of where the filtered throws landed
    if (this.heatmap) this._drawHeatmap(ctx);

    // grenade area effects (smokes/molotovs/flashes), under players
    if (this.showUtil) for (const g of demo.activeNades(state.t)) this._drawNade(ctx, g);

    // grenade throw trajectories
    if (this.showTrajectories) for (const g of demo.trajectoriesAt(state.t)) this._drawTrajectory(ctx, g, state.t);

    // bullet/hit traces (attacker -> victim)
    if (this.showTraces) for (const d of demo.tracesAt(state.t)) this._drawTrace(ctx, d, state.t, state);

    // recent kill markers
    for (const k of demo.recentKills(state.t, 1.6)) {
      if (k.vx != null) this._drawKillMark(ctx, k, state.t - k.t);
    }

    if (state.bomb) this._drawBomb(ctx, state.bomb, state.t);
    const pb = demo.plantedBombAt(state.t);            // planted-bomb glow (red pulse / green defused)
    if (pb) this._drawPlantedBomb(ctx, pb, state.t);

    // damage-received FX set
    this._recentDmg = demo.recentDamage(state.t);

    for (let i = 0; i < state.players.length; i++) {
      const p = state.players[i];
      if (!p) continue;
      this._drawPlayer(ctx, p, i === this.followIdx);
    }

    // whole-demo utility search overlay (set by app) -- full throw arcs + landing markers.
    // Draw the hovered throw LAST so its highlight sits on top of the others.
    if (this.searchOverlay && this.searchOverlay.length) {
      const hov = this.searchOverlay.includes(this._hoverNade) ? this._hoverNade : null;
      for (const g of this.searchOverlay) if (g !== hov) this._drawSearchNade(ctx, g, false);
      if (hov) this._drawSearchNade(ctx, hov, true);
    }

    // #62b: a player's death/kill SPOTS for the whole match (set by app, persists across scrubbing)
    if (this.posOverlay) this._drawPosOverlay(ctx);
  }

  // Where a player won (green dots) and died (red x) across the whole match -- the visual
  // companion to the per-callout K-D table, so "I keep dying at X" is locatable on the map.
  _drawPosOverlay(ctx) {
    const o = this.posOverlay;
    const k = 4.5 * this.dpr;
    ctx.lineCap = "round";
    for (const [wx, wy] of (o.kills || [])) {          // kills first, deaths drawn on top
      const [sx, sy] = this.worldToScreen(wx, wy);
      ctx.beginPath(); ctx.arc(sx, sy, k, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(76,201,123,0.85)"; ctx.fill();
      ctx.lineWidth = 1.4 * this.dpr; ctx.strokeStyle = "rgba(8,12,16,0.85)"; ctx.stroke();
    }
    const d = 5 * this.dpr;
    ctx.lineWidth = 2.4 * this.dpr; ctx.strokeStyle = "#ff5555"; ctx.globalAlpha = 0.92;
    for (const [wx, wy] of (o.deaths || [])) {
      const [sx, sy] = this.worldToScreen(wx, wy);
      ctx.beginPath();
      ctx.moveTo(sx - d, sy - d); ctx.lineTo(sx + d, sy + d);
      ctx.moveTo(sx + d, sy - d); ctx.lineTo(sx - d, sy + d);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }

  _drawSearchNade(ctx, g, hot) {
    if (!g.pts || !g.pts.length) return;
    // colour by the thrower's SIDE at throw time (CT blue / T orange) so CT vs T throws read at a
    // glance; the type stays legible via the letter on the landing marker. Falls back to type colour.
    const col = ({ 3: CT, 2: T_ })[g._side]
      || { smoke: "#cfd3da", molotov: "#ff6a2b", flash: "#ffe27a", he: "#ff8a3d", decoy: "#99a3b0" }[g.type] || "#9aa";
    if (g.pts.length >= 2) {                     // the throw arc
      if (hot) {                                 // glow under the highlighted arc
        ctx.strokeStyle = "#fff"; ctx.lineWidth = 5 * this.dpr; ctx.globalAlpha = 0.35;
        ctx.beginPath();
        for (let i = 0; i < g.pts.length; i++) {
          const [sx, sy] = this.worldToScreen(g.pts[i][1], g.pts[i][2]);
          if (i) ctx.lineTo(sx, sy); else ctx.moveTo(sx, sy);
        }
        ctx.stroke();
      }
      ctx.strokeStyle = col; ctx.lineWidth = (hot ? 2.8 : 1.8) * this.dpr; ctx.globalAlpha = hot ? 1 : 0.5;
      ctx.beginPath();
      for (let i = 0; i < g.pts.length; i++) {
        const [sx, sy] = this.worldToScreen(g.pts[i][1], g.pts[i][2]);
        if (i) ctx.lineTo(sx, sy); else ctx.moveTo(sx, sy);
      }
      ctx.stroke(); ctx.globalAlpha = 1;
    }
    const lp = g.pts[g.pts.length - 1];          // landing marker
    const [lx, ly] = this.worldToScreen(lp[1], lp[2]);
    const r = (hot ? 8 : 6) * this.dpr;
    ctx.beginPath(); ctx.arc(lx, ly, r, 0, Math.PI * 2);
    ctx.fillStyle = col; ctx.globalAlpha = hot ? 1 : 0.95; ctx.fill(); ctx.globalAlpha = 1;
    ctx.lineWidth = (hot ? 2 : 1.5) * this.dpr; ctx.strokeStyle = hot ? "#fff" : "rgba(0,0,0,.8)"; ctx.stroke();
    ctx.fillStyle = "#11151b"; ctx.font = `bold ${(hot ? 9 : 8) * this.dpr}px monospace`;
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(g.type[0].toUpperCase(), lx, ly + 0.5);
    ctx.textBaseline = "alphabetic";
  }

  // Nearest searchOverlay throw to a screen point (CSS px), or null. Hit-tests the throw
  // polyline + landing marker; used for hover-highlight + click-to-jump in 2D utility mode.
  pickNade(sxCss, syCss) {
    if (!this.map || !this.searchOverlay || !this.searchOverlay.length) return null;
    const px = sxCss * this.dpr, py = syCss * this.dpr;
    const thresh = 9 * this.dpr;
    let best = null, bestD = thresh;
    for (const g of this.searchOverlay) {
      if (!g.pts || !g.pts.length || g.t0 == null) continue;   // demo throws only (skip library previews)
      const lp = g.pts[g.pts.length - 1];
      const [lx, ly] = this.worldToScreen(lp[1], lp[2]);
      let d = Math.hypot(px - lx, py - ly);                       // landing marker
      for (let i = 1; i < g.pts.length; i++) {                    // arc segments
        const [ax, ay] = this.worldToScreen(g.pts[i - 1][1], g.pts[i - 1][2]);
        const [bx, by] = this.worldToScreen(g.pts[i][1], g.pts[i][2]);
        d = Math.min(d, segDist(px, py, ax, ay, bx, by));
      }
      if (d < bestD) { bestD = d; best = g; }
    }
    return best;
  }

  _drawTrajectory(ctx, g, t) {
    const col = { smoke: "#cfd3da", flash: "#ffe27a", he: "#ff8a3d", molotov: "#ff6a2b", decoy: "#99a3b0" }[g.type] || "#99a3b0";
    ctx.strokeStyle = col; ctx.lineWidth = 2 * this.dpr; ctx.globalAlpha = 0.85;
    ctx.beginPath();
    let started = false, lx = 0, ly = 0;
    for (const p of g.pts) {
      if (p[0] > t) break;
      const [sx, sy] = this.worldToScreen(p[1], p[2]);
      if (!started) { ctx.moveTo(sx, sy); started = true; } else ctx.lineTo(sx, sy);
      lx = sx; ly = sy;
    }
    if (started) {
      ctx.stroke();
      ctx.globalAlpha = 1; ctx.fillStyle = col;
      ctx.beginPath(); ctx.arc(lx, ly, 3 * this.dpr, 0, 7); ctx.fill();   // current head
    }
    ctx.globalAlpha = 1;
  }

  _drawTrace(ctx, d, t, state) {
    const a = state.players[d.atk], v = state.players[d.vic];
    if (!a || !v) return;
    const [ax, ay] = this.worldToScreen(a.x, a.y);
    const [vx, vy] = this.worldToScreen(v.x, v.y);
    const al = Math.max(0, 1 - (t - d.t) / 0.3);
    ctx.globalAlpha = al * 0.55;
    ctx.strokeStyle = d.hg === "head" ? "#ff5b5b" : "#ffd45b";
    ctx.lineWidth = 1.4 * this.dpr;
    ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(vx, vy); ctx.stroke();
    ctx.globalAlpha = 1;
  }

  _r(scaleUnits) { return scaleUnits / this.map.scale * this.zoom; }
  _dotR() { return clamp(6 * Math.pow(this.zoom / this.fitZoom, 0.5), 4.5, 18) * this.dpr * this.dotSize; }

  _drawPlayer(ctx, p, followed) {
    const [sx, sy] = this.worldToScreen(p.x, p.y);
    const teamCol = p.team === 3 ? CT : T_;
    const col = p.color || teamCol;
    const r = this._dotR();

    if (!p.alive) {
      // small faded cross
      ctx.globalAlpha = 0.5;
      ctx.strokeStyle = teamCol; ctx.lineWidth = 2 * this.dpr;
      const d = r * 0.6;
      ctx.beginPath();
      ctx.moveTo(sx - d, sy - d); ctx.lineTo(sx + d, sy + d);
      ctx.moveTo(sx + d, sy - d); ctx.lineTo(sx - d, sy + d);
      ctx.stroke();
      ctx.globalAlpha = 1;
      return;
    }

    // view cone
    const yaw = p.yaw * Math.PI / 180;
    const dir = [Math.cos(yaw), -Math.sin(yaw)];
    const coneLen = r * 4.5, coneHalf = 0.5;
    const a0 = Math.atan2(dir[1], dir[0]) - coneHalf;
    const a1 = Math.atan2(dir[1], dir[0]) + coneHalf;
    const grad = ctx.createRadialGradient(sx, sy, r, sx, sy, r + coneLen);
    grad.addColorStop(0, hexA(col, 0.45));
    grad.addColorStop(1, hexA(col, 0));
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.moveTo(sx, sy);
    ctx.arc(sx, sy, r + coneLen, a0, a1);
    ctx.closePath();
    ctx.fill();

    // flashed (p.flash = seconds of blindness remaining): subtle glow + a crossed-eye icon
    if (p.flash > 0.5) {
      ctx.globalAlpha = Math.min(0.5, p.flash / 3.5);
      ctx.fillStyle = "#ffffff";
      ctx.beginPath(); ctx.arc(sx, sy, r * 1.6, 0, 7); ctx.fill();
      ctx.globalAlpha = 1;
      const peak = (this._demoRef && this._demoRef.flashPeakAt(p.idx, this._tNow)) || 5;
      this._drawFlashIcon(ctx, sx + r + 8 * this.dpr, sy - r, r * 0.75, Math.min(1, p.flash / peak));
    }
    // damage-received FX
    if (this._recentDmg && this._recentDmg.has(p.idx)) {
      ctx.beginPath(); ctx.arc(sx, sy, r + 4 * this.dpr, 0, Math.PI * 2);
      ctx.strokeStyle = "#ff4d4d"; ctx.lineWidth = 2.5 * this.dpr;
      ctx.globalAlpha = 0.9; ctx.stroke(); ctx.globalAlpha = 1;
    }

    // dot
    ctx.beginPath();
    ctx.arc(sx, sy, r, 0, Math.PI * 2);
    ctx.fillStyle = col;
    ctx.fill();
    ctx.lineWidth = (followed ? 3 : 1.5) * this.dpr;
    ctx.strokeStyle = followed ? "#fff" : "rgba(0,0,0,.7)";
    ctx.stroke();

    // jersey number centred in the dot (CT 1-5 / T 6-10) -- only when the dot is big enough to read,
    // so zoomed-out overviews don't turn into a wall of digits (#12). Dark glyph reads on both palettes.
    if (p.num && r >= 7 * this.dpr) {
      ctx.fillStyle = "rgba(8,11,15,0.92)";
      ctx.font = `bold ${(r * 1.15).toFixed(0)}px Inter, system-ui, sans-serif`;
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(String(p.num), sx, sy + 0.5 * this.dpr);
    }

    // low-hp ring
    if (p.hp < 100) {
      ctx.beginPath();
      ctx.arc(sx, sy, r + 2.5 * this.dpr, -Math.PI / 2,
        -Math.PI / 2 + Math.PI * 2 * (p.hp / 100));
      ctx.strokeStyle = p.hp > 40 ? "#7dd87d" : "#e25555";
      ctx.lineWidth = 2 * this.dpr;
      ctx.stroke();
    }

    // name
    if (this.showNames) {
      ctx.font = `${11 * this.dpr}px Inter, system-ui, sans-serif`;
      ctx.textAlign = "center";
      ctx.lineWidth = 3 * this.dpr;
      ctx.strokeStyle = "rgba(0,0,0,.85)";
      ctx.strokeText(p.name, sx, sy - r - 4 * this.dpr);
      ctx.fillStyle = followed ? "#fff" : "#e8e8e8";
      ctx.fillText(p.name, sx, sy - r - 4 * this.dpr);
    }
  }

  _drawBomb(ctx, bomb, t) {
    const [sx, sy] = this.worldToScreen(bomb.x, bomb.y);
    const planted = bomb.state === "planted";
    const r = 6 * this.dpr;
    if (planted) {
      const pulse = 0.5 + 0.5 * Math.sin(t * 8);
      ctx.globalAlpha = 0.4 + 0.4 * pulse;
      ctx.fillStyle = "#ff3b30";
      ctx.beginPath(); ctx.arc(sx, sy, r * (1.4 + pulse), 0, 7); ctx.fill();
      ctx.globalAlpha = 1;
    }
    ctx.fillStyle = planted ? "#ff3b30" : "#d8a13a";
    ctx.fillRect(sx - r, sy - r, r * 2, r * 2);
    ctx.fillStyle = "#000";
    ctx.font = `bold ${8 * this.dpr}px monospace`;
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText("C4", sx, sy + 1);
    ctx.textBaseline = "alphabetic";
  }

  // planted bomb on the map: pulsing red glow while ticking, solid green once defused
  _drawPlantedBomb(ctx, pb, t) {
    const [sx, sy] = this.worldToScreen(pb.x, pb.y);
    const r = 7 * this.dpr;
    if (pb.state === "defused") {
      const g = ctx.createRadialGradient(sx, sy, 0, sx, sy, r * 2.6);
      g.addColorStop(0, "rgba(60,220,120,0.55)"); g.addColorStop(1, "rgba(60,220,120,0)");
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(sx, sy, r * 2.6, 0, 7); ctx.fill();
      ctx.fillStyle = "#3cdc78"; ctx.beginPath(); ctx.arc(sx, sy, r * 0.75, 0, 7); ctx.fill();
    } else {
      const pulse = 0.5 + 0.5 * Math.sin(performance.now() * 0.006);   // wall-clock -> pulses even when paused
      const R = r * (2.0 + pulse * 1.5);
      const g = ctx.createRadialGradient(sx, sy, 0, sx, sy, R);
      g.addColorStop(0, `rgba(255,60,50,${(0.35 + 0.4 * pulse).toFixed(2)})`); g.addColorStop(1, "rgba(255,40,30,0)");
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(sx, sy, R, 0, 7); ctx.fill();
      ctx.fillStyle = "#ff3b30"; ctx.beginPath(); ctx.arc(sx, sy, r * 0.75, 0, 7); ctx.fill();
    }
  }

  _drawNade(ctx, g) {
    const [sx, sy] = this.worldToScreen(g.x, g.y);
    if (g.type === "smoke") {
      const grow = Math.min(1, g.age / 1.2);
      const rad = this._r(150) * grow;
      const fade = g.age > g.life - 2 ? Math.max(0, (g.life - g.age) / 2) : 1;
      ctx.globalAlpha = 0.55 * fade;
      ctx.fillStyle = "#d8d8de";
      ctx.beginPath(); ctx.arc(sx, sy, rad, 0, 7); ctx.fill();
      ctx.globalAlpha = 1;
      // remaining-duration countdown in the smoke
      const remain = Math.max(0, g.life - g.age);
      if (remain > 0 && rad > 11 * this.dpr) {
        const fs = Math.max(9 * this.dpr, Math.min(15 * this.dpr, rad * 0.4));
        ctx.fillStyle = "rgba(18,22,28,.92)";
        ctx.font = `bold ${fs}px Inter, system-ui, sans-serif`;
        ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.fillText(remain.toFixed(1) + "s", sx, sy);
        ctx.textBaseline = "alphabetic";
      }
    } else if (g.type === "molotov") {
      const rad = this._r(165);                 // fire coverage area
      const flick = 0.5 + 0.18 * Math.sin(g.age * 13);
      ctx.globalAlpha = 0.4 + flick * 0.22;
      const grad = ctx.createRadialGradient(sx, sy, rad * 0.15, sx, sy, rad);
      grad.addColorStop(0, "#ff8a3d");
      grad.addColorStop(0.6, "rgba(255,80,0,.45)");
      grad.addColorStop(1, "rgba(170,40,0,.05)");
      ctx.fillStyle = grad;
      ctx.beginPath(); ctx.arc(sx, sy, rad, 0, 7); ctx.fill();
      // dashed border outlines the covered area
      ctx.globalAlpha = 0.6; ctx.strokeStyle = "#ff6a2b"; ctx.lineWidth = 1.5 * this.dpr;
      ctx.setLineDash([4 * this.dpr, 3 * this.dpr]);
      ctx.beginPath(); ctx.arc(sx, sy, rad, 0, 7); ctx.stroke();
      ctx.setLineDash([]); ctx.globalAlpha = 1;
    } else if (g.type === "flash") {
      const k = 1 - g.age / 0.7;
      ctx.globalAlpha = k;
      ctx.strokeStyle = "#fff"; ctx.lineWidth = 3 * this.dpr;
      ctx.beginPath(); ctx.arc(sx, sy, this._r(120) * (1 - k), 0, 7); ctx.stroke();
      ctx.globalAlpha = 1;
    } else if (g.type === "he") {
      const k = 1 - g.age / 0.7;
      ctx.globalAlpha = k;
      ctx.fillStyle = "#ff8a3d";
      ctx.beginPath(); ctx.arc(sx, sy, this._r(90) * (1 - k * 0.5), 0, 7); ctx.fill();
      ctx.globalAlpha = 1;
    }
  }

  _drawHeatmap(ctx) {
    const pts = this.heatmapPts;
    if (!pts || !pts.length) return;
    const rad = Math.max(16 * this.dpr, this._r(170));
    ctx.save();
    ctx.globalCompositeOperation = "lighter";   // overlapping throws accumulate -> hotspots
    for (const [wx, wy] of pts) {
      const [sx, sy] = this.worldToScreen(wx, wy);
      const g = ctx.createRadialGradient(sx, sy, 0, sx, sy, rad);
      g.addColorStop(0, "rgba(255,160,40,0.40)");
      g.addColorStop(0.5, "rgba(255,90,20,0.16)");
      g.addColorStop(1, "rgba(255,60,0,0)");
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(sx, sy, rad, 0, 7); ctx.fill();
    }
    ctx.restore();
  }

  _drawFlashIcon(ctx, cx, cy, sz, frac = 0) {   // crossed-out eye = blind; wedge behind = time left
    const w = Math.max(6 * this.dpr, sz), h = w * 0.62;
    // duration backing: faint disc + a depleting wedge (from 12 o'clock) = flash time remaining
    if (frac > 0) {
      const R = w * 1.75;
      ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(0,0,0,0.30)"; ctx.fill();
      ctx.beginPath(); ctx.moveTo(cx, cy);
      ctx.arc(cx, cy, R, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * Math.min(1, frac));
      ctx.closePath();
      ctx.fillStyle = "rgba(255,212,91,0.42)"; ctx.fill();
    }
    ctx.lineCap = "round";
    ctx.lineWidth = Math.max(1.4 * this.dpr, w * 0.16);
    ctx.strokeStyle = "#ffd45b";
    ctx.beginPath(); ctx.ellipse(cx, cy, w, h, 0, 0, Math.PI * 2); ctx.stroke();
    ctx.beginPath(); ctx.arc(cx, cy, w * 0.3, 0, Math.PI * 2); ctx.fillStyle = "#ffd45b"; ctx.fill();
    ctx.strokeStyle = "#ff5b5b";
    ctx.beginPath(); ctx.moveTo(cx - w * 1.1, cy + h * 1.4); ctx.lineTo(cx + w * 1.1, cy - h * 1.4); ctx.stroke();
  }

  _drawKillMark(ctx, k, age) {
    const [sx, sy] = this.worldToScreen(k.vx, k.vy);
    ctx.globalAlpha = Math.max(0, 1 - age / 1.6);
    ctx.strokeStyle = "#ff5555"; ctx.lineWidth = 2.5 * this.dpr;
    const d = 6 * this.dpr;
    ctx.beginPath();
    ctx.moveTo(sx - d, sy - d); ctx.lineTo(sx + d, sy + d);
    ctx.moveTo(sx + d, sy - d); ctx.lineTo(sx - d, sy + d);
    ctx.stroke();
    ctx.globalAlpha = 1;
  }

  // hit-test for click-to-spectate (css coords)
  pick(sxCss, syCss, state) {
    const sx = sxCss * this.dpr, sy = syCss * this.dpr;
    let best = -1, bestD = (this._dotR() + 8 * this.dpr) ** 2;
    for (let i = 0; i < state.players.length; i++) {
      const p = state.players[i];
      if (!p || !p.alive) continue;
      const [px, py] = this.worldToScreen(p.x, p.y);
      const d = (px - sx) ** 2 + (py - sy) ** 2;
      if (d < bestD) { bestD = d; best = i; }
    }
    return best;
  }
}

function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
// shortest distance from point (px,py) to segment (ax,ay)->(bx,by)
function segDist(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay;
  const len2 = dx * dx + dy * dy;
  let t = len2 ? ((px - ax) * dx + (py - ay) * dy) / len2 : 0;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}
function hexA(col, a) {
  // accept #rrggbb or hsl(...) -> rgba-ish; for hsl we wrap with alpha via canvas trick
  if (col.startsWith("#")) {
    const n = parseInt(col.slice(1), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }
  // hsl(h, s%, l%) -> hsla(h, s%, l%, a)
  if (col.startsWith("hsl(")) return col.replace("hsl(", "hsla(").replace(")", `, ${a})`);
  return col;
}
