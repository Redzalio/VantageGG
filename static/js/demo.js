// demo.js -- parsed-demo data model + time interpolation
// Consumes the JSON shape emitted by parser.py / mockgen.py.

export class Cs2Demo {
  constructor(json) {
    this.raw = json;
    this.map = json.map;
    this.analytics = json.analytics || null;   // coaching/analytics layer (may be null)
    this.players = json.players;          // [{steamid,name,team,color}]
    this.frames = json.frames;            // evenly spaced
    this.rounds = json.rounds || [];
    this.events = json.events || [];
    this.grenades = json.grenades || [];   // [{type,thrower,t0,t1,pts:[[t,x,y]...]}]
    this.damages = json.damages || [];     // [{t,atk,vic,dmg,hg}]
    this.loadouts = json.loadouts || {};    // {idx: [[t,[weapons]], ...]} held-inventory timeline
    this.sampleRate = json.sample_rate || 8;
    this.dt = 1 / this.sampleRate;
    this.duration = json.duration || (this.frames.length * this.dt);
    this.tickrate = json.tickrate || 64;
    this.kills = this.events.filter(e => e.type === "kill");
    this.hasDuck = this._detectDuck();      // crouch data present? (parses before duck_amount lack it)
    this._assignShades();
  }

  // Crouch ("duck") data is all-or-nothing per parse: demos parsed before the duck_amount field
  // existed have no `duck` key at all -> first-person crouch can't be shown for them.
  _detectDuck() {
    for (const f of (this.frames || [])) {
      for (const p of (f.players || [])) {
        if (p && typeof p === "object") return "duck" in p;
      }
    }
    return false;
  }

  // Stable per-player "shade" index within their starting team, so dots stay
  // distinguishable. Actual color is resolved per-frame from CURRENT team
  // (colorFor) so it flips correctly at the halftime side swap.
  _assignShades() {
    let ct = 0, t = 0;
    for (const p of this.players) p._k = (p.team === 3) ? ct++ : t++;
  }

  static teamColor(team, k = 0) {
    // comma syntax so both canvas AND THREE.Color parse it
    if (team === 3) return `hsl(${200 + k * 6}, 68%, ${56 + k * 4}%)`;  // CT blues
    return `hsl(${44 - k * 5}, 86%, ${52 + k * 4}%)`;                   // T golds/oranges
  }

  colorFor(i, team) {
    const k = this.players[i] ? (this.players[i]._k || 0) : 0;
    return Cs2Demo.teamColor(team, k);
  }

  teamAtTime(i, t) {
    const fi = Math.max(0, Math.min(this.frames.length - 1, Math.round(t / this.dt)));
    const p = this.frames[fi].players[i];
    return p ? p.team : 2;
  }

  frameIndexAt(t) {
    const f = t / this.dt;
    return Math.max(0, Math.min(this.frames.length - 1, f));
  }

  // Interpolated world state at time t (seconds).
  stateAt(t) {
    const fi = this.frameIndexAt(t);
    const i0 = Math.floor(fi);
    const i1 = Math.min(this.frames.length - 1, i0 + 1);
    const frac = fi - i0;
    const f0 = this.frames[i0], f1 = this.frames[i1];

    const players = [];
    for (let i = 0; i < this.players.length; i++) {
      const a = f0.players[i], b = f1.players[i];
      if (!a) { players.push(null); continue; }
      if (!b) { players.push({ ...a }); continue; }
      players.push({
        idx: i,
        x: lerp(a.x, b.x, frac),
        y: lerp(a.y, b.y, frac),
        z: lerp(a.z, b.z, frac),
        yaw: lerpAngle(a.yaw, b.yaw, frac),
        pitch: lerp(a.pitch || 0, b.pitch || 0, frac),
        hp: a.hp,
        armor: a.armor,
        alive: a.alive,
        team: a.team,
        weapon: a.weapon,
        flash: a.flash || 0,
        scoped: a.scoped || 0,
        zoom: a.zoom || 0,        // 0 none / 1 first zoom / 2 second zoom (for FP scope FOV)
        clip: a.clip == null ? null : a.clip,   // bullets in the magazine (FP ammo HUD)
        reload: a.reload || 0,                   // 1 while reloading
        duck: a.duck || 0,                       // 0 standing .. 1 fully crouched (FP eye-height)
        money: a.money,
        helmet: a.helmet,
        kit: a.kit,
        name: this.players[i].name,
        color: this.colorFor(i, a.team),
      });
    }

    // bomb (no interp on state, lerp position)
    let bomb = null;
    if (f0.bomb) {
      const ba = f0.bomb, bb = f1.bomb || f0.bomb;
      bomb = {
        x: lerp(ba.x, bb.x, frac), y: lerp(ba.y, bb.y, frac), z: ba.z,
        state: ba.state, carrier: ba.carrier,
      };
    }
    return { t, round: f0.round, players, bomb };
  }

