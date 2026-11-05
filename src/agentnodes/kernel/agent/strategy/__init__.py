"""LoopStrategy subpackage (P1-1 + P1-2/3/4).

Re-exports the core abstractions: ``LoopContext``, ``LoopStrategy``,
``LoopConfig``, ``StrategyFactory`` / ``create_strategy`` /
``register_strategy``, ``CustomStrategy``, ``ReActStrategyFactory``,
and the 9 step Protocols from ``protocol``.

P1-2/3/4 add three built-in strategies on top of ReAct:

- ``ExplorerStrategyFactory`` — high-iteration (50), relaxed progress
  window (5).
- ``ValidatorStrategyFactory`` — low-iteration (5), strict progress
  window (2), claim-validation finalization flag.
- ``MinimalStrategyFactory`` — single LLM call, tool execution is a
  no-op.

All three register themselves in the factory at import time (see
the bottom of this module).

v0.1 ships the *infrastructure* (types + factory + step overrides);
the actual AgentLoop._run_loop_core rewrite onto LoopStrategy
happens in a follow-up patch.
"""

from .custom_steps import (
    ClaimValidationFinalizationStep,
    NoOpToolExecutionStep,
)
from .explorer import ExplorerStrategy, ExplorerStrategyFactory
from .factory import (
    CustomStrategy,
    ReActStrategyFactory,
    StrategyFactory,
    create_strategy,
    register_strategy,
)
from .loop_context import LoopContext
from .loop_strategy import LoopConfig, LoopStrategy
from .minimal import MinimalStrategy, MinimalStrategyFactory
from .profile_resolver import resolve_loop_strategy
from .protocol import (
    CompactionStep,
    ContinuationStep,
    FinalizationStep,
    LLMCallStep,
    PreRunStep,
    ProgressStep,
    ResilienceStep,
    Step,
    StopStep,
    ToolExecutionStep,
)
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
from .validator import ValidatorStrategy, ValidatorStrategyFactory

__all__ = [
    "ClaimValidationFinalizationStep",
    "CompactionStep",
    "ContinuationStep",
    "CustomStrategy",
    "DefaultCompactionStep",
    "DefaultContinuationStep",
    "DefaultFinalizationStep",
    "DefaultLLMCallStep",
    "DefaultPreRunStep",
    "DefaultProgressStep",
    "DefaultResilienceStep",
    "DefaultStopStep",
    "DefaultToolExecutionStep",
    "ExplorerStrategy",
    "ExplorerStrategyFactory",
    "FinalizationStep",
    "LLMCallStep",
    "LoopConfig",
    "LoopContext",
    "LoopStrategy",
    "MinimalStrategy",
    "MinimalStrategyFactory",
    "NoOpToolExecutionStep",
    "resolve_loop_strategy",
    "PreRunStep",
    "ProgressStep",
    "ReActStrategyFactory",
    "ResilienceStep",
    "Step",
    "StopStep",
    "StrategyFactory",
    "ToolExecutionStep",
    "ValidatorStrategy",
    "ValidatorStrategyFactory",
    "create_strategy",
    "register_strategy",
]


# ── P1-2/3/4: register extra strategies ──────────────────────────
# Done here (instead of factory.py) so the order of module import is
# deterministic: every submodule loads before registration runs.
register_strategy("explorer", ExplorerStrategyFactory.create)
register_strategy("validator", ValidatorStrategyFactory.create)
register_strategy("minimal", MinimalStrategyFactory.create)
