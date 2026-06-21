"""samplemgr.py -- admin-managed replacement of the site's bundled sample demo.

The bundled sample (sample/sample.json.gz -> cache/sample.json) goes stale whenever the
schema/analytics versions bump: it then shows the "Analytics were not computed" fallback,
which is a bad first impression. This module lets an admin upload a fresh .dem, parse it via
the SAME trusted path normal uploads use, validate that real analytics computed, and atomically
swap it in as the runtime sample. /api/sample prefers this admin sample when it's present AND
valid, else falls back to the bundled sample exactly as before.

Design:
  * PURE functions -- they take explicit paths, the already-parsed data dict, and validation
    callables. app.py owns the routes + auth + the actual parse; this owns storage/validate/
    atomic-replace/revert/rebuild so it's all testable without a real .dem or a live server.
  * Persistent layout under DATA_DIR (a mounted volume on the VPS), mirroring db.py's DATA_DIR:
        <DATA_DIR>/sample/current.json        parsed + analytics-tagged sample JSON
        <DATA_DIR>/sample/current.meta.json   metadata sidecar (source/map/rounds/versions/sha1)
        <DATA_DIR>/sample/raw/current.dem      retained raw .dem (RETAINED so rebuild works later)
        <DATA_DIR>/sample/current.json.bak     backup of the prior admin sample (best-effort)
  * Atomicity: write the new parsed JSON to a temp file in the same dir, validate, write metadata,
    then os.replace() into place. The previous sample is never touched until the new one is fully
    written and verified, so a failed upload leaves the old sample serving.

Kept deliberately separate from normal user-demo retention (KEEP_DEM/library/quotas): the admin
sample is site furniture, never a user/team library member and never counted against any quota.
"""
import datetime
import hashlib
import json
import os
import shutil
import tempfile


# ---- paths ------------------------------------------------------------------
def sample_dir(data_dir):
    return os.path.join(data_dir, "sample")


def current_json_path(data_dir):
    return os.path.join(sample_dir(data_dir), "current.json")


def current_meta_path(data_dir):
    return os.path.join(sample_dir(data_dir), "current.meta.json")


def current_raw_path(data_dir):
    return os.path.join(sample_dir(data_dir), "raw", "current.dem")


def _backup_path(data_dir):
    return os.path.join(sample_dir(data_dir), "current.json.bak")


def ensure_dirs(data_dir):
    os.makedirs(sample_dir(data_dir), exist_ok=True)
    os.makedirs(os.path.dirname(current_raw_path(data_dir)), exist_ok=True)


