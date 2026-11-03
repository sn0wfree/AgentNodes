"""Step Protocol + concrete step protocols (P1-1).

A ``Step`` is a single decision point inside the ReAct-style loop:

- ``PreRunStep`` — hypothesis / goal / context injection (runs once)
- ``LLMCallStep`` — chat/stream call (per iteration)
- ``CompactionStep`` — context compression (per iteration, optional)
- ``StopStep`` — when to break (per iteration, after LLM call)
- ``ContinuationStep`` — when to keep iterating despite a text-only
  response (e.g. goal still has uncovered criteria)
- ``ProgressStep`` — repeated-hash detection (per iteration, after tools)
- ``ResilienceStep`` — circuit breaker / per-tool failures (per iteration)
- ``ToolExecutionStep`` — run the assistant's tool calls (per iteration)
- ``FinalizationStep`` — metrics, claim validation, git commit (after loop)

Each Step Protocol is a ``runtime_checkable`` ``Protocol`` so that
``isinstance(step, PreRunStep)`` works at the boundary. The same
shape — ``should_run(ctx)`` + ``execute(ctx, *, async_mode)`` — applies
to every step so composition stays uniform.
"""

from __future__ import annotations

from typing import Awaitable, Protocol, runtime_checkable

from .loop_context import LoopContext


@runtime_checkable
class Step(Protocol):
    """Common Step surface — name + should_run + execute."""

    @property
    def name(self) -> str:
        ...

    def should_run(self, ctx: LoopContext) -> bool:
        ...

    def execute(self, ctx: LoopContext, *, async_mode: bool) -> LoopContext | Awaitable[LoopContext]:
        ...


# ── Per-step Protocol contracts ──────────────────────────────


@runtime_checkable
class PreRunStep(Protocol):
    """Run once before the iteration loop starts."""

    def execute(self, ctx: LoopContext, *, async_mode: bool) -> LoopContext | Awaitable[LoopContext]:
        ...


@runtime_checkable
class LLMCallStep(Protocol):
    """Fetch the assistant's response for ``ctx.iteration``.

    Returns the updated context (with ``response`` populated). A return
    value of ``None`` (or a context with ``response is None``) signals
    the loop to break (e.g. fatal LLM error).
    """

    def execute(self, ctx: LoopContext, *, async_mode: bool) -> LoopContext | Awaitable[LoopContext]:
        ...


@runtime_checkable
class CompactionStep(Protocol):
    """Compress context when over threshold. No-op by default."""

    def execute(self, ctx: LoopContext, *, async_mode: bool) -> LoopContext | Awaitable[LoopContext]:
        ...


@runtime_checkable
class StopStep(Protocol):
    """Decide whether to break the loop after the LLM call.

    Returns ``(should_stop, reason)``; the loop also consults
    ``ctx.should_stop`` set by other steps (Resilience, Continuation).
    """

    def evaluate(self, ctx: LoopContext) -> tuple[bool, str | None]:
        ...


@runtime_checkable
class ContinuationStep(Protocol):
    """Decide whether to keep iterating despite a text-only response.

    Returns ``(should_continue, reason)``. ``False`` means the text
    response is final (loop ends); ``True`` means inject a follow-up
    prompt and re-call the LLM.
    """

    def evaluate(self, ctx: LoopContext) -> tuple[bool, str | None]:
        ...


@runtime_checkable
class ProgressStep(Protocol):
    """Detect no-progress (same tool call repeated N times)."""

    def record_hash(self, ctx: LoopContext, hash_value: str) -> None:
        ...

    def is_no_progress(self, ctx: LoopContext) -> bool:
        ...


@runtime_checkable
class ResilienceStep(Protocol):
    """Circuit breaker / per-tool failure gating."""

    def is_open(self, ctx: LoopContext) -> bool:
        ...

    def record_success(self, ctx: LoopContext, tool_name: str) -> None:
        ...

    def record_failure(self, ctx: LoopContext, tool_name: str) -> None:
        ...


@runtime_checkable
class ToolExecutionStep(Protocol):
    """Run the assistant's tool calls; update ctx.messages."""

    def execute(self, ctx: LoopContext, *, async_mode: bool) -> LoopContext | Awaitable[LoopContext]:
        ...


@runtime_checkable
class FinalizationStep(Protocol):
    """Run after the loop exits — metrics, claim validation, git."""

    def execute(self, ctx: LoopContext, *, async_mode: bool) -> LoopContext | Awaitable[LoopContext]:
        ...


__all__ = [
    "CompactionStep",
    "ContinuationStep",
    "FinalizationStep",
    "LLMCallStep",
    "PreRunStep",
    "ProgressStep",
    "ResilienceStep",
    "Step",
    "StopStep",
    "ToolExecutionStep",
]
