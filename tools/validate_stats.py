"""Validate the CS2 Demo Player's computed analytics against the demo's OWN
official scoreboard (an independent ground truth).

Usage:
    python tools/validate_stats.py <demo.dem> <cache.json>

The demo's engine tracks cumulative per-player totals (kills_total, deaths_total,
assists_total, damage_total). We read those at the LAST tick of the match and
compare them to data["analytics"]["players"] from the cache. Nothing here is
written back -- this is a read-only audit tool.
"""
import json
import sys

from demoparser2 import DemoParser

# demoparser2 0.41.3 official-scoreboard prop names (verified via probe).
OFFICIAL_PROPS = ["kills_total", "deaths_total", "assists_total", "damage_total"]


def get_official(demo_path):
    """Return ({steamid:str -> {kills,deaths,assists,damage}}, set_of_props_found)."""
    p = DemoParser(demo_path)
    found = []
    # Probe each prop independently so one missing prop never kills the rest.
    for prop in OFFICIAL_PROPS:
        try:
            df = p.parse_ticks([prop])
            if prop in df.columns and df[prop].notna().any():
                found.append(prop)
        except Exception:
            pass
    if not found:
        return {}, set()
    df = p.parse_ticks(found)
    max_t = df["tick"].max()
    last = df[df["tick"] == max_t]
    out = {}
    for row in last.itertuples(index=False):
        d = row._asdict()
        sid = d.get("steamid")
        if sid is None:
            continue
        sid = str(int(sid)) if not isinstance(sid, str) else sid
        rec = out.setdefault(sid, {})
        for prop in found:
            v = d.get(prop)
            if v is not None and v == v:  # not NaN
                rec[prop] = v
    return out, set(found)


def load_mine(cache_path):
    with open(cache_path, encoding="utf-8") as fh:
        data = json.load(fh)
    a = data.get("analytics", {})
    n_rounds = a.get("n_rounds") or len(a.get("rounds") or []) or None
    players = {}
    for p in a.get("players", []):
        sid = str(p.get("steamid"))
        players[sid] = p
    return players, n_rounds


def fmt(v):
    return "--" if v is None else (f"{v:g}" if isinstance(v, float) else str(v))


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    demo, cache = sys.argv[1], sys.argv[2]
    print(f"Demo : {demo}\nCache: {cache}\n")

    official, props = get_official(demo)
    mine, n_rounds = load_mine(cache)

    have_dmg = "damage_total" in props
    have_k = "kills_total" in props
    have_d = "deaths_total" in props
    have_a = "assists_total" in props
    print("Official props available:", sorted(props) or "(NONE)")
    if not props:
        print("\n!! demoparser2 exposed no official-scoreboard props for this demo.")
        print("   Cannot build a comparison. Aborting (no fabricated numbers).")
        sys.exit(2)
    print(f"Rounds (for ADR denominator): {n_rounds}\n")

    hdr = (f"{'name':<24} | {'my K/D/A':>11} | {'off K/D/A':>11} | "
           f"{'dK':>3} {'dD':>3} {'dA':>3} | {'myADR':>6} {'offADR':>6} {'dADR':>6} | note")
    print(hdr)
    print("-" * len(hdr))

    exact_kda = 0
    counted_kda = 0
    worst_adr = (0.0, None)  # (abs delta, name)
    sum_dk = sum_dd = sum_da = 0
    n_dk = 0
    adr_deltas = []

    for sid, p in sorted(mine.items(), key=lambda kv: -(kv[1].get("kills") or 0)):
        name = (p.get("name") or "?")[:24]
        mk, md, ma = p.get("kills"), p.get("deaths"), p.get("assists")
        my_adr = p.get("adr")
        o = official.get(sid, {})
        ok = int(o["kills_total"]) if have_k and "kills_total" in o else None
        od = int(o["deaths_total"]) if have_d and "deaths_total" in o else None
        oa = int(o["assists_total"]) if have_a and "assists_total" in o else None
        odmg = o.get("damage_total") if have_dmg else None
        off_adr = round(odmg / n_rounds, 1) if (odmg is not None and n_rounds) else None

        dk = (mk - ok) if (mk is not None and ok is not None) else None
        dd = (md - od) if (md is not None and od is not None) else None
        da = (ma - oa) if (ma is not None and oa is not None) else None
        dadr = round(my_adr - off_adr, 1) if (my_adr is not None and off_adr is not None) else None

        notes = []
        if not o:
            notes.append("NO OFFICIAL ROW (sub/bot?)")
        else:
            # K/D/A exactness
            if None not in (dk, dd, da):
                counted_kda += 1
                sum_dk += dk; sum_dd += dd; sum_da += da; n_dk += 1
                if dk == 0 and dd == 0 and da == 0:
                    exact_kda += 1
                else:
                    bad = []
                    for label, dv in (("K", dk), ("D", dd), ("A", da)):
                        if dv != 0:
                            tag = "minor" if abs(dv) <= 2 else "MISMATCH"
                            bad.append(f"{label}{dv:+d}({tag})")
                    notes.append(" ".join(bad))
            # ADR closeness
            if dadr is not None:
                adr_deltas.append((dadr, name))
                tol = max(5.0, 0.05 * (off_adr or 0))
                if abs(dadr) > tol:
                    notes.append(f"ADR off {dadr:+.1f} (>{tol:.1f})")
                if abs(dadr) > abs(worst_adr[0]):
                    worst_adr = (dadr, name)

        print(f"{name:<24} | {fmt(mk)+'/'+fmt(md)+'/'+fmt(ma):>11} | "
              f"{(fmt(ok)+'/'+fmt(od)+'/'+fmt(oa)):>11} | "
              f"{fmt(dk):>3} {fmt(dd):>3} {fmt(da):>3} | "
              f"{fmt(my_adr):>6} {fmt(off_adr):>6} {fmt(dadr):>6} | {'; '.join(notes)}")

    print("\n" + "=" * 60 + "\nSUMMARY")
    print(f"  Players with an official row compared: {counted_kda}")
    print(f"  Exact K/D/A matches: {exact_kda}/{counted_kda}")
    if n_dk:
        print(f"  Aggregate K/D/A delta (mine - official): "
              f"dK={sum_dk:+d}  dD={sum_dd:+d}  dA={sum_da:+d}")
    if adr_deltas:
        mean = sum(d for d, _ in adr_deltas) / len(adr_deltas)
        worst = max(adr_deltas, key=lambda x: abs(x[0]))
        print(f"  Mean ADR delta (mine - official): {mean:+.1f}")
        print(f"  Worst ADR delta: {worst[0]:+.1f} ({worst[1]})")
    else:
        print("  ADR: damage_total prop unavailable -- ADR not compared.")


if __name__ == "__main__":
    main()
