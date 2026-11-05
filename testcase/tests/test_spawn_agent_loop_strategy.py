"""Regression tests for spawn_agent() — verify loop_strategy kwarg is accepted
and forwarded to run_agent_via_llm.

Bug: spawn_agent() was missing loop_strategy in its signature, causing
TypeError when runner.py forwarded ExplorerStrategy via _make_spawn_fn.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from strategy_research.apps.autoresearch import spawn_agent


class TestSpawnAgentLoopStrategy:
    """Verify spawn_agent accepts loop_strategy and forwards it correctly."""

    def _call(self, **kwargs):
        """Call spawn_agent in stub mode (no real LLM)."""
        return spawn_agent(
            "researcher",
            Path("/tmp"),
            "test_strategy",
            {},
            [],
            behavior="static",  # force stub path
            **kwargs,
        )

    def test_accepts_loop_strategy_none(self):
        """Default: loop_strategy=None should not raise."""
        result = self._call(loop_strategy=None)
        assert isinstance(result, str)

    def test_accepts_loop_strategy_str(self):
        """String loop_strategy should not raise."""
        result = self._call(loop_strategy="react")
        assert isinstance(result, str)

    def test_accepts_loop_strategy_dict(self):
        """Dict loop_strategy should not raise."""
        result = self._call(loop_strategy={"name": "react"})
        assert isinstance(result, str)

    def test_accepts_loop_strategy_object(self):
        """LoopStrategy instance should not raise."""
        from strategy_research.kernel.agent.strategy import ReActStrategyFactory

        strategy = ReActStrategyFactory.create()
        result = self._call(loop_strategy=strategy)
        assert isinstance(result, str)

    def test_forwards_loop_strategy_to_run_agent_via_llm(self):
        """When use_real=True, loop_strategy must be forwarded to run_agent_via_llm."""
        mock_llm = MagicMock(return_value='{"answer": "ok"}')
        with patch(
            "strategy_research.kernel.agent.role_factory.run_agent_via_llm", mock_llm
        ), patch(
            "strategy_research.kernel.agent.role_factory.should_use_real_llm",
            return_value=True,
        ):
            spawn_agent(
                "researcher",
                Path("/tmp"),
                "test_strategy",
                {},
                [],
                loop_strategy="explorer",
            )
            # Verify run_agent_via_llm was called with loop_strategy
            _, kwargs = mock_llm.call_args
            assert kwargs.get("loop_strategy") == "explorer"

    def test_forwards_loop_strategy_none_to_run_agent_via_llm(self):
        """When loop_strategy is not passed, None should be forwarded."""
        mock_llm = MagicMock(return_value='{"answer": "ok"}')
        with patch(
            "strategy_research.kernel.agent.role_factory.run_agent_via_llm", mock_llm
        ), patch(
            "strategy_research.kernel.agent.role_factory.should_use_real_llm",
            return_value=True,
        ):
            spawn_agent(
                "researcher",
                Path("/tmp"),
                "test_strategy",
                {},
                [],
            )
            _, kwargs = mock_llm.call_args
            assert kwargs.get("loop_strategy") is None

    def test_backward_compat_no_loop_strategy(self):
        """Existing callers that don't pass loop_strategy should still work."""
        result = self._call()
        assert isinstance(result, str)
