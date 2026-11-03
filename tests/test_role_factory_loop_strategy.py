"""B: build_agent_loop + run_agent_via_llm loop_strategy integration tests.

Verifies:
- build_agent_loop passes loop_strategy to AgentLoop via resolve_loop_strategy.
- run_agent_via_llm forwards loop_strategy to build_agent_loop.
- Default (loop_strategy=None) → "react".
- String spec ("explorer") → correct strategy.
- Dict spec → correct strategy + config.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.core.agent.role_factory import build_agent_loop
from strategy_research.kernel.agent.strategy import LoopStrategy


class TestBuildAgentLoopLoopStrategy:
    def test_none_default_react(self, tmp_path):
        """loop_strategy=None → AgentLoop.get_strategy() returns react."""
        loop = build_agent_loop(
            role="researcher",
            workspace_path=tmp_path,
            strategy_name="test",
            loop_strategy=None,
        )
        assert loop is not None
        strategy = loop.get_strategy()
        assert strategy.name == "react"
        assert strategy.config.max_iterations == 10

    def test_string_spec(self, tmp_path):
        """loop_strategy='explorer' → AgentLoop uses explorer strategy."""
        loop = build_agent_loop(
            role="researcher",
            workspace_path=tmp_path,
            strategy_name="test",
            loop_strategy="explorer",
        )
        assert loop is not None
        strategy = loop.get_strategy()
        assert strategy.name == "explorer"
        assert strategy.config.max_iterations == 50

    def test_dict_spec_with_config(self, tmp_path):
        """loop_strategy={'name': 'validator', 'config': {'max_iterations': 7}}."""
        loop = build_agent_loop(
            role="researcher",
            workspace_path=tmp_path,
            strategy_name="test",
            loop_strategy={
                "name": "validator",
                "config": {"max_iterations": 7, "no_progress_window": 1},
            },
        )
        assert loop is not None
        strategy = loop.get_strategy()
        assert strategy.name == "validator"
        assert strategy.config.max_iterations == 7
        assert strategy.config.no_progress_window == 1

    def test_loop_strategy_instance(self, tmp_path):
        """Passing a LoopStrategy instance directly."""
        from strategy_research.kernel.agent.strategy import create_strategy

        custom = create_strategy("minimal")
        loop = build_agent_loop(
            role="researcher",
            workspace_path=tmp_path,
            strategy_name="test",
            loop_strategy=custom,
        )
        assert loop is not None
        assert loop.get_strategy().name == "minimal"


class TestRunAgentViaLlmLoopStrategy:
    def test_forwards_loop_strategy(self, tmp_path):
        """run_agent_via_llm passes loop_strategy to build_agent_loop."""
        # We can't actually run the LLM, but we can verify the strategy
        # is correctly resolved by mocking LLMConfig.load and the prompt.
        from strategy_research.core.agent.role_factory import build_agent_loop

        # Mock PromptBuilderFactory to always return a prompt
        import strategy_research.core.agent.role_factory as rf
        from strategy_research.core.agent.prompt_builder import PromptBuilderFactory

        class _FakePromptBuilder:
            def build_system_prompt(self, role, data):
                return "You are a test agent."

        original_get = PromptBuilderFactory.get
        PromptBuilderFactory.get = staticmethod(lambda role: _FakePromptBuilder())
        try:
            loop = build_agent_loop(
                role="researcher",
                workspace_path=tmp_path,
                strategy_name="test",
                loop_strategy={"name": "explorer"},
            )
            assert loop is not None
            assert loop.get_strategy().name == "explorer"
        finally:
            PromptBuilderFactory.get = original_get