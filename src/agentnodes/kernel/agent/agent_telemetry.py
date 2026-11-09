"""AgentLoop 的遥测原语（批次 13 · S3-b1）。

**背景**：这些方法原先散在两处——5 个在 ``loop_telemetry.py`` 的
``LoopTelemetryMixin`` 上（纯转发），``_trace`` / ``_trace_llm_request``
在 ``loop.py`` 里（``_trace_llm_request`` 有 95 行，是 loop.py 里最长的
非引擎方法）。

**形态**：全部是显式传 ``loop`` 的模块级函数——不依赖 ``self``、不要求
继承，任何持有 ``_on_event`` / ``_trace_writer`` / ``_hooks`` 的对象
（AgentLoop、未来的组合式 engine）都能复用。

**兼容层**：``loop_telemetry.LoopTelemetryMixin`` 保留为薄壳，方法体
1 行转发到这里（阶段 3 删）。``AgentLoop`` 上的 ``_trace`` /
``_trace_llm_request`` 也是 1 行转发（外部 32 处按名字引用，不能改）。

**为什么 ``_emit`` / ``_trace_and_emit`` 不留在 loop.py**：它们是全仓
32 处引用的咽喉（``loop._emit(...)``），所以方法名必须留在 ``AgentLoop``
上；但**实现**没有理由留在 1,455 行的 loop.py 里。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

from agentnodes.foundation.utils.token_utils import estimate_tokens
from agentnodes.kernel.agent.runtime.sync_bridge import _get_hook_loop

logger = logging.getLogger(__name__)

__all__ = [
    "afire_hooks",
    "emit",
    "emit_tool_progress",
    "fire_hooks",
    "trace",
    "trace_and_emit",
    "trace_llm_request",
]


# ── 事件出口 ─────────────────────────────────────────────────────────


def _emit(loop: Any, event_type: str, data: dict | None = None) -> None:
    """调用 ``loop._emit``（若存在），否则直接走 ``on_event``。

    测试与部分调用方会**在实例上重绑** ``loop._emit``（MagicMock / 自定义
    收集器）——直接读 ``_on_event`` 会绕过它们。这里优先用实例方法，
    避免静默改变可观测性契约。
    """
    method = getattr(loop, "_emit", None)
    if callable(method):
        method(event_type, data or {})
        return
    emit(loop, event_type, data)


def _trace(loop: Any, entry: dict[str, Any]) -> None:
    """调用 ``loop._trace``（若存在），否则直接写 trace writer。"""
    method = getattr(loop, "_trace", None)
    if callable(method):
        method(entry)
        return
    trace(loop, entry)


def emit(loop: Any, event_type: str, data: dict | None = None) -> None:
    """Emit an event to the on_event callback (if set)."""
    on_event = getattr(loop, "_on_event", None)
    if on_event is None:
        return
    try:
        on_event(event_type, data or {})
    except Exception:  # noqa: BLE001
        logger.warning("on_event callback failed for %s", event_type, exc_info=True)


def trace(loop: Any, entry: dict[str, Any]) -> None:
    """Write a trace entry if trace writer is active."""
    writer = getattr(loop, "_trace_writer", None)
    if writer is None:
        return
    try:
        writer.write(entry)
    except Exception:  # noqa: BLE001
        pass  # trace failures should never break the loop


def trace_and_emit(loop: Any, event_type: str, data: dict | None = None) -> None:
    """Write a trace entry AND emit it onto the event bus.

    Keeps ``trace.jsonl`` (backward-compat) and the event_log in sync for
    the same logical event, so the Trajectory View can be derived from the
    event_log alone (single source of truth).
    """
    trace(loop, {"type": event_type, **(data or {})})
    emit(loop, event_type, data or {})


def emit_tool_progress(
    loop: Any,
    *,
    tool: str,
    call_id: str,
    stage: str,
    current: int | None = None,
    total: int | None = None,
    message: str = "",
) -> None:
    """Emit a tool_progress event from inside a tool execution."""
    emit(loop, "tool_progress", {
        "tool": tool,
        "call_id": call_id,
        "stage": stage,
        "current": current,
        "total": total,
        "message": message,
    })


# ── hook 适配 ────────────────────────────────────────────────────────


def fire_hooks(loop: Any, method_name: str, *args: Any, **kwargs: Any) -> None:
    """Sync adapter for async CompositeHook methods.

    The coroutine runs on the shared persistent hook loop (see
    ``_get_hook_loop``) via ``run_coroutine_threadsafe`` — safe from any
    thread, and no per-call loop construction/teardown.
    """
    hooks = getattr(loop, "_hooks", None)
    if hooks is None:
        return
    try:
        coro = getattr(hooks, method_name)(*args, **kwargs)
        if asyncio.iscoroutine(coro):
            fut = asyncio.run_coroutine_threadsafe(coro, _get_hook_loop())
            fut.result()
    except Exception:  # noqa: BLE001
        logger.warning("Hook %s failed", method_name, exc_info=True)


async def afire_hooks(
    loop: Any, method_name: str, *args: Any, **kwargs: Any
) -> None:
    """Async version of fire_hooks — awaits coroutine hooks directly."""
    hooks = getattr(loop, "_hooks", None)
    if hooks is None:
        return
    try:
        result = getattr(hooks, method_name)(*args, **kwargs)
        if asyncio.iscoroutine(result):
            await result
    except Exception:  # noqa: BLE001
        logger.warning("Hook %s failed", method_name, exc_info=True)


# ── LLM request 信封（loop.py 搬入，95 行）────────────────────────────


def trace_llm_request(
    loop: Any,
    messages: list[dict[str, Any]],
    iteration: int,
    tools: list[dict[str, Any]] | None = None,
) -> None:
    """Log full LLM request envelope (DSH request/header pattern).

    Records system prompt, tools schema, and history metadata for each
    LLM call. Large fields (system prompt, tools JSON) are offloaded to
    sidecar files when they exceed the threshold.

    Two sinks:
    1. ``llm_request`` on_event → the B4 forwarder (service.py) offloads
       large fields and persists to event_log (single source of truth).
    2. trace.jsonl (optional, backward-compat) if a trace_writer is set.

    This provides the foundation for Trajectory View / Trace Viewer.
    """
    try:
        import json as _json

        system_prompt = ""
        if messages and messages[0].get("role") == "system":
            system_prompt = messages[0].get("content", "")

        tools_json = _json.dumps(tools, ensure_ascii=False) if tools else "[]"

        history_meta = []
        for m in messages[1:]:  # skip system prompt
            history_meta.append({
                "role": m.get("role", "?"),
                "content_len": len(m.get("content", "")),
                "has_tool_calls": bool(m.get("tool_calls")),
            })

        # DSH-inspired: hash fields for fast diff detection
        system_prompt_hash = hashlib.sha256(system_prompt.encode()).hexdigest()[:16]
        tools_hash = hashlib.sha256(tools_json.encode()).hexdigest()[:16]
        history_fingerprint = "|".join(
            f"{m.get('role', '?')}:{len(m.get('content', ''))}"
            for m in messages[1:]
        )
        history_hash = hashlib.sha256(history_fingerprint.encode()).hexdigest()[:16]

        entry: dict[str, Any] = {
            "type": "llm_request",
            "iteration": iteration,
            "session_id": getattr(loop, "session_id", "") or "",
            "history_count": len(messages),
            "history_meta": history_meta,
            "tools_count": len(tools) if tools else 0,
            "system_prompt_len": len(system_prompt),
            "system_prompt": system_prompt,
            "tools_schema": tools_json,
            "system_prompt_hash": system_prompt_hash,
            "tools_hash": tools_hash,
            "history_hash": history_hash,
            "estimated_tokens": estimate_tokens(messages),
        }

        # Sink 1: event_log via the on_event forwarder. Best-effort.
        try:
            _emit(loop, "llm_request", dict(entry))
        except Exception:  # noqa: BLE001
            logger.debug("llm_request emit failed", exc_info=True)

        # Sink 2: trace.jsonl (optional, backward-compat).
        writer = getattr(loop, "_trace_writer", None)
        if writer is not None:
            try:
                writer.write_text_entry(
                    dict(entry),
                    field="system_prompt",
                    value=system_prompt,
                    offload_kind=f"llm-request-system-{iteration}",
                )
                writer.write_text_entry(
                    dict(entry),
                    field="tools_schema",
                    value=tools_json,
                    offload_kind=f"llm-request-tools-{iteration}",
                    threshold=10_000,  # tools schema usually fits inline
                )
            except Exception:  # noqa: BLE001
                pass  # trace failures should never break the loop
    except Exception:  # noqa: BLE001
        pass  # trace failures should never break the loop
