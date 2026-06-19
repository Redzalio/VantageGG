"""SQLite metadata/index layer for the CS2 demo player (website infrastructure).

Stdlib-only (sqlite3). The big parsed replay JSONs stay on disk for actual replay loading;
this index holds only the small per-match + per-player summary rows so listing endpoints
(/api/matches, /api/players, /api/trends) don't have to scan + json.load 45-90 MB files.

Design:
  * one row per demo content hash (source_sha1) -- de-duped like matchindex did (the content
    cache <sha16>.json and its library copy lib_<fullsha>.json point at the SAME match).
  * `demos.key` = the preferred cache key for loading the replay (canonical non-lib_ if present).
  * owner_user_id / team_id columns exist now (NULL) so Stage 4/5 ownership is a column-fill,
    not a migration. users/teams/team_members/jobs tables are pre-created (empty) for the same reason.

Output shapes of list_matches/all_players/player_trends MATCH matchindex.py so the frontend and
existing endpoints are unchanged -- only the data source moves from JSON-scan to SQLite.
"""
import datetime
import json
import os
import secrets
import sqlite3

import matchindex as mi   # reuse the JSON->row helpers (_num/_score/_created_at) -- stdlib, no pandas

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR") or HERE
DB_PATH = os.environ.get("SQLITE_PATH") or os.path.join(DATA_DIR, "cs2dp.sqlite")

_PLAYER_FIELDS = mi._PLAYER_FIELDS              # kills,deaths,kd,adr,kast,hltv,open_wr,traded_pct,udr
_TREND_STATS = mi._STATS                        # hltv,adr,kast,open_wr,traded_pct,udr

SCHEMA = """
CREATE TABLE IF NOT EXISTS demos (
  sha1 TEXT PRIMARY KEY,
  key TEXT,
  map TEXT,
  rounds INTEGER,
  created_at TEXT,
  duration REAL,
  score TEXT,
  schema_version INTEGER,
  analytics_version INTEGER,
  has_duck INTEGER DEFAULT 0,
  owner_user_id INTEGER,
  team_id INTEGER,
  indexed_at TEXT
);
CREATE TABLE IF NOT EXISTS demo_players (
  sha1 TEXT NOT NULL,
  steamid TEXT NOT NULL,
  name TEXT,
  kills REAL, deaths REAL, kd REAL, adr REAL, kast REAL, hltv REAL,
  open_wr REAL, traded_pct REAL, udr REAL,
  PRIMARY KEY (sha1, steamid)
);
CREATE INDEX IF NOT EXISTS idx_dp_steamid ON demo_players(steamid);
CREATE INDEX IF NOT EXISTS idx_demos_created ON demos(created_at);
CREATE INDEX IF NOT EXISTS idx_demos_owner ON demos(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_demos_team ON demos(team_id);

-- forward-looking (Stages 4/5/3) -- created now so later wiring needs no migration
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  steam_id_64 TEXT UNIQUE,
  display_name TEXT, avatar_url TEXT,
  tier TEXT DEFAULT 'free',
  pro_until TEXT,                 -- ISO datetime Pro expires; NULL = indefinite (or not Pro)
  role TEXT DEFAULT 'user',
  name_locked INTEGER DEFAULT 0,  -- 1 once the user sets a custom display name (Steam login won't overwrite)
  created_at TEXT, last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS teams (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT, owner_user_id INTEGER, invite_code TEXT UNIQUE, created_at TEXT
);
CREATE TABLE IF NOT EXISTS team_members (
  team_id INTEGER NOT NULL, user_id INTEGER NOT NULL, role TEXT, joined_at TEXT,
  PRIMARY KEY (team_id, user_id)
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  owner_user_id INTEGER,
  filename TEXT, upload_path TEXT,
  status TEXT, progress TEXT,
  created_at TEXT, started_at TEXT, finished_at TEXT,
  error TEXT, demo_sha1 TEXT
);
-- Per-user library membership: which users have a given (content-addressed) demo in their library.
-- Decouples "who has this demo" from the single shared parse, so two players who upload the same
-- match each get their own copy automatically (sharing one cached parse). team_id = the member's
-- own share of it to one of their teams (NULL = private). Visibility/quota/delete key off this table.
CREATE TABLE IF NOT EXISTS user_demos (
  user_id INTEGER NOT NULL,
  sha1 TEXT NOT NULL,
  team_id INTEGER,
  created_at TEXT,
  PRIMARY KEY (user_id, sha1)
);
CREATE INDEX IF NOT EXISTS idx_user_demos_user ON user_demos(user_id);
CREATE INDEX IF NOT EXISTS idx_user_demos_sha ON user_demos(sha1);
-- Per-user squad curation: overrides on top of the auto-detected teammate list. status 'in' = the
-- user explicitly added this player; 'out' = explicitly removed an auto-suggested one. Auto-detected
-- players (>=2 shared matches) are in the squad by default unless marked 'out'.
CREATE TABLE IF NOT EXISTS user_roster (
  user_id INTEGER NOT NULL,
  steamid TEXT NOT NULL,
  status TEXT,
  name TEXT,
  PRIMARY KEY (user_id, steamid)
);
CREATE INDEX IF NOT EXISTS idx_user_roster_user ON user_roster(user_id);
-- Practice Goals (moved out of the legacy goals/goals.json -> one row per goal; scope is JSON).
-- owner_user_id = creator (NULL = local/legacy, visible to everyone); team_id = shared with that team
-- (NULL = personal). Grading still reads the demo cache; this table is just persistence + visibility.
CREATE TABLE IF NOT EXISTS goals (
  id TEXT PRIMARY KEY,
  owner_user_id INTEGER,
  team_id INTEGER,
  metric TEXT,
  title TEXT,
  target REAL,
  scope TEXT,
  drill TEXT,
  status TEXT,
  notes TEXT,
  source_match_key TEXT,
  baseline REAL,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_goals_owner ON goals(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_goals_team ON goals(team_id);
"""


