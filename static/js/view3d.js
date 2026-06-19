// view3d.js -- 3D fly-around view (Three.js), synced to the shared playback clock.
//
// Coordinate map: CS world (X east, Y north, Z up) -> three.js (Y up):
//   tx =  X * S ,  ty = (Z - zRef) * S ,  tz = -Y * S      (S = WORLD_SCALE)
// Ground plane is the radar image, placed at the map's world extent. Players are
// real 3D figures at their Z, so multi-level maps (Train/Nuke/Vertigo) read in 3D.
// Fly cam: click to capture mouse, WASD move, E up / Q (or Ctrl) down, Shift = fast.
// Space is NOT a fly key -- it stays global play/pause in both 2D and 3D.

import * as THREE from "three";
import { GLTFLoader } from "./vendor/GLTFLoader.js";
import { MeshoptDecoder } from "./vendor/meshopt_decoder.module.js";
// Accelerated raycasting: the map mesh is millions of triangles, and three's default raycast is
// O(tris) per ray (~0.5s on big maps) -> bullet-impact raycasts froze the view. A BVH makes each
// ray ~O(log tris). We build it lazily (first time impacts are used) on the loaded geo.
import { computeBoundsTree, disposeBoundsTree, acceleratedRaycast } from "three-mesh-bvh";
THREE.BufferGeometry.prototype.computeBoundsTree = computeBoundsTree;
THREE.BufferGeometry.prototype.disposeBoundsTree = disposeBoundsTree;
THREE.Mesh.prototype.raycast = acceleratedRaycast;
window.THREE = THREE;   // debug

const S = 0.06;                 // world-units -> three units
const INCH = 0.0254;            // metres per Source unit (VRF exports glTF in metres)
const PLAYER_H = 72, PLAYER_R = 16;
const VERTICAL_FIT_MAX_UNITS = 96;
const CT_COLOR = 0x4a90ff, T_COLOR = 0xff4040;   // 3D player colours by side
const MODEL_YAW = Math.PI / 2;   // base rotation so the extracted CS model's mesh-forward = the player's aim
const TRAIL_SEC = 4;            // seconds of movement drawn behind each player (trails toggle)
// 3D nade effects: active duration (s), world radius, colour.
const NADE_FX = {
  smoke:   { dur: 18,   rad: 144, color: 0xcfd3da },
  molotov: { dur: 7,    rad: 150, color: 0xff6a2b },
  flash:   { dur: 0.45, rad: 80,  color: 0xfff0a0 },
  he:      { dur: 0.5,  rad: 110, color: 0xff8a3d },
  decoy:   { dur: 0.5,  rad: 40,  color: 0x99a3b0 },
};

// An HE detonating inside a smoke briefly "parts" it -- a clear hole opens around the
// blast, holds, then heals as the smoke fills back in (CS2 mechanic, ~1s).
const SMOKE_PART_DUR = 0.95;    // seconds the hole stays open
const SMOKE_PART_RAD = 100;     // world-units clear radius around the HE detonation
const SMOKE_PART_REACH = 184;   // HE within this of a smoke centre parts it (rad 144 + edge margin)

// bullet-impact markers (sv_showimpacts style): raycast the map mesh from each shot's origin
// along its view angle; mark the hit, fading over IMPACT_DUR. Enabled per player via impactSet.
const IMPACT_DUR = 10;          // seconds an impact marker stays visible (fades 1 -> 0)
const IMPACT_RAD = 3;           // world-units marker radius
const IMPACT_COLOR = 0xffe066;  // yellow tracer-burn dot
const IMPACT_FAR = 8000;        // world-units max ray length when searching for the surface hit
// These caps predate the BVH (below), when each raycast hit the full map mesh (millions of tris,
// several ms each) and 40/frame froze the view. With the BVH every ray is ~O(log tris) and cheap,
// so the caps are now generous: the few shots actually ON SCREEN this frame must never "fill in"
// late (that read as "bullet impacts are delayed"). The time budget stays as a smoothness belt.
const IMPACT_RAYS_PER_FRAME = 400;   // hard cap on NEW raycasts/frame (belt)
const IMPACT_MS_BUDGET = 8;          // per-frame time budget for those raycasts (real governor)
// We also PRECOMPUTE all enabled players' shot impacts in the background (a chunk per frame, even
// while paused) -- by the time the clock reaches a shot its impact is already cached and appears in
// real time, with no "filling in" delay. Cheap-ray BVH lets us resolve a whole match in ~1s.
const IMPACT_PRECOMPUTE_PER_FRAME = 800;
const IMPACT_PRECOMPUTE_MS = 8;

// CS scope HORIZONTAL FOV in degrees, from CS's weapon scripts (default FOV is 90). The demo's
// per-frame `zoom` level (1 = first right-click, 2 = second) picks the exact level:
//   AWP            zoom1 40, zoom2 10
//   SSG08 / SCAR-20 / G3SG1   zoom1 40, zoom2 15
//   AUG / SG553    zoom1 45 (single level)
function scopeHFov(w, lvl) {
  if (!lvl) return 0;
  w = (w || "").toLowerCase();
  if (/\baug\b|sg ?553/.test(w)) return 45;                       // single zoom level
  if (/awp/.test(w)) return lvl >= 2 ? 10 : 40;
  if (/ssg|scout|scar-?20|g3sg1/.test(w)) return lvl >= 2 ? 15 : 40;
  return 0;   // not a scoped weapon
}

export class View3D {
  constructor(canvas) {
    this.canvas = canvas;
    this.active = false;
    this.zRef = 0;
    this.followIdx = -1;
    this.xray = false;   // see players through walls (radar style) when true
    this.showAim = true;       // per-player "laser" POV line from the head along their aim (toggle)
    this.showCone = true;      // per-player floor POV cone (toggle)
    this.trails = false;       // draw each living player's recent movement path
    this.showDeaths = false;   // mark every death this round (not just the last 3s)
    this.geoLoading = false;   // true while the map GLB is streaming/decoding
    this.calMode = false;      // calibration / debug overlay (spawns, axes, bounds)
    this.calibrated = false;   // true only when a VALIDATED transform placed the geometry
    this.anchors = null;       // real spawn/bomb anchors for the current map
    this._transforms = null;   // cached transforms.json
    this._calGroup = null;     // calibration helpers group
    this._cfg = null;          // selected transform config for the current map
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    this.renderer.setClearColor(0x05070a, 1);
    this.scene = new THREE.Scene();
    this.scene.fog = new THREE.Fog(0x05070a, 60, 600);
    this.camera = new THREE.PerspectiveCamera(75, 1, 0.1, 4000);
    this.camera.rotation.order = "YXZ";
    this.yaw = 0; this.pitch = -0.5;

    this.scene.add(new THREE.AmbientLight(0xffffff, 0.55));
    const dir = new THREE.DirectionalLight(0xffffff, 0.55);
    dir.position.set(0.5, 1, 0.3); this.scene.add(dir);
    const dir2 = new THREE.DirectionalLight(0xffffff, 0.2);
    dir2.position.set(-0.4, 0.5, -0.6); this.scene.add(dir2);

    this.ground = null;
    this.players = [];           // {group, body, head, cone, label}
    this.bomb = null;
    this._keys = {};
    this._tmpA = new THREE.Vector3();
    this._tmpB = new THREE.Vector3();
    this._up = new THREE.Vector3(0, 1, 0);
    this._down = new THREE.Vector3(0, -1, 0);
    this._raycaster = new THREE.Raycaster();
    this._geoZBias = 0;
    this._floorCache = new Map();
    this._fx = new THREE.Group(); this.scene.add(this._fx);   // nade arcs/volumes, kill marks
    this._pool = { sphere: [], disc: [], line: [], mark: [], smoke: [], impact: [] };
    // CONTRACT: the main agent's per-player UI mutates this set (player indices to show bullet
    // impacts for; "all" = every alive index). We only READ it. Empty => no impacts drawn.
    this.impactSet = new Set();
    this._impactQueue = null;         // background precompute queue of enabled shots
    this._impactSig = "";             // signature of impactSet -> rebuild queue when it changes
    this.camPreset = "free";          // free | follow | overhead
    this._lineup = null;              // persistent nade-library lineup drawn in 3D
    this._bindInput();
    this.canvas.addEventListener("contextmenu", (e) => e.preventDefault());   // no right-click menu over the 3D view
  }

  // ---- setup ---------------------------------------------------------------
  setMap(mapMeta, img) {
    if (this.ground) { this.scene.remove(this.ground); this.ground.geometry.dispose(); }
    const sizeW = (mapMeta.size || 1024) * mapMeta.scale;      // world units across
    const tex = new THREE.Texture(img);
    tex.colorSpace = THREE.SRGBColorSpace; tex.needsUpdate = true;
    const geo = new THREE.PlaneGeometry(sizeW * S, sizeW * S);
    const mat = new THREE.MeshBasicMaterial({ map: tex });
    const plane = new THREE.Mesh(geo, mat);
    plane.rotation.x = -Math.PI / 2;
    // world center of the radar image
    const cx = mapMeta.pos_x + sizeW / 2;
    const cy = mapMeta.pos_y - sizeW / 2;
    plane.position.set(cx * S, 0, -cy * S);
    // align texture: image row0 = top (world +Y / -tz). Flip so north stays north.
    plane.rotation.z = 0; plane.scale.y = -1;
    this.scene.add(plane);
    this.ground = plane;
    this.mapMeta = mapMeta;

    const grid = new THREE.GridHelper(sizeW * S, 24, 0x1b2530, 0x121821);
    grid.position.set(cx * S, 0.02, -cy * S);
    if (this._grid) this.scene.remove(this._grid);
    this.scene.add(grid); this._grid = grid;
  }

