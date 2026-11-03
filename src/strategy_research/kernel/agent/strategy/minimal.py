"""MinimalStrategy (P1-4) — single-shot LLM answer.

Built for read-only exploration: ask the model once, take whatever it
says as the final answer, do not invoke any tools. The agent loop runs
exactly one iteration (controlled via ``max_iterations=1``) so the
``StopStep`` sees a text-only response on the second iteration check
and breaks — but with ``max_iterations=1`` we never reach that check;
the ``for`` loop exits after one pass.

Composition:
- ReAct base, ``max_iterations=1``.
- ``tool_execution`` is replaced with ``NoOpToolExecutionStep`` so any
  tool calls the assistant suggests are silently ignored.
"""

from __future__ import annotations

from typing import Optional

from .custom_steps import NoOpToolExecutionStep
from .factory import CustomStrategy, ReActStrategyFactory
from .loop_strategy import LoopConfig, LoopStrategy

__all__ = ["MinimalStrategy", "MinimalStrategyFactory"]


class MinimalStrategy(CustomStrategy):
    """One-shot LLM answer; no tool execution."""

    def __init__(self, config: Optional[LoopConfig] = None):
        base = ReActStrategyFactory.create(
            config or LoopConfig(max_iterations=1),
        )
        super().__init__(
            name="minimal",
            description="One-shot LLM answer; tool_execution is a no-op.",
            base_strategy=base,
            config=base.config,
            tool_execution=NoOpToolExecutionStep(),
        )


class MinimalStrategyFactory:
    @staticmethod
    def create(config: Optional[LoopConfig] = None) -> LoopStrategy:
        return MinimalStrategy(config=config)
