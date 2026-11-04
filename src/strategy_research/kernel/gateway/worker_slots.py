"""WorkerSlotManager — 订阅 ``assignment.report`` 管理 Worker Slot（施工方案 §2.8）。

**职责边界**：只订阅事件，**不读 Ticket 表**。这是刻意的——
Ticket 是业务身份（apps/study），Slot 是网关资源（kernel）。
让 Slot 管理去查 Ticket 会把 kernel 拖回 apps 依赖（H1）。

**Slot 动作表（施工方案 §2.8）**：

| report.status | Slot 动作 |
|---|---|
| running | 保留（首次遇到时分配） |
| waiting | 释放（进入等待，不占 slot） |
| ready   | 释放（等待人工审核） |
| blocked | 释放 |
| failed  | 释放 |
| dropped | 释放 |

Slot 释放后 Assignment 仍在表里、Ticket 仍是 active；重新准入 = 新
Assignment，Ticket 不变——所以这里释放 slot 不代表"任务结束"。

**与容量闸的分工**：容量闸（阶段二）管"能不能起"，本模块管"这个
assignment 占着哪个 Worker"。两者是不同的资源：容量闸的池是**并发准入
上限**，Slot 是**单个 assignment 的执行位**。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: 会释放 slot 的状态（施工方案 §2.8 表格）。
RELEASE_STATUSES: frozenset[str] = frozenset(
    {"waiting", "ready", "blocked", "failed", "dropped"}
)

#: 保留（或首次分配）slot 的状态。
HOLD_STATUSES: frozenset[str] = frozenset({"running"})


@dataclass(frozen=True)
class SlotAssignment:
    """一个 assignment 与它占用的 slot。"""

    assignment_id: str
    run_id: str
    ticket_id: str
    slot: int


@dataclass
class WorkerSlotManager:
    """按 ``assignment.report`` 分配/释放 Worker Slot。线程安全。

    :param max_slots: slot 总数。None = 不限制（只有观测意义）
    """

    max_slots: int | None = None
    _slots: dict[str, SlotAssignment] = field(default_factory=dict, init=False)
    _free: list[int] = field(default_factory=list, init=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False)
    _events: int = field(default=0, init=False)
    _released: int = field(default=0, init=False)
    _ignored: int = field(default=0, init=False)
    _next_unlimited: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if self.max_slots is not None:
            self._free = list(range(self.max_slots))

    # ── 事件入口 ────────────────────────────────────────────────────

    def on_event(self, kind: str, data: dict[str, Any]) -> None:
        """``Gateway`` sink 契约：``(kind, data)``。

        只关心 ``assignment.report``，其余原样忽略（计数便于审计）。
        """
        if kind != "assignment.report":
            return
        self.handle_report(data)

    def handle_report(self, data: dict[str, Any]) -> None:
        """按 report 的 status 决定保留/释放 slot。"""
        self._events += 1
        assignment_id = str(data.get("assignment_id") or "")
        status = str(data.get("status") or "")
        if not assignment_id:
            self._ignored += 1
            logger.debug("assignment.report without assignment_id ignored")
            return

        if status in HOLD_STATUSES:
            self._hold(assignment_id, data)
            return
        if status in RELEASE_STATUSES:
            self._release(assignment_id, status)
            return

        # pending / ended / 未知状态：不动作（pending 本就不 emit；
        # ended 是终态写入，slot 应已在此之前释放）。
        self._ignored += 1

    # ── 内部 ────────────────────────────────────────────────────────

    def _hold(self, assignment_id: str, data: dict[str, Any]) -> None:
        with self._lock:
            if assignment_id in self._slots:
                return  # 已占用，保持（running→running 不重复分配）
            if self.max_slots is not None and not self._free:
                logger.warning(
                    "no free worker slot for %s (max=%s) — keeping it tracked "
                    "without a slot number",
                    assignment_id, self.max_slots,
                )
                self._slots[assignment_id] = SlotAssignment(
                    assignment_id=assignment_id,
                    run_id=str(data.get("run_id") or ""),
                    ticket_id=str(data.get("ticket_id") or ""),
                    slot=-1,
                )
                return
            if self.max_slots is None:
                # 不限量：每个 assignment 拿一个自增 slot（仍可观测）
                slot = self._next_unlimited
                self._next_unlimited += 1
            else:
                slot = self._free.pop(0)
            self._slots[assignment_id] = SlotAssignment(
                assignment_id=assignment_id,
                run_id=str(data.get("run_id") or ""),
                ticket_id=str(data.get("ticket_id") or ""),
                slot=slot,
            )
            logger.debug("slot %s assigned to %s", slot, assignment_id)

    def _release(self, assignment_id: str, status: str) -> None:
        with self._lock:
            held = self._slots.pop(assignment_id, None)
            if held is None:
                return
            if held.slot >= 0:
                self._free.append(held.slot)
                self._free.sort()
            self._released += 1
            logger.debug(
                "slot %s released from %s (status=%s)",
                held.slot, assignment_id, status,
            )

    # ── 观测 ────────────────────────────────────────────────────────

    def active(self) -> dict[str, SlotAssignment]:
        with self._lock:
            return dict(self._slots)

    def usage(self) -> dict[str, Any]:
        with self._lock:
            used = len([s for s in self._slots.values() if s.slot >= 0])
            return {
                "active": len(self._slots),
                "used_slots": used,
                "free_slots": len(self._free),
                "max_slots": self.max_slots,
                "events_seen": self._events,
                "released": self._released,
                "ignored": self._ignored,
            }

    def slot_of(self, assignment_id: str) -> int | None:
        with self._lock:
            held = self._slots.get(assignment_id)
            return held.slot if held else None


__all__ = [
    "HOLD_STATUSES",
    "RELEASE_STATUSES",
    "SlotAssignment",
    "WorkerSlotManager",
]