def connect():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _ensure_column(c, table, col, decl):
    """Add a column to an existing table if it's missing (CREATE TABLE IF NOT EXISTS won't alter)."""
    have = {r["name"] for r in c.execute("PRAGMA table_info(%s)" % table)}
    if col not in have:
        c.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, decl))


def migrate(con=None):
    """Create tables/indexes if missing + add new columns to existing tables. Idempotent."""
    c = con or connect()
    try:
        # first run with the membership model? -> backfill user_demos from the legacy single-owner column
        had_user_demos = c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='user_demos'").fetchone() is not None
        c.executescript(SCHEMA)
        _ensure_column(c, "users", "tier", "TEXT DEFAULT 'free'")   # subscription tier (free/pro)
        _ensure_column(c, "users", "pro_until", "TEXT")             # Pro expiry (NULL = indefinite)
        _ensure_column(c, "users", "role", "TEXT DEFAULT 'user'")   # access role (user/helper)
        _ensure_column(c, "users", "name_locked", "INTEGER DEFAULT 0")  # user set a custom display name
        _ensure_column(c, "users", "stripe_customer_id", "TEXT")    # Stripe customer (billing); NULL until first checkout
        c.execute("CREATE INDEX IF NOT EXISTS idx_users_stripe_cust ON users(stripe_customer_id)")
        _ensure_column(c, "jobs", "upload_ms", "INTEGER")           # 19A: server-side receive+save time per file (ms)
        _ensure_column(c, "jobs", "bytes", "INTEGER")               # 19A: uploaded file size (bytes), for the timing breakdown
        if not had_user_demos:
            c.execute("""INSERT OR IGNORE INTO user_demos(user_id, sha1, team_id, created_at)
                         SELECT owner_user_id, sha1, team_id, created_at
                         FROM demos WHERE owner_user_id IS NOT NULL""")
        c.commit()
    finally:
        if con is None:
            c.close()


# ---- write path -------------------------------------------------------------
def index_demo(data, key, created_at=None, owner_user_id=None, con=None):
    """Upsert one parsed demo's summary + player rows. `data` = parsed cache dict (with analytics),
    `key` = its cache filename stem. `owner_user_id` stamps the uploader (NULL in local mode).
    No-op for non-match data (no analytics.players). Returns sha1 or None."""
    if not isinstance(data, dict):
        return None
    a = data.get("analytics") or {}
    players = a.get("players")
    if not isinstance(players, list) or not players:
        return None
    sha = data.get("source_sha1") or key
    c = con or connect()
    try:
        # keep the canonical (non-lib_) key as the loader key if we already have one
        row = c.execute("SELECT key FROM demos WHERE sha1=?", (sha,)).fetchone()
        prefer = key
        if row and row["key"]:
            if key.startswith("lib_") and not row["key"].startswith("lib_"):
                prefer = row["key"]
        ca = created_at or _safe_created_at(key) or datetime.datetime.now().isoformat(timespec="seconds")
        c.execute(
            """INSERT INTO demos(sha1,key,map,rounds,created_at,duration,score,schema_version,
                                 analytics_version,has_duck,owner_user_id,indexed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(sha1) DO UPDATE SET key=excluded.key, map=excluded.map, rounds=excluded.rounds,
                 created_at=excluded.created_at, duration=excluded.duration, score=excluded.score,
                 schema_version=excluded.schema_version, analytics_version=excluded.analytics_version,
                 has_duck=excluded.has_duck,
                 owner_user_id=COALESCE(demos.owner_user_id, excluded.owner_user_id),
                 indexed_at=excluded.indexed_at""",
            (sha, prefer, data.get("map"),
             a.get("n_rounds") or len(data.get("rounds") or []),
             ca, data.get("duration"), mi._score(data),
             data.get("version"), a.get("version"),
             1 if _data_has_duck(data) else 0,
             owner_user_id,
             datetime.datetime.now().isoformat(timespec="seconds")))
        c.execute("DELETE FROM demo_players WHERE sha1=?", (sha,))
        rows = []
        for p in players:
            sid = p.get("steamid")
            if not sid:
                continue
            rows.append((sha, str(sid), p.get("name"),
                         *[mi._num(p.get(f)) for f in _PLAYER_FIELDS]))
        if rows:
            c.executemany(
                "INSERT OR REPLACE INTO demo_players(sha1,steamid,name,"
                + ",".join(_PLAYER_FIELDS) + ") VALUES(" + ",".join(["?"] * (3 + len(_PLAYER_FIELDS))) + ")",
                rows)
        # AUTOMATIC per-user library membership: every uploader (including the 2nd person who uploads
        # the same match -> cache hit, but still re-saved) gets their own copy. Idempotent.
        if owner_user_id is not None:
            c.execute("INSERT OR IGNORE INTO user_demos(user_id, sha1, created_at) VALUES(?,?,?)",
                      (owner_user_id, sha, datetime.datetime.now().isoformat(timespec="seconds")))
        c.commit()
        return sha
    finally:
        if con is None:
            c.close()


def remove_demo(sha1_or_key, con=None):
    """Drop a demo's index rows by source_sha1 OR cache key. Returns rows removed."""
    c = con or connect()
    try:
        sha = sha1_or_key
        hit = c.execute("SELECT sha1 FROM demos WHERE sha1=? OR key=?",
                        (sha1_or_key, sha1_or_key)).fetchone()
        if hit:
            sha = hit["sha1"]
        n = c.execute("DELETE FROM demos WHERE sha1=?", (sha,)).rowcount
        c.execute("DELETE FROM demo_players WHERE sha1=?", (sha,))
        c.execute("DELETE FROM user_demos WHERE sha1=?", (sha,))   # drop any remaining memberships
        c.commit()
        return n
    finally:
        if con is None:
            c.close()


