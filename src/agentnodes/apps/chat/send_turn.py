# -*- coding: utf-8 -*-
"""Chat send-turn 用例（L2）——一次发送的完整编排，深模块。

3a.3a（三前端一核心批 3a）：把 ``routers/chat.py::_send_via_gateway`` 及
其事件桥/答案落库器收进一个深模块。Router 只做鉴权、字段翻译和
错误→HTTP 映射；本模块不认识 FastAPI / HTTPException / SSE 缓冲。

接口（唯一外部 seam）：

    result = await send_turn(input, deps)

``input`` 是一次发送的全部业务参数，``deps`` 是注入的依赖（Gateway /
service / emit / publish / wait_timeout / build_llm_config）。两者都是
类型化 dataclass；测试穿过同一 seam 用 fake adapter 直测，无需 HTTP。

内部 seam（不对外暴露）：``_install_answer_persister`` / ``_event_bridge``
/ ``_precheck_history`` / ``_observe_turn``。Attempt 生命周期遵循 3a.3
决策 5：**先预检历史，再创建 Attempt/发事件，再 submit**——消除
send_async 原来「先发 attempt.created、后读历史失败」的半提交路径。

行为与现网逐字对齐（3a.0 wire 冻结表）：
- receipt_only / consumer receipt / 同步驱动 / wait=true / queue-full /
  entry-rejected / wait-timeout / no-run / missing-terminal / missing-run-id /
  run-failed / request-summarized / STEER absorbed/promoted 全部保持；
- 一次 run 的 assistant 恰一次落库（按 run_id 去重的单例观察器）；
- 每个 turn 桥只收自身关联事件并在终态注销；无残留 sink。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from agentnodes.kernel.gateway import api_chat_request
from agentnodes.kernel.orchestration import WorkKind
from agentnodes.kernel.orchestration.contracts import QueueMode, RejectReason

from agentnodes.apps.chat.events import ChatEvent, translate_engine_event
from agentnodes.apps.chat.messages import record_user_message

logger = logging.getLogger(__name__)

__all__ = [
    "SendTurnInput",
    "SendTurnDeps",
    "HistoryDeps",
    "SendTurnResult",
    "SendTurnError",
    "HistoryUnavailable",
    "QueueFull",
    "EntryRejected",
    "WaitTimeout",
    "NoRunExecuted",
    "MissingRunTerminal",
    "MissingRunId",
    "RequestSummarized",
    "RunFailed",
    "precheck_history",
    "send_turn",
]

# ── 应用层错误（router 一处映射到现有 HTTP status/detail）──────────


class SendTurnError(Exception):
    """send-turn 用例错误基类；``detail`` 是 wire 兼容的 error body。"""

    def __init__(self, detail: dict[str, Any]) -> None:
        self.detail = detail
        super().__init__(str(detail))


class HistoryUnavailable(SendTurnError):
    pass


class QueueFull(SendTurnError):
    pass


class EntryRejected(SendTurnError):
    pass


class WaitTimeout(SendTurnError):
    pass


class NoRunExecuted(SendTurnError):
    pass


class MissingRunTerminal(SendTurnError):
    pass


class MissingRunId(SendTurnError):
    pass


class RequestSummarized(SendTurnError):
    pass


class RunFailed(SendTurnError):
    pass


# ── 输入 / 依赖 / 结果（类型化 interface）───────────────────────────


@dataclass
class SendTurnInput:
    """一次发送的业务参数（不含传输/协议字段）。"""

    session_id: str
    content: str
    queue_mode: QueueMode | None = None
    max_pending: int | None = None
    user_message_id: str | None = None
    attempt_id: str | None = None
    assistant_message_id: str | None = None
    model: str | None = None
    system_prompt: str | None = None
    role: str | None = None
    allowed_tools: list[str] | None = None
    thinking: str | None = None
    wait: bool = False
    mode: str | None = None
    receipt_only: bool = False
    # 调用方（send_async）已在创建 Attempt 前预检过的历史——提供则
    # send_turn 跳过二次预检（重复 A 修复）。信任模型与所有注入依赖同款：
    # 只有同一请求内刚预检的结果才应传入；apps 不验证来源。
    prevalidated_history: list[dict] | None = None

    @property
    def turn_max_iterations(self) -> int | None:
        return 1 if (self.mode or "").strip().lower() == "plan" else None


@dataclass(frozen=True)
class HistoryDeps:
    """历史预检的窄依赖（第四轮 C2）——router 前置只需这两个 callable，
    不再伪造完整 SendTurnDeps 的 no-op emit/publish_sse。"""

    load_history: Callable[[str, bool], list[dict[str, Any]]]
    build_llm_config: Callable[[], Any]


@dataclass
class SendTurnDeps:
    """注入的依赖——组合根提供真 adapter，测试提供 fake。

    ``service: Any`` 已收为两个命名 port（第四轮 C2）：``load_history``
    与 ``update_attempt_status``（attempt 状态机归 kernel）。"""

    gateway: Any
    load_history: Callable[[str, bool], list[dict[str, Any]]]
    update_attempt_status: Callable[..., Any]
    emit: Callable[[str, str, dict[str, Any]], None]
    publish_sse: Callable[[str, dict[str, Any], str], None]
    build_llm_config: Callable[[], Any]
    wait_timeout: Callable[[], float]


@dataclass
class SendTurnResult:
    """用例结果——receipt 与 completed 两态，字段与现网响应逐字对齐。"""

    accepted: bool
    message_id: str = ""
    user_message_id: str = ""
    assistant_message_id: str = ""
    attempt_id: str = ""
    session_id: str = ""
    reply: str = ""
    status: str = ""

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"accepted": self.accepted}
        for key in ("message_id", "user_message_id", "assistant_message_id",
                    "attempt_id", "session_id", "reply", "status"):
            value = getattr(self, key)
            if value:  # 现网各响应形态只带自身字段
                d[key] = value
        return d


# ── 内部 seam 1：单例答案落库观察器 ─────────────────────────────────


def _install_answer_persister(gw: Any, deps: SendTurnDeps) -> None:
    """run.answer → assistant_message 恰一次落库（按 run_id 去重）。

    批1-S2（A1+B2）修复搬迁：外部启动 run / 原桥超时场景天然覆盖；
    落库失败只记 warning，不阻断事件流与各等待方 SSE。
    幂等键归 ``gw.ensure_sink``（第四轮 C5）——替代此前走私到 Gateway
    实例上的 ``_answer_persister_installed`` 私有 flag。
    """
    seen_run_ids: set[str] = set()

    def _persist_answer(kind: str, data: dict) -> None:
        if kind != "run.answer":
            return
        run_id = str(data.get("run_id") or "")
        session_id = str(data.get("business_session_id") or "")
        if not run_id or not session_id or run_id in seen_run_ids:
            return
        seen_run_ids.add(run_id)
        try:
            deps.emit(session_id, "assistant_message", {
                "message_id": str(data.get("assistant_message_id") or run_id),
                "role": "assistant",
                "content": str(data.get("content") or ""),
                "created_at": time.time(),
            })
        except Exception:  # noqa: BLE001 —— 落库失败不吞事件流
            logger.warning("answer persister: assistant persist failed", exc_info=True)

    gw.ensure_sink("answer_persister", lambda: _persist_answer)


# ── 内部 seam 2：事件桥（引擎 → 产品 SSE + Attempt 生命周期）────────

def _event_bridge(
    gw: Any,
    *,
    deps: SendTurnDeps,
    session_id: str,
    assistant_mid_holder: dict[str, str],
    event_correlation_id: str,
    terminal_observer: Callable[[str, dict], None] | None,
    seed_user: dict | None,
    persist: bool = False,
) -> Callable[[], None]:
    """安装事件桥，返回注销函数。逐字搬迁自 router，wire 零变化。

    事件名翻译走 :func:`events.translate_engine_event`（第四轮 C1）——
    词汇表与翻译规则的单一定义点；本地硬编码 mapping 已删。
    """
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

    def _update_attempt(data: dict, status: str, *, summary: str | None = None,
                        error: str = "") -> None:
        attempt_id = data.get("attempt_id")
        if not isinstance(attempt_id, str) or not attempt_id:
            return
        try:
            # C2：attempt 状态机 + 持久化归 kernel 公开面（缺失 → None）
            attempt = deps.update_attempt_status(
                session_id, attempt_id, status, summary=summary, error=error,
            )
            if attempt is None:
                logger.warning("Gateway event references missing Attempt %s", attempt_id)
                return
            lifecycle_event = {
                "running": (ChatEvent.ATTEMPT_STARTED, {
                    "attempt_id": attempt_id,
                    "message_id": str(attempt.message_id or ""),
                }),
                "completed": (ChatEvent.ATTEMPT_COMPLETED, {
                    "attempt_id": attempt_id, "status": "completed",
                    "summary": summary or "",
                }),
                "cancelled": (ChatEvent.ATTEMPT_FAILED, {
                    "attempt_id": attempt_id, "status": "cancelled",
                }),
                "failed": (ChatEvent.ATTEMPT_FAILED, {
                    "attempt_id": attempt_id, "status": "error",
                    "error": error or "Gateway run failed",
                }),
            }[status]
            deps.emit(session_id, lifecycle_event[0], lifecycle_event[1])
        except Exception:  # noqa: BLE001
            logger.debug("bridge attempt update failed", exc_info=True)

    def _bridge_body(kind: str, data: dict) -> None:
        if kind == "work.summarized":
            error = "queued request summarized before execution"
            _update_attempt(data, "failed", error=error)
            mid = str(data.get("assistant_message_id") or (seed_user or {}).get("message_id") or "")
            deps.publish_sse("error", {"message_id": mid, "error": error}, session_id)
            deps.publish_sse("agent_done", {"message_id": mid, "status": "error"}, session_id)
            return
        if kind == "run.started":
            _update_attempt(data, "running")
        elif kind == "run.answer":
            _update_attempt(data, "completed", summary=str(data.get("content") or ""))
        elif kind == "run.failed":
            _update_attempt(data, "failed", error=str(data.get("error") or ""))
        elif kind == "run.cancelled":
            _update_attempt(data, "cancelled")
        elif kind == "run.fenced":
            _update_attempt(data, "failed", error=str(data.get("reason") or ""))

        if kind == "run.started" and not assistant_mid_holder.get("id"):
            mid = str(data.get("assistant_message_id") or data.get("run_id") or "")
            if mid:
                assistant_mid_holder["id"] = mid
                if seed_user is not None:
                    try:
                        deps.publish_sse("message_received", {
                            **seed_user, "assistant_message_id": mid, "status": "processing",
                        }, session_id)
                    except Exception:  # noqa: BLE001
                        logger.debug("assistant seed push failed", exc_info=True)

        # receipt 路径（persist=True）：桥负责各等待方的终态 SSE 通知。
        # assistant_message 落库已收归单例观察器（run.answer 触发），桥不 emit。
        if persist:
            if kind == "run.answer":
                mid = str(data.get("assistant_message_id") or data.get("run_id") or "")
                assistant_mid_holder["id"] = mid
                deps.publish_sse("agent_done", {
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
                    deps.publish_sse("error", {"message_id": mid, "error": reason}, session_id)
                deps.publish_sse("agent_done", {
                    "message_id": mid,
                    "status": "cancelled" if cancelled else "error",
                }, session_id)
                return

    def bridge(kind: str, data: dict) -> None:
        if kind == "steer.applied" and event_correlation_id in data.get("applied_event_correlations", ()):
            state["followed_correlation_id"] = data["event_correlation_id"]
            _bridge_body("run.started", data)
            return
        correlation = data.get("event_correlation_id")
        followed = state.get("followed_correlation_id")
        if correlation != event_correlation_id and (not followed or correlation != followed):
            return
        try:
            if terminal_observer is not None:
                terminal_observer(kind, data)
            _bridge_body(kind, data)
        finally:
            if kind == "work.summarized":
                _close()
            elif terminal_observer is None and kind in (
                "run.answer", "run.failed", "run.cancelled", "run.fenced",
            ):
                _close()
            elif kind == "run.settled" and (
                terminal_observer is not None or data.get("fenced") or not data.get("ok", True)
            ):
                _close()

        out_kind = translate_engine_event(kind)
        if out_kind is None:
            return
        payload = dict(data)
        payload.pop("event_correlation_id", None)
        mid = assistant_mid_holder.get("id")
        if mid:
            payload.setdefault("message_id", mid)
        try:
            deps.publish_sse(out_kind, payload, session_id)
        except Exception:  # noqa: BLE001
            logger.debug("sse bridge push failed: %s", out_kind, exc_info=True)

    adder = getattr(gw, "add_sink", None)
    if callable(adder):
        state["unsubscribe"] = adder(bridge)
    else:
        gw.install_sink(bridge)
    return _close


# ── 内部 seam 3：历史预检（先验证，再创建 Attempt）──────────────────


def _precheck_history(inp: SendTurnInput, deps: HistoryDeps) -> list[dict]:
    """严格读契约：历史读取失败 → HistoryUnavailable，submit 不发生。"""
    try:
        cfg = deps.build_llm_config()
        keep_all = bool(
            cfg and getattr(cfg, "compact_config", None)
            and cfg.compact_config.keep_all_compactions_in_history
        )
        return deps.load_history(inp.session_id, keep_all)
    except Exception as exc:  # noqa: BLE001 —— 转为明确错误，不静默降级
        logger.exception("send_turn: history load failed for session %s", inp.session_id)
        raise HistoryUnavailable({
            "error": "history_unavailable", "reason": str(exc),
            "session_id": inp.session_id,
        }) from exc


# ── 主用例：唯一外部 seam ───────────────────────────────────────────


def precheck_history(inp: SendTurnInput, deps: HistoryDeps) -> list[dict]:
    """公开预检（决策 5 的 router 前置）：send_async 在创建 Attempt 之前
    调用本函数——历史失败时零 Attempt/事件。

    收窄为 :class:`HistoryDeps`（第四轮 C2）——router 前置只注入
    load_history + build_llm_config，不再伪造完整 deps 的 no-op。

    返回预检后的 history，供调用方通过 ``prevalidated_history`` 传给
    send_turn 跳过二次预检（重复 A 修复）。现有调用方忽略返回值仍兼容。
    """
    return _precheck_history(inp, deps)


async def send_turn(inp: SendTurnInput, deps: SendTurnDeps) -> SendTurnResult:
    """执行一次 chat send（receipt 或 completed）。

    决策 5：历史预检在 Attempt 创建之前——半提交路径从结构上消失。
    本函数不 import interfaces / FastAPI，错误以应用异常表达。
    """
    # ① 先验证历史（失败 → 零 Attempt/事件/run）。调用方已预检并传入结果时
    #    跳过二次读取（重复 A 修复）；未提供则自包含地预检。
    history = (
        inp.prevalidated_history
        if inp.prevalidated_history is not None
        else _precheck_history(inp, HistoryDeps(
            load_history=deps.load_history,
            build_llm_config=deps.build_llm_config,
        ))
    )

    gw = deps.gateway
    _install_answer_persister(gw, deps)
    user_mid = inp.user_message_id or str(uuid.uuid4())
    event_correlation_id = str(uuid.uuid4())
    assistant_mid_holder: dict[str, str] = {}

    turn_persist = inp.receipt_only or (gw.consumer_running() and not inp.wait)
    completion = threading.Event()
    outcome: dict[str, Any] = {}

    def observe_turn(kind: str, data: dict) -> None:
        if kind in ("run.answer", "run.failed", "run.cancelled", "run.fenced"):
            outcome["terminal"] = (kind, data)
        elif kind == "work.summarized":
            outcome["terminal"] = (kind, data)
            outcome["settled"] = {"ok": False}
            completion.set()
        elif kind == "run.settled":
            outcome["settled"] = data
            completion.set()

    _unsub = _event_bridge(
        gw, deps=deps, session_id=inp.session_id,
        assistant_mid_holder=assistant_mid_holder,
        event_correlation_id=event_correlation_id,
        terminal_observer=None if turn_persist else observe_turn,
        seed_user=({
            "message_id": user_mid, "user_message_id": user_mid,
            "role": "user", "content": inp.content, "created_at": time.time(),
        } if turn_persist else None),
        persist=turn_persist,
    )

    try:
        receipt = gw.entry.submit(
            api_chat_request(
                inp.session_id, inp.content, history=history,
                max_iterations=inp.turn_max_iterations,
                queue_mode=inp.queue_mode,
                attempt_id=inp.attempt_id,
                assistant_message_id=inp.assistant_message_id,
                model=inp.model, system_prompt=inp.system_prompt, role=inp.role,
                allowed_tools=inp.allowed_tools, mode=inp.mode, thinking=inp.thinking,
                event_correlation_id=event_correlation_id,
            ),
            max_pending=inp.max_pending,
        )
    except Exception:
        _unsub()
        raise
    if not receipt.accepted:
        _unsub()
        if receipt.reason is RejectReason.QUEUE_FULL:
            raise QueueFull({
                "error": "queue_full", "limit": inp.max_pending,
                "current_size": receipt.current_size,
            })
        raise EntryRejected({"error": "entry_rejected", "reason": str(receipt.reason)})

    if inp.receipt_only:
        return SendTurnResult(
            accepted=True, message_id=user_mid,
            user_message_id=user_mid,
            assistant_message_id=inp.assistant_message_id or "",
            attempt_id=inp.attempt_id or "",
            session_id=receipt.session_id or inp.session_id,
        )

    if turn_persist:
        try:
            deps.emit(inp.session_id, "message_received", {
                "message_id": user_mid, "user_message_id": user_mid,
                "role": "user", "content": inp.content, "created_at": time.time(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("send_turn: user persist failed: %s", exc)
        return SendTurnResult(
            accepted=True, message_id=user_mid,
            session_id=receipt.session_id or inp.session_id,
        )

    record_user_message(
        deps.emit, session_id=inp.session_id, message_id=user_mid,
        content=inp.content, created_at=time.time(),
    )

    try:
        if gw.consumer_running() and inp.wait:
            finished = await asyncio.get_running_loop().run_in_executor(
                None, lambda: completion.wait(deps.wait_timeout()),
            )
            if not finished:
                raise WaitTimeout({
                    "error": "wait_timeout",
                    "hint": "turn 未在时限内完成；请改用 SSE 订阅",
                })
        else:
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: gw.run_once({WorkKind.CHAT}),
            )
            if not completion.is_set():
                raise NoRunExecuted("no run executed")
    finally:
        _unsub()

    terminal = outcome.get("terminal")
    settled = outcome.get("settled", {})
    if terminal is None:
        raise MissingRunTerminal({"error": "missing_run_terminal"})
    terminal_kind, terminal_data = terminal
    if terminal_kind == "work.summarized":
        error = "queued request summarized before execution"
        mid = inp.assistant_message_id or user_mid
        deps.publish_sse("error", {"message_id": mid, "error": error}, inp.session_id)
        deps.publish_sse("agent_done", {"message_id": mid, "status": "error"}, inp.session_id)
        raise RequestSummarized({"error": "request_summarized", "reason": error})
    run_id = str(terminal_data.get("run_id") or "")
    if not run_id:
        raise MissingRunId({"error": "missing_run_id"})
    if not settled.get("ok") or terminal_kind != "run.answer":
        error = str(terminal_data.get("error") or terminal_data.get("reason") or "")
        deps.publish_sse("error", {"message_id": run_id, "error": error}, inp.session_id)
        deps.publish_sse("agent_done", {"message_id": run_id, "status": "error"}, inp.session_id)
        raise RunFailed({"error": "run_failed", "reason": error})
    answer = str(terminal_data.get("content") or "")

    assistant_mid = run_id
    deps.publish_sse(
        "agent_done", {"message_id": assistant_mid, "status": "completed"}, inp.session_id,
    )
    return SendTurnResult(
        accepted=True, message_id=assistant_mid, attempt_id=run_id,
        reply=answer, status="completed",
    )
