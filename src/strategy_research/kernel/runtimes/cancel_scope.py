"""CancelScope —— 一次 run 的取消信号（B1）。

语义（docs/stage-b1-interrupt-design-20261030.md）：
- **只置位不反转**：一旦取消，永远取消（一个 run 不能复活）。
- **节点边界检查**：GraphEngine 在节点开始前/结束后各查一次；节点内部
  （LLM 调用/工具执行）不强杀——Python 无法安全杀线程，强杀会破坏
  provider 连接。边界取消已满足「打断输入优先接 next」的体验要求。
- 与 steer 同型：作为 `state["cancel_scope"]` 随引擎 state 传递，
  不新建全局单例。

**不放**：`threading.Event` 之外的东西——越简单越不容易错。
"""
from __future__ import annotations

import threading

__all__ = ["CancelScope", "RunCancelled"]


class RunCancelled(Exception):
    """节点边界检测到取消 → 中止本次 run（引擎转成 INTERRUPT_KEY）。"""

    def __init__(self, reason: str = "cancelled") -> None:
        super().__init__(reason)
        self.reason = reason


class CancelScope:
    """线程安全的单向取消信号。"""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._reason: str | None = None
        self._lock = threading.Lock()

    def cancel(self, *, reason: str = "user") -> None:
        """置取消位（幂等：首次 reason 生效，后续调用不覆盖）。"""
        with self._lock:
            if self._reason is None:
                self._reason = reason
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str | None:
        with self._lock:
            return self._reason

    def raise_if_cancelled(self) -> None:
        """节点边界调用：已取消则抛 RunCancelled。"""
        if self._event.is_set():
            raise RunCancelled(self._reason or "cancelled")
