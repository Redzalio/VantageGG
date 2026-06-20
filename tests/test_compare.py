"""Tests for compare.py -- the player-vs-player comparison helper.

build_comparison is tested thoroughly as a pure function (winners per metric,
better=low path, deltas, na handling, summary). compare_from_trends gets a small
temp-DB integration test that seeds two players and compares their averaged
stats. compare_self_periods is tested on a synthetic per-match series.

Temp DB only (tmp_path); never the real cs2dp.sqlite.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import compare  # noqa: E402
import db        # noqa: E402


# ---- pure build_comparison --------------------------------------------------
def test_winners_deltas_and_summary():
    a = {"hltv": 1.20, "adr": 85.0, "kast": 72.0, "kd": 1.30,
         "open_wr": 55.0, "traded_pct": 22.0, "udr": 18.0, "n_matches": 10}
    b = {"hltv": 1.00, "adr": 90.0, "kast": 72.0, "kd": 1.10,
         "open_wr": 50.0, "traded_pct": 20.0, "udr": 25.0, "n_matches": 7}
    res = compare.build_comparison(a, b, label_a="Alice", label_b="Bob")

    by_key = {m["key"]: m for m in res["metrics"]}
    assert by_key["hltv"]["winner"] == "a"      # 1.20 > 1.00
    assert by_key["adr"]["winner"] == "b"       # 85 < 90, higher better -> B
    assert by_key["kast"]["winner"] == "tie"    # equal
    assert by_key["udr"]["winner"] == "b"       # 18 < 25

    # deltas are a_val - b_val, rounded per metric precision
    assert by_key["hltv"]["delta"] == 0.20      # rating -> 2dp
    assert by_key["adr"]["delta"] == -5.0       # 85 - 90
    assert by_key["kast"]["delta"] == 0.0

    # labels & match counts carried through
    assert res["a"]["label"] == "Alice" and res["b"]["label"] == "Bob"
    assert res["a"]["matches"] == 10 and res["b"]["matches"] == 7
    assert res["summary"]                      # non-empty
    assert "Alice" in res["summary"] or "Bob" in res["summary"]


def test_better_low_path_flips_winner():
    # Synthetic descriptor where SMALLER wins (e.g. a hypothetical deaths/round).
    metrics = [{"key": "dpr", "label": "Deaths/Rd", "better": "low", "unit": ""}]
    res = compare.build_comparison({"dpr": 0.60}, {"dpr": 0.75}, metrics=metrics)
    m = res["metrics"][0]
    assert m["winner"] == "a"        # 0.60 < 0.75 and lower is better -> A wins
    assert m["delta"] == -0.2        # a_val - b_val, default 1dp precision
    # and the reverse
    res2 = compare.build_comparison({"dpr": 0.90}, {"dpr": 0.75}, metrics=metrics)
    assert res2["metrics"][0]["winner"] == "b"


def test_na_handling_for_missing_and_bad_values():
    a = {"hltv": 1.10}                                  # adr/kast/... missing
    b = {"hltv": None, "adr": "oops", "kast": float("nan")}
    res = compare.build_comparison(a, b)
    by_key = {m["key"]: m for m in res["metrics"]}
    # hltv: A has a value, B is None -> na, no delta, no crash
    assert by_key["hltv"]["winner"] == "na"
    assert by_key["hltv"]["delta"] is None
    assert by_key["adr"]["winner"] == "na"             # both missing
    assert by_key["kast"]["winner"] == "na"            # NaN treated as missing
    assert res["summary"]                              # never empty


def test_never_raises_on_garbage_input():
    # Non-dict args must not throw; everything becomes na.
    res = compare.build_comparison(None, "not a dict")
    assert all(m["winner"] == "na" for m in res["metrics"])
    assert res["summary"]


def test_all_default_metric_keys_present():
    # Guards against drift from player_trends()'s averages keys.
    keys = {m["key"] for m in compare.METRICS}
    assert keys == {"hltv", "adr", "kast", "kd", "open_wr", "traded_pct", "udr"}


# ---- compare_self_periods (per-match series) --------------------------------
def test_self_periods_splits_and_compares():
    # 6 matches OLD->NEW; rating climbs. split_n=3 -> recent[last3] vs prior[first3].
    series = [{"hltv": r} for r in (0.90, 0.95, 1.00, 1.10, 1.15, 1.20)]
    res = compare.compare_self_periods(series, split_n=3)
    assert res["available"] is True
    assert res["n"] == {"recent": 3, "prior": 3}
    hltv = next(m for m in res["metrics"] if m["key"] == "hltv")
    # recent avg (1.15) > prior avg (0.95) -> "Recent" (a) wins
    assert hltv["winner"] == "a"
    assert res["summary"]


def test_self_periods_insufficient_history():
    res = compare.compare_self_periods([{"hltv": 1.0}], split_n=5)
    assert res["available"] is False
    assert res["summary"]
    assert res["metrics"] == []


# ---- compare_from_trends (temp-DB integration) ------------------------------
def _p(sid, name, **over):
    base = {"steamid": sid, "name": name, "kills": 10, "deaths": 10, "kd": 1.0,
            "adr": 75, "kast": 70, "hltv": 1.0, "open_wr": 50, "traded_pct": 20, "udr": 20}
    base.update(over)
    return base


def _match(sha, mp, players):
    return {"source_sha1": sha, "map": mp, "version": 14, "duration": 1800.0,
            "rounds": [{"score_ct": 13, "score_t": 7}],
            "analytics": {"version": 9, "n_rounds": 20, "players": players}}


def test_compare_from_trends_temp_db(tmp_path):
    db.DB_PATH = str(tmp_path / "cmp.sqlite")          # never the real DB
    db.migrate()
    # Player "1" (Ace) consistently out-rates player "2" (Benny) across 2 matches.
    db.index_demo(_match("a" * 40, "de_x",
                         [_p("1", "Ace", hltv=1.30, adr=90), _p("2", "Benny", hltv=0.90, adr=70)]),
                  "a" * 16, created_at="2026-06-01T00:00:00")
    db.index_demo(_match("b" * 40, "de_x",
                         [_p("1", "Ace", hltv=1.10, adr=80), _p("2", "Benny", hltv=1.00, adr=72)]),
                  "b" * 16, created_at="2026-06-02T00:00:00")

    con = db.connect()
    try:
        res = compare.compare_from_trends(con, "1", "2", scope=None)
    finally:
        con.close()

    assert res["a"]["label"] == "Ace" and res["b"]["label"] == "Benny"
    assert res["a"]["matches"] == 2 and res["b"]["matches"] == 2
    by_key = {m["key"]: m for m in res["metrics"]}
    assert by_key["hltv"]["winner"] == "a"             # 1.20 avg vs 0.95 avg
    assert by_key["adr"]["winner"] == "a"              # 85 avg vs 71 avg
    assert by_key["kast"]["winner"] == "tie"           # both seeded at 70
    assert res["summary"]
