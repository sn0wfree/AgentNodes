"""StrategyFactory + preset ReActStrategy (P1-1 L5).

Mirrors the LLM provider / DataStore / BacktestEngine registry pattern:
- Default provider is registered at import time.
- ``register(name, factory)`` adds new strategies.
- ``create(name=None, config=None)`` builds a fresh instance.

P1-1 ships ``ReActStrategy`` only — it composes the 9 default Step
implementations. ``ExplorerStrategy`` / ``ValidatorStrategy`` /
``MinimalStrategy`` are P1-2/3/4 deliverables.

``CustomStrategy`` is a thin subclass of ``LoopStrategy`` so callers
can override specific steps without rewriting composition logic.
"""

from __future__ import annotations

from typing import Callable, Optional

from .loop_strategy import LoopConfig, LoopStrategy
from .steps import (
    DefaultCompactionStep,
    DefaultContinuationStep,
    DefaultFinalizationStep,
    DefaultLLMCallStep,
    DefaultPreRunStep,
    DefaultProgressStep,
    DefaultResilienceStep,
    DefaultStopStep,
    DefaultToolExecutionStep,
)

__all__ = [
    "CustomStrategy",
    "ReActStrategyFactory",
    "StrategyFactory",
    "create_strategy",
    "register_strategy",
]


_FACTORIES: dict[str, Callable[[Optional[LoopConfig]], LoopStrategy]] = {}


class StrategyFactory:
    """Process-global strategy registry."""

    @classmethod
    def register(cls, name: str, factory: Callable) -> None:
        _FACTORIES[name] = factory

    @classmethod
    def unregister(cls, name: str) -> None:
        _FACTORIES.pop(name, None)

    @classmethod
    def available(cls) -> list[str]:
        return list(_FACTORIES.keys())


def register_strategy(
    name: str, factory: Callable[[Optional[LoopConfig]], LoopStrategy]
) -> None:
    """Module-level convenience for ``StrategyFactory.register``."""
    StrategyFactory.register(name, factory)


def create_strategy(
    name: Optional[str] = None,
    config: Optional[LoopConfig] = None,
) -> LoopStrategy:
    """Build a ``LoopStrategy`` by name. Default is ``"react"``."""
    if name is None:
        name = "react"
    if name not in _FACTORIES:
        raise KeyError(
            f"unknown LoopStrategy {name!r}; available: "
            f"{sorted(_FACTORIES.keys())}"
        )
    return _FACTORIES[name](config)


class ReActStrategyFactory:
    """Default ReAct strategy — current AgentLoop behaviour."""

    @staticmethod
    def create(config: Optional[LoopConfig] = None) -> LoopStrategy:
        cfg = config or LoopConfig()
        return LoopStrategy(
            name="react",
            description="Default ReAct loop — mirrors current AgentLoop behaviour",
            pre_run=DefaultPreRunStep(),
            llm_call=DefaultLLMCallStep(),
            compaction=DefaultCompactionStep(),
            stop=DefaultStopStep(),
            continuation=DefaultContinuationStep(),
            progress=DefaultProgressStep(),
            resilience=DefaultResilienceStep(),
            tool_execution=DefaultToolExecutionStep(),
            finalization=DefaultFinalizationStep(),
            config=cfg,
        )


class CustomStrategy(LoopStrategy):
    """Base class for user-defined strategies — override specific steps.

    Usage:
        class MyExplorerStrategy(CustomStrategy):
            def __init__(self):
                super().__init__(
                    name="my_explorer",
                    base_strategy=ReActStrategyFactory.create(
                        LoopConfig(max_iterations=50),
                    ),
                    stop=MyCustomStopStep(),
                )
    """

    def __init__(
        self,
        *,
        name: str = "custom",
        description: str = "Custom LoopStrategy",
        base_strategy: LoopStrategy | None = None,
        config: LoopConfig | None = None,
        pre_run=None,
        llm_call=None,
        compaction=None,
        stop=None,
        continuation=None,
        progress=None,
        resilience=None,
        tool_execution=None,
        finalization=None,
    ):
        base = base_strategy or ReActStrategyFactory.create(config)
        super().__init__(
            name=name,
            description=description,
            pre_run=pre_run or base.pre_run,
            llm_call=llm_call or base.llm_call,
            compaction=compaction or base.compaction,
            stop=stop or base.stop,
            continuation=continuation or base.continuation,
            progress=progress or base.progress,
            resilience=resilience or base.resilience,
            tool_execution=tool_execution or base.tool_execution,
            finalization=finalization or base.finalization,
            config=config or base.config,
        )


# ── Default registration at import time ──────────────────────────
register_strategy("react", ReActStrategyFactory.create)

# P1-2/3/4 built-in strategies (explorer/validator/minimal) are
# registered by the subpackage ``__init__`` after every module in
# this directory has loaded — avoids a circular import where
# ``factory`` triggers ``explorer`` before its own types exist.
