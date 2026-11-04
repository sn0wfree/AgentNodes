"""SQLite-backed TicketSink — 阶段一的业务侧实现（施工方案 §2.6）。

``SessionController`` 通过**构造参数注入**拿着它（kernel 不得 import apps，
H1/ADR-000），控制器只在准入/收尾路径调用两个方法：

- ``admit(...)``  → upsert Ticket（幂等）+ create Assignment（pending）
- ``settle(...)`` → Assignment 推终态 + emit ``assignment.report``

**为什么这一层存在**：Ticket 携带研究域业务身份（objective /
authorization_scope），属 Tier 1；而 kernel 只认一个 duck-typed 接口。
这样阶段三 EvolutionAgent 通过 ``CapabilityRegistry.resolve("ticket_sink")``
读取，不加重 L3→L2（H1/H2）。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from .store import StudyStore
from .ticket import (
    Assignment,
    AssignmentStatus,
    Ticket,
    TicketStatus,
)

logger = logging.getLogger(__name__)

#: ``settle()`` 的 status 字符串 → AssignmentStatus。
#: 只接受六个会 emit report 的态（施工方案 §2.7）；``pending`` 不在其中。
_SETTLE_STATUS: dict[str, AssignmentStatus] = {
    "running": AssignmentStatus.RUNNING,
    "waiting": AssignmentStatus.WAITING,
    "ready": AssignmentStatus.READY,
    "blocked": AssignmentStatus.BLOCKED,
    "failed": AssignmentStatus.FAILED,
    "dropped": AssignmentStatus.DROPPED,
}


class TicketSink:
    """Ticket/Assignment 的持久化 + 事件发射。

    :param store_factory: 返回 ``StudyStore`` 的可调用（便于测试注入
        tmp 路径；默认 ``StudyStore``）。
    :param emit: ``(event_type, data) -> None`` 的事件发射器，通常接到
        EventStore。None = 只持久化不发射（测试路径）。
    """

    def __init__(
        self,
        store_factory: Callable[[], StudyStore] | None = None,
        *,
        emit: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._store_factory = store_factory or (lambda: StudyStore())
        self._emit = emit

    # ── 准入 ────────────────────────────────────────────────────────

    def admit(
        self,
        *,
        session_key: str,
        session_id: str,
        run_id: str,
        turn_id: str,
        objective: str,
        fence_token: str = "",
    ) -> Assignment:
        """准入一个 turn：upsert Ticket（幂等）+ create Assignment(pending)。

        返回新建的 Assignment，供控制器在收尾时 ``settle()``。
        任何异常都向上抛，由控制器降级为 ``ticket.admit_failed`` 事件。
        """
        with self._store_factory() as store:
            # 一个 session_key 对应一个 Ticket（任务身份），turn 只增 Assignment。
            existing = self._find_ticket_for(store, session_key)
            if existing is None:
                ticket = Ticket.new(
                    objective=objective,
                    origin_session_key=session_key,
                )
                ticket = ticket.transition_to(TicketStatus.ACTIVE)
            else:
                ticket = existing
                if ticket.status is TicketStatus.PENDING:
                    ticket = ticket.transition_to(TicketStatus.ACTIVE)
            store.upsert_ticket(ticket)

            assignment = Assignment.new(
                ticket.ticket_id,
                run_id,
                worker_session_id=session_id,
                fence_token=fence_token,
            )
            store.create_assignment(assignment)

        if self._emit is not None:
            self._emit("ticket.upserted", {
                "ticket_id": ticket.ticket_id,
                "objective": ticket.objective,
                "status": ticket.status.value,
            })
        return assignment

    # ── 收尾 ────────────────────────────────────────────────────────

    def settle(
        self,
        *,
        assignment: Assignment,
        status: str,
        reason: str | None = None,
    ) -> Assignment:
        """把 Assignment 推到 ``status`` 终态并 emit ``assignment.report``。

        ``status`` 只接受六个 report 态（施工方案 §2.7 口径）；非法值抛
        ``ValueError``（控制器已 catch，不会打断 turn）。
        """
        target = _SETTLE_STATUS.get(status)
        if target is None:
            raise ValueError(
                f"settle() status must be one of {sorted(_SETTLE_STATUS)}, got {status!r}"
            )

        payload: dict[str, Any] = {}
        if reason:
            payload["reason"] = reason

        moved = assignment
        # 路径可能是 pending→X（引擎尚未启动）或 running→X；也允许
        # running→ended 后再 settle（上游已收尾）。逐跳转移以复用状态机校验。
        if moved.status is AssignmentStatus.PENDING and target is not (
            AssignmentStatus.RUNNING
        ):
            moved = moved.transition_to(target, payload=payload)
        elif moved.status is AssignmentStatus.RUNNING:
            moved = moved.transition_to(target, payload=payload)
        elif moved.status is not target:
            moved = moved.transition_to(target, payload=payload)

        with self._store_factory() as store:
            store.update_assignment(moved)

        if self._emit is not None and moved.should_emit_report:
            self._emit("assignment.report", moved.report_event_data())
        return moved

    # ── 崩溃恢复 ────────────────────────────────────────────────────

    #: 非终态 status（崩溃后需要恢复的）。failed/dropped/ended 是终态。
    NON_TERMINAL: frozenset[str] = frozenset(
        {"pending", "running", "waiting", "ready", "blocked"}
    )

    def recover(self) -> list[Assignment]:
        """返回所有非终态 Assignment（进程重启后仍待办的那些）。

        纯读：不改任何行、不发事件。调用方（Gateway.recover）决定如何
        纳入记账。emit 也留给定调用方——恢复事件的 reason 取决于
        "为什么重启"，那属于运维上下文，不是本模块能知道的。
        """
        out: list[Assignment] = []
        with self._store_factory() as store:
            for a in store.list_assignments(limit=1000):
                if a.status.value in self.NON_TERMINAL:
                    out.append(a)
        return out

    def recover_and_mark(self, reason: str = "restart") -> list[Assignment]:
        """恢复非终态 Assignment，把它们标成 ``blocked`` 并 emit report。

        **只恢复状态，不恢复执行**（施工方案定稿：auto_resume=false）。
        标注 ``blocked`` 而不是 ``failed``：这些任务没失败，只是进程重启
        打断了它们，需要重新准入——用 failed 会污染失败率统计。
        """
        recovered: list[Assignment] = []
        for a in self.recover():
            try:
                moved = a.transition_to(
                    AssignmentStatus.BLOCKED,
                    payload={"reason": reason, "current_step": "restart"},
                )
            except Exception:  # noqa: BLE001 —— 转移不合法就跳过该条
                logger.debug("cannot mark %s as blocked", a.assignment_id, exc_info=True)
                continue
            with self._store_factory() as store:
                store.update_assignment(moved)
            if self._emit is not None and moved.should_emit_report:
                self._emit("assignment.report", moved.report_event_data())
            recovered.append(moved)
        return recovered

    # ── helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _find_ticket_for(store: StudyStore, session_key: str) -> Ticket | None:
        """Return the active/pending Ticket for a session_key, if any.

        同一 session 的后续 turn 复用同一个 Ticket（任务身份不变，
        执行绑定每次新建）—— 施工方案 §2.4。
        """
        for t in store.list_tickets(limit=500):
            if t.origin_session_key != session_key:
                continue
            if t.status in (TicketStatus.ACTIVE, TicketStatus.PENDING):
                return t
        return None


__all__ = ["TicketSink"]
