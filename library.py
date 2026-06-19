"""library.py -- saved-demo library for the CS2 demo player (multi-upload backend).

A small, stdlib-only layer over the existing cache dir. For each demo the user
uploads (a raw .dem, or any number of .dem entries inside a .zip) the parsed JSON
is saved as cache/lib_<id>.json and a single cache/library.json index keeps a
lightweight, newest-first summary row per demo.

  id     -- the demo's source_sha1 if the parser gave us one, else a uuid4 hex.
            Re-uploading the same demo (same id) UPDATES its row + JSON in place
            instead of creating a duplicate.
  score  -- final match score, read off the last round (score_ct / score_t),
            stored as {"ct": int, "t": int}.

Design notes:
  * app.py owns parsing/caching helpers (atomic_write_json, clean_nan, the
    DemoParser-sharing parse+analytics flow). This module only handles zip
    extraction, the library index, scoring, and reading a saved demo back.
  * The index is advisory metadata; the saved lib_<id>.json is the source of
    truth and is what /api/demo/<id> serves.
"""
import datetime
import json
import os
import re
import uuid
import zipfile

# ---- limits / guards --------------------------------------------------------
# Total bytes we'll extract out of a single .zip, and the per-entry cap. CS2
# demos are big, so these are generous; they exist to stop a zip-bomb / a
# pathological archive from filling the disk.
MAX_ZIP_TOTAL_BYTES = int(os.environ.get("MAX_ZIP_TOTAL_MB", "2048")) * 1024 * 1024
MAX_ZIP_ENTRY_BYTES = int(os.environ.get("MAX_ZIP_ENTRY_MB", "2048")) * 1024 * 1024

LIBRARY_INDEX_NAME = "library.json"


def index_path(cache_dir):
    return os.path.join(cache_dir, LIBRARY_INDEX_NAME)


def lib_cache_path(cache_dir, demo_id):
    return os.path.join(cache_dir, "lib_{}.json".format(demo_id))


def canonical_cache_path(cache_dir, demo_id):
    """The ONE canonical parsed-JSON artifact for a demo: cache/<sha16>.json (content-hash cache).
    The library's lib_<id>.json is a tiny pointer at this, not a 45-90MB duplicate."""
    return os.path.join(cache_dir, "{}.json".format((demo_id or "")[:16]))


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


# ---- scoring ----------------------------------------------------------------
def final_score(data):
    """Final match score from the parsed rounds: the last round's score_ct/score_t.

    Returns {"ct": int, "t": int}; falls back to 0-0 when there are no rounds
    (or the last round lacks the fields), so the shape is always present.
    """
    rounds = (data or {}).get("rounds") or []
    if rounds:
        last = rounds[-1] or {}
        try:
            return {"ct": int(last.get("score_ct") or 0),
                    "t": int(last.get("score_t") or 0)}
        except (TypeError, ValueError):
            pass
    return {"ct": 0, "t": 0}


def demo_id_for(data):
    """Stable id for a parsed demo: its source_sha1 if present, else a uuid4 hex."""
    sid = (data or {}).get("source_sha1")
    if isinstance(sid, str) and sid.strip():
        return sid.strip()
    return uuid.uuid4().hex


# ---- zip extraction ---------------------------------------------------------
def _safe_member_name(name):
    """Last path component of a zip entry name, or '' if it's a dir / unsafe.

    Defends against directory traversal (``../``, absolute paths, drive
    letters): we never honour the archive's directory structure -- only the
    bare filename is ever used, and only for *.dem entries.
    """
    if not name or name.endswith("/") or name.endswith("\\"):
        return ""                                   # directory entry
    base = os.path.basename(name.replace("\\", "/"))
    if not base or base in (".", "..") or not base.lower().endswith(".dem"):
        return ""
    return base


