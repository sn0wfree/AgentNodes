"""EventV2 — typed event envelope for the event_log table (Level 3, B1 commit 2).

P0-1 A2 (2026-08-15): moved from ``strategy_research.api.session.event_v2``
to ``strategy_research.core.events.event_v2`` so ``core/agent/event_store``
can depend on it without inverting the core/api layering. The public surface
(EventV2, EventType, is_known_event_type) is unchanged; only the import
path differs. No shim is kept — update ``from .event_v2 import ...`` →
``from ...core.events.event_v2 import ...``.

This module defines the data shape that EventBusV2 publishes and the
projector consumes. It is intentionally thin: just a dataclass, an
event-type registry, and JSON serialization helpers. No I/O.

Opencode's event model (packages/core/src/session/event.ts:638) is the
reference: events are flat discriminated unions keyed by ``type`` (a
dot-namespaced string like ``"text.started"``). The data payload is
freeform — whatever the producer wants the consumer to know.

Why strings instead of an enum:
- The event vocabulary evolves over time. New event types shouldn't
  require a code change to deserialize.
- A typo in an event type must FAIL LOUDLY (KeyError at projector
  dispatch), not silently. We achieve this with an EventType registry
  that supports validation.

Usage:
    event = EventV2.create(
        aggregate_id=session_id,
        seq=next_seq,
        type=EventType.TEXT_STARTED,
        data={"text_id": "abc-123"},
    )
    event_bus_v2.publish(event)
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set


def _protocol_version() -> str:
    """当前协议版本（延迟导入：contracts 依赖本模块的事件形状）。

    导入失败时回落到 "1"——事件写入不应因为版本常量不可用而失败；
    读取侧（EventStore._replay / PlatformStore.replay）仍有强制校验。
    """
    try:
        from strategy_research.core.orchestration.contracts import (
            PROTOCOL_VERSION,
        )

        return PROTOCOL_VERSION
    except Exception:  # noqa: BLE001 —— 降级不阻断写入
        return "1"

# ── Event type registry ─────────────────────────────────────────────
#
# Centralized list of all event types in the system. New event types
# MUST be added here AND to the projector (projector.py). The
# projection contract:
#
#   1. EventBusV2 publishes events to event_log (persisted)
#   2. Projector reads events and updates messages + message_parts
#   3. Frontend SSE handler reads from EventBus (live stream)
#
# If a producer publishes an event type not in this registry, the
# projector will warn and skip (forward-compatible). If a consumer
# references a type not here, the producer code is out of sync.

class EventType:
    """Event type constants (dot-namespaced, opencode-style).

    Names match the existing SSE event names emitted by AgentLoop
    and the event_bus.emit() call sites. No renaming.
    """
    # Session lifecycle
    SESSION_CREATED = "session.created"
    SESSION_META_UPDATED = "session_meta_updated"
    ATTEMPT_CREATED = "attempt.created"
    QUEUE_STATE = "queue_state"
    QUEUE_PAUSED = "queue_paused"

    # User message
    MESSAGE_RECEIVED = "message_received"

    # Assistant message lifecycle
    ASSISTANT_MESSAGE = "assistant_message"
    AGENT_DONE = "agent_done"
    AGENT_ERROR = "error"

    # Text part (opencode 3-step protocol)
    TEXT_STARTED = "text.started"
    TEXT_DELTA = "text_delta"
    TEXT_ENDED = "text.ended"

    # Thinking part
    THINKING_START = "thinking_start"
    THINKING_DELTA = "thinking_delta"
    THINKING_DONE = "thinking_done"
    THINKING_END = "thinking_end"

    # Tool part
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TOOL_PROGRESS = "tool_progress"
    TOOL_HEARTBEAT = "tool_heartbeat"
    TOOL_INPUT = "tool.input"

    # Permission gate (Tier 1 A1) — emitted by PermissionGateway when
    # a tool call hits an ASK rule. The frontend answers via
    # POST /api/chat/permission/respond which calls gateway.respond().
    PERMISSION_REQUEST = "permission_request"
    PERMISSION_RESULT = "permission_result"

    # Iteration
    ITER_START = "iter_start"
    ITER_END = "iter_end"

    # AgentLoop lifecycle (Trajectory View). Routed into event_log so the
    # timeline has the full loop vocabulary, not just LLM requests.
    LOOP_START = "loop_start"
    LOOP_END = "loop_end"
    LOOP_FINAL = "loop_final"
    COMPRESSION = "compression"
    TOOL_ERROR = "tool_error"

    # LLM usage
    LLM_USAGE = "llm_usage"
    SESSION_TOTAL_TOKENS = "session_total_tokens"

    # LLM request envelope (DSH request-envelope pattern): recorded once
    # per LLM call. Large fields (system_prompt, tools_schema) are
    # offloaded to sidecar blobs; event_log stores metadata + refs.
    LLM_REQUEST = "llm_request"

    # LLM response envelope (Trajectory View): finish reason, tool-call
    # count, and a short content preview.
    LLM_RESPONSE = "llm_response"

    # Compaction
    COMPACT = "compact"
    COMPACT_STARTED = "compact.started"
    COMPACT_ENDED = "compact.ended"

    # Misc
    FILE_EDIT = "file_edit"
    TABLE = "table"
    CHART = "chart"
    IMAGE = "image"
    HTML = "html"

    # Subagent lifecycle
    SUBAGENT_STARTED = "subagent_started"
    SUBAGENT_TOOL_CALL = "subagent_tool_call"
    SUBAGENT_TOOL_RESULT = "subagent_tool_result"
    SUBAGENT_TEXT_DELTA = "subagent_text_delta"
    SUBAGENT_COMPLETED = "subagent_completed"
    SUBAGENT_FAILED = "subagent_failed"

    # Todo / task tracking (opencode-style todo_write tool)
    TODO_UPDATED = "todo_updated"

    # Goal state (full snapshot; persisted to messages as
    # message_type='goal' — see projector._on_goal_updated)
    GOAL_UPDATED = "goal_updated"

    # Study lifecycle (v2 design §16 — 5 groups)
    STUDY_QUEUED = "study_queued"
    STUDY_STARTED = "study_started"
    STUDY_PAUSED = "study_paused"
    STUDY_RESUMED = "study_resumed"
    STUDY_CANCELLED = "study_cancelled"
    STUDY_EARLY_STOPPED = "study_early_stopped"
    STUDY_COMPLETED = "study_completed"
    STUDY_FAILED = "study_failed"
    STUDY_EXECUTOR_STOPPED = "study_executor_stopped"
    STUDY_INTERRUPTED = "study_interrupted"
    STUDY_ROUND = "study_round"
    STUDY_ROUND_REJECTED = "study_round_rejected"
    STUDY_PHASE = "study_phase"
    STUDY_REVIEW = "study_review"
    STUDY_TODOS_UPDATED = "study_todos_updated"
    STUDY_EVIDENCE = "study_evidence"
    STUDY_PROGRESS = "study_progress"
    STUDY_BUDGET_LIMITED = "study_budget_limited"
    STUDY_MONITORING_STARTED = "study_monitoring_started"
    STUDY_MONITOR_CHECK = "study_monitor_check"
    STUDY_MONITOR_CHECK_FAILED = "study_monitor_check_failed"
    STUDY_DRIFT_DETECTED = "study_drift_detected"
    STUDY_KNOWLEDGE_CHECK = "study_knowledge_check"
    STUDY_KNOWLEDGE_UPDATE = "study_knowledge_update"
    STUDY_KNOWLEDGE_COMPACTED = "study_knowledge_compacted"
    STUDY_DIRECTIVES_CONSUMED = "study_directives_consumed"


# Set of all known event types (for validation)
_ALL_EVENT_TYPES: Set[str] = set()
for _name in dir(EventType):
    if _name.isupper() and not _name.startswith("_"):
        _ALL_EVENT_TYPES.add(getattr(EventType, _name))


def is_known_event_type(event_type: str) -> bool:
    """True if the event type is in the EventType registry.

    Unknown types are forward-compatible at the wire level (the
    projector will warn and skip them), but the producer should
    always use a registered type.
    """
    return event_type in _ALL_EVENT_TYPES


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    """Tolerant key lookup for event_log rows.

    sqlite3.Row supports bracket access but not ``.keys()``; plain dicts
    support both. Forward-compat for rows that lack the P0-1 A3 columns
    falls back to ``default`` so pre-A3 events keep reconstructing cleanly.
    """
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


# ── Event envelope ───────────────────────────────────────────────────

@dataclass
class EventV2:
    """Append-only event envelope (Level 3, B1).

    Attributes:
        id: Stable per-event UUID. Used for last_event_id recovery
            (mirrors the existing EventBus.event_id behavior).
        aggregate_id: The session_id (the aggregate root in DDD terms).
        seq: Per-aggregate monotonic sequence number. UNIQUE
            (aggregate_id, seq) in the event_log table.
        type: Event type string (e.g. "text.started"). See EventType.
        data: Freeform event payload (any JSON-serializable dict).
        parent_event_id: P0-1 A3 — parent event ID for trace-tree
            relationships (iter_start → tool_call → tool_result,
            subagent_started → parent agent event). ``None`` for root
            events. Persisted as a nullable column.
        branch_id: P0-1 A3 — fork branch identifier. ``"main"`` for the
            default branch; populated by EventStore.fork() in a later
            phase. Schema enforces ``NOT NULL DEFAULT 'main'`` so this
            default mirrors the DDL.
        time_created: Wall-clock timestamp (server time.time()).
    """

    id: str
    aggregate_id: str
    seq: int
    type: str
    data: Dict[str, Any]
    parent_event_id: Optional[str] = None
    branch_id: str = "main"
    time_created: float = field(default_factory=time.time)

    @classmethod
    def create(
        cls,
        aggregate_id: str,
        seq: int,
        type: str,
        data: Optional[Dict[str, Any]] = None,
        *,
        parent_event_id: Optional[str] = None,
        branch_id: str = "main",
    ) -> "EventV2":
        """Factory method that assigns id and timestamp.

        Args:
            aggregate_id: Session ID.
            seq: Per-aggregate sequence number (caller-managed).
            type: Event type (should be a constant from EventType).
            data: Optional event payload.
            parent_event_id: Optional parent event ID for trace trees.
            branch_id: Fork branch identifier (``"main"`` by default).

        Returns:
            A new EventV2 instance.
        """
        if not aggregate_id:
            raise ValueError("aggregate_id must be non-empty")
        if seq <= 0:
            raise ValueError(f"seq must be positive, got {seq}")
        if not type:
            raise ValueError("type must be non-empty")
        if not branch_id:
            raise ValueError("branch_id must be non-empty")
        return cls(
            id=str(uuid.uuid4()),
            aggregate_id=aggregate_id,
            seq=seq,
            type=type,
            data=data or {},
            parent_event_id=parent_event_id,
            branch_id=branch_id,
            time_created=time.time(),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dict (for JSON storage / wire format)."""
        return {
            "id": self.id,
            "aggregate_id": self.aggregate_id,
            "seq": self.seq,
            "type": self.type,
            "data": self.data,
            "parent_event_id": self.parent_event_id,
            "branch_id": self.branch_id,
            "time_created": self.time_created,
        }

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EventV2":
        """Deserialize from a plain dict.

        Defensive: missing fields raise ValueError (no silent defaults
        for required fields). Extra fields are ignored (forward-compat).
        The P0-1 A3 fields ``parent_event_id`` and ``branch_id`` are
        optional for backward compatibility with pre-A3 dicts — missing
        values use the same defaults as the dataclass / schema.
        """
        required = ("id", "aggregate_id", "seq", "type", "time_created")
        missing = [f for f in required if f not in d]
        if missing:
            raise ValueError(f"EventV2 missing required fields: {missing}")
        return cls(
            id=d["id"],
            aggregate_id=d["aggregate_id"],
            seq=int(d["seq"]),
            type=d["type"],
            data=d.get("data") or {},
            parent_event_id=d.get("parent_event_id"),
            branch_id=d.get("branch_id") or "main",
            time_created=float(d["time_created"]),
        )

    @classmethod
    def from_json(cls, s: str) -> "EventV2":
        """Deserialize from JSON string."""
        return cls.from_dict(json.loads(s))

    def to_row(self) -> Dict[str, Any]:
        """Convert to event_log INSERT column→value mapping.

        data_json is the JSON-serialized data payload only (not the
        full envelope). The other fields are stored as-is. The
        projector reads data_json and merges it with the envelope
        fields to reconstruct the EventV2.
        """
        return {
            "id": self.id,
            "aggregate_id": self.aggregate_id,
            "seq": self.seq,
            "type": self.type,
            "data_json": json.dumps(self.data, ensure_ascii=False),
            "parent_event_id": self.parent_event_id,
            "branch_id": self.branch_id,
            "time_created": self.time_created,
            # R4 补漏：schema_v 必须显式写入。此前依赖 event_log 的 DDL
            # DEFAULT '1'——那意味着「协议版本唯一来源」实际上不在代码里，
            # 而在建表语句里；改 PROTOCOL_VERSION 也不会影响写入。
            "schema_v": _protocol_version(),
        }

    @classmethod
    def from_row(cls, row: Any) -> "EventV2":
        """Build from an event_log SELECT row.

        Accepts sqlite3.Row, dict, or any object that supports
        bracket access. data_json is parsed and merged into data.
        P0-1 A3 fields use ``.get`` semantics to stay tolerant of
        pre-A3 rows that lack the columns (the schema-level DEFAULT
        ``'main'`` would have applied at INSERT time anyway).
        """
        return cls(
            id=row["id"],
            aggregate_id=row["aggregate_id"],
            seq=row["seq"],
            type=row["type"],
            data=json.loads(row["data_json"]) if isinstance(row["data_json"], str) else row["data_json"],
            parent_event_id=_row_get(row, "parent_event_id"),
            branch_id=_row_get(row, "branch_id") or "main",
            time_created=row["time_created"],
        )

    def is_message_lifecycle(self) -> bool:
        """True if this event marks a message-row boundary.

        Used by the projector to decide when to INSERT a new messages
        row (vs UPDATE an existing one). Specifically:
        - message_received → INSERT user message row
        - assistant_message → INSERT assistant message row
        """
        return self.type in (
            EventType.MESSAGE_RECEIVED,
            EventType.ASSISTANT_MESSAGE,
        )

    def is_text_event(self) -> bool:
        """True if this event is a text-part event (started/delta/ended)."""
        return self.type in (
            EventType.TEXT_STARTED,
            EventType.TEXT_DELTA,
            EventType.TEXT_ENDED,
        )

    def is_tool_event(self) -> bool:
        """True if this event is a tool-part event (call/result/progress)."""
        return self.type in (
            EventType.TOOL_CALL,
            EventType.TOOL_RESULT,
            EventType.TOOL_PROGRESS,
        )

    def is_thinking_event(self) -> bool:
        """True if this event is a thinking-part event."""
        return self.type in (
            EventType.THINKING_START,
            EventType.THINKING_DELTA,
            EventType.THINKING_DONE,
            EventType.THINKING_END,
        )


__all__ = ["EventV2", "EventType", "is_known_event_type"]
