# -*- coding: utf-8 -*-
"""B3 并发回归：GoalWorkflow.start 切 execution lease（裁决 2）。

覆盖设计文档 §3-B3 的并发回归清单：
- start 生命周期持有（执行中 holding True、结束 False）
- 双 runner 同 session 串行（不交错）
- 异常释放（_start_inner 抛错 → finally 释放，不悬挂死锁）
- goal 占用 → 第二个 runner 自旋等待（保持现状语义，不改拒绝）
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from agentnodes.apps.goal.workflow import GoalWorkflowConfig, GoalWorkflowRunner
from agentnodes.kernel.gateway.session_lease import SessionExecutionLease


def _config() -> GoalWorkflowConfig:
    from agentnodes.apps.goal.workflow import (
        GoalAgentConfig,
        GoalWorkflowGoalConfig,
    )

    return GoalWorkflowConfig(
        name="wf",
        description="",
        agents=[GoalAgentConfig(id="a", prompt_file=".prompts/a.md")],
        dag={"a": []},
        goal=GoalWorkflowGoalConfig(default_criteria=["c1"]),
    )


def _runner(lease: SessionExecutionLease, session_id: str, store=None):
    return GoalWorkflowRunner(
        config=_config(),
        session_id=session_id,
        store=store,
        execution_lease=lease,
    )


@dataclass
class _Gate:
    """控制 _start_inner 的挂起/放行（并发测试的节拍器）。"""

    release_event: asyncio.Event = field(default_factory=asyncio.Event)
    entered: list[str] = field(default_factory=list)


# ── 1. start 生命周期持有 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_runner_holds_lease_during_start():
    lease = SessionExecutionLease()
    runner = _runner(lease, "s1")
    holding_during = []

    async def stub_start_inner(objective):
        holding_during.append(lease.holding("s1"))
        return "g1"

    runner._start_inner = stub_start_inner  # type: ignore[method-assign]

    goal_id = await runner.start("objective")
    assert goal_id == "g1"
    assert holding_during == [True]  # 执行中持有
    assert lease.holding("s1") is False  # 结束后释放


@pytest.mark.asyncio
async def test_no_lease_injected_means_no_mutex():
    """execution_lease=None（直构测试路径）→ 无互斥，行为不变。"""
    runner = _runner(None, "s1")

    async def stub_start_inner(objective):
        return "g1"

    runner._start_inner = stub_start_inner  # type: ignore[method-assign]
    assert await runner.start("objective") == "g1"


# ── 2. 双 runner 同 session 串行（不交错）──────────────────────────


@pytest.mark.asyncio
async def test_two_runners_serialize_on_same_session():
    """r1 持有并挂起 → r2 等待（不执行）→ r1 放行 → r2 才执行。"""
    lease = SessionExecutionLease()
    gate = _Gate()
    exec_log: list[str] = []

    r1 = _runner(lease, "s1")
    r2 = _runner(lease, "s1")

    async def start_inner_1(objective):
        gate.entered.append("r1")
        await gate.release_event.wait()  # 挂起（模拟长跑）
        exec_log.append("r1")
        return "g1"

    async def start_inner_2(objective):
        gate.entered.append("r2")
        exec_log.append("r2")
        return "g2"

    r1._start_inner = start_inner_1  # type: ignore[method-assign]
    r2._start_inner = start_inner_2  # type: ignore[method-assign]

    t1 = asyncio.create_task(r1.start("objective"))
    await asyncio.sleep(0.05)  # r1 获得租约并挂起
    assert gate.entered == ["r1"]

    t2 = asyncio.create_task(r2.start("objective"))
    await asyncio.sleep(0.05)
    # r2 在等待租约（未进入 _start_inner、未完成）
    assert gate.entered == ["r1"] and not t2.done()

    gate.release_event.set()  # r1 放行
    await asyncio.gather(t1, t2)
    # 串行：r1 完成释放后 r2 才进入，不交错
    assert exec_log == ["r1", "r2"]


# ── 3. 异常释放 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_inner_exception_releases_lease():
    class _Boom(Exception):
        pass

    lease = SessionExecutionLease()
    runner = _runner(lease, "s1")

    async def boom(objective):
        raise _Boom("workflow failed")

    runner._start_inner = boom  # type: ignore[method-assign]
    await runner.start("objective")  # start 内部捕获，不外抛

    assert lease.holding("s1") is False, "异常路径必须释放租约（finally）"


# ── 4. goal 占用 → 第二个 runner 自旋等待（保持现状语义）───────────


@pytest.mark.asyncio
async def test_runner_spins_while_goal_holds_then_acquires():
    """租约先被外部占用（模拟上一轮 goal）→ runner 自旋等待 → 释放后获得。"""
    lease = SessionExecutionLease()
    token = lease.acquire("s1", "goal:previous")
    runner = _runner(lease, "s1")

    async def stub_start_inner(objective):
        return "g1"

    runner._start_inner = stub_start_inner  # type: ignore[method-assign]

    task = asyncio.create_task(runner.start("objective"))
    await asyncio.sleep(0.05)
    assert not task.done()  # 自旋等待中
    lease.release("s1", token)

    goal_id = await asyncio.wait_for(task, timeout=2.0)
    assert goal_id == "g1"
    assert lease.holding("s1") is False