def iter_zip_dems(zip_path):
    """Yield (member_name, bytes) for each safe .dem entry in a .zip.

    Skips non-.dem and directory entries; enforces a per-entry and a running
    total extracted-size cap. Raises zipfile.BadZipFile for a corrupt archive
    (the caller turns that into a per-file error, not a 500).
    """
    total = 0
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            base = _safe_member_name(info.filename)
            if not base:
                continue
            # Trust the declared size first (cheap zip-bomb guard), then verify
            # the real extracted size as we read.
            if info.file_size > MAX_ZIP_ENTRY_BYTES:
                continue
            if total + info.file_size > MAX_ZIP_TOTAL_BYTES:
                break
            with zf.open(info) as fh:
                payload = fh.read(MAX_ZIP_ENTRY_BYTES + 1)
            if len(payload) > MAX_ZIP_ENTRY_BYTES:
                continue                            # lied about its size
            total += len(payload)
            if total > MAX_ZIP_TOTAL_BYTES:
                break
            yield base, payload


def is_zip_name(filename):
    return bool(filename) and filename.lower().endswith(".zip")


def is_dem_name(filename):
    return bool(filename) and filename.lower().endswith(".dem")


def is_gz_name(filename):
    """A client-gzipped demo (CompressionStream upload), e.g. 'match.dem.gz'. The worker gunzips it
    back to byte-identical .dem before parsing, so the content-hash cache key is unchanged."""
    return bool(filename) and filename.lower().endswith(".gz")


def strip_gz(filename):
    """Display/storage name for a gzipped upload: 'match.dem.gz' -> 'match.dem'."""
    return filename[:-3] if is_gz_name(filename) else filename


