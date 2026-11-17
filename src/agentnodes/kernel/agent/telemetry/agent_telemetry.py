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
import json
import logging
from typing import Any

from agentnodes.foundation.utils.token_utils import estimate_tokens
from agentnodes.kernel.agent.runtime.sync_bridge import _get_hook_loop

logger = logging.getLogger(__name__)

__all__ = [
    "afire_hooks",
    "compose_ctx_hash_id",
    "compute_c_digest",
    "emit",
    "emit_tool_progress",
    "fire_hooks",
    "trace",
    "trace_and_emit",
    "trace_llm_request",
]


# ── 上下文指纹（统一引擎 U6 / ctx_hash_id）────────────────────────────
#
# ctx_hash_id = f"{trace_id}_{system_hash}_{tools_hash}_{c_digest}"
#
# **为什么是复合 id 而不是单一哈希**：单哈希不可分解——看到一个 id 无法
# 判断「哪部分变了」。复合形式下差异来源一眼可辨（实测）：
#
#   system 变了        → 第 1 段变
#   tools 变了         → 第 2 段变
#   messages 变了      → 第 3 段变
#
# **段的职责正交**：trace_id = 哪次 run（ContextVar，request/turn 级）；
# system/tools = 静态身份；c_digest = 本轮对话历史的内容级指纹。


def compute_c_digest(messages: list[dict[str, Any]]) -> str:
    """本轮对话历史的**内容级**指纹（16 字符 hex）。

    **不含 messages[0]（system）**：system 已被独立分量覆盖，再哈希是冗余；
    且含了会让 system 段与 c 段同时变——差异定位时看不出是 system 变了还是
    历史变了。三段职责必须正交。

    **为什么逐条哈希而不是整体 dumps**：全量 JSON 化在长对话下成本随消息量
    增长；逐条 sha256 是 O(n) 次小哈希，实测 1.5 μs/条（1000 条 = 1.5ms，
    相对一次 LLM 请求 0.49%）。

    **为什么不做增量**：compaction 会**替换** messages 列表（不是追加），
    增量缓存会失效。

    **为什么是内容级而不是长度级**：既有的 ``history_hash`` 用
    ``role:len(content)`` 指纹——实测 content 从 'AAAA' 改成 'BBBB'（等长）
    时 hash **完全不变**，不足以当身份用。
    """
    parts: list[str] = []
    for m in messages[1:]:  # skip system prompt（独立分量已覆盖）
        tc = m.get("tool_calls")
        parts.append(
            hashlib.sha256(
                f"{m.get('role', '')}\0{m.get('content', '') or ''}\0"
                f"{json.dumps(tc, sort_keys=True, default=str)}".encode("utf-8")
            ).hexdigest()[:16]
        )
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def compose_ctx_hash_id(
    trace_id: str | None,
    system_hash: str,
    tools_hash: str,
    c_digest: str,
) -> str:
    """组合上下文指纹：``trace_id_system_hash_tools_hash_c_digest``。

    **分隔符用 ``_``**：四段全是 hex（无 '_' 混入）→ ``split("_")`` 可无损
    反解出四段，差异来源可精确定位。

    **trace_id 段缺失时**：用空串占位会让 id 变成 ``_sys_tools_c``（前导
    下划线）——形态不统一。统一引擎路径的 trace_id 兜底已在
    ``AgentLoop._ensure_trace_context``（入口）补上，理论上不会为空；
    万一为空（外部未设且走了别的构造路径），退化为 ``"-"`` 占位以保持
    四段形态。
    """
    return f"{trace_id or '-'}_{system_hash}_{tools_hash}_{c_digest}"


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
    """Emit an event to the on_event callback (if set).

    **统一附加 ``ctx_hash_id``**（统一引擎 U6）：这里是**所有事件的最终
    出口**（``loop._emit`` → 本函数），所以一处附加即可让每个事件都带上
    当前状态的上下文指纹——不必逐个调用点改。

    载荷已自带（如 ``llm_request``）时不覆盖。实测开销可忽略
    （每事件一次 getattr + 字典赋值；63 字符的序列化成本 1.72 μs）。
    """
    on_event = getattr(loop, "_on_event", None)
    if on_event is None:
        return
    payload = data or {}
    if "ctx_hash_id" not in payload:
        _cid = getattr(loop, "_current_ctx_hash_id", "") or ""
        if _cid:
            payload = {**payload, "ctx_hash_id": _cid}
    try:
        on_event(event_type, payload)
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

        # 上下文指纹（统一引擎 U6）：trace_id + system + tools + 内容级历史。
        # 挂在 loop 上——它是**当前状态**（每轮更新），ctx / result / 事件
        # 都从这里派生。
        from agentnodes.foundation.observability.trace import _trace_id

        ctx_hash_id = compose_ctx_hash_id(
            _trace_id.get(),
            system_prompt_hash,
            tools_hash,
            compute_c_digest(messages),
        )
        try:
            loop._current_ctx_hash_id = ctx_hash_id
        except Exception:  # noqa: BLE001 — 不可写的 loop 不阻断
            pass

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
            "ctx_hash_id": ctx_hash_id,
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
