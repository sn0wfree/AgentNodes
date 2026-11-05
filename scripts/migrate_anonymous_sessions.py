#!/usr/bin/env python3
"""Migrate sessions owned by ``user_id='anonymous'`` (or any non-admin UUID) to the admin user.

Why this exists
---------------
The AuthMiddleware falls back to ``"anonymous"`` when no bearer token is
present, so browser sessions started before login accumulate rows tagged
with that sentinel string. Once the user logs in, those rows are
invisible to the authenticated UI even though the data is still there.

This script reassigns every non-admin session to the admin user's UUID
so the history reappears under the logged-in account.

Usage
-----
::

    # Dry-run only (default): print what would change, do nothing
    python3 scripts/migrate_anonymous_sessions.py

    # Actually commit
    python3 scripts/migrate_anonymous_sessions.py --apply

    # Custom DB path
    python3 scripts/migrate_anonymous_sessions.py --db /path/to/db.sqlite

    # Skip FTS rebuild
    python3 scripts/migrate_anonymous_sessions.py --no-rebuild-fts

Safety
------
* Dry-run by default — no DB writes unless ``--apply`` is passed.
* Only updates rows where ``user_id != admin_id``.
* FTS5 rebuild is non-destructive: it inserts back from ``messages``,
  so FTS shadow tables are repopulated, never truncated.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB_PATH = Path.home() / ".quantnodes" / "quantnodes_agentnodes_user.db"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Reassign anonymous sessions to the admin user.",
    )
    p.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"SQLite DB path (default: {DEFAULT_DB_PATH})",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Actually commit changes (default is dry-run).",
    )
    p.add_argument(
        "--target-user",
        default=None,
        help="Override the destination user UUID (default: SELECT id FROM users WHERE username='admin').",
    )
    p.add_argument(
        "--no-rebuild-fts",
        action="store_true",
        help="Skip the messages_fts rebuild step after migration.",
    )
    return p.parse_args()


def fetch_admin_id(conn: sqlite3.Connection) -> str:
    """Return the admin user's UUID, or raise if there isn't exactly one."""
    rows = conn.execute(
        "SELECT id, username FROM users WHERE username='admin'"
    ).fetchall()
    if not rows:
        raise SystemExit(
            "ERROR: no user with username='admin' found. "
            "Create one first or pass --target-user <uuid>.",
        )
    if len(rows) > 1:
        raise SystemExit(
            f"ERROR: {len(rows)} admin users exist ({rows!r}). "
            "Refusing to guess which one to use; pass --target-user <uuid>.",
        )
    return rows[0][0]


def list_user_ids(conn: sqlite3.Connection) -> list[str]:
    """Return all distinct user_ids present in the sessions table."""
    rows = conn.execute(
        "SELECT DISTINCT user_id FROM sessions ORDER BY user_id"
    ).fetchall()
    return [r[0] for r in rows]


def count_sessions(conn: sqlite3.Connection, user_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE user_id = ?", (user_id,)
    ).fetchone()
    return int(row[0]) if row else 0


def count_messages(conn: sqlite3.Connection, user_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE session_id IN "
        "(SELECT id FROM sessions WHERE user_id = ?)",
        (user_id,),
    ).fetchone()
    return int(row[0]) if row else 0


def reassign_user(
    conn: sqlite3.Connection, src: str, dst: str, *, apply: bool
) -> int:
    """Update sessions.user_id from src to dst. Returns row count."""
    cur = conn.execute(
        "UPDATE sessions SET user_id = ? WHERE user_id = ?", (dst, src)
    )
    affected = cur.rowcount
    if not apply:
        # SQLite only buffers UPDATE rowcount when actually executed.
        # For dry-run we have to compute via SELECT.
        cur = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE user_id = ?", (src,)
        )
        affected = int(cur.fetchone()[0])
    return affected


def rebuild_fts(conn: sqlite3.Connection, *, apply: bool) -> bool:
    """Re-populate messages_fts from messages. Returns whether anything happened."""
    # Only meaningful when FTS5 actually exists
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='messages_fts'"
    ).fetchone()
    if not row:
        print("  messages_fts does not exist, skipping rebuild")
        return False

    msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    print(f"  messages table has {msg_count} rows")
    if msg_count == 0:
        return False

    if not apply:
        print("  [DRY-RUN] would INSERT INTO messages_fts(rowid, content, role) "
              "SELECT rowid, content, role FROM messages")
        return False

    # Repopulate via the 'rebuild' command (clears + rebuilds FTS index in place)
    try:
        conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
        print(f"  ✓ messages_fts rebuilt via 'rebuild' command")
        return True
    except sqlite3.OperationalError as exc:
        print(f"  rebuild command failed ({exc}); falling back to full re-insert")
        conn.execute("DELETE FROM messages_fts")
        conn.execute(
            "INSERT INTO messages_fts(rowid, content, role) "
            "SELECT rowid, content, role FROM messages"
        )
        print(f"  ✓ messages_fts re-inserted {msg_count} rows")
        return True


def main() -> int:
    args = parse_args()
    if not args.db.exists():
        print(f"ERROR: DB not found at {args.db}", file=sys.stderr)
        return 1

    print(f"DB:       {args.db}")
    print(f"Mode:     {'APPLY (committing)' if args.apply else 'DRY-RUN (no writes)'}")
    print(f"FTS:      {'skip' if args.no_rebuild_fts else 'rebuild after migrate'}")
    print()

    conn = sqlite3.connect(str(args.db))
    conn.row_factory = sqlite3.Row
    try:
        admin_id = args.target_user or fetch_admin_id(conn)
        print(f"Target user_id: {admin_id}\n")

        all_user_ids = list_user_ids(conn)
        if not all_user_ids:
            print("No sessions in DB; nothing to do.")
            return 0

        targets = [uid for uid in all_user_ids if uid != admin_id]
        if not targets:
            print(f"All {sum(count_sessions(conn, uid) for uid in all_user_ids)} "
                  f"sessions already owned by admin; nothing to do.")
            return 0

        total_sessions = 0
        total_messages = 0
        per_user: list[tuple[str, int, int]] = []
        for uid in targets:
            sess_n = count_sessions(conn, uid)
            msg_n = count_messages(conn, uid)
            per_user.append((uid, sess_n, msg_n))
            total_sessions += sess_n
            total_messages += msg_n

        print("Sessions to migrate:")
        for uid, s, m in per_user:
            label = "anonymous" if uid == "anonymous" else f"unknown:{uid[:8]}"
            print(f"  • {label:<22}  sessions={s:<4}  messages={m:<4}  → admin")
        print(f"\nTotal: {total_sessions} sessions, {total_messages} messages")
        print()

        if not args.apply:
            print("[DRY-RUN] No changes written. Re-run with --apply to commit.")
            if not args.no_rebuild_fts:
                rebuild_fts(conn, apply=False)
            return 0

        # Actual migration
        conn.execute("BEGIN")
        try:
            for uid, _, _ in per_user:
                n = reassign_user(conn, uid, admin_id, apply=True)
                print(f"  ✓ reassigned {n} sessions from {uid!r} → admin")
            if not args.no_rebuild_fts:
                print("\nRebuilding FTS5:")
                rebuild_fts(conn, apply=True)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        print("\n✓ Migration complete.")

        # Verify
        remaining = conn.execute(
            "SELECT user_id, COUNT(*) FROM sessions "
            "WHERE user_id != ? GROUP BY user_id",
            (admin_id,),
        ).fetchall()
        if remaining:
            print(f"⚠ {len(remaining)} non-admin user_ids still present: {remaining}")
        else:
            print("✓ All sessions now owned by admin.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())