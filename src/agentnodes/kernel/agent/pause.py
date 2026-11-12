"""挂起/恢复原语（统一引擎 H1）——循环级 HITL。

**为什么需要**（design doc §3.5 盘查）：仓库里已有三套「人工介入」——
``approve_loop``（no-progress 闸）、``PermissionGateway``（工具级）、
``halt``（全局 kill-switch）——**都是一问一答**：能挂起等外部决定，但
不能「恢复并注入数据」。本模块提供缺失的那一段：挂起点 + 恢复注入。

**对标**：LangGraph 的 ``interrupt()`` + ``Command(resume=...)``；
pydantic-ai durable 的暂停/恢复。

**三条不变量**（H1 验收红线，见 design doc §3.5）：

1. **不隐式重放**：恢复后从挂起点继续，**不重新执行**挂起前已完成的
   副作用（LangGraph double-execution 坑：它在恢复时重放节点，导致
   「发邮件」这类副作用重复发生）。
2. **超时语义显式**：``pause(timeout_s=...)`` 超时后走取消分支，调用方
   拿到 ``CANCELLED`` 哨兵——不静默挂死。
3. **单次挂起**：同一 handle 只能挂起一次（重复 pause 抛错），不做嵌套
   挂起（那需要栈语义，超出本批范围）。

**线程模型**：``PauseHandle.resume()`` 可能从**另一个线程**调用（HTTP
请求线程 → 事件循环线程），因此用 ``threading.Event`` 而非 asyncio
原语——与既有 ``approval_event``（``loop_progress.py:212-219``）同模式。
"""
from __future__ import annotations

import threading
import time
from typing import Any

__all__ = ["CANCELLED", "PauseHandle", "PauseTimeout", "attach_pause"]


class _Cancelled:
    """挂起被取消/超时的哨兵（与恢复值区分）。"""

    _instance: "_Cancelled | None" = None

    def __new__(cls) -> "_Cancelled":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "<PAUSE_CANCELLED>"

    def __bool__(self) -> bool:
        return False


#: 挂起被取消（显式 cancel 或超时）的哨兵值。
CANCELLED = _Cancelled()


class PauseTimeout(Exception):
    """``pause()`` 等待超过 ``timeout_s``。"""


class PauseHandle:
    """一次挂起的句柄：外部用它恢复或取消。

    典型用法::

        handle = PauseHandle(reason="需要人工判断", payload={...})
        value = handle.wait(timeout_s=1800)   # 循环侧（阻塞）
        # 另一线程：
        handle.resume({"decision": "approve"})

    ``wait`` 返回恢复值；被取消/超时返回 :data:`CANCELLED`。
    """

    def __init__(
        self,
        *,
        reason: str,
        payload: dict[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> None:
        self.reason = reason
        self.payload = dict(payload or {})
        self.timeout_s = timeout_s
        self._event = threading.Event()
        self._value: Any = None
        self._cancelled = False
        self._cancel_reason: str = ""
        self._paused_at = 0.0
        self._resumed_at = 0.0

    # ── 状态 ──

    @property
    def is_paused(self) -> bool:
        """当前处于挂起态（已挂起且未恢复/取消）。"""
        return not self._event.is_set()

    @property
    def cancel_reason(self) -> str:
        return self._cancel_reason

    @property
    def elapsed_s(self) -> float:
        """挂起持续时长（秒）——未恢复时为至今。"""
        if not self._paused_at:
            return 0.0
        end = self._resumed_at or time.monotonic()
        return max(0.0, end - self._paused_at)

    # ── 外部侧 ──

    def resume(self, value: Any = None) -> bool:
        """注入恢复值并唤醒等待者。返回 False = 已恢复/已取消（重复调用）。"""
        if self._event.is_set():
            return False
        self._value = value
        self._resumed_at = time.monotonic()
        self._event.set()
        return True

    def cancel(self, reason: str = "cancelled") -> bool:
        """取消挂起。等待方拿到 :data:`CANCELLED`。"""
        if self._event.is_set():
            return False
        self._cancelled = True
        self._cancel_reason = reason
        self._resumed_at = time.monotonic()
        self._event.set()
        return True

    # ── 循环侧 ──

    def wait(self, timeout_s: float | None = None) -> Any:
        """等待恢复。返回恢复值，或 :data:`CANCELLED`（取消/超时）。

        **提前到达保护**（沿 ``approval_event`` 三步序，loop_progress.py:212）：
        决策可能在 ``wait`` 之前就到达——``resume()`` 先置值再 set 事件，
        因此「先到」不会丢。
        """
        if not self._paused_at:
            self._paused_at = time.monotonic()
        t = self.timeout_s if timeout_s is None else timeout_s
        if t is None:
            self._event.wait()
        elif not self._event.wait(timeout=t):
            # 超时：转取消（幂等——resume 可能刚好并发到达）
            self.cancel(reason="timeout")
        if self._cancelled:
            return CANCELLED
        return self._value

    def __repr__(self) -> str:
        state = "paused" if self.is_paused else (
            "cancelled" if self._cancelled else "resumed")
        return f"<PauseHandle {state} reason={self.reason!r}>"


def attach_pause(ctx: Any) -> Any:
    """给 ctx 装上 ``pause`` 工厂（装配层调用）。

    返回的工厂签名：``pause(reason, payload=None, timeout_s=None) -> PauseHandle | None``

    **decider 侧形态**（协议不变——Decision 仍是四值，decider 不阻塞）::

        handle = ctx.pause(reason="需要人工判断", payload={...})
        if handle is not None:
            state._pause_handle = handle      # 引擎据此挂起
        return Decision.PROCEED, None

    引擎在闸位判定后看到 ``_pause_handle``：yield pause 事件 → async 等恢复 →
    把恢复值写入 ``ctx.resume_value`` → **重跑该闸位**（decider 读到
    ``ctx.resume_value`` 决定后续）。恢复值因此能进循环。

    **单次挂起**（不变量 3）：``ctx.resume_value`` 非 None 说明已恢复过一次，
    工厂返回 None —— decider 第二次调用 ``pause`` 不会再次挂起（否则死循环）。
    """
    def _pause(
        reason: str,
        payload: dict[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> PauseHandle | None:
        if getattr(ctx, "resume_value", None) is not None:
            return None  # 已恢复过一次 → 不再挂起（不变量 3）
        handle = PauseHandle(reason=reason, payload=payload, timeout_s=timeout_s)
        try:
            ctx._pause_handle = handle
        except Exception:  # noqa: BLE001 — 冻结 ctx 则退化（不挂起）
            return None
        return handle

    ctx.pause = _pause
    return _pause