# ---- io helpers -------------------------------------------------------------
def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _atomic_write_json(path, data):
    """Write JSON to a temp file in the SAME dir, then atomically replace -- no partial files."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_sample_", suffix=".json", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(data, out)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _sha1_file(path):
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---- introspection of a parsed dict -----------------------------------------
def _analytics(data):
    a = data.get("analytics") if isinstance(data, dict) else None
    return a if isinstance(a, dict) else None


def _rounds_count(data):
    if not isinstance(data, dict):
        return None
    r = data.get("rounds")
    return len(r) if isinstance(r, list) else None


def _players_count(data):
    if not isinstance(data, dict):
        return 0
    p = data.get("players")
    return len(p) if isinstance(p, list) else 0


def score_of(data):
    """Final score as {"ct": int, "t": int} from the last round, or None if unavailable.
    Mirrors library.final_score but without importing it (keeps this module dependency-light)."""
    if not isinstance(data, dict):
        return None
    rounds = data.get("rounds") or []
    if not rounds:
        return None
    last = rounds[-1] or {}
    try:
        return {"ct": int(last.get("score_ct") or 0), "t": int(last.get("score_t") or 0)}
    except (TypeError, ValueError):
        return None


# Analytics sub-blocks a healthy sample should carry. A block that's absent/empty is "weak"
# (the sample is thin on it) -- surfaced to the admin so they know what a given demo won't show
# off, WITHOUT ever rejecting on it (rejection is reserved for missing-analytics entirely).
_FEATURE_BLOCKS = (
    ("players", "per-player stats"),
    ("rounds", "round timeline"),
    ("round_cards", "round cards"),
    ("position_samples", "position heatmap"),
    ("team_play", "team play"),
    ("insights", "coaching insights"),
)


def weak_features(data):
    """List of human-readable analytics blocks that are missing/empty in this parsed sample.
    Empty list == a rich sample. Never raises; tolerant of odd shapes."""
    a = _analytics(data)
    if not a:
        return []
    weak = []
    for key, label in _FEATURE_BLOCKS:
        v = a.get(key)
        if v is None or (isinstance(v, (list, dict, str)) and len(v) == 0):
            weak.append(label)
    if not a.get("have_econ", True):
        weak.append("buy/economy (no equip data in this demo)")
    return weak


def build_meta(data, source="admin", original_filename=None, source_sha1=None):
    a = _analytics(data)
    return {
        "source": source,
        "original_filename": original_filename,
        "uploaded_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "map": data.get("map") if isinstance(data, dict) else None,
        "rounds": _rounds_count(data),
        "schema_version": data.get("version") if isinstance(data, dict) else None,
        "analytics_version": a.get("version") if a else None,
        "source_sha1": source_sha1 or (data.get("source_sha1") if isinstance(data, dict) else None),
    }


# ---- validation -------------------------------------------------------------
def validate_parsed(data, replay_valid, analytics_valid):
    """Gate a parsed sample before it's allowed to replace the live one. Reuses app.py's
    replay_valid/analytics_valid so "valid" means EXACTLY what it means everywhere else.

    Requires: schema-valid replay, REAL computed analytics (not the dropped/fallback state),
    a map name, rounds>0, and players present. Returns (ok: bool, reason: str|None). Never
    fabricates anything -- if analytics didn't compute, ok is False and the caller keeps the
    old sample."""
    if not isinstance(data, dict):
        return False, "Parse produced no data."
    if not replay_valid(data):
        return False, "Replay failed schema validation (wrong/empty schema version or no frames)."
    if not analytics_valid(data):
        return False, ("Analytics were not computed for this demo "
                       "(would show the empty-analytics fallback). Sample rejected; old sample kept.")
    if not (data.get("map") and isinstance(data.get("map"), str)):
        return False, "Demo has no map name."
    rc = _rounds_count(data)
    if not rc or rc <= 0:
        return False, "Demo has no rounds."
    if _players_count(data) <= 0:
        return False, "Demo has no players."
    return True, None


# ---- status object (the GET/POST response payload) --------------------------
def status(data_dir, replay_valid, analytics_valid):
    """Build the status dict the API returns for the CURRENTLY effective admin sample (or the
    bundled fallback when no valid admin sample exists). Shape is the endpoint contract the
    frontend codes against -- keep field names stable."""
    data = _load_json(current_json_path(data_dir))
    meta = _load_json(current_meta_path(data_dir)) or {}
    raw_retained = os.path.exists(current_raw_path(data_dir))

    rv = bool(data) and replay_valid(data)
    av = bool(data) and analytics_valid(data)
    a = _analytics(data)
    has_admin = bool(data) and rv and av
    source = "admin" if has_admin else "bundled"

    sample_bytes = None
    cj = current_json_path(data_dir)
    if os.path.exists(cj):
        try:
            sample_bytes = os.path.getsize(cj)
        except OSError:
            sample_bytes = None

    warning = None
    if data is not None and not has_admin:
        # an admin sample exists on disk but is no longer valid -> we are SERVING the bundled one
        warning = ("The uploaded sample is no longer valid (schema/analytics version changed). "
                   "The bundled sample is being served. Rebuild from the retained demo or upload a new one."
                   if raw_retained else
                   "The uploaded sample is no longer valid and no raw demo is retained. "
                   "The bundled sample is being served. Upload a new sample demo.")

    return {
        "source": source,
        "map": (data.get("map") if isinstance(data, dict) else None) if has_admin else meta.get("map"),
        "rounds": _rounds_count(data) if has_admin else meta.get("rounds"),
        "score": score_of(data) if has_admin else None,
        "replay_valid": rv,
        "analytics_valid": av,
        "has_analytics": bool(a),
        "raw_retained": raw_retained,
        "uploaded_at": meta.get("uploaded_at"),
        "original_filename": meta.get("original_filename"),
        "schema_version": (data.get("version") if isinstance(data, dict) else None) if data else meta.get("schema_version"),
        "analytics_version": (a.get("version") if a else None) if data else meta.get("analytics_version"),
        "sample_bytes": sample_bytes,
        "weak_features": weak_features(data) if has_admin else [],
        "warning": warning,
    }


def has_valid_admin_sample(data_dir, replay_valid, analytics_valid):
    """True iff a current admin sample exists AND passes replay+analytics validation. /api/sample
    uses this to decide whether to prefer the admin sample over the bundled one."""
    data = _load_json(current_json_path(data_dir))
    return bool(data) and replay_valid(data) and analytics_valid(data)


# ---- mutations: install / rebuild / revert ----------------------------------
def install_parsed(data_dir, data, replay_valid, analytics_valid,
                   source="admin", original_filename=None, raw_src=None, source_sha1=None):
    """Validate `data` and, only if it passes, atomically make it the live admin sample.

    Order (so a failure never corrupts the served sample):
      1) validate the parsed dict (REAL analytics required)
      2) write parsed JSON to a temp file in the sample dir
      3) back up the prior current.json (best-effort)
      4) os.replace() temp -> current.json (atomic)
      5) write metadata sidecar
      6) retain the raw .dem (copy raw_src -> raw/current.dem) so a later rebuild works
    Returns (ok: bool, reason: str|None). On ok=False NOTHING on disk is changed.
    """
    ok, reason = validate_parsed(data, replay_valid, analytics_valid)
    if not ok:
        return False, reason

    ensure_dirs(data_dir)
    target = current_json_path(data_dir)

    # 2) stage to a temp file in the same dir (same-filesystem -> os.replace is atomic)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_newsample_", suffix=".json", dir=sample_dir(data_dir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(data, out)
    except Exception as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False, "Could not write new sample: %s" % e

    # 3) back up the prior sample (advisory -- never block the swap on it)
    if os.path.exists(target):
        try:
            shutil.copyfile(target, _backup_path(data_dir))
        except OSError:
            pass

    # 4) atomic swap
    try:
        os.replace(tmp, target)
    except OSError as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False, "Could not install new sample: %s" % e

    # 5) metadata sidecar (advisory)
    if source_sha1 is None and raw_src and os.path.exists(raw_src):
        try:
            source_sha1 = _sha1_file(raw_src)
        except OSError:
            source_sha1 = None
    try:
        _atomic_write_json(current_meta_path(data_dir),
                           build_meta(data, source=source, original_filename=original_filename,
                                      source_sha1=source_sha1))
    except Exception:
        pass

    # 6) retain the raw .dem for future rebuilds (RETAIN by default). Copy, don't move -- the
    #    caller's temp is cleaned in its own finally and may still be needed there.
    if raw_src and os.path.exists(raw_src):
        ensure_dirs(data_dir)
        raw_dst = current_raw_path(data_dir)
        try:
            fd2, rtmp = tempfile.mkstemp(prefix=".tmp_rawdem_", dir=os.path.dirname(raw_dst))
            os.close(fd2)
            shutil.copyfile(raw_src, rtmp)
            os.replace(rtmp, raw_dst)
        except OSError:
            # retention is best-effort; the sample is already installed. rebuild just won't work
            # until a future upload retains a raw demo.
            try:
                if os.path.exists(rtmp):
                    os.remove(rtmp)
            except (OSError, NameError):
                pass

    return True, None


def rebuild(data_dir, parse_callable, replay_valid, analytics_valid):
    """Re-parse the RETAINED raw .dem at the latest parser/schema/analytics version and reinstall.
    `parse_callable(dem_path) -> parsed dict` is injected by the caller (app.py's trusted parse path).
    Returns (ok, reason). 400-reason when no raw is retained."""
    raw = current_raw_path(data_dir)
    if not os.path.exists(raw):
        return False, "Raw demo not stored. Upload a new sample demo to regenerate analytics."
    # preserve the original filename across a rebuild if we have it
    meta = _load_json(current_meta_path(data_dir)) or {}
    try:
        data = parse_callable(raw)
    except Exception as e:
        return False, "Re-parse failed: %s" % e
    return install_parsed(data_dir, data, replay_valid, analytics_valid,
                          source="admin", original_filename=meta.get("original_filename"),
                          raw_src=raw)


def revert(data_dir):
    """Drop/disable the admin sample so /api/sample falls back to the bundled sample. Removes the
    parsed JSON, its metadata, the retained raw .dem, and the backup. Idempotent. Returns (ok, reason)."""
    for p in (current_json_path(data_dir), current_meta_path(data_dir),
              current_raw_path(data_dir), _backup_path(data_dir)):
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError as e:
            return False, "Could not remove %s: %s" % (os.path.basename(p), e)
    return True, None
