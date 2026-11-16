"""W-split: message to LLM history conversion (from SessionService)."""

from __future__ import annotations

import dataclasses
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

MAX_HISTORY_CHARS = 12000


@dataclasses.dataclass
class _HistoryConvertContext:
    """Shared state for the history conversion second pass."""
    messages: list
    hidden_until_seq: int
    keep_compaction_indices: set[int]
    tool_to_assistant_idx: dict[str, int]
    tc_to_tool_idx: dict[str, int]


def _find_compaction_indices(messages: list) -> list[int]:
    """Locate all compaction message indices (excluding the current turn)."""
    indices: list[int] = []
    for i, msg in enumerate(messages[:-1]):
        mt = msg.message_type if hasattr(msg, "message_type") else "assistant"
        if mt == "compaction":
            indices.append(i)
    return indices


def _decide_kept_compactions(
    compaction_indices: list[int], keep_all_compactions: bool
) -> set[int]:
    """Decide which compactions to keep in LLM context."""
    from agentnodes.kernel.agent.compact import _compaction_metrics

    if keep_all_compactions or not compaction_indices:
        return set(compaction_indices)
    # opencode-aligned: keep only the most recent compaction
    kept = {compaction_indices[-1]}
    hidden = len(compaction_indices) - 1
    _compaction_metrics["total_hidden"] += hidden
    _compaction_metrics["total_kept"] += 1
    if hidden > 0:
        logger.debug(
            "[HIST] hiding %d older compactions, keeping 1 most recent",
            hidden,
        )
    return kept


def _hidden_until_seq(messages: list, keep_compaction_indices: set[int]) -> int:
    """Compute the seq boundary hidden by the kept compaction markers.

    Everything with seq <= the boundary was replaced by the summary:
    it stays in the DB (chat record) but must NOT enter the LLM context.
    """
    from agentnodes.kernel.agent.compact import _compaction_metrics

    hidden_until_seq: int = -1
    for i in keep_compaction_indices:
        msg = messages[i]
        meta = getattr(msg, "metadata", None) or {}
        boundary = meta.get("compacted_until_seq")
        if isinstance(boundary, int) and boundary > hidden_until_seq:
            hidden_until_seq = boundary
    if hidden_until_seq >= 0:
        hidden_msgs = sum(
            1 for m in messages
            if getattr(m, "seq", 0) <= hidden_until_seq
            and getattr(m, "message_type", "") != "compaction"
        )
        _compaction_metrics["total_hidden"] += hidden_msgs
        logger.debug(
            "[HIST] hiding %d messages covered by compaction "
            "(seq <= %d), kept in DB for chat record",
            hidden_msgs, hidden_until_seq,
        )
    return hidden_until_seq


def _build_tool_indexes(
    messages: list,
) -> tuple[dict[str, int], dict[str, int]]:
    """Build assistant/tool lookup indexes for OpenAI protocol reordering.

    ``tool_to_assistant_idx``: tool_call_id -> assistant message index.
    ``tc_to_tool_idx``: tool_call_id -> tool message index.
    """
    tool_to_assistant_idx: dict[str, int] = {}
    tc_to_tool_idx: dict[str, int] = {}
    for i, msg in enumerate(messages[:-1]):
        role = msg.role if hasattr(msg, "role") else msg.get("role", "user")
        if role == "assistant":
            parts = msg.metadata.get("_parts", []) if hasattr(msg, "metadata") else []
            for p in parts:
                if isinstance(p, dict) and p.get("type") == "tool_call":
                    tc_id = p.get("id") or p.get("call_id")
                    if tc_id:
                        tool_to_assistant_idx.setdefault(tc_id, i)
        elif role == "tool":
            tc_id = msg.tool_call_id if hasattr(msg, "tool_call_id") else None
            if tc_id and tc_id not in tc_to_tool_idx:
                tc_to_tool_idx[tc_id] = i  # take first
    return tool_to_assistant_idx, tc_to_tool_idx