  // Load the real map mesh and place it with the VALIDATED per-map transform from
  // transforms.json (built + floor-checked against real CS2 spawns by
  // tools/build_map_geometry.py). If a map has no verified transform we DO NOT fake it:
  // keep the radar-image floor and set calibrated=false so the UI can say so. The old
  // "enclose the players" auto-scale is demoted to a post-load sanity check only.
  async loadGeo(mapName) {
    if (this._geo) { this.scene.remove(this._geo); this._disposeGroup(this._geo); this._geo = null; }
    this._teardownCal();
    this.hasGeo = false; this.calibrated = false; this._cfg = null; this._mapName = mapName;
    this._floorCache.clear();

    // real entity anchors (spawns/bomb sites) -- for calibration markers + diagnostics
    this.anchors = await fetch(`static/maps3d/${mapName}_anchors.json?t=${Date.now()}`)
      .then(r => r.ok ? r.json() : null).catch(() => null);

    const transforms = await this._getTransforms();
    const cfg = transforms[mapName];
    if (!cfg || !cfg.verified) {
      console.warn(`[view3d] "${mapName}" has no verified transform -- radar-floor fallback (UNCALIBRATED)`);
      this._showFloorFallback();
      if (this.calMode) this._buildCalibration();
      return;
    }

    this._cfg = cfg;   // map is calibratable (geometry may still be streaming in)
    if (!this._loader) {
      this._loader = new GLTFLoader();
      if (MeshoptDecoder) this._loader.setMeshoptDecoder(MeshoptDecoder);
    }
    const url = `static/maps3d/${cfg.glb || mapName + "_full.glb"}`;
    this.geoLoading = true;
    if (this.onGeoStatus) this.onGeoStatus("loading");
    this._loader.load(url, (gltf) => {
      const grp = gltf.scene;
      // dark blue-grey + flat shading so walls/floors/edges read (vs the old blown-out white)
      const mat = new THREE.MeshStandardMaterial({
        color: 0x3c4452, roughness: 1, metalness: 0, side: THREE.DoubleSide, flatShading: true });
      const strip = [];
      grp.traverse(o => { if (o.isMesh) o.material = mat; else if (o.isLight) strip.push(o); });
      strip.forEach(l => l.removeFromParent());   // drop the map's baked lights (huge intensity = blown-out white)
      grp.updateMatrixWorld(true);
      this._geoRaw = new THREE.Box3().setFromObject(grp);
      this._applyTransform(grp, cfg);            // explicit, validated transform
      this.scene.add(grp);
      this._geo = grp; this.hasGeo = true; this.calibrated = true; this._cfg = cfg;
      this._bvhDone = false;                             // rebuild the raycast BVH for this map (lazy)
      grp.updateMatrixWorld(true);
      this._fitGeoVerticalToDemo();
      grp.updateMatrixWorld(true);
      this._geoBox = new THREE.Box3().setFromObject(grp);
      if (this.ground) this.ground.visible = false;
      if (this._grid) this._grid.visible = false;
      this._diagEnclosure();                     // sanity check only (demoted heuristic)
      if (this.calMode) { this._buildCalibration(); this._logDiagnostics(); }
      this.geoLoading = false;
      if (this.onGeoStatus) this.onGeoStatus("ready");
    }, undefined, () => {
      console.warn(`[view3d] glb missing for "${mapName}" (${url}) -- radar-floor fallback`);
      this._showFloorFallback();
      if (this.calMode) this._buildCalibration();
      this.geoLoading = false;
      if (this.onGeoStatus) this.onGeoStatus("missing");
    });
  }

  // Apply the validated transform. The GLB is a VRF world export: glTF metres, Y-up, with
  // glb = (wx, wz, -wy) * unitScale. Our world->three mapping is the SAME permutation
  // (x, z, -y), so placing the GLB needs only a uniform scale S/unitScale + a vertical offset
  // to the demo floor (zRef), plus any explicit world rotation/translate from the config
  // (both zero for our maps; supported for completeness / future maps).
  _applyTransform(grp, cfg) {
    const unit = cfg.unitScale || INCH;
    const scale = S / unit;                      // 0.06/0.0254 = 2.3622 for VRF metres
    grp.scale.setScalar(scale);
    grp.rotation.y = (cfg.rotationDeg || 0) * Math.PI / 180;   // yaw about world up (three +Y)
    const tr = cfg.translate || [0, 0, 0];                      // world units (x,y,z)
    grp.position.set(tr[0] * S, -this.zRef * S + tr[2] * S, -tr[1] * S);
    this._geoScale = scale;
    this._geoZBias = 0;
  }

  async _getTransforms() {
    if (this._transforms) return this._transforms;
    this._transforms = await fetch(`static/maps3d/transforms.json?t=${Date.now()}`)
      .then(r => r.ok ? r.json() : {}).catch(() => ({}));
    return this._transforms;
  }

  _showFloorFallback() {
    if (this.ground) { this.ground.visible = true; this.ground.material.opacity = 1; this.ground.material.transparent = false; }
    if (this._grid) this._grid.visible = true;
  }

  // world (Source) point -> three-space -- identical to how players are placed in render()
  _toThree(wx, wy, wz) { return new THREE.Vector3(wx * S, (wz - this.zRef) * S, -wy * S); }

  _surfaceDeltaAt(v) {
    if (!this._geo) return null;
    this._tmpA.set(v.x, v.y + VERTICAL_FIT_MAX_UNITS * S, v.z);
    this._raycaster.set(this._tmpA, this._down);
    this._raycaster.near = 0;
    this._raycaster.far = VERTICAL_FIT_MAX_UNITS * 2 * S;
    const hits = this._raycaster.intersectObject(this._geo, true);
    let best = null, bestAbs = Infinity;
    for (const h of hits) {
      const delta = (h.point.y - v.y) / S;
      const ad = Math.abs(delta);
      if (ad <= VERTICAL_FIT_MAX_UNITS && ad < bestAbs) {
        best = delta;
        bestAbs = ad;
      }
    }
    return best;
  }

  _median(vals) {
    if (!vals.length) return null;
    const a = vals.slice().sort((x, y) => x - y);
    const m = Math.floor(a.length / 2);
    return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
  }

  _freezeWorldPlayers() {
    const out = [];
    if (!this.demo || !this.demo.frames || !this.demo.frames.length) return out;
    let target = this.demo.frames[0].t || 0;
    if (this.demo.rounds && this.demo.rounds.length) {
      const r = this.demo.rounds[0];
      target = (r.start_t + (r.freeze_end_t ?? r.start_t)) / 2;
    }
    let best = null, bd = Infinity;
    for (const f of this.demo.frames) {
      const d = Math.abs(f.t - target);
      if (d < bd) { bd = d; best = f; }
    }
    if (best) for (const p of best.players) if (p && p.alive) out.push(p);
    return out;
  }

  _fitGeoVerticalToDemo() {
    if (!this._geo || !this.demo) return;
    const deltas = [];
    for (const p of this._freezeWorldPlayers()) {
      const d = this._surfaceDeltaAt(this._toThree(p.x, p.y, p.z));
      if (d != null) deltas.push(d);
    }
    if (deltas.length < 4) return;
    const med = this._median(deltas);
    if (med == null || Math.abs(med) < 0.5 || Math.abs(med) > VERTICAL_FIT_MAX_UNITS) return;
    this._geo.position.y -= med * S;
    this._geoZBias = -med;
    this._floorCache.clear();
  }

  _displayYForPlayer(p) {
    const base = (p.z - this.zRef) * S;
    if (!this.hasGeo || !this.calibrated || !p.alive) return base;
    const key = `${Math.round(p.x / 16)},${Math.round(p.y / 16)},${Math.round(p.z / 8)}`;
    let d = this._floorCache.get(key);
    if (d === undefined) {
      d = this._surfaceDeltaAt(this._toThree(p.x, p.y, p.z));
      this._floorCache.set(key, d);
      if (this._floorCache.size > 4000) this._floorCache.clear();
    }
    return d != null && Math.abs(d) <= 14 ? base + d * S + 0.02 : base;
  }

  // Player extent in three-space (same transform render() uses). DIAGNOSTIC ONLY now.
  _playerBounds3() {
    if (!this.demo) return null;
    const f = this.demo.frames;
    const mn = { x: Infinity, y: Infinity, z: Infinity };
    const mx = { x: -Infinity, y: -Infinity, z: -Infinity };
    for (let i = 0; i < f.length; i += 3) for (const p of f[i].players) if (p && p.alive) {
      const tx = p.x * S, ty = (p.z - this.zRef) * S, tz = -p.y * S;
      mn.x = Math.min(mn.x, tx); mn.y = Math.min(mn.y, ty); mn.z = Math.min(mn.z, tz);
      mx.x = Math.max(mx.x, tx); mx.y = Math.max(mx.y, ty); mx.z = Math.max(mx.z, tz);
    }
    return isFinite(mn.x) ? { min: mn, max: mx } : null;
  }

