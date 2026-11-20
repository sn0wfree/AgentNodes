# -*- coding: utf-8 -*-
"""G2：完成深操作 complete_goal 的派发契约单测（此前零直接覆盖）。

钉两件事（异步派发的两条分支）：

- **无 running loop** → ``asyncio.run`` 同步执行，返回策略的 bool，store 已写入；
- **running loop** → ``create_task`` fire-and-forget，立即返回 None，不阻塞；
- 注入 strategy 优先于 Factory 选择（hook 的自定义策略路径）；
- 未知 mode 回落 auto（Factory 既有行为）。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agentnodes.apps.goal.completion_strategy import (
    CompletionContext,
    CompletionStrategyFactory,
    complete_goal,
)


def _context(**overrides):
    base = dict(
        store=SimpleNamespace(update_status=lambda **kw: None),
        session_id="s1",
        goal_id="g1",
        snapshot={},
        mode="auto",
        workflow_name="wf",
    )
    base.update(overrides)
    return CompletionContext(**base)


class TestCompleteGoalSyncBranch:
    """无 running loop → asyncio.run 同步执行。"""

    def test_returns_bool_and_runs_strategy(self):
        calls = []
        ctx = _context(
            store=SimpleNamespace(
                complete_lite=lambda **kw: calls.append(kw),
            ),
            mode="lite",
            recap="r",
        )
        result = complete_goal(ctx)
        assert result is True
        assert len(calls) == 1
        assert calls[0]["recap"] == "r"

    def test_injected_strategy_takes_precedence(self):
        """注入 strategy 优先——hook 用此路径保住自定义策略。"""
        calls = []

        class Injected:
            async def complete(self, context):
                calls.append(context.session_id)
                return False

        result = complete_goal(_context(strategy=Injected()))
        assert result is False
        assert calls == ["s1"]

    def test_unknown_mode_falls_back_to_auto(self):
        """未知 mode 回落 auto（Factory 既有行为，不抛）。"""
        assert CompletionStrategyFactory.get("no-such-mode") is CompletionStrategyFactory.get("auto")
        # 走完整深操作（auto 策略会调 update_status，不抛即通过）
        assert complete_goal(_context(mode="no-such-mode")) is True


class TestCompleteGoalAsyncBranch:
    """running loop → create_task，立即返回 None（fire-and-forget）。"""

    @pytest.mark.asyncio
    async def test_running_loop_returns_none_and_schedules(self):
        started = asyncio.Event()

        class Tracking:
            async def complete(self, context):
                started.set()
                return True

        result = complete_goal(_context(strategy=Tracking()))
        assert result is None  # 未等待
        # 给事件循环一次机会跑已调度的任务
        await asyncio.wait_for(started.wait(), timeout=1.0)
