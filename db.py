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

# Leetify-comparable per-match perf metrics, stored per (sha1, steamid) for the dashboard "last N
# matches" average + the per-match Player panel. Each entry: db_col -> (player_field, quality_key).
# quality_key gates no-fake-data: the value is stored ONLY when analytics flagged it perf_quality
# == "exact"; otherwise NULL, so SQL AVG()/COUNT() skip it (an unavailable metric never drags the
# average toward 0). quality_key=None = always-exact counts (0 thrown IS a real value).
_PERF_DB_FIELDS = {
    "headshot_accuracy":                   ("headshot_accuracy", "accuracy_head"),
    "he_dmg_per_he":                       ("he_dmg_per_he", "he_foes_damage_avg"),
    "accuracy":                            ("accuracy", "accuracy"),
    "flashes_hit_foe_per_game":            ("flashes_hit_foe_per_game", "flashbang_hit_foe"),
    "flashes_hit_friend_per_game":         ("flashes_hit_friend_per_game", "flashbang_hit_friend"),
    "total_flash_blind_duration_per_game": ("total_flash_blind_duration_per_game", "total_flash_blind_duration"),
    "flash_foe_avg_duration":              ("flash_foe_avg_duration", "flashbang_hit_foe_avg_duration"),
    "hes":            ("hes", None),
    "flashes_thrown": ("flashes_thrown", None),
    "smokes":         ("smokes", None),
    "molotovs":       ("molotovs", None),
    # gated flash scalars used by the cross-demo dashboard utility aggregates. Gated on the SAME
    # signal as the other flash metrics (perf_quality.flashbang_hit_foe == "exact" iff the demo has
    # player_blind data): a demo without player_blind stores NULL here, never a fabricated 0, so the
    # dashboard flash aggregate is omitted/None rather than wrongly showing "0 enemies flashed".
    "enemy_flashed":  ("enemy_flashed", "flashbang_hit_foe"),
    "team_flashed":   ("team_flashed", "flashbang_hit_foe"),
    "blind_time":     ("blind_time", "flashbang_hit_foe"),
    # total HE damage (HE-only, excludes molotov/fire). Always exact -- 0 HE damage IS a real value,
    # like the other 0-default counts -- so quality_key=None (stored as-is).
    "he_dmg":         ("he_dmg", None),
}
_PERF_DB_COLS = list(_PERF_DB_FIELDS)

# Subset of demo_players columns the cross-demo dashboard reads for its utility / flash / perf
# aggregates (all already persisted above). Kept as one list so the dashboard query and the tests
# stay in lock-step. `udr` lives in _PLAYER_FIELDS (per-round util damage) and is added by callers.
_DASH_UTIL_COLS = ["smokes", "flashes_thrown", "hes", "molotovs",
                   "enemy_flashed", "team_flashed", "blind_time", "he_dmg"]
_DASH_PERF_COLS = ["headshot_accuracy", "he_dmg_per_he", "accuracy",
                   "flashes_hit_foe_per_game", "flashes_hit_friend_per_game",
                   "total_flash_blind_duration_per_game"]


def _perf_db_values(p):
    """Per-match perf values for one player dict, aligned to _PERF_DB_COLS. Returns the value only when
    its perf_quality flag is 'exact' (else None -> NULL), so unavailable metrics are skipped by AVG()
    rather than fabricated as 0. Falls back to the raw value (None-preserving) if no quality block."""
    quality = p.get("perf_quality") if isinstance(p.get("perf_quality"), dict) else {}
    out = []
    for col in _PERF_DB_COLS:
        field, qkey = _PERF_DB_FIELDS[col]
        v = p.get(field)
        if qkey is not None and quality:
            status = (quality.get(qkey) or {}).get("status")
            if status != "exact":
                v = None
        out.append(v if isinstance(v, (int, float)) else None)
    return out

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
CREATE TABLE IF NOT EXISTS admin_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  admin_uid INTEGER,
  action TEXT NOT NULL,
  target_type TEXT,
  target_id TEXT,
  detail TEXT,
  ts TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);
