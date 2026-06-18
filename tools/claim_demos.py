"""Assign ownership of demos to a user.

Existing demos uploaded before login existed have no owner (owner_user_id = NULL). On a locked-down
site (AUTH_REQUIRED=1) ownerless demos are hidden from everyone so they never leak between users --
so after you turn auth on, claim your old demos to your own account with this script.

The target user must have logged in at least once (that creates their user row). Find your SteamID64
on your Steam profile URL, or run with --list to see known users.

Usage (from the project root, with the venv python):
  python tools/claim_demos.py --list
  python tools/claim_demos.py --steamid 7656119XXXXXXXXXX          # claim only OWNERLESS demos
  python tools/claim_demos.py --user 1 --all                       # reassign EVERY demo to user 1
  python tools/claim_demos.py --steamid 7656119XXXXXXXXXX --dry-run
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db   # noqa: E402


def _resolve_user(args):
    if args.user is not None:
        u = db.get_user(args.user)
        return u
    if args.steamid:
        return db.get_user_by_steamid(args.steamid)
    return None


def main():
    ap = argparse.ArgumentParser(description="Assign demo ownership to a user.")
    ap.add_argument("--user", type=int, help="target user id")
    ap.add_argument("--steamid", help="target SteamID64 (the 17-digit id)")
    ap.add_argument("--all", action="store_true",
                    help="reassign ALL demos (default: only ownerless/unclaimed ones)")
    ap.add_argument("--list", action="store_true", help="list known users and exit")
    ap.add_argument("--dry-run", action="store_true", help="show what would change, don't write")
    args = ap.parse_args()

    db.migrate()
    con = db.connect()
    try:
        if args.list:
            rows = con.execute("SELECT id, steam_id_64, display_name FROM users ORDER BY id").fetchall()
            if not rows:
                print("No users yet -- have someone sign in through Steam first.")
            for r in rows:
                print(f"  id={r['id']}  steamid={r['steam_id_64']}  name={r['display_name']}")
            owned = con.execute("SELECT COUNT(*) n FROM demos WHERE owner_user_id IS NOT NULL").fetchone()["n"]
            free = con.execute("SELECT COUNT(*) n FROM demos WHERE owner_user_id IS NULL").fetchone()["n"]
            print(f"\nDemos: {owned} owned, {free} ownerless/unclaimed.")
            return

        user = _resolve_user(args)
        if not user:
            print("Target user not found. Use --list to see users (the user must sign in once first).")
            sys.exit(1)

        where = "" if args.all else " WHERE owner_user_id IS NULL"
        n = con.execute("SELECT COUNT(*) c FROM demos" + where).fetchone()["c"]
        scope = "ALL" if args.all else "ownerless"
        print(f"Would assign {n} {scope} demo(s) to user id={user['id']} "
              f"({user.get('name')} / {user['steam_id_64']}).")
        if args.dry_run:
            print("(dry run -- no changes written)")
            return
        if n:
            con.execute("UPDATE demos SET owner_user_id=?" + where, (user["id"],))
            # membership model: add the user's library membership for every demo they now own, so the
            # claimed demos are actually visible (visibility keys off user_demos, not owner_user_id)
            con.execute(
                "INSERT OR IGNORE INTO user_demos(user_id, sha1, created_at) "
                "SELECT ?, sha1, created_at FROM demos WHERE owner_user_id=?", (user["id"], user["id"]))
            con.commit()
        print(f"Done. {n} demo(s) now owned by user id={user['id']}.")
    finally:
        con.close()


if __name__ == "__main__":
    main()
