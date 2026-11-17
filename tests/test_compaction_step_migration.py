"""B2-① T2 — CompactionStep 迁移到 handle 协议。

Covers（docs/design/step-protocol-migration-20261112.md §3.1）:

1. handle 恒走 ``_amaybe_compact``（async 孪生）。**原
   ``test_sync_path_calls_maybe_compact`` 已随同步分支删除**——U5.5 后
   ``async_mode=False`` 生产不可达（§1.3），该用例断言的分支不复存在。
2. 压缩发生时 fire ``_emit_compaction``。
3. 恒 fire ``_emit_iter_start``。
4. ``_inject_todos_snapshot`` 不在此步（ContextInjector 链负责）。
5. 产物走 ``StepResult.output = {"messages", "applied"}``——替换态**不写**
   ``ctx.messages``（由调用方写回）；``metadata["compaction_applied"]``
   双写保留。
6. R-019 注入网：step 源码不得引用同步孪生——有人加回分支此网必红。
"""

from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentnodes.kernel.agent.strategy import LoopContext
from agentnodes.kernel.agent.strategy.steps import DefaultCompactionStep


class _FakeLoop:
    """AgentLoop stand-in for compaction step testing."""

    def __init__(self, applied=None):
        self.applied = applied or []
        self.emit_compaction_calls = []
        self.emit_iter_start_calls = []
        self.inject_todos_calls = []
        self.amaybe_compact_calls = 0

    async def _amaybe_compact(self, messages):
        self.amaybe_compact_calls += 1
        return list(messages), self.applied

    def _emit_compaction(self, applied, iteration, result):
        self.emit_compaction_calls.append((applied, iteration))

    def _emit_iter_start(self, iteration, messages):
        self.emit_iter_start_calls.append((iteration, messages))

    def _inject_todos_snapshot(self, messages):
        self.inject_todos_calls.append(messages)


class TestCompactionStep:
    def test_compaction_roundtrip(self):
        loop = _FakeLoop(applied=["comp1"])
        step = DefaultCompactionStep()
        step.bind_agent_loop(loop)
        ctx = LoopContext(task="t", iteration=2, messages=[{"m": 1}], result=MagicMock())
        r = asyncio.run(step.handle(None, ctx))
        assert loop.amaybe_compact_calls == 1
        assert r.success is True
        assert r.output["messages"] == [{"m": 1}]
        assert r.output["applied"] == ["comp1"]
        # 替换态不入 ctx.messages（调用方负责写回）；metadata 双写保留
        assert ctx.messages == [{"m": 1}]
        assert ctx.metadata["compaction_applied"] == ["comp1"]
        assert loop.emit_compaction_calls == [(["comp1"], 2)]
        assert len(loop.emit_iter_start_calls) == 1
        assert loop.emit_iter_start_calls[0][0] == 2

    def test_no_compaction_skips_emit(self):
        loop = _FakeLoop(applied=[])
        step = DefaultCompactionStep()
        step.bind_agent_loop(loop)
        ctx = LoopContext(task="t", iteration=1, messages=[], result=MagicMock())
        r = asyncio.run(step.handle(None, ctx))
        assert len(loop.emit_compaction_calls) == 0
        assert r.output["applied"] == []
        assert ctx.metadata["compaction_applied"] == []

    def test_emit_iter_start_always_fires(self):
        loop = _FakeLoop()
        step = DefaultCompactionStep()
        step.bind_agent_loop(loop)
        ctx = LoopContext(task="t", iteration=5, messages=[], result=MagicMock())
        asyncio.run(step.handle(None, ctx))
        assert len(loop.emit_iter_start_calls) == 1
        assert loop.emit_iter_start_calls[0][0] == 5

    def test_inject_todos_snapshot_not_called(self):
        """_inject_todos_snapshot is handled by ContextInjector, not CompactionStep."""
        loop = _FakeLoop()
        step = DefaultCompactionStep()
        step.bind_agent_loop(loop)
        ctx = LoopContext(task="t", iteration=1, messages=[], result=MagicMock())
        asyncio.run(step.handle(None, ctx))
        assert len(loop.inject_todos_calls) == 0

    def test_noop_when_no_loop(self):
        step = DefaultCompactionStep()
        ctx = LoopContext(task="t", iteration=1, messages=[{"m": 1}], result=MagicMock())
        r = asyncio.run(step.handle(None, ctx))
        assert r.success is True
        assert r.output["messages"] == [{"m": 1}]
        assert r.output["applied"] == []
        assert "compaction_applied" not in ctx.metadata

    def test_sync_twin_not_referenced(self):
        """R-019 注入网：step 源码不得引用同步孪生——有人加回分支此网红。

        U5.5 后同步分支生产不可达（§1.3）。注意字面量：本测试与类 docstring
        都不得出现同步孪生的名字本身。
        """
        src = inspect.getsource(DefaultCompactionStep)
        assert "_maybe_compact" not in src, "同步孪生引用回流（应恒走 async 孪生）"
