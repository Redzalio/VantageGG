#!/usr/bin/env python3
"""Backfill the SQLite metadata index (db.py) from the on-disk cache JSONs.

One-time (or anytime) full scan: this is the slow json.load pass that the website endpoints
USED to do on every request -- run it once and /api/matches, /api/players, /api/trends serve
from SQLite instantly thereafter. Safe to re-run; rows are upserted (de-duped by source_sha1,
canonical non-lib_ key preferred). Replay JSONs are untouched.

Usage: python tools/rebuild_index.py [--reset]
  --reset : drop existing index rows first (full rebuild)
"""
import glob
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db          # noqa: E402
import matchindex as mi   # noqa: E402


def main():
    reset = "--reset" in sys.argv[1:]
    t0 = time.time()
    db.migrate()
    con = db.connect()
    try:
        if reset:
            con.execute("DELETE FROM demos")
            con.execute("DELETE FROM demo_players")
            con.commit()
            print("reset: cleared demos + demo_players")
        indexed = skipped = 0
        for path in sorted(glob.glob(os.path.join(mi.CACHE_DIR, "*.json"))):
            data = mi._load_match(path)            # None for non-matches / unreadable / sample
            if data is None:
                skipped += 1
                continue
            key = os.path.splitext(os.path.basename(path))[0]
            ca = mi._created_at(path, key)
            sha = db.index_demo(data, key, created_at=ca, con=con)
            if sha:
                indexed += 1
            else:
                skipped += 1
        con.commit()
        nd = con.execute("SELECT COUNT(*) FROM demos").fetchone()[0]
        npl = con.execute("SELECT COUNT(*) FROM demo_players").fetchone()[0]
        print(f"indexed {indexed} match files, skipped {skipped} non-match/dupe")
        print(f"index now: {nd} unique demos, {npl} player rows  ({db.DB_PATH})")
        print(f"done in {time.time() - t0:.1f}s")
    finally:
        con.close()


if __name__ == "__main__":
    main()