  // post-load sanity check: does the validated geometry actually contain the players?
  _diagEnclosure() {
    const pb = this._playerBounds3();
    if (!pb || !this._geoBox) return;
    const b = this._geoBox;
    this._geoEncloses = b.min.x <= pb.min.x + 1 && b.max.x >= pb.max.x - 1 &&
                        b.min.z <= pb.min.z + 1 && b.max.z >= pb.max.z - 1;
    if (!this._geoEncloses)
      console.warn("[view3d] SANITY: calibrated geometry does not enclose players -- verify transform", { geo: b, players: pb });
  }

  // ---- calibration / debug overlay ----------------------------------------
  setCalMode(on) {
    this.calMode = !!on;
    if (this.calMode) { this._buildCalibration(); this._logDiagnostics(); }
    else this._teardownCal();
  }

  _teardownCal() {
    if (this._calGroup) { this.scene.remove(this._calGroup); this._disposeGroup(this._calGroup); this._calGroup = null; }
    if (this.hasGeo) {   // restore floor hidden state when geometry is shown
      if (this.ground) { this.ground.visible = false; this.ground.material.opacity = 1; this.ground.material.transparent = false; }
      if (this._grid) this._grid.visible = false;
    }
  }

  _buildCalibration() {
    this._teardownCal();
    const g = new THREE.Group(); this._calGroup = g;
    if (this.ground) { this.ground.visible = true; this.ground.material.transparent = true; this.ground.material.opacity = 0.32; }
    if (this._grid) this._grid.visible = true;
    // CS world axes at origin (~600u): +X red, +Y green, +Z blue (up)
    g.add(this._axis(this._toThree(600, 0, 0), 0xff4040, "+X"));
    g.add(this._axis(this._toThree(0, 600, 0), 0x46d246, "+Y"));
    g.add(this._axis(this._toThree(0, 0, 600), 0x4a90ff, "+Z"));
    // bounds boxes: GLB (cyan) + players (magenta)
    if (this._geoBox) g.add(new THREE.Box3Helper(this._geoBox, 0x39d0d8));
    const pb = this._playerBounds3();
    if (pb) g.add(new THREE.Box3Helper(new THREE.Box3(
      new THREE.Vector3(pb.min.x, pb.min.y, pb.min.z),
      new THREE.Vector3(pb.max.x, pb.max.y, pb.max.z)), 0xff5bd0));
    // real spawn markers (rings): CT blue, T orange -- players at round start should sit in these
    if (this.anchors) {
      for (const s of (this.anchors.ct_spawns || [])) g.add(this._spawnRing(s, 0x5b9bd5));
      for (const s of (this.anchors.t_spawns || [])) g.add(this._spawnRing(s, 0xd8a13a));
      const cm = this._meanWorld(this.anchors.ct_spawns), tm = this._meanWorld(this.anchors.t_spawns);
      if (cm) g.add(this._label("CT spawn", "#7fb6ff", this._toThree(cm[0], cm[1], cm[2] + 90)));
      if (tm) g.add(this._label("T spawn", "#ffc266", this._toThree(tm[0], tm[1], tm[2] + 90)));
    }
    // demo freeze-time player positions (white dots) -- should land inside the spawn rings
    for (const v of this._freezePositions()) g.add(this._freezeMarker(v));
    this.scene.add(g);
  }

  _axis(end, color, label) {
    const grp = new THREE.Group();
    const geo = new THREE.BufferGeometry().setFromPoints([this._toThree(0, 0, 0), end]);
    grp.add(new THREE.Line(geo, new THREE.LineBasicMaterial({ color, depthTest: false })));
    grp.add(this._label(label, "#" + color.toString(16).padStart(6, "0"), end));
    return grp;
  }

