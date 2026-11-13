"""B2-① T6 — pre_run 接线网。

契约（docs/design/step-protocol-migration-20261112.md §3.6）：

1. **spy 网（行为级双跑网）**：custom ``pre_run`` → ``arun`` 一次 →
   step 恰调 **1** 次，且 ``loop_start`` 事件恰 **1** 次（双跑的可观测
   信号就是它 ×2——``_prepare_run`` 在 loop_progress.py:74 发它）。
2. **注入网（源码级）**：``loop_engine_adapters.py`` 源码不含
   ``_prepare_run``——有人把直调加回去，此网必红（仓内先例：
   ``test_regression_2026_08_05.py:84`` 同型源码断言）。
3. **handle 单测**：ctx 字段写入（task/messages/result/t0/metadata）
   + output 四元组。
4. **失败契约**：``_prepare_run`` 异常 → 向上抛（**不经** ``_call_step``
   的 fail-result 转换）——prepare 在引擎 ``try:`` 之外。
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agentnodes.foundation.llm import LLMConfig, LLMResponse
from agentnodes.kernel.agent.builtin_tools import build_default_registry
from agentnodes.kernel.agent.loop import AgentLoop
from agentnodes.kernel.agent.loop_engine_adapters import LoopPrepare
from agentnodes.kernel.agent.strategy import LoopContext
from agentnodes.kernel.agent.strategy.steps import DefaultPreRunStep


class _Mock:
    def __init__(self, text="x"):
        self.text = text

    def chat(self, messages, **kw):  # noqa: ANN001, ANN202
        return LLMResponse(content=self.text, tool_calls=[], finish_reason="stop")

    async def achat(self, messages, **kw):  # noqa: ANN001, ANN202
        return self.chat(messages, **kw)

    async def astream(self, messages, tools=None, **kw):  # noqa: ANN001, ANN202
        from agentnodes.foundation.llm.parser import StreamChunk

        yield StreamChunk(delta_content=self.text, finish_reason="stop")

    def stream(self, messages, tools=None, **kw):  # noqa: ANN001, ANN202
        from agentnodes.foundation.llm.parser import StreamChunk

        def _gen():
            yield StreamChunk(delta_content=self.text, finish_reason="stop")

        return _gen()


def _make_loop(tmp_path: Path, strategy=None, text="x") -> AgentLoop:
    kw = {}
    if strategy is not None:
        kw["strategy"] = strategy
    loop = AgentLoop(
        stream_mode=False,
        config=LLMConfig(api_key="sk-test"),
        registry=build_default_registry(),
        workspace=tmp_path,
        trace_dir=tmp_path / "traces",
        **{"max_iterations": 3, **kw},
    )
    m = _Mock(text)
    loop.client.chat = m.chat
    loop.client.achat = m.achat
    loop.client.stream = m.stream
    loop.client.astream = m.astream
    return loop


class _SpyPreRun:
    """计数型 pre_run（装进 custom strategy 的 pre_run 槽位）。"""

    name = "pre_run_spy"

    def __init__(self) -> None:
        self.count = 0
        self._loop = None

    def bind_agent_loop(self, agent_loop) -> None:  # noqa: ANN001
        self._loop = agent_loop

    def handle(self, input, ctx):  # noqa: ANN001, ANN202
        from agentnodes.kernel.agent._core_types import StepResult

        self.count += 1
        full_task, result, messages, t0 = self._loop._prepare_run(
            ctx.task, ctx.context, ctx.history,
        )
        ctx.task = full_task
        ctx.messages = list(messages)
        ctx.result = result
        ctx.iteration = 0
        ctx.t0 = t0
        ctx.metadata["full_task"] = full_task
        return StepResult.ok({
            "full_task": full_task, "result": result,
            "messages": list(messages), "t0": t0,
        })


# ── 1. spy 网：custom pre_run 恰跑 1 次 + loop_start 恰 1 次 ────────


def test_custom_pre_run_runs_exactly_once(tmp_path: Path):
    spy = _SpyPreRun()
    loop = _make_loop(tmp_path, strategy={"name": "react", "config": {"max_iterations": 2}})
    loop._strategy.pre_run = spy
    spy.bind_agent_loop(loop)

    events: list[tuple[str, dict | None]] = []
    loop._on_event = lambda kind, payload=None: events.append((kind, payload))

    asyncio.run(loop.arun("测试"))

    assert spy.count == 1, f"pre_run 被调用 {spy.count} 次（应为 1——双跑信号）"
    starts = [e for e in events if e[0] == "loop_start"]
    assert len(starts) == 1, f"loop_start 事件 {len(starts)} 次（双跑会 ×2）"


# ── 2. 注入网：adapters 源码不得直调 _prepare_run ──────────────────


def test_adapters_no_direct_prepare_run():
    """R-019 注入网：有人把 ``_prepare_run`` 直调加回 adapters，此网必红。

    **用 AST 查调用节点**——字符串匹配会被 docstring 里的 ``_prepare_run``
    提及误伤（本文件的注释就写着它）。AST 只认 ``Call(func=Attribute(
    attr="_prepare_run"))`` 这一个形状：既能抓住真调用，又不被文档误报。
    """
    tree = ast.parse(inspect.getsource(LoopPrepare))
    calls = [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
    ]
    assert "_prepare_run" not in calls, (
        f"LoopPrepare 不得直调 _prepare_run——唯一调用者必须是 "
        f"strategy.pre_run；实测调用: {calls}"
    )


# ── 3. handle 单测：ctx 写入 + output 四元组 ─────────────────────


def test_pre_run_handle_writes_ctx_and_output(tmp_path: Path):
    loop = _make_loop(tmp_path)
    step = DefaultPreRunStep()
    step.bind_agent_loop(loop)
    ctx = LoopContext(task="原始任务", context=None, history=None)
    r = step.handle(None, ctx)
    assert r.success is True
    out = r.output
    assert set(out) == {"full_task", "result", "messages", "t0"}
    assert out["full_task"] == ctx.task, "ctx.task 被换成 full_task"
    assert out["result"] is ctx.result
    assert out["messages"] == ctx.messages
    assert out["t0"] == ctx.t0
    assert ctx.metadata["full_task"] == out["full_task"]
    assert ctx.iteration == 0


def test_pre_run_handle_raises_on_prepare_failure(tmp_path: Path):
    """失败契约：异常向上抛（不经 _call_step 的 fail-result 转换）。"""
    loop = _make_loop(tmp_path)
    step = DefaultPreRunStep()
    step.bind_agent_loop(loop)
    loop._prepare_run = MagicMock(side_effect=RuntimeError("prepare boom"))
    ctx = LoopContext(task="t")
    with pytest.raises(RuntimeError, match="prepare boom"):
        step.handle(None, ctx)


def test_pre_run_noop_when_no_loop():
    step = DefaultPreRunStep()
    ctx = LoopContext(task="t")
    r = step.handle(None, ctx)
    assert r.success is True
    assert r.output["full_task"] == "t"
    assert r.output["result"] is None
