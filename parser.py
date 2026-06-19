"""
parser.py -- turn a CS2 .dem into the normalized JSON the web player consumes.

Emits the SAME schema as mockgen.py (see that file's docstring).

Usage:
    python parser.py path/to/demo.dem [out.json]      # parse -> JSON
    python parser.py --probe path/to/demo.dem         # inspect fields/events

demoparser2 prop/field names can vary slightly by version, so tick parsing is
done defensively (drops unknown optional props and fills defaults). Run --probe
on a real demo to confirm the exact names available on your build.
"""
import json
import math
import sys

import pandas as pd
from demoparser2 import DemoParser

from schema import SCHEMA_VERSION       # replay JSON schema version (shared, dep-free module)
from roundlib import norm_weapon, pair_rounds   # shared round construction + weapon naming

SAMPLE_RATE = 8           # frames/sec emitted
DEFAULT_TICKRATE = 64

# Optimistic tick prop list; safe_parse_ticks() drops names this build rejects.
TICK_PROPS = ["X", "Y", "Z", "yaw", "pitch", "health", "armor_value",
              "team_num", "is_alive", "active_weapon_name", "inventory",
              "balance", "flash_duration", "has_helmet", "has_defuser", "is_scoped", "zoom_lvl",
              "m_iClip1", "is_in_reload", "duck_amount"]
ESSENTIAL = ["X", "Y", "Z", "health", "team_num"]
OPTIONAL_ORDER = ["duck_amount", "m_iClip1", "is_in_reload", "zoom_lvl", "inventory", "is_scoped",
                  "has_defuser", "has_helmet", "flash_duration", "balance", "pitch",
                  "active_weapon_name", "is_alive", "armor_value"]


def _clean_inv(inv):
    """demoparser2 'inventory' = list of held weapon DISPLAY names (with duplicates, e.g. two
    Flashbangs). Keep display case for the UI; drop the always-present knife. Returns a list."""
    out = []
    try:
        for w in inv:
            w = str(w).strip()
            lw = w.lower()
            if not w or lw == "nan" or "knife" in lw or "bayonet" in lw or "karambit" in lw or "daggers" in lw:
                continue
            out.append(w.replace("weapon_", ""))
    except TypeError:
        pass
    return out


def _gtype(s):
    s = (s or "").lower()
    if "smoke" in s: return "smoke"
    if "flash" in s: return "flash"
    if "molotov" in s or "incendiary" in s or "incgren" in s or "inferno" in s: return "molotov"
    if "decoy" in s: return "decoy"
    if "he" in s or "frag" in s or "grenade" == s: return "he"
    return "he"


