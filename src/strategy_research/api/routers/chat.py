"""Chat API — send message + SSE event stream with real LLM integration。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import TYPE_CHECKING, Any, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

if TYPE_CHECKING:
    from ...core.permission import PermissionGateway

from ..schemas.chat import (
    CancelRequest,
    ChatAttemptsResponse,
    ChatCancelResponse,
    ChatPersonasResponse,
    ChatQueueResumeResponse,
    SendMessageResponse,
)
from ..schemas.chat import (
    ChatMessageRequest as ChatMessage,
)
from ..session.models import AttemptId, SessionId
from ..session.service import SessionService
from .slash_commands import (
    _handle_clear_command,
    _handle_compact_command,
    _handle_goal_command,
    _handle_help_command,
    _handle_study_command,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _shell_tools_enabled(mode: str | None = None) -> bool:
    """Whether the ``run_command`` shell tool should be offered to the agent.

    Shell tools are opt-in: set ``SR_ALLOW_SHELL_TOOLS=1`` (accepts
    1/true/yes, case-insensitive) in the server environment to enable
    ``run_command``. Plan mode never exposes shell tools (analysis-only,
    single iteration), regardless of the env var.
    """
    enabled = os.environ.get("SR_ALLOW_SHELL_TOOLS", "").lower() in (
        "1", "true", "yes",
    )
    return False if mode == "plan" else enabled

# ── Shared EventStore + SessionService (singleton) ──────────────────────────
# The EventStore is created per-DB-path inside _get_session_service()
# (one per workspace). There is NO module-level EventStore instance —
# the previous ``_event_store = EventStore()`` was a dead instance that
# opened an empty ``~/.quantnodes/sessions.db`` and was never emitted
# to, but it held a file descriptor and confused DB-path unification.
# See core.agent.memory_manager.resolve_session_db_path for the unified
# DB path resolution shared by EventStore and web_session.

# True process-wide singleton. Recreating the service per request was a
# real bug: queues/tasks/pause state live on the instance, so cancel,
# queue_full and FIFO ordering silently broke (two parallel AgentLoops
# per session). Keyed by db path so tests/workspace switches re-create.
_session_service_cache: dict[str, SessionService] = {}

# ── Tier 1 A1: permission gateway ──────────────────────────────────
# One process-wide PermissionGateway (mirrors SessionService). Wired
# with an ``on_request`` hook that pushes a ``permission_request``
# SSE event for every ASK verdict; the front-end answers via
# ``POST /api/chat/permission/respond`` which calls
# ``gateway.respond(tool_call_id, ...)``.
_permission_gateway_cache: dict[str, "PermissionGateway"] = {}


def _get_permission_gateway(request: Request | None = None) -> "PermissionGateway | None":
    """Lazy-init the process-wide gateway.

    The ``on_request`` hook is wired on first use so we can attach the
    SSE buffer without circular imports.
    """
    from ...core.permission import PermissionGateway

    gateway = _permission_gateway_cache.get("default")
    if gateway is not None:
        return gateway

    def _push_sse(tool_call_id, decision, args):
        try:
            from ...core.events.event_v2 import EventType

            session_id = args.get("__session_id__") or ""
            if not session_id:
                # No session scope = no subscriber can see this.
                # Drop on the floor (and log so operators notice).
                logger.warning(
                    "permission request without session_id; dropping",
                )
                return
            # Tool name is implicit from the EventType subscriber;
            # payload carries call_id + args for the dialog UI.
            payload = {
                "tool_call_id": tool_call_id,
                "tool_name": args.get("__tool_name__", ""),
                "args": _safe_payload(args),
                "pattern": decision.pattern,
                "target": decision.target,
            }
            _push_event(session_id, EventType.PERMISSION_REQUEST, payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("permission SSE push failed: %s", exc)

    gateway = PermissionGateway(
        on_request=_push_sse,
    )
    _permission_gateway_cache["default"] = gateway
    return gateway


def _safe_payload(args: dict) -> dict:
    """Drop large fields before SSE-serialising the request."""
    out = {}
    for k, v in args.items():
        if k.startswith("__") and k.endswith("__"):
            continue  # internal marker
        s = repr(v)
        out[k] = s[:200] + "…" if len(s) > 200 else v
    return out


def _push_event(session_id: str, event_type: str, data: Any) -> None:
    """A2: emit an SSE-visible event through the single EventStore track.

    Replaces the retired ``legacy in-process SSE push``
    calls. The event store persists it (event_log), feeds live
    subscribers (``/api/chat/events`` via ``subscribe()``) and — when
    configured — projects it into messages. Payloads that were already
    JSON strings on the old track are accepted for a mechanical
    migration; dicts are preferred.

    Never raises: SSE visibility must not break the request path.
    """
    try:
        payload: Any = data
        if isinstance(data, str):
            try:
                payload = json.loads(data)
            except (json.JSONDecodeError, TypeError):
                payload = {"raw": data}
        if not isinstance(payload, dict):
            payload = {"value": payload}
        _get_session_service().event_bus.emit(session_id, event_type, payload)
    except Exception as exc:  # noqa: BLE001 — best effort by design
        logger.warning("[SSE] emit %s failed session=%s: %s", event_type, session_id, exc)


def _get_session_service() -> SessionService:
    """Return the process-wide singleton SessionService for the DB.

    The DI container is the single construction point: ``create_app``
    attaches it and pre-seeds this cache; the lazy fallback here (only
    reached in tests/scripts without an app) builds a fresh container
    with the same production wiring:

    1. event_log (persistent source of truth)
    2. SSE push (via sse_pusher callback → SSEEventBuffer)
    3. messages + message_parts tables via Projector.flush (flush_to_messages=True)
    """
    from ..container import build_container
    from .web_session import _get_db_path

    db_path = str(_get_db_path())
    service = _session_service_cache.get(db_path)
    if service is None:
        container = build_container(db_path=db_path)
        service = container.session_service
        _session_service_cache[db_path] = service
    return service


# ── LLM Config ────────────────────────────────────────────────────────


def _build_llm_config():
    """Build LLMConfig from env/settings. Same logic as TUI's __main__.py."""
    try:
        from pathlib import Path

        from dotenv import load_dotenv
        load_dotenv(Path.home() / ".quantnodes" / ".env", override=True)
    except Exception:
        pass
    try:
        from strategy_research.core.llm.config import LLMConfig
        cfg = LLMConfig.load()
        return cfg
    except Exception as exc:
        logger.warning("Failed to load LLMConfig: %s", exc)
        return None








