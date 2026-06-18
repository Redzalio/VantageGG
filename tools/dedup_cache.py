#!/usr/bin/env python3
"""Stage 2 storage dedup + orphan report for the CS2 demo player.

Each demo's 45-90MB parsed JSON was stored TWICE -- the content cache cache/<sha16>.json AND a
library copy cache/lib_<fullsha>.json. This converts every full lib_ copy into a tiny POINTER at
the canonical cache/<sha16>.json (promoting the lib_ copy to canonical first if the canonical is
missing), so no replay data is lost. It also REPORTS content caches not referenced by any library
row (delete only with the explicit --delete-orphans flag).

Dry-run by default (reports MB). Pass --apply to convert; add --delete-orphans to also remove orphans.
Usage: python tools/dedup_cache.py [--apply] [--delete-orphans]
"""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import library as lib   # noqa: E402

CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")


def _is_pointer(d):
    return isinstance(d, dict) and (d.get("_pointer") or d.get("canonical_key"))


def _is_match(d):
    return isinstance(d, dict) and (("frames" in d) or bool((d.get("analytics") or {}).get("players")))


def main():
    apply = "--apply" in sys.argv
    del_orphans = "--delete-orphans" in sys.argv
    reclaimed = converted = promoted = already = 0

    print("=== dedup: full lib_ copies -> pointers ===")
    for p in sorted(glob.glob(os.path.join(CACHE, "lib_*.json"))):
        d = lib._read_json(p)
        if d is None:
            continue
        if _is_pointer(d):
            already += 1
            continue
        demo_id = os.path.basename(p)[len("lib_"):-len(".json")]
        sha = d.get("source_sha1") or demo_id
        key16 = sha[:16]
        canonical = os.path.join(CACHE, key16 + ".json")
        size = os.path.getsize(p)
        has_can = os.path.exists(canonical)
        print(f"  {os.path.basename(p)[:28]:30} {size/1048576:6.1f}MB  "
              f"{'-> pointer (canonical exists)' if has_can else '-> promote then pointer'}")
        if apply:
            if not has_can:
                lib._direct_write_json(canonical, d)      # promote: never lose the only copy
                promoted += 1
            lib._direct_write_json(p, {"_pointer": True, "canonical_key": key16,
                                       "version": d.get("version"), "source_sha1": sha})
            reclaimed += max(0, size - os.path.getsize(p))
            converted += 1

    print("\n=== orphan content caches (not referenced by any library row) ===")
    referenced = {(r.get("id") or "")[:16] for r in lib.load_index(CACHE)}
    orphans = []
    for p in sorted(glob.glob(os.path.join(CACHE, "*.json"))):
        name = os.path.basename(p)
        if (name.startswith("lib_") or name.startswith(".") or name.endswith(".meta.json")
                or name in ("sample.json", "library.json")):
            continue
        key = name[:-len(".json")]
        if key in referenced:
            continue
        if _is_match(lib._read_json(p)):
            orphans.append((p, os.path.getsize(p)))
    for p, sz in orphans:
        tag = "  [DELETED]" if (apply and del_orphans) else ("  (use --delete-orphans)" if not del_orphans else "")
        print(f"  {os.path.basename(p)[:28]:30} {sz/1048576:6.1f}MB{tag}")
        if apply and del_orphans:
            os.remove(p)
            reclaimed += sz

    mode = "APPLIED" if apply else "DRY-RUN (pass --apply to act)"
    print(f"\n{mode}: {converted} converted, {promoted} promoted, {already} already-pointers, "
          f"{len(orphans)} orphans" + (f", {reclaimed/1048576:.1f}MB reclaimed" if apply else ""))


if __name__ == "__main__":
    main()
