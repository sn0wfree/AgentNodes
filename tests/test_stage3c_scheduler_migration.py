"""3c 测试 —— kind 归属消费 / scheduler 提交级接线 / 双门翻默认。

方法论（replan v2 §5）：迁移验收必须包含**执行等价**，而非仅投递成功。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from strategy_research.kernel.gateway import (
    Gateway,
    api_chat_request,
    research_graph_enabled,
    scheduler_request,
)
from strategy_research.kernel.orchestration import EntryPoint, WorkKind
from strategy_research.core.study import StudyScheduler, StudyStatus, StudyStore
from strategy_research.core.study.models import StudyRecord


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))
    monkeypatch.setenv("SR_STUDY_MAX_CONCURRENT", "1")
    from strategy_research.kernel.gateway import reset_gateway

    reset_gateway()
    yield
    reset_gateway()


def _record(study_id: str, session_id: str = "sess-1") -> StudyRecord:
    return StudyRecord(
        study_id=study_id, session_id=session_id, goal_id="g1", objective="o",
        executor_type="autoresearch", workspace_path="/tmp/ws",
        strategy_name="s1", execution_status=StudyStatus.QUEUED,
    )


# ── 门退役契约（阶段 4）───────────────────────────────


def test_study_gate_removed():
    """study 门已删除：旧 asyncio.Queue 通道同时消失。"""
    import strategy_research.kernel.gateway as gw

    assert not hasattr(gw, "study_entry_enabled")


def test_research_graph_gate_retired():
    """阶段 4：门已退役，恒返回 True（保留函数仅向后兼容）。"""
    assert research_graph_enabled() is True


# ── kind 归属：消费隔离（堵串道事故）─────────────────────


def test_chat_consumer_leaves_automation_work():
    ep = EntryPoint(sink=lambda k, d: None)
    ep.submit(api_chat_request("s1", "hi"))
    ep.submit(scheduler_request("s1", {"study_id": "st1"}))

    mailbox = ep.mailbox_for("chat:s1") or ep.mailbox_for("automation:s1")
    chat_mail = ep.mailbox_for("chat:s1")
    auto_mail = ep.mailbox_for("automation:s1")

    assert chat_mail.poll_due({WorkKind.CHAT}) is not None
    assert chat_mail.poll_due({WorkKind.CHAT}) is None
    # AUTOMATION 工单原封未动
    assert auto_mail.peek_depth() == 1
    assert auto_mail.poll_due({WorkKind.CHAT}) is None
    assert auto_mail.peek_depth() == 1  # 过滤不丢单
    assert auto_mail.poll_due({WorkKind.AUTOMATION}) is not None
    assert mailbox is not None


def test_gateway_run_once_kind_filter_only_runs_chat():
    class FakeClient:
        def __init__(self) -> None:
            self.calls: list = []

        def chat(self, messages, **kwargs):
            self.calls.append(messages)

            class R:
                content = "ok"
                usage = {"total_tokens": 1}

            return R()

    client = FakeClient()
    gw = Gateway(client=client)
    gw.entry.submit(api_chat_request("s1", "hi"))
    gw.entry.submit(scheduler_request("s1", {"study_id": "st1"}))

    runs = gw.run_once({WorkKind.CHAT})
    assert len(runs) == 1 and runs[0].ok is True
    assert len(client.calls) == 1  # 研究工单没被当聊天执行

    # AUTOMATION 工单仍在队（由 scheduler 消费者接管）
    auto = gw.entry.mailbox_for("automation:s1")
    assert auto.peek_depth() == 1
    gw.close()


def test_default_run_once_still_handles_all_kinds():
    """默认 None = 全部（保留既有脚本/测试行为）。"""
    ep = EntryPoint(sink=lambda k, d: None)
    ep.submit(api_chat_request("s1", "hi"))

    class FakeClient:
        def chat(self, messages, **kwargs):
            class R:
                content = "ok"
                usage = {"total_tokens": 1}

            return R()

    from strategy_research.kernel.gateway import SessionController

    ctrl = SessionController(ep, client=FakeClient())
    assert len(ctrl.run_pending()) == 1


def test_interrupt_priority_respects_kind_filter():

    from strategy_research.kernel.orchestration import (
        QueueMode,
        SourceKind,
        WorkRequest,
    )

    ep = EntryPoint(sink=lambda k, d: None)
    ep.submit(api_chat_request("s1", "first"))
    ep.submit(WorkRequest(
        kind=WorkKind.AUTOMATION, source=SourceKind.SCHEDULER,
        session_key="automation:s1", payload={"study_id": "st1"},
        queue_mode=QueueMode.INTERRUPT,
    ))
    # AUTOMATION 是 interrupt（队首），但 CHAT 消费者看不到它
    chat = ep.mailbox_for("chat:s1")
    got = chat.poll_due({WorkKind.CHAT})
    assert got is not None and got.kind is WorkKind.CHAT


# ── scheduler 提交级接线 + 执行等价 ──────────────────────


class _FakeRunner:
    """替代 AutoresearchRunner：只记录是否被执行。"""

    instances: list["_FakeRunner"] = []

    def __init__(self, study, store, *, control, emitter) -> None:
        self.study = study
        self.executed = False
        type(self).instances.append(self)

    async def run(self) -> None:
        self.executed = True


def test_submit_routes_to_mailbox_and_consumer_executes(monkeypatch, tmp_path):
    """执行等价：工单不只进 mailbox，还要真的被执行体消费。"""
    monkeypatch.setenv("QNODES_NEW_ENTRY_STUDY", "1")
    import strategy_research.core.study.scheduler as sched_mod

    _FakeRunner.instances.clear()
    monkeypatch.setattr(sched_mod, "AutoresearchRunner", _FakeRunner)
    store = StudyStore(db_path=tmp_path / "study.db")
    # _run_one_study 以 store 为准（终态判定）——必须先建真实行
    created = store.create_study(
        owner_session_id="sess-1", goal_id="g1", objective="o",
        workspace_path=str(tmp_path), strategy_name="s1",
    )
    study_id = created.study_id if hasattr(created, "study_id") else created
    session_id = store.get_study(study_id).session_id
    sched = StudyScheduler(store=store)

    async def run():
        ok = await sched.submit(store.get_study(study_id))
        for _ in range(50):
            await asyncio.sleep(0.01)
            if _FakeRunner.instances:
                break
        await asyncio.sleep(0.05)
        return ok

    assert asyncio.run(run()) is True

    from strategy_research.kernel.gateway import get_gateway

    gw = get_gateway()
    assert gw.entry.mailbox_for(f"automation:{session_id}").peek_depth() == 0  # 被消费
    assert _FakeRunner.instances, "工单未被执行体消费（投递≠执行）"
    assert _FakeRunner.instances[0].executed is True


def test_legacy_queue_channel_is_gone(monkeypatch, tmp_path):
    """阶段 4：旧 asyncio.Queue 通道删除——即使设旧门变量也恒走 mailbox。"""
    monkeypatch.setenv("QNODES_NEW_ENTRY_STUDY", "0")
    store = StudyStore(db_path=tmp_path / "study.db")
    sched = StudyScheduler(store=store)

    async def run():
        ok = await sched.submit(_record("s-legacy"))
        await sched.shutdown()
        return ok

    try:
        assert asyncio.run(run()) is True
        assert not hasattr(sched, "_session_queues")
        assert not hasattr(sched, "_session_loop")
    finally:
        store.close()
