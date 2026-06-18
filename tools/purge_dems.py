"""Delete stored raw .dem files to reclaim disk. The parsed cache (cache/<sha>.json) is all the app
needs to replay + analyze; the raw .dem is only used to RE-parse on a future parser/schema upgrade.

Safe: only touches *.dem in the uploads dir (never the cache or library). Dry-run by default.

Usage (from the project root, with the venv python):
  python tools/purge_dems.py            # show what WOULD be deleted (dry run)
  python tools/purge_dems.py --apply    # actually delete them
"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOADS = os.environ.get("UPLOAD_DIR") or os.path.join(HERE, "uploads")


def main():
    apply = "--apply" in sys.argv
    if not os.path.isdir(UPLOADS):
        print(f"no uploads dir at {UPLOADS}")
        return
    dems = [f for f in os.listdir(UPLOADS) if f.lower().endswith(".dem")]
    if not dems:
        print(f"no .dem files in {UPLOADS} -- nothing to reclaim.")
        return
    total = 0
    for f in dems:
        p = os.path.join(UPLOADS, f)
        try:
            sz = os.path.getsize(p)
        except OSError:
            sz = 0
        total += sz
        print(f"  {'delete' if apply else 'would delete'}  {f}  ({sz // (1 << 20)} MB)")
        if apply:
            try:
                os.remove(p)
            except OSError as e:
                print(f"    ! could not delete: {e}")
    gb = total / (1 << 30)
    human = f"{gb:.2f} GB" if gb >= 1 else f"{total // (1 << 20)} MB"
    print(f"\n{len(dems)} file(s), {human} {'reclaimed' if apply else 'reclaimable'}.")
    if not apply:
        print("re-run with --apply to delete. (The parsed demos stay fully usable.)")


if __name__ == "__main__":
    main()
