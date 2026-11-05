"""IsolatedSlotReplay — 在容量闸 study 池的隔离 slot 里只读回放（§4.5）。

**为什么需要这一层**：validation_gate 的 ``replay`` 是个由调用方注入的
可调用对象。施工方案 §4.5 要求它在「隔离 Worker slot（来自阶段二容量闸的
study 池独立 slot）」中运行，且**只读**。把这段职责实现成独立类，而不是
让 gate 自己去拿 slot —— 那样 gate 会同时承担"判定"与"资源管理"两件事。

**隔离的实现**：本实现用 ``CapacityGate.acquire("study")`` 取得一个 slot
令牌，在 ``try/finally`` 里跑回放，结束即释放。这与 study 任务共用同一个
池，因此回放不会与正在跑的研究任务叠加超载（容量闸的意义就在这里）。

**只读的实现**：回放函数收到的只有 ``GenomeProposal`` 与 ``HoldoutSet``。
``HoldoutSet`` 是 frozen dataclass 且无写方法（H5）；``GenomeProposal``
也是 frozen。回放实现拿不到任何写业务表的通道——契约由类型保证，
不只靠纪律。

**slot 满时的行为**：``acquire`` 返回 None 表示池满。此时**不拒绝**，
而是等待（``wait_timeout`` 内轮询）——与容量闸"满→排队不拒绝"的语义
一致。超时才降级为"无法验证"，由 gate 判为失败并 escalate。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from agentnodes.apps.evolution.genome_proposals import GenomeProposal
from agentnodes.apps.evolution.holdout import HoldoutSet

logger = logging.getLogger(__name__)


class SlotUnavailable(RuntimeError):
    """容量闸 study 池在等待窗口内始终没有空位。"""


@dataclass
class IsolatedSlotReplay:
    """在 study 池的隔离 slot 中执行只读回放。

    :param gate: ``CapacityGate``（阶段二）。None = 不做容量隔离（测试路径）
    :param replay_fn: 真正的回放实现 ``(proposal, holdout) -> metric``。
        它**只能**读 holdout、算指标；不得写业务表。
    :param pool: 使用的池名（默认 ``study``，施工方案 §4.5）
    :param wait_timeout: slot 满时最多等待多少秒（轮询间隔 0.05s）
    """

    gate: Any | None
    replay_fn: Callable[[GenomeProposal, HoldoutSet], float]
    pool: str = "study"
    wait_timeout: float = 30.0
    _poll_interval: float = field(default=0.05, repr=False)

    # 可观测计数（测试与运维用）
    replays: int = field(default=0, init=False)
    waits: int = field(default=0, init=False)
    slot_unavailable: int = field(default=0, init=False)

    def __call__(self, proposal: GenomeProposal, holdout: HoldoutSet) -> float:
        """CapacityGate 风格的可调用入口，直接作为 gate 的 ``replay`` 参数。"""
        token = self._acquire()
        try:
            self.replays += 1
            return float(self.replay_fn(proposal, holdout))
        finally:
            self._release(token)

    # ── slot 管理 ───────────────────────────────────────────────────

    def _acquire(self) -> Any:
        if self.gate is None:
            return None
        deadline = time.monotonic() + self.wait_timeout
        while True:
            token = self.gate.acquire(self.pool)
            if token is not None:
                return token
            self.waits += 1
            if time.monotonic() >= deadline:
                self.slot_unavailable += 1
                raise SlotUnavailable(
                    f"no free slot in {self.pool!r} pool within "
                    f"{self.wait_timeout}s"
                )
            time.sleep(self._poll_interval)

    def _release(self, token: Any) -> None:
        if self.gate is not None and token is not None:
            self.gate.release(token)

    # ── 观测 ────────────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        return {
            "replays": self.replays,
            "waits": self.waits,
            "slot_unavailable": self.slot_unavailable,
            "pool": self.pool,
            "pool_usage": self.gate.usage(self.pool) if self.gate is not None else None,
        }


__all__ = ["IsolatedSlotReplay", "SlotUnavailable"]