# ── PartAccumulator (replaces inline on_event closure) ──────────────────────


class _PartAccumulator:
    """Accumulates LLM streaming parts and pushes events to SSE buffer.

    Replaces the inline ``on_event`` closure in ``_run_agent_loop_background``
    to reduce C901 complexity from 31 to <10.
    """

    def __init__(
        self,
        session_id: str,
        message_id: str,
    ) -> None:
        self._session_id = session_id
        self._message_id = message_id
        self.parts: list[dict[str, Any]] = []

    def handle(self, event_type: str, data: dict) -> None:
        event_data = {**data, "message_id": self._message_id}
        _push_event(self._session_id, event_type, json.dumps(event_data, ensure_ascii=False))
        self._route(event_type, event_data)

    def _route(self, event_type: str, data: dict) -> None:
        handler = {
            "text.started": self._on_text_started,
            "text_delta": self._on_text_delta,
            "text.ended": self._on_text_ended,
            "tool_call": self._on_tool_call,
            "tool_result": self._on_tool_result,
            "tool_progress": self._on_tool_progress,
            "thinking_delta": self._on_thinking_delta,
            "thinking_done": self._on_thinking_done,
        }.get(event_type)
        if handler is not None:
            handler(data)

    def _on_text_started(self, data: dict) -> None:
        text_id = data.get("text_id")
        if text_id and not any(
            p.get("type") == "text" and p.get("id") == text_id
            for p in reversed(self.parts)
        ):
            self.parts.append({"type": "text", "id": text_id, "text": ""})

    def _on_text_delta(self, data: dict) -> None:
        text_id = data.get("text_id")
        text = data.get("text", "")
        if not text_id:
            return
        for p in reversed(self.parts):
            if p.get("type") == "text" and p.get("id") == text_id:
                p["text"] += text
                return
        self.parts.append({"type": "text", "id": text_id, "text": text})

    def _on_text_ended(self, data: dict) -> None:
        text_id = data.get("text_id")
        final_text = data.get("text", "")
        if text_id:
            for p in reversed(self.parts):
                if p.get("type") == "text" and p.get("id") == text_id:
                    p["text"] = final_text
                    return

    def _on_tool_call(self, data: dict) -> None:
        self.parts.append({
            "type": "tool_call",
            "id": data.get("id"),
            "name": data.get("name"),
            "arguments": data.get("arguments"),
        })

    def _on_tool_result(self, data: dict) -> None:
        for p in reversed(self.parts):
            if p.get("type") == "tool_call" and p.get("id") == data.get("id"):
                p["result"] = data.get("result")
                p["status"] = data.get("status", "done")
                return

    def _on_tool_progress(self, data: dict) -> None:
        for p in reversed(self.parts):
            if p.get("type") == "tool_call" and p.get("id") == data.get("id"):
                p["progress"] = data.get("steps", [])
                return

    def _on_thinking_delta(self, data: dict) -> None:
        delta = data.get("delta", "")
        if self.parts and self.parts[-1].get("type") == "thinking":
            self.parts[-1]["text"] += delta
        else:
            self.parts.append({"type": "thinking", "text": delta})

    def _on_thinking_done(self, data: dict) -> None:
        if self.parts and self.parts[-1].get("type") == "thinking":
            self.parts[-1]["status"] = "done"

    @property
    def assistant_content(self) -> str:
        return "".join(
            p.get("text", "") for p in self.parts if p.get("type") == "text"
        )


# ── Helpers for _run_agent_loop_background ──────────────────────────────


def _persist_user_message(session_id: str, content: str) -> None:
    """Persist user message to DB."""
    from .web_session import persist_message
    persist_message(session_id=session_id, role="user", content=content)