# ---- users (Stage 4 Steam auth) ---------------------------------------------
def _user_row(r):
    if not r:
        return None
    keys = r.keys()
    tier = (r["tier"] if "tier" in keys else None) or "free"
    role = (r["role"] if "role" in keys else None) or "user"
    pro_until = r["pro_until"] if "pro_until" in keys else None
    return {"id": r["id"], "steam_id_64": r["steam_id_64"], "name": r["display_name"],
            "avatar": r["avatar_url"], "tier": tier, "pro_until": pro_until, "role": role,
            "stripe_customer_id": (r["stripe_customer_id"] if "stripe_customer_id" in keys else None),
            "created_at": r["created_at"], "last_login_at": r["last_login_at"]}


def set_stripe_customer(user_id, customer_id, con=None):
    """Link a Stripe customer to a user (set once at first checkout). Returns True if updated."""
    c = con or connect()
    try:
        n = c.execute("UPDATE users SET stripe_customer_id=? WHERE id=?", (customer_id, user_id)).rowcount
        c.commit()
        return bool(n)
    finally:
        if con is None:
            c.close()


def user_by_stripe_customer(customer_id, con=None):
    """The user linked to a Stripe customer id, or None. Used by subscription.* webhooks (which carry
    only the customer, not our user id)."""
    if not customer_id:
        return None
    c = con or connect()
    try:
        return _user_row(c.execute("SELECT * FROM users WHERE stripe_customer_id=?", (str(customer_id),)).fetchone())
    finally:
        if con is None:
            c.close()


def set_user_tier(user_id, tier, pro_until=None, con=None):
    """Set a user's subscription tier. tier='pro' with pro_until=<ISO datetime> expires then;
    pro_until=None means indefinite. tier='free' always clears the expiry. Returns True if updated."""
    tier = "pro" if str(tier).strip().lower() == "pro" else "free"
    if tier == "free":
        pro_until = None
    c = con or connect()
    try:
        n = c.execute("UPDATE users SET tier=?, pro_until=? WHERE id=?", (tier, pro_until, user_id)).rowcount
        c.commit()
        return bool(n)
    finally:
        if con is None:
            c.close()


def set_user_role(user_id, role, con=None):
    """Set a user's access role ('user' or 'helper'). Returns True if a row was updated."""
    role = "helper" if str(role).strip().lower() == "helper" else "user"
    c = con or connect()
    try:
        n = c.execute("UPDATE users SET role=? WHERE id=?", (role, user_id)).rowcount
        c.commit()
        return bool(n)
    finally:
        if con is None:
            c.close()


def list_users(con=None):
    """All users (newest-login first) with their demo counts -- for the admin panel."""
    c = con or connect()
    try:
        rows = c.execute("SELECT * FROM users ORDER BY last_login_at DESC").fetchall()
        out = []
        for r in rows:
            u = _user_row(r)
            u["demo_count"] = c.execute("SELECT COUNT(*) n FROM demos WHERE owner_user_id=?",
                                        (r["id"],)).fetchone()["n"]
            out.append(u)
        return out
    finally:
        if con is None:
            c.close()


def _since(days):
    """ISO date `days-1` days ago (so a 7-day window includes today + the previous 6)."""
    return (datetime.date.today() - datetime.timedelta(days=days - 1)).isoformat()


def _daily(c, table, days=14):
    """Per-day row counts for the last `days` days (zero-filled), oldest-first, for a sparkline."""
    by = {r["d"]: r["n"] for r in c.execute(
        "SELECT substr(created_at,1,10) d, COUNT(*) n FROM %s WHERE created_at >= ? GROUP BY d" % table,
        (_since(days),))}
    out = []
    for i in range(days - 1, -1, -1):
        day = (datetime.date.today() - datetime.timedelta(days=i)).isoformat()
        out.append({"date": day, "count": by.get(day, 0)})
    return out


def admin_overview(con=None):
    """Admin metrics: user/demo/team totals, pro-vs-free, growth windows (7d/30d) and 14-day
    signup + upload activity series. Aggregate numbers only -- no per-row data."""
    c = con or connect()
    try:
        one = lambda q, a=(): c.execute(q, a).fetchone()["n"]
        now = datetime.datetime.now().isoformat(timespec="seconds")
        users = one("SELECT COUNT(*) n FROM users")
        # active Pro only -- an expired pro_until no longer counts (ISO strings compare lexically)
        pro = one("SELECT COUNT(*) n FROM users WHERE tier='pro' AND (pro_until IS NULL OR pro_until >= ?)", (now,))
        jobs_by = {r["status"]: r["c"] for r in
                   c.execute("SELECT status, COUNT(*) c FROM jobs GROUP BY status")}
        return {
            "users": users, "pro_users": pro, "free_users": users - pro,
            "helpers": one("SELECT COUNT(*) n FROM users WHERE role='helper'"),
            "demos": one("SELECT COUNT(*) n FROM demos"),
            "demos_ownerless": one("SELECT COUNT(*) n FROM demos WHERE owner_user_id IS NULL"),
            "teams": one("SELECT COUNT(*) n FROM teams"),
            "players_indexed": one("SELECT COUNT(DISTINCT steamid) n FROM demo_players"),
            "new_users_7d": one("SELECT COUNT(*) n FROM users WHERE created_at >= ?", (_since(7),)),
            "new_users_30d": one("SELECT COUNT(*) n FROM users WHERE created_at >= ?", (_since(30),)),
            "demos_7d": one("SELECT COUNT(*) n FROM demos WHERE created_at >= ?", (_since(7),)),
            "demos_30d": one("SELECT COUNT(*) n FROM demos WHERE created_at >= ?", (_since(30),)),
            "signups_14d": _daily(c, "users", 14),
            "uploads_14d": _daily(c, "demos", 14),
            "jobs": jobs_by,
        }
    finally:
        if con is None:
            c.close()


