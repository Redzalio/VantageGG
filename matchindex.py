"""Index cached parsed demos and compute per-player trends across matches.

Stdlib-only. A cached match is a cache/<key>.json whose
data["analytics"]["players"] is a non-empty list. Helps answer "am I improving?"
"""
import os
import json
import glob
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "cache")

# stats tracked in trend series / averages
_STATS = ["hltv", "adr", "kast", "open_wr", "traded_pct", "udr"]
_PLAYER_FIELDS = ["kills", "deaths", "kd", "adr", "kast", "hltv",
                  "open_wr", "traded_pct", "udr"]


def _num(v):
    """Coerce to float, defaulting missing/bad values to 0."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _load_match(path):
    """Safely load a cache file; return data dict only if it's a valid match.

    Returns None for .meta.json, sample.json, unreadable files, or any json
    lacking a non-empty analytics.players list.
    """
    name = os.path.basename(path)
    if name.endswith(".meta.json") or name == "sample.json":
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    players = (data.get("analytics") or {}).get("players")
    if not isinstance(players, list) or not players:
        return None
    return data


def _created_at(path, key):
    """Prefer sibling <key>.meta.json created_at; else file mtime."""
    meta_path = os.path.join(os.path.dirname(path), key + ".meta.json")
    try:
        with open(meta_path, encoding="utf-8") as fh:
            ca = json.load(fh).get("created_at")
        if ca:
            return ca
    except Exception:
        pass
    return datetime.datetime.fromtimestamp(
        os.path.getmtime(path)).isoformat(timespec="seconds")


def _player_row(p):
    row = {"steamid": p.get("steamid"), "name": p.get("name")}
    for f in _PLAYER_FIELDS:
        row[f] = _num(p.get(f))
    return row


def _score(data):
    rounds = data.get("rounds") or []
    if not rounds:
        return None
    last = rounds[-1]
    if "score_ct" in last and "score_t" in last:
        return "{}-{}".format(last.get("score_ct"), last.get("score_t"))
    return None


def list_matches(cache_dir=CACHE_DIR):
    """All cached matches as summaries, de-duped by source_sha1, newest-first.

    The content cache (<sha16>.json) and its library copy (lib_<fullsha>.json) point at the
    SAME match; counting both doubled every cross-match number (trends, player match counts).
    De-dupe by source_sha1, preferring the canonical (non-lib_) key for stable ids.
    """
    by_sha = {}
    for path in glob.glob(os.path.join(cache_dir, "*.json")):
        data = _load_match(path)
        if data is None:
            continue
        a = data.get("analytics") or {}
        key = os.path.splitext(os.path.basename(path))[0]
        sha = data.get("source_sha1") or key
        row = {
            "key": key,
            "map": data.get("map"),
            "rounds": a.get("n_rounds") or len(data.get("rounds") or []),
            "created_at": _created_at(path, key),
            "duration": data.get("duration"),
            "score": _score(data),
            "players": [_player_row(p) for p in a.get("players") or []],
        }
        prev = by_sha.get(sha)
        if prev is None or (prev["key"].startswith("lib_") and not key.startswith("lib_")):
            by_sha[sha] = row
    out = list(by_sha.values())
    out.sort(key=lambda m: m["created_at"] or "", reverse=True)
    return out


def _round(stat, val):
    return round(val, 2) if stat in ("hltv", "kd") else round(val, 1)


def player_trends(steamid, cache_dir=CACHE_DIR):
    """Trend series + averages + first/second-half delta for one player."""
    series = []
    for m in list_matches(cache_dir):
        pl = next((p for p in m["players"] if p.get("steamid") == steamid), None)
        if pl is None:
            continue
        entry = {"key": m["key"], "map": m["map"], "created_at": m["created_at"],
                 "name": pl.get("name"), "kd": pl.get("kd", 0.0)}
        for s in _STATS:
            entry[s] = pl.get(s, 0.0)
        series.append(entry)
    series.sort(key=lambda e: e["created_at"] or "")

    name = series[-1]["name"] if series else None
    for e in series:
        e.pop("name", None)

    n = len(series)
    averages = {}
    for s in _STATS + ["kd"]:
        averages[s] = _round(s, sum(e[s] for e in series) / n) if n else 0.0

    trend = {}
    if n >= 2:
        half = n // 2
        first, second = series[:half], series[half:]
        for s in _STATS:
            fm = sum(e[s] for e in first) / len(first)
            sm = sum(e[s] for e in second) / len(second)
            trend[s] = _round(s, sm - fm)

    return {"steamid": steamid, "name": name, "n_matches": n,
            "series": series, "averages": averages, "trend": trend}


def all_players(cache_dir=CACHE_DIR):
    """Deduped players across all matches, by n_matches desc then name."""
    seen = {}
    for m in list_matches(cache_dir):
        for p in m["players"]:
            sid = p.get("steamid")
            if not sid:
                continue
            rec = seen.setdefault(sid, {"steamid": sid, "name": p.get("name"),
                                        "n_matches": 0})
            rec["n_matches"] += 1
            if p.get("name"):
                rec["name"] = p.get("name")
    out = list(seen.values())
    out.sort(key=lambda r: (-r["n_matches"], (r["name"] or "").lower()))
    return out


if __name__ == "__main__":
    ms = list_matches()
    print("Cached matches: {}".format(len(ms)))
    for m in ms:
        print("  {} {} {} rounds={} score={} players={}".format(
            m["key"], m["created_at"], m["map"], m["rounds"], m["score"],
            len(m["players"])))
    print("\nPlayers:")
    for p in all_players():
        print("  {} {} ({} matches)".format(
            p["steamid"], p["name"], p["n_matches"]))
    top = all_players()
    if top:
        t = player_trends(top[0]["steamid"])
        print("\nTrends for {} ({} matches):".format(t["name"], t["n_matches"]))
        print("  averages:", t["averages"])
        print("  trend:", t["trend"])