def _auto_title_and_notify(session_id: str, task: str) -> None:
    """Auto-title session and notify frontend via SSE."""
    from .web_session import _get_db, auto_title_session
    new_title = auto_title_session(session_id, task)
    if not new_title:
        return
    try:
        conn = _get_db()
        row = conn.execute(
            "SELECT message_count, starred, tags_json, archived "
            "FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        meta_update = {
            "session_id": session_id,
            "title": new_title,
            "message_count": row["message_count"] if row else 0,
            "starred": bool(row["starred"]) if row else False,
            "tags": json.loads(row["tags_json"]) if row and row["tags_json"] else [],
            "archived": bool(row["archived"]) if row else False,
        }
        _push_event(session_id, "session_meta_updated", json.dumps(meta_update, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001 — best-effort SSE emit
        logger.warning("Failed to emit session_meta_updated: %s", exc)


def _persist_assistant_message(
    session_id: str,
    content: str,
    parts: list[dict[str, Any]],
    message_id: str,
    cfg: Any = None,
    claim_validation: dict[str, Any] | None = None,
) -> None:
    """Persist assistant message to DB."""
    from .web_session import persist_message
    metadata: dict[str, Any] = {"model": getattr(cfg, "model", None)} if cfg else {}
    if claim_validation:
        metadata["claim_validation"] = claim_validation
    persist_message(
        session_id=session_id, role="assistant", content=content,
        parts=parts or None,
        metadata=metadata or None,
        message_id=message_id,
    )


async def _emit_config_error(session_id: str, message_id: str) -> None:
    """Emit SSE error events when LLM is not configured."""
    err_data = {"message_id": message_id, "error": "LLM 未配置。请设置 OPENAI_API_KEY 环境变量。"}
    _push_event(session_id, "error", json.dumps(err_data))
    _push_event(session_id, "agent_done", json.dumps({"message_id": message_id, "status": "error"}))


def _emit_agent_done(session_id: str, message_id: str, answer: str | None) -> None:
    """Emit agent_done SSE event."""
    _push_event(session_id, "agent_done", json.dumps({
            "message_id": message_id,
            "status": "success" if answer else "empty",
            "answer_length": len(answer) if answer else 0,
        }))


async def _emit_agent_error(session_id: str, message_id: str, exc: Exception) -> None:
    """Emit error + agent_done SSE events on AgentLoop failure."""
    _push_event(session_id, "error", json.dumps({"message_id": message_id, "error": str(exc)}))
    _push_event(session_id, "agent_done", json.dumps({"message_id": message_id, "status": "error"}))


async def _run_agent_loop_background(
    session_id: str,
    message_id: str,
    task: str,
):
    """Run AgentLoop in background, pushing events to SSE buffer."""
    import os

    _persist_user_message(session_id, task)
    _auto_title_and_notify(session_id, task)

    accumulator = _PartAccumulator(session_id, message_id)

    if os.environ.get("STRATEGY_RESEARCH_TEST_CHAT") == "1":
        await _run_test_script(session_id, message_id, task, accumulator.parts)
        _persist_assistant_message(session_id, accumulator.assistant_content, accumulator.parts, message_id)
        return

    cfg = _build_llm_config()
    if cfg is None or not cfg.api_key:
        await _emit_config_error(session_id, message_id)
        _persist_assistant_message(session_id, "LLM 未配置", [{"type": "error", "message": "LLM 未配置"}], message_id)
        return

    try:
        from strategy_research.core.agent.chat_loop import build_chat_agent_loop
        from strategy_research.core.agent.memory_manager import (
            get_default_memory_manager,
        )

        mm = get_default_memory_manager()

        gateway = _get_permission_gateway(None)
        loop = build_chat_agent_loop(
            config=cfg,
            session_id=session_id,
            role="chat",
            on_event=accumulator.handle,
            workspace=None,
            enable_goal_injection=True,  # long-horizon: continue until goal criteria met
            # Tier 1 A1: wire the permission gate so ASK verdicts
            # trigger an SSE permission_request that the front-end
            # answers via POST /api/chat/permission/respond.
            permission_evaluator=gateway.evaluator if gateway else None,
            permission_gateway=gateway,
        )

        result = await loop.arun(task)

        if result.answer:
            await mm.append(session_id, "user", task)
            await mm.append(session_id, "assistant", result.answer)

        _emit_agent_done(session_id, message_id, result.answer)
        _persist_assistant_message(
            session_id,
            result.answer or accumulator.assistant_content,
            accumulator.parts,
            message_id,
            cfg,
            claim_validation=result.metrics.get("claim_validation"),
        )

    except Exception as exc:  # noqa: BLE001 — agent loop can throw anything
        logger.error("AgentLoop failed for session %s: %s", session_id, exc, exc_info=True)
        await _emit_agent_error(session_id, message_id, exc)
        _persist_assistant_message(session_id, f"[error] {exc}", [{"type": "error", "message": str(exc)}], message_id)


async def _run_test_script(
    session_id: str,
    message_id: str,
    task: str,
    accumulated_parts: Optional[list[dict[str, Any]]] = None,
):
    """Emit a scripted sequence of SSE events for E2E testing.

    Mirrors what the real AgentLoop would emit:
    - thinking_start → thinking_delta (×N) → thinking_done
    - text_delta (×N, simulating streaming response)
    - agent_done

    Events are spaced 30ms apart to keep tests fast but realistic.

    If ``accumulated_parts`` is provided, fills it with the same text/thinking
    parts so persistence can save the assistant message.
    """
    reply_parts = [
        f"这是对「{task}」的脚本化回复。",
        " 主要演示 SSE 流式事件分发链路。",
        " 客户端 useSSE hook 会把每个 text_delta 累积到 streamingText。",
        "\n\n**测试要点**：",
        "\n- 事件 message_id 关联",
        "\n- streamingText 累积",
        "\n- agent_done 清空 streamingMessageId",
    ]
    if accumulated_parts is None:
        accumulated_parts = []
    try:
        # Phase 1: thinking block (collapsed by default)
        _push_event(session_id, "thinking_start", json.dumps({"message_id": message_id}, ensure_ascii=False))
        thinking_chunks = ["分析用户问题", " → 检索相关策略", " → 准备回复"]
        thinking_text = "".join(thinking_chunks)
        accumulated_parts.append({"type": "thinking", "text": thinking_text, "status": "streaming"})
        for chunk in thinking_chunks:
            await asyncio.sleep(0.03)
            _push_event(session_id, "thinking_delta", json.dumps({"message_id": message_id, "delta": chunk}, ensure_ascii=False))
        # Mark thinking done
        if accumulated_parts and accumulated_parts[-1].get("type") == "thinking":
            accumulated_parts[-1]["status"] = "done"
        _push_event(session_id, "thinking_done", json.dumps({"message_id": message_id}, ensure_ascii=False))

        # Phase 2: streaming text reply — accumulate as single text part
        full_text = "".join(reply_parts)
        accumulated_parts.append({"type": "text", "text": full_text})
        for part in reply_parts:
            await asyncio.sleep(0.03)
            _push_event(session_id, "text_delta", json.dumps({"message_id": message_id, "text": part}, ensure_ascii=False))

        # Phase 3: completion signal
        _push_event(session_id, "agent_done", {
            "message_id": message_id,
            "status": "success",
            "answer_length": sum(len(p) for p in reply_parts),
        })
    except asyncio.CancelledError:
        # Test cancellation handling matches production
        _push_event(session_id, "agent_done", json.dumps({"message_id": message_id, "status": "cancelled"}, ensure_ascii=False))
        raise


@router.post("/send_async", response_model=SendMessageResponse)
async def send_async(body: ChatMessage, request: Request):
    """Send a message asynchronously. Enqueues into per-session FIFO queue.

    Returns message_id + event_id for confirmation.
    Frontend should listen to /events SSE stream for real-time response.

    Uses the unified SessionService (also shared with TUI). New messages
    are appended to a per-session queue and processed FIFO; if the queue
    is full (hard limit 10) the API returns 429.
    """
    logger.info("[SEND] session=%s content_len=%d content_preview=%s",
                body.session_id, len(body.content), body.content[:50])

    # Ownership check: only the session owner may post messages
    from .web_session import _fetch_session_owned, _get_db
    user_id = getattr(request.state, "user_id", "anonymous")
    _fetch_session_owned(_get_db(), body.session_id, user_id)

# ── /goal command intercept ────────────────────────────────────────
    if body.content.strip().startswith("/goal"):
        return await _handle_goal_command(body)

    # ── /study command intercept (study task system) ───────────────────
    if body.content.strip().startswith("/study"):
        return await _handle_study_command(body)

    # ── /compact command intercept ──────────────────────────────────────
    if body.content.strip() == "/compact":
        return await _handle_compact_command(body)

    # ── /clear command intercept (webui only — clears LLM-visible
    # history while preserving the persisted message log). Returns
    # immediately without invoking the LLM. The TUI does not use this
    # path; equivalent TUI behaviour is `clear_session`. ─────────────
    if body.content.strip() == "/clear":
        return await _handle_clear_command(body)

    # ── /help command intercept — returns a static cheat-sheet text
    # in a synthetic assistant message so the user sees it in context.
    # Skips LLM invocation. ──────────────────────────────────────────
    if body.content.strip() == "/help":
        return await _handle_help_command(body)

    # Delegate to SessionService — it handles DB persistence, queueing,
    # AgentLoop execution, history context, and event emission.
    service = _get_session_service()
    # Chat mode: bounded iteration budget from llm.json (default 50).
    # Prevents a failing tool loop from growing the prompt unboundedly.
    from strategy_research.core.llm.config import LLMConfig
    try:
        _cfg = LLMConfig.load()
        _max_iter = _cfg.max_iterations
    except (OSError, ValueError, KeyError):
        _max_iter = 50
    # Shell tools are opt-in (SR_ALLOW_SHELL_TOOLS=1); plan mode never
    # exposes them (analysis-only, single iteration).
    _mode = body.mode or "build"
    _max_iter_eff = 1 if _mode == "plan" else _max_iter
    _allow_shell_eff = _shell_tools_enabled(_mode)
    result = await service.send_message(
        session_id=body.session_id,
        content=body.content,
        max_iterations=_max_iter_eff,
        allow_shell_tools=_allow_shell_eff,
        persona=body.agent_id,
        mode=_mode,
        model=body.model,
        thinking=body.thinking,
    )

    # Queue-full guard: SessionService returns {"error": "queue_full", ...}
    # when the session already has _QUEUE_LIMIT items waiting.
    if result.get("error") == "queue_full":
        logger.warning("[SEND] queue_full session=%s limit=%d current=%d",
                       body.session_id, result["limit"], result["current_size"])
        raise HTTPException(
            status_code=429,
            detail={
                "error": "queue_full",
                "limit": result["limit"],
                "current_size": result["current_size"],
            },
        )

    logger.info("[SEND] ok session=%s user_msg=%s attempt=%s status=%s",
                body.session_id, result.get("user_message_id"),
                result.get("attempt_id"), result.get("status"))

    return SendMessageResponse(
        message_id=result["user_message_id"],
        user_message_id=result["user_message_id"],
        assistant_message_id=result["assistant_message_id"],
        event_id="",
        status=result.get("status", "processing"),
        attempt_id=result.get("attempt_id"),
    )


@router.post("/cancel", response_model=ChatCancelResponse)
async def cancel_attempt(body: CancelRequest, request: Request):
    """Cancel an in-flight agent attempt for a session.

    If attempt_id is provided, cancels that specific attempt.
    Otherwise, cancels any active attempt for the session.

    After cancellation the per-session queue is **paused** (queue_paused
    SSE event). Use ``POST /chat/queue/resume`` to continue processing.
    """
    from .web_session import _fetch_session_owned, _get_db
    user_id = getattr(request.state, "user_id", "anonymous")
    _fetch_session_owned(_get_db(), body.session_id, user_id)
    service = _get_session_service()

    # Prefer the session-scoped cancel (cancels the per-attempt task; the
    # consumer loop catches CancelledError and pauses the queue).
    if body.attempt_id:
        ok = service.cancel(AttemptId(body.attempt_id))
        if ok:
            return {"status": "cancelled", "attempt_id": body.attempt_id}

    # Fallback: cancel by session_id only
    ok = service.cancel_session(SessionId(body.session_id))
    if ok:
        return {"status": "cancelled", "session_id": body.session_id}

    return {"status": "no_active_attempt", "session_id": body.session_id}


class QueueResumeRequest(BaseModel):
    session_id: str


@router.post("/queue/resume", response_model=ChatQueueResumeResponse)
async def queue_resume(body: QueueResumeRequest, request: Request):
    """Resume a paused per-session queue after an explicit cancel.

    Returns ``{"ok": true, "session_id": ...}`` if the queue was paused
    and is now resumed; ``{"ok": false}`` if no paused queue exists.
    """
    from .web_session import _fetch_session_owned, _get_db
    user_id = getattr(request.state, "user_id", "anonymous")
    _fetch_session_owned(_get_db(), body.session_id, user_id)
    service = _get_session_service()
    ok = service.resume_queue(body.session_id)
    return {"ok": ok, "session_id": body.session_id}


# ── 阶段 3a：新入口路径（灰度） ──────────────────────────────


def _get_gateway():
    """进程内唯一 Gateway（见 core/gateway/singleton.py 的串行域说明）。"""
    from strategy_research.core.gateway import get_gateway

    return get_gateway()


async def _send_via_gateway(session_id: str, content: str, *, wait: bool = False) -> dict:
    """新路径：WorkRequest → EntryPoint → 准入 → 引擎 → 事件流。

    P5-1：补齐历史注入与持久化，**照抄旧路径的两条机制**（不改选型）：
    - 读历史 → ``SessionStore.get_messages`` + ``_convert_messages_to_history``
    - 写消息 → ``event_bus.emit`` → projector（``flush_to_messages=True``）

    P5-1b：流式与工具链——
    - Gateway 装配 tool registry（shell 工具按 ``_shell_tools_enabled`` 门禁）
    - 引擎事件经 ``install_sink`` 转发为前端词表（text_delta / tool_call / …）
    - 末尾补发 ``agent_done``

    R2.1c（E1=C）：消费者在跑且未要求 wait → **receipt 路径**——submit 后
    立即返回 ``{accepted, message_id, session_id}``，答案经 ``run.answer``
    事件由 persist 桥落库并补 agent_done；请求线程不再驱动执行。
    同步路径（消费者未跑 = 测试/直构；或 ``wait=true``）保持现契约：
    阻塞至 turn 完成、响应携带 reply。wait=true 只是**等待**，执行仍是
    消费者的事（无消费者时回落请求内驱动）。

    顺序关键：**先读历史、再 emit 本轮事件**——否则当前轮会被重复注入。
    """
    import time as _time
    import uuid as _uuid

    from strategy_research.core.gateway import api_chat_request
    from strategy_research.core.orchestration import WorkKind

    service = _get_session_service()

    # ── ① 读历史（与旧路径同一函数、同一参数）──────────────────
    history: list[dict] = []
    try:
        cfg = _build_llm_config()
        keep_all = bool(
            cfg and cfg.compact_config
            and cfg.compact_config.keep_all_compactions_in_history
        )
        stored = service.store.get_messages(session_id, limit=100)
        history = service._convert_messages_to_history(
            stored, keep_all_compactions=keep_all,
        )
    except Exception as exc:  # noqa: BLE001 —— 历史缺失不阻断对话
        logger.warning("send_via_gateway: history load failed: %s", exc)

    # ── ② 提交（历史随 payload 进入引擎）───────────────────────
    gw = _get_gateway()
    user_mid = str(_uuid.uuid4())
    assistant_mid_holder: dict[str, str] = {}

    receipt = gw.entry.submit(
        api_chat_request(session_id, content, history=history)
    )
    if not receipt.accepted:
        raise HTTPException(
            status_code=400,
            detail={"error": "entry_rejected", "reason": str(receipt.reason)},
        )

    # ── ②-b receipt 路径（R2.1c，E1=C）────────────────────────
    # 消费者在跑且未要求 wait：用户消息此刻落库，答案经 run.answer 由
    # persist 桥落库并补 agent_done；HTTP 立即返回两态回执。
    if gw.consumer_running() and not wait:
        _unsub = _install_chat_event_bridge(
            gw, session_id, assistant_mid_holder,
            service=service, persist=True,
        )
        try:
            service.event_bus.emit(session_id, "message_received", {
                "message_id": user_mid,
                "user_message_id": user_mid,
                "role": "user",
                "content": content,
                "created_at": _time.time(),
            })
        except Exception as exc:  # noqa: BLE001 —— 落库失败不阻断受理
            logger.warning("send_via_gateway: user persist failed: %s", exc)
        return {
            "accepted": True,
            "message_id": user_mid,
            "session_id": receipt.session_id or session_id,
        }

    # ── ③ 执行（同步路径：kind 过滤；wait=true 时只等不驱动）────
    _unsub = _install_chat_event_bridge(gw, session_id, assistant_mid_holder)
    try:
        if gw.consumer_running() and wait:
            run = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: gw.wait_turn(
                    receipt.session_id or session_id,
                    timeout=_chat_wait_timeout(),
                ),
            )
            if run is None:
                raise HTTPException(
                    status_code=504,
                    detail={"error": "wait_timeout",
                            "hint": "turn 未在时限内完成；请改用 SSE 订阅"},
                )
        else:
            runs = await asyncio.get_running_loop().run_in_executor(
                None, lambda: gw.run_once({WorkKind.CHAT}),
            )
            if not runs:
                raise HTTPException(status_code=504, detail="no run executed")
            run = runs[-1]
    finally:
        # R7：兜底注销（bridge 在收尾事件时已自注销，这里幂等）
        _unsub()
    if not run.ok:
        _push_sse("error", {"message_id": run.run_id, "error": run.error or ""}, session_id)
        _push_sse("agent_done", {"message_id": run.run_id, "status": "error"}, session_id)
        raise HTTPException(
            status_code=502,
            detail={"error": "run_failed", "reason": run.error or ""},
        )

    # ── ④ 成功后只发事件 → projector 落库（与旧路径同源）─────────
    # 不调 persist_message：旧路径注释明确「not via direct store.append_message()」，
    # 且两路同写会撞 UNIQUE 或产生重复行（已实测）。
    assistant_mid = run.run_id  # 新路径无 attempt，用 run_id 作消息标识
    try:
        service.event_bus.emit(session_id, "message_received", {
            "message_id": user_mid,
            "user_message_id": user_mid,
            "assistant_message_id": assistant_mid,
            "role": "user",
            "content": content,
            "created_at": _time.time(),
        })
        service.event_bus.emit(session_id, "assistant_message", {
            "message_id": assistant_mid,
            "role": "assistant",
            "content": run.answer,
            "created_at": _time.time(),
        })
    except Exception as exc:  # noqa: BLE001 —— 落库失败不吞掉回答
        logger.warning("send_via_gateway: persist via event failed: %s", exc)

    # ── ⑤ 收尾事件 ─────────────────────────────────────────
    _push_sse("agent_done", {"message_id": assistant_mid, "status": "completed"}, session_id)

    return {
        "message_id": assistant_mid,
        "attempt_id": run.run_id,
        "reply": run.answer,
        "status": "completed",
    }


def _push_sse(kind: str, data: dict, session_id: str) -> None:
    """把一条事件推入 SSE 缓冲（失败只记日志，不影响主路径）。"""
    try:
        _push_event(session_id, kind, json.dumps(data, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        logger.debug("sse push failed (%s): %s", kind, exc)


def _chat_wait_timeout() -> float:
    """wait=true 的等待上限（秒）。turn 可能很长，默认 300s。"""
    raw = os.environ.get("QNODES_CHAT_WAIT_TIMEOUT", "").strip()
    try:
        return float(raw) if raw else 300.0
    except ValueError:
        return 300.0


def _install_chat_event_bridge(
    gw: Any, session_id: str, message_id_holder: dict[str, str],
    *, service: Any = None, persist: bool = False,
) -> Any:
    """把引擎的平台事件翻译成前端词表并推 SSE（P5-1b）。

    引擎事件（node.started / text_delta / tool.started）是平台观测语义；
    前端需要 tool_call / tool_result / text_delta 等产品语义。翻译层放这里，
    不改引擎。

    R2.1c：``persist=True``（receipt 路径）时，桥还承担收尾——
    ``run.answer`` → assistant_message 落库 + agent_done；
    ``run.failed`` / ``run.settled{fenced}`` → error + agent_done。
    消费者线程里没有人替请求做这些（请求早已返回 receipt）。

    R7：返回注销函数。**收尾事件后自动注销**——否则旧 bridge 闭包会
    一直存活，用旧 message_id 推送重复 SSE（同一 session 第二次请求时
    出现重复文本），且 sink 链随请求数线性增长。调用方也应在 finally
    里兜底注销。
    """
    mapping = {
        "tool.started": "tool_call",
        "tool.completed": "tool_result",
    }
    state: dict[str, Any] = {"unsubscribe": None, "closed": False}

    def _close() -> None:
        if state["closed"]:
            return
        state["closed"] = True
        unsub = state.get("unsubscribe")
        if callable(unsub):
            try:
                unsub()
            except Exception:  # noqa: BLE001
                logger.debug("bridge unsubscribe failed", exc_info=True)

    def bridge(kind: str, data: dict) -> None:
        try:
            _bridge_body(kind, data)
        finally:
            # 收尾事件 → 本 turn 结束 → 注销，防累积/重复推送
            if kind in ("run.answer", "run.failed"):
                _close()
            elif kind == "run.settled" and (
                data.get("fenced") or not data.get("ok", True)
            ):
                _close()

    def _bridge_body(kind: str, data: dict) -> None:
        if persist:
            if kind == "run.answer":
                mid = str(data.get("run_id") or "")
                message_id_holder["id"] = mid
                if service is not None:
                    try:
                        service.event_bus.emit(session_id, "assistant_message", {
                            "message_id": mid,
                            "role": "assistant",
                            "content": str(data.get("content") or ""),
                            "created_at": time.time(),
                        })
                    except Exception:  # noqa: BLE001 —— 落库失败不吞 SSE
                        logger.warning(
                            "bridge: assistant persist failed", exc_info=True)
                _push_sse("agent_done", {
                    "message_id": mid, "status": "completed",
                }, session_id)
                return
            if kind == "run.failed" or (
                kind == "run.settled" and data.get("fenced")
            ):
                mid = str(data.get("run_id") or "")
                reason = str(data.get("error") or data.get("reason") or "")
                _push_sse("error", {
                    "message_id": mid, "error": reason,
                }, session_id)
                _push_sse("agent_done", {
                    "message_id": mid, "status": "error",
                }, session_id)
                return

        out_kind = mapping.get(kind)
        if out_kind is None:
            # text_delta / thinking_delta / thinking_done / stream.failed 直通
            if kind in ("text_delta", "thinking_delta", "thinking_done", "stream.failed"):
                out_kind = kind
            else:
                return
        payload = dict(data)
        mid = message_id_holder.get("id")
        if mid:
            payload.setdefault("message_id", mid)
        # bridge 在引擎执行线程内被调用：SSE 故障绝不能中断对话
        try:
            _push_sse(out_kind, payload, session_id)
        except Exception:  # noqa: BLE001
            logger.debug("sse bridge push failed: %s", out_kind, exc_info=True)

    # R7：注册到集合（不再链式累积），把注销函数交给 bridge 自用。
    # 优先 add_sink；鸭子类型替身只有 install_sink 时退回（返回 None，
    # 自注销退化为 no-op——测试路径无泄漏）。
    adder = getattr(gw, "add_sink", None)
    if callable(adder):
        state["unsubscribe"] = adder(bridge)
    else:
        gw.install_sink(bridge)
    return _close


@router.post("/send")
async def send_sync(body: ChatMessage, request: Request):
    """Send a message synchronously (non-streaming).

    Two paths (阶段 3a 灰度):
    - legacy (default): unified SessionService (event-log persistence, FIFO
      queue, conversation-history context).
    - new (QNODES_NEW_ENTRY=1): WorkRequest -> EntryPoint -> mailbox ->
      SessionController -> GraphEngine, events collected in-process.

    The ownership check is shared by both paths.
    """
    # Ownership check: only the session owner may post messages
    from .web_session import _fetch_session_owned, _get_db
    user_id = getattr(request.state, "user_id", "anonymous")
    _fetch_session_owned(_get_db(), body.session_id, user_id)

    # ── 阶段 3a 灰度分支（QNODES_NEW_ENTRY=1 时走新架构） ──────────
    from strategy_research.core.gateway import new_entry_enabled
    if new_entry_enabled():
        # R2.1c（E1=C）：?wait=true 保留同步 reply 语义（外部脚本用户）；
        # 默认 receipt 型——submit 即回，答案走 SSE。
        wait = (request.query_params.get("wait") or "").strip().lower() in (
            "1", "true", "yes",
        )
        return await _send_via_gateway(body.session_id, body.content, wait=wait)

    service = _get_session_service()
    result = await service.send_message(
        session_id=body.session_id,
        content=body.content,
        allow_shell_tools=_shell_tools_enabled(),
    )
    if result.get("error") == "queue_full":
        raise HTTPException(status_code=429, detail=result)

    attempt_id = result.get("attempt_id")
    outcome = await service.wait_for_attempt(
        body.session_id, attempt_id,
    ) if attempt_id else None
    if outcome is None:
        raise HTTPException(
            status_code=504, detail="LLM response timed out",
        )
    return {
        "message_id": result.get("message_id"),
        "attempt_id": attempt_id,
        "reply": outcome.get("summary", ""),
        "status": outcome.get("status"),
    }


@router.get("/events")
async def chat_events(
    session_id: str = Query(...),
    token: Optional[str] = Query(None),
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
    last_event_id_query: Optional[str] = Query(None, alias="Last-Event-ID"),
    request: Request = None,
):
    """SSE event stream for a session."""
    from fastapi import HTTPException as _HTTPExc

    from .web_session import _fetch_session_owned, _get_db, _get_db_path
    user_id = getattr(request.state, "user_id", "anonymous")
    try:
        _fetch_session_owned(_get_db(), session_id, user_id)
    except _HTTPExc as exc:
        # Auto-recreate bare study_id session on 404 so SSE works
        # even after the session row was deleted or never created.
        if exc.status_code == 404 and session_id.startswith("study_"):
            try:
                from ...core.study.engine_common import ensure_study_session
                ensure_study_session(
                    _get_db_path(), session_id,
                    f"Study: {session_id}",
                )
                _fetch_session_owned(_get_db(), session_id, user_id)
            except Exception:
                raise exc from exc
        else:
            raise
    resolved_last_event_id = last_event_id or last_event_id_query or ""
    logger.info("[SSE] client connected session=%s last_event_id=%s", session_id, resolved_last_event_id)

    # A2: single track — EventStore.subscribe() (replay + live). The old
    # legacy in-process SSE buffer was retired; its ring-buffer
    # semantics (5min / 10k) are strictly weaker than the event store's
    # persistent log.
    return StreamingResponse(
        _event_generator_v2(session_id, resolved_last_event_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/attempts", response_model=ChatAttemptsResponse)
async def list_active_attempts(
    session_id: str = Query(...),
    request: Request = None,
):
    """Non-terminal attempts for a session (reload recovery).

    The frontend calls this after loading messages to rebuild its
    streaming/queued state: a page reload loses ``streamingMessageId``
    and the in-memory queued placeholders, and SSE replay cannot
    recover ``attempt.started`` / ``message_received`` (they sit before
    the Last-Event-ID cursor). See docs/streaming-reload-recovery.md.

    Returns ``{"attempts": [...]}`` with each entry:
    ``{attempt_id, message_id, status: "running"|"queued", prompt,
    created_at}`` — oldest first. Zombie rows (server restart) are
    filtered out by in-memory guards inside SessionService.
    """
    from .web_session import _fetch_session_owned, _get_db
    user_id = getattr(request.state, "user_id", "anonymous")
    _fetch_session_owned(_get_db(), session_id, user_id)
    service = _get_session_service()
    return {"attempts": service.list_active_attempts(session_id)}


@router.get("/session/{session_id}/available_actions")
async def chat_available_actions(session_id: str, request: Request):
    """C3: actions the current session state permits (drives the UI's buttons)."""
    from .web_session import _fetch_session_owned, _get_db
    user_id = getattr(request.state, "user_id", "anonymous")
    _fetch_session_owned(_get_db(), session_id, user_id)
    service = _get_session_service()
    return {
        "status": "ok",
        "session_id": session_id,
        "actions": service.get_available_actions(session_id),
    }


@router.get("/session/{session_id}/export")
async def chat_export(
    session_id: str,
    request: Request,
    format: str = Query("markdown", pattern="^(markdown|json)$"),
):
    """C4: export chat history as markdown or JSON.

    Returns the formatted content as a plain-text response (not JSON)
    so the browser can offer a download.
    """
    from fastapi.responses import PlainTextResponse

    from .web_session import _fetch_session_owned, _get_db
    user_id = getattr(request.state, "user_id", "anonymous")
    _fetch_session_owned(_get_db(), session_id, user_id)
    service = _get_session_service()
    messages = service.store.get_messages(session_id, limit=10000)

    if format == "json":
        import json
        data = [
            {
                "role": m.role,
                "content": m.content,
                "message_type": m.message_type,
                "created_at": m.created_at,
                "message_id": m.message_id,
            }
            for m in messages
        ]
        return PlainTextResponse(
            json.dumps(data, ensure_ascii=False, indent=2),
            media_type="application/json",
        )

    # Markdown format
    lines: list[str] = []
    for m in messages:
        if m.message_type == "compaction":
            lines.append(f"\n---\n*{m.content[:200]}*\n---\n")
            continue
        if m.message_type == "error":
            lines.append(f"**Error:** {m.content}\n")
            continue
        if m.role == "user":
            lines.append(f"**User:** {m.content}\n")
        elif m.role == "assistant":
            lines.append(f"**Assistant:** {m.content}\n")
        else:
            lines.append(f"**{m.role.title()}:** {m.content}\n")
    return PlainTextResponse(
        "\n".join(lines),
        media_type="text/plain; charset=utf-8",
    )


@router.get("/session/{session_id}/trace")
async def get_session_trace(
    request: Request,
    session_id: str,
    limit: int = Query(100, ge=1, le=500),
    types: str | None = Query(None, description="Comma-separated event types to filter"),
):
    """Read trace events for a session (DSH session-query pattern).

    A2: the event_log is the single source of truth. ``llm_request``
    events are projected out of it (TraceProjection), reconstructing large
    offloaded fields (system_prompt, tools_schema) from their sidecar
    blobs. trace.jsonl is used only as a backward-compat fallback for
    sessions that predate A1 (no llm_request events in the event_log).

    Security (P5-5②): ownership enforced — a session's trace contains the
    full LLM trajectory (system_prompt, tool schemas); previously ANY
    caller could read ANY session's trace. 404/403 per _fetch_session_owned.
    """
    from ...core.agent.trace import TraceWriter
    from ..session.trace_projection import TraceProjection
    from .web_session import _fetch_session_owned, _get_db
    user_id = getattr(request.state, "user_id", "anonymous")
    _fetch_session_owned(_get_db(), session_id, user_id)

    type_filter = set(types.split(",")) if types else None

    # Primary: project from event_log (single source of truth).
    try:
        service = _get_session_service()
        events = TraceProjection(service.event_bus).project(
            session_id, limit=limit, types=types,
        )
        if events:
            return {
                "events": events,
                "session_id": session_id,
                "total": len(events),
                "source": "event_log",
            }
    except Exception:
        logger.exception("TraceProjection failed for session %s", session_id)

    # Fallback: legacy trace.jsonl (backward compat for pre-A2 sessions).
    trace_dir = TraceWriter.find_trace_dir(session_id)
    if trace_dir is None:
        return {"events": [], "session_id": session_id}

    try:
        records = TraceWriter.read(
            trace_dir,
            resolve_offloads=True,
            resolve_fields={"system_prompt", "tools_schema"},
        )
        if type_filter:
            records = [r for r in records if r.get("type") in type_filter]
        records = records[-limit:]
        return {
            "events": records,
            "session_id": session_id,
            "total": len(records),
            "source": "trace.jsonl",
        }
    except Exception:
        logger.exception("Failed to read trace for session %s", session_id)
        return {"events": [], "session_id": session_id, "error": "failed to read trace"}


@router.get("/personas", response_model=ChatPersonasResponse)
async def list_personas(request: Request = None):
    """List available chat personas (roles) for the Composer agent selector.

    Returns ``{"personas": [{"id", "name", "description"}, ...]}`` — the
    curated set of role prompts the composer may switch to. ``chat`` is the
    default (general) persona.
    """
    from strategy_research.core.agent.prompt_builder import PromptBuilderFactory

    labels = {
        "chat": ("通用助手", "默认聊天助手，适合日常问答与策略讨论"),
        "researcher": ("研究员", "深度资料调研与信息检索"),
        "strategist": ("策略师", "策略设计与回测分析"),
        "factor_analyst": ("因子分析师", "因子计算与有效性分析"),
        "data_quality": ("数据质量", "数据质量与来源核查"),
        "portfolio_construction": ("组合构建", "组合权重与配置优化"),
        "risk_controller": ("风控", "风险度量与回撤控制"),
        "attribution_analyst": ("归因分析", "收益与风险归因"),
        "anti_overfit_analyst": ("反过拟合", "过拟合诊断与稳健性检验"),
        "backtest_diagnostics": ("回测诊断", "回测结果核查与诊断"),
        "critic": ("评审", "对结论与策略进行批判性审查"),
        "workflow_orchestrator": (
            "编排助手",
            "DAG 编排专用：把任务拆解为节点并通过 submit_dag_step 提交；固定用于 dag:{name} 会话",
        ),
    }
    persona = PromptBuilderFactory.list_roles()
    personas = []
    for pid in persona:
        name, desc = labels.get(pid, (pid, ""))
        personas.append({"id": pid, "name": name, "description": desc})
    return {"personas": personas}


async def _event_generator_v2(
    session_id: str,
    last_id: str,
):
    """SSE generator backed by the single EventStore track (A2).

    Replaces the legacy in-process buffer track: ``EventStore.subscribe()`` already
    provides cache replay -> SQLite tail replay -> live queue, which is a
    superset of the old ring buffer (persistent across restarts, not
    limited to 5 minutes / 10k events).

    ``last_id`` accepts both forms the frontend may echo back: the legacy
    ``evt_<n>`` string (ignored — EventSource just replays what the event
    store has) and the EventV2 UUID (used for Last-Event-ID recovery).
    """
    logger.info("[SSE] generator (v2) started session=%s", session_id)
    yield ": connected\n\n"
    yield "retry: 3000\n\n"

    # P0 修复（2026-10-31）：SessionService 上承载 EventStore 的属性是
    # `event_bus`（见 service.py:70 docstring「event_bus: EventStore for
    # event_log + SSE + projector flush」）。此前写成 `event_store`——
    # 属性不存在，SSE 流在首轮 resume 时即 AttributeError：WebUI 实时
    # 时间线全坏，且客户端挂着连接时整个响应任务崩掉。被 PR 门排除的
    # test_sse_e2e 首次完整运行暴露（docs/known-bug-sse-send-deadlock）。
    store = _get_session_service().event_bus
    event_count = 0
    emitted_id = ""
    try:
        # P0 修复之二（2026-10-31）：心跳改为**时间制**——SSE 保活语义
        # 要求「15s 无活动必须发心跳」。超时逻辑在 subscribe 内部的
        # queue.get 上（见 EventStore.subscribe keepalive_after）——在
        # 端点层对 __anext__ 套 wait_for 会杀死整条流。
        async for event in store.subscribe(
            session_id, keepalive_after=_SSE_HEARTBEAT_INTERVAL_S,
        ):
            if event is None:  # keepalive tick → 心跳 comment 帧
                event_count += 1
                yield _heartbeat_sse(event_count)
                continue

            # Last-Event-ID recovery: skip everything up to and including
            # the id the client already saw.
            if last_id and event.id != last_id and not emitted_id:
                continue
            if last_id and event.id == last_id:
                emitted_id = last_id
                continue

            yield _format_event_v2(event)
            emitted_id = event.id
            event_count += 1
    except asyncio.CancelledError:
        logger.info("[SSE] client disconnected session=%s events=%d",
                    session_id, event_count)
    except Exception as exc:  # noqa: BLE001 — defensive catch-and-raise
        logger.error("[SSE] generator (v2) error session=%s: %s", session_id, exc)
        raise
    finally:
        logger.info("[SSE] generator (v2) ended session=%s total_events=%d",
                    session_id, event_count)


def _format_event_v2(event) -> str:
    """Format an EventV2 as an SSE frame.

    Field mapping (EventV2 -> SSE): ``id`` -> id, ``type`` -> event,
    ``data`` -> JSON payload. The frontend receives the same wire shape
    it did on the legacy buffer track.
    """
    lines = []
    if getattr(event, "id", None):
        lines.append(f"id: {event.id}")
    event_type = getattr(event, "type", "") or ""
    if event_type:
        lines.append(f"event: {event_type}")
    payload = getattr(event, "data", None) or {}
    try:
        data = json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        data = json.dumps({})
    for line in data.split("\n"):
        lines.append(f"data: {line}")
    return "\n".join(lines) + "\n\n"

def _heartbeat_sse(count: int) -> str:
    """SSE comment-line keep-alive (opencode style).

    Pure comment lines are ignored by the browser's EventSource
    (no onerror, no onmessage) but they keep the TCP connection alive
    and re-arm the browser's 3-minute idle timeout. See
    opencode packages/server/src/handlers/event.ts:37 for the
    reference implementation.
    """
    return ": heartbeat\n\n"


# P0 修复之二：时间制心跳间隔（秒）。与 test_sse_e2e 的 15s 等待、
# EventSource 的 3 分钟空闲超时对齐——空闲 15s 必发保活帧。
_SSE_HEARTBEAT_INTERVAL_S = 15.0
