"""Ticket / Assignment — persistent task identity and re-entrant execution binding.

阶段一（docs/Super-agent-RSI升级施工方案.md §2）：把"一次 turn 准入"从运行时
概念升级为持久化实体。

- ``Ticket``     = 任务身份（"要做什么"），持久化，可恢复
- ``Assignment`` = 执行绑定（"谁在做、做到哪"），可重入
- 每次 Assignment 状态变更 → ``assignment.report`` 事件 → EventStore（H6）

**为什么放 apps/study/ 而非 kernel/**（施工方案 §2.5）：
``Ticket.objective`` / ``authorization_scope`` 携带研究域业务身份（Tier 1 范围）。
放 kernel 会让阶段三 EvolutionAgent 读 Ticket 时加重 L3→L2 违规（H1/H2）。
kernel 侧只通过 ``CapabilityRegistry.resolve("ticket_store")`` 读，不直接 import。

**状态口径**（施工方案 §2.2 尾注）：
- Ticket 三态：pending / active / ended（ended 是终态）
- Assignment 七态：pending + 六个会 emit report 的态
  （running/waiting/ready/blocked/failed/dropped）。
  ``pending`` 是创建时态，**不 emit** report — 这是 goal §3「六态」的口径。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any


# ── Errors ───────────────────────────────────────────────────────────


class StaleTicketError(ValueError):
    """Raised on an illegal Ticket/Assignment state transition.

    与 ``apps/goal/models.py`` 的 ``StaleGoalError`` 同族语义（都是
    "状态已过期，不能再转移"），但独立成类：Ticket 的状态机与 Goal 的
    12 态生命周期无关，共用会让两者的合法转移集合互相污染。
    """


# ── Ticket ───────────────────────────────────────────────────────────


class TicketStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    ENDED = "ended"


#: 合法转移：source -> allowed targets。``ended`` 是终态。
_TICKET_TRANSITIONS: dict[TicketStatus, frozenset[TicketStatus]] = {
    TicketStatus.PENDING: frozenset({TicketStatus.ACTIVE, TicketStatus.ENDED}),
    TicketStatus.ACTIVE: frozenset({TicketStatus.ENDED}),
    TicketStatus.ENDED: frozenset(),
}


@dataclass(frozen=True)
class Ticket:
    """任务身份。同一 ``ticket_id`` 多次准入只保留一行（幂等）。"""

    ticket_id: str
    objective: str
    origin_session_key: str
    authorization_scope: dict[str, Any] = field(default_factory=dict)
    priority: int = 0
    status: TicketStatus = TicketStatus.PENDING
    superseded_by: str | None = None
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def new(
        cls,
        objective: str,
        *,
        origin_session_key: str = "",
        authorization_scope: dict[str, Any] | None = None,
        priority: int = 0,
        now: str = "",
        ticket_id: str | None = None,
    ) -> "Ticket":
        """Create a fresh Ticket in ``pending`` (not yet admitted)."""
        tid = ticket_id or f"tkt-{uuid.uuid4()}"
        ts = now or _now_iso()
        if not 0 <= priority <= 9:
            raise StaleTicketError(f"priority out of range 0-9: {priority}")
        return cls(
            ticket_id=tid,
            objective=objective,
            origin_session_key=origin_session_key,
            authorization_scope=dict(authorization_scope or {}),
            priority=priority,
            status=TicketStatus.PENDING,
            superseded_by=None,
            created_at=ts,
            updated_at=ts,
        )

    def transition_to(
        self, new_status: TicketStatus, *, now: str = "", superseded_by: str | None = None
    ) -> "Ticket":
        """Return a new Ticket in ``new_status``; raise on illegal move.

        Legal moves (施工方案 §2.3):
            pending -> active      （SessionController 准入）
            pending -> ended       （准入前取消）
            active  -> ended       （正常结束 / 被取代）
        ``ended`` 是终态：任何从 ended 出发的转移都是 Illegal。
        """
        allowed = _TICKET_TRANSITIONS.get(self.status, frozenset())
        if new_status not in allowed:
            raise StaleTicketError(
                f"illegal ticket transition {self.status.value} -> {new_status.value} "
                f"(ticket_id={self.ticket_id})"
            )
        if new_status is TicketStatus.ENDED and superseded_by is not None:
            if self.superseded_by is not None:
                raise StaleTicketError(
                    f"ticket {self.ticket_id} already superseded by {self.superseded_by}"
                )
        return replace(
            self,
            status=new_status,
            superseded_by=superseded_by if superseded_by is not None else self.superseded_by,
            updated_at=now or _now_iso(),
        )

    @property
    def is_terminal(self) -> bool:
        return self.status is TicketStatus.ENDED


# ── Assignment ───────────────────────────────────────────────────────


class AssignmentStatus(str, Enum):
    PENDING = "pending"  # 创建，未开始（不 emit report）
    RUNNING = "running"
    WAITING = "waiting"
    READY = "ready"
    BLOCKED = "blocked"
    FAILED = "failed"
    DROPPED = "dropped"
    ENDED = "ended"  # 终态，不 emit 额外事件


#: 会 emit ``assignment.report`` 的六个态（goal §3 口径）。
REPORT_STATUSES: frozenset[AssignmentStatus] = frozenset(
    {
        AssignmentStatus.RUNNING,
        AssignmentStatus.WAITING,
        AssignmentStatus.READY,
        AssignmentStatus.BLOCKED,
        AssignmentStatus.FAILED,
        AssignmentStatus.DROPPED,
    }
)

#: 合法转移。``pending -> running`` 由 GraphEngine 开始执行时触发；
#: ``running`` 可落到任意 report 态，也可直接 ``ended``（施工方案 §2.3
#: 的「Assignment.* → ended」）；所有 report 态只能走向 ``ended``。
#:
#: ``pending`` 也能直接落到 ``failed`` / ``dropped``：turn 可能在引擎
#: 真正开始前就失败（客户端构造错误、fence 被超越）。这不是
#: "跳过了 running"，而是"从未开始"——必须允许，否则这类失败无法
#: 被持久化记录，Assignment 会永远停在 pending（可观测性黑洞）。
_ASSIGNMENT_TRANSITIONS: dict[AssignmentStatus, frozenset[AssignmentStatus]] = {
    AssignmentStatus.PENDING: frozenset(
        {
            AssignmentStatus.RUNNING,
            AssignmentStatus.READY,
            AssignmentStatus.FAILED,
            AssignmentStatus.DROPPED,
        }
    ),
    AssignmentStatus.RUNNING: frozenset(
        {
            AssignmentStatus.WAITING,
            AssignmentStatus.READY,
            AssignmentStatus.BLOCKED,
            AssignmentStatus.FAILED,
            AssignmentStatus.DROPPED,
            AssignmentStatus.ENDED,
        }
    ),
    AssignmentStatus.WAITING: frozenset(
        {AssignmentStatus.RUNNING, AssignmentStatus.ENDED}
    ),
    AssignmentStatus.READY: frozenset(
        {AssignmentStatus.RUNNING, AssignmentStatus.ENDED}
    ),
    AssignmentStatus.BLOCKED: frozenset(
        {AssignmentStatus.RUNNING, AssignmentStatus.ENDED}
    ),
    AssignmentStatus.FAILED: frozenset({AssignmentStatus.ENDED}),
    AssignmentStatus.DROPPED: frozenset({AssignmentStatus.ENDED}),
    AssignmentStatus.ENDED: frozenset(),
}


@dataclass(frozen=True)
class Assignment:
    """执行绑定。同一 Ticket 可有多条 Assignment（对应重试）。"""

    assignment_id: str
    ticket_id: str
    run_id: str
    worker_session_id: str
    status: AssignmentStatus = AssignmentStatus.PENDING
    fence_token: str = ""
    report_payload: dict[str, Any] = field(default_factory=dict)
    started_at: str = ""
    ended_at: str | None = None

    @classmethod
    def new(
        cls,
        ticket_id: str,
        run_id: str,
        *,
        worker_session_id: str = "",
        fence_token: str = "",
        now: str = "",
        assignment_id: str | None = None,
    ) -> "Assignment":
        """Create a fresh Assignment in ``pending`` (no report emitted)."""
        return cls(
            assignment_id=assignment_id or f"asg-{uuid.uuid4()}",
            ticket_id=ticket_id,
            run_id=run_id,
            worker_session_id=worker_session_id,
            status=AssignmentStatus.PENDING,
            fence_token=fence_token,
            report_payload={},
            started_at=now or _now_iso(),
            ended_at=None,
        )

    def transition_to(
        self,
        new_status: AssignmentStatus,
        *,
        payload: dict[str, Any] | None = None,
        now: str = "",
    ) -> "Assignment":
        """Return a new Assignment in ``new_status``; raise on illegal move."""
        allowed = _ASSIGNMENT_TRANSITIONS.get(self.status, frozenset())
        if new_status not in allowed:
            raise StaleTicketError(
                f"illegal assignment transition {self.status.value} -> {new_status.value} "
                f"(assignment_id={self.assignment_id})"
            )
        ts = now or _now_iso()
        return replace(
            self,
            status=new_status,
            report_payload=dict(payload) if payload is not None else self.report_payload,
            ended_at=ts if new_status is AssignmentStatus.ENDED else self.ended_at,
        )

    @property
    def should_emit_report(self) -> bool:
        """True when this status must produce an ``assignment.report`` event.

        ``pending`` / ``ended`` are silent: pending is the creation state and
        ended is the terminal write (施工方案 §2.3 关键约束).
        """
        return self.status in REPORT_STATUSES

    def report_event_data(self) -> dict[str, Any]:
        """Build the ``assignment.report`` payload (施工方案 §2.7 schema).

        Raises when called on a non-report status — emitting a report for
        ``pending``/``ended`` would violate the six-state contract.
        """
        if not self.should_emit_report:
            raise StaleTicketError(
                f"status {self.status.value} does not emit assignment.report"
            )
        payload = dict(self.report_payload)
        return {
            "type": "assignment.report",
            "run_id": self.run_id,
            "assignment_id": self.assignment_id,
            "ticket_id": self.ticket_id,
            "status": self.status.value,
            "payload": {
                "reason": payload.get("reason"),
                "progress_pct": payload.get("progress_pct", 0.0),
                "current_step": payload.get("current_step", ""),
                "blockers": list(payload.get("blockers", [])),
            },
            "ts": self.started_at,
        }


# ── helpers ──────────────────────────────────────────────────────────


def _now_iso() -> str:
    """ISO8601 UTC timestamp with a trailing Z (matches event schema)."""
    import datetime as _dt

    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


__all__ = [
    "StaleTicketError",
    "Ticket",
    "TicketStatus",
    "Assignment",
    "AssignmentStatus",
    "REPORT_STATUSES",
]