-- Admin-authored callout edits/boundaries, layered over the static JSON seed. Once a map has ANY
-- override rows the admin fully owns its callout set (seed becomes fallback for un-edited maps only).
CREATE TABLE IF NOT EXISTS callout_overrides (
  map TEXT NOT NULL,
  callout_id TEXT NOT NULL,
  name TEXT,
  aliases TEXT,            -- JSON array of alias strings
  side TEXT,               -- 't' | 'ct' | 'both'
  world_x REAL, world_y REAL,
  boundary TEXT,           -- JSON array of [x,y] world points (polygon), or NULL
  notes TEXT,
  sort_order INTEGER DEFAULT 0,
  updated_at TEXT,
  updated_by INTEGER,
  PRIMARY KEY (map, callout_id)
);
-- Per-(map, zone) position samples learned from real demo deaths -> centroid/boundary suggestions.
CREATE TABLE IF NOT EXISTS callout_samples (
  map TEXT NOT NULL,
  zone TEXT NOT NULL,      -- raw last_place_name token
  n INTEGER DEFAULT 0,
  sum_x REAL DEFAULT 0, sum_y REAL DEFAULT 0,
  min_x REAL, min_y REAL, max_x REAL, max_y REAL,
  updated_at TEXT,
  PRIMARY KEY (map, zone)
);
-- Guard so each demo's samples fold into callout_samples at most once (reparse/re-index safe).
CREATE TABLE IF NOT EXISTS callout_sample_sources (
  sha1 TEXT PRIMARY KEY,
  ts TEXT
);
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
        _ensure_column(c, "jobs", "team_id", "INTEGER")             # upload destination: target team (NULL = personal)
        _ensure_column(c, "user_demos", "archived", "INTEGER DEFAULT 0")  # #22: replay removed to free space, stats kept
        _ensure_column(c, "user_demos", "tag", "TEXT")             # per-user label (scrim/mm/faceit/... or free-form); NULL = untagged
        for _pc in _PERF_DB_COLS:                                   # #117: Leetify-comparable perf metrics (nullable; NULL = unavailable that match)
            _ensure_column(c, "demo_players", _pc, "REAL")
        if not had_user_demos:
            c.execute("""INSERT OR IGNORE INTO user_demos(user_id, sha1, team_id, created_at)
                         SELECT owner_user_id, sha1, team_id, created_at
                         FROM demos WHERE owner_user_id IS NOT NULL""")
        c.commit()
    finally:
        if con is None:
            c.close()


