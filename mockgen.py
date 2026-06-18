"""
Generate a realistic MOCK parsed-demo JSON matching the schema the real parser
emits, so the frontend can be built/tested without a real .dem file.

Output: cache/sample.json   (map = de_mirage)

Schema (SCHEMA_VERSION, imported from schema.py) -- see also parser.py which must emit the
SAME shape. The mock stamps the CURRENT version so a regenerated sample is never replay-stale:
{
  "version": SCHEMA_VERSION,
  "map": "de_mirage",
  "tickrate": 64,
  "sample_rate": 8,            # frames per second of position data
  "duration": <seconds>,
  "players": [ {steamid,name,color} ],          # stable index == array position
  "frames": [ {                                  # evenly spaced at 1/sample_rate
      "t": <sec>, "round": <int>,
      "players": [ {x,y,z,yaw,hp,armor,alive,team,weapon,flash,scoped} | null ],
      "bomb": {x,y,z,state,carrier}             # state: carried|dropped|planted|defused|exploded
  } ],
  "rounds": [ {number,start_t,freeze_end_t,end_t,winner,reason,score_ct,score_t} ],
  "events": [ {t,type,...} ]                     # kill | bomb_planted | bomb_defused | smoke | flash | he | molotov
}
"""
import json
import math
import os
import random

from schema import SCHEMA_VERSION   # keep the mock in lock-step with the real parser schema

HERE = os.path.dirname(os.path.abspath(__file__))
random.seed(42)

MAP = "de_mirage"
POS_X, POS_Y, SCALE, SIZE = -3230.0, 1713.0, 5.0, 1024
SR = 8  # sample rate (frames/sec)

# world bounds derived from calibration
WX0, WX1 = POS_X, POS_X + SIZE * SCALE          # -3230 .. 1890
WY0, WY1 = POS_Y - SIZE * SCALE, POS_Y          # -3407 .. 1713

def norm_to_world(nx, ny):
    px, py = nx * SIZE, ny * SIZE
    return (POS_X + px * SCALE, POS_Y - py * SCALE)

CT_SPAWN = norm_to_world(0.28, 0.70)
T_SPAWN  = norm_to_world(0.87, 0.36)
SITE_A   = norm_to_world(0.54, 0.76)
SITE_B   = norm_to_world(0.23, 0.28)
MID      = norm_to_world(0.50, 0.50)

CT_NAMES = ["dev1ce", "ropz", "b1t", "rain", "Twistzz"]
T_NAMES  = ["s1mple", "ZywOo", "NiKo", "m0NESY", "donk"]

WEAPONS_CT = ["ak47", "m4a1", "m4a1_silencer", "awp", "deagle"]
WEAPONS_T  = ["ak47", "ak47", "awp", "deagle", "ak47"]


def make_players():
    players = []
    for i, name in enumerate(CT_NAMES):
        players.append({"steamid": f"7656119800000{i:04d}", "name": name,
                        "team": 3, "color": None})
    for i, name in enumerate(T_NAMES):
        players.append({"steamid": f"7656119900000{i:04d}", "name": name,
                        "team": 2, "color": None})
    return players


def clampw(x, lo, hi):
    return max(lo, min(hi, x))


class Mover:
    """Smooth wander toward a target, retargeting periodically."""
    def __init__(self, x, y, anchor):
        self.x, self.y = x, y
        self.anchor = anchor
        self.tx, self.ty = x, y
        self.yaw = random.uniform(0, 360)
        self.retarget()

    def retarget(self):
        ax, ay = self.anchor
        self.tx = clampw(ax + random.uniform(-900, 900), WX0 + 100, WX1 - 100)
        self.ty = clampw(ay + random.uniform(-900, 900), WY0 + 100, WY1 - 100)
        self.timer = random.uniform(1.5, 4.0)

    def step(self, dt):
        self.timer -= dt
        if self.timer <= 0:
            self.retarget()
        dx, dy = self.tx - self.x, self.ty - self.y
        d = math.hypot(dx, dy)
        if d > 1:
            spd = 250.0  # units/sec
            step = min(spd * dt, d)
            self.x += dx / d * step
            self.y += dy / d * step
            target_yaw = math.degrees(math.atan2(dy, dx))
            # ease yaw
            diff = (target_yaw - self.yaw + 540) % 360 - 180
            self.yaw += diff * min(1.0, dt * 6)


