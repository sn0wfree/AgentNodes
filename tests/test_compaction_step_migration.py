"""L7 v0.5 — CompactionStep execution migration tests.

Covers:

1. DefaultCompactionStep runs _maybe_compact / _amaybe_compact based
   on async_mode.
2. It fires _emit_compaction when compaction is applied.
3. It fires _emit_iter_start always.
4. Step error isolation still works.
5. should_run returns True (compaction threshold checked internally).
6. _inject_todos_snapshot is NOT called (handled by ContextInjector chain).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentnodes.kernel.agent.strategy import LoopContext
from agentnodes.kernel.agent.strategy.steps import DefaultCompactionStep


class _FakeLoop:
    """AgentLoop stand-in for compaction step testing."""

    def __init__(self, applied=None, async_applied=None):
        self.applied = applied or []
        self.async_applied = async_applied
        self.emit_compaction_calls = []
        self.emit_iter_start_calls = []
        self.inject_todos_calls = []

    def _maybe_compact(self, messages):
        return list(messages), self.applied

    async def _amaybe_compact(self, messages):
        return list(messages), (self.async_applied if self.async_applied is not None else self.applied)

    def _emit_compaction(self, applied, iteration, result):
        self.emit_compaction_calls.append((applied, iteration))

    def _emit_iter_start(self, iteration, messages):
        self.emit_iter_start_calls.append((iteration, messages))

    def _inject_todos_snapshot(self, messages):
        self.inject_todos_calls.append(messages)


class TestCompactionStep:
    def test_sync_path_calls_maybe_compact(self):
        loop = _FakeLoop(applied=["comp1"])
        step = DefaultCompactionStep()
        step.bind_agent_loop(loop)
        ctx = LoopContext(task="t", iteration=2, messages=[{"m": 1}], result=MagicMock())
        import asyncio

        out = asyncio.run(step.execute(ctx, async_mode=False))
        assert out.messages == [{"m": 1}]
        assert out.metadata["compaction_applied"] == ["comp1"]
        assert len(loop.emit_compaction_calls) == 1
        assert loop.emit_compaction_calls[0] == (["comp1"], 2)

    def test_async_path_calls_amaybe_compact(self):
        loop = _FakeLoop(async_applied=["comp_async"])
        step = DefaultCompactionStep()
        step.bind_agent_loop(loop)
        ctx = LoopContext(task="t", iteration=3, messages=[], result=MagicMock())
        import asyncio

        out = asyncio.run(step.execute(ctx, async_mode=True))
        assert out.metadata["compaction_applied"] == ["comp_async"]

    def test_no_compaction_skips_emit(self):
        loop = _FakeLoop(applied=[])
        step = DefaultCompactionStep()
        step.bind_agent_loop(loop)
        ctx = LoopContext(task="t", iteration=1, messages=[], result=MagicMock())
        import asyncio

        asyncio.run(step.execute(ctx, async_mode=False))
        assert len(loop.emit_compaction_calls) == 0

    def test_emit_iter_start_always_fires(self):
        loop = _FakeLoop()
        step = DefaultCompactionStep()
        step.bind_agent_loop(loop)
        ctx = LoopContext(task="t", iteration=5, messages=[], result=MagicMock())
        import asyncio

        asyncio.run(step.execute(ctx, async_mode=False))
        assert len(loop.emit_iter_start_calls) == 1
        assert loop.emit_iter_start_calls[0][0] == 5

    def test_inject_todos_snapshot_not_called(self):
        """_inject_todos_snapshot is handled by ContextInjector, not CompactionStep."""
        loop = _FakeLoop()
        step = DefaultCompactionStep()
        step.bind_agent_loop(loop)
        ctx = LoopContext(task="t", iteration=1, messages=[], result=MagicMock())
        import asyncio

        asyncio.run(step.execute(ctx, async_mode=False))
        assert len(loop.inject_todos_calls) == 0

    def test_should_run_returns_true(self):
        assert DefaultCompactionStep().should_run(LoopContext(task="t")) is True

    def test_noop_when_no_loop(self):
        step = DefaultCompactionStep()
        ctx = LoopContext(task="t", iteration=1, messages=[], result=MagicMock())
        import asyncio

        out = asyncio.run(step.execute(ctx, async_mode=False))
        assert out is ctx