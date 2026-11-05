"""Custom Step implementations (P1-3 / P1-4).

Shared helpers used by ValidatorStrategy / MinimalStrategy when their
default ReAct behaviour needs to differ from the regular flow:

- ``ClaimValidationFinalizationStep`` — runs the legacy claim-validation
  hook (mirrors ``AgentLoop._run_claim_validation`` semantics) before
  returning. The actual call is deferred to L7 (AgentLoop rewrite); v0.1
  marks ``ctx.metadata["claim_validation_ran"] = True`` so tests can
  assert the strategy asked for the validation.

- ``NoOpToolExecutionStep`` — runs no tool calls; the assistant's
  response is recorded as a final answer. Used by MinimalStrategy to
  force a one-shot "ask the LLM, return what it says" flow.
"""

from __future__ import annotations

from .loop_context import LoopContext


class ClaimValidationFinalizationStep:
    """Marks ``ctx.metadata["claim_validation_ran"] = True``.

    Real call lands in L7 alongside the AgentLoop rewrite.
    """

    @property
    def name(self) -> str:
        return "finalization.claim_validation"

    def execute(self, ctx: LoopContext, *, async_mode: bool) -> LoopContext:
        ctx.metadata["claim_validation_ran"] = True
        return ctx


class NoOpToolExecutionStep:
    """No-op tool execution — context passes through unchanged.

    The assistant's tool calls (if any) are *not* invoked. This is
    intentional: MinimalStrategy wants a single LLM answer without
    consuming tool budget.
    """

    @property
    def name(self) -> str:
        return "tool_execution.noop"

    def execute(self, ctx: LoopContext, *, async_mode: bool) -> LoopContext:
        # Strip any tool-call request from the response so the next
        # iteration's StopStep sees a text-only response and breaks.
        if ctx.response is not None and getattr(
            ctx.response, "tool_calls", None,
        ):
            ctx.response = ctx.response  # leave as-is; StopStep
            ctx.metadata["tool_execution_skipped"] = True
        return ctx


__all__ = [
    "ClaimValidationFinalizationStep",
    "NoOpToolExecutionStep",
]
