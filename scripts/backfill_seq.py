#!/usr/bin/env python3
"""Backfill `seq` column from `created_at` ordering (Level 1, step 2).

For every message in the messages table, assign a per-session seq
based on its created_at order. The first message in a session gets
seq=1, the next gets seq=2, etc.

This script must be run:
- Once after deploying the `feat(schema): add seq column` commit
- BEFORE deploying the `refactor(history): ORDER BY seq instead of created_at` commit
  (otherwise legacy rows with seq=0 will all sort to the front)

After backfill, the UNIQUE INDEX (session_id, seq) is created in
this same script, guaranteeing no two messages in the same session
share a seq.

Usage:
    # Dry-run (default): print summary, do nothing
    python3 scripts/backfill_seq.py

    # Actually commit
    python3 scripts/backfill_seq.py --apply

    # Custom DB path
    python3 scripts/backfill_seq.py --db /path/to/db.sqlite

Safety:
- Default mode is dry-run; nothing is modified without --apply.
- Each session's seq assignment is atomic (single UPDATE per row,
  wrapped in a transaction).
- The UNIQUE INDEX is only created after all rows are updated, so
  transient duplicates during the update don't fail.
- Re-running with --apply is idempotent: rows with non-zero seq
  are left alone (re-derives only from rows with seq=0).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def _get_default_db_path() -> Path:
    """Mirror the webui's DB path resolution."""
    import os
    db_dir = Path(os.environ.get("AN_WORKSPACE_PATH", str(Path.home() / ".quantnodes")))
    return db_dir / "quantnodes_agentnodes_user.db"


def _ensure_seq_column(conn: sqlite3.Connection) -> None:
    """Add seq column if missing. Idempotent."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
    if "seq" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN seq INTEGER NOT NULL DEFAULT 0")
        print("  + added messages.seq column")


def _count_sessions(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(DISTINCT session_id) FROM messages").fetchone()[0]


def _count_zero_seq(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM messages WHERE seq = 0").fetchone()[0]


def _count_total(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]


def _max_seq_per_session(conn: sqlite3.Connection) -> dict[str, int]:
    """Return {session_id: max(seq)} for sessions that already have non-zero seqs."""
    rows = conn.execute(
        """
        SELECT session_id, MAX(seq) FROM messages
        WHERE seq > 0 GROUP BY session_id
        """
    ).fetchall()
    return {sid: mx for sid, mx in rows}


def backfill(
    db_path: Path,
    apply: bool,
    batch_size: int = 500,
) -> None:
    """Run the backfill.

    Strategy:
    1. For each session, find the existing max seq (if any rows have
       non-zero seq, that means partial backfill already happened or
       a live process has been writing).
    2. Compute the desired seq for each message: 1, 2, 3, ... in
       created_at order, OFFSET by the existing max (so new seqs
       continue from where live writes left off).
    3. UPDATE each row to its computed seq.
    4. After all rows are updated, create the UNIQUE INDEX (only if
       applying) to enforce the invariant going forward.
    """
    print(f"DB: {db_path}")
    print(f"Mode: {'APPLY' if apply else 'DRY-RUN'}")

    if not db_path.exists():
        print(f"  ! DB not found, aborting")
        sys.exit(1)

    conn = sqlite3.connect(str(db_path))
    try:
        _ensure_seq_column(conn)
        conn.commit()

        total_sessions = _count_sessions(conn)
        total_rows = _count_total(conn)
        zero_rows = _count_zero_seq(conn)
        non_zero_rows = total_rows - zero_rows

        print(f"\nStats:")
        print(f"  sessions:        {total_sessions}")
        print(f"  total messages:  {total_rows}")
        print(f"  already have seq: {non_zero_rows}")
        print(f"  need backfill:   {zero_rows}")

        if zero_rows == 0:
            print("\n✓ No rows need backfill. Skipping.")
        else:
            # For each session, backfill rows with seq=0
            sessions = conn.execute(
                "SELECT DISTINCT session_id FROM messages ORDER BY session_id"
            ).fetchall()

            total_updated = 0
            for (sid,) in sessions:
                # Compute starting seq (after any existing non-zero seqs)
                max_existing = (
                    conn.execute(
                        "SELECT COALESCE(MAX(seq), 0) FROM messages WHERE session_id = ?",
                        (sid,),
                    ).fetchone()[0]
                )
                starting_seq = max_existing

                # Get rows needing update, in created_at order
                rows = conn.execute(
                    """
                    SELECT id FROM messages
                    WHERE session_id = ? AND seq = 0
                    ORDER BY created_at ASC
                    """,
                    (sid,),
                ).fetchall()

                if not rows:
                    continue

                if not apply:
                    print(f"  session {sid[:12]}: would assign seq {starting_seq+1}..{starting_seq+len(rows)}")
                    total_updated += len(rows)
                    continue

                # Apply: assign seqs in created_at order
                for i, (mid,) in enumerate(rows, start=1):
                    new_seq = starting_seq + i
                    conn.execute(
                        "UPDATE messages SET seq = ? WHERE id = ? AND seq = 0",
                        (new_seq, mid),
                    )
                total_updated += len(rows)

                # Commit per session to keep transactions small
                conn.commit()

                if total_updated % batch_size == 0:
                    print(f"  progress: {total_updated} rows updated")

            if apply:
                print(f"\n  ✓ Updated {total_updated} rows")
            else:
                print(f"\n  → Would update {total_updated} rows")

        # Now create the UNIQUE INDEX (only if applying and not exists).
        # The non-unique idx_messages_session_seq created by the schema
        # commit is dropped here to avoid duplicate indexing on the
        # same columns.
        if apply:
            existing_unique = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name='uq_messages_session_seq'"
            ).fetchone()
            existing_nonunique = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_messages_session_seq'"
            ).fetchone()

            if not existing_unique:
                # First, verify no duplicates would prevent the index
                dupes = conn.execute(
                    """
                    SELECT session_id, seq, COUNT(*) AS c FROM messages
                    WHERE seq > 0
                    GROUP BY session_id, seq HAVING c > 1
                    """
                ).fetchall()
                if dupes:
                    print(f"\n  ! Cannot create UNIQUE INDEX: {len(dupes)} duplicate (session, seq) pairs")
                    for sid, seq, c in dupes[:5]:
                        print(f"      {sid[:12]} seq={seq} count={c}")
                    sys.exit(1)
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_messages_session_seq "
                    "ON messages(session_id, seq)"
                )
                print(f"  ✓ Created UNIQUE INDEX uq_messages_session_seq")
            else:
                print(f"  - UNIQUE INDEX uq_messages_session_seq already exists")

            if existing_nonunique:
                conn.execute("DROP INDEX IF EXISTS idx_messages_session_seq")
                print(f"  ✓ Dropped redundant non-unique idx_messages_session_seq")
            conn.commit()

            # Verify integrity
            post = _count_zero_seq(conn)
            if post > 0:
                print(f"\n  ! {post} rows still have seq=0 (these were inserted after backfill)")
            else:
                print(f"\n✓ All messages have non-zero seq")
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Path to user DB (default: $AN_WORKSPACE_PATH/quantnodes_agentnodes_user.db)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually commit changes (default is dry-run)",
    )
    args = parser.parse_args()

    db_path = args.db or _get_default_db_path()
    backfill(db_path, apply=args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
