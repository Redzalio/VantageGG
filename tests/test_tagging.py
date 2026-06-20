"""Per-user demo tagging (scrim/mm/faceit/... or free-form): db.set_demo_tag / tags_for_user and the
`tag` field surfaced in library_membership. Per-USER -- one member tagging their copy must NOT change a
teammate's view. Temp DBs only; never touches the real cs2dp.sqlite."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db   # noqa: E402


def _seed(con, sha, uid, team_id=None):
    con.execute("INSERT OR IGNORE INTO demos(sha1,key,map,rounds,created_at,score,schema_version,"
                "analytics_version,owner_user_id) VALUES(?,?,?,?,?,?,?,?,?)",
                (sha, sha[:16], "de_dust2", 24, "2026-06-18T00:00:00", "13-11", 1, 1, uid))
    con.execute("INSERT INTO user_demos(user_id,sha1,team_id,created_at,archived) VALUES(?,?,?,?,0)",
                (uid, sha, team_id, "2026-06-18T00:00:00"))


def _scope(uid):
    return {"uid": uid, "team_ids": db.team_ids_for_user(uid), "ownerless": False}


def _fresh(tmp_path, name):
    db.DB_PATH = str(tmp_path / name)
    db.migrate()


def test_tag_visible_to_owner_only(tmp_path):
    """A tag set on user1's copy shows up for user1 and is absent from user2's view (per-user)."""
    _fresh(tmp_path, "tag_iso.sqlite")
    a = db.upsert_user("76561190000000600", "A")
    b = db.upsert_user("76561190000000601", "B")
    team = db.create_team("Squad", a)
    tid = team["id"]
    db.join_team(team["invite_code"], b)
    X = "a" * 40                                       # A's demo, shared to the team so B can see it too
    con = db.connect()
    _seed(con, X, a, tid)
    con.commit()
    con.close()

    assert db.set_demo_tag(a, X, "faceit") is True
    ma = db.library_membership(_scope(a))
    assert ma[X]["tag"] == "faceit"                    # owner sees their own tag
    mb = db.library_membership(_scope(b))
    assert X in mb and "tag" not in mb[X]              # teammate's view carries no foreign tag


def test_each_member_has_independent_tag(tmp_path):
    """Two members with their own copies of the same content sha keep independent tags."""
    _fresh(tmp_path, "tag_indep.sqlite")
    a = db.upsert_user("76561190000000610", "A")
    b = db.upsert_user("76561190000000611", "B")
    X = "b" * 40
    con = db.connect()
    _seed(con, X, a, None)                             # both uploaded the same match -> two membership rows
    _seed(con, X, b, None)
    con.commit()
    con.close()

    db.set_demo_tag(a, X, "scrim")
    db.set_demo_tag(b, X, "matchmaking")
    assert db.library_membership(_scope(a))[X]["tag"] == "scrim"
    assert db.library_membership(_scope(b))[X]["tag"] == "matchmaking"


def test_empty_tag_clears(tmp_path):
    """Empty / whitespace-only tag clears back to NULL (absent in the membership dict)."""
    _fresh(tmp_path, "tag_clear.sqlite")
    a = db.upsert_user("76561190000000620", "A")
    X = "c" * 40
    con = db.connect()
    _seed(con, X, a, None)
    con.commit()
    con.close()

    db.set_demo_tag(a, X, "tournament")
    assert db.library_membership(_scope(a))[X]["tag"] == "tournament"
    assert db.set_demo_tag(a, X, "   ") is True        # whitespace -> clear
    assert "tag" not in db.library_membership(_scope(a))[X]
    assert db.tags_for_user(a) == []
    db.set_demo_tag(a, X, "esea")
    assert db.set_demo_tag(a, X, "") is True           # empty string -> clear
    assert "tag" not in db.library_membership(_scope(a))[X]


def test_tag_trimmed_and_clamped(tmp_path):
    """Tag is stripped and clamped to TAG_MAXLEN chars."""
    _fresh(tmp_path, "tag_clamp.sqlite")
    a = db.upsert_user("76561190000000630", "A")
    X = "d" * 40
    con = db.connect()
    _seed(con, X, a, None)
    con.commit()
    con.close()

    db.set_demo_tag(a, X, "  practice  ")
    assert db.library_membership(_scope(a))[X]["tag"] == "practice"   # surrounding whitespace gone
    long = "z" * 100
    db.set_demo_tag(a, X, long)
    stored = db.library_membership(_scope(a))[X]["tag"]
    assert stored == "z" * db.TAG_MAXLEN and len(stored) <= db.TAG_MAXLEN


def test_tags_for_user_distinct_and_isolated(tmp_path):
    """tags_for_user returns the user's distinct non-null tags, not another user's."""
    _fresh(tmp_path, "tag_list.sqlite")
    a = db.upsert_user("76561190000000640", "A")
    b = db.upsert_user("76561190000000641", "B")
    X, Y, Z = "e" * 40, "f" * 40, "0" * 40
    con = db.connect()
    _seed(con, X, a, None)
    _seed(con, Y, a, None)
    _seed(con, Z, b, None)
    con.commit()
    con.close()

    db.set_demo_tag(a, X, "scrim")
    db.set_demo_tag(a, Y, "scrim")                     # duplicate value -> collapses to one
    db.set_demo_tag(b, Z, "faceit")                    # B's tag must not leak into A's list
    assert db.tags_for_user(a) == ["scrim"]
    assert db.tags_for_user(b) == ["faceit"]
    db.set_demo_tag(a, Y, "tournament")
    assert db.tags_for_user(a) == ["scrim", "tournament"]   # sorted, distinct


def test_tagging_is_idempotent(tmp_path):
    """Setting the same tag repeatedly is stable (no dup rows, value unchanged)."""
    _fresh(tmp_path, "tag_idem.sqlite")
    a = db.upsert_user("76561190000000650", "A")
    X = "1" * 40
    con = db.connect()
    _seed(con, X, a, None)
    con.commit()
    con.close()

    for _ in range(3):
        assert db.set_demo_tag(a, X, "scrim") is True
    assert db.library_membership(_scope(a))[X]["tag"] == "scrim"
    assert db.tags_for_user(a) == ["scrim"]
    con = db.connect()
    n = con.execute("SELECT COUNT(*) n FROM user_demos WHERE user_id=? AND sha1=?", (a, X)).fetchone()["n"]
    con.close()
    assert n == 1                                      # still exactly one membership row


def test_set_tag_requires_membership(tmp_path):
    """Tagging a demo the user doesn't hold (or with no uid) is a no-op returning False."""
    _fresh(tmp_path, "tag_nomember.sqlite")
    a = db.upsert_user("76561190000000660", "A")
    b = db.upsert_user("76561190000000661", "B")
    X = "2" * 40
    con = db.connect()
    _seed(con, X, a, None)                             # only A holds it
    con.commit()
    con.close()

    assert db.set_demo_tag(b, X, "scrim") is False     # B has no membership row
    assert db.set_demo_tag(None, X, "scrim") is False  # open/local mode
    assert db.tags_for_user(b) == []


def test_set_tag_accepts_demo_key(tmp_path):
    """set_demo_tag resolves a demo by its cache key as well as its full sha1."""
    _fresh(tmp_path, "tag_bykey.sqlite")
    a = db.upsert_user("76561190000000670", "A")
    X = "3" * 40
    con = db.connect()
    _seed(con, X, a, None)
    con.commit()
    con.close()

    assert db.set_demo_tag(a, X[:16], "scrim") is True  # key (sha[:16]) -> resolves to the sha row
    assert db.library_membership(_scope(a))[X]["tag"] == "scrim"
