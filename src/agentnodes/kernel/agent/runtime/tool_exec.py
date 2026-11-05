"""W5: pure helpers for AgentLoop tool-call execution (A3+ views)."""

from __future__ import annotations

import json
from typing import Any

# Keep in sync with loop._TOOL_RESULT_HISTORY_MAX semantics.
DEFAULT_HISTORY_MAX = 30_000
TOOL_RESULT_MAX = 50_000
PREVIEW_CHARS = 200


def missing_tool_payload(tc: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Event data + tool-result message when the tool is not in the registry."""
    content = json.dumps(
        {"status": "error", "error": f"tool '{tc.name}' not found"},
        ensure_ascii=False,
    )
    event = {
        "tool": tc.name,
        "id": tc.id,
        "call_id": tc.id,
        "status": "error",
        "ok": False,
        "result": "tool not in registry",
        "preview": "tool not in registry",
        "elapsed_ms": 0,
    }
    msg = {"role": "tool", "tool_call_id": tc.id, "content": content}
    return event, msg


def guard_denied_payload(tc: Any, reason: str) -> tuple[dict[str, Any], dict[str, Any]]:
    content = json.dumps({"status": "denied", "error": reason}, ensure_ascii=False)
    event = {
        "tool": tc.name,
        "id": tc.id,
        "call_id": tc.id,
        "status": "denied",
        "ok": False,
        "result": reason,
        "preview": reason[:PREVIEW_CHARS],
        "elapsed_ms": 0,
    }
    msg = {"role": "tool", "tool_call_id": tc.id, "content": content}
    return event, msg


def build_tool_kwargs(
    arguments: Any,
    *,
    workspace: Any = None,
    session_id: Any = None,
    ctx: Any = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge tool-call arguments with injected workspace/session/ctx."""
    kwargs: dict[str, Any] = dict(arguments) if isinstance(arguments, dict) else {}
    if "workspace" not in kwargs and workspace is not None:
        kwargs["workspace"] = workspace
    if "session_id" not in kwargs and session_id is not None:
        kwargs["session_id"] = session_id
    if ctx is not None:
        kwargs["ctx"] = ctx
    if extra:
        kwargs.update(extra)
    return kwargs


def is_error_output(output: Any) -> bool:
    return isinstance(output, str) and output.startswith('{"status": "error"')


def classify_tool_output(output: Any) -> dict[str, Any]:
    """Normalize tool output for events/trace (preview + capped full)."""
    text = output if isinstance(output, str) else str(output)
    return {
        "is_error": is_error_output(output),
        "preview": text[:PREVIEW_CHARS],
        "full": text[:TOOL_RESULT_MAX],
    }


def render_history_output(
    output: Any,
    max_chars: int | None = DEFAULT_HISTORY_MAX,
) -> str:
    """A3 smart LLM view; falls back to head/tail cut."""
    if max_chars is None:
        max_chars = DEFAULT_HISTORY_MAX
    try:
        from agentnodes.kernel.agent.tool_result import from_any, render_for_llm

        return render_for_llm(from_any(output), max_chars=max_chars or 0)
    except Exception:  # noqa: BLE001
        text = output if isinstance(output, str) else str(output)
        if max_chars and len(text) > max_chars:
            half = max_chars // 2
            return (
                text[:half]
                + f"\n\n[truncated {len(text) - max_chars} chars]\n\n"
                + text[-half:]
            )
        return text


def tool_result_message(call_id: str, content: str) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


__all__ = [
    "PREVIEW_CHARS",
    "TOOL_RESULT_MAX",
    "build_tool_kwargs",
    "classify_tool_output",
    "guard_denied_payload",
    "is_error_output",
    "missing_tool_payload",
    "render_history_output",
    "tool_result_message",
]
