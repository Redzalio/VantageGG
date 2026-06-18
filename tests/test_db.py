"""Tests for the SQLite metadata index (db.py). Synthetic data, temp DB -- no real cache needed."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db   # noqa: E402


def _p(sid, name, **kw):
    base = {"steamid": sid, "name": name, "kills": 20, "deaths": 15, "kd": 1.33,
            "adr": 80, "kast": 70, "hltv": 1.1, "open_wr": 50, "traded_pct": 20, "udr": 25}
    base.update(kw)
    return base


def _match(sha, mp, players, ver=14):
    return {"source_sha1": sha, "map": mp, "version": ver, "duration": 1800.0,
            "rounds": [{"score_ct": 13, "score_t": 7}],
            "analytics": {"version": 9, "n_rounds": 20, "players": players}}


def _tmpdb(tmp_path):
    db.DB_PATH = str(tmp_path / "idx.sqlite")
    db.migrate()


def test_index_list_players_trends(tmp_path):
    _tmpdb(tmp_path)
    db.index_demo(_match("a" * 40, "de_dust2", [_p("111", "Alice"), _p("222", "Bob")]),
                  "a" * 16, created_at="2026-06-01T10:00:00")
    db.index_demo(_match("b" * 40, "de_mirage", [_p("111", "Alice", hltv=1.4)]),
                  "b" * 16, created_at="2026-06-02T10:00:00")
    m = db.list_matches()
    assert len(m) == 2
    assert m[0]["map"] == "de_mirage"                 # newest-first by created_at
    assert m[0]["created_at"] >= m[1]["created_at"]
    assert {p["steamid"] for p in m[1]["players"]} == {"111", "222"}
    pl = db.all_players()
    assert next(p for p in pl if p["steamid"] == "111")["n_matches"] == 2
    tr = db.player_trends("111")
    assert tr["n_matches"] == 2 and tr["name"] == "Alice"
    assert "hltv" in tr["averages"]


def test_no_full_json_scan(tmp_path):
    # data is returned purely from SQLite even with NO cache files on disk -> proves the
    # listing endpoints don't scan/json.load the big replay blobs.
    _tmpdb(tmp_path)
    db.index_demo(_match("c" * 40, "de_nuke", [_p("9", "Z")]), "c" * 16, created_at="2026-06-03T00:00:00")
    assert len(db.list_matches()) == 1            # no cache dir touched
    assert len(db.all_players()) == 1


def test_dedup_prefers_canonical_key(tmp_path):
    _tmpdb(tmp_path)
    sha = "d" * 40
    db.index_demo(_match(sha, "de_ancient", [_p("1", "A")]), "lib_" + sha)   # library copy first
    db.index_demo(_match(sha, "de_ancient", [_p("1", "A")]), sha[:16])        # canonical content cache
    m = db.list_matches()
    assert len(m) == 1                             # one row per source_sha1
    assert not m[0]["key"].startswith("lib_")      # canonical (loadable) key preferred


def test_remove_demo(tmp_path):
    _tmpdb(tmp_path)
    db.index_demo(_match("e" * 40, "de_inferno", [_p("7", "Q")]), "e" * 16)
    assert len(db.list_matches()) == 1
    assert db.remove_demo("e" * 40) == 1           # by sha
    assert db.list_matches() == [] and db.all_players() == []


def test_non_match_skipped(tmp_path):
    _tmpdb(tmp_path)
    assert db.index_demo({"source_sha1": "f" * 40, "map": "x", "analytics": {}}, "f" * 16) is None
    assert db.index_demo({"not": "a demo"}, "g" * 16) is None
    assert db.list_matches() == []
