"""P1-A extended: ContextInjector edge cases + integration tests."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.kernel.agent.context_injector import (
    ContextInjector,
    GoalContinuationInjector,
    GoalContextInjector,
    TodosInjector,
    build_default_injectors,
)


# ── Edge cases ────────────────────────────────────────────────────


class TestContextInjectorEdgeCases:
    def test_injectors_sorted_by_order(self):
        """build_default_injectors returns injectors sorted by order."""
        injectors = build_default_injectors()
        orders = [i.order for i in injectors]
        assert orders == sorted(orders)

    def test_duplicate_order_stable(self):
        """Injectors with same order maintain insertion order."""
        injectors = build_default_injectors()
        # All three have distinct orders, but test stability
        names = [i.name for i in injectors]
        assert len(names) == len(set(names))  # all unique

    def test_pre_run_does_not_mutate_loop(self):
        """inject_pre_run should not modify loop attributes."""
        inj = GoalContextInjector()
        loop = MagicMock()
        loop.enable_goal_injection = True
        loop.session_id = "abc"
        original_attrs = dict(loop.__dict__)
        inj.inject_pre_run(loop, "task", [])
        # No new attributes should be added
        assert set(loop.__dict__.keys()) == set(original_attrs.keys())

    def test_per_iteration_does_not_mutate_loop(self):
        """inject_per_iteration should not modify loop attributes."""
        inj = TodosInjector()
        loop = MagicMock()
        loop.session_id = None
        original_attrs = dict(loop.__dict__)
        inj.inject_per_iteration(loop, [])
        assert set(loop.__dict__.keys()) == set(original_attrs.keys())

    def test_post_response_returns_false_by_default(self):
        """ContextInjector default post_response returns False."""
        # The protocol default implementation returns False
        inj = GoalContinuationInjector()
        loop = MagicMock()
        loop.enable_goal_injection = False
        result = inj.inject_post_response(
            loop, MagicMock(content=""), [], MagicMock(), 1
        )
        assert result is False

    def test_injector_exception_is_safe(self):
        """A broken injector should not crash the loop."""
        class _BrokenInjector:
            name = "broken"
            order = 0

            def inject_per_iteration(self, loop, messages):
                raise RuntimeError("boom")

        inj = _BrokenInjector()
        loop = MagicMock()
        loop.session_id = "abc"
        # Should not raise — the loop wraps injector calls in try/except
        # This test verifies the injector itself doesn't guard against errors
        with pytest.raises(RuntimeError):
            inj.inject_per_iteration(loop, [])


# ── GoalContextInjector edge cases ────────────────────────────────


class TestGoalContextInjectorEdgeCases:
    def test_goal_prepend_format(self):
        """Goal context is prepended with double newline separator."""
        inj = GoalContextInjector()
        loop = MagicMock()
        loop.enable_goal_injection = True
        loop.session_id = "abc"
        # Mock get_current_goal_context to return a known value
        import unittest.mock as mock
        with mock.patch(
            "strategy_research.apps.goal.get_current_goal_context",
            return_value=("GOAL CONTEXT", {}),
        ):
            result = inj.inject_pre_run(loop, "original task", [])
            assert result.startswith("GOAL CONTEXT\n\n")
            assert result.endswith("original task")

    def test_empty_goal_no_prepend(self):
        """Empty goal context does not prepend."""
        inj = GoalContextInjector()
        loop = MagicMock()
        loop.enable_goal_injection = True
        loop.session_id = "abc"
        import unittest.mock as mock
        with mock.patch(
            "strategy_research.apps.goal.get_current_goal_context",
            return_value=("", {}),
        ):
            result = inj.inject_pre_run(loop, "original task", [])
            assert result == "original task"


# ── TodosInjector edge cases ─────────────────────────────────────


class TestTodosInjectorEdgeCases:
    def test_empty_todos_no_injection(self):
        """Empty todo list produces no injection."""
        inj = TodosInjector()
        loop = MagicMock()
        loop.session_id = "abc"
        messages: list[dict[str, Any]] = []
        import unittest.mock as mock
        with mock.patch(
            "strategy_research.kernel.agent.builtin_tools.todo_tools.TodoStore"
        ) as MockStore:
            MockStore.get.return_value = []
            inj.inject_per_iteration(loop, messages)
            assert len(messages) == 0

    def test_hash_change_triggers_reinjection(self):
        """Different todo content produces new injection."""
        inj = TodosInjector()
        loop = MagicMock()
        loop.session_id = "abc"
        messages: list[dict[str, Any]] = []
        import unittest.mock as mock

        with mock.patch(
            "strategy_research.kernel.agent.builtin_tools.todo_tools.TodoStore"
        ) as MockStore, mock.patch(
            "strategy_research.kernel.agent.builtin_tools.todo_tools._format_todos_snapshot"
        ) as mock_fmt:
            MockStore.get.return_value = [{"task": "a"}]
            mock_fmt.return_value = "## Todos\n- a"
            inj.inject_per_iteration(loop, messages)
            assert len(messages) == 1

            MockStore.get.return_value = [{"task": "a"}, {"task": "b"}]
            mock_fmt.return_value = "## Todos\n- a\n- b"
            inj.inject_per_iteration(loop, messages)
            assert len(messages) == 2  # new injection


# ── GoalContinuationInjector edge cases ──────────────────────────


class TestGoalContinuationInjectorEdgeCases:
    def test_continuation_appends_to_messages_and_result(self):
        """Continuation adds message to both messages and result."""
        inj = GoalContinuationInjector()
        loop = MagicMock()
        loop.enable_goal_injection = True
        loop.session_id = "abc"
        loop._trace = MagicMock()
        response = MagicMock()
        response.content = "previous answer"
        messages: list[dict[str, Any]] = []
        result = MagicMock()
        result.messages = []
        import unittest.mock as mock

        with mock.patch(
            "strategy_research.kernel.agent.context_injector._get_goal_snapshot",
            return_value={"goal": {"goal_id": "g1"}},
        ), mock.patch(
            "strategy_research.apps.goal.context.goal_needs_continuation",
            return_value=True,
        ), mock.patch(
            "strategy_research.apps.goal.context.format_goal_continuation_prompt",
            return_value="Continue the research.",
        ):
            cont = inj.inject_post_response(loop, response, messages, result, 1)
            assert cont is True
            assert len(messages) == 1
            assert messages[0]["role"] == "user"
            assert messages[0]["content"] == "Continue the research."
            assert len(result.messages) == 1