  roundAt(t) {
    let cur = this.rounds[0] || null;
    for (const r of this.rounds) {
      if (t >= r.start_t) cur = r; else break;
    }
    return cur;
  }

  // #48 Death Review: all of a player's deaths as review-session items (seek ~2s before each so
  // you watch the lead-up; spectate the victim). Round is derived since kill events carry no round.
  deathsFor(idx) {
    const out = [];
    for (const k of this.kills) {
      if (k.victim !== idx) continue;
      const r = this.roundAt(k.t);
      const killer = (k.attacker != null && k.attacker >= 0 && this.players[k.attacker])
        ? this.players[k.attacker].name : "world";
      const extra = k.weapon ? ` (${k.weapon}${k.headshot ? ", HS" : ""})` : (k.headshot ? " (HS)" : "");
      out.push({ t: Math.max(0, k.t - 2.0), eventT: k.t, player: idx,
                 round: r ? r.number : null, text: `killed by ${killer}${extra}` });
    }
    out.sort((a, b) => a.t - b.t);
    return out;
  }

  scoreAt(t) {
    let ct = 0, tt = 0;
    for (const r of this.rounds) {
      if (t >= r.end_t) { ct = r.score_ct; tt = r.score_t; }
    }
    return { ct, t: tt };
  }

  // Cumulative K/A/D for each player up to time t.
  statsUpTo(t) {
    const s = this.players.map(() => ({ k: 0, a: 0, d: 0 }));
    for (const e of this.kills) {
      if (e.t > t) break;
      if (e.attacker != null && e.attacker >= 0) s[e.attacker].k++;
      if (e.assister != null && e.assister >= 0) s[e.assister].a++;
      if (e.victim != null && e.victim >= 0) s[e.victim].d++;
    }
    return s;
  }

  // kill feed = all kills in the CURRENT round up to t (persists until the round ends)
  killFeed(t) {
    const r = this.roundAt(t);
    const start = r ? (r.start_t || 0) : 0;
    return this.kills.filter(k => k.t >= start && k.t <= t);
  }
  // short-window kills for the on-map X markers
  recentKills(t, win = 1.6) {
    return this.kills.filter(k => k.t <= t && k.t >= t - win);
  }
  // a player's recent movement path (world coords) over the last `secs` -- for trails.
  // frameIndexAt() returns a FLOAT, so floor/round to real array indices.
  trail(idx, t, secs = 4) {
    const out = [];
    const n = this.frames.length;
    const i1 = Math.min(n - 1, Math.round(this.frameIndexAt(t)));
    const i0 = Math.max(0, Math.floor(this.frameIndexAt(t - secs)));
    for (let i = i0; i <= i1; i++) {
      const fr = this.frames[i];
      const p = fr && fr.players[idx];
      if (p && p.alive) out.push([p.x, p.y, p.z]);
    }
    return out;
  }
  // every utility throw (for the whole-demo utility search)
  utilityList() {
    return this.events.filter(e =>
      e.type === "smoke" || e.type === "molotov" || e.type === "flash" || e.type === "he");
  }

  // Active grenade effects at time t.
  activeNades(t) {
    const out = [];
    for (const e of this.events) {
      if (e.type === "smoke" || e.type === "molotov") {
        if (t >= e.t && t <= (e.end_t || e.t + 1)) {
          out.push({ ...e, age: t - e.t, life: (e.end_t || e.t + 1) - e.t });
        }
      } else if (e.type === "flash" || e.type === "he") {
        if (t >= e.t && t <= e.t + 0.7) out.push({ ...e, age: t - e.t });
      }
    }
    return out;
  }

  // grenades whose throw arc should show now (flight + ~1.5s linger after landing).
  // pts are trimmed to the flight only, so this stays clean for ALL nade types.
  trajectoriesAt(t) {
    // Show the projectile/arc until it DETONATES (det_t): the smoke/molly volume -- keyed off the
    // same detonation event -- then takes over with no gap. Without a matched detonation, fall back
    // to the trimmed arc end (+0.5s linger), the old behaviour.
    return this.grenades.filter(g =>
      t >= g.t0 - 0.05 && t <= (g.det_t != null ? g.det_t : g.t1 + 0.5));
  }
  // all grenade throws (with arcs) for the whole-demo utility search
  utilityThrows() { return this.grenades; }
  // damage events in the last `win` seconds (bullet/hit traces)
  tracesAt(t, win = 0.3) {
    return this.damages.filter(d => d.atk >= 0 && d.t <= t && d.t >= t - win);
  }
  // set of player indices who took damage very recently (damage-received FX)
  recentDamage(t, win = 0.25) {
    const s = new Set();
    for (const d of this.damages) if (d.t <= t && d.t >= t - win) s.add(d.vic);
    return s;
  }
  // a player's bullet shots in the last `win` seconds (3D impact markers, sv_showimpacts style).
  // Each shot carries origin (ox,oy,oz) + view angle (pitch,yaw); the 3D view raycasts the map mesh.
  shotsAt(idx, t, win = 10) {
    return this.events.filter(e =>
      e.type === "shot" && e.player === idx && e.t <= t && e.t >= t - win);
  }

