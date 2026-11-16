"""Event -> ProjectedSession handlers (pure updates, zero IO).

Extracted from projector.py in the Phase 2 split
(docs/design/phase2-web-session-projector-design.md). Each handler mutates
the given ``ProjectedSession`` in place; the dispatcher
``apply_event`` routes by ``EventType`` via ``HANDLERS``.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Callable, Dict, Optional

from agentnodes.kernel.events.event_v2 import EventType, EventV2, is_known_event_type
from .projection_models import ProjectedMessage, ProjectedPart, ProjectedSession

logger = logging.getLogger(__name__)


_GOAL_CHANGE_LABELS = {
    "create": "创建目标",
    "evidence": "添加证据",
    "complete": "完成目标",
}


def _format_goal_content(data: dict[str, Any]) -> str:
    """Build the LLM-facing content for a goal message.

    Compact, self-explaining, prefixed so the model never mistakes it
    for user input. The FULL snapshot lives in the message metadata;
    this text only needs to keep the agent oriented (progress,
    criteria status, latest evidence).
    """
    change = data.get("change_type", "")
    label = _GOAL_CHANGE_LABELS.get(change, "目标更新")
    objective = (data.get("objective") or "").strip()
    status = data.get("goal_status") or "active"
    progress = data.get("progress_percent") or 0
    evidence_llm = (data.get("evidence_text_llm") or "").strip()

    criteria = data.get("criteria") or []
    criteria_brief = ", ".join(
        f"{c.get('text', '')[:20]}({c.get('status', 'pending')})"
        for c in criteria
        if isinstance(c, dict)
    ) or "无"

    lines = [
        "[目标状态] 系统生成的目标快照（非用户输入，供你跟踪研究目标进度）。",
        f"变更: {label}",
        f"目标: {objective!r} | 状态: {status} | 进度: {progress}%",
        f"完成标准: [{criteria_brief}]",
    ]
    if evidence_llm:
        lines.append(f"最新证据: {evidence_llm!r}")
    return "\n".join(lines)


# ── Dispatcher ──────────────────────────────────────────


def apply_event(event: EventV2, state: ProjectedSession) -> None:
    if event.aggregate_id != state.session_id:
        logger.warning(
            "Projector: event aggregate_id %s != state session_id %s",
            event.aggregate_id, state.session_id,
        )
        return
    handler = HANDLERS.get(event.type)
    if handler is None:
        if not is_known_event_type(event.type):
            logger.debug(
                "Projector: skipping unknown event type %r", event.type
            )
        else:
            # Known type but no handler — not yet implemented
            logger.debug(
                "Projector: no handler for known type %r (B1 scope)",
                event.type,
            )
        return
    try:
        handler(event, state)
    except Exception as exc:
        logger.exception(
            "Projector: handler for %s raised: %s", event.type, exc,
        )

def _on_message_received(
 event: EventV2, state: ProjectedSession,
    ) -> None:
    """Handle user message: INSERT messages row (role=user)."""
    data = event.data
    message_id = data.get("message_id") or data.get("user_message_id")
    if not message_id:
        logger.warning(
            "Projector: message_received without message_id (seq=%s)",
            event.seq,
        )
        return
    if message_id in state.messages:
        # Duplicate — first event wins
        return
    msg_seq = len(state.messages) + 1
    # B4 D7: MemoryManager.append() takes a `metadata` kwarg and publishes it
    # on the event; without this merge it was silently dropped, so the public
    # signature promised something the storage layer discarded.
    metadata: Dict[str, Any] = dict(data.get("metadata") or {})
    state.messages[message_id] = ProjectedMessage(
        id=message_id,
        session_id=event.aggregate_id,
        role="user",
        content=data.get("content", ""),
        message_type="user",
        created_at=event.time_created,
        seq=msg_seq,
        metadata=metadata,
    )

def _on_assistant_message(
 event: EventV2, state: ProjectedSession,
    ) -> None:
    """Handle assistant message boundary.

    The assistant_message event is emitted at the END of an
    iteration (loop.py:582) carrying the final accumulated
    content. We treat it as the message-row boundary.

    But: text/tool events are emitted BEFORE this event, so
    the message_id is needed earlier. The event_callback in
    service.py adds attempt.message_id to every event's
    data, so we use that for the message_id here.
    """
    message_id = event.data.get("message_id")
    if not message_id:
        logger.warning(
            "Projector: assistant_message without message_id (seq=%s)",
            event.seq,
        )
        return
    msg_type = event.data.get("message_type", "assistant")
    if message_id in state.messages:
        # Update content and type with the final summary
        state.messages[message_id].content = event.data.get("content", "")
        state.messages[message_id].message_type = msg_type
        # Preserve agent_id if present in event and not yet set
        if "agent_id" in event.data and "agent_id" not in state.messages[message_id].metadata:
            state.messages[message_id].metadata["agent_id"] = event.data["agent_id"]
        return
    msg_seq = len(state.messages) + 1
    # B4 D7: merge caller metadata (same note as _on_message_received).
    # agent_id is applied after, so a structural id cannot be shadowed by a
    # caller-supplied duplicate.
    metadata: Dict[str, Any] = dict(event.data.get("metadata") or {})
    # Propagate agent_id from event data
    if "agent_id" in event.data:
        metadata["agent_id"] = event.data["agent_id"]
    state.messages[message_id] = ProjectedMessage(
        id=message_id,
        session_id=event.aggregate_id,
        role="assistant",
        content=event.data.get("content", ""),
        message_type=msg_type,
        created_at=event.time_created,
        seq=msg_seq,
        metadata=metadata,
        attempt_id=event.data.get("attempt_id"),
    )

def _ensure_assistant_message(
 event: EventV2, state: ProjectedSession,
    ) -> Optional[ProjectedMessage]:
    """Lazy-create an assistant message on first text/tool event.

    The assistant_message event may not have arrived yet (it
    comes at the end of the iteration), but text.started /
    tool.call events need a parent message_id. If the message
    doesn't exist, create it with empty content and let the
    final assistant_message event update the content.
    """
    message_id = event.data.get("message_id")
    if not message_id:
        return None
    if message_id in state.messages:
        msg = state.messages[message_id]
        # Propagate agent_id if present in event and not yet set
        if "agent_id" in event.data and "agent_id" not in msg.metadata:
            msg.metadata["agent_id"] = event.data["agent_id"]
        return msg
    # Lazy-create
    msg_seq = len(state.messages) + 1
    # B4 D7: merge caller metadata (same note as _on_message_received).
    # agent_id is applied after, so a structural id cannot be shadowed by a
    # caller-supplied duplicate.
    metadata: Dict[str, Any] = dict(event.data.get("metadata") or {})
    # Propagate agent_id from event data
    if "agent_id" in event.data:
        metadata["agent_id"] = event.data["agent_id"]
    msg = ProjectedMessage(
        id=message_id,
        session_id=event.aggregate_id,
        role="assistant",
        content="",
        message_type="assistant",
        created_at=event.time_created,
        seq=msg_seq,
        metadata=metadata,
        attempt_id=event.data.get("attempt_id"),
    )
    state.messages[message_id] = msg
    return msg

def _on_text_started(
 event: EventV2, state: ProjectedSession,
    ) -> None:
    msg = _ensure_assistant_message(event, state)
    if msg is None:
        return
    text_id = event.data.get("text_id") or str(uuid.uuid4())
    if text_id in msg.parts:
        return
    msg.parts[text_id] = ProjectedPart(
        id=text_id,
        type="text",
        data={"type": "text", "id": text_id, "text": ""},
        seq=len(msg.parts),
        time_created=event.time_created,
    )

def _on_text_delta(
 event: EventV2, state: ProjectedSession,
    ) -> None:
    msg = _ensure_assistant_message(event, state)
    if msg is None:
        return
    text_id = event.data.get("text_id")
    if not text_id or text_id not in msg.parts:
        # text.started might have been missed; create lazily
        text_id = text_id or str(uuid.uuid4())
        msg.parts[text_id] = ProjectedPart(
            id=text_id,
            type="text",
            data={"type": "text", "id": text_id, "text": ""},
            seq=len(msg.parts),
            time_created=event.time_created,
        )
    part = msg.parts[text_id]
    part.data["text"] = part.data.get("text", "") + event.data.get("text", "")

def _on_text_ended(
 event: EventV2, state: ProjectedSession,
    ) -> None:
    msg = _ensure_assistant_message(event, state)
    if msg is None:
        return
    text_id = event.data.get("text_id")
    if not text_id or text_id not in msg.parts:
        return
    part = msg.parts[text_id]
    # text.ended carries the final text; prefer it if present
    if "text" in event.data:
        part.data["text"] = event.data["text"]

def _on_tool_call(
 event: EventV2, state: ProjectedSession,
    ) -> None:
    msg = _ensure_assistant_message(event, state)
    if msg is None:
        return
    tc_id = event.data.get("id") or event.data.get("call_id")
    if not tc_id:
        logger.warning(
            "Projector: tool_call without id (seq=%s)", event.seq,
        )
        return
    if tc_id in msg.parts:
        return
    # Build tool_call data in the frontend ToolCallPart shape
    # (name/arguments/status) AND keep backend-compatible fields
    # (tool/input/state) so _convert_messages_to_history still works.
    #
    # Two input event shapes are supported:
    # 1. Flat: {tool, name, id, arguments} — loop._emit("tool_call") shape
    # 2. Nested: {function: {name, arguments}} — LLM API style
    #
    # Frontend ToolCallPart reads: name, arguments, status, result, progress.
    # If these are missing on DB reload, ToolCallBlock shows blank icons
    # and undefined status → "tool call 标识消失". So we MUST populate them.
    data: Dict[str, Any] = {
        "type": "tool_call",
        "id": tc_id,
        "state": "call",
        "status": "running",
    }
    # Tool name resolution: prefer flat `name` (loop.py emits both
    # `tool` and `name`), fall back to `tool`, then function.name.
    function = event.data.get("function")
    if isinstance(function, dict):
        data["function"] = function
        data["tool"] = function.get("name", "")
        data["name"] = event.data.get("name") or data["tool"]
    else:
        data["tool"] = event.data.get("tool", "")
        data["name"] = event.data.get("name") or data["tool"]
    # Arguments resolution: prefer flat `arguments` (loop.py shape),
    # fall back to `input`, then function.arguments.
    if "arguments" in event.data:
        data["arguments"] = event.data["arguments"]
        data["input"] = event.data["arguments"]
    elif "input" in event.data:
        data["arguments"] = event.data["input"]
        data["input"] = event.data["input"]
    elif isinstance(function, dict) and "arguments" in function:
        data["arguments"] = function["arguments"]
        data["input"] = function["arguments"]

    msg.parts[tc_id] = ProjectedPart(
        id=tc_id,
        type="tool_call",
        data=data,
        seq=len(msg.parts),
        time_created=event.time_created,
    )

def _on_tool_result(
 event: EventV2, state: ProjectedSession,
    ) -> None:
    """Update the tool_call part with the result.

    This is the opencode pattern: tool_result is a STATE
    UPDATE to an existing tool_call part, not a new part.
    The part's data.status becomes "done" and data.result
    is filled in.
    """
    msg = _ensure_assistant_message(event, state)
    if msg is None:
        return
    tc_id = event.data.get("id") or event.data.get("call_id")
    if not tc_id:
        logger.warning(
            "Projector: tool_result without id (seq=%s)", event.seq,
        )
        return
    part = msg.parts.get(tc_id)
    if part is None:
        # Tool result arrived before tool_call (shouldn't happen,
        # but be defensive). Create the part now.
        part = ProjectedPart(
            id=tc_id,
            type="tool_call",
            data={"type": "tool_call", "id": tc_id},
            seq=len(msg.parts),
            time_created=event.time_created,
        )
        msg.parts[tc_id] = part
    part.data["result"] = event.data.get("result", event.data.get("preview", ""))
    part.data["status"] = event.data.get("status", "done")
    part.data["state"] = "done"
    # Ensure name/arguments survive (defensive for tool_result-first cases)
    if "name" not in part.data:
        part.data["name"] = event.data.get("name") or event.data.get("tool", "")
    if "arguments" not in part.data:
        part.data["arguments"] = event.data.get("arguments") or event.data.get("input", "")

def _on_tool_progress(
 event: EventV2, state: ProjectedSession,
    ) -> None:
    """Tool progress is metadata on the tool_call part."""
    msg = _ensure_assistant_message(event, state)
    if msg is None:
        return
    tc_id = event.data.get("id") or event.data.get("call_id")
    if not tc_id or tc_id not in msg.parts:
        return
    part = msg.parts[tc_id]
    progress = part.data.get("progress", [])
    progress.append({
        "stage": event.data.get("stage", ""),
        "current": event.data.get("current"),
        "total": event.data.get("total"),
        "message": event.data.get("message", ""),
        "time": event.time_created,
    })
    part.data["progress"] = progress

# ── Thinking blocks (B7-fix: was lambda: None) ──────────────

def _on_thinking_start(
 event: EventV2, state: ProjectedSession,
    ) -> None:
    """Open a new thinking block.

    The backend's ``thinking_start`` event has no ``thinking_id``
    field (unlike ``text_id``) — each block is scoped to the
    current message. We derive a stable part_id from
    ``event.seq`` so concurrent thinking blocks in the same
    message don't collide (verified: a single message can have
    3+ thinking blocks when the LLM alternates between thinking
    and tool calls). The part_id is recorded in
    ``ProjectedMessage.open_thinking_part_id`` so subsequent
    ``thinking_delta`` and ``thinking_end`` events know which
    part to append to / close.
    """
    msg = _ensure_assistant_message(event, state)
    if msg is None:
        return
    part_id = f"think_{event.seq}"
    if part_id in msg.parts:
        # Idempotent on replay: re-emission of the same start
        # event must not create a duplicate part. The existing
        # part's open state is restored so subsequent deltas
        # still append to the right block.
        msg.open_thinking_part_id = part_id
        return
    msg.parts[part_id] = ProjectedPart(
        id=part_id,
        type="thinking",
        data={"type": "thinking", "text": "", "collapsed": True},
        seq=len(msg.parts),
        time_created=event.time_created,
    )
    msg.open_thinking_part_id = part_id

def _on_thinking_delta(
 event: EventV2, state: ProjectedSession,
    ) -> None:
    """Append the delta to the currently-open thinking block.

    If no thinking block is open (the matching ``thinking_start``
    was missed — e.g. event loss during reconnect / replay), we
    lazy-create one keyed by ``event.seq`` so the text is not
    lost. Mirrors the same fallback in ``_on_text_delta``.
    """
    msg = _ensure_assistant_message(event, state)
    if msg is None:
        return
    part_id = msg.open_thinking_part_id
    if part_id is None or part_id not in msg.parts:
        # Lazy create — fall back to the seq-keyed id. This
        # creates a separate part from the one the missing start
        # would have opened. We set the new part as the open one
        # so subsequent deltas and the eventual end land in the
        # same part.
        part_id = f"think_{event.seq}"
        msg.parts[part_id] = ProjectedPart(
            id=part_id,
            type="thinking",
            data={"type": "thinking", "text": "", "collapsed": True},
            seq=len(msg.parts),
            time_created=event.time_created,
        )
        msg.open_thinking_part_id = part_id
    part = msg.parts[part_id]
    part.data["text"] = part.data.get("text", "") + event.data.get("delta", "")

def _on_thinking_done(
 event: EventV2, state: ProjectedSession,
    ) -> None:
    """No-op: ``thinking_done`` is informational, the part is
    already accumulated by ``thinking_delta``. We leave collapse
    state as-is so the frontend can decide based on its own
    streaming flags. The part remains "open" (in case more
    deltas arrive) until ``thinking_end`` is seen.
    """
    return

def _on_thinking_end(
 event: EventV2, state: ProjectedSession,
    ) -> None:
    """Close the currently-open thinking block.

    The thinking part is now sealed — subsequent ``thinking_delta``
    events (which shouldn't happen, but might on a replay
    boundary) will lazy-create a new part. The persisted part
    keeps ``collapsed: True`` so DB reloads render folded.
    """
    msg = _ensure_assistant_message(event, state)
    if msg is None:
        return
    # Only clear the open pointer if it's still set; a second
    # ``thinking_end`` (replay) is then a true no-op.
    if msg.open_thinking_part_id is not None:
        msg.open_thinking_part_id = None

# ── Future-proof part handlers (B7-2: defense-in-depth) ──────
#
# The backend's AgentLoop does NOT currently emit file_edit /
# table / chart / image events (see docs/todo or
# webui/frontend/src/hooks/sse/types.ts:43-49 for the full
# design intent). Wiring them here is *insurance* — if a future
# contributor adds ``emit("file_edit", ...)`` to loop.py, the
# projector will already know how to persist the part instead of
# silently dropping it like the original thinking bug.

def _on_file_edit(
 event: EventV2, state: ProjectedSession,
    ) -> None:
    msg = _ensure_assistant_message(event, state)
    if msg is None:
        return
    part_id = event.data.get("id") or f"file_edit_{event.seq}"
    if part_id in msg.parts:
        return
    msg.parts[part_id] = ProjectedPart(
        id=part_id,
        type="file_edit",
        data={
            "type": "file_edit",
            "file_path": event.data.get("file_path", ""),
            "old_content": event.data.get("old_content", ""),
            "new_content": event.data.get("new_content", ""),
        },
        seq=len(msg.parts),
        time_created=event.time_created,
    )

def _on_table(
 event: EventV2, state: ProjectedSession,
    ) -> None:
    msg = _ensure_assistant_message(event, state)
    if msg is None:
        return
    part_id = event.data.get("id") or f"table_{event.seq}"
    if part_id in msg.parts:
        return
    msg.parts[part_id] = ProjectedPart(
        id=part_id,
        type="table",
        data={
            "type": "table",
            "headers": event.data.get("headers", []),
            "rows": event.data.get("rows", []),
            "caption": event.data.get("caption"),
        },
        seq=len(msg.parts),
        time_created=event.time_created,
    )

def _on_chart(
 event: EventV2, state: ProjectedSession,
    ) -> None:
    msg = _ensure_assistant_message(event, state)
    if msg is None:
        return
    part_id = event.data.get("id") or f"chart_{event.seq}"
    if part_id in msg.parts:
        return
    msg.parts[part_id] = ProjectedPart(
        id=part_id,
        type="chart",
        data={
            "type": "chart",
            "chart_type": event.data.get("chart_type", "bar"),
            "data": event.data.get("data", []),
            "title": event.data.get("title"),
        },
        seq=len(msg.parts),
        time_created=event.time_created,
    )

def _on_image(
 event: EventV2, state: ProjectedSession,
    ) -> None:
    msg = _ensure_assistant_message(event, state)
    if msg is None:
        return
    part_id = event.data.get("id") or f"image_{event.seq}"
    if part_id in msg.parts:
        return
    msg.parts[part_id] = ProjectedPart(
        id=part_id,
        type="image",
        data={
            "type": "image",
            "url": event.data.get("url", ""),
            "alt": event.data.get("alt"),
        },
        seq=len(msg.parts),
        time_created=event.time_created,
    )

def _on_html(
 event: EventV2, state: ProjectedSession,
    ) -> None:
    """Persist an html part (agent-driven report renderable).

    show_report emits an ``html`` event carrying self-contained
    HTML content; the frontend renders it in a sandboxed iframe
    (chat stream + right panel). Persisting the content here means
    reloads keep the report without depending on the file.
    """
    msg = _ensure_assistant_message(event, state)
    if msg is None:
        return
    part_id = event.data.get("id") or f"html_{event.seq}"
    if part_id in msg.parts:
        return
    msg.parts[part_id] = ProjectedPart(
        id=part_id,
        type="html",
        data={
            "type": "html",
            "title": event.data.get("title"),
            "content": event.data.get("content", ""),
        },
        seq=len(msg.parts),
        time_created=event.time_created,
    )

def _on_compact(
 event: EventV2, state: ProjectedSession,
    ) -> None:
    """Handle compaction event — add a compaction marker message.

    Compaction messages are system-level messages that mark where
    the history was compressed. They have role='system' and
    message_type='compaction'. The content is the summary.

    opencode-aligned behavior (compaction-history-filter.md):
    - ALL original messages are KEPT in the projection (chat
      record preserved for UI / future review).
    - The marker records `compacted_until_seq` in its metadata:
      every message with seq <= that value was covered by this
      compaction and must be hidden from the LLM context (but
      kept in the DB).
    - Only the most recent compaction marker is used when building
      LLM context (see SessionService._convert_messages_to_history).
    """
    data = event.data
    summary = data.get("summary") or data.get("content") or ""
    msg_id = f"compact-{event.id[:8]}"

    # Compute the compaction boundary: the seq of the last message
    # covered by this compaction. Prefer an explicit value from the
    # event; otherwise infer by matching the compressed `recent`
    # messages against the projection by (role, content).
    compacted_until_seq = data.get("compacted_until_seq")
    if compacted_until_seq is None:
        compressed_msgs = data.get("messages")
        if compressed_msgs and isinstance(compressed_msgs, list):
            ordered = state.messages_in_order()
            # This marker's position (if a prior partial replay
            # added it) — messages after it are post-compaction.
            marker_idx = next(
                (i for i, m in enumerate(ordered)
                 if m.id == msg_id),
                len(ordered),
            )
            recent_msgs = [
                m for m in compressed_msgs
                if m.get("role") != "system"
            ]
            if recent_msgs:
                # Match by (role, content): the compressed recent
                # dicts are the same messages that exist in the
                # projection. The OLDEST matching message is the
                # first recent message; everything before it was
                # compacted.
                recent_keys = {
                    (m.get("role"), m.get("content", ""))
                    for m in recent_msgs
                }
                recent_indices = [
                    idx for idx in range(marker_idx)
                    if (ordered[idx].role, ordered[idx].content)
                    in recent_keys
                ]
                if recent_indices:
                    oldest = min(recent_indices)
                    compacted_until_seq = ordered[oldest].seq - 1
                else:
                    compacted_until_seq = 0
            else:
                compacted_until_seq = 0
        else:
            compacted_until_seq = 0

    marker = ProjectedMessage(
        id=msg_id,
        session_id=event.aggregate_id,
        role="system",
        content=summary,
        message_type="compaction",
        created_at=event.time_created,
        seq=len(state.messages) + 1,
        metadata={
            "compacted_until_seq": compacted_until_seq,
            "compaction_id": data.get("compaction_id", ""),
        },
    )

    # If a marker with this id already exists (replayed event),
    # update its summary/metadata instead of duplicating.
    existing = state.messages.get(msg_id)
    if existing is not None:
        existing.content = summary
        existing.metadata = marker.metadata
        return

    state.messages[msg_id] = marker

def _on_goal_updated(
 event: EventV2, state: ProjectedSession,
    ) -> None:
    """Handle goal_updated event — add a goal state message.

    The event carries a FULL snapshot (built by
    core/goal/events.build_goal_updated_payload). We persist one
    self-contained ``message_type='goal'`` message per event:
    - role=system + compact content → enters the LLM context so
      the agent tracks goal evolution (docs/archive/undated/goal-events-panel-link.md)
    - metadata holds the structured snapshot → UI rendering + audit
    - id is derived from the event id → replay is idempotent
      (updates in place, never duplicates)
    """
    data = event.data or {}
    if not data.get("goal_id"):
        return
    # Prefer the stable id produced by build_goal_updated_payload
    # (shared with the frontend SSE handler, so live additions and
    # DB reloads use the same key → Map.set / INSERT OR REPLACE
    # are naturally idempotent); fall back to the event id.
    msg_id = data.get("message_id") or f"goal-{event.id[:8]}"

    content = _format_goal_content(data)
    message = ProjectedMessage(
        id=msg_id,
        session_id=event.aggregate_id,
        role="system",
        content=content,
        message_type="goal",
        created_at=event.time_created,
        seq=len(state.messages) + 1,
        metadata=dict(data),
    )

    # Idempotent on replay: refresh content/metadata instead of
    # duplicating the message.
    existing = state.messages.get(msg_id)
    if existing is not None:
        existing.content = content
        existing.metadata = dict(data)
        return

    state.messages[msg_id] = message


HANDLERS: Dict[str, Callable[[EventV2, ProjectedSession], None]] = {

            EventType.MESSAGE_RECEIVED: _on_message_received,
            EventType.ASSISTANT_MESSAGE: _on_assistant_message,
            EventType.TEXT_STARTED: _on_text_started,
            EventType.TEXT_DELTA: _on_text_delta,
            EventType.TEXT_ENDED: _on_text_ended,
            EventType.TOOL_CALL: _on_tool_call,
            EventType.TOOL_RESULT: _on_tool_result,
            EventType.TOOL_PROGRESS: _on_tool_progress,
            # Thinking blocks: each ``thinking_start`` opens a new
            # ProjectedPart (id = ``think_{event.seq}`` so concurrent
            # thinking blocks in the same message don't collide),
            # ``thinking_delta`` appends text, ``thinking_done`` /
            # ``thinking_end`` are no-ops (the part is sealed by
            # ``agent_done`` / DB reload; the frontend folds via
            # ``collapsed`` on the part itself).
            EventType.THINKING_START: _on_thinking_start,
            EventType.THINKING_DELTA: _on_thinking_delta,
            EventType.THINKING_DONE: _on_thinking_done,
            EventType.THINKING_END: _on_thinking_end,
            # Future-proof handlers for structured part events the
            # backend doesn't emit yet (the only consumer today is
            # tool_call). Wiring them up now means the next time a
            # contributor adds ``emit("file_edit", ...)`` or similar
            # to the AgentLoop, the projector already knows how to
            # persist the part — no silent B1-style ``lambda: None``
            # bug.
            EventType.FILE_EDIT: _on_file_edit,
            EventType.TABLE: _on_table,
            EventType.CHART: _on_chart,
            EventType.IMAGE: _on_image,
            EventType.HTML: _on_html,
            # Compaction events create a compaction message (system)
            EventType.COMPACT: _on_compact,
            EventType.COMPACT_ENDED: _on_compact,
            # Goal state events create a goal message (system). The
            # event carries a FULL snapshot (see core/goal/events.py)
            # so each goal message is self-contained: it enters the
            # LLM context (role=system, compact content) and drives
            # the UI (metadata holds the structured snapshot).
            EventType.GOAL_UPDATED: _on_goal_updated,
}
