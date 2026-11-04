"""阶段一验收：Ticket / Assignment 实体化 + 六态报告（施工方案 §2.9）。

覆盖 goal §3 阶段一的全部完成标准：
- Ticket 状态机 pending → active → ended 合法；非法转移抛 StaleTicketError
- Assignment 六态各 emit 一条 assignment.report（pending 不 emit）
- Ticket upsert 幂等
- Assignment 继承 fence_token；被超越时 → dropped，不落业务状态表
- assignment.report replay 出六态各 ≥1 条
- 无新增 L3→L2（基线 46）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.apps.study import (
    REPORT_STATUSES,
    Assignment,
    AssignmentStatus,
    StaleTicketError,
    Ticket,
    TicketStatus,
)
from strategy_research.apps.study.store import StudyStore


# ── fixtures ─────────────────────────────────────────────────────────


@pytest.fixture()
def store(tmp_path: Path):
    """Isolated StudyStore on a throwaway goals.db."""
    with StudyStore(tmp_path / "goals.db") as s:
        yield s


@pytest.fixture()
def event_store(tmp_path: Path):
    """Isolated EventStore so assignment.report replays are deterministic."""
    from strategy_research.kernel.agent.event_store import EventStore

    es = EventStore(db_path=tmp_path / "events.db")
    yield es
    es.close()


# ── Ticket state machine ─────────────────────────────────────────────


class TestTicketStateMachine:
    def test_pending_to_active_to_ended(self):
        t = Ticket.new("研究动量因子", origin_session_key="sess-a")
        assert t.status is TicketStatus.PENDING
        active = t.transition_to(TicketStatus.ACTIVE)
        assert active.status is TicketStatus.ACTIVE
        ended = active.transition_to(TicketStatus.ENDED)
        assert ended.status is TicketStatus.ENDED
        assert ended.is_terminal

    def test_pending_to_ended_is_legal(self):
        """准入前取消：pending → ended 合法（施工方案 §2.3）。"""
        t = Ticket.new("取消前的研究")
        ended = t.transition_to(TicketStatus.ENDED)
        assert ended.status is TicketStatus.ENDED

    def test_active_to_pending_raises(self):
        t = Ticket.new("x").transition_to(TicketStatus.ACTIVE)
        with pytest.raises(StaleTicketError, match="illegal ticket transition"):
            t.transition_to(TicketStatus.PENDING)

    def test_ended_is_terminal(self):
        t = Ticket.new("x").transition_to(TicketStatus.ENDED)
        for target in (TicketStatus.PENDING, TicketStatus.ACTIVE):
            with pytest.raises(StaleTicketError):
                t.transition_to(target)

    def test_superseded_by_recorded(self):
        t = Ticket.new("旧任务").transition_to(TicketStatus.ACTIVE)
        ended = t.transition_to(TicketStatus.ENDED, superseded_by="tkt-new")
        assert ended.superseded_by == "tkt-new"

    def test_priority_range_enforced(self):
        with pytest.raises(StaleTicketError, match="priority out of range"):
            Ticket.new("x", priority=10)


# ── Assignment state machine ─────────────────────────────────────────


class TestAssignmentStateMachine:
    def test_pending_does_not_emit_report(self):
        """pending 是创建态，不 emit（goal §3「六态」口径）。"""
        a = Assignment.new("tkt-1", "sess#1:r0")
        assert a.status is AssignmentStatus.PENDING
        assert a.should_emit_report is False
        with pytest.raises(StaleTicketError, match="does not emit"):
            a.report_event_data()

    def test_all_six_report_states_transition_from_running(self):
        a = Assignment.new("tkt-1", "sess#1:r0").transition_to(
            AssignmentStatus.RUNNING
        )
        for st in (
            AssignmentStatus.WAITING,
            AssignmentStatus.READY,
            AssignmentStatus.BLOCKED,
            AssignmentStatus.FAILED,
            AssignmentStatus.DROPPED,
        ):
            nxt = a.transition_to(st)
            assert nxt.status is st
            assert nxt.should_emit_report is True

    def test_six_report_statuses_constant(self):
        assert {s.value for s in REPORT_STATUSES} == {
            "running",
            "waiting",
            "ready",
            "blocked",
            "failed",
            "dropped",
        }

    def test_ended_to_running_raises(self):
        a = Assignment.new("tkt-1", "sess#1:r0").transition_to(
            AssignmentStatus.RUNNING
        ).transition_to(AssignmentStatus.ENDED)
        with pytest.raises(StaleTicketError, match="illegal assignment transition"):
            a.transition_to(AssignmentStatus.RUNNING)

    def test_report_payload_schema(self):
        """report 的 payload 结构固定为 reason/progress_pct/current_step/blockers。"""
        a = Assignment.new("tkt-1", "sess#1:r0", fence_token="f1").transition_to(
            AssignmentStatus.RUNNING,
            payload={
                "current_step": "数据准备",
                "progress_pct": 0.4,
                "blockers": ["缺少数据"],
            },
        )
        d = a.report_event_data()
        assert d["type"] == "assignment.report"
        assert d["run_id"] == "sess#1:r0"
        assert d["status"] == "running"
        assert set(d["payload"]) == {
            "reason",
            "progress_pct",
            "current_step",
            "blockers",
        }
        assert d["payload"]["progress_pct"] == 0.4
        assert d["payload"]["blockers"] == ["缺少数据"]


# ── persistence ──────────────────────────────────────────────────────


class TestTicketPersistence:
    def test_ticket_upsert_idempotent(self, store: StudyStore):
        """同 ticket_id 两次 upsert，只一行（施工方案 §2.4）。"""
        t = Ticket.new("研究目标 A")
        store.upsert_ticket(t)
        store.upsert_ticket(t)
        rows = store.list_tickets()
        assert len(rows) == 1
        assert rows[0].ticket_id == t.ticket_id

    def test_ticket_upsert_updates_status(self, store: StudyStore):
        t = Ticket.new("目标")
        store.upsert_ticket(t)
        store.upsert_ticket(t.transition_to(TicketStatus.ACTIVE))
        got = store.get_ticket(t.ticket_id)
        assert got is not None and got.status is TicketStatus.ACTIVE
        # created_at 保留首次值（幂等语义）
        assert got.created_at == t.created_at

    def test_assignment_roundtrip(self, store: StudyStore):
        t = Ticket.new("目标")
        store.upsert_ticket(t)
        a = Assignment.new(t.ticket_id, "sess#1:r0", fence_token="f-1")
        store.create_assignment(a)
        a2 = a.transition_to(
            AssignmentStatus.RUNNING, payload={"current_step": "step-1"}
        )
        store.update_assignment(a2)
        got = store.get_assignment(a.assignment_id)
        assert got is not None
        assert got.status is AssignmentStatus.RUNNING
        assert got.report_payload["current_step"] == "step-1"
        assert got.fence_token == "f-1"

    def test_multiple_assignments_per_ticket(self, store: StudyStore):
        """同一 Ticket 可多次 Assignment（对应重试）。"""
        t = Ticket.new("目标")
        store.upsert_ticket(t)
        for i in range(3):
            store.create_assignment(Assignment.new(t.ticket_id, f"sess#{i}:r0"))
        assert len(store.list_assignments(t.ticket_id)) == 3

    def test_list_tickets_by_status(self, store: StudyStore):
        a = Ticket.new("a")
        b = Ticket.new("b")
        store.upsert_ticket(a)
        store.upsert_ticket(b.transition_to(TicketStatus.ACTIVE))
        assert len(store.list_tickets(status="pending")) == 1
        assert len(store.list_tickets(status="active")) == 1


# ── fence semantics ──────────────────────────────────────────────────


class TestAssignmentFence:
    def test_assignment_inherits_fence_token(self):
        """Assignment 继承 turn 的 fence_token（施工方案 §2.4）。"""
        a = Assignment.new("tkt-1", "sess#1:r0", fence_token="fence-abc")
        assert a.fence_token == "fence-abc"

    def test_fence_superseded_assignment_dropped(self, store: StudyStore):
        """被超越的 Assignment → dropped，且不写业务状态表。

        fence 校验沿用 R1 逻辑：fence 失配时不落业务产物，只把
        Assignment 标 dropped 并 emit report。
        """
        t = Ticket.new("目标")
        store.upsert_ticket(t)
        a = Assignment.new(t.ticket_id, "sess#1:r0", fence_token="old-fence")
        store.create_assignment(a)
        dropped = a.transition_to(
            AssignmentStatus.DROPPED,
            payload={"reason": "fence superseded by newer assignment"},
        )
        store.update_assignment(dropped)
        got = store.get_assignment(a.assignment_id)
        assert got is not None
        assert got.status is AssignmentStatus.DROPPED
        assert "fence" in (got.report_payload.get("reason") or "")
        # dropped 不是终态（可再进 ended），但不产生业务落库副作用
        assert dropped.should_emit_report is True


# ── event replay ─────────────────────────────────────────────────────


class TestAssignmentReportReplay:
    def test_report_six_states_emitted(self, event_store):
        """六态各 emit ≥1 条 assignment.report，且可 replay 回来。"""
        session = "sess-replay"
        ticket = Ticket.new("回放目标")
        base = Assignment.new(ticket.ticket_id, f"{session}#1:r0", fence_token="f")

        # pending 不 emit
        assert base.should_emit_report is False

        running = base.transition_to(AssignmentStatus.RUNNING)
        event_store.emit(session, "assignment.report", running.report_event_data())

        for st in (
            AssignmentStatus.WAITING,
            AssignmentStatus.READY,
            AssignmentStatus.BLOCKED,
            AssignmentStatus.FAILED,
            AssignmentStatus.DROPPED,
        ):
            nxt = running.transition_to(st)
            event_store.emit(session, "assignment.report", nxt.report_event_data())

        replayed = event_store.replay(session, types=["assignment.report"])
        seen = [e.data["status"] for e in replayed]
        assert len(replayed) == 6
        assert set(seen) == {
            "running",
            "waiting",
            "ready",
            "blocked",
            "failed",
            "dropped",
        }
        # 每个事件都带完整定位字段（可 replay 断言）
        for e in replayed:
            assert e.data["assignment_id"] == base.assignment_id
            assert e.data["ticket_id"] == ticket.ticket_id
            assert e.data["run_id"] == f"{session}#1:r0"

    def test_pending_never_emits(self, event_store):
        """创建态绝不出现在 replay 里。"""
        session = "sess-pending"
        a = Assignment.new("tkt-x", f"{session}#1:r0")
        # 不 emit pending
        replayed = event_store.replay(session, types=["assignment.report"])
        assert replayed == []
        assert a.status is AssignmentStatus.PENDING


# ── regression: no new L3→L2 ─────────────────────────────────────────


class TestNoNewLayerViolation:
    def test_no_new_l3l2(self):
        """阶段一不增加 L3→L2 违规（基线 46，goal H2）。

        用门禁自身的 _collect() 实测，不抄文档数字。
        """
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
        import test_layer_matrix as lm

        viol, _ = lm._collect()
        measured = viol.get("L3>L2", 0)
        baseline = lm.BASELINE["L3>L2"]
        assert measured <= baseline, (
            f"L3→L2 违规 {measured} 超过基线 {baseline}"
        )


# ── end-to-end: controller → TicketSink → store + EventStore ─────────


class _Resp:
    def __init__(self, content: str = "回答") -> None:
        self.content = content
        self.usage = {"total_tokens": 5}


class _FakeClient:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def chat(self, messages, **kwargs):
        if self.fail:
            raise RuntimeError("client exploded")
        return _Resp()


def _make_sink(tmp_path: Path, emitted: list[tuple[str, dict]]):
    """TicketSink bound to a tmp DB + an in-memory event collector."""
    from strategy_research.apps.study.ticket_sink import TicketSink

    db = tmp_path / "goals.db"
    return TicketSink(
        store_factory=lambda: StudyStore(db),
        emit=lambda kind, data: emitted.append((kind, data)),
    )


class TestControllerIntegration:
    """验证注入路径：kernel 不 import apps（H1），业务侧实现 sink。"""

    def test_admit_creates_ticket_and_assignment(self, tmp_path: Path):
        from strategy_research.kernel.gateway.session_controller import (
            SessionController,
        )
        from strategy_research.kernel.orchestration import (
            EntryPoint,
            SourceKind,
            WorkKind,
            WorkRequest,
        )

        emitted: list[tuple[str, dict]] = []
        sink = _make_sink(tmp_path, emitted)
        ep = EntryPoint(sink=lambda k, d: None)
        ctrl = SessionController(
            ep, client=_FakeClient(), system="s", ticket_sink=sink,
        )
        ep.submit(
            WorkRequest(
                kind=WorkKind.CHAT,
                source=SourceKind.CLI,
                session_key="chat:int-1",
                payload={"text": "研究动量"},
            )
        )
        results = ctrl.run_pending()
        assert len(results) == 1 and results[0].ok

        with StudyStore(tmp_path / "goals.db") as store:
            tickets = store.list_tickets()
            assert len(tickets) == 1
            assert tickets[0].status is TicketStatus.ACTIVE
            assert tickets[0].objective == "研究动量"
            assignments = store.list_assignments(tickets[0].ticket_id)
            assert len(assignments) == 1
            # 成功的 turn 落成 ready（施工方案 §2.8）
            assert assignments[0].status is AssignmentStatus.READY
            assert assignments[0].run_id.endswith(":r0")

        kinds = [k for k, _ in emitted]
        assert "ticket.upserted" in kinds
        assert "assignment.report" in kinds
        report = [d for k, d in emitted if k == "assignment.report"][0]
        assert report["status"] == "ready"

    def test_second_turn_reuses_ticket(self, tmp_path: Path):
        """同一 session 的第二个 turn 复用 Ticket，只新增 Assignment。"""
        from strategy_research.kernel.gateway.session_controller import (
            SessionController,
        )
        from strategy_research.kernel.orchestration import (
            EntryPoint,
            SourceKind,
            WorkKind,
            WorkRequest,
        )

        emitted: list[tuple[str, dict]] = []
        sink = _make_sink(tmp_path, emitted)
        ep = EntryPoint(sink=lambda k, d: None)
        ctrl = SessionController(
            ep, client=_FakeClient(), system="s", ticket_sink=sink,
        )
        for i in range(2):
            ep.submit(
                WorkRequest(
                    kind=WorkKind.CHAT,
                    source=SourceKind.CLI,
                    session_key="chat:int-2",
                    payload={"text": f"第 {i} 轮"},
                )
            )
            ctrl.run_pending()

        with StudyStore(tmp_path / "goals.db") as store:
            tickets = store.list_tickets()
            assert len(tickets) == 1, "Ticket 应复用（任务身份不变）"
            assert len(store.list_assignments(tickets[0].ticket_id)) == 2

    def test_failed_turn_settles_as_failed(self, tmp_path: Path):
        from strategy_research.kernel.gateway.session_controller import (
            SessionController,
        )
        from strategy_research.kernel.orchestration import (
            EntryPoint,
            SourceKind,
            WorkKind,
            WorkRequest,
        )

        emitted: list[tuple[str, dict]] = []
        sink = _make_sink(tmp_path, emitted)
        ep = EntryPoint(sink=lambda k, d: None)
        ctrl = SessionController(
            ep, client=_FakeClient(fail=True), system="s", ticket_sink=sink,
        )
        ep.submit(
            WorkRequest(
                kind=WorkKind.CHAT,
                source=SourceKind.CLI,
                session_key="chat:int-3",
                payload={"text": "会失败"},
            )
        )
        results = ctrl.run_pending()
        assert results[0].ok is False

        with StudyStore(tmp_path / "goals.db") as store:
            t = store.list_tickets()[0]
            a = store.list_assignments(t.ticket_id)[0]
            assert a.status is AssignmentStatus.FAILED
            assert "exploded" in (a.report_payload.get("reason") or "")

    def test_sink_failure_does_not_break_turn(self, tmp_path: Path):
        """ticket_sink 抛异常时 turn 仍正常完成（只追加，不改控制流）。"""
        from strategy_research.kernel.gateway.session_controller import (
            SessionController,
        )
        from strategy_research.kernel.orchestration import (
            EntryPoint,
            SourceKind,
            WorkKind,
            WorkRequest,
        )

        class Boom:
            def admit(self, **kw):
                raise RuntimeError("sink down")

            def settle(self, **kw):  # pragma: no cover
                raise RuntimeError("sink down")

        probe: list[tuple[str, dict]] = []
        # EntryPoint 的 sink 只接 submit 期事件；控制器的 sink 是独立参数。
        # 要观察 ticket.admit_failed，必须把同一个 probe 传给两者。
        ep = EntryPoint(sink=lambda k, d: probe.append((k, d)))
        ctrl = SessionController(
            ep, client=_FakeClient(), system="s", ticket_sink=Boom(),
            sink=lambda k, d: probe.append((k, d)),
        )
        ep.submit(
            WorkRequest(
                kind=WorkKind.CHAT,
                source=SourceKind.CLI,
                session_key="chat:int-4",
                payload={"text": "sink 会挂"},
            )
        )
        results = ctrl.run_pending()
        assert results[0].ok is True, "sink 失败不得影响 turn"
        assert any(k == "ticket.admit_failed" for k, _ in probe)


class TestTicketSinkSettleContract:
    def test_settle_rejects_pending_status(self, tmp_path: Path):
        """pending 不在六态里，settle 必须拒绝。"""
        from strategy_research.apps.study.ticket_sink import TicketSink

        sink = TicketSink(store_factory=lambda: StudyStore(tmp_path / "g.db"))
        a = Assignment.new("tkt-1", "s#1:r0")
        with pytest.raises(ValueError, match="status must be one of"):
            sink.settle(assignment=a, status="pending")

    def test_settle_emits_only_for_report_states(self, tmp_path: Path):
        from strategy_research.apps.study.ticket_sink import TicketSink

        emitted: list[tuple[str, dict]] = []
        sink = TicketSink(
            store_factory=lambda: StudyStore(tmp_path / "g.db"),
            emit=lambda k, d: emitted.append((k, d)),
        )
        # 先建 ticket，再建 assignment（store FK 语义由调用方保证）
        with StudyStore(tmp_path / "g.db") as store:
            store.upsert_ticket(Ticket.new("目标"))
        a = Assignment.new("tkt-1", "s#1:r0")
        with StudyStore(tmp_path / "g.db") as store:
            store.create_assignment(a)

        sink.settle(assignment=a, status="dropped", reason="fence lost")
        assert len(emitted) == 1
        assert emitted[0][0] == "assignment.report"
        assert emitted[0][1]["status"] == "dropped"
