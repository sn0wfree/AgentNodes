"""P1-A: ContextInjector protocol + concrete injectors tests."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.kernel.agent.context_injector import (
    ContextInjector,
    GoalContextInjector,
    GoalContinuationInjector,
    TodosInjector,
    build_default_injectors,
)

# ── Protocol conformance ──────────────────────────────────────────


class TestContextInjectorProtocol:
    """ContextInjector is a runtime-checkable Protocol."""

    def test_protocol_check(self):
        assert hasattr(ContextInjector, "name")
        assert hasattr(ContextInjector, "order")
        assert hasattr(ContextInjector, "inject_pre_run")
        assert hasattr(ContextInjector, "inject_per_iteration")
        assert hasattr(ContextInjector, "inject_post_response")

    def test_concrete_injectors_satisfy_protocol(self):
        """Each concrete injector has at least the required protocol attributes."""
        for inj in build_default_injectors():
            assert hasattr(inj, "name")
            assert hasattr(inj, "order")
            # Each injector implements at least one injection method
            has_any = any(
                callable(getattr(inj, m, None))
                for m in ("inject_pre_run", "inject_per_iteration", "inject_post_response")
            )
            assert has_any, f"{inj.name} has no injection method"


# ── Factory ──────────────────────────────────────────────────────


class TestBuildDefaultInjectors:
    def test_returns_sorted_list(self):
        injectors = build_default_injectors()
        orders = [i.order for i in injectors]
        assert orders == sorted(orders)

    def test_contains_default_injectors(self):
        injectors = build_default_injectors()
        names = {i.name for i in injectors}
        assert names == {
            "goal_context",
            "memory_retriever",
            "todos_snapshot",
            "goal_continuation",
        }

    def test_order_values(self):
        injectors = build_default_injectors()
        by_name = {i.name: i.order for i in injectors}
        assert by_name["goal_context"] == -100
        assert by_name["memory_retriever"] == -90
        assert by_name["todos_snapshot"] == 0
        assert by_name["goal_continuation"] == 100


# ── GoalContextInjector ──────────────────────────────────────────


class TestGoalContextInjector:
    def test_name_and_order(self):
        inj = GoalContextInjector()
        assert inj.name == "goal_context"
        assert inj.order == -100

    def test_inject_pre_run_no_session(self):
        inj = GoalContextInjector()
        loop = MagicMock()
        loop.enable_goal_injection = True
        loop.session_id = None
        result = inj.inject_pre_run(loop, "task text", [])
        assert result == "task text"

    def test_inject_pre_run_disabled(self):
        inj = GoalContextInjector()
        loop = MagicMock()
        loop.enable_goal_injection = False
        loop.session_id = "abc"
        result = inj.inject_pre_run(loop, "task text", [])
        assert result == "task text"

    def test_inject_pre_run_goal_import_error(self):
        """Gracefully handles import errors (no goal module)."""
        inj = GoalContextInjector()
        loop = MagicMock()
        loop.enable_goal_injection = True
        loop.session_id = "abc"
        # Force ImportError by mocking the import
        import unittest.mock as mock
        with mock.patch.dict("sys.modules", {"strategy_research.apps.goal": None}):
            result = inj.inject_pre_run(loop, "task text", [])
            assert result == "task text"


# ── TodosInjector ────────────────────────────────────────────────


class TestTodosInjector:
    def test_name_and_order(self):
        inj = TodosInjector()
        assert inj.name == "todos_snapshot"
        assert inj.order == 0

    def test_inject_per_iteration_no_session(self):
        inj = TodosInjector()
        loop = MagicMock()
        loop.session_id = None
        messages: list[dict[str, Any]] = []
        inj.inject_per_iteration(loop, messages)
        assert len(messages) == 0

    def test_inject_per_iteration_dedup(self):
        """Same content only injected once."""
        inj = TodosInjector()
        loop = MagicMock()
        loop.session_id = "abc"
        messages: list[dict[str, Any]] = []

        # Mock TodoStore to return same content both times
        import unittest.mock as mock
        fake_block = "## Todos\n- item1"
        with mock.patch(
            "strategy_research.kernel.agent.builtin_tools.todo_tools.TodoStore"
        ) as MockStore, mock.patch(
            "strategy_research.kernel.agent.builtin_tools.todo_tools._format_todos_snapshot",
            return_value=fake_block,
        ):
            MockStore.get.return_value = [{"task": "item1"}]
            inj.inject_per_iteration(loop, messages)
            assert len(messages) == 1
            inj.inject_per_iteration(loop, messages)
            # Still 1 — deduped
            assert len(messages) == 1


# ── GoalContinuationInjector ─────────────────────────────────────


class TestGoalContinuationInjector:
    def test_name_and_order(self):
        inj = GoalContinuationInjector()
        assert inj.name == "goal_continuation"
        assert inj.order == 100

    def test_inject_post_response_disabled(self):
        inj = GoalContinuationInjector()
        loop = MagicMock()
        loop.enable_goal_injection = False
        loop.session_id = "abc"
        response = MagicMock()
        response.content = "answer"
        messages: list[dict[str, Any]] = []
        result = MagicMock()
        result.messages = []
        assert inj.inject_post_response(loop, response, messages, result, 1) is False
        assert len(messages) == 0

    def test_inject_post_response_no_session(self):
        inj = GoalContinuationInjector()
        loop = MagicMock()
        loop.enable_goal_injection = True
        loop.session_id = None
        response = MagicMock()
        messages: list[dict[str, Any]] = []
        result = MagicMock()
        result.messages = []
        assert inj.inject_post_response(loop, response, messages, result, 1) is False

    def test_inject_post_response_no_goal(self):
        """Returns False when no goal snapshot exists."""
        inj = GoalContinuationInjector()
        loop = MagicMock()
        loop.enable_goal_injection = True
        loop.session_id = "abc"
        loop._trace = MagicMock()
        response = MagicMock()
        messages: list[dict[str, Any]] = []
        result = MagicMock()
        result.messages = []
        # _get_goal_snapshot returns None (import error)
        import unittest.mock as mock
        with mock.patch.dict("sys.modules", {"strategy_research.apps.goal": None}):
            assert inj.inject_post_response(loop, response, messages, result, 1) is False
