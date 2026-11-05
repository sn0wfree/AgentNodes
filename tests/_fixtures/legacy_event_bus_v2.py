"""EventBusV2 — **test harness only** (moved from api/session, W3b).

Production uses ``EventStore`` (``strategy_research.kernel.agent.event_store``).
This module remains so projector / dual-write regression tests can drive
the historical publish path without keeping a deprecated API surface.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
import uuid
import warnings
from pathlib import Path
from typing import List, Optional

from strategy_research.kernel.events.event_v2 import EventV2, is_known_event_type

from .legacy_events import EventBus, SSEEvent

logger = logging.getLogger(__name__)


class EventBusV2:
    """Dual-write event publisher (event_log + EventBus).

    .. deprecated::
        Use ``EventStore`` (``strategy_research.kernel.agent.event_store``)
        instead. This class is kept for backward compatibility.

    Attributes:
        event_bus: Legacy EventBus for SSE delivery. Unchanged.
        db_path: Path to SQLite DB (for event_log).
    """

    def __init__(
        self,
        event_bus: EventBus,
        db_path: Path,
        flush_to_messages: bool = False,
    ) -> None:
        """
        .. deprecated::
            Use ``EventStore`` instead. EventBusV2 is kept for backward
            compatibility.
        """
        warnings.warn(
            "EventBusV2 is deprecated, use EventStore instead",
            DeprecationWarning, stacklevel=2,
        )
        self.event_bus = event_bus
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        self._flush_to_messages = flush_to_messages
        # Lazy-import projector to avoid circular imports at startup
        self._projector = None
        # Shared sqlite connection for event_log writes, guarded by
        # self._lock (see docs/design/projector-incremental.md §3). Created
        # lazily; recreated on OperationalError (db replaced/deleted).
        self._conn = None

    def _get_projector(self):
        """Lazy-get the projector instance."""
        if self._projector is None:
            from strategy_research.interfaces.api.session.projector import Projector
            self._projector = Projector(self.db_path)
        return self._projector

    def _get_conn(self) -> sqlite3.Connection:
        """Get the shared write connection (caller must hold the lock).

        check_same_thread=False is safe here: all access happens under
        self._lock, so only one thread touches the connection at a time.
        """
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        return self._conn

    def publish(self, event: EventV2) -> None:
        """Publish to event_log AND forward to legacy EventBus.

        Args:
            event: EventV2 to publish.
        """
        # Validate (forward-compat: log + continue on unknown types)
        if not is_known_event_type(event.type):
            logger.debug(
                "EventBusV2: unknown event type %r (will persist but projector may skip)",
                event.type,
            )

        # Sink 1: persist to event_log (the source of truth)
        with self._lock:
            self._persist_locked(event)

        # Sink 2: forward to legacy EventBus (live SSE)
        self._forward(event)

        # Sink 3 (optional): flush projector → messages table
        if self._flush_to_messages and self._should_flush(event.type):
            self._flush_projection(event.aggregate_id)

    def emit(
        self,
        session_id: str,
        event_type: str,
        data: Optional[dict] = None,
    ) -> SSEEvent:
        """Build and publish an event — drop-in replacement for EventBus.emit.

        Same signature as EventBus.emit(). Auto-assigns a monotonic
        seq number per session. Dual-writes to event_log + legacy bus.

        This is the B2 entry point: service.py doesn't need to change
        any call sites — just swap the bus instance.

        Args:
            session_id: Session / aggregate ID.
            event_type: Event type string.
            data: Event payload dict.

        Returns:
            The published SSEEvent (for backward compatibility with
            any callers that check the return value).
        """
        # Sink 1: persist to event_log. seq assignment AND the INSERT
        # happen inside a single critical section, so concurrent emit()
        # calls can never reuse a seq (previously the loser's event was
        # silently dropped on an IntegrityError).
        with self._lock:
            seq = self.last_seq(session_id) + 1
            event = EventV2(
                id=uuid.uuid4().hex[:16],
                aggregate_id=session_id,
                seq=seq,
                type=event_type,
                data=data or {},
                time_created=time.time(),
            )
            self._persist_locked(event)
        # Sink 2: forward to legacy EventBus (live SSE)
        result = self._forward(event)
        # Sink 3 (optional): flush projector → messages table
        # Only flush on message-boundary events for performance.
        if self._flush_to_messages and self._should_flush(event.type):
            self._flush_projection(event.aggregate_id)
        return result

    def publish_batch(self, events: List[EventV2]) -> None:
        """Publish multiple events as a single transaction.

        Useful for replay (loading events from event_log and re-emitting
        them) or for bulk operations like L4 compaction finalization.
        """
        if not events:
            return
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("PRAGMA foreign_keys=ON")
                for event in events:
                    row = event.to_row()
                    conn.execute(
                        "INSERT INTO event_log (id, aggregate_id, seq, "
                        "type, data_json, time_created) VALUES "
                        "(?, ?, ?, ?, ?, ?)",
                        (row["id"], row["aggregate_id"], row["seq"],
                         row["type"], row["data_json"], row["time_created"]),
                    )
                conn.commit()
            except sqlite3.OperationalError:
                # DB file replaced/deleted — reconnect once, then re-raise
                # so the caller can log-and-continue as before.
                self._drop_conn()
                raise

        # Forward all events to SSE
        for event in events:
            self._forward(event)

        # Flush if enabled (only once per session in the batch)
        if self._flush_to_messages and events:
            # Only flush if at least one event is a boundary event
            has_boundary = any(self._should_flush(e.type) for e in events)
            if has_boundary:
                session_ids = list({e.aggregate_id for e in events})
                for sid in session_ids:
                    self._flush_projection(sid)

    def _persist(self, event: EventV2) -> None:
        """INSERT event into event_log (locking wrapper).

        See ``_persist_locked``. Convenience for callers that don't
        already hold the bus lock.
        """
        with self._lock:
            self._persist_locked(event)

    def _persist_locked(self, event: EventV2) -> None:
        """INSERT event into event_log. Caller must hold ``self._lock``.

        Best-effort: if the DB write fails, log and continue. The SSE
        forward still happens (so live clients see the event) but
        replay will be missing this event. For production, we'd want
        a retry queue; for B1 we keep it simple.

        Handled errors:
        - sqlite3.IntegrityError: UNIQUE (aggregate_id, seq) collision
          or PK collision on event id. Log and skip.
        - TypeError: data is not JSON-serializable. Log and skip.
        - sqlite3.OperationalError: DB file doesn't exist, no event_log
          table, FK violation, etc. Log and skip.
        """
        try:
            row = event.to_row()
        except (TypeError, ValueError) as exc:
            logger.error(
                "EventBusV2: data not serializable for event %s: %s",
                event.id, exc,
            )
            return

        def _insert() -> None:
            """INSERT the event row + commit (uses the shared connection)."""
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(
                "INSERT INTO event_log (id, aggregate_id, seq, "
                "type, data_json, time_created) VALUES "
                "(?, ?, ?, ?, ?, ?)",
                (row["id"], row["aggregate_id"], row["seq"],
                 row["type"], row["data_json"], row["time_created"]),
            )
            conn.commit()

        try:
            conn = self._get_conn()
        except sqlite3.OperationalError as exc:
            # Cannot even open the DB file (missing dir, permissions, …)
            logger.error(
                "EventBusV2: DB error for event %s: %s", event.id, exc,
            )
            return
        try:
            _insert()
        except sqlite3.OperationalError:
            # Shared connection died (db replaced/deleted) — roll back
            # any dangling transaction, reconnect once and retry.
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            self._drop_conn()
            try:
                conn = self._get_conn()
                _insert()
            except sqlite3.Error as exc:
                logger.error(
                    "EventBusV2: DB error for event %s: %s", event.id, exc,
                )
        except (sqlite3.IntegrityError, sqlite3.Error) as exc:
            # Best-effort: log and continue (SSE forward still happens).
            # Roll back the failed statement so the shared connection
            # doesn't hold a dangling write transaction (which would
            # lock the DB for other writers).
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            if isinstance(exc, sqlite3.IntegrityError):
                # Likely a UNIQUE (aggregate_id, seq) violation
                logger.error(
                    "EventBusV2: seq collision for aggregate=%s seq=%s: %s",
                    event.aggregate_id, event.seq, exc,
                )
            else:
                logger.error(
                    "EventBusV2: persist failed for event %s: %s",
                    event.id, exc,
                )

    def _forward(self, event: EventV2) -> SSEEvent:
        """Forward to legacy EventBus as SSEEvent.

        The SSE event id matches the event_log event id so that
        last_event_id recovery works seamlessly across both sinks.

        Returns:
            The SSEEvent that was published.
        """
        sse_event = SSEEvent(
            event_id=event.id,
            event_type=event.type,
            data=event.data,
            session_id=event.aggregate_id,
            timestamp=event.time_created,
        )
        self.event_bus.publish(sse_event)
        return sse_event

    def _next_seq(self, session_id: str) -> int:
        """Return the next monotonic seq number for a session.

        Locked standalone helper (used by tests and any standalone
        caller). ``emit()`` does its own inline locked seq-assignment
        so that seq computation + INSERT are a single critical section.
        """
        with self._lock:
            return self.last_seq(session_id) + 1

    def _drop_conn(self) -> None:
        """Close + drop the shared connection (after OperationalError)."""
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None

    def invalidate(self, session_id: str) -> None:
        """Drop the projector's cached projection for a session.

        Called when a session is deleted, so the next flush rebuilds
        from event_log instead of serving a stale in-memory state.
        """
        with self._lock:
            if self._projector is not None:
                self._projector.invalidate(session_id)

    def _flush_projection(self, session_id: str) -> None:
        """Flush event_log → messages + message_parts via projector.

        Incremental: replays only events after the last flushed seq
        and writes only the touched messages (see
        docs/design/projector-incremental.md). Falls back to a full flush
        for whole-session rewrites (compact.ended with replacement
        list) and on cache misses.

        Idempotent: projector.flush() uses INSERT OR REPLACE / upserts.

        Serialized under the bus lock so two concurrent flushes of the
        same session cannot interleave (the projector's DELETE of
        non-projected rows must never run against a half-written
        projection).

        Best-effort: if flush fails, log and continue. The event_log
        still has all events, so the projection can be rebuilt at
        any time. This is safer than blocking the event stream.
        """
        try:
            with self._lock:
                proj = self._get_projector()
                state, touched = proj.project_incremental(
                    session_id, collect_touched=True,
                )
                proj.flush(state, touched=touched)
        except Exception as exc:
            logger.error(
                "EventBusV2: flush failed for session %s: %s",
                session_id, exc,
            )

    def _should_flush(self, event_type: str) -> bool:
        """Determine if an event should trigger a projector flush.

        For performance, we only flush on message-boundary events
        (when a new message is created or finalized), not on every
        streaming delta (text_delta, tool_progress, etc.).

        Why this is safe:
        - event_log always has all events (source of truth)
        - messages table is a materialized view that can be stale
          between boundaries (acceptable — it catches up on next flush)
        - If the process crashes between flushes, replaying event_log
          and re-flushing restores full state

        Events that trigger flush:
        - message_received: user message created
        - assistant_message: assistant message finalized
        - compact / compact.ended: compaction message created
        - iter_start: each LLM iteration boundary — persists in-flight
          responses so a refresh during a long agent run still shows
          the completed iterations (not just finalized messages).
        """
        boundary_types = {
            "message_received",
            "assistant_message",
            "compact",
            "compact.ended",
            "iter_start",
        }
        return event_type in boundary_types

    # ── Replay support ──────────────────────────────────────────────
    #
    # Used by:
    # 1. SSE handler (Phase 3 B2) to recover missed events after
    #    client reconnect
    # 2. Projector (Phase 3 B1.4) to rebuild message state from
    #    event_log
    # 3. Tests to verify round-trip persistence

    def replay(
        self,
        session_id: str,
        after_seq: int = 0,
        limit: Optional[int] = None,
    ) -> List[EventV2]:
        """Read events from event_log in seq order.

        Args:
            session_id: Aggregate (session) ID to replay.
            after_seq: Return only events with seq > after_seq (for
                resume-after-disconnect). 0 means "all events".
            limit: Optional cap on number of events returned.

        Returns:
            List of EventV2 ordered by seq ASC. Returns [] if the
            DB doesn't exist, has no event_log table, or the session
            has no events.
        """
        sql = (
            "SELECT id, aggregate_id, seq, type, data_json, time_created "
            "FROM event_log WHERE aggregate_id = ? AND seq > ? "
            "ORDER BY seq ASC"
        )
        params: tuple = (session_id, after_seq)
        if limit is not None:
            sql += " LIMIT ?"
            params = (session_id, after_seq, limit)

        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(sql, params).fetchall()
            finally:
                conn.close()
        except sqlite3.OperationalError as exc:
            # DB doesn't exist, or event_log table is missing
            logger.debug(
                "EventBusV2.replay: DB unavailable for %s: %s",
                self.db_path, exc,
            )
            return []
        return [EventV2.from_row(r) for r in rows]

    def last_seq(self, session_id: str) -> int:
        """Return the highest seq stored for this session, or 0.

        Returns 0 if the DB doesn't exist or has no event_log table.
        """
        try:
            conn = sqlite3.connect(str(self.db_path))
            try:
                row = conn.execute(
                    "SELECT MAX(seq) AS max_seq FROM event_log "
                    "WHERE aggregate_id = ?",
                    (session_id,),
                ).fetchone()
            finally:
                conn.close()
        except sqlite3.OperationalError as exc:
            logger.debug(
                "EventBusV2.last_seq: DB unavailable: %s", exc,
            )
            return 0
        if row is None or row[0] is None:
            return 0
        return int(row[0])

    def count(self, session_id: Optional[str] = None) -> int:
        """Return event_log row count. If session_id given, only that session.

        Returns 0 if the DB doesn't exist or has no event_log table.
        """
        try:
            conn = sqlite3.connect(str(self.db_path))
            try:
                if session_id is None:
                    row = conn.execute("SELECT COUNT(*) FROM event_log").fetchone()
                else:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM event_log WHERE aggregate_id = ?",
                        (session_id,),
                    ).fetchone()
            finally:
                conn.close()
        except sqlite3.OperationalError as exc:
            logger.debug(
                "EventBusV2.count: DB unavailable: %s", exc,
            )
            return 0
        return int(row[0]) if row else 0


__all__ = ["EventBusV2"]