  _spawnRing([wx, wy, wz], color) {
    const ring = new THREE.Mesh(
      new THREE.RingGeometry(9 * S, 15 * S, 22),
      new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide, transparent: true, opacity: 0.9, depthTest: false }));
    ring.rotation.x = -Math.PI / 2;
    const v = this._toThree(wx, wy, wz);
    ring.position.set(v.x, v.y + 0.15, v.z);
    ring.renderOrder = 26;
    return ring;
  }

  _freezeMarker(v) {
    const m = new THREE.Mesh(
      new THREE.SphereGeometry(6 * S, 8, 6),
      new THREE.MeshBasicMaterial({ color: 0xffffff, depthTest: false }));
    m.position.copy(v); m.renderOrder = 27;
    return m;
  }

  _freezePositions() {
    const out = [];
    if (!this.demo || !this.demo.rounds || !this.demo.rounds.length) return out;
    const r = this.demo.rounds[0];
    const ft = (r.start_t + (r.freeze_end_t ?? r.start_t)) / 2;
    let best = null, bd = Infinity;
    for (const f of this.demo.frames) { const d = Math.abs(f.t - ft); if (d < bd) { bd = d; best = f; } }
    if (best) for (const p of best.players) if (p && p.alive) out.push(this._toThree(p.x, p.y, p.z));
    return out;
  }

  _label(text, color, pos) {
    const c = document.createElement("canvas"); c.width = 160; c.height = 64;
    const ctx = c.getContext("2d");
    ctx.fillStyle = "rgba(8,11,15,0.72)"; ctx.fillRect(0, 0, 160, 64);
    ctx.font = "bold 30px Inter, system-ui, sans-serif";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillStyle = color; ctx.fillText(text, 80, 34);
    const spr = new THREE.Sprite(new THREE.SpriteMaterial({
      map: new THREE.CanvasTexture(c), depthTest: false, transparent: true }));
    spr.position.copy(pos); spr.scale.set(6.4, 2.56, 1); spr.renderOrder = 30;
    return spr;
  }

  _meanWorld(pts) {
    if (!pts || !pts.length) return null;
    const s = [0, 0, 0];
    for (const p of pts) { s[0] += p[0]; s[1] += p[1]; s[2] += p[2]; }
    return [s[0] / pts.length, s[1] / pts.length, s[2] / pts.length];
  }

  _radarWorldBounds() {
    const m = this.mapMeta; if (!m) return null;
    const w = (m.size || 1024) * m.scale;
    return { minX: m.pos_x, maxX: m.pos_x + w, minY: m.pos_y - w, maxY: m.pos_y };
  }

  _logDiagnostics() {
    console.group(`[view3d] calibration -- ${this._mapName}`);
    console.log("selected transform:", this._cfg || "(none -- radar-floor fallback, UNCALIBRATED)");
    console.log("calibrated:", this.calibrated, "geoScale:", this._geoScale, "zRef:", this.zRef, "demo vertical bias:", this._geoZBias);
    console.log("GLB raw bounds (glb/metres):", this._geoRaw && this._fmtBox(this._geoRaw));
    console.log("GLB transformed bounds (three):", this._geoBox && this._fmtBox(this._geoBox));
    console.log("player bounds (three):", this._playerBounds3());
    console.log("radar world bounds (XY):", this._radarWorldBounds());
    console.log("spawn world bounds:", this.anchors && this.anchors.spawn_bounds);
    console.log("enclosure sanity (expect true):", this._geoEncloses);
    console.groupEnd();
  }

  _fmtBox(b) { return { min: [+b.min.x.toFixed(2), +b.min.y.toFixed(2), +b.min.z.toFixed(2)], max: [+b.max.x.toFixed(2), +b.max.y.toFixed(2), +b.max.z.toFixed(2)] }; }

  _disposeGroup(grp) {
    grp.traverse(o => {
      if (o.geometry) o.geometry.dispose();
      const m = o.material; if (m) (Array.isArray(m) ? m : [m]).forEach(x => { if (x.map) x.map.dispose(); x.dispose(); });
    });
  }

  // ---- 3D effects: nade arcs + smoke/molly/flash/HE volumes, kill marks -----
  // pooled so render() stays allocation-free; unused objects are hidden each frame.
  _grab(kind, make) {
    const i = this._pi[kind]++;
    let o = this._pool[kind][i];
    if (!o) { o = make(); this._pool[kind].push(o); this._fx.add(o); }
    o.visible = true; return o;
  }
  _fxSphere() {
    return this._grab("sphere", () => new THREE.Mesh(new THREE.SphereGeometry(1, 16, 12),
      new THREE.MeshBasicMaterial({ transparent: true, depthWrite: false })));
  }
  // small marker for a bullet impact. depthTest stays ON (it sits on a real surface; we nudge it
  // out along the hit normal to avoid z-fighting), unlike the always-visible coaching volumes.
  _fxImpact() {
    return this._grab("impact", () => new THREE.Mesh(new THREE.SphereGeometry(1, 8, 6),
      new THREE.MeshBasicMaterial({ color: IMPACT_COLOR, transparent: true, depthWrite: false })));
  }
  // Compute (once) + cache the world impact point of a shot by raycasting the map mesh from its
  // origin along its view angle. Caches on the shot: a THREE.Vector3 hit, or null if no surface.
  // Returns true iff a NEW raycast was performed (so the caller can cap rays/frame).
  // Build a BVH on the loaded map meshes once (lazy: only when impacts are first used). The build
  // is a one-time ~1-2s cost on big maps; afterwards every map raycast (impacts, kill marks, floor
  // fit) is ~O(log tris) instead of O(tris). Reset on map load so a new map rebuilds.
  _ensureBVH() {
    if (this._bvhDone || !this._geo) return;
    this._bvhDone = true;
    this._geo.traverse(o => {
      if (o.isMesh && o.geometry && !o.geometry.boundsTree && o.geometry.attributes.position) {
        try { o.geometry.computeBoundsTree(); } catch (e) { /* leave un-accelerated on failure */ }
      }
    });
  }
  _computeImpact(shot) {
    if (shot._imp !== undefined) return false;          // already resolved (hit Vector3 or null)
    if (!this._geo) return false;                        // geometry not streamed in yet -> retry later
    const o = this._tmpA.set(shot.ox * S, (shot.oz - this.zRef) * S, -shot.oy * S);
    const yaw = shot.yaw * Math.PI / 180, pitch = shot.pitch * Math.PI / 180;
    const cp = Math.cos(pitch);                           // Source +pitch = looking DOWN (matches aim laser)
    const dir = this._tmpB.set(cp * Math.cos(yaw), -Math.sin(pitch), -cp * Math.sin(yaw)).normalize();
    this._raycaster.set(o, dir);
    this._raycaster.near = 0; this._raycaster.far = IMPACT_FAR * S;
    const hits = this._raycaster.intersectObject(this._geo, true);
    shot._imp = hits.length ? hits[0].point.clone() : null;
    // world-space surface normal (face normal is in mesh-local space -> apply the hit object's
    // normal matrix) so the marker can be nudged off the surface regardless of geo rotation/scale.
    shot._impN = null;
    if (hits.length && hits[0].face) {
      const h = hits[0];
      const nm = new THREE.Matrix3().getNormalMatrix(h.object.matrixWorld);
      shot._impN = h.face.normal.clone().applyMatrix3(nm).normalize();
    }
    return true;
  }
  // Eagerly resolve all enabled players' shot impacts in the background (a chunk per frame, even
  // while paused) so they're cached BEFORE the clock reaches them -> impacts appear in real time
  // instead of trickling in. Rebuilds the queue whenever the enabled-player set changes.
  _pumpImpactPrecompute() {
    if (!this._geo || !this.impactSet.size || !this.demo) { this._impactSig = ""; this._impactQueue = null; return; }
    this._ensureBVH();
    const sig = Array.from(this.impactSet).sort((a, b) => a - b).join(",");
    if (sig !== this._impactSig) {                       // selection changed -> (re)build the work list
      this._impactSig = sig;
      this._impactQueue = (this.demo.events || []).filter(
        e => e.type === "shot" && this.impactSet.has(e.player) && e._imp === undefined);
    }
    const q = this._impactQueue;
    if (!q || !q.length) return;
    const start = performance.now();
    let n = 0;
    while (q.length && n < IMPACT_PRECOMPUTE_PER_FRAME && performance.now() - start < IMPACT_PRECOMPUTE_MS) {
      const shot = q.pop();
      if (shot._imp === undefined) { this._computeImpact(shot); n++; }
    }
  }
  // smoke dome with a carve-able hole: fragments within uHole (xyz world centre, w radius)
  // fade to nothing, so an HE blast opens a clean window through the smoke.
  _makeSmokeMat() {
    return new THREE.ShaderMaterial({
      // depthTest ON: smokes are occluded by walls (you shouldn't see a smoke through geometry),
      // matching how it looks in-game. depthWrite stays off (translucent, no self-occlusion).
      transparent: true, depthWrite: false, depthTest: true,
      uniforms: {
        uColor: { value: new THREE.Color(NADE_FX.smoke.color) },
        uOpacity: { value: 0.32 },
        uHole: { value: new THREE.Vector4(0, 0, 0, -1) },   // w < 0 => no hole
      },
      vertexShader: `
        varying vec3 vWorld;
        void main() {
          vec4 wp = modelMatrix * vec4(position, 1.0);
          vWorld = wp.xyz;
          gl_Position = projectionMatrix * viewMatrix * wp;
        }`,
      fragmentShader: `
        precision mediump float;
        uniform vec3 uColor; uniform float uOpacity; uniform vec4 uHole;
        varying vec3 vWorld;
        void main() {
          float a = uOpacity;
          vec3 col = uColor;
          if (uHole.w > 0.0) {
            float d = distance(vWorld, uHole.xyz);
            float clear = smoothstep(uHole.w * 0.5, uHole.w, d);    // 0 inside the hole -> full outside
            a *= clear;
            // thin hot ring at the carve edge -- blast heat pushing the smoke open
            float ring = smoothstep(uHole.w * 0.80, uHole.w, d) * (1.0 - smoothstep(uHole.w, uHole.w * 1.12, d));
            col = mix(col, vec3(1.0, 0.60, 0.28), ring * 0.75);
            a = max(a, ring * 0.45 * uOpacity);                     // keep the ring visible across the carve
          }
          if (a < 0.004) discard;
          gl_FragColor = vec4(col, a);
        }`,
    });
  }
  _smokeSphere() {
    return this._grab("smoke", () => new THREE.Mesh(
      new THREE.SphereGeometry(1, 24, 16), this._makeSmokeMat()));
  }
  // Precompute, per smoke event, the HE detonations that fall inside it (window + range) and
  // therefore part it. Mutates the smoke events with `_parts`; recomputed when the demo changes.
  _computeSmokeParts() {
    const evs = (this.demo && this.demo.events) || [];
    const hes = evs.filter(e => e.type === "he" && e.x != null);
    const reach2 = SMOKE_PART_REACH * SMOKE_PART_REACH;
    for (const s of evs) {
      if (s.type !== "smoke" || s.x == null) continue;
      const end = s.end_t || (s.t + NADE_FX.smoke.dur);
      const parts = [];
      for (const h of hes) {
        if (h.t < s.t || h.t > end) continue;
        const dx = h.x - s.x, dy = h.y - s.y;
        if (dx * dx + dy * dy > reach2) continue;
        if (s.z != null && h.z != null && Math.abs(h.z - s.z) > 160) continue;   // different floor
        parts.push({ t: h.t, x: h.x, y: h.y, z: h.z });
      }
      if (parts.length) s._parts = parts; else if (s._parts) delete s._parts;
    }
  }
  _fxDisc() {
    return this._grab("disc", () => {
      const g = new THREE.CircleGeometry(1, 30); g.rotateX(-Math.PI / 2);
      return new THREE.Mesh(g, new THREE.MeshBasicMaterial({ transparent: true, depthWrite: false, side: THREE.DoubleSide }));
    });
  }
  _fxLine() {
    return this._grab("line", () => {
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(256 * 3), 3));
      return new THREE.Line(g, new THREE.LineBasicMaterial({ transparent: true }));
    });
  }

  // world floor height at (wx,wy) by raycasting the geo (for kill marks); fallback zRef.
  _floorZAt(wx, wy) {
    if (!this._geo) return this.zRef;
    this._tmpA.set(wx * S, (3000 - this.zRef) * S, -wy * S);
    this._raycaster.set(this._tmpA, this._down); this._raycaster.near = 0; this._raycaster.far = 6000 * S;
    const hits = this._raycaster.intersectObject(this._geo, true);
    return hits.length ? hits[0].point.y / S + this.zRef : this.zRef;
  }

  _setArc(line, pts, color, upTo) {
    const attr = line.geometry.attributes.position;
    let n = 0;
    for (const p of pts) {
      if (upTo != null && p[0] > upTo) break;
      if (n * 3 + 2 >= attr.array.length) break;
      const v = this._toThree(p[1], p[2], p.length > 3 ? p[3] : 0);
      attr.array[n * 3] = v.x; attr.array[n * 3 + 1] = v.y; attr.array[n * 3 + 2] = v.z; n++;
    }
    if (n < 2) { line.visible = false; return; }
    attr.needsUpdate = true;
    line.geometry.setDrawRange(0, n);
    line.geometry.computeBoundingSphere();
    line.material.color.set(color);
  }

  // set a pooled line to a single A->B segment
  _setSeg(line, ax, ay, az, bx, by, bz) {
    const attr = line.geometry.attributes.position;
    attr.array[0] = ax; attr.array[1] = ay; attr.array[2] = az;
    attr.array[3] = bx; attr.array[4] = by; attr.array[5] = bz;
    attr.needsUpdate = true;
    line.geometry.setDrawRange(0, 2);
    line.geometry.computeBoundingSphere();
    return line;
  }

  // victim's real death height (from the demo state) -- memoized; fixes kill marks indoors
  _killZ(k) {
    if (k._z === undefined) {
      let z = this.zRef;
      try { const vp = this.demo.stateAt(k.t).players[k.victim]; if (vp) z = vp.z; } catch { /* keep zRef */ }
      k._z = z;
    }
    return k._z;
  }

  renderFX(state) {
    if (!this.demo) return;
    this._pi = { sphere: 0, disc: 0, line: 0, mark: 0, smoke: 0, impact: 0 };
    const t = state.t;
    if (this._partsFor !== this.demo) { this._computeSmokeParts(); this._partsFor = this.demo; }

    // active utility from DETONATE EVENTS (accurate detonation x,y,z -- the trajectory's last
    // flight point can be mid-air because the parser trims pts to the throw flight only).
    for (const n of this.demo.activeNades(t)) {
      const fx = NADE_FX[n.type]; if (!fx || n.x == null) continue;
      const age = n.age, life = n.life || fx.dur;
      const pos = this._toThree(n.x, n.y, n.z != null ? n.z : 0);
      if (n.type === "molotov") {
        const d = this._fxDisc();
        const flick = 0.45 + 0.18 * Math.sin(t * 13 + n.x);
        const fade = age > life - 1 ? Math.max(0, life - age) : 1;
        d.position.set(pos.x, pos.y + 0.1, pos.z);
        d.scale.setScalar(fx.rad * S * Math.min(1, age / 0.5));
        d.material.color.setHex(fx.color); d.material.opacity = Math.min(1, 0.6 * fade * flick * 2);
      } else if (n.type === "smoke") {
        const s = this._smokeSphere();
        const grow = Math.min(1, age / 1.0);
        const fade = age > life - 2 ? Math.max(0, (life - age) / 2) : 1;
        const cy = pos.y + fx.rad * S * 0.35;                      // detonation z ~ ground; dome upward
        s.position.set(pos.x, cy, pos.z);
        s.scale.setScalar(fx.rad * S * grow);
        const u = s.material.uniforms;
        u.uColor.value.setHex(fx.color); u.uOpacity.value = 0.5 * fade;   // denser -> reads vs the map
        // HE parting: open the strongest currently-active hole (rise fast, hold, heal)
        let hole = null, str = 0;
        if (n._parts) {
          for (const p of n._parts) {
            const dtp = t - p.t;
            if (dtp < 0 || dtp > SMOKE_PART_DUR) continue;
            const k = dtp < 0.1 ? dtp / 0.1
              : dtp > SMOKE_PART_DUR - 0.3 ? Math.max(0, (SMOKE_PART_DUR - dtp) / 0.3) : 1;
            if (k > str) { str = k; hole = p; }
          }
        }
        if (hole && str > 0.001) {
          const hp = this._toThree(hole.x, hole.y, hole.z != null ? hole.z : (n.z || 0));
          u.uHole.value.set(hp.x, cy, hp.z, SMOKE_PART_RAD * S * str);   // carve at HE x/z, dome mid-height
        } else {
          u.uHole.value.w = -1;
        }
      } else {
        const s = this._fxSphere();              // flash / he / decoy: quick pop
        const k = Math.max(0, 1 - age / fx.dur);
        s.position.set(pos.x, pos.y + fx.rad * S * 0.4, pos.z);
        s.scale.setScalar(fx.rad * S * (1 - k * 0.5));
        s.material.color.setHex(fx.color); s.material.opacity = k * (n.type === "flash" ? 0.65 : 0.45);
      }
    }

    // in-flight throw arcs (real 3D parabolas) + a bright moving head so it reads on screen
    for (const g of this.demo.trajectoriesAt(t)) {
      if (!g.pts || g.pts.length < 2) continue;
      const fx = NADE_FX[g.type] || NADE_FX.he;
      this._setArc(this._fxLine(), g.pts, fx.color, t + 0.05);
      let cur = g.pts[0];
      for (const p of g.pts) { if (p[0] > t) break; cur = p; }
      const head = this._fxSphere();
      const hv = this._toThree(cur[1], cur[2], cur.length > 3 ? cur[3] : 0);
      head.position.copy(hv); head.scale.setScalar(6 * S);
      head.material.color.setHex(fx.color); head.material.opacity = 1;
    }

    // bullet/hit traces -- attacker -> victim for very recent damage
    for (const d of this.demo.tracesAt(t, 0.25)) {
      const a = state.players[d.atk], v = state.players[d.vic];
      if (!a || !v || !a.alive) continue;
      const ap = this._toThree(a.x, a.y, a.z), vp = this._toThree(v.x, v.y, v.z);
      const ln = this._setSeg(this._fxLine(),
        ap.x, ap.y + PLAYER_H * S * 0.6, ap.z, vp.x, vp.y + PLAYER_H * S * 0.6, vp.z);
      ln.material.color.setHex(d.hg === "head" ? 0xff5b5b : 0xffd45b);
      ln.material.opacity = Math.max(0, 1 - (t - d.t) / 0.25) * 0.6;
    }

    // kill markers (X at the victim's real death height). showDeaths -> every death this round
    // held at steady opacity; otherwise just the last 3s, fading out.
    let killWin = 3.0;
    if (this.showDeaths) { const r = this.demo.roundAt(t); killWin = r ? (t - (r.start_t || 0)) + 0.2 : 3.0; }
    for (const k of this.demo.recentKills(t, killWin)) {
      if (k.vx == null) continue;
      const v = this._toThree(k.vx, k.vy, this._killZ(k));
      const fade = this.showDeaths ? 0.7 : Math.max(0.15, 1 - (t - k.t) / 3.0);
      for (const ang of [Math.PI / 4, -Math.PI / 4]) {
        const ln = this._setSeg(this._fxLine(),
          v.x - Math.cos(ang) * 8 * S, v.y + 0.3, v.z - Math.sin(ang) * 8 * S,
          v.x + Math.cos(ang) * 8 * S, v.y + 0.3, v.z + Math.sin(ang) * 8 * S);
        ln.material.color.setHex(0xff4444); ln.material.opacity = fade;
      }
    }

    // player trails -- recent movement path per living player (team-coloured fading line)
    if (this.trails) {
      for (let i = 0; i < this.players.length; i++) {
        const p = state.players[i];
        if (!p || !p.alive) continue;
        const tr = this.demo.trail(i, t, TRAIL_SEC);
        if (tr.length < 2) continue;
        const line = this._fxLine();
        const attr = line.geometry.attributes.position;
        let n = 0;
        for (const wp of tr) {
          if (n * 3 + 2 >= attr.array.length) break;
          const v = this._toThree(wp[0], wp[1], wp[2]);
          attr.array[n * 3] = v.x; attr.array[n * 3 + 1] = v.y + 0.06; attr.array[n * 3 + 2] = v.z; n++;
        }
        attr.needsUpdate = true;
        line.geometry.setDrawRange(0, n);
        line.geometry.computeBoundingSphere();
        line.material.color.setHex(p.team === 3 ? CT_COLOR : T_COLOR);
        line.material.opacity = 0.45;
      }
    }

    // bullet impacts (sv_showimpacts style) for ENABLED players only. Raycast each in-window shot
    // ONCE (cached on the shot), then draw a marker fading over IMPACT_DUR. Needs the map mesh.
    this._pumpImpactPrecompute();   // resolve enabled shots ahead of playback so they show in real time
    if (this._geo && this.impactSet.size) {
      this._ensureBVH();                               // one-time: accelerate map raycasts (else ~0.5s/ray)
      let newRays = 0;
      const rayStart = performance.now();
      for (const idx of this.impactSet) {
        for (const shot of this.demo.shotsAt(idx, t, IMPACT_DUR)) {
          if (shot._imp === undefined) {                 // not yet raycast
            // defer to a later frame once we hit the count cap OR this frame's time budget (keeps fps smooth)
            if (newRays >= IMPACT_RAYS_PER_FRAME || performance.now() - rayStart > IMPACT_MS_BUDGET) continue;
            if (this._computeImpact(shot)) newRays++;
          }
          if (!shot._imp) continue;                      // no surface hit (or still deferred)
          const age = t - shot.t;
          if (age < 0 || age > IMPACT_DUR) continue;
          const m = this._fxImpact();
          m.position.copy(shot._imp);
          if (shot._impN) m.position.addScaledVector(shot._impN, IMPACT_RAD * S * 0.6);  // lift off surface
          m.scale.setScalar(IMPACT_RAD * S);
          m.material.opacity = 1 - age / IMPACT_DUR;
        }
      }
    }

    // hide unused pooled objects
    for (const kind of ["sphere", "disc", "line", "smoke", "impact"])
      for (let i = this._pi[kind]; i < this._pool[kind].length; i++) this._pool[kind][i].visible = false;
  }

  // ---- camera presets ------------------------------------------------------
  setCamPreset(name) { this.camPreset = name; }
  _applyCamPreset(state) {
    if (this.camPreset === "overhead") {
      // top-down tracking the spectated player, else the alive-players centroid
      let tx = 0, ty = 0, tz = 0, n = 0;
      const foc = this.followIdx >= 0 ? [state.players[this.followIdx]] :
        state.players.filter(p => p && p.alive);
      for (const p of foc) { if (!p || !p.alive) continue; tx += p.x * S; ty += (p.z - this.zRef) * S; tz += -p.y * S; n++; }
      if (!n) return false;
      tx /= n; ty /= n; tz /= n;
      this.camera.up.set(0, 1, 0);
      this._tmpA.set(tx, ty + 48, tz + 26);        // steep tactical angle (reads better than flat top-down)
      this.camera.position.lerp(this._tmpA, 0.15);
      this.camera.lookAt(tx, ty + 2, tz);
      return true;
    }
    if (this.camPreset === "utility") {
      // follow the in-flight grenade if any, else the newest active smoke/molly
      let tgt = null;
      for (const g of this.demo.trajectoriesAt(state.t)) {
        if (!g.pts || !g.pts.length) continue;
        let cur = g.pts[0];
        for (const p of g.pts) { if (p[0] > state.t) break; cur = p; }
        tgt = this._toThree(cur[1], cur[2], cur.length > 3 ? cur[3] : 0);
      }
      if (!tgt) {
        const na = this.demo.activeNades(state.t);
        if (na.length) { const n = na[na.length - 1]; tgt = this._toThree(n.x, n.y, n.z != null ? n.z : 0); }
      }
      if (!tgt) return false;
      this.camera.up.set(0, 1, 0);
      this._tmpA.set(tgt.x + 9, tgt.y + 11, tgt.z + 13);
      this.camera.position.lerp(this._tmpA, 0.1);
      this.camera.lookAt(tgt.x, tgt.y, tgt.z);
      return true;
    }
    if (this.camPreset === "death") {
      // cinematic cut to the most recent death spot
      const kills = this.demo.recentKills(state.t, 3.0);
      const k = kills.length ? kills[kills.length - 1] : null;
      if (!k || k.vx == null) return false;
      const v = this._toThree(k.vx, k.vy, this._killZ(k));
      this.camera.up.set(0, 1, 0);
      this._tmpA.set(v.x + 10, v.y + 9, v.z + 13);
      this.camera.position.lerp(this._tmpA, 0.14);
      this.camera.lookAt(v.x, v.y + 2, v.z);
      return true;
    }
    return false;
  }

  // ---- nade-library lineup shown in 3D -------------------------------------
  showLineup3D(n) {
    this.clearLineup3D();
    if (!n) return;
    const g = new THREE.Group(); this._lineup = g;
    const col = (NADE_FX[n.type] || NADE_FX.he).color;
    if (n.throw_pos && n.land_pos) {                  // arc from throw to landing
      const a = this._toThree(n.throw_pos[0], n.throw_pos[1], n.throw_pos[2] || 0);
      const b = this._toThree(n.land_pos[0], n.land_pos[1], n.land_pos[2] || 0);
      const mid = a.clone().add(b).multiplyScalar(0.5); mid.y += a.distanceTo(b) * 0.35;  // parabola apex
      const curve = new THREE.QuadraticBezierCurve3(a, mid, b);
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(curve.getPoints(24)),
        new THREE.LineBasicMaterial({ color: col, depthTest: false }));
      line.renderOrder = 28; g.add(line);
    }
    if (n.land_pos) {                                 // landing volume
      const fx = NADE_FX[n.type] || NADE_FX.he;
      const b = this._toThree(n.land_pos[0], n.land_pos[1], n.land_pos[2] || 0);
      const s = new THREE.Mesh(new THREE.SphereGeometry(fx.rad * S, 16, 12),
        new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: 0.3, depthWrite: false }));
      s.position.set(b.x, b.y + fx.rad * S * 0.5, b.z); g.add(s);
    }
    this.scene.add(g);
  }
  clearLineup3D() {
    if (this._lineup) { this.scene.remove(this._lineup); this._disposeGroup(this._lineup); this._lineup = null; }
  }
  // Stand the camera at a saved lineup's THROW spot, looking toward where it lands -- a "how do I
  // line this up" first-person view. Saved lineups store z=0 (captured from the 2D map), so the real
  // floor height comes from a geo raycast; falls back gracefully when a map mesh isn't loaded.
  enterLineupPov(n) {
    if (!n || !n.throw_pos || !n.land_pos) return false;
    this.active = true; this.canvas.style.display = "block";
    const [tx, ty] = n.throw_pos, [lx, ly] = n.land_pos;
    let fz = this._floorZAt(tx, ty); if (fz == null) fz = this._floorZAt(lx, ly); if (fz == null) fz = this.zRef;
    let lz = this._floorZAt(lx, ly); if (lz == null) lz = fz;
    this.camera.position.copy(this._toThree(tx, ty, fz + PLAYER_H * 0.85));   // standing eye height
    this.camera.lookAt(this._toThree(lx, ly, lz + 30));                       // aim at the landing
    const e = new THREE.Euler().setFromQuaternion(this.camera.quaternion, "YXZ");
    this.pitch = e.x; this.yaw = e.y;     // sync fly-control state so the user can look around from here
    this.camPreset = "free"; this.followIdx = -1;
    this.resize();
    return true;
  }

  // floating per-player nametag (canvas sprite); redrawn only when name/HP changes.
  _makeLabelSprite() {
    const c = document.createElement("canvas"); c.width = 256; c.height = 128;
    const tex = new THREE.CanvasTexture(c);
    const spr = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true }));
    spr.scale.set(4.4, 2.2, 1);                 // ~30% smaller than before so it doesn't dwarf the soldier model
    spr.position.y = PLAYER_H * S + 1.7;        // float just above the head (lowered to match the smaller sprite)
    spr.renderOrder = 31;
    return { sprite: spr, canvas: c, ctx: c.getContext("2d"), tex, last: "" };
  }
  _drawCrossedEye(ctx, cx, cy, frac = 1) {  // "you are flashed"; depleting ring = blind time left
    if (frac > 0) {
      const R = 15;
      ctx.lineWidth = 4; ctx.lineCap = "butt";
      ctx.strokeStyle = "rgba(255,255,255,0.18)";
      ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.stroke();               // faint full ring
      ctx.strokeStyle = "rgba(255,212,91,0.92)";
      ctx.beginPath(); ctx.arc(cx, cy, R, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * Math.min(1, frac)); ctx.stroke();
    }
    ctx.strokeStyle = "#ffd45b"; ctx.lineWidth = 3; ctx.lineCap = "round";
    ctx.beginPath(); ctx.ellipse(cx, cy, 13, 7.5, 0, 0, Math.PI * 2); ctx.stroke();
    ctx.beginPath(); ctx.arc(cx, cy, 3.8, 0, Math.PI * 2); ctx.fillStyle = "#ffd45b"; ctx.fill();
    ctx.strokeStyle = "#ff5b5b"; ctx.beginPath(); ctx.moveTo(cx - 16, cy + 10); ctx.lineTo(cx + 16, cy - 10); ctx.stroke();
  }
  _drawLabel(lbl, name, hp, nameHex, flashFrac, gun) {
    const fq = Math.round((flashFrac || 0) * 8);   // quantize so the ring redraws in ~8 steps, not every frame
    const key = name + "|" + hp + "|" + nameHex + "|" + fq + "|" + (gun || "");
    if (lbl.last === key) return;               // only redraw on change (cheap most frames)
    lbl.last = key;
    const ctx = lbl.ctx, W = 256;
    ctx.clearRect(0, 0, W, 128);
    if (flashFrac > 0) this._drawCrossedEye(ctx, W / 2, 15, flashFrac);   // above the healthbar/name
    ctx.fillStyle = "rgba(8,11,15,0.72)"; ctx.fillRect(6, 32, W - 12, 90);
    const nm = name.length > 16 ? name.slice(0, 15) + "..." : name;
    ctx.font = "bold 26px Inter, system-ui, sans-serif";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillStyle = nameHex; ctx.fillText(nm, W / 2, 52);
    const bx = 24, by = 70, bw = W - 48, bh = 14;
    ctx.fillStyle = "#222a33"; ctx.fillRect(bx, by, bw, bh);
    const frac = Math.max(0, Math.min(1, hp / 100));
    ctx.fillStyle = hp > 40 ? "#5fbf5f" : "#e25555"; ctx.fillRect(bx, by, bw * frac, bh);
    ctx.fillStyle = "#eef2f6"; ctx.font = "bold 15px Inter, system-ui, sans-serif";
    ctx.fillText(hp + " HP", W / 2, by + bh / 2 + 1);
    if (gun) {                                   // equipped weapon, under the healthbar (3D only; model gun is fixed)
      ctx.fillStyle = "#cfd6de"; ctx.font = "600 18px Inter, system-ui, sans-serif";
      ctx.fillText(gun.replace(/_/g, " ").toUpperCase(), W / 2, 108);
    }
    lbl.tex.needsUpdate = true;
  }

  // a flat team-coloured POV cone that lies on the floor at the player's feet (the 2D view cone,
  // brought into 3D). Built pointing +X in the XZ plane; render() yaws it to the player's facing.
  _makeViewCone() {
    const half = 0.5, len = 230 * S, seg = 14;
    const pos = [0, 0, 0];
    for (let i = 0; i <= seg; i++) {
      const a = -half + (2 * half) * i / seg;
      pos.push(Math.cos(a) * len, 0, Math.sin(a) * len);
    }
    const idx = [];
    for (let i = 1; i <= seg; i++) idx.push(0, i, i + 1);
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    g.setIndex(idx); g.computeVertexNormals();
    const mesh = new THREE.Mesh(g, new THREE.MeshBasicMaterial({
      transparent: true, opacity: 0.16, side: THREE.DoubleSide, depthWrite: false }));
    mesh.position.y = 0.05;     // just off the floor
    mesh.renderOrder = 5;
    return mesh;
  }

  // thin team-coloured "laser" line from a player's head along their aim. Two points in the
  // group's LOCAL space (group sits at the player's feet, world orientation = identity), updated
  // each frame in render(). depthTest off so the beam reads through the model; renderOrder ~ cone.
  _makeAimLine() {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(6), 3));
    const line = new THREE.Line(g, new THREE.LineBasicMaterial({
      color: CT_COLOR, transparent: true, opacity: 0.5, depthTest: false, depthWrite: false }));
    line.renderOrder = 6;       // just above the floor cone (5); reads over the model
    line.frustumCulled = false; // endpoint moves each frame; skip stale-bounds culling
    return line;
  }

  setDemo(demo) {
    this.demo = demo;
    // reference floor = min alive Z over a sample of frames
    let zmin = Infinity;
    for (let i = 0; i < demo.frames.length; i += 5) {
      for (const p of demo.frames[i].players) if (p && p.alive) zmin = Math.min(zmin, p.z);
    }
    this.zRef = isFinite(zmin) ? zmin : 0;

    // (re)build player figures
    for (const pl of this.players) this.scene.remove(pl.group);
    this.players = [];
    for (let i = 0; i < demo.players.length; i++) {
      const group = new THREE.Group();
      // players are occluded by the real map geometry (sit at their true positions);
      // x-ray (see-through) is a toggle on `this.xray`.
      const pmat = (c) => new THREE.MeshBasicMaterial({ color: c });
      const body = new THREE.Mesh(
        new THREE.CylinderGeometry(PLAYER_R * S, PLAYER_R * S, PLAYER_H * S, 12), pmat(0xffffff));
      body.position.y = PLAYER_H * S / 2;
      const head = new THREE.Mesh(
        new THREE.SphereGeometry(PLAYER_R * 0.7 * S, 12, 10), pmat(0xffffff));
      head.position.y = PLAYER_H * S + PLAYER_R * 0.4 * S;
      const cone = this._makeViewCone();          // flat POV cone on the floor (replaces the triangle)
      body.renderOrder = head.renderOrder = 20;
      const label = this._makeLabelSprite();      // floating eyeball/name/HP/gun above the head
      const mh = new THREE.Group();               // holds the real CS2 model clone (capsule = fallback)
      const aim = this._makeAimLine();            // thin "laser" from the head along the player's aim
      group.add(body); group.add(head); group.add(mh); group.add(cone); group.add(label.sprite); group.add(aim);
      this.scene.add(group);
      this.players.push({ group, body, head, mh, modelTeam: null, cone, label, aim });
    }
    // bomb marker
    if (!this.bomb) {
      this.bomb = new THREE.Mesh(
        new THREE.BoxGeometry(14 * S, 14 * S, 14 * S),
        new THREE.MeshBasicMaterial({ color: 0xd8a13a }));
      this.scene.add(this.bomb);
    }
    // planted-bomb glow: a core orb + a ground halo at the plant spot (red pulse / green defused).
    // depthTest off so it reads through walls (you always see where the bomb is).
    if (!this.bombGlow) {
      const grp = new THREE.Group();
      const gm = () => new THREE.MeshBasicMaterial({ color: 0xff3b30, transparent: true, depthWrite: false, depthTest: false });
      const core = new THREE.Mesh(new THREE.SphereGeometry(5 * S, 16, 12), gm());
      core.position.y = 5 * S; core.renderOrder = 27;
      const dg = new THREE.CircleGeometry(14 * S, 32); dg.rotateX(-Math.PI / 2);
      const disc = new THREE.Mesh(dg, gm()); disc.material.side = THREE.DoubleSide;
      disc.position.y = 0.4 * S; disc.renderOrder = 26;
      grp.add(core); grp.add(disc); grp.visible = false;
      this.scene.add(grp);
      this.bombGlow = grp; this.bombGlow.core = core; this.bombGlow.disc = disc;
    }
  }

  // ---- enter / exit --------------------------------------------------------
  enterAt(worldX, worldY, groundZ) {
    this.active = true;
    this.canvas.style.display = "block";
    // Floor height at the click: prefer the nearest alive player's z (passed in) -- it's a real
    // on-floor height available immediately, even before the GLB streams in. Only fall back to the
    // geo raycast / flat zRef if no player z was given. (A flat zRef sank the cam under multi-level maps.)
    const floorWz = (groundZ != null) ? groundZ : this._floorZAt(worldX, worldY);
    const tx = worldX * S, ty = floorWz * S, tz = -worldY * S;
    this.camera.position.set(tx, ty + 15, tz + 17);
    this.camera.lookAt(tx, ty + PLAYER_H * S * 0.6, tz);   // aim at ~head height above the real floor
    const e = new THREE.Euler().setFromQuaternion(this.camera.quaternion, "YXZ");
    this.pitch = e.x; this.yaw = e.y;   // sync fly-control state to the look direction
    this.resize();
  }
  exit() {
    this.active = false;
    this.canvas.style.display = "none";
    if (document.pointerLockElement === this.canvas) document.exitPointerLock();
  }
  follow(idx) { this.followIdx = idx; }

  resize() {
    const r = this.canvas.getBoundingClientRect();
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    this.renderer.setPixelRatio(dpr);
    this.renderer.setSize(r.width, r.height, false);
    this.camera.aspect = r.width / Math.max(1, r.height);
    this.camera.updateProjectionMatrix();
  }

  // ---- per-frame -----------------------------------------------------------
  update(dt) {
    if (!this.active) return;
    const fast = this._keys["shift"] ? 3 : 1;
    if (this.followIdx >= 0 || this.camPreset !== "free") return;   // cam owned by a preset/follow
    const speed = 380 * S * fast * Math.min(dt, 0.05);
    const fwd = this.camera.getWorldDirection(this._tmpA);
    const right = this._tmpB.crossVectors(fwd, this._up).normalize();
    if (this._keys["w"]) this.camera.position.addScaledVector(fwd, speed);
    if (this._keys["s"]) this.camera.position.addScaledVector(fwd, -speed);
    if (this._keys["d"]) this.camera.position.addScaledVector(right, speed);
    if (this._keys["a"]) this.camera.position.addScaledVector(right, -speed);
    if (this._keys["e"]) this.camera.position.y += speed;                          // E = up
    if (this._keys["q"] || this._keys["control"]) this.camera.position.y -= speed; // Q / Ctrl = down
  }

  // Load the real CS2 character models (extracted GLBs) once. Each team shares one flat
  // material (red/blue, no texture); clones share geometry so 10 players stay cheap.
  _ensurePlayerModels() {
    if (this._pmodel || this._pmodelLoading || !this._loader) return;
    this._pmodelLoading = true;
    const mk = (url, hex) => new Promise(res => this._loader.load(url, g => {
      const mat = new THREE.MeshBasicMaterial({ color: hex });
      g.scene.traverse(o => { if (o.isMesh) { o.material = mat; o.frustumCulled = false; o.renderOrder = 20; } });
      res({ scene: g.scene, mat });
    }, undefined, () => res(null)));
    Promise.all([mk("/static/models/ct_body.glb", CT_COLOR), mk("/static/models/t_body.glb", T_COLOR)])
      .then(([ct, t]) => {
        if (ct && t) {
          const h = new THREE.Box3().setFromObject(ct.scene).getSize(new THREE.Vector3()).y || 1.82;
          this._pmodelScale = (PLAYER_H * S) / h;     // match the 72-unit player height
          this._pmodel = { 3: ct.scene, 2: t.scene }; // 3=CT, 2=T
          this._pmat = { 3: ct.mat, 2: t.mat };
        }
        this._pmodelLoading = false;
      });
  }

  // Player model opacity policy (single source). Models are ALWAYS translucent so they read cleanly:
  //   x-ray OFF -> 0.5, occluded by walls normally (the "looks much better" default);
  //   x-ray ON  -> 0.1, drawn THROUGH walls (faint "behind cover" ghost).
  // Toggling x-ray must never snap back to 1.0 (the old bug); opacity stays 0.5/0.1.
  _applyXray(mat) {
    const x = this.xray;
    mat.depthTest = !x;          // x-ray on => draw through geometry
    mat.depthWrite = !x;         // no depth-write when seeing through walls
    mat.transparent = true;
    mat.opacity = x ? 0.1 : 0.5;
  }

  render(state) {
    if (!this.active || !this.demo) return;
    this._ensurePlayerModels();
    for (let i = 0; i < this.players.length; i++) {
      const p = state.players[i]; const pl = this.players[i];
      if (!p || !p.alive) { pl.group.visible = false; continue; }   // only living players show in 3D
      if (this.camPreset === "fp" && i === this.followIdx) { pl.group.visible = false; continue; }  // you ARE this player
      pl.group.visible = true;
      // colour by side: CT blue, T red (team 3 = CT, team 2 = T)
      const col = new THREE.Color(p.team === 3 ? CT_COLOR : T_COLOR);
      const dt = !this.xray;   // xray -> draw through walls
      const useModel = !!this._pmodel;
      if (useModel) {
        if (pl.modelTeam !== p.team) {                 // attach/swap the right team's CS model
          while (pl.mh.children.length) pl.mh.remove(pl.mh.children[0]);
          const clone = this._pmodel[p.team].clone(true);
          clone.scale.setScalar(this._pmodelScale);
          pl.mh.add(clone);
          pl.modelTeam = p.team;
        }
        pl.mh.visible = true; pl.body.visible = false; pl.head.visible = false;
        pl.mh.rotation.y = p.yaw * Math.PI / 180 + MODEL_YAW;   // face the player's aim
        this._applyXray(this._pmat[3]); this._applyXray(this._pmat[2]);
      } else {
        pl.body.visible = true; pl.head.visible = true;
        this._applyXray(pl.body.material); this._applyXray(pl.head.material);
        pl.body.material.color.copy(col);
        pl.head.material.color.copy(col);
      }
      // use the demo's own Z (map is aligned) -- no per-frame floor raycast (that was the lag)
      pl.group.position.set(p.x * S, (p.z - this.zRef) * S, -p.y * S);
      // floating label: crossed-eye (flashed, depleting over actual blind time) + name + HP. No weapon
      // text -- the sidebar loadout icons show what they hold.
      const fpk = p.flash > 0.5 ? Math.min(1, p.flash / (this.demo.flashPeakAt(i, state.t) || 5)) : 0;
      this._drawLabel(pl.label, this.demo.players[i].name, Math.max(0, Math.round(p.hp)),
        p.team === 3 ? "#8fb8ff" : "#ff8f8f", fpk, p.weapon);
      // POV cone flat on the floor, team-coloured, yawed to facing (toggle: this.showCone)
      pl.cone.visible = this.showCone;
      pl.cone.rotation.y = p.yaw * Math.PI / 180;
      pl.cone.material.color.copy(col);
      // aim "laser": from the head along the player's aim. Group sits at the feet with world
      // orientation = identity, so we set both endpoints in group-LOCAL space using dir directly.
      // dir matches the floor cone (cone is yawed by +yaw about Y; +X forward => cos/-sin in XZ).
      if (pl.aim) {
        if (this.showAim) {
          const yaw = p.yaw * Math.PI / 180, pitch = (p.pitch || 0) * Math.PI / 180;
          const cp = Math.cos(pitch);                       // full 3D aim: yaw + pitch (+pitch = looking down)
          const dx = cp * Math.cos(yaw), dy = -Math.sin(pitch), dz = -cp * Math.sin(yaw);
          const headY = PLAYER_H * S, LEN = 500 * S;
          const a = pl.aim.geometry.attributes.position;
          a.array[0] = 0;            a.array[1] = headY;            a.array[2] = 0;
          a.array[3] = dx * LEN;     a.array[4] = headY + dy * LEN; a.array[5] = dz * LEN;
          a.needsUpdate = true;
          pl.aim.geometry.computeBoundingSphere();
          pl.aim.material.color.copy(col);
          pl.aim.material.depthTest = dt;   // honor x-ray (see-through) like the model
          pl.aim.visible = true;
        } else {
          pl.aim.visible = false;
        }
      }
      const hi = i === this.followIdx;
      if (!useModel) pl.body.material.color.offsetHSL(0, 0, hi ? 0.15 : 0);
      pl.group.scale.setScalar(hi ? 1.25 : 1);
    }
    if (state.bomb && this.bomb) {
      this.bomb.position.set(state.bomb.x * S, (state.bomb.z - this.zRef) * S + 6 * S, -state.bomb.y * S);
      this.bomb.material.color.set(state.bomb.state === "planted" ? 0xff3b30 : 0xd8a13a);
    }
    // planted-bomb glow at the plant spot: red pulsing while ticking, solid green once defused
    const pb = this.demo.plantedBombAt(state.t);
    if (this.bombGlow) {
      if (pb) {
        this.bombGlow.visible = true;
        this.bombGlow.position.set(pb.x * S, (pb.z - this.zRef) * S, -pb.y * S);
        const green = pb.state === "defused";
        const col = green ? 0x3cdc78 : 0xff3b30;
        const pulse = green ? 1 : 0.5 + 0.5 * Math.sin(performance.now() * 0.006);   // wall-clock -> pulses even when paused
        const core = this.bombGlow.core, disc = this.bombGlow.disc;
        core.material.color.setHex(col); disc.material.color.setHex(col);
        core.material.opacity = green ? 0.95 : 0.55 + 0.45 * pulse;
        disc.material.opacity = green ? 0.45 : 0.22 + 0.33 * pulse;
        disc.scale.setScalar(green ? 3.0 : 2.2 + pulse * 1.6);
      } else {
        this.bombGlow.visible = false;
      }
    }
    this.renderFX(state);   // nade arcs/volumes, kill marks (synced to the clock)

    // camera: first-person (fp, at the player's eyes) > scripted preset > follow-cam > free fly
    const fpOK = this.camPreset === "fp" && this.followIdx >= 0
      && state.players[this.followIdx] && state.players[this.followIdx].alive;
    // FOV: 75 normally; 74 in FP; when FP + actually scoped on a sniper, narrow to CS's exact
    // scope FOV (zoom 1). The CS value is HORIZONTAL deg -> convert to three's vertical fov at
    // the current aspect so the magnification matches CS.
    let wantFov = fpOK ? 74 : 75;
    if (fpOK) {
      const fp = state.players[this.followIdx];
      const sh = scopeHFov(fp.weapon, fp.zoom || (fp.scoped ? 1 : 0));   // exact level 1/2 from the demo
      if (sh) {
        const aspect = this.camera.aspect || (16 / 9);
        wantFov = 2 * Math.atan(Math.tan(sh * Math.PI / 360) / aspect) * 180 / Math.PI;
      }
    }
    if (Math.abs(this.camera.fov - wantFov) > 0.01) { this.camera.fov = wantFov; this.camera.updateProjectionMatrix(); }
    if (fpOK) {
      // sit at their eyes and look EXACTLY along their view angles (same yaw/pitch math as the
      // aim laser), so screen-centre = their crosshair. Snap (no lerp) -- it's locked to the head.
      const p = state.players[this.followIdx];
      const yaw = p.yaw * Math.PI / 180, pitch = (p.pitch || 0) * Math.PI / 180;
      const cp = Math.cos(pitch);
      const dx = cp * Math.cos(yaw), dy = -Math.sin(pitch), dz = -cp * Math.sin(yaw);
      // eye height drops when crouched: ~64u standing -> ~46u fully ducked (p.duck 0..1)
      const eyeU = 64 - 18 * Math.max(0, Math.min(1, p.duck || 0));
      const ex = p.x * S, ey = (p.z - this.zRef) * S + eyeU * S, ez = -p.y * S;
      this.camera.up.set(0, 1, 0);
      this.camera.position.set(ex, ey, ez);
      this.camera.lookAt(ex + dx, ey + dy, ez + dz);
    } else {
      const presetCam = (this.camPreset === "overhead" || this.camPreset === "utility"
        || this.camPreset === "death") && this._applyCamPreset(state);
      if (!presetCam) {
        this.camera.up.set(0, 1, 0);
        if (this.followIdx >= 0 && state.players[this.followIdx] && state.players[this.followIdx].alive) {
          const p = state.players[this.followIdx];
          const px = p.x * S, py = (p.z - this.zRef) * S, pz = -p.y * S;
          const yaw = p.yaw * Math.PI / 180;
          this._tmpA.set(px - Math.cos(yaw) * 9, py + 6, pz + Math.sin(yaw) * 9);
          this.camera.position.lerp(this._tmpA, 0.2);
          this.camera.lookAt(px, py + 2.5, pz);
        }
      }
    }
    this.renderer.render(this.scene, this.camera);
  }

  // ---- input ---------------------------------------------------------------
  _applyRot() { this.camera.rotation.set(this.pitch, this.yaw, 0); }
  _bindInput() {
    const c = this.canvas;
    c.addEventListener("click", () => { if (this.active) c.requestPointerLock(); });
    document.addEventListener("mousemove", (e) => {
      if (!this.active || document.pointerLockElement !== c) return;
      this.yaw -= e.movementX * 0.0024;
      this.pitch -= e.movementY * 0.0024;
      this.pitch = Math.max(-1.5, Math.min(1.5, this.pitch));
      this._applyRot();
    });
    const set = (e, v) => {
      if (!this.active) return;
      const k = e.key.toLowerCase();
      // NOTE: Space is intentionally NOT captured here -- it stays global play/pause (app.js).
      if (["w","a","s","d","q","e","shift","control"].includes(k)) {
        this._keys[k] = v; e.preventDefault();
      }
    };
    window.addEventListener("keydown", (e) => set(e, true));
    window.addEventListener("keyup", (e) => set(e, false));
  }
}
