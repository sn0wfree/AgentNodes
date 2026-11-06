"""SkipMonitor — ``rsi.cycle_skipped`` 的聚合读者（评审 O）。

**为什么需要它**：``RsiLoop.run_once()`` 在每种失败路径上都发
``rsi.cycle_skipped``，但**没有任何读者**。事件落在流里没人看，等价于
"RSI 一直在空转"这件事不可观测——正是本轮评审发现的形态：链路号称
接上了，实际每轮都跳过，而监控面板上一切正常。

**它做什么**：按 ``reason`` 分类计数；滚动 24h 内某一类跳过超过阈值就
emit ``rsi.skip_escalated`` 并打 WARNING。**只读统计 + 一条告警事件**，
不参与决策、不改变 RSI 行为——把"该不该退役/降级"留给人和上层策略。

**为什么阈值是模块常量而不是配置项**：当前阶段先让"可调"这件事**可见**。
按框架 §0.5 元规则（退役门槛可调），阈值必须参数化；实现上是构造参数
（``threshold``），模块常量只是缺省值。等真正需要按环境调时，装配根
（L1）读配置传进来即可，内核侧不需要改。

**落盘（可选）**：``snapshot()`` / ``load_state()`` 走纯 dict，落盘由
调用方决定路径（L1 用 ``foundation.storage.sqlite.resolve_db_path`` 的
同源数据目录）。kernel 侧不硬编码任何路径——那是 interfaces 的职责。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

#: 滚动窗口（秒）。24h = 一天的运维节律；短于此的抖动不告警。
DEFAULT_WINDOW_S = 24 * 3600

#: 单类跳过在窗口内的缺省阈值。**可参数化**（构造参数 ``threshold``）——
#: 按框架 §0.5「退役门槛可调」，这里的常量只是缺省值，不是硬编码规则。
DEFAULT_ESCALATION_THRESHOLD = 3

#: 会触发 escalation 的 reason（其余 reason 计数但保持安静）。
#:
#: 正常的节流不应告警——把它们算进告警会让告警永远亮着，最终失去意义
#: （告警疲劳）。因此显式排除三类"期望之内的跳过"：
#:
#: - ``no_new_evidence``  —— 证据没增量，门的设计目的就是此时不跑
#: - ``session_budget`` / ``daily_budget`` —— 预算用尽，正常限流
#: - ``halted`` —— 用户主动停机
#:
#: 其余都算"本该跑却没跑成"：它们一旦高频出现，说明链路真的坏了。
ESCALATING_REASONS: frozenset[str] = frozenset(
    {
        "holdout_failed",
        "empty_holdout",
        "proposal_failed",
        "wiring_skipped",
        "no_proposal",
        "no_signed_evidence",
        "signature_unmapped",
        "gateway_unavailable",
        "cycle_crashed",
    }
)


@dataclass
class SkipStats:
    """某一类跳过的累计统计。"""

    reason: str
    total: int = 0
    recent: int = 0
    last_ts: float = 0.0
    escalated_at: float = 0.0
    #: 窗口内的时间戳（跨进程恢复用；不进 ``to_dict`` 的常规字段）。
    marks: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "total": self.total,
            "recent": self.recent,
            "last_ts": self.last_ts,
            "escalated_at": self.escalated_at,
            "marks": list(self.marks),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SkipStats":
        marks: list[float] = []
        for m in raw.get("marks") or []:
            try:
                marks.append(float(m))
            except (TypeError, ValueError):
                continue
        return cls(
            reason=str(raw.get("reason", "")),
            total=int(raw.get("total", 0)),
            recent=int(raw.get("recent", 0)),
            last_ts=float(raw.get("last_ts", 0.0)),
            escalated_at=float(raw.get("escalated_at", 0.0)),
            marks=marks,
        )


class SkipMonitor:
    """按 reason 聚合 ``rsi.cycle_skipped`` 并在超阈时升级告警。

    线程安全（RLock）：事件 sink 可能从多个 worker 线程进来。

    :param emit: ``(kind, data) -> None`` 事件发射器；None = 只记日志
    :param threshold: 单类跳过在窗口内超过它就升级（缺省 3，可参数化）
    :param window_s: 滚动窗口秒数（缺省 24h）
    :param clock: 时间源（测试注入，缺省 ``time.time``）
    """

    def __init__(
        self,
        *,
        emit: Callable[[str, dict[str, Any]], None] | None = None,
        threshold: int = DEFAULT_ESCALATION_THRESHOLD,
        window_s: float = DEFAULT_WINDOW_S,
        clock: Callable[[], float] | None = None,
        max_marks: int = 500,
    ) -> None:
        self._emit = emit
        self._threshold = max(1, int(threshold))
        self._window_s = float(window_s)
        self._clock = clock or time.time
        self._max_marks = max(1, int(max_marks))
        self._lock = threading.RLock()
        # reason -> 窗口内的时间戳序列（deque 便于左端淘汰）
        self._marks: dict[str, deque[float]] = {}
        self._stats: dict[str, SkipStats] = {}

    # ── 主入口 ──────────────────────────────────────────────────────

    def observe(self, event: dict[str, Any] | str, **extra: Any) -> None:
        """记录一次跳过。

        接受两种形态，因为 sink 侧与直接调用侧传的东西不同：

        - ``dict``：``rsi.cycle_skipped`` 的 data（含 ``reason``）
        - ``str``：reason 本身（调用方已解包）
        """
        reason = (
            str(event.get("reason") or "unknown")
            if isinstance(event, dict)
            else str(event or "unknown")
        )
        now = self._clock()
        escalate: SkipStats | None = None

        with self._lock:
            marks = self._marks.setdefault(reason, deque())
            marks.append(now)
            self._prune(marks, now)
            # 有界：只保留最近的 max_marks 个时间戳（跨进程落盘时尤其重要）
            while len(marks) > self._max_marks:
                marks.popleft()

            stats = self._stats.setdefault(reason, SkipStats(reason=reason))
            stats.total += 1
            stats.recent = len(marks)
            stats.last_ts = now
            stats.marks = list(marks)[-self._max_marks:]

            if (
                stats.recent > self._threshold
                and reason in ESCALATING_REASONS
            ):
                stats.escalated_at = now
                escalate = stats

        data: dict[str, Any] = {
            "reason": reason,
            "count_in_window": stats.recent,
            "total": stats.total,
            "window_s": self._window_s,
            "threshold": self._threshold,
        }
        data.update(extra)

        if escalate is not None:
            logger.warning(
                "rsi skip escalated: reason=%s count=%d > threshold=%d "
                "within %.0fs (total=%d)",
                reason, escalate.recent, self._threshold,
                self._window_s, escalate.total,
            )
            self._safe_emit("rsi.skip_escalated", data)
        else:
            logger.debug("rsi skip observed: %s", data)

    def observe_event(self, kind: str, data: dict[str, Any]) -> None:
        """``(kind, data)`` sink 形态的适配器：只订阅 ``rsi.cycle_skipped``。"""
        if kind == "rsi.cycle_skipped":
            self.observe(data)

    # ── 读 ──────────────────────────────────────────────────────────

    def counts(self) -> dict[str, int]:
        """按 reason 的窗口内计数（只含窗口内有活动的类）。"""
        now = self._clock()
        out: dict[str, int] = {}
        with self._lock:
            for reason, marks in self._marks.items():
                self._prune(marks, now)
                if marks:
                    out[reason] = len(marks)
        return out

    def totals(self) -> dict[str, int]:
        """按 reason 的累计计数（含窗口外的历史）。"""
        with self._lock:
            return {r: s.total for r, s in self._stats.items()}

    def snapshot(self) -> dict[str, Any]:
        """可落盘的纯 dict 快照（L1 决定写到哪）。"""
        with self._lock:
            now = self._clock()
            out: list[dict[str, Any]] = []
            for s in self._stats.values():
                marks = self._marks.get(s.reason)
                if marks is not None:
                    self._prune(marks, now)
                    s.marks = list(marks)[-self._max_marks:]
                    s.recent = len(marks)
                out.append(s.to_dict())
            return {
                "window_s": self._window_s,
                "threshold": self._threshold,
                "stats": out,
            }

    def load_snapshot(self, raw: dict[str, Any] | None) -> None:
        """从 ``snapshot()`` 恢复（幂等；缺字段按空处理）。

        **窗口轨迹一起恢复**：24h 滚动窗口的语义是"跨进程连续"。只恢复
        累计量而不恢复时间戳，会让每次进程重启都把窗口清零——一天里分散
        在多次 CLI 调用中的跳过就永远攒不到阈值，告警形同虚设。
        时间戳按当前时钟淘汰，旧进程留下的过期点不会误触发。
        """
        if not raw:
            return
        stats = raw.get("stats") or []
        now = self._clock()
        with self._lock:
            for item in stats:
                if not isinstance(item, dict):
                    continue
                s = SkipStats.from_dict(item)
                if not s.reason:
                    continue
                marks = deque(float(m) for m in s.marks)
                self._prune(marks, now)
                self._marks[s.reason] = marks
                s.recent = len(marks)
                s.marks = list(marks)
                self._stats[s.reason] = s

    # ── helpers ─────────────────────────────────────────────────────

    def _prune(self, marks: deque[float], now: float) -> None:
        cutoff = now - self._window_s
        while marks and marks[0] < cutoff:
            marks.popleft()

    def _safe_emit(self, kind: str, data: dict[str, Any]) -> None:
        if self._emit is None:
            return
        try:
            self._emit(kind, data)
        except Exception:  # noqa: BLE001 —— 告警发不出去不得影响主流程
            logger.warning("skip monitor emit failed: %s", kind, exc_info=True)


def monitor_from_sink(
    sink: Callable[[str, dict[str, Any]], None] | None = None,
    *,
    threshold: int = DEFAULT_ESCALATION_THRESHOLD,
) -> tuple[SkipMonitor, Callable[[], None]] | tuple[None, None]:
    """便捷装配：建一个 monitor 并把它挂成 sink 的观察者。

    返回 ``(monitor, unsubscribe)``；``sink`` 不支持 ``add_sink``（测试替身
    或纯函数）时返回 ``(None, None)``——调用方应直接持有 monitor 调
    ``observe()``。
    """
    add = getattr(sink, "add_sink", None) if sink is not None else None
    if not callable(add):
        return None, None
    monitor = SkipMonitor(emit=sink, threshold=threshold)
    unsubscribe = add(monitor.observe_event)
    return monitor, unsubscribe


def summarize(skip_events: Iterable[dict[str, Any]]) -> dict[str, int]:
    """纯函数：把一批 ``rsi.cycle_skipped`` data 汇总成 reason→count。"""
    out: dict[str, int] = {}
    for ev in skip_events:
        reason = str((ev or {}).get("reason") or "unknown")
        out[reason] = out.get(reason, 0) + 1
    return out


__all__ = [
    "DEFAULT_ESCALATION_THRESHOLD",
    "DEFAULT_WINDOW_S",
    "ESCALATING_REASONS",
    "SkipMonitor",
    "SkipStats",
    "monitor_from_sink",
    "summarize",
]