# ---- write path -------------------------------------------------------------
def index_demo(data, key, created_at=None, owner_user_id=None, con=None, team_id=None):
    """Upsert one parsed demo's summary + player rows. `data` = parsed cache dict (with analytics),
    `key` = its cache filename stem. `owner_user_id` stamps the uploader (NULL in local mode).
    `team_id` is the upload destination for the uploader's library copy (NULL = personal).
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
            # core stats via mi._num (0-default is fine -- always present); perf via _perf_db_values
            # (None-preserving + quality-gated, so an unavailable metric is NULL, never a fake 0).
            rows.append((sha, str(sid), p.get("name"),
                         *[mi._num(p.get(f)) for f in _PLAYER_FIELDS],
                         *_perf_db_values(p)))
        if rows:
            allcols = _PLAYER_FIELDS + _PERF_DB_COLS
            c.executemany(
                "INSERT OR REPLACE INTO demo_players(sha1,steamid,name,"
                + ",".join(allcols) + ") VALUES(" + ",".join(["?"] * (3 + len(allcols))) + ")",
                rows)
        # AUTOMATIC per-user library membership: every uploader (including the 2nd person who uploads
        # the same match -> cache hit, but still re-saved) gets their own copy. Idempotent.
        if owner_user_id is not None:
            # archived=0: a fresh upload IS a full replay. ON CONFLICT also resets archived to 0 so
            # RE-uploading a previously-deleted (stats-only) match restores it as a watchable replay.
            # team_id = the chosen upload destination; COALESCE on conflict so a value-less re-index
            # (e.g. a future reparse) never wipes an existing Personal/Team assignment -- only an
            # explicit team_id moves it (and Share -> "Make private" still sets it back to personal).
            c.execute("INSERT INTO user_demos(user_id, sha1, team_id, created_at, archived) VALUES(?,?,?,?,0) "
                      "ON CONFLICT(user_id, sha1) DO UPDATE SET archived=0, "
                      "team_id=COALESCE(excluded.team_id, team_id)",
                      (owner_user_id, sha, team_id, datetime.datetime.now().isoformat(timespec="seconds")))
        # learn callout positions: fold this demo's per-zone death-coordinate aggregate (computed at
        # parse time as analytics.position_samples) into the rolling callout_samples table, once per sha.
        samples = a.get("position_samples")
        if isinstance(samples, dict) and samples and data.get("map"):
            fold_position_samples(sha, data.get("map"), samples, con=c)
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
    """How many FULL-REPLAY demos are in this user's library (for the Free upload cap). Archived demos
    (#22: replay removed, stats kept) don't count -- archiving frees a slot."""
    if user_id is None:
        return 0
    c = con or connect()
    try:
        return c.execute("SELECT COUNT(*) n FROM user_demos WHERE user_id=? AND archived=0",
                         (user_id,)).fetchone()["n"]
    finally:
        if con is None:
            c.close()


def user_has_demo(user_id, sha1, con=None):
    """Does this user already have this demo (by content sha1) in their library, NOT archived? Used to
    skip re-uploading a demo they already have. user_id None (local/open mode) -> check if ANY non-
    archived copy exists (single-owner install). Returns the demo's loader key if present, else None."""
    c = con or connect()
    try:
        if user_id is None:
            r = c.execute("SELECT d.key FROM demos d WHERE d.sha1=?", (str(sha1),)).fetchone()
            return (r["key"] if r else None)
        r = c.execute(
            "SELECT d.key FROM user_demos ud JOIN demos d ON d.sha1=ud.sha1 "
            "WHERE ud.user_id=? AND ud.sha1=? AND ud.archived=0",
            (user_id, str(sha1))).fetchone()
        return (r["key"] if r else None)
    finally:
        if con is None:
            c.close()


def set_archived(user_id, sha1, archived=1, con=None):
    """Mark/unmark one user's copy of a demo as archived. Returns how many members still hold it as a
    FULL replay (archived=0) -- 0 means the shared cache/.dem can be deleted (no one can watch it)."""
    c = con or connect()
    try:
        c.execute("UPDATE user_demos SET archived=? WHERE user_id=? AND sha1=?",
                  (1 if archived else 0, user_id, sha1))
        c.commit()
        return c.execute("SELECT COUNT(*) n FROM user_demos WHERE sha1=? AND archived=0",
                         (sha1,)).fetchone()["n"]
    finally:
        if con is None:
            c.close()


def match_for_stats(sha1, con=None):
    """Compact match record (summary + per-player aggregates) for one demo, from the index -- the
    source for the retained .txt written when a replay is deleted. None if not indexed."""
    c = con or connect()
    try:
        d = c.execute("SELECT map, rounds, score, created_at FROM demos WHERE sha1=?", (sha1,)).fetchone()
        if not d:
            return None
        cols = ["kills", "deaths", "kd", "adr", "kast", "hltv", "open_wr", "traded_pct", "udr"]
        players = []
        for r in c.execute("SELECT steamid, name, " + ",".join(cols) + " FROM demo_players WHERE sha1=?",
                           (sha1,)):
            players.append({"steamid": r["steamid"], "name": r["name"], **{f: r[f] for f in cols}})
        return {"map": d["map"], "rounds": d["rounds"], "score": d["score"],
                "created_at": d["created_at"], "players": players}
    finally:
        if con is None:
            c.close()


def archived_library_rows(scope, con=None):
    """#22: the caller's archived demos as file-library-shaped rows (id, map, rounds, score, date,
    archived) sourced from the index -- so the library still lists them after the heavy cache is gone."""
    if scope is None:
        return []
    uid, tids = scope.get("uid"), set(scope.get("team_ids") or [])
    c = con or connect()
    try:
        rows = c.execute(
            """SELECT DISTINCT d.sha1, d.map, d.rounds, d.score, d.created_at,
                      d.schema_version, ud.team_id
               FROM user_demos ud JOIN demos d ON d.sha1 = ud.sha1
               WHERE ud.archived=1 AND (ud.user_id=? OR ud.team_id IN (%s))
               ORDER BY d.created_at DESC""" % (",".join("?" * len(tids)) or "NULL"),
            tuple([uid] + list(tids))).fetchall()
        out = []
        for r in rows:
            ct, t = 0, 0
            if r["score"] and "-" in str(r["score"]):
                try:
                    ct, t = (int(x) for x in str(r["score"]).split("-", 1))
                except ValueError:
                    ct, t = 0, 0
            out.append({"id": r["sha1"], "map": r["map"], "rounds": r["rounds"],
                        "score": {"ct": ct, "t": t}, "date": r["created_at"], "archived": True})
        return out
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


def log_admin_action(admin_uid, action, target_type=None, target_id=None, detail=None, con=None):
    c = con or connect()
    try:
        c.execute(
            "INSERT INTO admin_audit(admin_uid,action,target_type,target_id,detail) VALUES(?,?,?,?,?)",
            (admin_uid, action, target_type,
             str(target_id) if target_id is not None else None,
             json.dumps(detail) if detail is not None else None))
        c.commit()
    except Exception:
        pass   # audit log must never break the action it wraps
    finally:
        if con is None:
            c.close()


def get_admin_audit(limit=100, con=None):
    c = con or connect()
    try:
        rows = c.execute("""
            SELECT a.id, a.action, a.target_type, a.target_id, a.detail, a.ts,
                   u.display_name AS admin_name, u.steam_id_64 AS admin_steamid
            FROM admin_audit a
            LEFT JOIN users u ON u.id = a.admin_uid
            ORDER BY a.id DESC LIMIT ?
        """, (limit,)).fetchall()
        out = []
        for r in rows:
            out.append({"id": r["id"], "action": r["action"],
                        "target_type": r["target_type"], "target_id": r["target_id"],
                        "detail": json.loads(r["detail"]) if r["detail"] else None,
                        "ts": r["ts"], "admin_name": r["admin_name"],
                        "admin_steamid": r["admin_steamid"]})
        return out
    finally:
        if con is None:
            c.close()


def get_user_detail(user_id, con=None):
    c = con or connect()
    try:
        u = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not u:
            return None
        demos_rows = c.execute("""
            SELECT d.sha1, d.key, d.map, d.rounds, d.created_at, d.score
            FROM user_demos ud JOIN demos d ON d.sha1=ud.sha1
            WHERE ud.user_id=? AND (ud.archived IS NULL OR ud.archived=0)
            ORDER BY d.created_at DESC LIMIT 10
        """, (user_id,)).fetchall()
        demo_count_row = c.execute("""
            SELECT COUNT(*) n FROM user_demos
            WHERE user_id=? AND (archived IS NULL OR archived=0)
        """, (user_id,)).fetchone()
        demo_count = demo_count_row["n"] if demo_count_row else 0
        teams_rows = c.execute("""
            SELECT t.id, t.name, t.created_at AS team_created_at, tm.role, tm.joined_at
            FROM team_members tm JOIN teams t ON t.id=tm.team_id
            WHERE tm.user_id=?
        """, (user_id,)).fetchall()
        job_rows = c.execute("""
            SELECT id, filename, status, created_at, finished_at, bytes, error
            FROM jobs WHERE owner_user_id=? ORDER BY created_at DESC LIMIT 5
        """, (user_id,)).fetchall()
        return {
            "id": u["id"], "steam_id_64": u["steam_id_64"],
            "display_name": u["display_name"], "avatar_url": u["avatar_url"],
            "tier": u["tier"], "pro_until": u["pro_until"], "role": u["role"],
            "created_at": u["created_at"], "last_login_at": u["last_login_at"],
            "demo_count": demo_count,
            "demos": [dict(d) for d in demos_rows],
            "teams": [dict(t) for t in teams_rows],
            "recent_jobs": [{"id": j["id"], "filename": j["filename"],
                             "status": j["status"], "created_at": j["created_at"],
                             "finished_at": j["finished_at"], "bytes": j["bytes"],
                             "error": (j["error"] or "")[:200]} for j in job_rows],
        }
    finally:
        if con is None:
            c.close()


def list_all_teams_admin(con=None):
    c = con or connect()
    try:
        rows = c.execute("""
            SELECT t.id, t.name, t.invite_code, t.created_at,
                   u.display_name AS owner_name, u.steam_id_64 AS owner_steamid,
                   COUNT(DISTINCT tm.user_id) AS member_count,
                   COUNT(DISTINCT d.sha1) AS demo_count,
                   MAX(d.created_at) AS last_activity
            FROM teams t
            LEFT JOIN users u ON u.id=t.owner_user_id
            LEFT JOIN team_members tm ON tm.team_id=t.id
            LEFT JOIN demos d ON d.team_id=t.id
            GROUP BY t.id ORDER BY t.created_at DESC
        """).fetchall()
        return [dict(r) for r in rows]
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


TAG_MAXLEN = 32   # per-user demo tag is clamped/trimmed to this many chars


def set_demo_tag(user_id, sha1, tag, con=None):
    """Set (or clear) the per-USER tag on the caller's OWN copy of a demo. Each member tags their own
    membership row independently, so this only ever touches `user_id`'s row. The tag is trimmed and
    clamped to TAG_MAXLEN chars; an empty/whitespace-only tag (or None) clears it back to NULL.
    Returns True if a membership row was updated (the user holds the demo), else False."""
    if user_id is None:
        return False
    c = con or connect()
    try:
        d = c.execute("SELECT sha1 FROM demos WHERE sha1=? OR key=?", (sha1, sha1)).fetchone()
        sha = d["sha1"] if d else sha1
        clean = (tag or "").strip()[:TAG_MAXLEN].strip() or None
        cur = c.execute("UPDATE user_demos SET tag=? WHERE user_id=? AND sha1=?", (clean, user_id, sha))
        c.commit()
        return cur.rowcount > 0
    finally:
        if con is None:
            c.close()


def tags_for_user(user_id, con=None):
    """Distinct non-null tags this user has applied to their demos, alphabetical (case-insensitive) --
    for building the library/dashboard/trends filter chips."""
    if user_id is None:
        return []
    c = con or connect()
    try:
        rows = c.execute("SELECT DISTINCT tag FROM user_demos WHERE user_id=? AND tag IS NOT NULL",
                         (user_id,))
        return sorted((r["tag"] for r in rows), key=lambda t: t.lower())
    finally:
        if con is None:
            c.close()


# ---- visibility (Stage 5): what a logged-in user may see --------------------
# `scope` is None (open/local mode -> no restriction) or {"uid": int|None, "team_ids": [int,...]}.
# Membership model: a demo is visible if it's in the user's library (user_demos), OR another member
# shared their copy to one of the user's teams, OR it has NO members at all ("ownerless": legacy/local
# uploads, included only when scope['ownerless'] is set so unclaimed demos never leak on a locked site).
def _visibility(scope, sha_col="demos.sha1", exclude_archived=False):
    """Return (sql_clause, args) restricting the demos table (or a join where `sha_col` names the demos
    sha1 column, e.g. 'd.sha1'). MUST be a qualified column (demos.sha1 / d.sha1) so the correlated
    user_demos subquery references the OUTER demo, not its own inner sha1. ("", []) = unrestricted.
    `exclude_archived`: also require a NON-archived membership row -- so a demo whose replay the user
    deleted (kept only as compact stats) drops out of clickable match lists, while still counting toward
    trends/profile (those callers leave this False).

    `scope["workspace"]` narrows to ONE dashboard context instead of the default own+team OR:
      "personal"      -> only the user's own copies NOT assigned to a team
      ("team", <id>)  -> only copies shared with that team (any member's), independent of `team_ids`."""
    if scope is None:
        return "", []
    arch = " AND ud.archived=0" if exclude_archived else ""
    ws = scope.get("workspace")
    if ws == "personal":
        uid = scope.get("uid")
        if uid is None:
            return "1=0", []                           # personal workspace needs a signed-in user
        return ("EXISTS (SELECT 1 FROM user_demos ud WHERE ud.sha1=%s AND ud.user_id=? "
                "AND ud.team_id IS NULL%s)" % (sha_col, arch)), [uid]
    if isinstance(ws, (list, tuple)) and len(ws) == 2 and ws[0] == "team":
        return ("EXISTS (SELECT 1 FROM user_demos ud WHERE ud.sha1=%s AND ud.team_id=?%s)"
                % (sha_col, arch)), [ws[1]]
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
        arch = " AND ud.archived=0" if exclude_archived else ""
        parts.append("EXISTS (SELECT 1 FROM user_demos ud WHERE ud.sha1=%s%s AND (%s))"
                     % (sha_col, arch, " OR ".join(mem)))
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
        for r in c.execute("SELECT sha1, user_id, team_id, archived FROM user_demos"):
            has_member.add(r["sha1"])                   # still a member (for ownerless detection)
            if r["archived"]:
                continue                                # deleted their replay -> not in THEIR library
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


def library_membership(scope, con=None):
    """Classify each demo by sha1 from `scope`'s point of view, for the Personal/Team library split:
    `{sha1: {"personal": bool, "team_ids": [int,...]}}`, plus a `"tag": str` entry on demos the VIEWER
    has tagged. `personal` = the user holds a non-team copy (their own upload, not assigned to a team).
    `team_ids` = teams the user belongs to that the demo is shared with (their own share or a teammate's).
    `tag` = the VIEWER's own per-user tag (only present when set; teammates' copies keep their own tags --
    the dashboard only cares about the viewer's). Open/local mode (scope None) -> empty map; callers treat
    a missing entry as personal. One query."""
    if scope is None:
        return {}
    uid = scope.get("uid")
    my_teams = set(scope.get("team_ids") or [])
    c = con or connect()
    try:
        out = {}
        for r in c.execute("SELECT sha1, user_id, team_id, archived, tag FROM user_demos"):
            if r["archived"]:
                continue                               # deleted their replay -> not in their library
            ent = out.get(r["sha1"])
            if ent is None:
                ent = out[r["sha1"]] = {"personal": False, "team_ids": []}
            tid = r["team_id"]
            if tid is not None and tid in my_teams and tid not in ent["team_ids"]:
                ent["team_ids"].append(tid)
            if uid is not None and r["user_id"] == uid:
                if r["tag"]:
                    ent["tag"] = r["tag"]              # the viewer's own tag (absent if untagged)
                if tid is None:
                    ent["personal"] = True            # my own copy, not assigned to a team
        return out
    finally:
        if con is None:
            c.close()


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
        # exclude_archived: a match the user deleted the replay of (stats-only) must not appear in the
        # clickable recent/all-matches lists -- clicking it would 404. Trends/profile still count it.
        clause, args = _visibility(scope, exclude_archived=True)
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


def player_perf_averages(steamid, *, scope=None, limit=15, con=None):
    """Average the Leetify-comparable perf metrics over a player's most recent `limit` matches
    (scope-aware). A metric that was unavailable in a match is stored NULL, so SQL AVG()/COUNT() skip
    it -- it never drags the average toward 0, and its honest per-metric sample size is returned in
    `counts`. Returns {steamid, name, window, averages:{col:avg|None}, counts:{col:n}}."""
    c = con or connect()
    try:
        clause, vargs = _visibility(scope, "d.sha1")
        inner = ("SELECT dp.name AS name, "
                 + ", ".join("dp.%s AS %s" % (col, col) for col in _PERF_DB_COLS)
                 + " FROM demo_players dp JOIN demos d ON d.sha1=dp.sha1 WHERE dp.steamid=?")
        args = [str(steamid)]
        if clause:
            inner += " AND " + clause
            args += vargs
        inner += " ORDER BY d.created_at DESC LIMIT ?"
        args.append(int(limit))
        agg = ("SELECT COUNT(*) AS window_n, MAX(name) AS name, "
               + ", ".join("AVG(%s) AS %s, COUNT(%s) AS %s__n" % (col, col, col, col)
                           for col in _PERF_DB_COLS)
               + " FROM (" + inner + ")")
        r = c.execute(agg, args).fetchone()
        averages, counts = {}, {}
        for col in _PERF_DB_COLS:
            v = r[col]
            averages[col] = round(v, 4) if isinstance(v, (int, float)) else None
            counts[col] = int(r[col + "__n"] or 0)
        return {"steamid": str(steamid), "name": r["name"], "window": int(r["window_n"] or 0),
                "averages": averages, "counts": counts}
    finally:
        if con is None:
            c.close()


def dashboard_player_metrics(scope=None, con=None):
    """Per-(sha1, steamid) utility / flash / perf columns for the cross-demo dashboard, restricted to
    the matches `scope` may see (same visibility as list_matches). ONE bounded query against the
    already-indexed demo_players rows -- no cache JSON / frames are loaded.

    Returns {sha1: {steamid: {<col>: value|None}}} where the columns are _DASH_UTIL_COLS +
    _DASH_PERF_COLS (+ map under the special key '_map' per sha). A value is None exactly when it was
    unavailable that match (NULL in the DB) -- never a fabricated 0 -- so callers can omit honestly.
    Demos indexed before these columns existed simply yield None for them (re-index/re-parse fills)."""
    cols = _DASH_UTIL_COLS + _DASH_PERF_COLS
    c = con or connect()
    try:
        clause, vargs = _visibility(scope, "d.sha1")
        sql = ("SELECT dp.sha1 AS sha1, dp.steamid AS steamid, d.map AS map, "
               + ", ".join("dp.%s AS %s" % (col, col) for col in cols)
               + " FROM demo_players dp JOIN demos d ON d.sha1=dp.sha1")
        if clause:
            sql += " WHERE " + clause
        out = {}
        for r in c.execute(sql, vargs):
            per = out.setdefault(r["sha1"], {})
            row = {"_map": r["map"]}
            for col in cols:
                v = r[col]
                row[col] = v if isinstance(v, (int, float)) else None
            per[str(r["steamid"])] = row
        return out
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


# ---- callouts: admin overrides + demo-learned samples -----------------------
_CO_COLS = ("map", "callout_id", "name", "aliases", "side", "world_x", "world_y",
            "boundary", "notes", "sort_order", "updated_at", "updated_by")


def _co_from_row(r):
    """A callout_overrides row -> the same dict shape as a seed callout."""
    def _jload(s, default):
        try:
            return json.loads(s) if s else default
        except (ValueError, TypeError):
            return default
    wx, wy = r["world_x"], r["world_y"]
    return {
        "id": r["callout_id"], "name": r["name"] or r["callout_id"],
        "aliases": _jload(r["aliases"], []),
        "side": r["side"] or "both",
        "world": {"x": wx, "y": wy},
        "boundary": _jload(r["boundary"], None),
        "notes": r["notes"] or "",
        "sort_order": r["sort_order"] if r["sort_order"] is not None else 0,
        "source": "admin",
    }


def callout_overrides_for(map_name, con=None):
    """All admin override callouts for a map (effective set once a map is admin-managed), sorted."""
    c = con or connect()
    try:
        rows = c.execute("SELECT * FROM callout_overrides WHERE map=? ORDER BY sort_order, callout_id",
                         (map_name,)).fetchall()
        return [_co_from_row(r) for r in rows]
    finally:
        if con is None:
            c.close()


def callout_map_is_managed(map_name, con=None):
    """True if an admin has saved a custom callout set for this map (overrides exist)."""
    c = con or connect()
    try:
        return c.execute("SELECT 1 FROM callout_overrides WHERE map=? LIMIT 1",
                         (map_name,)).fetchone() is not None
    finally:
        if con is None:
            c.close()


def callout_overrides_replace(map_name, callouts, admin_uid=None, con=None):
    """Replace the ENTIRE override set for a map with `callouts` (the editor's full desired list).
    Each callout: {id, name, aliases[], side, world:{x,y}, boundary[[x,y]..]|None, notes, sort_order}.
    Deleting a callout in the editor = omitting it here. Returns count saved."""
    now = datetime.datetime.now().isoformat(timespec="seconds")
    c = con or connect()
    try:
        c.execute("DELETE FROM callout_overrides WHERE map=?", (map_name,))
        rows = []
        for i, co in enumerate(callouts or []):
            cid = str(co.get("id") or "").strip()
            if not cid:
                continue
            w = co.get("world") or {}
            wx, wy = w.get("x"), w.get("y")
            bnd = co.get("boundary")
            rows.append((
                map_name, cid, (co.get("name") or cid),
                json.dumps([str(a) for a in (co.get("aliases") or []) if str(a).strip()]),
                (co.get("side") or "both"),
                (float(wx) if wx is not None else None), (float(wy) if wy is not None else None),
                (json.dumps(bnd) if bnd else None),
                (co.get("notes") or ""),
                int(co.get("sort_order", i)),
                now, admin_uid))
        if rows:
            c.executemany(
                "INSERT INTO callout_overrides(map,callout_id,name,aliases,side,world_x,world_y,"
                "boundary,notes,sort_order,updated_at,updated_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        c.commit()
        return len(rows)
    finally:
        if con is None:
            c.close()


def callout_overrides_clear(map_name, con=None):
    """Drop all admin overrides for a map -> revert it to the static JSON seed. Returns rows removed."""
    c = con or connect()
    try:
        n = c.execute("DELETE FROM callout_overrides WHERE map=?", (map_name,)).rowcount
        c.commit()
        return n
    finally:
        if con is None:
            c.close()


def maps_with_overrides(con=None):
    """Set of map names that have admin overrides (for the coverage readout)."""
    c = con or connect()
    try:
        return {r["map"] for r in c.execute("SELECT DISTINCT map FROM callout_overrides")}
    finally:
        if con is None:
            c.close()


def fold_position_samples(sha1, map_name, samples, con=None):
    """Accumulate one demo's per-zone aggregate into callout_samples (idempotent per sha1).
    `samples` = {zone: {n, sum_x, sum_y, min_x, min_y, max_x, max_y}}. Returns zones folded (0 if
    already folded or nothing to fold)."""
    if not map_name or not samples:
        return 0
    c = con or connect()
    try:
        if sha1 and c.execute("SELECT 1 FROM callout_sample_sources WHERE sha1=?", (sha1,)).fetchone():
            return 0                                   # already contributed -> no double-count
        now = datetime.datetime.now().isoformat(timespec="seconds")
        folded = 0
        for zone, s in samples.items():
            if not zone or not s.get("n"):
                continue
            c.execute(
                """INSERT INTO callout_samples(map,zone,n,sum_x,sum_y,min_x,min_y,max_x,max_y,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(map,zone) DO UPDATE SET
                     n=n+excluded.n, sum_x=sum_x+excluded.sum_x, sum_y=sum_y+excluded.sum_y,
                     min_x=MIN(min_x,excluded.min_x), min_y=MIN(min_y,excluded.min_y),
                     max_x=MAX(max_x,excluded.max_x), max_y=MAX(max_y,excluded.max_y),
                     updated_at=excluded.updated_at""",
                (map_name, str(zone), int(s["n"]), float(s["sum_x"]), float(s["sum_y"]),
                 float(s.get("min_x", s["sum_x"] / s["n"])), float(s.get("min_y", s["sum_y"] / s["n"])),
                 float(s.get("max_x", s["sum_x"] / s["n"])), float(s.get("max_y", s["sum_y"] / s["n"])),
                 now))
            folded += 1
        if sha1:
            c.execute("INSERT OR IGNORE INTO callout_sample_sources(sha1, ts) VALUES(?, ?)", (sha1, now))
        c.commit()
        return folded
    finally:
        if con is None:
            c.close()


def callout_learned(map_name, con=None):
    """Learned zone centroids for a map: {zone: {x, y, n, bbox:[minx,miny,maxx,maxy]}} (centroid =
    sample mean). Empty if no samples yet."""
    c = con or connect()
    try:
        out = {}
        for r in c.execute("SELECT * FROM callout_samples WHERE map=? AND n>0", (map_name,)):
            n = r["n"]
            out[r["zone"]] = {
                "x": round(r["sum_x"] / n, 1), "y": round(r["sum_y"] / n, 1), "n": n,
                "bbox": [r["min_x"], r["min_y"], r["max_x"], r["max_y"]],
            }
        return out
    finally:
        if con is None:
            c.close()


def callout_sample_maps(con=None):
    """{map: total_sample_count} across all learned zones (for the coverage readout)."""
    c = con or connect()
    try:
        return {r["map"]: r["t"] for r in
                c.execute("SELECT map, SUM(n) t FROM callout_samples GROUP BY map")}
    finally:
        if con is None:
            c.close()


def demo_keys_for_map(map_name, con=None):
    """(sha1, key) for every indexed demo on a map -- lets sample ingest load only that map's caches."""
    c = con or connect()
    try:
        return [(r["sha1"], r["key"]) for r in
                c.execute("SELECT sha1, key FROM demos WHERE map=?", (map_name,))]
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