def _defer_or_drop_tool_message(
    i: int,
    msg,
    ctx: _HistoryConvertContext,
    emitted_tool_msg_idxs: set[int],
    emitted_assistant_idxs: set[int],
) -> list:
    """Handle a role="tool" message: skip, defer, or drop as orphan.

    Tool messages are emitted as part of their assistant:
    1. Already emitted alongside its assistant → skip
    2. Assistant comes later in the list → defer (paired then)
    3. Assistant not in this history slice (orphan) → drop with log
    """
    if i in emitted_tool_msg_idxs:
        return []
    tc_id = msg.tool_call_id if hasattr(msg, "tool_call_id") else None
    if not tc_id:
        return []
    assistant_idx = ctx.tool_to_assistant_idx.get(tc_id)
    if assistant_idx is None or assistant_idx in emitted_assistant_idxs:
        if assistant_idx is None:
            logger.debug(
                "[HIST] orphan tool dropped: tc_id=%s msg_id=%s",
                tc_id, msg.message_id,
            )
        return []
    return []  # Defer: assistant is later, will be paired then


def _convert_one_history_message(
    i: int,
    msg,
    ctx: _HistoryConvertContext,
    emitted_assistant_idxs: set[int],
    emitted_tool_msg_idxs: set[int],
) -> list[tuple[dict[str, Any], int | None]]:
    """Convert a single message into history entries (possibly empty).

    ``emitted_assistant_idxs`` / ``emitted_tool_msg_idxs`` are mutated
    to track which messages were already emitted.
    """
    from agentnodes.kernel.agent.compaction_message import CompactionMessage

    role = msg.role if hasattr(msg, "role") else msg.get("role", "user")
    content = msg.content if hasattr(msg, "content") else msg.get("content", "")
    parts = msg.metadata.get("_parts", []) if hasattr(msg, "metadata") else []
    message_type = msg.message_type if hasattr(msg, "message_type") else "assistant"

    # Skip messages covered by compaction (kept in DB, replaced by summary).
    if (
        ctx.hidden_until_seq >= 0
        and message_type != "compaction"
        and getattr(msg, "seq", 0) <= ctx.hidden_until_seq
    ):
        return []

    # Compaction messages: filter then convert
    if message_type == "compaction":
        if i not in ctx.keep_compaction_indices:
            return []
        logger.debug(
            "[HIST] keeping compaction msg id=%s content_len=%d",
            msg.message_id, len(content),
        )
        comp = CompactionMessage(
            id=msg.message_id,
            session_id=msg.session_id,
            summary=content,
            recent="",
            reason="auto",
        )
        return [(comp.to_llm_message(), None)]

    # Goal messages: role=system state snapshot the agent MUST see.
    if message_type == "goal":
        return [({"role": "system", "content": content or ""}, None)]

    if role == "tool":
        return _defer_or_drop_tool_message(
            i, msg, ctx, emitted_tool_msg_idxs, emitted_assistant_idxs
        )

    if role not in ("user", "assistant"):
        return []

    entry: dict[str, Any] = {"role": role, "content": content or ""}
    msg_seq = getattr(msg, "seq", None)
    if isinstance(msg_seq, int):
        entry["seq"] = msg_seq
    group_id: int | None = None

    if role == "assistant" and parts:
        tool_calls = _extract_tool_calls(parts)
        if tool_calls:
            entry["tool_calls"] = tool_calls
            group_id = i  # this assistant + its tools share group_id

    if not content and not entry.get("tool_calls"):
        return []

    emitted_assistant_idxs.add(i)
    entries: list[tuple[dict[str, Any], int | None]] = [(entry, group_id)]

    # Immediately follow with tool results for each tool_call.
    if role == "assistant" and entry.get("tool_calls"):
        _append_tool_results(
            entry["tool_calls"],
            parts,
            ctx.messages,
            ctx.tc_to_tool_idx,
            emitted_tool_msg_idxs,
            entries,
            group_id,
        )
    return entries


