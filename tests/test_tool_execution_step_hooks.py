"""B2-① T4 — ToolExecutionStep 迁移到 handle 协议 + hook 自触发测试。

Covers the "Step self-triggers hooks" pattern (v0.3) 保持不变：

- fires before_execute_tools before dispatch；
- on_tool_error for error results / after_tool_executed for success results
  （顺序同 legacy _fire_tool_result_hooks）；
- 收集 hashes + 追加结果进 ctx（metadata 双写 + messages）；
- loop is None → no-op（可独立单测）；
- error / success 工具结果路由到正确的 hook。

**B2-① T4 变化**（docs/design/step-protocol-migration-20261112.md §3.3）：

- ``execute(ctx, *, async_mode)`` → ``handle(input, ctx) → StepResult``
- ``input`` = LLMResponse（工具调用来源）
- 恒走 async 孪生 ``_aexecute_tool_batch``——**原
  ``test_sync_path_uses_execute_tool_batch`` 随同步分支删除**（U5.5 后
  ``async_mode=False`` 生产不可达，§1.3）；R-019 注入网替代：源码不得引用
  同步孪生
- 产物走 ``StepResult.output = {"tool_hashes", "tool_result_msgs"}``；
  metadata 双写保留（legacy 读 metadata）
- **产物有效 ≠ 成功**：``_append_tool_results`` 失败 → fail **带产物**
"""

from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentnodes.kernel.agent.strategy import LoopContext
from agentnodes.kernel.agent.strategy.steps import DefaultToolExecutionStep


class _FakeLoop:
    """AgentLoop stand-in capturing hook calls."""

    def __init__(self, batch_result=None):
        self.hook_calls: list[tuple] = []
        self.batch_result = batch_result
        self._collect_tool_hashes = MagicMock(return_value=["h1", "h2"])
        self._append_tool_results = MagicMock()

    async def _afire_hooks(self, name, hook_ctx, *args, **kwargs):
        self.hook_calls.append((name, hook_ctx, args, kwargs))

    async def _aexecute_tool_batch(self, tool_calls, result):
        return self.batch_result or []


class _ToolCall:
    def __init__(self, name="tool_x"):
        self.name = name


def _success_msg():
    return {"role": "tool", "tool_call_id": "t1", "content": "ok"}


def _error_msg():
    return {
        "role": "tool",
        "tool_call_id": "t2",
        "content": '{"status": "error", "error": "boom"}',
    }


class TestToolExecutionStepHooks:
    def test_fires_before_execute_tools_first(self):
        loop = _FakeLoop()
        step = DefaultToolExecutionStep()
        step.bind_agent_loop(loop)

        resp = MagicMock(tool_calls=[_ToolCall()])
        ctx = LoopContext(task="t", response=resp, hook_ctx=object())
        r = asyncio.run(step.handle(resp, ctx))
        assert r.success is True
        assert loop.hook_calls[0][0] == "before_execute_tools"
        # after tool hooks come later (none here — batch returned []).
        assert all(c[0] != "on_tool_error" for c in loop.hook_calls)
        assert all(c[0] != "after_tool_executed" for c in loop.hook_calls)

    def test_noop_when_no_loop(self):
        step = DefaultToolExecutionStep()
        ctx = LoopContext(task="t", response=MagicMock(tool_calls=[_ToolCall()]))
        r = asyncio.run(step.handle(None, ctx))
        assert r.success is True
        assert r.output == {"tool_hashes": [], "tool_result_msgs": []}
        assert "tool_hashes" not in ctx.metadata

    def test_noop_when_no_tool_calls(self):
        loop = _FakeLoop()
        step = DefaultToolExecutionStep()
        step.bind_agent_loop(loop)
        resp = MagicMock(tool_calls=[])
        ctx = LoopContext(task="t", response=resp)
        r = asyncio.run(step.handle(resp, ctx))
        assert r.success is True
        assert r.output == {"tool_hashes": [], "tool_result_msgs": []}
        assert loop.hook_calls == []  # no before_execute_tools either

    def test_success_result_fires_after_tool_executed(self):
        loop = _FakeLoop(batch_result=[_success_msg()])
        step = DefaultToolExecutionStep()
        step.bind_agent_loop(loop)
        resp = MagicMock(tool_calls=[_ToolCall("a")])
        ctx = LoopContext(task="t", response=resp, hook_ctx=object())
        asyncio.run(step.handle(resp, ctx))
        names = [c[0] for c in loop.hook_calls]
        assert "after_tool_executed" in names
        assert "on_tool_error" not in names
        # after_tool_executed fired after before_execute_tools.
        assert names.index("before_execute_tools") < names.index("after_tool_executed")

    def test_error_result_fires_on_tool_error(self):
        loop = _FakeLoop(batch_result=[_error_msg()])
        step = DefaultToolExecutionStep()
        step.bind_agent_loop(loop)
        resp = MagicMock(tool_calls=[_ToolCall("b")])
        ctx = LoopContext(task="t", response=resp, hook_ctx=object())
        asyncio.run(step.handle(resp, ctx))
        names = [c[0] for c in loop.hook_calls]
        assert "on_tool_error" in names
        assert "after_tool_executed" not in names

    def test_mixed_results_fire_both(self):
        loop = _FakeLoop(batch_result=[_success_msg(), _error_msg()])
        step = DefaultToolExecutionStep()
        step.bind_agent_loop(loop)
        resp = MagicMock(tool_calls=[_ToolCall("a"), _ToolCall("b")])
        ctx = LoopContext(task="t", response=resp, hook_ctx=object())
        asyncio.run(step.handle(resp, ctx))
        names = [c[0] for c in loop.hook_calls]
        assert "before_execute_tools" in names
        assert "after_tool_executed" in names
        assert "on_tool_error" in names

    def test_collects_hashes_and_appends_results(self):
        loop = _FakeLoop(batch_result=[_success_msg()])
        step = DefaultToolExecutionStep()
        step.bind_agent_loop(loop)
        resp = MagicMock(tool_calls=[_ToolCall("x")])
        ctx = LoopContext(task="t", response=resp, hook_ctx=object())
        r = asyncio.run(step.handle(resp, ctx))
        # 产物走 output
        assert r.output["tool_hashes"] == ["h1", "h2"]
        assert len(r.output["tool_result_msgs"]) == 1
        # metadata 双写保留（legacy 读 metadata）
        assert ctx.metadata["tool_hashes"] == ["h1", "h2"]
        assert len(ctx.metadata["tool_result_msgs"]) == 1
        loop._append_tool_results.assert_called_once()

    def test_append_failure_keeps_product(self):
        """产物有效 ≠ 成功（T3 教训同型）：工具已执行完，产物必须保留。"""
        loop = _FakeLoop(batch_result=[_success_msg()])
        step = DefaultToolExecutionStep()
        step.bind_agent_loop(loop)
        resp = MagicMock(tool_calls=[_ToolCall("x")])
        ctx = LoopContext(task="t", response=resp, hook_ctx=object())
        loop._append_tool_results = MagicMock(
            side_effect=RuntimeError("append boom")
        )
        r = asyncio.run(step.handle(resp, ctx))
        assert r.success is False
        assert r.output["tool_hashes"] == ["h1", "h2"], "产物必须保留"
        assert "step_tool_execution" in r.error

    def test_sync_twin_not_referenced(self):
        """R-019 注入网：源码不得引用同步孪生——有人加回分支此网必红。"""
        src = inspect.getsource(DefaultToolExecutionStep)
        assert "_execute_tool_batch" not in src, "同步孪生引用回流（应恒走 async 孪生）"
