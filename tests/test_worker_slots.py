"""阶段一验收 ⑧：Gateway 订阅 assignment.report 管理 Worker Slot（施工方案 §2.8）。

验证动作表：
    running            → 保留（首次分配）
    waiting/ready/
    blocked/failed/
    dropped            → 释放
且 Gateway 只订阅事件、不读 Ticket 表（kernel 不得 import apps）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentnodes.kernel.gateway.worker_slots import (
    HOLD_STATUSES,
    RELEASE_STATUSES,
    WorkerSlotManager,
)


def _report(aid: str = "asg-1", status: str = "running", **extra) -> dict:
    d = {
        "type": "assignment.report",
        "assignment_id": aid,
        "run_id": "sess#1:r0",
        "ticket_id": "tkt-1",
        "status": status,
        "payload": {},
    }
    d.update(extra)
    return d


# ── 动作表 ───────────────────────────────────────────────────────────


class TestActionTable:
    def test_running_allocates_slot(self):
        m = WorkerSlotManager(max_slots=4)
        m.handle_report(_report("a1", "running"))
        assert m.slot_of("a1") is not None
        assert m.usage()["used_slots"] == 1

    def test_running_twice_does_not_double_allocate(self):
        m = WorkerSlotManager(max_slots=4)
        m.handle_report(_report("a1", "running"))
        m.handle_report(_report("a1", "running"))
        assert m.usage()["used_slots"] == 1

    @pytest.mark.parametrize("status", sorted(RELEASE_STATUSES))
    def test_release_statuses_free_the_slot(self, status):
        m = WorkerSlotManager(max_slots=4)
        m.handle_report(_report("a1", "running"))
        assert m.usage()["used_slots"] == 1
        m.handle_report(_report("a1", status))
        assert m.usage()["used_slots"] == 0, f"{status} 应释放 slot"
        assert m.slot_of("a1") is None

    def test_hold_statuses_constant(self):
        assert HOLD_STATUSES == {"running"}

    def test_release_statuses_constant(self):
        assert RELEASE_STATUSES == {
            "waiting", "ready", "blocked", "failed", "dropped"
        }

    def test_pending_and_ended_ignored(self):
        m = WorkerSlotManager(max_slots=4)
        m.handle_report(_report("a1", "pending"))
        m.handle_report(_report("a1", "ended"))
        assert m.usage()["used_slots"] == 0
        assert m.usage()["ignored"] == 2

    def test_missing_assignment_id_ignored(self):
        m = WorkerSlotManager(max_slots=4)
        m.handle_report({"status": "running"})
        assert m.usage()["ignored"] == 1


# ── slot 复用 ────────────────────────────────────────────────────────


class TestSlotReuse:
    def test_released_slot_can_be_reassigned(self):
        m = WorkerSlotManager(max_slots=2)
        m.handle_report(_report("a1", "running"))
        first = m.slot_of("a1")
        m.handle_report(_report("a1", "dropped"))
        m.handle_report(_report("a2", "running"))
        assert m.slot_of("a2") == first, "释放的 slot 应可复用"

    def test_over_limit_tracked_without_slot_number(self):
        m = WorkerSlotManager(max_slots=1)
        m.handle_report(_report("a1", "running"))
        m.handle_report(_report("a2", "running"))
        assert m.usage()["used_slots"] == 1
        assert m.usage()["active"] == 2, "超限仍跟踪，只是没有 slot 号"
        assert m.slot_of("a2") == -1

    def test_unlimited_when_max_none(self):
        m = WorkerSlotManager(max_slots=None)
        for i in range(10):
            m.handle_report(_report(f"a{i}", "running"))
        assert m.usage()["used_slots"] == 10


# ── on_event 契约 ────────────────────────────────────────────────────


class TestSinkContract:
    def test_ignores_non_assignment_events(self):
        m = WorkerSlotManager(max_slots=2)
        m.on_event("run.settled", {"run_id": "x"})
        assert m.usage()["events_seen"] == 0

    def test_handles_assignment_report(self):
        m = WorkerSlotManager(max_slots=2)
        m.on_event("assignment.report", _report("a1", "running"))
        assert m.usage()["events_seen"] == 1
        assert m.usage()["used_slots"] == 1


# ── Gateway 集成 ─────────────────────────────────────────────────────


class TestGatewayIntegration:
    def _gateway(self, **kw):
        from agentnodes.kernel.gateway.gateway import Gateway

        class _Client:
            def chat(self, messages, **k):
                raise AssertionError("llm should not be called")

        return Gateway(client=_Client(), **kw)

    def test_gateway_exposes_worker_slots(self):
        gw = self._gateway(max_worker_slots=4)
        try:
            assert gw.worker_slots is not None
            assert gw.worker_slots.usage()["max_slots"] == 4
        finally:
            gw.close()

    def test_gateway_subscribes_to_assignment_report(self):
        """Gateway 的事件链把 assignment.report 送进 slot 管理器。"""
        gw = self._gateway(max_worker_slots=2)
        try:
            gw._combined_sink("assignment.report", _report("a1", "running"))
            assert gw.worker_slots.usage()["used_slots"] == 1
            gw._combined_sink("assignment.report", _report("a1", "ready"))
            assert gw.worker_slots.usage()["used_slots"] == 0
        finally:
            gw.close()

    def test_gateway_ignores_other_events(self):
        gw = self._gateway(max_worker_slots=2)
        try:
            gw._combined_sink("run.settled", {"run_id": "x"})
            assert gw.worker_slots.usage()["events_seen"] == 0
        finally:
            gw.close()

    def test_slot_failure_does_not_swallow_event(self):
        """slot 记账抛错时事件仍要送到外部 sink。"""
        gw = self._gateway(max_worker_slots=2)
        seen = []
        gw.add_sink(lambda k, d: seen.append((k, d)))
        try:
            def boom(_data):
                raise RuntimeError("slot bookkeeping down")

            gw.worker_slots.handle_report = boom  # type: ignore[assignment]
            gw._combined_sink("assignment.report", _report("a1", "running"))
            assert any(k == "assignment.report" for k, _ in seen), (
                "slot 失败不得吞掉事件"
            )
        finally:
            gw.close()

    def test_gateway_does_not_import_ticket_store(self):
        """§2.8：Gateway 只订阅事件，不读 Ticket 表（H1）。"""
        import agentnodes.kernel.gateway.gateway as m

        src = Path(m.__file__).read_text(encoding="utf-8")
        bad = [
            ln for ln in src.splitlines()
            if ln.strip().startswith(("from agentnodes.apps", "import agentnodes.apps"))
        ]
        assert bad == [], f"gateway.py 出现了 apps import: {bad}"
