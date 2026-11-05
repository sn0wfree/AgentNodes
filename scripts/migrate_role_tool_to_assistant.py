#!/usr/bin/env python3
"""Migrate role=tool rows into their parent assistant's tool_call part (Level 2, commit 6).

This script runs BEFORE the destructive commit that drops the role=tool
rows. It:
1. For each role=tool message, find the parent assistant message
   (the one whose parts_json has a tool_call with matching tc_id)
2. Update the parent assistant's tool_call part to include the result
3. After all updates verified, the destructive commit drops:
   - role=tool rows
   - parts_json column
   - tool_call_id column

Usage:
    # Dry-run (default): print summary, do nothing
    python3 scripts/migrate_role_tool_to_assistant.py

    # Actually commit
    python3 scripts/migrate_role_tool_to_assistant.py --apply

    # Custom DB path
    python3 scripts/migrate_role_tool_to_assistant.py --db /path/to/db.sqlite

Safety:
- Default mode is dry-run; nothing is modified without --apply.
- Only role=tool rows with a matching parent assistant are migrated.
  Orphan role=tool rows (9 in qn-research production DB) are logged
  and DROPPED in the destructive commit.
- Idempotent: running with --apply on already-migrated rows is a no-op.
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_default_db_path() -> Path:
    import os
    db_dir = Path(os.environ.get("AN_WORKSPACE_PATH", str(Path.home() / ".quantnodes")))
    return db_dir / "quantnodes_agentnodes_user.db"


def _ensure_message_parts_table(conn: sqlite3.Connection) -> None:
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


def _count_role_tool_messages(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM messages WHERE role = 'tool'").fetchone()[0]


def _count_orphan_role_tool(conn: sqlite3.Connection) -> int:
    """role=tool messages whose tool_call_id has no matching assistant tool_call part."""
    return conn.execute(
        """
        SELECT COUNT(*) FROM messages tool_msg
        WHERE tool_msg.role = 'tool'
        AND NOT EXISTS (
            SELECT 1 FROM message_parts mp
            WHERE mp.message_id != tool_msg.id
            AND json_extract(mp.data_json, '$.id') = tool_msg.tool_call_id
            AND json_extract(mp.data_json, '$.type') = 'tool_call'
        )
        """
    ).fetchone()[0]


def _count_assistant_with_tool_call_results(conn: sqlite3.Connection) -> int:
    """tool_call parts that already have a result set."""
    return conn.execute(
        """
        SELECT COUNT(*) FROM message_parts
        WHERE type = 'tool_call'
        AND json_extract(data_json, '$.result') IS NOT NULL
        AND json_extract(data_json, '$.result') != ''
        AND json_extract(data_json, '$.result') != 'null'
        """
    ).fetchone()[0]


def migrate(db_path: Path, apply: bool) -> None:
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

        total_tool = _count_role_tool_messages(conn)
        total_orphan = _count_orphan_role_tool(conn)
        already_with_result = _count_assistant_with_tool_call_results(conn)

        print(f"\nStats:")
        print(f"  role=tool messages:           {total_tool}")
        print(f"  orphan role=tool (no match):  {total_orphan}")
        print(f"  tool_call parts with result:  {already_with_result}")

        if total_tool == 0:
            print("\n  No role=tool messages to migrate.")
            return

        # For each role=tool message, find the parent assistant's tool_call part
        # and update it with the result.
        tool_rows = conn.execute(
            "SELECT id, session_id, tool_call_id, content FROM messages "
            "WHERE role = 'tool' ORDER BY created_at"
        ).fetchall()

        migrated = 0
        orphans = 0
        errors = 0
        for mid, sid, tc_id, content in tool_rows:
            if not tc_id:
                orphans += 1
                continue

            # Find the parent assistant's tool_call part with matching tc_id.
            # message_parts stores the FULL part data; tool_call parts have
            # an "id" field that matches the role=tool row's tool_call_id.
            parent_part = conn.execute(
                """
                SELECT mp.id, mp.data_json, mp.message_id
                FROM message_parts mp
                JOIN messages m ON m.id = mp.message_id
                WHERE m.session_id = ?
                AND json_extract(mp.data_json, '$.id') = ?
                AND json_extract(mp.data_json, '$.type') = 'tool_call'
                LIMIT 1
                """,
                (sid, tc_id),
            ).fetchone()

            if parent_part is None:
                orphans += 1
                continue

            part_id, part_data_json, parent_msg_id = parent_part
            try:
                part = json.loads(part_data_json)
            except json.JSONDecodeError:
                errors += 1
                continue

            # Update the tool_call part with the result
            part["result"] = content
            part["status"] = "done"
            new_data_json = json.dumps(part, ensure_ascii=False)

            if not apply:
                migrated += 1
                continue

            conn.execute(
                "UPDATE message_parts SET data_json = ? WHERE id = ?",
                (new_data_json, part_id),
            )
            migrated += 1

        if apply:
            conn.commit()

        if apply:
            print(f"\n  ✓ Migrated {migrated} tool results to parent parts")
            print(f"  ⚠ {orphans} orphan role=tool (will be DROPPED in next commit)")
            if errors:
                print(f"  ! {errors} errors")
        else:
            print(f"\n  → Would migrate {migrated}")
            print(f"  ⚠ {orphans} orphan role=tool (will be DROPPED)")
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    db_path = args.db or _get_default_db_path()
    migrate(db_path, apply=args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