def build_data():
    """Construct the mock demo dict (no file I/O) -- importable + testable."""
    players = make_players()
    n = len(players)
    frames = []
    rounds = []
    events = []
    t = 0.0
    dt = 1.0 / SR
    score_ct, score_t = 0, 0
    n_rounds = 4

    for rnd in range(1, n_rounds + 1):
        # per-round setup
        movers = []
        hp = [100] * n
        armor = [100] * n
        alive = [True] * n
        weapon = [None] * n
        flash = [0.0] * n
        for i, p in enumerate(players):
            if p["team"] == 3:
                anchor = CT_SPAWN
                weapon[i] = random.choice(WEAPONS_CT)
            else:
                anchor = T_SPAWN
                weapon[i] = random.choice(WEAPONS_T)
            sx = clampw(anchor[0] + random.uniform(-150, 150), WX0, WX1)
            sy = clampw(anchor[1] + random.uniform(-150, 150), WY0, WY1)
            movers.append(Mover(sx, sy, anchor))

        round_start = t
        freeze = 5.0
        action = 35.0
        round_dur = freeze + action

        # plan kills: random victims at random times during action
        kill_plan = []
        n_kills = random.randint(4, 7)
        killers = list(range(n))
        used_victims = set()
        for _ in range(n_kills):
            victim = random.choice([i for i in range(n) if i not in used_victims])
            used_victims.add(victim)
            attacker = random.choice([i for i in range(n)
                                      if players[i]["team"] != players[victim]["team"]])
            ktime = round_start + freeze + random.uniform(2, action - 2)
            kill_plan.append((ktime, attacker, victim))
        kill_plan.sort()

        # bomb plan (round 1 & 3: plant at A; defuse round1, explode round3)
        bomb_carrier = next(i for i, p in enumerate(players) if p["team"] == 2)
        bomb_state = "carried"
        bomb_pos = list(movers[bomb_carrier].step(0) or (movers[bomb_carrier].x, movers[bomb_carrier].y))
        planted = False
        plant_time = round_start + freeze + 18 if rnd in (1, 3) else None
        if plant_time:
            events.append({"t": round(plant_time, 2), "type": "bomb_planted",
                           "site": "A", "player": bomb_carrier,
                           "x": round(SITE_A[0], 1), "y": round(SITE_A[1], 1)})
        # a couple of utility nades in each round
        events.append({"t": round(round_start + freeze + 6, 2), "type": "smoke",
                       "x": round(MID[0] + random.uniform(-200, 200), 1),
                       "y": round(MID[1] + random.uniform(-200, 200), 1),
                       "z": 0.0, "end_t": round(round_start + freeze + 6 + 18, 2)})
        events.append({"t": round(round_start + freeze + 8, 2), "type": "flash",
                       "x": round(SITE_A[0] + random.uniform(-150, 150), 1),
                       "y": round(SITE_A[1] + random.uniform(-150, 150), 1)})
        if rnd % 2 == 0:
            events.append({"t": round(round_start + freeze + 10, 2), "type": "molotov",
                           "x": round(SITE_B[0], 1), "y": round(SITE_B[1], 1),
                           "z": 0.0, "end_t": round(round_start + freeze + 10 + 7, 2)})

        ki = 0
        local_t = 0.0
        while local_t < round_dur:
            in_freeze = local_t < freeze
            # process kills due
            while ki < len(kill_plan) and kill_plan[ki][0] <= t:
                _, atk, vic = kill_plan[ki]
                if alive[vic]:
                    alive[vic] = False
                    hp[vic] = 0
                    events.append({
                        "t": round(t, 2), "type": "kill",
                        "attacker": atk, "victim": vic,
                        "assister": None,
                        "weapon": weapon[atk] or "ak47",
                        "headshot": random.random() < 0.45,
                        "ax": round(movers[atk].x, 1), "ay": round(movers[atk].y, 1),
                        "vx": round(movers[vic].x, 1), "vy": round(movers[vic].y, 1),
                    })
                ki += 1

            # movement (frozen during freeze time)
            for i in range(n):
                if alive[i] and not in_freeze:
                    movers[i].step(dt)
                # decay flash
                if flash[i] > 0:
                    flash[i] = max(0.0, flash[i] - dt * 0.7)

            # bomb logic
            if plant_time and not planted and t >= plant_time:
                planted = True
                bomb_state = "planted"
                if alive[bomb_carrier]:
                    movers[bomb_carrier].x, movers[bomb_carrier].y = SITE_A
            if not planted:
                if alive[bomb_carrier]:
                    bomb_pos = [movers[bomb_carrier].x, movers[bomb_carrier].y]
                    bomb_state = "carried"
            else:
                bomb_pos = list(SITE_A)

            # build frame players
            fp = []
            for i in range(n):
                fp.append({
                    "x": round(movers[i].x, 1),
                    "y": round(movers[i].y, 1),
                    "z": 0.0,
                    "yaw": round(movers[i].yaw, 1),
                    "hp": hp[i] if alive[i] else 0,
                    "armor": armor[i],
                    "alive": 1 if alive[i] else 0,
                    "team": players[i]["team"],
                    "weapon": weapon[i] if alive[i] else "",
                    "flash": round(flash[i], 2),
                    "scoped": 0,
                })
            frames.append({
                "t": round(t, 3),
                "round": rnd,
                "players": fp,
                "bomb": {"x": round(bomb_pos[0], 1), "y": round(bomb_pos[1], 1),
                         "z": 0.0, "state": bomb_state,
                         "carrier": bomb_carrier if bomb_state == "carried" else -1},
            })
            t += dt
            local_t += dt

        # round result
        ct_alive = sum(1 for i in range(n) if players[i]["team"] == 3 and alive[i])
        t_alive = sum(1 for i in range(n) if players[i]["team"] == 2 and alive[i])
        if rnd in (1, 3):
            # bomb scenario
            if rnd == 1:
                winner, reason = "CT", "bomb_defused"
                events.append({"t": round(round_start + freeze + 30, 2),
                               "type": "bomb_defused",
                               "player": next(i for i in range(n) if players[i]["team"] == 3)})
            else:
                winner, reason = "T", "target_bombed"
        else:
            winner = "CT" if ct_alive >= t_alive else "T"
            reason = "elimination"
        if winner == "CT":
            score_ct += 1
        else:
            score_t += 1
        rounds.append({
            "number": rnd, "start_t": round(round_start, 2),
            "freeze_end_t": round(round_start + freeze, 2),
            "end_t": round(t, 2), "winner": winner, "reason": reason,
            "score_ct": score_ct, "score_t": score_t,
        })

    return {
        "version": SCHEMA_VERSION, "map": MAP, "tickrate": 64, "sample_rate": SR,
        "duration": round(t, 3),
        "mock": True,            # synthetic fallback sample (no real .dem behind it)
        "analytics": None,       # mock has no parser -> no coaching; UI shows the honest empty state
        "players": players, "frames": frames, "rounds": rounds,
        "events": sorted(events, key=lambda e: e["t"]),
        "loadouts": {},   # real loadout timeline only comes from a parsed demo
    }


def build(path=None):
    """Build the mock and write it (default: cache/sample.json). Returns the data dict."""
    out = build_data()
    path = path or os.path.join(HERE, "cache", "sample.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f)
    print(f"wrote {path}")
    print(f"  map={out['map']} dur={out['duration']}s frames={len(out['frames'])} "
          f"rounds={len(out['rounds'])} events={len(out['events'])} players={len(out['players'])}")
    return out


if __name__ == "__main__":
    build()
