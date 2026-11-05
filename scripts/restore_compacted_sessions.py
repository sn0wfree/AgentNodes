#!/usr/bin/env python3
"""Restore original messages for compacted sessions from event_log.

With the opencode-aligned compaction behavior, compaction no longer
deletes original messages — it keeps them in the chat record and only
hides them from LLM context. Sessions compacted BEFORE this change
(where the projector cleared original messages) can be restored by
re-projecting from event_log (the source of truth).

Usage:
    python scripts/restore_compacted_sessions.py [--session SESSION_ID]
        [--db-path /path/to/.quantnodes_agentnodes_session.db]

Without --session, restores all sessions that have compaction markers.
Idempotent: re-projecting is a pure function of event_log.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# Ensure we can import the package when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _resolve_db_path(db_path: str | None) -> Path:
    if db_path:
        p = Path(db_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    from agentnodes.core.agent.memory_manager import resolve_session_db_path
    return resolve_session_db_path()


def list_compacted_sessions(db_path: Path) -> list[str]:
    """Sessions whose event_log contains a compact.ended event."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT DISTINCT aggregate_id FROM event_log "
            "WHERE type IN ('compact.ended', 'compact') ORDER BY aggregate_id"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def restore_session(db_path: Path, session_id: str) -> int:
    """Re-project a session from event_log and flush to messages table.

    Returns the number of messages written.
    """
    from agentnodes.api.session.projector import Projector

    proj = Projector(str(db_path))
    state = proj.project(session_id)
    proj.flush(state)
    return len(state.messages)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", default=None, help="Only restore this session ID")
    parser.add_argument("--db-path", default=None, help="Explicit DB path override")
    args = parser.parse_args()

    db_path = _resolve_db_path(args.db_path)
    print(f"DB: {db_path}")

    if args.session:
        sessions = [args.session]
    else:
        sessions = list_compacted_sessions(db_path)
        print(f"Found {len(sessions)} sessions with compaction events")

    restored = 0
    for sid in sessions:
        try:
            n = restore_session(db_path, sid)
            restored += 1
            print(f"  restored {sid[:12]}... ({n} messages)")
        except Exception as exc:
            print(f"  FAILED {sid[:12]}...: {exc}", file=sys.stderr)

    print(f"\nDone: {restored}/{len(sessions)} sessions restored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