  // a player's ACTUAL held weapons at time t (last change <= t in the loadout timeline).
  // Returns an array of weapon names (with duplicates -> counts), or null if none recorded.
  loadoutAt(idx, t) {
    const arr = this.loadouts[idx] || this.loadouts[String(idx)];
    if (!arr || !arr.length) return null;
    let cur = null;
    for (const e of arr) { if (e[0] <= t) cur = e[1]; else break; }
    return cur;
  }

  // Magazine size per weapon, derived from the demo itself (max clip ever seen for that weapon).
  // Cached on first use. Only firearms reach >=5; knife/grenades/c4/zeus stay <=1, so the FP HUD
  // uses (cap >= 5) to decide whether to show an ammo readout. Returns 0 if unknown.
  magCap(weapon) {
    if (!this._magCap) {
      const m = {};
      for (const f of this.frames) {
        for (const p of f.players) {
          if (p && p.weapon && p.clip != null) {
            const c = p.clip | 0;
            if (c > (m[p.weapon] || 0)) m[p.weapon] = c;
          }
        }
      }
      this._magCap = m;
    }
    return this._magCap[weapon] || 0;
  }

  // Per-player flash "spikes" {t0: time at peak blindness, peak: seconds blind}, derived from the
  // sawtooth flash_duration track. Lets the flash indicator deplete over the player's ACTUAL blind
  // time (a partial flash that only blinds 1.5s shows a full->empty wedge over 1.5s, not 1.5/5).
  _flashSpikesFor(idx) {
    if (!this._flashSpikes) this._flashSpikes = {};
    if (this._flashSpikes[idx]) return this._flashSpikes[idx];
    const sp = [];
    let prev = 0, cur = null;
    for (const fr of this.frames) {
      const pl = fr.players[idx];
      const f = pl ? (pl.flash || 0) : 0;
      if (f > prev + 0.05) {                         // rising
        if (!cur || prev <= 0.05) { cur = { t0: fr.t, peak: f }; sp.push(cur); }  // new flash (from ~0)
        else { cur.peak = f; cur.t0 = fr.t; }        // still climbing / re-flash -> raise peak
      } else if (f <= 0.05) {
        cur = null;                                  // recovered
      }
      prev = f;
    }
    this._flashSpikes[idx] = sp;
    return sp;
  }
  // peak blind-seconds of the flash active at time t for player idx (0 if not flashed)
  flashPeakAt(idx, t) {
    const sp = this._flashSpikesFor(idx);
    let peak = 0;
    for (const s of sp) {
      if (s.t0 > t + 0.25) break;
      if (t <= s.t0 + s.peak + 0.3) peak = s.peak;
    }
    return peak;
  }

  bombEventNear(t) {
    // returns the bomb events relevant to the current round
    const r = this.roundAt(t);
    if (!r) return {};
    let planted = null, defused = null, begindefuse = null;
    for (const e of this.events) {
      if (e.t < r.start_t || e.t > r.end_t) continue;
      if (e.type === "bomb_planted") planted = e;
      else if (e.type === "bomb_defused") defused = e;
      else if (e.type === "bomb_begindefuse") begindefuse = e;   // last begin = the one that completed
    }
    return { planted, defused, begindefuse };
  }

  // Reconstructed PLANTED-bomb position + state for the current round. The demo has no bomb coords,
  // so the plant spot = the planter's position at the plant tick. Returns {x,y,z,state} where state
  // is "planted" (ticking) or "defused", or null (not planted yet, or exploded). Cached per round.
  plantedBombAt(t) {
    const r = this.roundAt(t);
    if (!r) return null;
    if (!this._plantCache) this._plantCache = {};
    let pc = this._plantCache[r.number];
    if (pc === undefined) {
      pc = null;
      let planted = null, defused = null;
      for (const e of this.events) {
        if (e.t < r.start_t || e.t > r.end_t) continue;
        if (e.type === "bomb_planted") planted = e;
        else if (e.type === "bomb_defused") defused = e;
      }
      if (planted && planted.player != null && planted.player >= 0) {
        const p = this.stateAt(planted.t).players[planted.player];
        if (p) pc = { x: p.x, y: p.y, z: p.z, plantT: planted.t, defuseT: defused ? defused.t : null };
      }
      this._plantCache[r.number] = pc || null;
    }
    if (!pc || t < pc.plantT) return null;
    if (pc.defuseT != null && t >= pc.defuseT) return { x: pc.x, y: pc.y, z: pc.z, state: "defused" };
    if (pc.defuseT == null && t >= pc.plantT + 40) return null;   // exploded -> glow stops
    return { x: pc.x, y: pc.y, z: pc.z, state: "planted" };
  }
}

function lerp(a, b, f) { return a + (b - a) * f; }
function lerpAngle(a, b, f) {
  let d = ((b - a + 540) % 360) - 180;
  return a + d * f;
}
