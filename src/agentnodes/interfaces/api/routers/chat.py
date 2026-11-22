"""Chat API — send message + SSE event stream with real LLM integration。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from typing import TYPE_CHECKING, Any, Callable, Optional

from agentnodes.foundation.config import settings as _settings

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

if TYPE_CHECKING:
    from agentnodes.kernel.permission import PermissionGateway

from agentnodes.interfaces.api.schemas.chat import (
    CancelRequest,
    ChatAttemptsResponse,
    ChatCancelResponse,
    ChatPersonasResponse,
    ChatQueueResumeResponse,
    SendMessageResponse,
)
from agentnodes.interfaces.api.schemas.chat import (
    ChatMessageRequest as ChatMessage,
)
from agentnodes.kernel.session.models import (
    Attempt,
    AttemptId,
    AttemptStatus,
    MessageId,
    SessionId,
)
from agentnodes.kernel.orchestration import QueueMode, RejectReason
from agentnodes.kernel.orchestration.session_keys import make_session_key
from agentnodes.kernel.session.service import SessionService
from agentnodes.interfaces.api.routers.slash_common import (
    _handle_clear_command,
    _handle_compact_command,
    _handle_help_command,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _shell_tools_enabled(mode: str | None = None) -> bool:
    """Whether the ``run_command`` shell tool should be offered to the agent.

    2026-11-05：策略本体已下沉到 L2 ``apps/chat/policy.py``——L1 是哑
    适配器，不该读环境变量做安全决策。Gateway 组合根对工具注册表
    应用相同策略；这里保留薄壳以兼容已有调用方。
    """
    from agentnodes.apps.chat.policy import shell_tools_enabled

    return shell_tools_enabled(mode)


def _gateway_chat_options(body: ChatMessage) -> dict[str, Any]:
    """Translate legacy API options into Gateway per-turn execution options."""
    from agentnodes.kernel.session.loop_events import (
        _plan_mode_allowed_tools,
        _resolve_loop_prompt,
        _thinking_instructions,
    )

    mode = (body.mode or "build").strip().lower()
    system_prompt, role = _resolve_loop_prompt(None, body.agent_id)
    thinking = body.thinking or "auto"
    instructions = _thinking_instructions(thinking)
    if instructions and system_prompt is not None:
        system_prompt += instructions
    return {
        "mode": mode,
        "system_prompt": system_prompt,
        "role": role,
        "allowed_tools": _plan_mode_allowed_tools(mode),
        "thinking": thinking,
        "model": body.model,
    }


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
# 第二轮候选 4：缓存单点已搬到 composition._SERVICE_CACHE；此处保留别名
# 过渡（测试锚仍可 .clear()），下版删除（与 M11b 同款标注）。
from agentnodes.interfaces.api.composition import _SERVICE_CACHE as _session_service_cache  # noqa: F401

# ── Tier 1 A1: permission gateway ──────────────────────────────────
# 第四轮 C3：进程级 PermissionGateway 单例与 SSE 推送 helper 归
# composition 单一所有（``get_permission_gateway`` / ``_push_event`` /
# ``_safe_payload``）；此处经模块级 import 保持既有调用面与测试锚
# （chat_mod._push_event 等）——名字仍是可 patch 的模块全局。
from agentnodes.interfaces.api.composition import (  # noqa: E402,F401
    _push_event,
    _safe_payload,
)


def _get_session_service() -> SessionService:
    """SessionService 取用（过渡 re-export：下版删，改调 composition 单点）。

    实现已搬到 ``composition.session_service_from``（第二轮候选 4）——缓存
    单点 + 取用 seam 归 composition root。本名保留供 router/测试锚过渡。
    """
    from agentnodes.interfaces.api.composition import session_service_from

    return session_service_from()


# ── LLM Config ────────────────────────────────────────────────────────


def _build_llm_config():
    """Build LLMConfig. 唯一构造入口是 ``LLMConfig.load()``。

    **2026-11-16 收敛**：此处原有一层 ``load_dotenv(override=True)``，已删除。理由：

    1. **冗余** —— ``LLMConfig.load()`` 内部的 ``_try_load_dotenv()`` 已经加载
       cwd/.env 与 ``~/.agentnodes/.env``（config.py:345-370），实测「只调 load()
       就能拿到 api_key」。
    2. **有害** —— ``override=True`` 会**覆盖进程里显式 export 的 API key**。
       实测：进程有值时 override=True 用文件值、override=False 保留用户值。
       按 R-006「显式 > 文件」的精神，用户显式设置应当赢。

    那层是早年从 TUI 启动代码（``cli/__main__.py``）复制来的 —— CLI 启动场景
    需要先载入密钥，语义与 web 服务不同。

    保留本函数（而非让调用方各自 ``LLMConfig.load()``）是为了保留这里
    「load 失败 → 返回 None，不抛异常」的兜底语义。
    """
    try:
        from agentnodes.foundation.llm.config import LLMConfig

        return LLMConfig.load()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load LLMConfig: %s", exc)
        return None








@router.post("/send_async", response_model=SendMessageResponse)
async def send_async(body: ChatMessage, request: Request):
    """Send a message asynchronously. Enqueues into per-session FIFO queue.

    Returns message_id + event_id for confirmation.
    Frontend should listen to /events SSE stream for real-time response.

    Submits a FOLLOWUP work item to the shared Gateway mailbox. New messages
    are processed FIFO; if 10 items are already pending, the API returns 429.
    SessionService remains responsible for persisted session and Attempt data.
    """
    logger.info("[SEND] session=%s content_len=%d content_preview=%s",
                body.session_id, len(body.content), body.content[:50])

    # Ownership check: only the session owner may post messages
    from agentnodes.interfaces.api.authz import enforce_session_owned
    enforce_session_owned(request, body.session_id)

    # ── /goal command intercept 已随 apps/goal 移除（2026-11-04 批次 2）──
    # ── /study command intercept 已随 apps/study 移除（2026-11-05 批次 3）──

    # ── /compact command intercept ──────────────────────────────────────
    if body.content.strip() == "/compact":
        return await _handle_compact_command(body)

    # ── /clear command intercept (webui only — clears LLM-visible
    # history while preserving the persisted message log). Returns
    # immediately without invoking the LLM. The TUI does not use this
    # path; equivalent TUI behaviour is `clear_session`. ─────────────
    if body.content.strip() == "/clear":
        from agentnodes.interfaces.api.dependencies import get_memory_manager

        return await _handle_clear_command(
            body,
            memory_manager=get_memory_manager(request),
        )

    # ── /help command intercept — returns a static cheat-sheet text
    # in a synthetic assistant message so the user sees it in context.
    # Skips LLM invocation. ──────────────────────────────────────────
    if body.content.strip() == "/help":
        return await _handle_help_command(body)

    service = _get_session_service()

    # 3a.3b（决策 5）：先验证严格历史，再创建 Attempt/发 attempt.created——
    # 历史失败时零 Attempt 事件（消除原「先发事件后删行」半提交路径）。
    from agentnodes.apps.chat import send_turn as usecase

    try:
        prevalidated_history = usecase.precheck_history(
            usecase.SendTurnInput(session_id=body.session_id, content=body.content),
            usecase.HistoryDeps(
                load_history=service.load_history,
                build_llm_config=_build_llm_config,
            ),
        )
    except usecase.HistoryUnavailable as exc:
        raise HTTPException(status_code=500, detail=exc.detail) from exc
    import uuid

    user_message_id = str(uuid.uuid4())
    assistant_message_id = str(uuid.uuid4())
    attempt_id = uuid.uuid4().hex[:12]
    options = _gateway_chat_options(body)
    attempt = Attempt(
        attempt_id=AttemptId(attempt_id),
        session_id=SessionId(body.session_id),
        prompt=body.content,
        message_id=MessageId(assistant_message_id),
        status=AttemptStatus.PENDING,
        persona=body.agent_id,
        mode=options["mode"],
        model_override=body.model,
        thinking=options["thinking"],
    )
    service.store.create_attempt(attempt)
    try:
        service.event_bus.emit(
            body.session_id,
            "attempt.created",
            {"attempt_id": attempt_id, "prompt": body.content},
        )
    except Exception:  # noqa: BLE001 — event-log failure must not block admission
        logger.warning("send_async: attempt.created persistence failed", exc_info=True)

    admitted = False
    try:
        await _send_via_gateway(
            body.session_id,
            body.content,
            queue_mode=QueueMode.FOLLOWUP,
            max_pending=10,
            user_message_id=user_message_id,
            attempt_id=attempt_id,
            assistant_message_id=assistant_message_id,
            receipt_only=True,
            # 重复 A 修复：预检结果透传给 send_turn，跳过二次历史读取。
            # 只在本请求内刚预检过的结果才传入。
            prevalidated_history=prevalidated_history,
            **options,
        )
        admitted = True
    finally:
        if not admitted:
            service.store.delete_attempt(attempt_id)

    gateway = _get_gateway()
    live_state = gateway.attempt_state(body.session_id, attempt_id)
    persisted_attempt = service.store.get_attempt(body.session_id, attempt_id)
    status = "queued" if live_state == "queued" or (
        persisted_attempt is not None
        and persisted_attempt.status == AttemptStatus.PENDING
    ) else "processing"
    mailbox = gateway.entry.mailbox_for(make_session_key("chat", body.session_id))
    queue_length = mailbox.peek_depth() if mailbox is not None else 0
    try:
        service.event_bus.emit(body.session_id, "message_received", {
            "message_id": user_message_id,
            "user_message_id": user_message_id,
            "assistant_message_id": assistant_message_id,
            "role": "user",
            "content": body.content,
            "attempt_id": attempt_id,
            "status": status,
            "queue_position": queue_length if status == "queued" else 1,
            "queue_length": queue_length,
            "created_at": time.time(),
        })
    except Exception:  # noqa: BLE001 — persistence failure must not reject accepted work
        logger.warning("send_async: message_received persistence failed", exc_info=True)
    try:
        service.event_bus.emit(body.session_id, "queue_state", {
            "session_id": body.session_id,
            "queue_length": queue_length,
        })
    except Exception:  # noqa: BLE001 — queue observability must not reject accepted work
        logger.warning("send_async: queue_state persistence failed", exc_info=True)

    logger.info(
        "[SEND] ok session=%s user_msg=%s attempt=%s status=%s",
        body.session_id, user_message_id, attempt_id, status,
    )

    return SendMessageResponse(
        message_id=user_message_id,
        user_message_id=user_message_id,
        assistant_message_id=assistant_message_id,
        event_id="",
        status=status,
        attempt_id=attempt_id,
    )


@router.post("/cancel", response_model=ChatCancelResponse)
async def cancel_attempt(body: CancelRequest, request: Request):
    """Cancel an in-flight agent attempt for a session.

    If attempt_id is provided, cancels that specific attempt.
    Otherwise, cancels any active attempt for the session.

    After cancellation the Gateway mailbox is **paused** (queue_paused
    SSE event). Use ``POST /chat/queue/resume`` to continue processing.
    """
    from agentnodes.interfaces.api.authz import enforce_session_owned
    enforce_session_owned(request, body.session_id)
    gateway = _get_gateway()

    if body.attempt_id and (
        gateway.attempt_state(body.session_id, body.attempt_id) == "running"
    ):
        ok = gateway.cancel_session(body.session_id)
        if ok:
            return {
                "status": "cancelled",
                "session_id": body.session_id,
                "attempt_id": body.attempt_id,
            }

    ok = gateway.cancel_session(body.session_id)
    if ok:
        return {"status": "cancelled", "session_id": body.session_id}

    return {"status": "no_active_attempt", "session_id": body.session_id}


class QueueResumeRequest(BaseModel):
    session_id: str


@router.post("/queue/resume", response_model=ChatQueueResumeResponse)
async def queue_resume(body: QueueResumeRequest, request: Request):
    """Resume a paused Gateway mailbox after an explicit cancel.

    Returns ``{"ok": true, "session_id": ...}`` if the queue was paused
    and is now resumed; ``{"ok": false}`` if no paused queue exists.
    """
    from agentnodes.interfaces.api.authz import enforce_session_owned
    enforce_session_owned(request, body.session_id)
    ok = _get_gateway().resume_session(body.session_id)
    return {"ok": ok, "session_id": body.session_id}


# ── 阶段 3a：新入口路径（灰度） ──────────────────────────────


def _get_gateway():
    """进程内唯一 Gateway（见 core/gateway/singleton.py 的串行域说明）。

    **W3**：``stream=True``——/send 的打字机流式。AgentLoop 适配器在
    client 无 ``stream``/``astream`` 时自动降级为非流式（测试假件不受
    影响）；生产 client（OpenAICompatClient）四件套齐全。
    """
    from agentnodes.kernel.gateway import get_gateway

    return get_gateway(stream=True)


def _install_answer_persister(gw: Any) -> None:
    """把 ``run.answer`` 的 assistant_message 落库收归**进程单例观察器**。

    批1-S2（三前端一核心 · STEER 单次通知，A1+B2 裁决）：此前三个 HTTP
    落库点（receipt 桥 / sync 分支 / STEER 跟随桥）各自 emit——STEER 被
    吸收时两个同会话桥对同一 ``run.answer`` 分别落库，transcript 出现
    重复 assistant 行。收归单点后：

    - 每次成功的 run 恰好落库一次（按 run_id 去重，防桥内外重复触发）；
    - **外部启动 run**（消费者驱动、无 HTTP 桥）与**原桥超时**场景天然
      覆盖——落库不依赖任何桥存活；
    - 各等待方连接的通知/流式转发保持不变（A1：多等待方各自得到结果）。

    落库失败只记 warning，不阻断事件流与各等待方 SSE。

    DEPRECATED（2026-11-19，3a.4 裁决）：同一观察器已下沉为
    apps/chat/send_turn.py::_install_answer_persister，生产零调用；
    **下一版本删除**，本版本保留兼容（单例语义不变）。
    """
    if getattr(gw, "_answer_persister_installed", False):
        return
    seen_run_ids: set[str] = set()

    def _persist_answer(kind: str, data: dict) -> None:
        if kind != "run.answer":
            return
        run_id = str(data.get("run_id") or "")
        # 归属键 = business_session_id（controller 剥掉 chat:/automation:
        # kind 前缀后的物理 session id）。run_meta["session_id"] 是 EntryPoint
        # 回执 id（UUID），不能用于持久化归属。
        session_id = str(data.get("business_session_id") or "")
        if not run_id or not session_id or run_id in seen_run_ids:
            return
        seen_run_ids.add(run_id)
        try:
            service = _get_session_service()
            service.event_bus.emit(session_id, "assistant_message", {
                "message_id": str(data.get("assistant_message_id") or run_id),
                "role": "assistant",
                "content": str(data.get("content") or ""),
                "created_at": time.time(),
            })
        except Exception:  # noqa: BLE001 —— 落库失败不吞事件流
            logger.warning("answer persister: assistant persist failed", exc_info=True)

    gw.add_sink(_persist_answer)
    gw._answer_persister_installed = True


async def _send_via_gateway(
    session_id: str,
    content: str,
    *,
    wait: bool = False,
    mode: str | None = None,
    queue_mode: QueueMode | None = None,
    max_pending: int | None = None,
    user_message_id: str | None = None,
    attempt_id: str | None = None,
    assistant_message_id: str | None = None,
    model: str | None = None,
    system_prompt: str | None = None,
    role: str | None = None,
    allowed_tools: list[str] | None = None,
    thinking: str | None = None,
    receipt_only: bool = False,
    prevalidated_history: list[dict] | None = None,
) -> dict:
    """L1 薄适配（3a.3b）：构造 apps.send_turn 的输入/依赖，调用用例，
    并把应用层错误映射为既有 HTTP status/detail。

    签名保留为过渡面（3a.0 基线冻结的 17 处 monkeypatch 锚继续生效）；
    3a.4 再逐项关闭。RunResult/事件 wire 由 apps 用例保证逐字不变。

    DEPRECATED（2026-11-19，3a.4 裁决）：本 wrapper 是 send-turn 下沉的
    过渡面，生产调用已全部迁至 apps/chat.send_turn。**下一版本删除**
    （与批 2 死代码同批次协调）；本版本只标注、不改行为。测试锚按
    R-016 出口网逐项迁移，迁移完成前保留此面。
    """
    from agentnodes.apps.chat import send_turn as usecase

    service = _get_session_service()
    gw = _get_gateway()
    inp = usecase.SendTurnInput(
        session_id=session_id, content=content, wait=wait, mode=mode,
        queue_mode=queue_mode, max_pending=max_pending,
        user_message_id=user_message_id, attempt_id=attempt_id,
        assistant_message_id=assistant_message_id, model=model,
        system_prompt=system_prompt, role=role, allowed_tools=allowed_tools,
        thinking=thinking, receipt_only=receipt_only,
        prevalidated_history=prevalidated_history,
    )
    deps = usecase.SendTurnDeps(
        gateway=gw,
        # C2：命名 port——组合根绑定 kernel 公开面（attempt 状态机归 kernel）
        load_history=service.load_history,
        update_attempt_status=service.update_attempt_status,
        # 延迟取 event_bus.emit：严格历史失败路径在 send_turn 内先预检，
        # 不触达 emit——fake service 可无 event_bus（与旧实现一致）。
        emit=lambda sid, kind, data: service.event_bus.emit(sid, kind, data),
        publish_sse=_push_sse,
        build_llm_config=_build_llm_config, wait_timeout=_chat_wait_timeout,
    )
    error_map = {
        usecase.HistoryUnavailable: 500,
        usecase.QueueFull: 429,
        usecase.EntryRejected: 400,
        usecase.WaitTimeout: 504,
        usecase.NoRunExecuted: 504,
        usecase.MissingRunTerminal: 502,
        usecase.MissingRunId: 502,
        usecase.RequestSummarized: 502,
        usecase.RunFailed: 502,
    }
    try:
        result = await usecase.send_turn(inp, deps)
    except Exception as exc:
        status = None
        for etype, code in error_map.items():
            if isinstance(exc, etype):
                status = code
                break
        if status is None:
            raise
        detail = exc.detail if isinstance(exc, usecase.SendTurnError) else str(exc)
        raise HTTPException(status_code=status, detail=detail) from exc
    return result.as_dict()


def _push_sse(kind: str, data: dict, session_id: str) -> None:
    """把一条事件推入 SSE 缓冲（失败只记日志，不影响主路径）。"""
    try:
        _push_event(session_id, kind, json.dumps(data, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        logger.debug("sse push failed (%s): %s", kind, exc)


def _chat_wait_timeout() -> float:
    """wait=true 的等待上限（秒）。turn 可能很长，默认 300s。"""
    raw = _settings.get_str("AGENTNODES_CHAT_WAIT_TIMEOUT").strip()
    try:
        return float(raw) if raw else 300.0
    except ValueError:
        return 300.0


def _install_chat_event_bridge(
    gw: Any, session_id: str, message_id_holder: dict[str, str],
    *, service: Any = None, persist: bool = False,
    seed_user: dict | None = None,
    event_correlation_id: str | None = None,
    terminal_observer: Callable[[str, dict], None] | None = None,
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

    DEPRECATED（2026-11-19，3a.4 裁决）：本桥已下沉为
    apps/chat/send_turn.py::_event_bridge 内部 seam，生产零调用；
    **下一版本删除**（测试 import/patch 锚先迁走），本版本保留兼容。
    """
    # 3a.2：引擎→产品事件映射与流式直通集合收归 apps/chat.events 单点
    # （词表值从 kernel EventType 取，wire 名绝对值不变）。
    from agentnodes.apps.chat.events import (
        ENGINE_TO_PRODUCT,
        STREAM_PASSTHROUGH_KINDS,
    )

    mapping = ENGINE_TO_PRODUCT
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
        if (
            event_correlation_id is not None
            and kind == "steer.applied"
            and event_correlation_id in data.get("applied_event_correlations", ())
        ):
            state["followed_correlation_id"] = data["event_correlation_id"]
            _bridge_body("run.started", data)
            return
        if event_correlation_id is not None:
            correlation = data.get("event_correlation_id")
            followed = state.get("followed_correlation_id")
            if correlation != event_correlation_id and (not followed or correlation != followed):
                return
        try:
            if terminal_observer is not None:
                terminal_observer(kind, data)
            _bridge_body(kind, data)
        finally:
            # 收尾事件 → 本 turn 结束 → 注销，防累积/重复推送
            if kind == "work.summarized":
                _close()
            elif terminal_observer is None and kind in (
                "run.answer", "run.failed", "run.cancelled", "run.fenced",
            ):
                _close()
            elif kind == "run.settled" and (
                terminal_observer is not None
                or data.get("fenced") or not data.get("ok", True)
            ):
                _close()

    def _update_attempt(
        data: dict,
        status: str,
        *,
        summary: str | None = None,
        error: str = "",
    ) -> None:
        attempt_id = data.get("attempt_id")
        if service is None or not isinstance(attempt_id, str) or not attempt_id:
            return
        try:
            attempt = service.store.get_attempt(session_id, attempt_id)
            if attempt is None:
                logger.warning("Gateway event references missing Attempt %s", attempt_id)
                return
            if status == "running":
                attempt.mark_running()
            elif status == "completed":
                attempt.mark_completed(summary)
            elif status == "cancelled":
                attempt.mark_cancelled()
            elif status == "failed":
                attempt.mark_failed(error or "Gateway run failed")
            else:
                raise ValueError(f"unsupported Attempt status: {status}")
            service.store.update_attempt(attempt)
            # 3a.3 候选3：attempt.* 产品事件名从单一事实源取（wire 零变化）
            from agentnodes.apps.chat.events import ChatEvent

            lifecycle_event = {
                "running": (ChatEvent.ATTEMPT_STARTED, {
                    "attempt_id": attempt_id,
                    "message_id": str(attempt.message_id or ""),
                }),
                "completed": (ChatEvent.ATTEMPT_COMPLETED, {
                    "attempt_id": attempt_id,
                    "status": "completed",
                    "summary": summary or "",
                }),
                "cancelled": (ChatEvent.ATTEMPT_FAILED, {
                    "attempt_id": attempt_id,
                    "status": "cancelled",
                }),
                "failed": (ChatEvent.ATTEMPT_FAILED, {
                    "attempt_id": attempt_id,
                    "status": "error",
                    "error": error or "Gateway run failed",
                }),
            }[status]
            try:
                service.event_bus.emit(
                    session_id,
                    lifecycle_event[0],
                    lifecycle_event[1],
                )
            except Exception:  # noqa: BLE001 — lifecycle event failure is observable
                logger.warning(
                    "Gateway Attempt event failed for %s", attempt_id, exc_info=True,
                )
        except Exception:  # noqa: BLE001 — bookkeeping must not stop chat delivery
            logger.warning(
                "Gateway Attempt update failed for %s", attempt_id, exc_info=True,
            )

    def _bridge_body(kind: str, data: dict) -> None:
        if kind == "work.summarized":
            error = "queued request summarized before execution"
            _update_attempt(data, "failed", error=error)
            if persist:
                mid = str(
                    data.get("assistant_message_id") or (seed_user or {}).get("message_id") or ""
                )
                _push_sse("error", {"message_id": mid, "error": error}, session_id)
                _push_sse("agent_done", {"message_id": mid, "status": "error"}, session_id)
            return
        if kind == "run.started":
            _update_attempt(data, "running")
        elif kind == "run.answer":
            _update_attempt(
                data, "completed", summary=str(data.get("content") or ""),
            )
        elif kind == "run.failed":
            _update_attempt(data, "failed", error=str(data.get("error") or ""))
        elif kind == "run.cancelled":
            _update_attempt(data, "cancelled")
        elif kind == "run.fenced":
            _update_attempt(data, "failed", error=str(data.get("reason") or ""))

        # 在 run.started 就把 assistant message ID 写入 holder——此后的
        # 流式事件（text_delta / thinking_delta / tool_call）经 setdefault 拿到
        # message_id，前端按 text_id/think_id 路由的 handlers 才能落件。
        # 此前 holder 到 run.answer 才置，整个流式阶段的 chunk 全部无主。
        if kind == "run.started" and not message_id_holder.get("id"):
            mid = str(data.get("assistant_message_id") or data.get("run_id") or "")
            if mid:
                message_id_holder["id"] = mid
                # **W3**：占位 seed——前端 `messageReceived` 只有在事件带
                # `assistant_message_id` 时才创建 assistant 占位消息（而
                # 提交时 run_id 尚不存在，receipt 的 emit 带不了），缺了
                # 它 `updateMessage` 对不存在的消息是 no-op，流式 chunk
                # 全部落空。这里以完整 message_received 形状补推（含
                # user_mid）：projector 首事件胜出（真实 user 事件已在
                # 途，同 id 去重，零副作用）；前端幂等覆盖 user + 新建
                # assistant 占位并进入流式态。
                if seed_user is not None:
                    try:
                        _push_sse("message_received", {
                            **seed_user,
                            "assistant_message_id": mid,
                            "status": "processing",
                        }, session_id)
                    except Exception:  # noqa: BLE001 —— seed 失败不阻断
                        logger.debug("assistant seed push failed", exc_info=True)
        if persist:
            if kind == "run.answer":
                mid = str(data.get("assistant_message_id") or data.get("run_id") or "")
                message_id_holder["id"] = mid
                # 批1-S2（A1+B2）：assistant_message 落库已收归进程单例
                # 观察器（_install_answer_persister，run.answer 触发）——
                # 桥不再 emit。STEER 被吸收时跟随桥与所有者桥只各自通知
                # （agent_done），落库恰一次。
                _push_sse("agent_done", {
                    "message_id": mid, "status": "completed",
                }, session_id)
                return
            if kind in ("run.failed", "run.fenced", "run.cancelled") or (
                kind == "run.settled" and (data.get("fenced") or data.get("cancelled"))
            ):
                mid = str(data.get("assistant_message_id") or data.get("run_id") or "")
                reason = str(data.get("error") or data.get("reason") or "")
                cancelled = kind == "run.cancelled" or data.get("cancelled")
                if not cancelled:
                    _push_sse("error", {
                        "message_id": mid, "error": reason,
                    }, session_id)
                _push_sse("agent_done", {
                    "message_id": mid,
                    "status": "cancelled" if cancelled else "error",
                }, session_id)
                return

        out_kind = mapping.get(kind)
        if out_kind is None:
            # 3a.2：流式直通集合收归 apps/chat.events（现网 8 个事件名
            # 逐字一致）；AgentLoop 的 3-step / thinking / stream.failed
            # 协议前端 handlers 全都注册。
            if kind in STREAM_PASSTHROUGH_KINDS:
                out_kind = kind
            else:
                return
        payload = dict(data)
        payload.pop("event_correlation_id", None)
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

    唯一路径（P5-1c ④ 起）：WorkRequest → EntryPoint → mailbox →
    SessionController → GraphEngine，事件在进程内收集。

    ``?wait=true`` 保留同步 reply 语义（外部脚本用户）；默认 receipt 型
    ——submit 即回，答案走 SSE。

    旧的 SessionService 直连分支已删除（`QNODES_NEW_ENTRY` 回退开关退役）。
    """
    # Ownership check: only the session owner may post messages
    from agentnodes.interfaces.api.authz import enforce_session_owned
    enforce_session_owned(request, body.session_id)

    # R2.1c（E1=C）：?wait=true 保留同步 reply 语义（外部脚本用户）；
    # 默认 receipt 型——submit 即回，答案走 SSE。
    wait = (request.query_params.get("wait") or "").strip().lower() in (
        "1", "true", "yes",
    )
    gateway_options = _gateway_chat_options(body)
    return await _send_via_gateway(
        body.session_id, body.content, wait=wait, **gateway_options,
    )


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

    from agentnodes.interfaces.api.authz import enforce_session_owned
    from agentnodes.kernel.session.db import _get_db_path
    try:
        enforce_session_owned(request, session_id)
    except _HTTPExc as exc:
        # Auto-recreate bare study_id session on 404 so SSE works
        # even after the session row was deleted or never created.
        if exc.status_code == 404 and session_id.startswith("study_"):
            try:
                from agentnodes.kernel.session.db import ensure_session_row

                ensure_session_row(
                    _get_db_path(), session_id,
                    f"Study: {session_id}",
                )
                enforce_session_owned(request, session_id)
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
    """Live and recent failed Attempts for a session (reload recovery).

    The frontend calls this after loading messages to rebuild its
    streaming/queued state: a page reload loses ``streamingMessageId``
    and the in-memory queued placeholders, and SSE replay cannot
    recover ``attempt.started`` / ``message_received`` (they sit before
    the Last-Event-ID cursor). See docs/archive/undated/streaming-reload-recovery.md.

    Returns ``{"attempts": [...]}`` with each entry:
    ``{attempt_id, message_id, status: "running"|"queued", prompt,
    created_at}`` — oldest first. Zombie rows (server restart) are
    filtered by the Gateway's live mailbox/run state.
    """
    from agentnodes.interfaces.api.authz import enforce_session_owned
    enforce_session_owned(request, session_id)
    # 顺序④（裁决 4）：存储记录 × Gateway 实时状态的拼接归 apps/chat
    # 查询用例；router 只做鉴权、取组合根句柄与返回。
    from agentnodes.apps.chat.attempts_query import query_live_attempts

    return {
        "attempts": query_live_attempts(
            session_id,
            store=_get_session_service().store,
            gateway=_get_gateway(),
        )
    }


@router.get("/session/{session_id}/available_actions")
async def chat_available_actions(session_id: str, request: Request):
    """C3: actions the current session state permits (drives the UI's buttons)."""
    from agentnodes.interfaces.api.authz import enforce_session_owned
    from agentnodes.kernel.session.service import available_actions

    enforce_session_owned(request, session_id)
    state = _get_gateway().chat_session_state(session_id)
    # 动作矩阵收在 kernel.session.service.available_actions 单点
    # （第三轮复审候选 S3）——含 goal lease 抑制 send（B2 裁决 2）。
    actions = available_actions(
        active=state["active"], pending=state["pending"],
        paused=state["paused"], lease_holder=state.get("lease_holder"),
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "actions": actions,
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

    from agentnodes.apps.chat.export import render
    from agentnodes.interfaces.api.authz import enforce_session_owned

    enforce_session_owned(request, session_id)
    service = _get_session_service()
    messages = service.store.get_messages(session_id, limit=10000)

    # 2026-11-05：格式渲染下沉到 apps/chat/export.py（L2 产品策略）。
    # L1 只负责把它包成 HTTP 响应。
    body, media_type = render(messages, format)
    return PlainTextResponse(body, media_type=media_type)


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
    from agentnodes.kernel.agent.telemetry.trace import TraceWriter
    from agentnodes.kernel.session.trace_projection import TraceProjection
    from agentnodes.interfaces.api.authz import enforce_session_owned
    enforce_session_owned(request, session_id)

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

    2026-11-05：展示元数据（中文名 / 一句话说明）已下沉到
    ``apps/chat/personas.py``；角色清单本身仍来自 L3 的
    ``PromptBuilderFactory``——L1 只做拼接。
    """
    from agentnodes.apps.chat.personas import list_for
    from agentnodes.kernel.agent.prompt.builder import PromptBuilderFactory

    return {"personas": list_for(PromptBuilderFactory.list_roles())}


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
