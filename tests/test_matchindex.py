"""Tests for matchindex (list_matches / player_trends / all_players)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matchindex   # noqa: E402


def _match(s1_stats, s2_stats=None):
    s2_stats = s2_stats or {"kills": 10, "deaths": 15, "kd": 0.67, "adr": 60.0,
                            "kast": 60.0, "hltv": 0.8, "open_wr": 40.0,
                            "traded_pct": 10.0, "udr": 5.0}
    return {
        "map": "de_dust2",
        "duration": 1800.0,
        "rounds": [{"number": 1, "score_ct": 7, "score_t": 5},
                   {"number": 2, "score_ct": 13, "score_t": 11}],
        "analytics": {"n_rounds": 24, "players": [
            dict(steamid="S1", name="Zalio", **s1_stats),
            dict(steamid="S2", name="Rival", **s2_stats),
        ]},
    }


def _write(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)


def _make_cache(tmp_path):
    # S1 has clearly different stats in each match (improving)
    _write(str(tmp_path / "m1.json"), _match(
        {"kills": 15, "deaths": 20, "kd": 0.75, "adr": 70.0, "kast": 65.0,
         "hltv": 0.90, "open_wr": 45.0, "traded_pct": 12.0, "udr": 6.0}))
    _write(str(tmp_path / "m2.json"), _match(
        {"kills": 25, "deaths": 15, "kd": 1.67, "adr": 100.0, "kast": 80.0,
         "hltv": 1.40, "open_wr": 60.0, "traded_pct": 20.0, "udr": 10.0}))
    # meta sidecars give a deterministic created_at ordering (m2 newer)
    _write(str(tmp_path / "m1.meta.json"), {"created_at": "2026-06-01T10:00:00"})
    _write(str(tmp_path / "m2.meta.json"), {"created_at": "2026-06-10T10:00:00"})
    # must be skipped: sample.json (by name) + a stray .meta.json with no match
    _write(str(tmp_path / "sample.json"), _match(
        {"kills": 99, "deaths": 1, "kd": 99.0, "adr": 200.0, "kast": 100.0,
         "hltv": 3.0, "open_wr": 100.0, "traded_pct": 50.0, "udr": 99.0}))
    _write(str(tmp_path / "stray.meta.json"), {"created_at": "2026-06-09T00:00:00"})


def test_list_matches_excludes_sample_and_meta(tmp_path):
    _make_cache(tmp_path)
    ms = matchindex.list_matches(str(tmp_path))
    assert len(ms) == 2                       # sample.json + *.meta.json excluded
    keys = [m["key"] for m in ms]
    assert "sample" not in keys and "m1.meta" not in keys and "stray.meta" not in keys
    assert ms[0]["key"] == "m2"               # sorted created_at DESCENDING
    for m in ms:
        assert m["players"] and len(m["players"]) == 2
        assert m["map"] == "de_dust2"
        assert m["rounds"] == 24              # from analytics.n_rounds
        assert m["score"] == "13-11"          # from rounds[-1]
        assert m["duration"] == 1800.0
        p = m["players"][0]
        for f in ("steamid", "name", "kills", "deaths", "kd", "adr", "kast",
                  "hltv", "open_wr", "traded_pct", "udr"):
            assert f in p


def test_list_matches_dedupes_library_copies(tmp_path):
    # same match cached twice: content cache (<sha16>.json) + library copy (lib_<full>.json)
    m = _match({"kills": 15, "deaths": 20, "kd": 0.75, "adr": 70.0, "kast": 65.0,
                "hltv": 0.90, "open_wr": 45.0, "traded_pct": 12.0, "udr": 6.0})
    m["source_sha1"] = "ABC123"
    _write(str(tmp_path / "abc123.json"), m)
    _write(str(tmp_path / "lib_ABC123FULL.json"), m)
    ms = matchindex.list_matches(str(tmp_path))
    assert len(ms) == 1                           # de-duped by source_sha1 (was double-counted)
    assert ms[0]["key"] == "abc123"               # prefers the canonical (non-lib_) key
    assert matchindex.all_players(str(tmp_path))[0]["n_matches"] == 1   # counts not doubled


def test_player_trends_two_matches(tmp_path):
    _make_cache(tmp_path)
    t = matchindex.player_trends("S1", str(tmp_path))
    assert t["steamid"] == "S1"
    assert t["name"] == "Zalio"
    assert t["n_matches"] == 2
    assert len(t["series"]) == 2
    # series sorted created_at ASCENDING (m1 then m2)
    assert [e["key"] for e in t["series"]] == ["m1", "m2"]
    assert t["series"][0]["created_at"] < t["series"][1]["created_at"]
    for key in ("hltv", "adr", "kast", "open_wr", "traded_pct", "udr", "kd"):
        assert key in t["averages"]
    # averages: hltv (0.90+1.40)/2 = 1.15 (2dp); adr (70+100)/2 = 85.0 (1dp)
    assert t["averages"]["hltv"] == 1.15
    assert t["averages"]["adr"] == 85.0
    # trend present (n>=2) and positive for an improving player
    assert set(t["trend"]) == {"hltv", "adr", "kast", "open_wr", "traded_pct", "udr"}
    assert t["trend"]["hltv"] == 0.5          # 1.40 - 0.90
    assert t["trend"]["adr"] == 30.0          # 100 - 70


def test_player_trends_single_match_no_trend(tmp_path):
    _write(str(tmp_path / "only.json"), _match(
        {"kills": 15, "deaths": 20, "kd": 0.75, "adr": 70.0, "kast": 65.0,
         "hltv": 0.90, "open_wr": 45.0, "traded_pct": 12.0, "udr": 6.0}))
    t = matchindex.player_trends("S1", str(tmp_path))
    assert t["n_matches"] == 1
    assert t["trend"] == {}                    # fewer than 2 matches


def test_all_players(tmp_path):
    _make_cache(tmp_path)
    ps = matchindex.all_players(str(tmp_path))
    by_id = {p["steamid"]: p for p in ps}
    assert "S1" in by_id and by_id["S1"]["n_matches"] == 2
    assert by_id["S1"]["name"] == "Zalio"
    assert "S2" in by_id and by_id["S2"]["n_matches"] == 2


def test_robust_on_corrupt_and_missing(tmp_path):
    _make_cache(tmp_path)
    # corrupt json + a valid-shaped json lacking analytics.players must not raise
    with open(str(tmp_path / "broken.json"), "w", encoding="utf-8") as fh:
        fh.write("{not valid json")
    _write(str(tmp_path / "noplayers.json"),
           {"map": "de_x", "rounds": [], "analytics": {"players": []}})
    ms = matchindex.list_matches(str(tmp_path))
    assert len(ms) == 2                         # still only m1 + m2
    # unknown player + nonexistent dir return safe empties
    assert matchindex.player_trends("NOPE", str(tmp_path))["n_matches"] == 0
    assert matchindex.list_matches(str(tmp_path / "does_not_exist")) == []
