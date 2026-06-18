"""Tests for the saved-demo library (scoring, ids, zip extraction, index round-trip).

All stdlib-only -- no demo parsing -- so these stay fast and run in CI without a .dem.
"""
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import library            # noqa: E402
from schema import SCHEMA_VERSION   # noqa: E402


def _writer(path, data):
    """Minimal atomic-ish json writer matching app.atomic_write_json's contract."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


# ---- scoring ----------------------------------------------------------------
def test_final_score_reads_last_round():
    data = {"rounds": [{"score_ct": 1, "score_t": 0}, {"score_ct": 13, "score_t": 11}]}
    assert library.final_score(data) == {"ct": 13, "t": 11}


def test_final_score_defaults_when_no_rounds():
    assert library.final_score({"rounds": []}) == {"ct": 0, "t": 0}
    assert library.final_score({}) == {"ct": 0, "t": 0}
    assert library.final_score(None) == {"ct": 0, "t": 0}


# ---- ids --------------------------------------------------------------------
def test_demo_id_uses_source_sha1_when_present():
    assert library.demo_id_for({"source_sha1": "abc123"}) == "abc123"


def test_demo_id_falls_back_to_uuid_hex():
    a = library.demo_id_for({})
    b = library.demo_id_for({"source_sha1": "   "})   # blank -> uuid
    assert a != b and len(a) == 32 and len(b) == 32


# ---- zip extraction + traversal safety --------------------------------------
def test_iter_zip_dems_filters_and_sanitizes(tmp_path):
    zp = tmp_path / "demos.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("match.dem", b"x" * 10)
        z.writestr("notes.txt", b"nope")                 # non-.dem dropped
        z.writestr("sub/dir/nested.dem", b"y" * 10)      # nested -> basename only
        z.writestr("../evil.dem", b"z" * 10)             # traversal -> basename only
        z.writestr("emptydir/", b"")                     # dir entry dropped
    names = sorted(n for n, _ in library.iter_zip_dems(str(zp)))
    assert names == ["evil.dem", "match.dem", "nested.dem"]
    assert all("/" not in n and "\\" not in n and ".." not in n for n in names)


def test_safe_member_name_rejects_nondem_and_dirs():
    assert library._safe_member_name("a/b/x.dem") == "x.dem"
    assert library._safe_member_name("x.txt") == ""
    assert library._safe_member_name("folder/") == ""
    assert library._safe_member_name("..") == ""


# ---- index round-trip: upsert / list / load / dedup / prune -----------------
def _demo(sha, mp, ct, t, ver=SCHEMA_VERSION):
    return {"source_sha1": sha, "map": mp, "version": ver,
            "rounds": [{"score_ct": ct, "score_t": t}]}


def test_upsert_list_load_roundtrip(tmp_path):
    cd = str(tmp_path)
    library.upsert(cd, "id1", "a.dem", _demo("id1", "de_dust2", 16, 14), _writer)
    library.upsert(cd, "id2", "b.dem", _demo("id2", "de_mirage", 13, 9), _writer)

    rows = library.list_demos(cd, SCHEMA_VERSION)
    assert {r["id"] for r in rows} == {"id1", "id2"}
    by_id = {r["id"]: r for r in rows}
    assert by_id["id1"]["map"] == "de_dust2" and by_id["id1"]["score"] == {"ct": 16, "t": 14}
    assert all(r["stale"] is False for r in rows)

    # the saved JSON is served verbatim
    assert library.load_demo(cd, "id1")["map"] == "de_dust2"


def test_upsert_dedupes_by_id(tmp_path):
    cd = str(tmp_path)
    library.upsert(cd, "id1", "a.dem", _demo("id1", "de_dust2", 1, 0), _writer)
    library.upsert(cd, "id1", "a.dem", _demo("id1", "de_dust2", 16, 14), _writer)  # re-upload
    rows = library.list_demos(cd, SCHEMA_VERSION)
    assert len(rows) == 1 and rows[0]["score"] == {"ct": 16, "t": 14}


def test_list_flags_stale_on_schema_mismatch(tmp_path):
    cd = str(tmp_path)
    library.upsert(cd, "old", "o.dem", _demo("old", "de_nuke", 7, 5, ver=SCHEMA_VERSION - 1), _writer)
    assert library.list_demos(cd, SCHEMA_VERSION)[0]["stale"] is True


def test_list_prunes_rows_whose_json_vanished(tmp_path):
    cd = str(tmp_path)
    library.upsert(cd, "gone", "g.dem", _demo("gone", "de_train", 9, 9), _writer)
    os.remove(library.lib_cache_path(cd, "gone"))         # delete the backing JSON
    assert library.list_demos(cd, SCHEMA_VERSION) == []   # row pruned from the index


def test_load_demo_rejects_path_traversal_ids(tmp_path):
    cd = str(tmp_path)
    assert library.load_demo(cd, "../secret") is None
    assert library.load_demo(cd, "a/b") is None
    assert library.load_demo(cd, "") is None


# ---- Stage 2: canonical-artifact / pointer dedup ----------------------------
def test_upsert_writes_pointer_when_canonical_exists(tmp_path):
    cd, sha = str(tmp_path), "a" * 40
    big = {"source_sha1": sha, "map": "de_dust2", "version": SCHEMA_VERSION,
           "rounds": [{"score_ct": 13, "score_t": 7}],
           "frames": [{"p": i} for i in range(200)]}        # stands in for the 45-90MB blob
    _writer(library.canonical_cache_path(cd, sha), big)     # parse step wrote the canonical first
    library.upsert(cd, sha, "m.dem", big, _writer)
    ptr = json.load(open(library.lib_cache_path(cd, sha)))
    assert ptr.get("_pointer") and ptr["canonical_key"] == sha[:16]
    assert os.path.getsize(library.lib_cache_path(cd, sha)) < 500     # tiny pointer, NOT a 2nd blob
    assert library.load_demo(cd, sha)["map"] == "de_dust2"           # resolves pointer -> canonical


def test_upsert_full_copy_when_no_canonical(tmp_path):
    cd, sha = str(tmp_path), "b" * 40
    library.upsert(cd, sha, "m.dem", _demo(sha, "de_mirage", 13, 9), _writer)  # no canonical pre-written
    saved = json.load(open(library.lib_cache_path(cd, sha)))
    assert not saved.get("_pointer") and saved["map"] == "de_mirage"  # legacy full-copy fallback
    assert library.load_demo(cd, sha)["map"] == "de_mirage"


def test_load_demo_canonical_only(tmp_path):
    cd, sha = str(tmp_path), "c" * 40
    _writer(library.canonical_cache_path(cd, sha), _demo(sha, "de_ancient", 16, 5))  # no lib_ file
    assert library.load_demo(cd, sha)["map"] == "de_ancient"
