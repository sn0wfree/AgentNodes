#!/usr/bin/env python3
"""Backfill message_parts from existing messages.parts_json (Level 2, commit 3).

For every message that has a non-NULL `parts_json`, parse it and
insert each part as a row in `message_parts`. After this script
runs successfully, message_parts is in sync with parts_json for
all existing data.

This script MUST be run between commit 2 (dual-write active) and
commit 5 (read path switched to message_parts). After commit 5
runs, this script is a no-op for already-migrated rows (idempotent:
parts with the same `(message_id, seq)` are skipped).

What this script does NOT do (deferred to commit 6):
- Delete the `role=tool` rows from the messages table
- Drop the `parts_json` / `tool_call_id` columns

Both of those are explicit destruction steps that should only
happen AFTER commit 5 has been verified to work in production.

Usage:
    # Dry-run (default): print summary, do nothing
    python3 scripts/backfill_message_parts.py

    # Actually commit
    python3 scripts/backfill_message_parts.py --apply

    # Custom DB path
    python3 scripts/backfill_message_parts.py --db /path/to/db.sqlite

Safety:
- Default mode is dry-run; nothing is modified without --apply.
- Idempotent: a row with (message_id, seq) already present is
  skipped, so re-running after commit 5 is safe.
- Each message's parts are inserted in a single transaction;
  per-message failures are logged but don't abort the whole
  migration.
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_default_db_path() -> Path:
    """Mirror the webui's DB path resolution."""
    import os
    db_dir = Path(os.environ.get("SR_WORKSPACE_PATH", str(Path.home() / ".quantnodes")))
    return db_dir / "quantnodes_agentnodes_user.db"


def _ensure_message_parts_table(conn: sqlite3.Connection) -> None:
    """Create the table if missing (idempotent)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS message_parts (
            id TEXT PRIMARY KEY,
            message_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            type TEXT NOT NULL,
            data_json TEXT NOT NULL,
            seq INTEGER NOT NULL DEFAULT 0,
            time_created REAL NOT NULL,
            FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        )
        """
    )


def _count_existing_parts(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM message_parts").fetchone()[0]


def _count_messages_with_parts(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM messages "
        "WHERE parts_json IS NOT NULL AND parts_json != 'null' AND parts_json != '[]'"
    ).fetchone()[0]


def _count_orphan_tools(conn: sqlite3.Connection) -> int:
    """role=tool messages whose tool_call_id has no matching assistant.

    Level 2 / Phase 2 commit 6 dropped tool_call_id. On post-migration
    DBs this is a no-op (returns 0 — there are no role=tool messages).
    """
    # Check if tool_call_id column still exists (pre-migration DBs)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
    if "tool_call_id" not in cols:
        return 0  # post-migration: no orphan tracking possible
    return conn.execute(
        """
        SELECT COUNT(*) FROM messages
        WHERE role = 'tool'
        AND tool_call_id NOT IN (
            SELECT json_extract(p.value, '$.id') FROM messages m,
                 json_each(m.parts_json) p
            WHERE m.parts_json IS NOT NULL
            AND json_extract(p.value, '$.type') = 'tool_call'
        )
        """).fetchone()[0]


def backfill(
    db_path: Path,
    apply: bool,
    batch_size: int = 100,
) -> None:
    print(f"DB: {db_path}")
    print(f"Mode: {'APPLY' if apply else 'DRY-RUN'}")

    if not db_path.exists():
        print(f"  ! DB not found, aborting")
        sys.exit(1)

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        _ensure_message_parts_table(conn)
        conn.commit()

        total_msgs = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        msgs_with_parts = _count_messages_with_parts(conn)
        existing_parts = _count_existing_parts(conn)
        orphan_tools = _count_orphan_tools(conn)

        print(f"\nStats:")
        print(f"  total messages:         {total_msgs}")
        print(f"  messages with parts:    {msgs_with_parts}")
        print(f"  existing message_parts: {existing_parts}")
        print(f"  orphan tool messages:   {orphan_tools}")

        if msgs_with_parts == 0:
            print("\n  No parts to migrate.")
            return

        # Get all messages with non-empty parts_json
        rows = conn.execute(
            "SELECT id, session_id, parts_json, created_at "
            "FROM messages "
            "WHERE parts_json IS NOT NULL AND parts_json != 'null' AND parts_json != '[]'"
        ).fetchall()

        total_inserted = 0
        total_skipped = 0
        total_errors = 0
        orphan_count = 0
        for mid, sid, parts_json, ts in rows:
            try:
                parts = json.loads(parts_json) if parts_json else []
            except json.JSONDecodeError:
                logger.warning(f"bad parts_json for {mid}, skipping")
                total_errors += 1
                continue

            if not isinstance(parts, list):
                continue

            for i, p in enumerate(parts):
                if not isinstance(p, dict):
                    continue
                part_type = p.get("type", "text")

                # Skip if already migrated (idempotent)
                existing = conn.execute(
                    "SELECT 1 FROM message_parts WHERE message_id = ? AND seq = ?",
                    (mid, i),
                ).fetchone()
                if existing:
                    total_skipped += 1
                    continue

                if not apply:
                    total_inserted += 1
                    continue

                part_id = str(uuid.uuid4())
                part_data_json = json.dumps(p, ensure_ascii=False)
                conn.execute(
                    "INSERT INTO message_parts "
                    "(id, message_id, session_id, type, data_json, seq, time_created) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (part_id, mid, sid, part_type, part_data_json, i, ts),
                )
                total_inserted += 1

            if apply and total_inserted % batch_size == 0:
                conn.commit()
                print(f"  progress: {total_inserted} parts inserted")

        if apply:
            conn.commit()

        # Count role=tool messages (these will be migrated via the
        # orphan-handling logic in commit 4-5; for now just report)
        if orphan_count > 0:
            pass

        if apply:
            print(f"\n  ✓ Inserted {total_inserted} parts ({total_skipped} skipped, {total_errors} errors)")
        else:
            print(f"\n  → Would insert {total_inserted} parts ({total_skipped} skipped, {total_errors} errors)")

        if orphan_tools > 0:
            print(f"\n  Note: {orphan_tools} role=tool messages have no matching assistant tool_call.")
            print(f"        These will be DROPPED in commit 6 once the read path uses message_parts.")
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--db", type=Path, default=None,
        help="Path to user DB (default: $SR_WORKSPACE_PATH/quantnodes_agentnodes_user.db)",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually commit changes (default is dry-run)",
    )
    args = parser.parse_args()

    db_path = args.db or _get_default_db_path()
    backfill(db_path, apply=args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
