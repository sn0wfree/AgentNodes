"""TraceProjection — derive ``GET /session/{id}/trace`` from the event_log.

Phase A2: the event_log is the single source of truth for LLM request
tracing. Previously the trace endpoint read ``trace.jsonl`` via
TraceWriter; now it projects ``llm_request`` events out of the event_log,
reconstructing large offloaded fields (system_prompt, tools_schema) from
their sidecar blobs — the same blobs ``_LoopEventForwarder._offload_llm_request``
writes under ``<event-db>/trace-blobs/``.

trace.jsonl remains only as a backward-compat fallback for sessions that
predate A1 (no ``llm_request`` events in the event_log).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from ...core.agent.event_store import EventStore
from strategy_research.kernel.events.event_v2 import EventType

logger = logging.getLogger(__name__)


class TraceProjection:
    """Project trajectory trace records out of a session's event_log.

    Reads the event_log (append-ordered by seq) and returns the trace
    records, resolving any offloaded large fields back from their sidecar
    blobs so the caller gets the full request envelope.
    """

    # Sidecar subdir (relative to the event DB) where offloaded blobs live.
    _BLOB_SUBDIR = "trace-blobs"

    # Summary-level events that make up the Trajectory View. Deliberately
    # excludes the high-frequency text/thinking deltas so the timeline stays
    # readable. Used as the default when no ``types`` filter is supplied.
    DEFAULT_TRACE_TYPES = frozenset({
        EventType.LLM_REQUEST,
        EventType.LLM_RESPONSE,
        EventType.LOOP_START,
        EventType.LOOP_END,
        EventType.LOOP_FINAL,
        EventType.ITER_START,
        EventType.ITER_END,
        EventType.TOOL_CALL,
        EventType.TOOL_RESULT,
        EventType.TOOL_ERROR,
        EventType.COMPRESSION,
        EventType.COMPACT,
        EventType.AGENT_ERROR,
        EventType.TOOL_HEARTBEAT,
        EventType.SESSION_TOTAL_TOKENS,
    })

    def __init__(self, event_store: EventStore) -> None:
        self._event_store = event_store
        self._db_path = getattr(event_store, "_db_path", None)
        self._blob_root: Path | None = None
        if self._db_path is not None:
            self._blob_root = Path(self._db_path).parent / self._BLOB_SUBDIR

    def project(
        self,
        session_id: str,
        *,
        limit: int = 100,
        types: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Return up to ``limit`` trace records for a session.

        Records are ordered by event seq (append order, oldest first) and
        truncated to the last ``limit``. ``types`` is a comma-separated
        allowlist matched against the event type; when omitted, the curated
        ``DEFAULT_TRACE_TYPES`` vocabulary is returned (the Trajectory View).
        """
        type_filter = set(types.split(",")) if types else set(self.DEFAULT_TRACE_TYPES)

        records: list[dict[str, Any]] = []
        # P0-1 B1: push the type allow-list down to SQLite so we only
        # deserialize the events that survive the filter. With ~5% of
        # events matching DEFAULT_TRACE_TYPES, this is a 20x reduction in
        # JSON parsing for a typical session.
        for ev in self._event_store.replay(
            session_id,
            types=list(type_filter) if type_filter else None,
        ):
            record = self._resolve_offloads(dict(ev.data))
            record.setdefault("type", ev.type)
            record.setdefault("seq", ev.seq)
            record.setdefault("time_created", ev.time_created)
            records.append(record)
        return records[-limit:]

    def _resolve_offloads(self, data: dict[str, Any]) -> dict[str, Any]:
        """Reconstruct offloaded fields from their sidecar blobs in place.

        For every ``{field}_path`` reference, read the blob back into
        ``data[field]`` (skipping missing/unreadable blobs). A plain
        filename is expected; the reference is forced to a bare name so a
        hostile path cannot escape the blob root.
        """
        for key in list(data.keys()):
            if not key.endswith("_path") or not isinstance(data[key], str):
                continue
            field = key[:-5]
            if field in data:
                continue
            if self._blob_root is None:
                continue
            rel = data[key]
            if rel.startswith(self._BLOB_SUBDIR + "/"):
                rel = rel[len(self._BLOB_SUBDIR) + 1:]
            blob = self._blob_root / Path(rel).name
            if not blob.exists():
                continue
            try:
                data[field] = blob.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                data.pop(field, None)
        return data


__all__ = ["TraceProjection"]