def _extract_tool_calls(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract OpenAI-format tool_calls from an assistant message's parts."""
    tool_calls = []
    for p in parts:
        if not isinstance(p, dict) or p.get("type") != "tool_call":
            continue
        tc_id = p.get("id") or p.get("call_id")
        if not tc_id:
            continue
        args_str = p.get("arguments") or "{}"
        if not isinstance(args_str, str):
            args_str = json.dumps(args_str, ensure_ascii=False)
        tool_calls.append({
            "id": tc_id,
            "type": "function",
            "function": {
                "name": p.get("name", ""),
                "arguments": args_str,
            },
        })
    return tool_calls


def _append_tool_results(
    tool_calls: list[dict[str, Any]],
    parts: list[dict[str, Any]],
    messages: list,
    tc_to_tool_idx: dict[str, int],
    emitted_tool_msg_idxs: set[int],
    history_with_groups: list[tuple[dict[str, Any], int | None]],
    group_id: int | None,
) -> None:
    """Append tool result entries right after their assistant message.

    Two sources (in priority order):
      1. Separate role="tool" messages (if they exist in DB)
      2. Embedded result in assistant's _parts (projector pattern)
    """
    seen_tc_ids: set[str] = set()
    for tc in tool_calls:
        tc_id = tc["id"]
        if tc_id in seen_tc_ids:
            continue
        seen_tc_ids.add(tc_id)

        tool_content = ""
        source_found = False

        # Source 1: separate tool message (legacy/compat)
        tool_msg_idx = tc_to_tool_idx.get(tc_id)
        if tool_msg_idx is not None and tool_msg_idx not in emitted_tool_msg_idxs:
            tool_msg = messages[tool_msg_idx]
            tool_content = (
                tool_msg.content if hasattr(tool_msg, "content")
                else tool_msg.get("content", "")
            )
            emitted_tool_msg_idxs.add(tool_msg_idx)
            source_found = True

        # Source 2: extract from assistant's _parts (projector pattern)
        if not source_found:
            for p in parts:
                if not isinstance(p, dict) or p.get("type") != "tool_call":
                    continue
                p_id = p.get("id") or p.get("call_id")
                if p_id == tc_id:
                    tool_content = p.get("result", "")
                    if not tool_content and p.get("status") == "error":
                        error_msg = p.get("error", "tool execution failed")
                        tool_content = json.dumps(
                            {"status": "error", "error": error_msg},
                            ensure_ascii=False,
                        )
                    break

        history_with_groups.append((
            {
                "role": "tool",
                "tool_call_id": tc_id,
                "content": tool_content or "",
            },
            group_id,
        ))


def _trim_history_groups(
    history_with_groups: list[tuple[dict[str, Any], int | None]],
) -> list[dict[str, Any]]:
    """Trim history by character budget, keeping assistant-tool groups intact.

    Group consecutive entries sharing a group_id; each group is kept
    whole or dropped whole. Within a group the order is preserved.
    Returns the final OpenAI-format history list.
    """
    grouped: list[list[tuple[dict[str, Any], int | None]]] = []
    current_group: list[tuple[dict[str, Any], int | None]] = []
    current_group_id: int | None = -1  # sentinel: no group yet
    for entry, gid in history_with_groups:
        if gid == current_group_id and gid is not None:
            current_group.append((entry, gid))
        else:
            if current_group:
                grouped.append(current_group)
            current_group = [(entry, gid)]
            current_group_id = gid
    if current_group:
        grouped.append(current_group)

    total_chars = 0
    kept_groups: list[list[dict[str, Any]]] = []  # groups in arrival order
    for group in reversed(grouped):
        group_chars = 0
        for e, _ in group:
            group_chars += len(e.get("content", ""))
            for tc in e.get("tool_calls") or []:
                group_chars += len(tc.get("function", {}).get("arguments", ""))
        if total_chars + group_chars > MAX_HISTORY_CHARS:
            # Drop this whole group; older groups are even larger
            continue
        kept_groups.append([e for e, _ in group])
        total_chars += group_chars
    # Reverse the outer group order (oldest first), keep each group's order.
    kept_groups.reverse()
    return [e for group in kept_groups for e in group]


# ── Helpers ────────────────────────────────────────────────────────────