def upsert_user(steam_id_64, display_name=None, avatar_url=None, con=None):
    """Create or refresh a user by SteamID64; bumps last_login_at. Keeps an existing name/avatar when
    this login didn't fetch one (no STEAM_API_KEY). Returns the integer user id."""
    c = con or connect()
    try:
        now = datetime.datetime.now().isoformat(timespec="seconds")
        c.execute(
            """INSERT INTO users(steam_id_64,display_name,avatar_url,created_at,last_login_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(steam_id_64) DO UPDATE SET
                 display_name=CASE WHEN COALESCE(users.name_locked,0)=1 THEN users.display_name
                                   ELSE COALESCE(excluded.display_name, users.display_name) END,
                 avatar_url=COALESCE(excluded.avatar_url, users.avatar_url),
                 last_login_at=excluded.last_login_at""",
            (str(steam_id_64), display_name, avatar_url, now, now))
        c.commit()
        r = c.execute("SELECT id FROM users WHERE steam_id_64=?", (str(steam_id_64),)).fetchone()
        return r["id"] if r else None
    finally:
        if con is None:
            c.close()


def set_display_name(user_id, name, con=None):
    """Set a user's custom display name (and lock it so Steam re-login won't overwrite it).
    Returns the trimmed name, or None if invalid/empty."""
    nm = (str(name or "").strip())[:32]
    if not nm:
        return None
    c = con or connect()
    try:
        c.execute("UPDATE users SET display_name=?, name_locked=1 WHERE id=?", (nm, user_id))
        c.commit()
        return nm
    finally:
        if con is None:
            c.close()


def owned_demo_ids(user_id, con=None):
    """sha1 ids of demos in this user's library (their memberships) -- used to refcount-wipe on
    account deletion (a demo's files are only removed once its LAST member is gone)."""
    if user_id is None:
        return []
    c = con or connect()
    try:
        return [r["sha1"] for r in c.execute("SELECT sha1 FROM user_demos WHERE user_id=?", (user_id,))]
    finally:
        if con is None:
            c.close()


def remove_membership(user_id, sha1, con=None):
    """Remove one user's copy of a demo from their library. Returns how many members REMAIN for that
    demo (0 -> caller may wipe the shared parse/cache)."""
    c = con or connect()
    try:
        c.execute("DELETE FROM user_demos WHERE user_id=? AND sha1=?", (user_id, sha1))
        c.commit()
        return c.execute("SELECT COUNT(*) n FROM user_demos WHERE sha1=?", (sha1,)).fetchone()["n"]
    finally:
        if con is None:
            c.close()


def demo_member_count(sha1, con=None):
    """How many users have this demo in their library (refcount)."""
    c = con or connect()
    try:
        return c.execute("SELECT COUNT(*) n FROM user_demos WHERE sha1=?", (sha1,)).fetchone()["n"]
    finally:
        if con is None:
            c.close()


def resolve_sha(demo_id, con=None):
    """Canonical source_sha1 for a demo id (sha1 OR cache key), or the input unchanged if not indexed."""
    c = con or connect()
    try:
        r = c.execute("SELECT sha1 FROM demos WHERE sha1=? OR key=?", (demo_id, demo_id)).fetchone()
        return r["sha1"] if r else demo_id
    finally:
        if con is None:
            c.close()


def user_demo_count(user_id, con=None):
    """How many demos are in this user's library (for the Free upload cap)."""
    if user_id is None:
        return 0
    c = con or connect()
    try:
        return c.execute("SELECT COUNT(*) n FROM user_demos WHERE user_id=?", (user_id,)).fetchone()["n"]
    finally:
        if con is None:
            c.close()


def get_user(user_id, con=None):
    if user_id is None:
        return None
    c = con or connect()
    try:
        return _user_row(c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())
    finally:
        if con is None:
            c.close()


def get_user_by_steamid(steam_id_64, con=None):
    c = con or connect()
    try:
        return _user_row(c.execute("SELECT * FROM users WHERE steam_id_64=?",
                                   (str(steam_id_64),)).fetchone())
    finally:
        if con is None:
            c.close()


def recent_demos(limit=12, con=None):
    """Most-recently-created demos with their owner's name, for the admin activity feed."""
    c = con or connect()
    try:
        rows = c.execute(
            """SELECT d.sha1, d.map, d.rounds, d.created_at, d.owner_user_id, u.display_name AS owner
               FROM demos d LEFT JOIN users u ON u.id = d.owner_user_id
               ORDER BY d.created_at DESC LIMIT ?""", (limit,)).fetchall()
        return [{"sha": r["sha1"], "map": r["map"], "rounds": r["rounds"], "created_at": r["created_at"],
                 "owner": r["owner"] or (("user " + str(r["owner_user_id"])) if r["owner_user_id"] else None)}
                for r in rows]
    finally:
        if con is None:
            c.close()


