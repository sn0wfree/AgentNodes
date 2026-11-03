"""scheduler 单通道测试（阶段 3a 引入，阶段 4 旧通道删除后重写）。

阶段 4 起：唯一准入通道 = Gateway mailbox（旧 asyncio.Queue 已删）。
验证点：
- submit 进 Gateway mailbox，且被 scheduler 的 mailbox 消费者取走
- 防重语义保留：重复 submit 仍被拒（active / queued / terminal 检查在上方）
- 旧通道彻底不存在（属性级断言）
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

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


@pytest.fixture
def store(tmp_path: Path) -> StudyStore:
    return StudyStore(db_path=tmp_path / "study.db")


def _record(study_id: str, session_id: str = "sess-1") -> StudyRecord:
    return StudyRecord(
        study_id=study_id,
        session_id=session_id,
        goal_id="g1",
        objective="o",
        executor_type="autoresearch",
        workspace_path="/tmp/ws",
        strategy_name="s1",
        execution_status=StudyStatus.QUEUED,
    )


def test_submit_uses_mailbox_only(store):
    sched = StudyScheduler(store=store)

    async def run():
        ok = await sched.submit(_record("s1"))
        await asyncio.sleep(0.05)  # 让 mailbox 消费者取走工单
        await sched.shutdown()
        return ok

    ok = asyncio.run(run())
    assert ok is True
    # 阶段 4：旧通道属性不存在
    assert not hasattr(sched, "_session_queues")
    assert not hasattr(sched, "_session_consumers")

    from strategy_research.core.study.scheduler import _get_gateway_singleton

    gw = _get_gateway_singleton()
    mailbox = gw.entry.mailbox_for("automation:sess-1")
    assert mailbox is not None
    assert mailbox.peek_depth() == 0  # 已被消费


def test_duplicate_submit_rejected(store):
    """防重语义：重复 submit 必须被拒。"""
    sched = StudyScheduler(store=store)
    rec = _record("dup-1")

    async def run():
        first = await sched.submit(rec)
        second = await sched.submit(rec)
        await asyncio.sleep(0.05)
        await sched.shutdown()
        return first, second

    first, second = asyncio.run(run())
    assert first is True
    assert second is False


def test_terminal_study_rejected(store):
    sched = StudyScheduler(store=store)
    created = store.create_study(
        owner_session_id="sess-1",
        goal_id="g1",
        objective="o",
        workspace_path="/tmp/ws",
        strategy_name="s1",
    )
    study_id = created.study_id if hasattr(created, "study_id") else created
    store.update_execution_status(study_id, StudyStatus.COMPLETE)

    async def run():
        return await sched.submit(_record(study_id))

    assert asyncio.run(run()) is False
