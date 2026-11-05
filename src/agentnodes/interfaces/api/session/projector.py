"""Projector — derive message state from event_log (Level 3, B3 commit 1).

Facade over the Phase 2 split:
- projection_models   : ProjectedPart / ProjectedMessage / ProjectedSession
- projection_handlers : event -> state updates (pure, zero IO)
- projection_store    : connection lifecycle, snapshots, materialized writes

The projector is the authoritative source of message state in the
event-sourced architecture: event_log is the source of truth and
messages/message_parts are materialized views (flush is best-effort).
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from agentnodes.kernel.events.event_v2 import EventType, EventV2
from .projection_models import (  # noqa: F401  (re-exports)
    ProjectedMessage,
    ProjectedPart,
    ProjectedSession,
)
from .projection_store import ProjectionStore

logger = logging.getLogger(__name__)


class Projector:
    """Read-side projector: events -> ProjectedSession.

    Owns the per-instance projection cache and the pure projection
    loops; persistence and event handlers live in the sibling modules.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._store = ProjectionStore(self.db_path)
        # Per-session in-memory projections, keyed by session_id. Only
        # used by project_incremental(); project() remains a pure
        # function. Entries are invalidated on session deletion.
        self._cache: Dict[str, ProjectedSession] = {}

    # ── Persistence facade (ProjectionStore owns conn + flush) ──

    @property
    def _conn(self) -> Optional[sqlite3.Connection]:
        """Read-only view of the store connection (tests assert laziness)."""
        return self._store._conn

    def _ensure_snapshots_table(self, conn) -> None:
        self._store._ensure_snapshots_table(conn)

    def close(self) -> None:
        """Close the underlying sqlite connection (idempotent)."""
        self._store.close()

    def flush(
        self,
        state: ProjectedSession,
        touched: Optional[Set[str]] = None,
        *,
        snapshot_interval: int = 200,
    ) -> None:
        """Atomically UPSERT the projected state (delegates to store)."""
        self._store.flush(state, touched, snapshot_interval=snapshot_interval)

    def _get_conn(self) -> sqlite3.Connection:
        return self._store._get_conn()

    def _save_snapshot(self, *args, **kwargs) -> None:
        self._store._save_snapshot(*args, **kwargs)

    def _load_snapshot(self, *args, **kwargs):
        return self._store._load_snapshot(*args, **kwargs)

    @staticmethod
    def _message_row(msg: ProjectedMessage) -> Dict[str, Any]:
        return ProjectionStore._message_row(msg)

    @staticmethod
    def _part_rows(msg: ProjectedMessage) -> List[Dict[str, Any]]:
        return ProjectionStore._part_rows(msg)

    def project_incremental(
        self,
        session_id: str,
        collect_touched: bool = False,
    ) -> Tuple[ProjectedSession, Optional[Set[str]]]:
        """Extend the cached projection with only the new events.

        On a cache hit, only events after the cached ``last_seq`` are
        replayed (O(delta) instead of O(N)). On a cache miss, falls
        back to a full ``project()``.

        Returns ``(state, touched)``:
        - ``state``: the (mutated) cached ProjectedSession.
        - ``touched``: set of message ids modified by the new events,
          or None when ``collect_touched`` is False. Contains the
          sentinel ``"*"`` when an event rewrites the whole session
          (compact.ended with a replacement message list) — callers
          must then fall back to a full flush.
        """
        touched: Optional[Set[str]] = set() if collect_touched else None
        cached = self._cache.get(session_id)
        if cached is not None:
            events = self.load_events(session_id, after_seq=cached.last_seq)
            if touched is not None:
                for event in events:
                    self._collect_touched(event, touched)
            for event in events:
                self._apply(event, cached)
            if events:
                cached.last_seq = max(e.seq for e in events)
            return cached, touched

        # Cache miss — full rebuild. touched=None so the caller falls
        # back to a full flush (all rows written, stale rows deleted).
        state = self.project(session_id)
        self._cache[session_id] = state
        return state, None


    def invalidate(self, session_id: str) -> None:
        """Drop the cached projection for a session (session deleted).

        The next project_incremental() for this session does a full
        rebuild — correct because event_log is the source of truth.
        """
        self._cache.pop(session_id, None)


    def _collect_touched(self, event: EventV2, touched: Set[str]) -> None:
        """Record which message ids an event modifies (for delta flush).

        Every handler resolves its target message via
        ``data["message_id"]`` (or ``user_message_id`` for
        message_received), so those keys enumerate the touched set.
        Compact events with a full replacement list rewrite every
        message — signalled with the ``"*"`` sentinel.
        """
        data = event.data
        for key in ("message_id", "user_message_id"):
            mid = data.get(key)
            if mid:
                touched.add(mid)
        if event.type in (EventType.COMPACT, EventType.COMPACT_ENDED):
            if isinstance(data.get("messages"), list):
                touched.add("*")
            else:
                # Simple marker mode (L4 auto-compaction without
                # replacement list): explicitly record the marker id
                # so the delta flush actually writes it to the
                # messages table.  Without this, the marker is
                # created in the in-memory state but never persisted
                # because ``touched`` stays empty.
                marker_id = f"compact-{event.id[:8]}"
                touched.add(marker_id)


    def load_events(
        self,
        session_id: str,
        after_seq: int = 0,
    ) -> List[EventV2]:
        """Read events from event_log for a session."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT id, aggregate_id, seq, type, data_json, time_created "
                "FROM event_log WHERE aggregate_id = ? AND seq > ? "
                "ORDER BY seq ASC",
                (session_id, after_seq),
            ).fetchall()
        finally:
            conn.close()
        return [EventV2.from_row(r) for r in rows]


    def project(
        self,
        session_id: str,
        after_seq: int = 0,
    ) -> ProjectedSession:
        """Build a ProjectedSession from event_log.

        Returns a fresh ProjectedSession each call. Multiple calls
        with the same args produce identical state (pure function).

        P0-1 B2: when a snapshot with ``seq >= after_seq`` exists, the
        snapshot seeds the state and only events after the snapshot's
        seq are replayed. Falls back to a full replay when no snapshot
        is available — same behaviour as before B2.
        """
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            snap = self._load_snapshot(conn, session_id, after_seq)
        finally:
            conn.close()
        if snap is not None:
            state = snap
            after_seq = snap.last_seq
        else:
            state = ProjectedSession(session_id=session_id)
        events = self.load_events(session_id, after_seq=after_seq)
        for event in events:
            self._apply(event, state)
        state.last_seq = max(
            (e.seq for e in events),
            default=after_seq,
        )
        return state


    def _apply(self, event: EventV2, state: ProjectedSession) -> None:
        """Dispatch one event to its handler.

        Delegates to projection_handlers.apply_event. Kept under
        this name: project_incremental and the projector tests call
        ``_apply`` directly.
        """
        from .projection_handlers import apply_event

        apply_event(event, state)


    def apply(self, event: EventV2, state: ProjectedSession) -> None:
        """Apply a single event to an existing ProjectedSession.

        Used by:
        - project() above to build state from a full event stream
        - Tests to verify individual event handling
        - Future: live projector that maintains state in memory
        """
        self._apply(event, state)

    # ── Flush (B2) ──────────────────────────────────────────────


    def project_and_flush(self, session_id: str) -> ProjectedSession:
        """Convenience: project + flush in one call."""
        state = self.project(session_id)
        self.flush(state)
        return state


    def project_to_messages(
        self,
        session_id: str,
        limit: int = 100,
    ) -> List[Any]:
        """Project event_log → list[Message] (B3 primary read path).

        Drop-in replacement for SessionStore.get_messages(). Returns
        the same Message objects with parts in metadata["_parts"].

        Args:
            session_id: Session to project.
            limit: Max messages to return (first N by seq, matching
                SessionStore.get_messages behavior).

        Returns:
            List of Message in chronological order (by seq).
        """
        state = self.project(session_id)
        messages = state.to_messages()
        if limit and len(messages) > limit:
            # First N by seq, matching SessionStore.get_messages() /
            # the DB read path (ORDER BY seq ASC LIMIT N).
            messages = messages[:limit]
        return messages

    # ── Event handlers ──────────────────────────────────────────