def delete_user(user_id, con=None):
    """Remove a user account: drop their library memberships (user_demos) and team memberships, and
    null the legacy owner column. Demos with OTHER members survive; the caller refcount-wipes any that
    are now memberless (it owns the cache files). Returns True if the user existed."""
    c = con or connect()
    try:
        c.execute("DELETE FROM user_demos WHERE user_id=?", (user_id,))
        c.execute("UPDATE demos SET owner_user_id=NULL WHERE owner_user_id=?", (user_id,))   # legacy col
        c.execute("DELETE FROM team_members WHERE user_id=?", (user_id,))
        n = c.execute("DELETE FROM users WHERE id=?", (user_id,)).rowcount
        c.commit()
        return bool(n)
    finally:
        if con is None:
            c.close()


# ---- teams / workspaces (Stage 5 data isolation) ----------------------------
def create_team(name, owner_user_id, con=None):
    """Create a team owned by `owner_user_id` (auto-joined as 'owner'). Returns {id,name,invite_code,role}."""
    c = con or connect()
    try:
        code = secrets.token_urlsafe(8)
        now = datetime.datetime.now().isoformat(timespec="seconds")
        nm = str(name or "Team")[:80]
        tid = c.execute("INSERT INTO teams(name,owner_user_id,invite_code,created_at) VALUES(?,?,?,?)",
                        (nm, owner_user_id, code, now)).lastrowid
        c.execute("INSERT OR REPLACE INTO team_members(team_id,user_id,role,joined_at) VALUES(?,?,?,?)",
                  (tid, owner_user_id, "owner", now))
        c.commit()
        return {"id": tid, "name": nm, "invite_code": code, "role": "owner"}
    finally:
        if con is None:
            c.close()


def join_team(invite_code, user_id, con=None):
    """Add `user_id` to the team with this invite code. Returns {id,name,role} or None if no such code."""
    c = con or connect()
    try:
        t = c.execute("SELECT * FROM teams WHERE invite_code=?", (str(invite_code or ""),)).fetchone()
        if not t:
            return None
        c.execute("INSERT OR IGNORE INTO team_members(team_id,user_id,role,joined_at) VALUES(?,?,?,?)",
                  (t["id"], user_id, "member", datetime.datetime.now().isoformat(timespec="seconds")))
        c.commit()
        return {"id": t["id"], "name": t["name"], "role": "member"}
    finally:
        if con is None:
            c.close()


def team_ids_for_user(user_id, con=None):
    if user_id is None:
        return []
    c = con or connect()
    try:
        return [r["team_id"] for r in
                c.execute("SELECT team_id FROM team_members WHERE user_id=?", (user_id,))]
    finally:
        if con is None:
            c.close()


def teams_for_user(user_id, con=None):
    """Teams the user belongs to (with member_count; invite_code only for teams they own)."""
    if user_id is None:
        return []
    c = con or connect()
    try:
        rows = c.execute("""SELECT t.id, t.name, tm.role, t.invite_code
                            FROM team_members tm JOIN teams t ON t.id=tm.team_id
                            WHERE tm.user_id=? ORDER BY t.name""", (user_id,)).fetchall()
        out = []
        for r in rows:
            members = [{"user_id": m["user_id"], "name": m["name"], "role": m["role"],
                        "steamid": m["steamid"]} for m in c.execute(
                "SELECT tm.user_id, tm.role, u.display_name AS name, u.steam_id_64 AS steamid "
                "FROM team_members tm LEFT JOIN users u ON u.id=tm.user_id WHERE tm.team_id=? "
                "ORDER BY (tm.role='owner') DESC, u.display_name", (r["id"],))]
            d = {"id": r["id"], "name": r["name"], "role": r["role"],
                 "member_count": len(members), "members": members}
            if r["role"] == "owner":
                d["invite_code"] = r["invite_code"]      # only owners see/share the join code
            out.append(d)
        return out
    finally:
        if con is None:
            c.close()


def leave_team(user_id, team_id, con=None):
    """A non-owner member leaves the team; their demos shared to it revert to private. The owner can't
    leave (they disband instead). Returns True if they left."""
    c = con or connect()
    try:
        row = c.execute("SELECT role FROM team_members WHERE team_id=? AND user_id=?",
                        (team_id, user_id)).fetchone()
        if not row or row["role"] == "owner":
            return False
        c.execute("DELETE FROM team_members WHERE team_id=? AND user_id=?", (team_id, user_id))
        c.execute("UPDATE user_demos SET team_id=NULL WHERE user_id=? AND team_id=?", (user_id, team_id))
        c.commit()
        return True
    finally:
        if con is None:
            c.close()


def remove_member(team_id, target_uid, requester_uid, con=None):
    """Owner removes another member (can't remove themselves -- that's disband). The removed member's
    demos shared to this team revert to private. Returns True on success."""
    c = con or connect()
    try:
        t = c.execute("SELECT owner_user_id FROM teams WHERE id=?", (team_id,)).fetchone()
        if not t or t["owner_user_id"] != requester_uid or target_uid == requester_uid:
            return False
        n = c.execute("DELETE FROM team_members WHERE team_id=? AND user_id=? AND role<>'owner'",
                      (team_id, target_uid)).rowcount
        c.execute("UPDATE user_demos SET team_id=NULL WHERE user_id=? AND team_id=?", (target_uid, team_id))
        c.commit()
        return bool(n)
    finally:
        if con is None:
            c.close()


def disband_team(team_id, requester_uid, con=None):
    """Owner deletes the team: drops it + all memberships, and reverts every demo shared to it back to
    private. Returns True on success (requester must be the owner)."""
    c = con or connect()
    try:
        t = c.execute("SELECT owner_user_id FROM teams WHERE id=?", (team_id,)).fetchone()
        if not t or t["owner_user_id"] != requester_uid:
            return False
        c.execute("UPDATE user_demos SET team_id=NULL WHERE team_id=?", (team_id,))
        c.execute("DELETE FROM team_members WHERE team_id=?", (team_id,))
        c.execute("DELETE FROM teams WHERE id=?", (team_id,))
        c.commit()
        return True
    finally:
        if con is None:
            c.close()


