"""LoopConfig + LoopStrategy (P1-1 L4).

A ``LoopStrategy`` is a composition of the 9 step Protocols from
``protocol.py``. v0.1 ships one built-in strategy (``ReActStrategy``)
that mirrors the current ``AgentLoop._run_loop_core`` behaviour.

``LoopConfig`` holds the numbers the AgentLoop currently keeps as
constructor kwargs (max_iterations, no_progress_window, etc.) so
they can travel with the strategy rather than as separate kwargs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .loop_context import LoopContext
from .protocol import (
    CompactionStep,
    ContinuationStep,
    FinalizationStep,
    LLMCallStep,
    PreRunStep,
    ProgressStep,
    ResilienceStep,
    StopStep,
    ToolExecutionStep,
)


@dataclass
class LoopConfig:
    """Numerical knobs that drive the loop, v0.1 mirrors AgentLoop defaults."""

    max_iterations: int = 10
    no_progress_window: int = 3
    heartbeat_interval: float = 15.0
    threshold_tokens: int | None = None
    parallel_tool_execution: bool = True
    max_parallel_tools: int = 4
    tool_max_retries: int = 2
    tool_retry_delay: float = 2.0
    wrap_up_ratio: float = 0.8
    snapshot_interval: int = 0  # 0 = disabled; future-proofing

    # ── A1: three-dimensional run budget (None / <=0 = unlimited) ──
    budget_max_tokens: int | None = None
    budget_max_cost_usd: float | None = None
    budget_max_seconds: float | None = None
    # USD per 1M tokens for cost accounting (optional).
    price_per_mtok_input: float | None = None
    price_per_mtok_output: float | None = None


@dataclass
class LoopStrategy:
    """Composition of 9 step Protocols + a config bag.

    The AgentLoop consults ``should_continue`` to decide whether to keep
    iterating; per-step execution is delegated to the matching slot.
    """

    name: str
    description: str

    pre_run: PreRunStep
    llm_call: LLMCallStep
    compaction: CompactionStep
    stop: StopStep
    continuation: ContinuationStep
    progress: ProgressStep
    resilience: ResilienceStep
    tool_execution: ToolExecutionStep
    finalization: FinalizationStep

    config: LoopConfig = field(default_factory=LoopConfig)

    def should_continue(self, ctx: LoopContext) -> bool:
        """Decide whether to keep iterating after the LLM call + tools.

        Reads ``ctx.should_stop`` (set by StopStep / ResilienceStep /
        ContinuationStep); defaults to True so v0.1's AgentLoop-owned
        flow keeps working unchanged.
        """
        if ctx.should_stop:
            return False
        return True


__all__ = ["LoopConfig", "LoopStrategy"]
