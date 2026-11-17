"""LoopStrategy subpackage (P1-1 + P1-2/3/4 + B2-① T7).

Re-exports the core abstractions: ``LoopContext``, ``LoopStrategy``,
``LoopConfig``, ``StrategyFactory`` / ``create_strategy`` /
``register_strategy``, ``CustomStrategy``, ``ReActStrategyFactory``,
``StepHandler``（执行型槽位的统一底座）与判定型的 4 枚 Protocol。

**B2-① T7**：执行型 6 枚 Protocol（``PreRunStep`` / ``LLMCallStep`` /
``CompactionStep`` / ``ToolExecutionStep`` / ``FinalizationStep`` /
``Step``）已并入 ``kernel/agent/_core_types.py::StepHandler``——7 个
生产 step 类全部改 ``handle(input, ctx) → StepResult`` 后，它们失去存在
理由。本包只导出判定型 4 枚（decider 家族的降级适配面）。

P1-2/3/4 add three built-in strategies on top of ReAct:

- ``ExplorerStrategyFactory`` — high-iteration (50), relaxed progress
  window (5).
- ``ValidatorStrategyFactory`` — low-iteration (5), strict progress
  window (2), claim-validation finalization flag.
- ``MinimalStrategyFactory`` — single LLM call, tool execution is a
  no-op.

All three register themselves in the factory at import time (see
the bottom of this module).
"""

from agentnodes.kernel.agent._core_types import StepHandler
from agentnodes.kernel.agent.strategy.custom_steps import (
    ClaimValidationFinalizationStep,
    NoOpToolExecutionStep,
)
from agentnodes.kernel.agent.strategy.explorer import ExplorerStrategy, ExplorerStrategyFactory
from agentnodes.kernel.agent.strategy.factory import (
    CustomStrategy,
    ReActStrategyFactory,
    StrategyFactory,
    create_strategy,
    register_strategy,
)
from agentnodes.kernel.agent.strategy.loop_context import LoopContext
from agentnodes.kernel.agent.strategy.loop_strategy import LoopConfig, LoopStrategy
from agentnodes.kernel.agent.strategy.minimal import MinimalStrategy, MinimalStrategyFactory
from agentnodes.kernel.agent.strategy.profile_resolver import resolve_loop_strategy
from agentnodes.kernel.agent.strategy.protocol import (
    ContinuationStep,
    ProgressStep,
    ResilienceStep,
    StopStep,
)
from agentnodes.kernel.agent.strategy.steps import (
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
from agentnodes.kernel.agent.strategy.validator import ValidatorStrategy, ValidatorStrategyFactory

__all__ = [
    "ClaimValidationFinalizationStep",
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
    "LoopConfig",
    "LoopContext",
    "LoopStrategy",
    "MinimalStrategy",
    "MinimalStrategyFactory",
    "NoOpToolExecutionStep",
    "ProgressStep",
    "ReActStrategyFactory",
    "ResilienceStep",
    "StepHandler",
    "StopStep",
    "StrategyFactory",
    "ValidatorStrategy",
    "ValidatorStrategyFactory",
    "create_strategy",
    "register_strategy",
    "resolve_loop_strategy",
]


# ── P1-2/3/4: register extra strategies ──────────────────────────
# Done here (instead of factory.py) so the order of module import is
# deterministic: every submodule loads before registration runs.
register_strategy("explorer", ExplorerStrategyFactory.create)
register_strategy("validator", ValidatorStrategyFactory.create)
register_strategy("minimal", MinimalStrategyFactory.create)