# ---- practice goals (persistence; grading lives in goals.py) ----------------
_GOAL_COLS = ("id", "owner_user_id", "team_id", "metric", "title", "target", "scope",
              "drill", "status", "notes", "source_match_key", "baseline", "created_at")


def _goal_from_row(r):
    if r is None:
        return None
    g = {k: r[k] for k in _GOAL_COLS}
    try:
        g["scope"] = json.loads(g["scope"]) if g["scope"] else {}
    except (ValueError, TypeError):
        g["scope"] = {}
    return g


def goal_all(con=None):
    c = con or connect()
    try:
        return [_goal_from_row(r) for r in c.execute("SELECT * FROM goals ORDER BY created_at")]
    finally:
        if con is None:
            c.close()


def goal_get(goal_id, con=None):
    c = con or connect()
    try:
        return _goal_from_row(c.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone())
    finally:
        if con is None:
            c.close()


def goal_upsert(g, con=None):
    """Insert or replace a full goal record (scope dict is stored as JSON)."""
    c = con or connect()
    try:
        scope = g.get("scope")
        c.execute(
            """INSERT INTO goals(id,owner_user_id,team_id,metric,title,target,scope,drill,status,
                                 notes,source_match_key,baseline,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 owner_user_id=excluded.owner_user_id, team_id=excluded.team_id, metric=excluded.metric,
                 title=excluded.title, target=excluded.target, scope=excluded.scope, drill=excluded.drill,
                 status=excluded.status, notes=excluded.notes, source_match_key=excluded.source_match_key,
                 baseline=excluded.baseline, created_at=excluded.created_at""",
            (g.get("id"), g.get("owner_user_id"), g.get("team_id"), g.get("metric"), g.get("title"),
             g.get("target"), json.dumps(scope if isinstance(scope, dict) else {}), g.get("drill"),
             g.get("status"), g.get("notes"), g.get("source_match_key"), g.get("baseline"),
             g.get("created_at")))
        c.commit()
        return g
    finally:
        if con is None:
            c.close()


_GOAL_UPDATABLE = ("status", "notes", "title", "target", "drill")


def goal_update(goal_id, fields, con=None):
    """Update whitelisted columns (caller validates values). Returns the updated record or None."""
    cols = {k: fields[k] for k in _GOAL_UPDATABLE if k in fields}
    c = con or connect()
    try:
        if cols:
            sets = ", ".join("%s=?" % k for k in cols)
            c.execute("UPDATE goals SET %s WHERE id=?" % sets, (*cols.values(), goal_id))
            c.commit()
        return _goal_from_row(c.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone())
    finally:
        if con is None:
            c.close()


def goal_delete(goal_id, con=None):
    c = con or connect()
    try:
        n = c.execute("DELETE FROM goals WHERE id=?", (goal_id,)).rowcount
        c.commit()
        return n
    finally:
        if con is None:
            c.close()


def goals_visible(uid, team_ids, con=None):
    """Goals the user may see: legacy/local (owner NULL) + their own + shared with one of their teams."""
    c = con or connect()
    try:
        clause, args = ["owner_user_id IS NULL"], []
        if uid is not None:
            clause.append("owner_user_id=?")
            args.append(uid)
        tids = list(team_ids or [])
        if tids:
            clause.append("team_id IN (%s)" % ",".join("?" * len(tids)))
            args.extend(tids)
        rows = c.execute("SELECT * FROM goals WHERE %s ORDER BY created_at" % " OR ".join(clause), args)
        return [_goal_from_row(r) for r in rows.fetchall()]
    finally:
        if con is None:
            c.close()


def set_demo_team(demo_id, team_id, requester_uid, con=None):
    """Share (team_id set) or unshare (None) the requester's OWN copy of a demo with a team. The user
    must have the demo in their library and belong to the team. Returns True on success."""
    c = con or connect()
    try:
        if requester_uid is None:
            return False
        d = c.execute("SELECT sha1 FROM demos WHERE sha1=? OR key=?", (demo_id, demo_id)).fetchone()
        if not d:
            return False
        sha = d["sha1"]
        if not c.execute("SELECT 1 FROM user_demos WHERE user_id=? AND sha1=?",
                         (requester_uid, sha)).fetchone():
            return False                               # not in your library -> can't share it
        if team_id is not None:
            if not c.execute("SELECT 1 FROM team_members WHERE team_id=? AND user_id=?",
                             (team_id, requester_uid)).fetchone():
                return False                           # not a member of that team
        c.execute("UPDATE user_demos SET team_id=? WHERE user_id=? AND sha1=?",
                  (team_id, requester_uid, sha))
        c.commit()
        return True
    finally:
        if con is None:
            c.close()


# ---- visibility (Stage 5): what a logged-in user may see --------------------
# `scope` is None (open/local mode -> no restriction) or {"uid": int|None, "team_ids": [int,...]}.
# Membership model: a demo is visible if it's in the user's library (user_demos), OR another member
# shared their copy to one of the user's teams, OR it has NO members at all ("ownerless": legacy/local
# uploads, included only when scope['ownerless'] is set so unclaimed demos never leak on a locked site).
def _visibility(scope, sha_col="demos.sha1"):
    """Return (sql_clause, args) restricting the demos table (or a join where `sha_col` names the demos
    sha1 column, e.g. 'd.sha1'). MUST be a qualified column (demos.sha1 / d.sha1) so the correlated
    user_demos subquery references the OUTER demo, not its own inner sha1. ("", []) = unrestricted."""
    if scope is None:
        return "", []
    parts, args = [], []
    if scope.get("ownerless"):
        parts.append("NOT EXISTS (SELECT 1 FROM user_demos ud WHERE ud.sha1=%s)" % sha_col)
    mem, margs = [], []
    if scope.get("uid") is not None:
        mem.append("ud.user_id=?")
        margs.append(scope["uid"])
    tids = scope.get("team_ids") or []
    if tids:
        mem.append("ud.team_id IN (%s)" % ",".join("?" * len(tids)))
        margs.extend(tids)
    if mem:
        parts.append("EXISTS (SELECT 1 FROM user_demos ud WHERE ud.sha1=%s AND (%s))"
                     % (sha_col, " OR ".join(mem)))
        args.extend(margs)
    if not parts:
        return "1=0", []                               # scoped to nothing -> show nothing (never all)
    return "(" + " OR ".join(parts) + ")", args


