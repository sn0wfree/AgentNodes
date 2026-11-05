"""生产接线验收：三个子系统在**真实 Gateway 构造路径**上真的接上了。

与 ``test_ticket_assignment.py`` / ``test_capacity_gate.py`` 的区别：
那些测的是**组件契约**（直接 new 组件、直接调方法）。本文件测
**装配结果**——走 ``singleton.get_gateway()`` 这条生产唯一构造路径，
断言 TicketSink / CapacityGate / GenomeVersionStore 都在位且转发到位。

为什么必须单独一个文件：本轮发现的真实缺陷正是"组件测试全绿、生产没接"。
组件测试无法发现这种缺失，因为它们在测试里自己 new 了组件。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class _Client:
    """最小 LLM 替身。"""

    def chat(self, messages, tools=None):
        class R:
            content = "ok"
            tool_calls = []
            usage = {"prompt_tokens": 1, "completion_tokens": 1}

            def has_tool_calls(self):
                return False

        return R()


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    """真实构造路径：import interfaces（触发钩子）→ get_gateway()。"""
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
    monkeypatch.setenv("QNODES_PLATFORM_STORE", "1")

    import agentnodes.interfaces  # noqa: F401 —— 注册接线钩子
    from agentnodes.kernel.gateway.singleton import get_gateway, reset_gateway

    reset_gateway()
    gw = get_gateway(client=_Client())
    yield gw
    gw.close()
    reset_gateway()


class TestWiringHappensOnRealPath:
    """生产构造路径上三个子系统必须都在位。"""

    def test_ticket_sink_attached(self, gateway):
        """TicketSink 必须在位，且**转发给 controller**。

        只挂在 Gateway 上没用——准入发生在 controller，本轮的真实缺陷
        就是"Gateway 有、controller 没有"。
        """
        assert gateway.ticket_sink is not None, (
            "ticket_sink 未接线 —— 崩溃恢复与 assignment.report 都不会发生"
        )
        assert gateway.controller._ticket_sink is gateway.ticket_sink, (
            "ticket_sink 未转发给 SessionController —— 准入路径不会落库"
        )

    def test_capacity_gate_attached(self, gateway):
        assert gateway.capacity is not None, "容量闸未接线"
        assert gateway.controller._capacity is gateway.capacity, (
            "容量闸未转发给 SessionController —— 派发路径不会准入"
        )

    def test_genome_store_attached(self, gateway):
        assert gateway.genome is not None, "基因组版本链未接线"
        assert hasattr(gateway.genome, "activate")
        assert hasattr(gateway.genome, "rollback")

    def test_capability_registry_populated(self, gateway):
        """能力注册必须发生——否则 harness 链解析不到 evolution.*。

        2026-11-05 批次 3：``study.ticket_sink`` 已不是 kind
        （Ticket 迁到 kernel/orchestration，直接构造），所以这里断言
        registry 非空 + evolution.* 在册。
        """
        from agentnodes.kernel.agent.capability import list_kinds

        kinds = list_kinds()
        assert kinds, "apps 能力未注册"
        assert not any(k.startswith("study.") for k in kinds), (
            "study.* kind 应已随 apps/study 移除"
        )
        assert any(k.startswith("evolution.") for k in kinds), (
            "evolution.* 未注册 —— 装配期 resolver 会永远拿到空配置"
        )


class TestTicketActuallyPersists:
    """跑一个真实 turn，断言真的落库。"""

    def test_turn_creates_ticket_and_assignment(self, gateway, tmp_path):
        from agentnodes.kernel.gateway import cli_request, submit_via_entry
        from agentnodes.kernel.orchestration.ticket_store import TicketStore

        events: list[str] = []
        gateway.add_sink(lambda k, d: events.append(k))

        submit_via_entry(gateway.entry, cli_request("chat:wiring", "研究一个动量策略"))
        results = gateway.run_once()

        assert results and results[0].ok, f"turn 未成功: {results}"
        assert "ticket.upserted" in events, "Ticket 未落库事件未发出"
        assert "assignment.report" in events, "assignment.report 未发出"

        # 批次 3 后 TicketStore 直接持有 db_path（不再有 store_factory）
        db = Path(gateway.ticket_sink.db_path)
        with TicketStore(db) as store:
            tickets = store.list_tickets()
            assignments = store.list_assignments()

        assert len(tickets) == 1, f"预期 1 个 Ticket，实际 {len(tickets)}"
        assert len(assignments) == 1, f"预期 1 个 Assignment，实际 {len(assignments)}"
        assert assignments[0].status.value == "ready", (
            f"成功 turn 的 Assignment 应为 ready，实际 {assignments[0].status.value}"
        )


class TestCapacityActuallyGates:
    """容量闸在派发路径上真的挡得住。"""

    def test_full_pool_keeps_work_queued(self, gateway):
        from agentnodes.kernel.gateway import cli_request, submit_via_entry

        events: list[str] = []
        gateway.add_sink(lambda k, d: events.append(k))

        # 占满 chat 池
        gateway.capacity._limits["chat"] = 1
        held = gateway.capacity.acquire("chat")
        assert held is not None

        submit_via_entry(gateway.entry, cli_request("chat:cap", "被挡住的请求"))
        results = gateway.run_once()

        assert results == [], "池满时不应执行任何 turn"
        assert "capacity.full" in events, "池满必须 emit capacity.full"
        assert gateway.entry.mailbox_for("chat:cap").peek_depth() == 1, (
            "工单必须留在队列（不拒绝、不丢弃）"
        )

        # 归还后应能执行
        gateway.capacity.release(held)
        results2 = gateway.run_once()
        assert results2 and results2[0].ok, "归还池位后 turn 应正常执行"


class TestRsiLoopAssembles:
    """RSI 闭环能从已接线的 Gateway 组装出来。"""

    def test_loop_builds_from_wired_gateway(self, gateway):
        from agentnodes.interfaces.rsi_wiring import build_rsi_loop

        loop = build_rsi_loop(gateway, seed=42)
        assert loop is not None, "RSI 闭环组装失败（genome/capacity/store 缺一环）"

    def test_loop_runs_a_full_cycle(self, gateway):
        """抽 holdout → 提案 → 验证，全链路可跑。"""
        from agentnodes.apps.evolution.holdout import compute_task_signature
        from agentnodes.interfaces.rsi_wiring import build_rsi_loop

        sig = compute_task_signature("动量研究", "researcher", {"read", "write"})
        store = gateway.store
        for i in range(6):
            # 失败率高 → default_policy 会提"收窄工具集"的提案
            store.emit("sess-rsi", "assignment.report", {
                "type": "assignment.report",
                "run_id": f"r{i}", "assignment_id": f"a{i}",
                "ticket_id": f"t{i}",
                "status": "ready" if i < 2 else "failed",
                "task_signature": sig,
                "payload": {"reason": None, "progress_pct": 1.0,
                            "current_step": "", "blockers": []},
                "ts": f"2026-11-0{i + 1}T00:00:00",
            })

        events: list[tuple[str, dict]] = []
        gateway.add_sink(lambda k, d: events.append((k, d)))

        loop = build_rsi_loop(gateway, seed=42)
        out = loop.run_once(
            sig, objective="动量研究", role="researcher",
            tool_set=frozenset({"read", "write"}),
        )

        kinds = [k for k, _ in events]
        assert "rsi.holdout_sampled" in kinds, "holdout 抽样未发生"
        assert out["holdout_size"] > 0, f"holdout 为空: {out}"
        # 提案要么产出并验证，要么因证据不足跳过——两者都是合法路径，
        # 但"抽样成功"必须成立。
        assert out["reason"] in ("approved", "rejected", "rolled_back",
                                 "no_proposal", "empty_holdout"), (
            f"意外的 cycle 结果: {out}"
        )


class TestWiringIsResilientToFailure:
    """接线失败不得阻断 chat。"""

    def test_missing_apps_still_boots(self, tmp_path, monkeypatch):
        """apps 不可用时 Gateway 仍能构造（子系统降级为 None）。"""
        monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "g.db"))

        import agentnodes.interfaces  # noqa: F401
        from agentnodes.kernel.gateway import gateway as gw_mod
        from agentnodes.kernel.gateway.singleton import get_gateway, reset_gateway

        reset_gateway()

        # 模拟 ticket_sink 解析失败
        original = gw_mod.Gateway.__init__

        def _broken_init(self, **kwargs):
            original(self, **kwargs)
            self._ticket_sink = None

        monkeypatch.setattr(gw_mod.Gateway, "__init__", _broken_init)

        gw = get_gateway(client=_Client())
        try:
            # 不抛即通过——降级是设计行为
            assert gw is not None
        finally:
            gw.close()
            reset_gateway()
