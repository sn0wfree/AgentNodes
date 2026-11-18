"""Chat API — send message + SSE event stream with real LLM integration。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import TYPE_CHECKING, Any, Optional

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
    from agentnodes.kernel.permission import PermissionGateway

    gateway = _permission_gateway_cache.get("default")
    if gateway is not None:
        return gateway

    def _push_sse(tool_call_id, decision, args):
        try:
            from agentnodes.kernel.events.event_v2 import EventType

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
    from agentnodes.interfaces.api.container import build_container
    from agentnodes.interfaces.api.routers.web_session import _get_db_path

    db_path = str(_get_db_path())
    service = _session_service_cache.get(db_path)
    if service is None:
        container = build_container(db_path=db_path)
        service = container.session_service
        _session_service_cache[db_path] = service
    return service


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
    from agentnodes.interfaces.api.routers.web_session import _fetch_session_owned, _get_db
    user_id = getattr(request.state, "user_id", "anonymous")
    _fetch_session_owned(_get_db(), body.session_id, user_id)

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
    mailbox = gateway.entry.mailbox_for(f"chat:{body.session_id}")
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
    from agentnodes.interfaces.api.routers.web_session import _fetch_session_owned, _get_db
    user_id = getattr(request.state, "user_id", "anonymous")
    _fetch_session_owned(_get_db(), body.session_id, user_id)
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
    from agentnodes.interfaces.api.routers.web_session import _fetch_session_owned, _get_db
    user_id = getattr(request.state, "user_id", "anonymous")
    _fetch_session_owned(_get_db(), body.session_id, user_id)
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
) -> dict:
    """新路径：WorkRequest → EntryPoint → 准入 → 引擎 → 事件流。

    P5-1：补齐历史注入与持久化，**照抄旧路径的两条机制**（不改选型）：
    - 读历史 → ``SessionStore.get_messages`` + ``_convert_messages_to_history``
    - 写消息 → ``event_bus.emit`` → projector（``flush_to_messages=True``）

    P5-1b：流式与工具链——
    - Gateway 装配 tool registry（shell 工具遵循 apps/chat 的 shell policy）
    - 引擎事件经 ``install_sink`` 转发为前端词表（text_delta / tool_call / …）
    - 末尾补发 ``agent_done``

    R2.1c（E1=C）：消费者在跑且未要求 wait → **receipt 路径**——submit 后
    立即返回 ``{accepted, message_id, session_id}``，答案经 ``run.answer``
    事件由 persist 桥落库并补 agent_done；请求线程不再驱动执行。
    同步路径（消费者未跑 = 测试/直构；或 ``wait=true``）保持现契约：
    阻塞至 turn 完成、响应携带 reply。wait=true 只是**等待**，执行仍是
    消费者的事（无消费者时回落请求内驱动）。

    **W4**：``mode="plan"`` → payload 带 ``max_iterations=1``（分析单轮、不进循环）。
    build 模式不传 → 用 singleton 按 LLMConfig.max_iterations 装配的值。

    顺序关键：**先读历史、再 emit 本轮事件**——否则当前轮会被重复注入。
    """
    import time as _time
    import uuid as _uuid

    from agentnodes.kernel.gateway import api_chat_request
    from agentnodes.kernel.orchestration import WorkKind

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
    user_mid = user_message_id or str(_uuid.uuid4())
    assistant_mid_holder: dict[str, str] = {}

    # **W3**：桥在 submit **之前**安装——work.enqueued 会在 submit 内唤醒
    # 消费者，晚装可能错过 run.started（占位 seed 与 message_id 注入全落
    # 空）。receipt 路径（persist=True）带 seed_user：run.started 时补推
    # assistant 占位 seed；sync/wait 路径不带（保持「失败不落任何行」的
    # 既有不变量）。
    turn_persist = receipt_only or (gw.consumer_running() and not wait)
    _unsub = _install_chat_event_bridge(
        gw, session_id, assistant_mid_holder,
        service=service, persist=turn_persist,
        seed_user=(
            {
                "message_id": user_mid,
                "user_message_id": user_mid,
                "role": "user",
                "content": content,
                "created_at": _time.time(),
            }
            if turn_persist
            else None
        ),
    )

    turn_max_iter = 1 if (mode or "").strip().lower() == "plan" else None
    receipt = gw.entry.submit(
        api_chat_request(
            session_id, content, history=history,
            max_iterations=turn_max_iter,
            queue_mode=queue_mode,
            attempt_id=attempt_id,
            assistant_message_id=assistant_message_id,
            model=model,
            system_prompt=system_prompt,
            role=role,
            allowed_tools=allowed_tools,
            mode=mode,
            thinking=thinking,
        ),
        max_pending=max_pending,
    )
    if not receipt.accepted:
        # 未受理：桥立即撤（无 turn 可挂）
        _unsub()
        if receipt.reason is RejectReason.QUEUE_FULL:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "queue_full",
                    "limit": max_pending,
                    "current_size": receipt.current_size,
                },
            )
        raise HTTPException(
            status_code=400,
            detail={"error": "entry_rejected", "reason": str(receipt.reason)},
        )

    if receipt_only:
        return {
            "accepted": True,
            "message_id": user_mid,
            "user_message_id": user_mid,
            "assistant_message_id": assistant_message_id or "",
            "attempt_id": attempt_id,
            "session_id": receipt.session_id or session_id,
        }

    # ── ②-b receipt 路径（R2.1c，E1=C）────────────────────────
    # 消费者在跑且未要求 wait：用户消息此刻落库，答案经 run.answer 由
    # persist 桥落库并补 agent_done；HTTP 立即返回两态回执。
    # **注意**：这里 return 不注销桥——turn 交给消费者异步执行，run.answer
    # 的落库与 seed/流式转发都靠它；桥在收尾事件时自注销（R7）。
    if turn_persist:
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
    raw = _settings.get_str("AGENTNODES_CHAT_WAIT_TIMEOUT").strip()
    try:
        return float(raw) if raw else 300.0
    except ValueError:
        return 300.0


def _install_chat_event_bridge(
    gw: Any, session_id: str, message_id_holder: dict[str, str],
    *, service: Any = None, persist: bool = False,
    seed_user: dict | None = None,
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
        # **V3-c**:react 词表映射已随 react.py 删除——唯一执行引擎是
        # AgentLoop,词表直通。
        "tool_call": "tool_call",
        "tool_result": "tool_result",
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
            if kind in (
                "run.answer", "run.failed", "run.cancelled", "run.fenced",
            ):
                _close()
            elif kind == "run.settled" and (
                data.get("fenced") or not data.get("ok", True)
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
            lifecycle_event = {
                "running": ("attempt.started", {
                    "attempt_id": attempt_id,
                    "message_id": str(attempt.message_id or ""),
                }),
                "completed": ("attempt.completed", {
                    "attempt_id": attempt_id,
                    "status": "completed",
                    "summary": summary or "",
                }),
                "cancelled": ("attempt.failed", {
                    "attempt_id": attempt_id,
                    "status": "cancelled",
                }),
                "failed": ("attempt.failed", {
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
            # 流式词表直通。AgentLoop 的 3-step 协议（text.started →
            # text_delta{text_id} → text.ended）与 thinking 协议
            # （thinking_start/delta/done/end）前端 handlers 全都注册；
            # stream.failed 由 llm 单发节点（nodes/llm.py）继续发出。
            if kind in (
                "text_delta", "thinking_delta", "thinking_done", "stream.failed",
                "text.started", "text.ended",
                "thinking_start", "thinking_end",
            ):
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

    唯一路径（P5-1c ④ 起）：WorkRequest → EntryPoint → mailbox →
    SessionController → GraphEngine，事件在进程内收集。

    ``?wait=true`` 保留同步 reply 语义（外部脚本用户）；默认 receipt 型
    ——submit 即回，答案走 SSE。

    旧的 SessionService 直连分支已删除（`QNODES_NEW_ENTRY` 回退开关退役）。
    """
    # Ownership check: only the session owner may post messages
    from agentnodes.interfaces.api.routers.web_session import _fetch_session_owned, _get_db
    user_id = getattr(request.state, "user_id", "anonymous")
    _fetch_session_owned(_get_db(), body.session_id, user_id)

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

    from agentnodes.interfaces.api.routers.web_session import _fetch_session_owned, _get_db, _get_db_path
    user_id = getattr(request.state, "user_id", "anonymous")
    try:
        _fetch_session_owned(_get_db(), session_id, user_id)
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
    from agentnodes.interfaces.api.routers.web_session import _fetch_session_owned, _get_db
    user_id = getattr(request.state, "user_id", "anonymous")
    _fetch_session_owned(_get_db(), session_id, user_id)
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
    from agentnodes.interfaces.api.routers.web_session import _fetch_session_owned, _get_db
    user_id = getattr(request.state, "user_id", "anonymous")
    _fetch_session_owned(_get_db(), session_id, user_id)
    state = _get_gateway().chat_session_state(session_id)
    is_processing = state["active"] or state["pending"]
    actions = []
    if is_processing:
        actions.append({"name": "cancel", "label": "取消", "destructive": "false"})
    if state["paused"]:
        actions.append({"name": "resume", "label": "恢复", "destructive": "false"})
    if not is_processing and not state["paused"]:
        actions.append({"name": "send", "label": "发送", "destructive": "false"})
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
    from agentnodes.interfaces.api.routers.web_session import _fetch_session_owned, _get_db

    user_id = getattr(request.state, "user_id", "anonymous")
    _fetch_session_owned(_get_db(), session_id, user_id)
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
    from agentnodes.interfaces.api.routers.web_session import _fetch_session_owned, _get_db
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
