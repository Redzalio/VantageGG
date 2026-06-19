"""Compact per-match stats retained after a replay is deleted (the 'free storage, keep stats' flow).

A tiny plain-text file (a few hundred bytes) holding ONLY the numbers long-term analytics need --
match summary + per-player aggregates. Deliberately NOT a replay/cache file and NOT a JSON dump:
no frames, no tick data, no grenade paths, no positions. Stdlib only.

Format (line-based, pipe-delimited player rows)::

    v=1
    sha=<source_sha1>
    map=de_dust2
    date=2026-06-18T16:14:00
    rounds=24
    score=13-11
    p=<steamid>|<name>|kills|deaths|kd|adr|kast|hltv|open_wr|traded_pct|udr
    ...

The live app reads stats from the SQLite index (fast); this .txt is the durable, portable,
human-inspectable record + a rebuild source if the index is ever lost.
"""
import os

VERSION = 1
# order MUST match _PLAYER_FIELDS below and the demo_players columns used by trends
PLAYER_FIELDS = ["kills", "deaths", "kd", "adr", "kast", "hltv", "open_wr", "traded_pct", "udr"]


def stats_dir(base):
    return os.path.join(base, "stats")


def path_for(base, sha):
    return os.path.join(stats_dir(base), str(sha) + ".txt")


def _clean(s):
    return str(s if s is not None else "").replace("|", " ").replace("\n", " ").replace("\r", " ")


def write(base, sha, match):
    """Write the compact .txt for one match. `match` = {map, rounds, score, date/created_at,
    players:[{steamid,name, <PLAYER_FIELDS>}]}. Returns the file path. Tiny + atomic-ish."""
    d = stats_dir(base)
    os.makedirs(d, exist_ok=True)
    lines = [
        "v=%d" % VERSION,
        "sha=%s" % _clean(sha),
        "map=%s" % _clean(match.get("map")),
        "date=%s" % _clean(match.get("date") or match.get("created_at")),
        "rounds=%s" % _clean(match.get("rounds") or 0),
        "score=%s" % _clean(match.get("score")),
    ]
    for p in (match.get("players") or []):
        vals = "|".join(str(p.get(f, 0)) for f in PLAYER_FIELDS)
        lines.append("p=%s|%s|%s" % (_clean(p.get("steamid")), _clean(p.get("name")), vals))
    path = path_for(base, sha)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)
    return path


def read(base, sha):
    """Parse a compact .txt back into a match dict, or None if missing/unreadable."""
    path = path_for(base, sha)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return None
    match = {"sha": str(sha), "players": []}
    for line in raw.splitlines():
        if line.startswith("p="):
            parts = line[2:].split("|")
            if len(parts) < 2 + len(PLAYER_FIELDS):
                continue
            steamid, name = parts[0], parts[1]
            pl = {"steamid": steamid, "name": name}
            for i, fld in enumerate(PLAYER_FIELDS):
                try:
                    pl[fld] = float(parts[2 + i])
                except ValueError:
                    pl[fld] = 0.0
            match["players"].append(pl)
        elif "=" in line:
            k, v = line.split("=", 1)
            match[k] = v
    return match


def exists(base, sha):
    return os.path.isfile(path_for(base, sha))
