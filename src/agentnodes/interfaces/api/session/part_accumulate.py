"""W-split: assistant part accumulation (from SessionService)."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)
def _accumulate_part(
    parts: list[dict[str, Any]],
    event_type: str,
    data: dict[str, Any],
) -> None:
    """Accumulate SSE event payloads into a structured parts list.

    Mirrors the logic in chat.py:_run_agent_loop_background.

    Text events use the opencode-style 3-step lifecycle keyed by text_id:
        text.started { text_id }    → push new text part with id
        text_delta   { text_id, text } → findLast by id, append
        text.ended   { text_id, text } → findLast by id, override final

    text_delta events MUST carry text_id; missing text_id is a protocol
    error and the chunk is dropped.
    """
    if event_type == "text.started":
        _accumulate_text_started(parts, data)
    elif event_type == "text_delta":
        _accumulate_text_delta(parts, data)
    elif event_type == "text.ended":
        _accumulate_text_ended(parts, data)
    elif event_type == "tool_call":
        parts.append(_build_tool_call_part(data))
    elif event_type == "tool_result":
        _accumulate_tool_result(parts, data)
    elif event_type == "thinking_delta":
        _accumulate_thinking_delta(parts, data)
    elif event_type == "thinking_done":
        if parts and parts[-1].get("type") == "thinking":
            parts[-1]["status"] = "done"


def _accumulate_text_started(
    parts: list[dict[str, Any]], data: dict[str, Any]
) -> None:
    """Push a new text part (idempotent by text_id)."""
    text_id = data.get("text_id")
    if not text_id:
        logger.warning("text.started without text_id")
        return
    for p in reversed(parts):
        if p.get("type") == "text" and p.get("id") == text_id:
            return
    parts.append({"type": "text", "id": text_id, "text": ""})


def _accumulate_text_delta(
    parts: list[dict[str, Any]], data: dict[str, Any]
) -> None:
    """Append delta text to the matching text part (or create orphan)."""
    text_id = data.get("text_id")
    text = data.get("text", "")
    if not text_id:
        logger.warning("text_delta without text_id, dropping chunk")
        return
    for p in reversed(parts):
        if p.get("type") == "text" and p.get("id") == text_id:
            p["text"] += text
            break
    else:
        logger.warning("text_delta with orphan text_id=%s, pushing new", text_id)
        parts.append({"type": "text", "id": text_id, "text": text})


def _accumulate_text_ended(
    parts: list[dict[str, Any]], data: dict[str, Any]
) -> None:
    """Override the matching text part with the final text."""
    text_id = data.get("text_id")
    final_text = data.get("text", "")
    if not text_id:
        logger.warning("text.ended without text_id")
        return
    for p in reversed(parts):
        if p.get("type") == "text" and p.get("id") == text_id:
            p["text"] = final_text
            break


def _build_tool_call_part(data: dict[str, Any]) -> dict[str, Any]:
    """Build a tool_call part from an SSE event payload."""
    raw_args = data.get("arguments")
    args_str = (
        raw_args
        if isinstance(raw_args, str)
        else json.dumps(raw_args, ensure_ascii=False) if raw_args is not None else None
    )
    return {
        "type": "tool_call",
        "id": data.get("id") or data.get("call_id"),
        "name": data.get("name") or data.get("tool"),
        "arguments": args_str,
    }


def _accumulate_tool_result(
    parts: list[dict[str, Any]], data: dict[str, Any]
) -> None:
    """Attach a tool result to the matching tool_call part."""
    tool_call_id = data.get("id") or data.get("call_id")
    for p in reversed(parts):
        if p.get("type") == "tool_call" and p.get("id") == tool_call_id:
            p["result"] = data.get("result") or data.get("preview")
            p["status"] = data.get("status", "done")
            break


def _accumulate_thinking_delta(
    parts: list[dict[str, Any]], data: dict[str, Any]
) -> None:
    """Append a thinking delta to the last thinking part (or create one)."""
    delta = data.get("delta", "")
    if parts and parts[-1].get("type") == "thinking":
        parts[-1]["text"] += delta
    else:
        parts.append({"type": "thinking", "text": delta})