def accessible(demo_id, scope, con=None):
    """Whether `scope` may load this demo (by sha1 or cache key). Un-indexed (e.g. the sample) and
    ownerless demos are always allowed; otherwise the user must have it or share a team copy."""
    if scope is None:
        return True
    c = con or connect()
    try:
        d = c.execute("SELECT sha1 FROM demos WHERE sha1=? OR key=?", (demo_id, demo_id)).fetchone()
        if not d:
            return True                                # un-indexed (e.g. the sample) -- not restricted
        sha = d["sha1"]
        if scope.get("uid") is not None and c.execute(
                "SELECT 1 FROM user_demos WHERE sha1=? AND user_id=?", (sha, scope["uid"])).fetchone():
            return True
        tids = scope.get("team_ids") or []
        if tids and c.execute("SELECT 1 FROM user_demos WHERE sha1=? AND team_id IN (%s)"
                              % ",".join("?" * len(tids)), [sha] + list(tids)).fetchone():
            return True
        members = c.execute("SELECT 1 FROM user_demos WHERE sha1=?", (sha,)).fetchone()
        return bool(scope.get("ownerless")) if not members else False   # ownerless only in local/open
    finally:
        if con is None:
            c.close()


def can_delete(demo_id, scope, con=None):
    """Whether `scope` may remove this demo. In the membership model 'delete' = remove YOUR copy, so
    any member may (the shared parse is only wiped once the last member is gone). Team viewers without
    their own copy cannot; ownerless demos only in local/open mode."""
    if scope is None:
        return True
    c = con or connect()
    try:
        d = c.execute("SELECT sha1 FROM demos WHERE sha1=? OR key=?", (demo_id, demo_id)).fetchone()
        if not d:
            return True                                # un-indexed
        sha = d["sha1"]
        if scope.get("uid") is not None and c.execute(
                "SELECT 1 FROM user_demos WHERE sha1=? AND user_id=?", (sha, scope["uid"])).fetchone():
            return True                                # it's in your library -> you can remove it
        members = c.execute("SELECT 1 FROM user_demos WHERE sha1=?", (sha,)).fetchone()
        return bool(scope.get("ownerless")) if not members else False
    finally:
        if con is None:
            c.close()


def visible_predicate(scope, con=None):
    """A fast f(demo_id)->bool for filtering the file-based library list (one query). Membership-based:
    visible if it's in the user's library / shared to their team / has no members (ownerless, local
    only) / un-indexed (sample)."""
    if scope is None:
        return lambda _id: True
    c = con or connect()
    try:
        indexed = {r["sha1"] for r in c.execute("SELECT sha1 FROM demos")}
        uid, tids = scope.get("uid"), set(scope.get("team_ids") or [])
        visible, has_member = set(), set()
        for r in c.execute("SELECT sha1, user_id, team_id FROM user_demos"):
            has_member.add(r["sha1"])
            if (uid is not None and r["user_id"] == uid) or (r["team_id"] is not None and r["team_id"] in tids):
                visible.add(r["sha1"])
    finally:
        if con is None:
            c.close()
    own_ok = bool(scope.get("ownerless"))

    def ok(demo_id):
        sid = str(demo_id)
        if sid not in indexed:
            return True                                # un-indexed (e.g. the sample)
        if sid in visible:
            return True                                # my copy or a team share
        return own_ok if sid not in has_member else False   # no members -> ownerless (local only)
    return ok


# ---- read path (mirrors matchindex output shapes) ---------------------------
def _match_row(con, d):
    players = [{"steamid": r["steamid"], "name": r["name"],
                **{f: r[f] for f in _PLAYER_FIELDS}}
               for r in con.execute("SELECT * FROM demo_players WHERE sha1=?", (d["sha1"],))]
    return {"id": d["sha1"], "key": d["key"], "map": d["map"], "rounds": d["rounds"],
            "created_at": d["created_at"], "duration": d["duration"],
            "score": d["score"], "players": players}


def list_matches(cache_dir=None, scope=None, con=None):
    """Match summaries newest-first, from the index. `scope` (None=open/local mode) restricts
    visibility to the user's own + team-shared + ownerless demos."""
    c = con or connect()
    try:
        clause, args = _visibility(scope)
        sql = "SELECT * FROM demos" + (" WHERE " + clause if clause else "") + " ORDER BY created_at DESC"
        demos = c.execute(sql, args).fetchall()
        return [_match_row(c, d) for d in demos]
    finally:
        if con is None:
            c.close()


