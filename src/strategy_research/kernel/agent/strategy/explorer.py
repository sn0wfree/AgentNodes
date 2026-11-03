"""ExplorerStrategy (P1-2) — high-iteration research mode.

Built for tasks that need many tool calls without being flagged as
"no progress": large studies, deep code archaeology, multi-source
data exploration. The strategy bumps ``max_iterations`` to 50 and
relaxes ``no_progress_window`` to 5 so the loop doesn't bail out
prematurely when the LLM legitimately iterates over a small set of
adjacent calls.

Composition:
- Inherits every default Step from the ReAct base.
- ``LoopConfig`` overrides ``max_iterations`` and ``no_progress_window``.
- ``StopStep`` stays default; the Progress window change is enough.

The user can further customise by passing ``config=LoopConfig(...)``
to ``ExplorerStrategyFactory.create(config=...)``.
"""

from __future__ import annotations

from typing import Optional

from .factory import CustomStrategy, ReActStrategyFactory
from .loop_strategy import LoopConfig, LoopStrategy

__all__ = ["ExplorerStrategy", "ExplorerStrategyFactory"]


class ExplorerStrategy(CustomStrategy):
    """High-iteration / relaxed-progress exploration strategy."""

    def __init__(self, config: Optional[LoopConfig] = None):
        base = ReActStrategyFactory.create(
            config or LoopConfig(
                max_iterations=50,
                no_progress_window=5,
            ),
        )
        super().__init__(
            name="explorer",
            description="High-iteration (50) exploration; relaxed progress window (5).",
            base_strategy=base,
            config=base.config,
        )


class ExplorerStrategyFactory:
    """Factory mirroring ReActStrategyFactory's static shape."""

    @staticmethod
    def create(config: Optional[LoopConfig] = None) -> LoopStrategy:
        return ExplorerStrategy(config=config)