def _wpn(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    return norm_weapon(v)   # shared normalizer (lowercase, drop 'weapon_')


def _f(v, default=0.0):
    """NaN-safe float (NaN serializes as invalid JSON; force a finite number)."""
    try:
        x = float(v)
        return default if math.isnan(x) else x
    except (TypeError, ValueError):
        return default


def _sid(v):
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return None
        return str(int(v))
    except (ValueError, TypeError):
        s = str(v)
        return s if s and s != "nan" else None


def safe_parse_ticks(parser, props, ticks):
    """Try the full prop list; progressively drop optional props on failure."""
    try:
        return parser.parse_ticks(props, ticks=ticks), list(props)
    except Exception as e:
        print(f"  parse_ticks full set failed ({e}); degrading...")
    cur = list(props)
    for opt in OPTIONAL_ORDER:
        if opt in cur:
            cur.remove(opt)
            try:
                return parser.parse_ticks(cur, ticks=ticks), cur
            except Exception:
                continue
    return parser.parse_ticks(ESSENTIAL, ticks=ticks), list(ESSENTIAL)


def _event(parser, name, available, force=False, **kwargs):
    # round_start/round_end are NOT returned by list_game_events() but ARE parseable
    # directly -- callers pass force=True for those.
    if not force and name not in available:
        return pd.DataFrame()
    try:
        df = parser.parse_event(name, **kwargs)
        return df if df is not None else pd.DataFrame()
    except Exception as e:
        print(f"  event {name} failed: {e}")
        return pd.DataFrame()


def _col(df, *names, default=None):
    for n in names:
        if n in df.columns:
            return df[n]
    return pd.Series([default] * len(df))


def parse_demo(src, sample_rate=SAMPLE_RATE):
    # accept a path OR an existing DemoParser (so upload can share one parse with analytics)
    parser = src if isinstance(src, DemoParser) else DemoParser(src)
    header = {}
    try:
        header = parser.parse_header()
    except Exception as e:
        print(f"  header failed: {e}")
    map_name = header.get("map_name") or "de_unknown"
    tickrate = int(float(header.get("tickrate") or 0)) or DEFAULT_TICKRATE

    available = set()
    try:
        available = set(parser.list_game_events())
    except Exception:
        pass

    # ---- determine tick range -------------------------------------------------
    max_tick = 0
    for key in ("playback_ticks", "playback_frames"):
        try:
            max_tick = max(max_tick, int(float(header.get(key) or 0)))
        except (ValueError, TypeError):
            pass
    # The header's playback count can UNDERCOUNT the real demo (seen on MM demos): the final
    # round's events live beyond it, so sampling only to max_tick dropped the LAST round's
    # frames -- you could watch round N-1 but clicking round N wrapped back to round 1.
    # Always extend the range to the last round event (+ a few seconds of post-round footage).
    for ev in ("round_officially_ended", "round_end"):
        df = _event(parser, ev, available, force=True)
        if len(df) and "tick" in df:
            try:
                max_tick = max(max_tick, int(df["tick"].max()) + tickrate * 5)
            except (ValueError, TypeError):
                pass
    step = max(1, round(tickrate / sample_rate))
    if max_tick <= 0:
        # last resort: parse everything, then we'll learn the range
        ticks = None
    else:
        ticks = list(range(0, max_tick + step, step))

    # ---- ticks ----------------------------------------------------------------
    print(f"  parsing ticks (map={map_name}, tickrate={tickrate}, step={step})...")
    tdf, used = safe_parse_ticks(parser, TICK_PROPS, ticks)
    print(f"  tick rows={len(tdf)} cols={list(tdf.columns)}")
    if not len(tdf):
        raise RuntimeError("No tick data parsed from demo.")
    if max_tick <= 0:
        max_tick = int(tdf["tick"].max())

    has = lambda c: c in tdf.columns
    # roster: order of first appearance
    roster = {}            # steamid -> idx
    players = []
    names = {}
    for sid, name in zip(tdf.get("steamid", []), tdf.get("name", [])):
        s = _sid(sid)
        if s is None:
            continue
        if s not in roster:
            roster[s] = len(players)
            players.append({"steamid": s, "name": str(name), "team": 2, "color": None})
        if name is not None:
            names[s] = str(name)
    for s, nm in names.items():
        players[roster[s]]["name"] = nm

    # group rows by tick
    frames = []
    # compact per-player loadout timeline: {idx: [[t, [weapons]], ...]} recorded only when the
    # held inventory CHANGES (buys/pickups/throws) -- accurate "current loadout" without per-frame bloat
    loadouts = {}
    last_inv = {}
    has_inv = "inventory" in used
    # ensure sorted by tick
    tdf = tdf.sort_values("tick")
    for tick, g in tdf.groupby("tick"):
        slot = [None] * len(players)
        for row in g.itertuples(index=False):
            d = row._asdict()
            s = _sid(d.get("steamid"))
            if s is None or s not in roster:
                continue
            idx = roster[s]
            health = int(_f(d.get("health")))
            alive = d.get("is_alive")
            alive = (1 if alive else 0) if alive is not None else (1 if health > 0 else 0)
            if has_inv and alive:                       # record real held inventory on change
                inv = _clean_inv(d.get("inventory") or [])
                if inv:
                    key = tuple(sorted(inv))
                    if last_inv.get(idx) != key:
                        last_inv[idx] = key
                        loadouts.setdefault(idx, []).append([round(int(tick) / tickrate, 2), inv])
            team = int(_f(d.get("team_num")))      # _f is NaN-safe (`NaN or 0` -> NaN -> int() crashed)
            if team in (2, 3):
                players[idx]["team"] = team if players[idx].get("_teamset") else team
                players[idx]["_teamset"] = True
            slot[idx] = {
                "x": round(_f(d.get("X")), 1),
                "y": round(_f(d.get("Y")), 1),
                "z": round(_f(d.get("Z")), 1),
                "yaw": round(_f(d.get("yaw")), 1),
                "pitch": round(_f(d.get("pitch")), 1),   # view pitch (+down/-up) for the 3D aim laser
                "hp": health,
                "armor": int(_f(d.get("armor_value"))),
                "alive": alive,
                "team": team,
                "weapon": _wpn(d.get("active_weapon_name")),
                "flash": round(_f(d.get("flash_duration")), 2),
                "money": int(_f(d.get("balance"))),
                "helmet": 1 if d.get("has_helmet") else 0,
                "kit": 1 if d.get("has_defuser") else 0,
                "scoped": 1 if d.get("is_scoped") else 0,
                "zoom": int(_f(d.get("zoom_lvl"))),   # 0 = none, 1 = first zoom, 2 = second zoom
                "clip": int(_f(d.get("m_iClip1"))),   # bullets in the current magazine
                "reload": 1 if d.get("is_in_reload") else 0,
                "duck": round(_f(d.get("duck_amount")), 2),   # 0 standing .. 1 fully crouched (FP eye-height)
            }
        frames.append({"t": round(int(tick) / tickrate, 3), "round": 0,
                       "players": slot, "bomb": None})
    for p in players:
        p.pop("_teamset", None)

    # ---- rounds (robust tick-range pairing; shared with analytics) -------------
    rs = _event(parser, "round_start", available, force=True)
    rfe = _event(parser, "round_freeze_end", available)
    rend = _event(parser, "round_end", available, force=True)
    starts = [int(t) for t in rs["tick"]] if len(rs) and "tick" in rs else []
    freezes = [int(t) for t in rfe["tick"]] if len(rfe) and "tick" in rfe else []
    end_rows = rend.sort_values("tick").to_dict("records") if len(rend) and "tick" in rend else []
    rounds = []
    score_ct = score_t = 0
    for r in pair_rounds(starts, freezes, end_rows):
        if r["winner"] == "CT":
            score_ct += 1
        elif r["winner"] == "T":
            score_t += 1
        rounds.append({
            "number": r["num"],
            "start_t": round(r["start"] / tickrate, 2),
            "freeze_end_t": round(r["freeze_end"] / tickrate, 2),
            "end_t": round(r["end"] / tickrate, 2),
            "winner": r["winner"],
            "reason": r["reason"],
            "score_ct": score_ct, "score_t": score_t,
        })
    # tag frames with round number
    if rounds:
        ri = 0
        for fr in frames:
            while ri + 1 < len(rounds) and fr["t"] >= rounds[ri + 1]["start_t"]:
                ri += 1
            fr["round"] = rounds[ri]["number"]

    def round_num_at(t):
        rn = rounds[0]["number"] if rounds else 0
        for r in rounds:
            if t >= r["start_t"]:
                rn = r["number"]
            else:
                break
        return rn

    # ---- events ---------------------------------------------------------------
    events = []
    deaths = _event(parser, "player_death", available, player=["X", "Y"])
    if len(deaths):
        atk_sid = _col(deaths, "attacker_steamid")
        usr_sid = _col(deaths, "user_steamid")
        ast_sid = _col(deaths, "assister_steamid")
        for i in range(len(deaths)):
            vi = roster.get(_sid(usr_sid.iloc[i]))
            if vi is None:
                continue
            ai = roster.get(_sid(atk_sid.iloc[i]))
            si = roster.get(_sid(ast_sid.iloc[i]))
            tick = int(deaths["tick"].iloc[i])
            events.append({
                "t": round(tick / tickrate, 2), "type": "kill",
                "attacker": ai if ai is not None else -1,
                "victim": vi,
                "assister": si if si is not None else None,
                "weapon": norm_weapon(_col(deaths, "weapon").iloc[i]),
                "headshot": bool(_col(deaths, "headshot", default=False).iloc[i]),
                "ax": _num(_col(deaths, "attacker_X").iloc[i]),
                "ay": _num(_col(deaths, "attacker_Y").iloc[i]),
                "vx": _num(_col(deaths, "user_X").iloc[i]),
                "vy": _num(_col(deaths, "user_Y").iloc[i]),
            })

    # begin* events let the UI show plant/defuse progress from when it ACTUALLY starts
    # (the demo records both the begin and the completed event).
    for ename, etype in (("bomb_planted", "bomb_planted"), ("bomb_defused", "bomb_defused"),
                         ("bomb_begindefuse", "bomb_begindefuse"), ("bomb_beginplant", "bomb_beginplant")):
        bdf = _event(parser, ename, available)
        has_user = "user_steamid" in bdf.columns
        has_kit = "haskit" in bdf.columns
        for i in range(len(bdf)):
            tick = int(bdf["tick"].iloc[i])
            ev = {"t": round(tick / tickrate, 2), "type": etype}
            site = _col(bdf, "site").iloc[i]
            if site is not None:
                ev["site"] = str(site)
            if has_user:
                ev["player"] = roster.get(_sid(bdf["user_steamid"].iloc[i]), -1)
            if etype == "bomb_begindefuse" and has_kit:
                ev["kit"] = bool(bdf["haskit"].iloc[i])
            events.append(ev)

    nade_map = [("smokegrenade_detonate", "smoke", 18.0),
                ("inferno_startburn", "molotov", 7.0),
                ("flashbang_detonate", "flash", 0.0),
                ("hegrenade_detonate", "he", 0.0)]
    for ename, etype, life in nade_map:
        ndf = _event(parser, ename, available)
        has_user = "user_steamid" in ndf.columns
        for i in range(len(ndf)):
            tick = int(ndf["tick"].iloc[i])
            t = round(tick / tickrate, 2)
            player = -1
            if has_user:
                player = roster.get(_sid(ndf["user_steamid"].iloc[i]), -1)
            ev = {"t": t, "type": etype, "round": round_num_at(t), "player": player,
                  "x": _num(_col(ndf, "x", "X").iloc[i]),
                  "y": _num(_col(ndf, "y", "Y").iloc[i]),
                  "z": _num(_col(ndf, "z", "Z").iloc[i])}
            if life > 0:
                ev["end_t"] = round(t + life, 2)
            if ev["x"] is not None:
                events.append(ev)

    events.sort(key=lambda e: e["t"])

    # ---- grenade trajectories (parse_grenades) -------------------------------
    grenades = []
    try:
        gdf = parser.parse_grenades()
    except Exception as e:
        print("  parse_grenades failed:", e)
        gdf = pd.DataFrame()
    if len(gdf):
        cols = {c.lower(): c for c in gdf.columns}
        cx = cols.get("x") or cols.get("grenade_x")
        cy = cols.get("y") or cols.get("grenade_y")
        cz = cols.get("z") or cols.get("grenade_z")   # height -> 3D arcs/smoke volumes
        ctk = cols.get("tick")
        cent = cols.get("entity_id") or cols.get("grenade_entity_id")
        ctype = cols.get("grenade_type") or cols.get("name")
        cthr = (cols.get("thrower_steamid") or cols.get("thrower")
                or cols.get("user_steamid") or cols.get("steamid"))
        if cx and cy and ctk and cent:
            for ent, grp in gdf.sort_values(ctk).groupby(cent):
                # entity ids are REUSED across the match -> split into separate throws
                # at tick gaps (the entity wasn't present in between). Otherwise different
                # nades merge: wrong type (first wins) + a path spanning the whole round.
                segments, seg, prev = [], [], None
                for row in grp.itertuples(index=False):
                    dd = row._asdict()
                    tk = int(dd[ctk])
                    if prev is not None and tk - prev > 32:
                        if seg:
                            segments.append(seg)
                        seg = []
                    seg.append(dd)
                    prev = tk
                if seg:
                    segments.append(seg)
                for seg in segments:
                    pts, last, gtype = [], -999, None
                    for dd in seg:
                        if gtype is None and ctype and dd.get(ctype):
                            gtype = dd.get(ctype)
                        tk = int(dd[ctk])
                        if tk - last < 4:
                            continue
                        try:
                            xf = float(dd[cx]); yf = float(dd[cy])
                            zf = float(dd[cz]) if cz and dd.get(cz) is not None else 0.0
                        except (TypeError, ValueError):
                            continue
                        if math.isnan(xf) or math.isnan(yf):
                            continue
                        if math.isnan(zf):
                            zf = 0.0
                        last = tk
                        pts.append([round(tk / tickrate, 2), round(xf, 1), round(yf, 1), round(zf, 1)])
                    if len(pts) < 2:
                        continue
                    # keep the throw FLIGHT: stop when it goes stationary (landed), with a
                    # generous time cap as a fallback (4.0s covers long lobs that the <30u
                    # stationary test never trips). The detonation point is appended later so
                    # the projectile stays visible right up to the pop (no disappear gap).
                    t_end = pts[0][0] + 4.0
                    land, still = len(pts), 0
                    for k in range(1, len(pts)):
                        if pts[k][0] > t_end:
                            land = k
                            break
                        d2 = (pts[k][1] - pts[k - 1][1]) ** 2 + (pts[k][2] - pts[k - 1][2]) ** 2
                        if d2 < 900:        # < 30 units of movement = landed
                            still += 1
                            if still >= 2:
                                land = k + 1
                                break
                        else:
                            still = 0
                    pts = pts[:max(2, land)]
                    thr = _sid(seg[0].get(cthr)) if cthr else None
                    grenades.append({"type": _gtype(str(gtype) if gtype else ""),
                                     "thrower": roster.get(thr, -1),
                                     "round": round_num_at(pts[0][0]),
                                     "t0": pts[0][0], "t1": pts[-1][0], "pts": pts})
    print(f"  grenade trajectories: {len(grenades)}")

    # inferno_startburn has no thrower -> match molotov events to molotov trajectories
    mol_lands = [(g["pts"][-1][1], g["pts"][-1][2], g["pts"][-1][0], g["thrower"])
                 for g in grenades if g["type"] == "molotov" and g["pts"]]
    if mol_lands:
        for ev in events:
            if ev.get("type") == "molotov" and ev.get("player", -1) < 0 and ev.get("x") is not None:
                best, bestd = -1, 250.0 ** 2
                for lx, ly, lt, thr in mol_lands:
                    if abs(lt - ev["t"]) > 3.5:
                        continue
                    d = (lx - ev["x"]) ** 2 + (ly - ev["y"]) ** 2
                    if d < bestd:
                        bestd, best = d, thr
                if best >= 0:
                    ev["player"] = best

    # Attach each grenade arc to its DETONATION event (same type, nearest landing, det at/after
    # the projectile's last point). This gives a reliable detonation time+position so:
    #   * the projectile stays visible right up to the pop (no disappear/reappear gap), via det_t
    #     + appending the detonation point to the path (frontend shows the arc until det_t, then
    #     the smoke/molly volume -- keyed off the same event time -- takes over seamlessly), and
    #   * heatmaps / lineup matching use the true detonation xyz, not a trimmed arc endpoint.
    # Also backfills the thrower from the event when the trajectory lacked one.
    ev_by_type = {}
    for ev in events:
        if ev.get("type") in ("smoke", "flash", "he", "molotov") and ev.get("x") is not None:
            ev_by_type.setdefault(ev["type"], []).append(ev)
    for g in grenades:
        if not g["pts"]:
            continue
        lp = g["pts"][-1]
        # Prefer the SAME-THROWER detonation of this type after the throw (reliable even when the
        # trajectory parse is broken/sparse and its endpoint is far off); otherwise fall back to the
        # nearest landing within ~260u. Rank: same-thrower first, then closest to the arc's end.
        best, bestkey = None, None
        for ev in ev_by_type.get(g["type"], []):
            if ev["t"] < g["t0"] - 0.5 or ev["t"] > g["t0"] + 12.0:   # detonation after the throw
                continue
            same_thrower = g["thrower"] >= 0 and ev.get("player", -1) == g["thrower"]
            d = (ev["x"] - lp[1]) ** 2 + (ev["y"] - lp[2]) ** 2
            if not same_thrower and d > 260.0 ** 2:                   # position gate (cross-thrower only)
                continue
            key = (0 if same_thrower else 1, d)
            if bestkey is None or key < bestkey:
                bestkey, best = key, ev
        if best is None:
            continue
        if g["thrower"] < 0 and best.get("player", -1) >= 0:
            g["thrower"] = best["player"]
        bz = best.get("z")
        dz = bz if bz is not None else (lp[3] if len(lp) > 3 else 0.0)
        g["det_t"] = best["t"]
        g["det_pos"] = [round(best["x"], 1), round(best["y"], 1), round(dz, 1)]
        if best.get("end_t") is not None:
            g["end_t"] = best["end_t"]            # active-volume end (smoke 18s / molly 7s)
        # keep the projectile visible until it pops: extend the path to the detonation point
        # when the (trimmed) arc ended before it.
        if best["t"] > lp[0] + 0.05:
            g["pts"].append([best["t"], g["det_pos"][0], g["det_pos"][1], g["det_pos"][2]])
            g["t1"] = best["t"]

    # ---- damage events (player_hurt) -> traces + damage FX -------------------
    damages = []
    hurts = _event(parser, "player_hurt", available)
    if len(hurts):
        a_sid = _col(hurts, "attacker_steamid"); u_sid = _col(hurts, "user_steamid")
        dmgc = _col(hurts, "dmg_health"); hgc = _col(hurts, "hitgroup")
        for i in range(len(hurts)):
            vi = roster.get(_sid(u_sid.iloc[i]))
            if vi is None:
                continue
            ai = roster.get(_sid(a_sid.iloc[i]))
            tick = int(hurts["tick"].iloc[i])
            damages.append({"t": round(tick / tickrate, 2),
                            "atk": ai if ai is not None else -1, "vic": vi,
                            "dmg": int(dmgc.iloc[i] or 0), "hg": str(hgc.iloc[i] or "")})
    print(f"  damage events: {len(damages)}")

    # ---- bullet shots (fire_bullets) -> 3D impact markers (sv_showimpacts style) ----
    # Each shot's view angle + origin lets the 3D view raycast the map mesh to find the impact
    # point client-side (no impact event exists), then fade the marker over ~10s, per enabled player.
    fb = _event(parser, "fire_bullets", available)
    shots_n = 0
    shot_refs = []   # (event, tick, steamid) -> backfill per-shot horizontal speed for counter-strafe %
    if len(fb):
        fb_sid = _col(fb, "user_steamid")
        fb_ox = _col(fb, "origin_x"); fb_oy = _col(fb, "origin_y"); fb_oz = _col(fb, "origin_z")
        fb_pitch = _col(fb, "angles_x"); fb_yaw = _col(fb, "angles_y")
        for i in range(len(fb)):
            sid = _sid(fb_sid.iloc[i])
            player = roster.get(sid, -1)
            ox = _num(fb_ox.iloc[i])
            if player < 0 or ox is None:
                continue
            tick = int(fb["tick"].iloc[i])
            ev = {
                "t": round(tick / tickrate, 2), "type": "shot", "player": player,
                "ox": ox, "oy": _num(fb_oy.iloc[i]), "oz": _num(fb_oz.iloc[i]),
                "pitch": round(_f(fb_pitch.iloc[i]), 1), "yaw": round(_f(fb_yaw.iloc[i]), 1),
            }
            events.append(ev)
            shot_refs.append((ev, tick, sid))
            shots_n += 1
        # per-shot horizontal SPEED for counter-strafe %. Derived from the position delta between the
        # shot tick and the tick before it (dist * tickrate = u/s) -- the raw velocity_* props are
        # spiky/garbage at fire ticks. Position is reliable, and a shot tick is mid-round (no teleport).
        try:
            sticks = {t for _, t, _ in shot_refs}
            need = sorted(sticks | {t - 1 for t in sticks})
            pdf = parser.parse_ticks(["X", "Y"], ticks=need)
            psid = _col(pdf, "steamid"); px = _col(pdf, "X"); py = _col(pdf, "Y")
            pos = {}
            for j in range(len(pdf)):
                pos[(int(pdf["tick"].iloc[j]), _sid(psid.iloc[j]))] = (_f(px.iloc[j]), _f(py.iloc[j]))
            for ev, tk, sid in shot_refs:
                a, b = pos.get((tk, sid)), pos.get((tk - 1, sid))
                if a and b:
                    spd = ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 * tickrate
                    ev["vel"] = round(spd, 1)
        except Exception as e:   # noqa: BLE001  -- velocity is optional; shots still work without it
            print(f"  (shot velocity unavailable: {e})")
        events.sort(key=lambda e: e["t"])   # keep events time-ordered after appending shots
    print(f"  bullet shots: {shots_n}")

    out = {
        "version": SCHEMA_VERSION, "map": map_name, "tickrate": tickrate,
        "sample_rate": sample_rate, "duration": round(max_tick / tickrate, 3),
        "players": players, "frames": frames, "rounds": rounds, "events": events,
        "grenades": grenades, "damages": damages, "loadouts": loadouts,
    }
    print(f"  done: {len(frames)} frames, {len(rounds)} rounds, "
          f"{len(events)} events, {len(players)} players")
    return out


def _num(v):
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return None
        return round(float(v), 1)
    except (ValueError, TypeError):
        return None


def probe(path):
    parser = DemoParser(path)
    print("=== HEADER ===")
    try:
        print(json.dumps(parser.parse_header(), indent=2, default=str))
    except Exception as e:
        print("header error:", e)
    print("\n=== GAME EVENTS ===")
    try:
        print(sorted(parser.list_game_events()))
    except Exception as e:
        print("events error:", e)
    print("\n=== TICK COLUMNS (first 64 ticks) ===")
    tdf, used = safe_parse_ticks(parser, TICK_PROPS, list(range(0, 64)))
    print("used props:", used)
    print("columns:", list(tdf.columns))
    print(tdf.head(12).to_string())
    print("\n=== player_death sample ===")
    try:
        d = parser.parse_event("player_death", player=["X", "Y"])
        print("columns:", list(d.columns))
        print(d.head(5).to_string())
    except Exception as e:
        print("player_death error:", e)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    if args[0] == "--probe":
        probe(args[1])
    else:
        src = args[0]
        dst = args[1] if len(args) > 1 else src.rsplit(".", 1)[0] + ".json"
        data = parse_demo(src)
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(data, f)
        print("wrote", dst)