# ---- the library index ------------------------------------------------------
def load_index(cache_dir):
    """Load library.json as a list of rows; [] if missing/corrupt."""
    try:
        with open(index_path(cache_dir), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    if isinstance(data, dict):                      # tolerate {"demos": [...]}
        data = data.get("demos")
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def save_index(cache_dir, rows, write_json):
    """Persist the index via app's atomic_write_json (passed in as write_json)."""
    write_json(index_path(cache_dir), rows)


def make_row(demo_id, name, data):
    """One library index row from a parsed demo."""
    return {
        "id": demo_id,
        "name": name,
        "map": (data or {}).get("map"),
        "score": final_score(data),
        "date": datetime.datetime.now().isoformat(timespec="seconds"),
        "rounds": len((data or {}).get("rounds") or []),
    }


def upsert(cache_dir, demo_id, name, data, write_json):
    """Add/replace the demo's row in library.json and point lib_<id>.json at the ONE canonical
    parsed JSON (cache/<sha16>.json) instead of duplicating the 45-90MB blob.

    The canonical artifact is written if missing; lib_<id>.json becomes a tiny pointer carrying
    the schema version (so the cheap stale-check in list_demos still works). De-dupes by id.
    Returns the row.
    """
    key16 = (demo_id or "")[:16]
    canonical = canonical_cache_path(cache_dir, demo_id) if key16 else None
    if canonical and os.path.exists(canonical):
        # the canonical parsed JSON is already on disk (the parse step wrote cache/<sha16>.json)
        # -> store a tiny pointer instead of duplicating the 45-90MB blob
        write_json(lib_cache_path(cache_dir, demo_id),
                   {"_pointer": True, "canonical_key": key16,
                    "version": (data or {}).get("version"), "source_sha1": demo_id})
    else:
        write_json(lib_cache_path(cache_dir, demo_id), data)   # no canonical yet -> keep a full copy
    row = make_row(demo_id, name, data)
    rows = load_index(cache_dir)
    for i, existing in enumerate(rows):
        if existing.get("id") == demo_id:
            rows[i] = row
            break
    else:
        rows.append(row)
    save_index(cache_dir, rows, write_json)
    return row


def list_demos(cache_dir, schema_version):
    """Library rows, newest-first, each tagged stale if its saved JSON's
    version != schema_version (mirrors /api/sample's staleness handling).

    Rows whose lib_<id>.json is missing are dropped (and pruned from the index)
    so the listing never points at demos that aren't there.
    """
    rows = load_index(cache_dir)
    out = []
    kept = []
    changed = False
    for row in rows:
        demo_id = row.get("id")
        if not demo_id:
            changed = True
            continue
        cache_file = lib_cache_path(cache_dir, demo_id)
        version = _saved_version(cache_file)
        if version is _MISSING:
            changed = True                          # JSON gone -> drop the row
            continue
        kept.append(row)
        out.append({
            "id": demo_id,
            "name": row.get("name"),
            "map": row.get("map"),
            "score": row.get("score") or {"ct": 0, "t": 0},
            "date": row.get("date"),
            "rounds": row.get("rounds", 0),
            "stale": version != schema_version,
        })
    if changed:
        save_index(cache_dir, kept, _direct_write_json)
    out.sort(key=lambda d: d.get("date") or "", reverse=True)
    return out


_MISSING = object()


def _saved_version(cache_file):
    """The saved JSON's 'version', or _MISSING if the file is absent.

    Only the first chunk is read -- the demo blob can be hundreds of MB and we
    just need the small top-level 'version' int for the stale check.
    """
    if not os.path.exists(cache_file):
        return _MISSING
    try:
        with open(cache_file, encoding="utf-8") as fh:
            head = fh.read(4096)
        m = re.search(r'"version"\s*:\s*(\d+)', head)
        if m:
            return int(m.group(1))
        # Fallback: tiny/oddly-ordered file -> parse it fully.
        with open(cache_file, encoding="utf-8") as fh:
            return (json.load(fh) or {}).get("version")
    except (OSError, ValueError):
        return None


def _direct_write_json(path, data):
    """Minimal atomic-ish writer for the prune path (when app's writer wasn't
    passed in). Writes to a temp sibling then replaces."""
    import tempfile
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_lib_", suffix=".json", dir=d)
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


def load_demo(cache_dir, demo_id):
    """The saved parsed JSON for an id (same shape as /api/sample), or None.

    Resolves the lib_<id>.json pointer to the canonical cache/<sha16>.json; falls back to a
    legacy full lib copy, or the canonical directly if no lib file exists. Ids are guarded so
    they can only resolve inside cache_dir.
    """
    if not demo_id or not re.fullmatch(r"[A-Za-z0-9_-]+", demo_id):
        return None
    data = _read_json(lib_cache_path(cache_dir, demo_id))
    if isinstance(data, dict) and (data.get("_pointer") or data.get("canonical_key")):
        ck = data.get("canonical_key") or demo_id[:16]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", ck or ""):
            return None
        return _read_json(os.path.join(cache_dir, ck + ".json"))
    if data is not None:
        return data                                     # legacy full copy
    return _read_json(canonical_cache_path(cache_dir, demo_id))   # no lib file -> canonical directly


def delete_demo(cache_dir, uploads_dir, demo_id):
    """Fully remove a demo: its library JSON + index row, the upload cache + sidecar, the
    raw .dem, and the nade sidecars -- everything on disk for it. Frees the big files (the
    .dem can be hundreds of MB). Re-uploading the same .dem later re-parses it cleanly.

    The id is the library id (the demo's source_sha1); the upload cache/.dem use its first
    16 chars as the key. Missing files are skipped. Returns {ok, removed:[names], bytes}.
    """
    if not demo_id or not re.fullmatch(r"[A-Za-z0-9_-]+", demo_id):
        return {"ok": False, "removed": [], "bytes": 0, "error": "bad id"}
    removed, freed = [], 0

    def rm(path):
        nonlocal freed
        try:
            if path and os.path.isfile(path):
                freed += os.path.getsize(path)
                os.remove(path)
                removed.append(os.path.basename(path))
        except OSError:
            pass

    key = demo_id[:16]                                  # upload cache/.dem key = sha prefix
    lib_json = lib_cache_path(cache_dir, demo_id)
    rm(lib_json)                                        # library copy (source of truth)
    rm(lib_json[:-5] + ".meta.json")                    # its sidecar (if any)
    rm(os.path.join(cache_dir, "_nades", "lib_{}.json".format(demo_id)))
    rm(os.path.join(cache_dir, key + ".json"))          # original upload cache
    rm(os.path.join(cache_dir, key + ".meta.json"))
    rm(os.path.join(cache_dir, "_nades", key + ".json"))
    rm(os.path.join(uploads_dir, key + ".dem"))         # the big raw demo file

    rows = load_index(cache_dir)
    kept = [r for r in rows if r.get("id") != demo_id]
    row_removed = len(kept) != len(rows)
    if row_removed:
        save_index(cache_dir, kept, _direct_write_json)
    return {"ok": True, "removed": removed, "bytes": freed, "row_removed": row_removed}
