"""Backfill: messages + message_parts → event_log (Level 3, B3 commit 4).

Reconstructs event_log from existing messages + message_parts tables.
This enables the projector read path for pre-B2 sessions that don't
have events in event_log yet.

Algorithm:
For each message (ordered by seq):
  - user message → message_received event
  - assistant message → text.started + text.ended (for each text part),
    tool_call + tool_result (for each tool_call part),
    assistant_message (final summary)

The backfill is idempotent: if a session already has events in
event_log, it's skipped. Use --force to overwrite.

Usage:
    python -m strategy_research.interfaces.api.session.backfill_event_log
    python -m strategy_research.interfaces.api.session.backfill_event_log --db-path /path/to.db
    python -m strategy_research.interfaces.api.session.backfill_event_log --session 700dc7f7
    python -m strategy_research.interfaces.api.session.backfill_event_log --force
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def backfill_event_log(
    db_path: Path,
    session_id: Optional[str] = None,
    force: bool = False,
) -> dict:
    """Backfill event_log from messages + message_parts.

    Args:
        db_path: Path to SQLite DB.
        session_id: Only backfill this session. If None, all sessions.
        force: If True, delete existing events for the session first.
            If False, skip sessions that already have events.

    Returns:
        dict with stats: sessions_total, sessions_backfilled,
        sessions_skipped, events_inserted.
    """
    db_path = Path(db_path)
    stats = {
        "sessions_total": 0,
        "sessions_backfilled": 0,
        "sessions_skipped": 0,
        "events_inserted": 0,
    }

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # Ensure event_log table exists (canonical schema — P0-1 A1).
        from strategy_research.foundation.storage.event_schema import ensure_event_log_schema

        ensure_event_log_schema(conn)

        # Get sessions to process
        if session_id:
            session_rows = conn.execute(
                "SELECT id FROM sessions WHERE id = ?", (session_id,)
            ).fetchall()
        else:
            session_rows = conn.execute(
                "SELECT id FROM sessions ORDER BY created_at ASC"
            ).fetchall()

        stats["sessions_total"] = len(session_rows)

        for s_row in session_rows:
            sid = s_row["id"]

            # Check if already has events
            existing_count = conn.execute(
                "SELECT COUNT(*) FROM event_log WHERE aggregate_id = ?",
                (sid,),
            ).fetchone()[0]

            if existing_count > 0 and not force:
                stats["sessions_skipped"] += 1
                continue

            if force and existing_count > 0:
                conn.execute(
                    "DELETE FROM event_log WHERE aggregate_id = ?", (sid,),
                )

            events = _generate_events_for_session(conn, sid)
            if not events:
                stats["sessions_skipped"] += 1
                continue

            # Insert events
            seq = 0
            for evt in events:
                seq += 1
                event_id = uuid.uuid4().hex[:16]
                conn.execute(
                    "INSERT INTO event_log "
                    "(id, aggregate_id, seq, type, data_json, time_created) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        event_id,
                        sid,
                        seq,
                        evt["type"],
                        json.dumps(evt["data"], ensure_ascii=False),
                        evt["time_created"],
                    ),
                )
                stats["events_inserted"] += 1

            stats["sessions_backfilled"] += 1
            logger.info(
                "Backfilled %d events for session %s (was %s messages)",
                seq, sid[:12],
                conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE session_id = ?", (sid,)
                ).fetchone()[0],
            )

        conn.commit()
    finally:
        conn.close()

    return stats


def _generate_events_for_session(
    conn: sqlite3.Connection, session_id: str,
) -> list[dict]:
    """Generate event_log events for a session from messages + parts.

    Returns list of {type, data, time_created} dicts in chronological order.
    """
    events: list[dict] = []

    # Get messages ordered by seq
    msg_rows = conn.execute(
        "SELECT id, role, content, created_at, message_type, seq "
        "FROM messages WHERE session_id = ? ORDER BY seq ASC, created_at ASC",
        (session_id,),
    ).fetchall()

    for msg in msg_rows:
        msg_id = msg["id"]
        role = msg["role"]
        content = msg["content"] or ""
        created_at = msg["created_at"] or time.time()
        # Use message_type as the primary discriminator since it
        # more accurately reflects the message kind. Fall back to
        # role for legacy data where message_type is missing.
        msg_type = msg["message_type"] or role

        # Get parts for this message
        parts = conn.execute(
            "SELECT id, type, data_json, seq, time_created "
            "FROM message_parts WHERE message_id = ? ORDER BY seq ASC",
            (msg_id,),
        ).fetchall()

        if msg_type == "user":
            events.append({
                "type": "message_received",
                "data": {
                    "message_id": msg_id,
                    "user_message_id": msg_id,
                    "content": content,
                    "role": "user",
                    "created_at": created_at,
                },
                "time_created": created_at,
            })

        elif msg_type == "assistant":
            # Emit events for each part first
            for part in parts:
                part_type = part["type"]
                part_id = part["id"]
                try:
                    part_data = json.loads(part["data_json"])
                except (json.JSONDecodeError, TypeError):
                    part_data = {}
                part_time = part["time_created"] or created_at

                if part_type == "text":
                    text = part_data.get("text", "")
                    events.append({
                        "type": "text.started",
                        "data": {
                            "message_id": msg_id,
                            "text_id": part_id,
                        },
                        "time_created": part_time,
                    })
                    events.append({
                        "type": "text.ended",
                        "data": {
                            "message_id": msg_id,
                            "text_id": part_id,
                            "text": text,
                        },
                        "time_created": part_time,
                    })

                elif part_type == "tool_call":
                    tool = part_data.get("tool", part_data.get("name", ""))
                    tool_input = part_data.get("input", part_data.get("arguments", {}))
                    events.append({
                        "type": "tool_call",
                        "data": {
                            "message_id": msg_id,
                            "id": part_id,
                            "tool": tool,
                            "input": tool_input,
                            "name": part_data.get("name", ""),
                            "arguments": part_data.get("arguments", ""),
                        },
                        "time_created": part_time,
                    })
                    # If the part has a result, emit tool_result too
                    if part_data.get("result") is not None or part_data.get("status"):
                        events.append({
                            "type": "tool_result",
                            "data": {
                                "message_id": msg_id,
                                "id": part_id,
                                "result": part_data.get("result", ""),
                                "status": part_data.get("status", "done"),
                            },
                            "time_created": part_time,
                        })

                elif part_type == "thinking":
                    text = part_data.get("text", "")
                    events.append({
                        "type": "thinking_start",
                        "data": {"message_id": msg_id},
                        "time_created": part_time,
                    })
                    if text:
                        events.append({
                            "type": "thinking_delta",
                            "data": {
                                "message_id": msg_id,
                                "delta": text,
                            },
                            "time_created": part_time,
                        })
                    events.append({
                        "type": "thinking_done",
                        "data": {"message_id": msg_id},
                        "time_created": part_time,
                    })

                # Other part types (image, table, chart, etc.) are
                # preserved as-is via forward-compat; we skip them here
                # since the projector will also skip unknown types.

            # Final assistant_message event
            events.append({
                "type": "assistant_message",
                "data": {
                    "message_id": msg_id,
                    "content": content,
                    "message_type": msg_type,
                },
                "time_created": created_at,
            })

        elif msg_type == "compaction":
            events.append({
                "type": "compact",
                "data": {
                    "message_id": msg_id,
                    "summary": content,
                    "content": content,
                },
                "time_created": created_at,
            })

        # Other roles (tool, system, error) — skip for now
        # since the projector doesn't handle them in B3. They're still
        # in the messages table and readable via the legacy path.

    return events


def main() -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Backfill event_log from messages + message_parts"
    )
    parser.add_argument(
        "--db-path", type=str, default=None,
        help="Path to SQLite DB (default: SR_WORKSPACE_PATH/user.db)",
    )
    parser.add_argument(
        "--session", type=str, default=None,
        help="Only backfill this session ID",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Delete existing events for sessions before backfilling",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Verbose logging",
    )

    args = parser.parse_args()

    # 统一入口（docs/logging-unified-entry-design-20261102.md §4.4）：
    # script 面 console → stdout，日志随 run_bg 重定向进 run.log。
    from strategy_research.foundation.observability import configure_logging
    configure_logging(
        "script",
        level=logging.DEBUG if args.verbose else logging.INFO,
    )

    if args.db_path:
        db_path = Path(args.db_path)
    else:
        import os
        workspace = Path(os.environ.get(
            "SR_WORKSPACE_PATH", Path.home() / ".quantnodes",
        ))
        db_path = workspace / "quantnodes_strategy_research_user.db"

    if not db_path.exists():
        logger.error("DB not found: %s", db_path)
        raise SystemExit(1)

    logger.info("Backfilling event_log from: %s", db_path)
    if args.session:
        logger.info("Session filter: %s", args.session)
    if args.force:
        logger.info("Force mode: will overwrite existing events")

    t0 = time.time()
    stats = backfill_event_log(
        db_path,
        session_id=args.session,
        force=args.force,
    )
    elapsed = time.time() - t0

    logger.info(
        "Done in %.1fs: %d sessions backfilled, %d skipped, %d events inserted",
        elapsed,
        stats["sessions_backfilled"],
        stats["sessions_skipped"],
        stats["events_inserted"],
    )


if __name__ == "__main__":
    main()
