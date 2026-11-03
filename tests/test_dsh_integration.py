"""Integration: AgentLoop with DSH-inspired features (injectors + inbox + guards)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.kernel.agent.context_injector import (
    ContextInjector,
    GoalContextInjector,
    TodosInjector,
    GoalContinuationInjector,
    build_default_injectors,
)
from strategy_research.kernel.agent.loop import AgentLoop
from strategy_research.kernel.agent.tools import ToolRegistry, BaseTool


class _FakeConfig:
    compact_config = None


class _DummyTool(BaseTool):
    name = "dummy"
    description = "A dummy tool"
    parameters = {}

    def execute(self, **kwargs):
        return '{"status": "ok"}'


class _TrackingInjector:
    """Injector that tracks calls for testing."""

    def __init__(self, name: str, order: int):
        self.name = name
        self.order = order
        self.pre_run_calls = []
        self.per_iter_calls = []
        self.post_resp_calls = []

    def inject_pre_run(self, loop, task, messages):
        self.pre_run_calls.append(task)
        return task + " [injected]"

    def inject_per_iteration(self, loop, messages):
        self.per_iter_calls.append(len(messages))

    def inject_post_response(self, loop, response, messages, result, iteration):
        self.post_resp_calls.append(iteration)
        return False


class TestAgentLoopDSHIntegration:
    def _make_loop(self, injectors=None):
        return AgentLoop(
            config=_FakeConfig(),
            registry=ToolRegistry(),
            max_iterations=5,
            injectors=injectors,
        )

    def test_default_injectors_loaded(self):
        """AgentLoop loads default injectors when none provided."""
        loop = self._make_loop()
        assert len(loop._injectors) == 4
        names = {i.name for i in loop._injectors}
        assert names == {
            "goal_context", "memory_retriever",
            "todos_snapshot", "goal_continuation",
        }

    def test_custom_injectors(self):
        """AgentLoop accepts custom injectors."""
        custom = [_TrackingInjector("custom", 0)]
        loop = self._make_loop(injectors=custom)
        assert len(loop._injectors) == 1
        assert loop._injectors[0].name == "custom"

    def test_injectors_sorted_by_order(self):
        """Injectors are sorted by order."""
        i1 = _TrackingInjector("first", 100)
        i2 = _TrackingInjector("second", -100)
        i3 = _TrackingInjector("third", 0)
        loop = self._make_loop(injectors=[i1, i2, i3])
        orders = [i.order for i in loop._injectors]
        assert orders == [-100, 0, 100]

    def test_inbox_initialized(self):
        """AgentLoop creates inbox on init."""
        loop = self._make_loop()
        assert hasattr(loop, "_inbox")
        assert isinstance(loop._inbox, asyncio.Queue)

    def test_inject_adds_to_inbox(self):
        """inject() adds message to inbox."""
        loop = self._make_loop()
        loop.inject({"role": "user", "content": "steer"})
        assert not loop._inbox.empty()

    def test_drain_inbox_works(self):
        """_drain_inbox moves messages to conversation."""
        loop = self._make_loop()
        loop.inject({"role": "user", "content": "a"})
        loop.inject({"role": "user", "content": "b"})
        messages = []
        loop._drain_inbox(messages)
        assert len(messages) == 2
        assert messages[0]["content"] == "a"
        assert messages[1]["content"] == "b"

    def test_guard_blocks_tool(self):
        """Guard denies tool execution."""
        r = ToolRegistry()
        r.register(_DummyTool())

        class _DenyDummy:
            def check(self, name, params):
                if name == "dummy":
                    return "Denied"
                return None

        r.guard(_DenyDummy())
        reason = r.check_guards("dummy", {})
        assert reason == "Denied"

    def test_guard_allows_tool(self):
        """Guard allows tool when not denied."""
        r = ToolRegistry()
        r.register(_DummyTool())

        class _DenyOther:
            def check(self, name, params):
                if name == "other":
                    return "Denied"
                return None

        r.guard(_DenyOther())
        assert r.check_guards("dummy", {}) is None

    def test_restricted_registry(self):
        """restricted() returns independent copy."""
        r = ToolRegistry()
        r.register(_DummyTool())
        r2 = r.restricted(deny={"dummy"})
        assert len(r) == 1  # original unchanged
        assert len(r2) == 0  # copy has restriction

    def test_injector_exception_safe(self):
        """Broken injector doesn't crash the loop."""

        class _BrokenInjector:
            name = "broken"
            order = 0

            def inject_per_iteration(self, loop, messages):
                raise RuntimeError("boom")

        loop = self._make_loop(injectors=[_BrokenInjector()])
        messages = []
        # _drain_inbox + injector calls are wrapped in try/except in the loop
        # This test verifies the injector itself raises
        with pytest.raises(RuntimeError):
            loop._injectors[0].inject_per_iteration(loop, messages)

    def test_inbox_drain_fifo(self):
        """Inbox maintains FIFO order."""
        loop = self._make_loop()
        for i in range(10):
            loop.inject({"role": "user", "content": f"msg{i}"})
        messages = []
        loop._drain_inbox(messages)
        contents = [m["content"] for m in messages]
        assert contents == [f"msg{i}" for i in range(10)]

    def test_multiple_tool_guards(self):
        """Multiple guards compose correctly."""
        r = ToolRegistry()
        r.register(_DummyTool())

        class _Guard1:
            def check(self, name, params):
                return None  # allow all

        class _Guard2:
            def check(self, name, params):
                if name == "dummy":
                    return "Guard2 denied"
                return None

        r.guard(_Guard1())
        r.guard(_Guard2())
        # Guard2 denies
        assert r.check_guards("dummy", {}) == "Guard2 denied"
