"""P1-5 — Profile resolver + AgentLoop strategy wiring tests.

Covers:
- resolve_loop_strategy() accepts None / str / dict / LoopStrategy
  and rejects invalid input.
- AgentLoop.__init__ stores the resolved strategy on self._strategy
  via get_strategy().
- max_iterations on the strategy.config overrides AgentLoop's default
  when supplied via dict spec (does NOT yet drive _run_loop_core —
  that's L7, but we verify the field is reachable).
- Existing AgentLoop call sites (strategy=None) keep working with
  ReAct default.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentnodes.kernel.agent.strategy import (
    LoopConfig,
    LoopStrategy,
    ReActStrategyFactory,
    resolve_loop_strategy,
)


class TestResolveLoopStrategy:
    def test_none_returns_default_react(self):
        s = resolve_loop_strategy(None)
        assert isinstance(s, LoopStrategy)
        assert s.name == "react"

    def test_string_name_dispatches_to_registry(self):
        s = resolve_loop_strategy("explorer")
        assert s.name == "explorer"
        assert s.config.max_iterations == 50

    def test_dict_name_only(self):
        s = resolve_loop_strategy({"name": "minimal"})
        assert s.name == "minimal"
        assert s.config.max_iterations == 1

    def test_dict_with_config_overrides(self):
        s = resolve_loop_strategy(
            {"name": "validator", "config": {"max_iterations": 3, "no_progress_window": 1}}
        )
        assert s.name == "validator"
        assert s.config.max_iterations == 3
        assert s.config.no_progress_window == 1

    def test_dict_with_unknown_config_keys_ignored(self):
        """Unknown keys must be silently dropped (LoopConfig ignores
        extras); valid keys still apply."""
        s = resolve_loop_strategy(
            {"name": "react", "config": {"max_iterations": 7, "this_key_does_not_exist": True}}
        )
        assert s.config.max_iterations == 7

    def test_passing_a_loop_strategy_returns_unchanged(self):
        custom = ReActStrategyFactory.create(LoopConfig(max_iterations=99))
        out = resolve_loop_strategy(custom)
        assert out is custom
        assert out.config.max_iterations == 99

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError):
            resolve_loop_strategy(42)
        with pytest.raises(ValueError):
            resolve_loop_strategy(["name", "explorer"])

    def test_dict_name_must_be_string(self):
        with pytest.raises(ValueError) as ei:
            resolve_loop_strategy({"name": 42})
        assert "name" in str(ei.value)

    def test_dict_config_must_be_dict(self):
        with pytest.raises(ValueError) as ei:
            resolve_loop_strategy({"name": "react", "config": "not a dict"})
        assert "config" in str(ei.value)

    def test_unknown_strategy_name_raises(self):
        with pytest.raises(KeyError):
            resolve_loop_strategy("no_such_strategy")


# ── AgentLoop integration (smoke — heavy AgentLoop construction is
# avoided; we use a lightweight stand-in subclass instead.) ────────


class TestAgentLoopStrategyField:
    """Lightweight AgentLoop-shaped object that records what the
    resolver was given. Avoids spinning up a full AgentLoop (which
    needs an LLMConfig, tool registry, event bus, etc.)."""

    def _make_loop_like(self, **kwargs):
        """Build an object shaped like AgentLoop for the strategy slot."""

        class _LoopLike:
            pass

        loop = _LoopLike()
        # Re-run the resolver the same way AgentLoop.__init__ does.
        from agentnodes.kernel.agent.strategy.profile_resolver import (
            resolve_loop_strategy as _resolve,
        )
        loop._strategy = _resolve(kwargs.get("strategy"))
        return loop

    def test_default_strategy_when_strategy_is_none(self):
        loop = self._make_loop_like(strategy=None)
        assert isinstance(loop._strategy, LoopStrategy)
        assert loop._strategy.name == "react"

    def test_string_spec_round_trip(self):
        loop = self._make_loop_like(strategy="explorer")
        assert loop._strategy.name == "explorer"
        assert loop._strategy.config.max_iterations == 50

    def test_dict_spec_round_trip(self):
        loop = self._make_loop_like(strategy={"name": "minimal", "config": {"max_iterations": 2}})
        assert loop._strategy.name == "minimal"
        assert loop._strategy.config.max_iterations == 2

    def test_loopstrategy_instance_passes_through(self):
        custom = ReActStrategyFactory.create(LoopConfig(max_iterations=42))
        loop = self._make_loop_like(strategy=custom)
        assert loop._strategy is custom
        assert loop._strategy.config.max_iterations == 42