def all_players(cache_dir=None, scope=None, con=None):
    """Deduped players across visible matches, by n_matches desc then name."""
    c = con or connect()
    try:
        clause, args = _visibility(scope, "d.sha1")
        if clause:
            sql = ("SELECT dp.steamid AS steamid, MAX(dp.name) AS name, COUNT(*) AS n_matches "
                   "FROM demo_players dp JOIN demos d ON d.sha1=dp.sha1 WHERE " + clause
                   + " GROUP BY dp.steamid")
        else:
            sql = ("SELECT steamid, MAX(name) AS name, COUNT(*) AS n_matches "
                   "FROM demo_players GROUP BY steamid")
        rows = c.execute(sql, args).fetchall()
        out = [{"steamid": r["steamid"], "name": r["name"], "n_matches": r["n_matches"]}
               for r in rows]
        out.sort(key=lambda r: (-r["n_matches"], (r["name"] or "").lower()))
        return out
    finally:
        if con is None:
            c.close()


def player_trends(steamid, cache_dir=None, scope=None, con=None):
    """Trend series + averages + first/second-half delta for one player (matches matchindex shape).
    `scope` (None=open/local mode) restricts which matches count toward the trend."""
    c = con or connect()
    try:
        clause, vargs = _visibility(scope, "d.sha1")
        sql = ("SELECT dp.*, d.map AS map, d.key AS key, d.created_at AS created_at "
               "FROM demo_players dp JOIN demos d ON d.sha1=dp.sha1 WHERE dp.steamid=?")
        args = [str(steamid)]
        if clause:
            sql += " AND " + clause
            args += vargs
        sql += " ORDER BY d.created_at ASC"
        rows = c.execute(sql, args).fetchall()
        series, name = [], None
        for r in rows:
            name = r["name"] or name
            e = {"key": r["key"], "map": r["map"], "created_at": r["created_at"], "kd": r["kd"]}
            for s in _TREND_STATS:
                e[s] = r[s]
            series.append(e)
        n = len(series)
        averages = {}
        for s in _TREND_STATS + ["kd"]:
            averages[s] = mi._round(s, sum(e[s] for e in series) / n) if n else 0.0
        trend = {}
        if n >= 2:
            half = n // 2
            first, second = series[:half], series[half:]
            for s in _TREND_STATS:
                trend[s] = mi._round(s, sum(e[s] for e in second) / len(second)
                                     - sum(e[s] for e in first) / len(first))
        return {"steamid": str(steamid), "name": name, "n_matches": n,
                "series": series, "averages": averages, "trend": trend}
    finally:
        if con is None:
            c.close()


# ---- squad auto-detection + roster curation (Pro) ---------------------------
def squad_for(uid, scope=None, con=None):
    """Detect who `uid` plays WITH: returns (you, co_players). `you` = their own {steamid,name} (the
    account's SteamID matched against player rows) or None if it isn't in any visible match. `co_players`
    = [{steamid,name,shared}] for everyone who appeared in the user's matches, sorted by shared-match
    count desc. The caller marks shared>=2 as the auto-squad."""
    c = con or connect()
    try:
        urow = c.execute("SELECT steam_id_64 FROM users WHERE id=?", (uid,)).fetchone() if uid else None
        my_sid = urow["steam_id_64"] if urow else None
        if not my_sid:
            return None, []
        clause, vargs = _visibility(scope, "d.sha1")
        mine = [r["sha1"] for r in c.execute(
            "SELECT DISTINCT dp.sha1 FROM demo_players dp JOIN demos d ON d.sha1=dp.sha1 "
            "WHERE dp.steamid=?" + ((" AND " + clause) if clause else ""), [str(my_sid)] + vargs)]
        if not mine:
            return None, []
        ph = ",".join("?" * len(mine))
        you_name = c.execute("SELECT MAX(name) n FROM demo_players WHERE steamid=? AND sha1 IN (%s)" % ph,
                             [str(my_sid)] + mine).fetchone()["n"]
        rows = c.execute(
            "SELECT steamid, MAX(name) name, COUNT(DISTINCT sha1) shared FROM demo_players "
            "WHERE steamid<>? AND sha1 IN (%s) GROUP BY steamid ORDER BY shared DESC, name" % ph,
            [str(my_sid)] + mine).fetchall()
        you = {"steamid": str(my_sid), "name": you_name or "You"}
        return you, [{"steamid": r["steamid"], "name": r["name"], "shared": r["shared"]} for r in rows]
    finally:
        if con is None:
            c.close()


def roster_overrides(uid, con=None):
    """The user's manual squad overrides: {steamid: status} where status is 'in' or 'out'."""
    if uid is None:
        return {}
    c = con or connect()
    try:
        return {r["steamid"]: r["status"]
                for r in c.execute("SELECT steamid, status FROM user_roster WHERE user_id=?", (uid,))}
    finally:
        if con is None:
            c.close()


def set_roster_entry(uid, steamid, status, name=None, con=None):
    """Pin ('in') or hide ('out') a player in the user's squad. status=None clears the override
    (reverts to auto). Returns True."""
    sid = str(steamid or "").strip()
    if uid is None or not sid:
        return False
    c = con or connect()
    try:
        if status not in ("in", "out"):
            c.execute("DELETE FROM user_roster WHERE user_id=? AND steamid=?", (uid, sid))
        else:
            c.execute("INSERT INTO user_roster(user_id,steamid,status,name) VALUES(?,?,?,?) "
                      "ON CONFLICT(user_id,steamid) DO UPDATE SET status=excluded.status, "
                      "name=COALESCE(excluded.name, user_roster.name)",
                      (uid, sid, status, (str(name)[:64] if name else None)))
        c.commit()
        return True
    finally:
        if con is None:
            c.close()


# ---- helpers ----------------------------------------------------------------
def _safe_created_at(key):
    path = os.path.join(mi.CACHE_DIR, key + ".json")
    if os.path.exists(path):
        try:
            return mi._created_at(path, key)
        except Exception:
            return None
    return None


def _data_has_duck(data):
    for f in (data.get("frames") or []):
        for pl in (f.get("players") or []):
            if isinstance(pl, dict):
                return "duck" in pl
    return False
