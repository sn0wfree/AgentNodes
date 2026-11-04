"""CapacityGate — 容量组闸（施工方案 §3.4）。

**它不是队列**：容量闸是**准入闸**。池满时请求留在 mailbox 排队
（不拒绝，符合 v8.4「全系统唯一拒绝点 = mailbox cap」），闸只回答
"现在能不能起"。

**三个池**：

| 池 | 上限 | 依据 |
|---|---|---|
| chat  | 宽（默认 32） | 交互式会话，用户体验优先 |
| study | 窄（= ExecutorBridge 进程池上限） | 研究任务受 CPU 约束 |
| cron  | 隔离（默认 2） | 定时任务独立池，避免被 chat/study 饿死 |

**study 上限的来历（实测，非猜测）**：
``kernel/runtimes/executor_bridge.py:101`` —
``self.size = size or max(1, (os.cpu_count() or 2) - 1)``
即默认 CPU-1。容量闸的 study 池据此对齐同一个函数，而不是硬编码数字——
换机器时两边自动一致。

**行为契约**：
- ``acquire`` 成功返回 token；池满返回 None（调用方回 mailbox 排队）
- running 不抢占（已持 token 的不会被新请求挤掉）
- ``release`` 后可被后续请求取得，唤醒顺序由调用方的 mailbox 保证
- ``enabled=False`` 时 ``acquire`` 恒返回 token（一键 disable，§3.8 回滚）
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


def bridge_default_pool_size() -> int:
    """Mirror ``ExecutorBridge`` 的默认池大小（CPU-1，下限 1）。

    与 ``kernel/runtimes/executor_bridge.py:101`` 保持同一公式：
    对齐而不是抄数字，换机器时两边自动一致。
    """
    return max(1, (os.cpu_count() or 2) - 1)


#: 各池默认上限。chat / cron 是可决策项（施工方案 §3.4 给出建议值），
#: study 必须与 ExecutorBridge 对齐（附录 D 要求实测）。
DEFAULT_CHAT_LIMIT = 32
DEFAULT_CRON_LIMIT = 2


@dataclass
class CapacityConfig:
    """三个池的上限。``study`` 默认跟随 ExecutorBridge（CPU-1）。"""

    chat: int = DEFAULT_CHAT_LIMIT
    study: int = field(default_factory=bridge_default_pool_size)
    cron: int = DEFAULT_CRON_LIMIT
    enabled: bool = True


@dataclass(frozen=True)
class CapacityToken:
    """一次准入凭证。持有即占用一个池位。"""

    pool: str
    slot: int


class CapacityGate:
    """按池限流的准入闸。线程安全。"""

    POOLS = ("chat", "study", "cron")

    def __init__(self, config: CapacityConfig | None = None) -> None:
        self._config = config or CapacityConfig()
        self._lock = threading.RLock()
        self._used: dict[str, set[int]] = {p: set() for p in self.POOLS}
        self._limits = {
            "chat": self._config.chat,
            "study": self._config.study,
            "cron": self._config.cron,
        }

    # ── 准入 ────────────────────────────────────────────────────────

    def acquire(self, pool: str) -> CapacityToken | None:
        """Try to take a slot. Returns None when the pool is full.

        None 的语义是「现在起不了，去 mailbox 排队」——**不是**拒绝。
        """
        if pool not in self._limits:
            raise ValueError(
                f"unknown pool {pool!r} (known: {', '.join(self.POOLS)})"
            )
        if not self._config.enabled:
            # disable 时不占位，但 token 仍带池名（调用方无感）
            return CapacityToken(pool=pool, slot=-1)

        with self._lock:
            limit = self._limits[pool]
            used = self._used[pool]
            if len(used) >= limit:
                return None
            for slot in range(limit):
                if slot not in used:
                    used.add(slot)
                    return CapacityToken(pool=pool, slot=slot)
        return None  # pragma: no cover — range 与 len 保证不可达

    def release(self, token: CapacityToken | None) -> None:
        """Return a slot. Idempotent and safe on disabled tokens."""
        if token is None or token.slot < 0:
            return
        with self._lock:
            self._used.get(token.pool, set()).discard(token.slot)

    # ── 观测 ────────────────────────────────────────────────────────

    def usage(self, pool: str) -> dict[str, int]:
        """Return ``{used, limit, available}`` for one pool."""
        if pool not in self._limits:
            raise ValueError(f"unknown pool {pool!r}")
        with self._lock:
            used = len(self._used[pool])
            limit = self._limits[pool]
        return {"used": used, "limit": limit, "available": max(0, limit - used)}

    def snapshot(self) -> dict[str, dict[str, int]]:
        return {p: self.usage(p) for p in self.POOLS}

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def disable(self) -> None:
        """Turn the gate off (§3.8 回滚开关). Already-held tokens stay valid."""
        self._config.enabled = False

    def enable(self) -> None:
        self._config.enabled = True

    def __repr__(self) -> str:
        return f"CapacityGate({self.snapshot()}, enabled={self.enabled})"


def pool_for_kind(kind: Any) -> str:
    """Map a ``WorkKind`` (or its string value) to a capacity pool.

    chat  → 交互式会话
    study → 研究任务
    cron  → 定时任务
    其他  → 归 chat（宽池兜底，避免未知 kind 被误限流）
    """
    value = getattr(kind, "value", kind)
    value = str(value).lower()
    if "study" in value or "research" in value:
        return "study"
    if "cron" in value or "schedul" in value or "automat" in value:
        return "cron"
    return "chat"


__all__ = [
    "CapacityConfig",
    "CapacityGate",
    "CapacityToken",
    "DEFAULT_CHAT_LIMIT",
    "DEFAULT_CRON_LIMIT",
    "bridge_default_pool_size",
    "pool_for_kind",
]
