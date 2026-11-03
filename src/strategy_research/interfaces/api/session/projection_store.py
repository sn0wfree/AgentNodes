"""Projected-state persistence: connection, snapshots, flush.

Extracted from projector.py in the Phase 2 split
(docs/phase2-web-session-projector-design.md). Owns the sqlite
connection lifecycle (close() is explicit — the WinError-32 flake
family lives where connections are leaked) and the materialized
messages/message_parts writes. Pure projection loops live in the
Projector facade, not here.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .projection_models import ProjectedMessage, ProjectedSession

logger = logging.getLogger(__name__)


class ProjectionStore:
    """Connection + snapshot + materialized-view writes."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        # Reuse a single connection across flush calls to avoid the
        # overhead of creating a new connection every time and to
        # prevent "database is locked" errors from concurrent writers.
        self._conn: sqlite3.Connection | None = None
        # Serialize concurrent flush calls so two threads never
        # interleave their BEGIN/COMMIT on the same connection.
        self._flush_lock = threading.Lock()
        # Handler dispatch table: event_type → handler function
        # Per-session in-memory projections, keyed by session_id. Only
        # used by project_incremental(); project() remains a pure
        # function. Entries are invalidated on session deletion.
        # See docs/projector-incremental.md.
        self._cache: Dict[str, ProjectedSession] = {}


    def _get_conn(self) -> sqlite3.Connection:
        """Return a reusable connection, creating one on first call.

        ``check_same_thread=False`` is safe here: the ``_flush_lock``
        ensures only one thread uses the connection at a time.
        """
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path), check_same_thread=False,
            )
        return self._conn


    def close(self) -> None:
        """Close the cached connection (idempotent).

        Owners with a finite lifetime (test fixtures, server shutdown)
        must call this before deleting the db file — sqlite keeps the
        file locked until the connection is closed, which fails temp
        cleanup on Windows with WinError 32.
        """
        with self._flush_lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ── Snapshot support (P0-1 B2) ─────────────────────────────────

    _SNAPSHOT_SCHEMA_DDL = (
        "CREATE TABLE IF NOT EXISTS snapshots ("
        "session_id TEXT PRIMARY KEY, "
        "seq INTEGER NOT NULL, "
        "snapshot_json TEXT NOT NULL, "
        "created_at REAL NOT NULL)"
    )
    _SNAPSHOT_INDEX_DDL = (
        "CREATE INDEX IF NOT EXISTS idx_snapshots_session_seq "
        "ON snapshots(session_id, seq)"
    )


    def _ensure_snapshots_table(self, conn: sqlite3.Connection) -> None:
        conn.execute(self._SNAPSHOT_SCHEMA_DDL)
        conn.execute(self._SNAPSHOT_INDEX_DDL)


    def _save_snapshot(
        self,
        conn: sqlite3.Connection,
        state: ProjectedSession,
    ) -> None:
        """Persist ``state`` to ``snapshots`` (UPSERT, single-row).

        Called from ``flush()`` inside the existing transaction so the
        snapshot is consistent with the messages + message_parts it
        accompanies.
        """
        self._ensure_snapshots_table(conn)
        conn.execute(
            "INSERT INTO snapshots (session_id, seq, snapshot_json, created_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET "
            "seq = excluded.seq, snapshot_json = excluded.snapshot_json, "
            "created_at = excluded.created_at "
            "WHERE excluded.seq > snapshots.seq",
            (
                state.session_id,
                state.last_seq,
                json.dumps(state.to_dict(), ensure_ascii=False),
                sqlite3.time.time(),
            ),
        )


    def _load_snapshot(
        self,
        conn: sqlite3.Connection,
        session_id: str,
        after_seq: int,
    ) -> Optional[ProjectedSession]:
        """Return the most recent snapshot whose seq ≥ ``after_seq``."""
        self._ensure_snapshots_table(conn)
        row = conn.execute(
            "SELECT snapshot_json, seq FROM snapshots "
            "WHERE session_id = ? AND seq >= ? "
            "ORDER BY seq DESC LIMIT 1",
            (session_id, after_seq),
        ).fetchone()
        if row is None:
            return None
        return ProjectedSession.from_dict(json.loads(row[0]))

    # ── Incremental projection (see docs/projector-incremental.md) ─


    @staticmethod
    def _message_row(msg: ProjectedMessage) -> Dict[str, Any]:
        """Serialize a single message to a messages-table row.

        Includes ``metadata_json`` so delta flushes carry agent_id (and
        any other metadata) — matching the full-flush path
        (``to_message_rows``). Without this, the first delta INSERT
        writes NULL and the UPSERT's COALESCE keeps it NULL forever.
        """
        return {
            "id": msg.id,
            "session_id": msg.session_id,
            "role": msg.role,
            "content": msg.content,
            "message_type": msg.message_type,
            "created_at": msg.created_at,
            "seq": msg.seq,
            "metadata_json": json.dumps(msg.metadata, ensure_ascii=False)
            if msg.metadata else None,
        }


    @staticmethod
    def _part_rows(msg: ProjectedMessage) -> List[Dict[str, Any]]:
        """Serialize a single message's parts to message_parts rows."""
        return [
            {
                "id": p.id,
                "message_id": msg.id,
                "session_id": msg.session_id,
                "type": p.type,
                "data_json": json.dumps(p.data, ensure_ascii=False),
                "seq": p.seq,
                "time_created": p.time_created,
            }
            for p in msg.parts_in_order()
        ]

    # ── Public API ──────────────────────────────────────────────


    def flush(
        self,
        state: ProjectedSession,
        touched: Optional[Set[str]] = None,
        *,
        snapshot_interval: int = 200,
    ) -> None:
        """Atomically UPSERT the projected state to messages + message_parts.

        Idempotent: calling flush() twice with the same state produces
        the same result (no duplicate rows). Uses INSERT OR REPLACE
        for both messages and message_parts.

        Safety:
        - Runs in a single transaction
        - Only touches rows belonging to state.session_id
        - Deletes message_parts rows that no longer exist in the
          projection (handles part deletion edge cases)

        Args:
            touched: Optional set of message ids to write (delta
                flush, see docs/projector-incremental.md). Only those
                messages are UPSERTed and their parts rewritten.
                ``None`` or a set containing the ``"*"`` sentinel
                (whole-session rewrite, e.g. compact.ended) performs a
                full flush.

        This is the B2 write path. In B2, service.py publishes events
        via EventBusV2 which writes to event_log, then calls flush()
        to materialize the state to messages + message_parts for the
        existing read path (SessionStore.get_messages).

        Eventually (B3+), the read path will use the projector
        directly and messages + message_parts can be removed.
        """
        full = touched is None or "*" in touched
        msg_rows = state.to_message_rows() if full else []
        part_rows = state.to_part_rows() if full else []

        with self._flush_lock:
            conn = self._get_conn()
            try:
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute("BEGIN")

                if full:
                    # ── Full flush: UPSERT everything ──────────────
                    msg_ids = {row["id"] for row in msg_rows}
                    for row in msg_rows:
                        self._upsert_message(conn, row)

                    if msg_ids:
                        placeholders = ",".join("?" * len(msg_ids))
                        conn.execute(
                            f"DELETE FROM messages "
                            f"WHERE session_id = ? AND id NOT IN ({placeholders})",
                            (state.session_id, *msg_ids),
                        )
                    else:
                        existing_count = conn.execute(
                            "SELECT COUNT(*) FROM messages WHERE session_id = ?",
                            (state.session_id,),
                        ).fetchone()[0]
                        if existing_count > 0:
                            logger.warning(
                                "flush: projection empty but DB has %d messages "
                                "for session %s — skipping DELETE "
                                "(possible event_log gap)",
                                existing_count, state.session_id,
                            )
                        else:
                            conn.execute(
                                "DELETE FROM messages WHERE session_id = ?",
                                (state.session_id,),
                            )

                    # UPSERT message_parts
                    for row in part_rows:
                        self._insert_part(conn, row)

                    # Delete parts that no longer exist (session scope)
                    part_ids = {r["id"] for r in part_rows}
                    if part_ids:
                        placeholders = ",".join("?" * len(part_ids))
                        conn.execute(
                            f"DELETE FROM message_parts "
                            f"WHERE session_id = ? AND id NOT IN ({placeholders})",
                            (state.session_id, *part_ids),
                        )
                    else:
                        conn.execute(
                            "DELETE FROM message_parts WHERE session_id = ?",
                            (state.session_id,),
                        )
                else:
                    # ── Delta flush: only touched messages ──────────
                    for mid in sorted(touched):
                        msg = state.messages.get(mid)
                        if msg is None:
                            continue
                        self._upsert_message(conn, self._message_row(msg))
                        conn.execute(
                            "DELETE FROM message_parts WHERE message_id = ?",
                            (mid,),
                        )
                        for row in self._part_rows(msg):
                            self._insert_part(conn, row)

                # P0-1 B2: snapshot persistence
                if (
                    snapshot_interval > 0
                    and state.last_seq > 0
                    and state.last_seq % snapshot_interval == 0
                ):
                    self._save_snapshot(conn, state)

                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise


    @staticmethod
    def _upsert_message(conn: sqlite3.Connection, row: Dict[str, Any]) -> None:
        """UPSERT a single messages-table row (preserves metadata_json)."""
        metadata_json = row.get("metadata_json")
        conn.execute(
            "INSERT INTO messages "
            "(id, session_id, role, content, created_at, "
            "message_type, seq, metadata_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "session_id=excluded.session_id, "
            "role=excluded.role, "
            "content=excluded.content, "
            "created_at=excluded.created_at, "
            "message_type=excluded.message_type, "
            "seq=excluded.seq, "
            "metadata_json=COALESCE(excluded.metadata_json, "
            "                      messages.metadata_json)",
            (
                row["id"],
                row["session_id"],
                row["role"],
                row["content"],
                row["created_at"],
                row["message_type"],
                row["seq"],
                metadata_json,
            ),
        )


    @staticmethod
    def _insert_part(conn: sqlite3.Connection, row: Dict[str, Any]) -> None:
        """INSERT OR REPLACE a single message_parts row."""
        conn.execute(
            "INSERT OR REPLACE INTO message_parts "
            "(id, message_id, session_id, type, data_json, "
            "seq, time_created) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                row["id"],
                row["message_id"],
                row["session_id"],
                row["type"],
                row["data_json"],
                row["seq"],
                row["time_created"],
            ),
        )

