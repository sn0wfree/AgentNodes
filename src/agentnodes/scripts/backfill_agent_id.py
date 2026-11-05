"""Backfill messages.metadata_json.agent_id for study round messages.

Usage:
    python -m agentnodes.scripts.backfill_agent_id --db <session.db> [--session <session_id>] [--dry-run]

For study round messages (id format ``study:{study_id}:r{round}:{agent}``),
derives the agent_id from the message id tail and writes it into
``messages.metadata_json.agent_id`` — but only for rows whose metadata
is missing the key (idempotent).

Why this is needed: the projector's delta-flush path did not serialize
``metadata_json`` (fixed), so messages written via delta flushes ended
up with NULL metadata and the frontend could not render the agent's
name/avatar for them.

Example:
    python -m agentnodes.scripts.backfill_agent_id \
        --db /home/ll/Public/qn-research/.quantnodes_agentnodes_session.db \
        --session study:study_f48295053041:round:3
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def backfill(db_path: Path, session_id: str | None, dry_run: bool) -> int:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        if session_id:
            rows = conn.execute(
                "SELECT id, metadata_json FROM messages "
                "WHERE session_id = ? AND id LIKE 'study:%'",
                (session_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, metadata_json FROM messages WHERE id LIKE 'study:%'",
            ).fetchall()

        updated = 0
        for row in rows:
            mid = row["id"]
            # id format: study:{study_id}:r{round}:{agent}
            parts = mid.split(":")
            if len(parts) < 4 or not parts[2].startswith("r"):
                continue
            agent_id = ":".join(parts[3:])
            if not agent_id:
                continue

            try:
                metadata = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
            except (json.JSONDecodeError, TypeError):
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            if metadata.get("agent_id"):
                continue  # already set — idempotent

            metadata["agent_id"] = agent_id
            if not dry_run:
                conn.execute(
                    "UPDATE messages SET metadata_json = ? WHERE id = ?",
                    (json.dumps(metadata, ensure_ascii=False), mid),
                )
            updated += 1

        if not dry_run:
            conn.commit()
        print(f"{'[dry-run] ' if dry_run else ''}backfilled agent_id on {updated} message(s)")
        return updated
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", required=True, type=Path,
        help="Path to the session DB (.quantnodes_agentnodes_session.db)",
    )
    parser.add_argument(
        "--session", default=None,
        help="Limit to one session id (e.g. study:study_xxx:round:3). "
             "Default: all study messages.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"error: db not found: {args.db}", file=sys.stderr)
        return 1
    backfill(args.db, args.session, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
