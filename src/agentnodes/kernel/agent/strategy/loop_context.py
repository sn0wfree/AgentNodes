"""LoopContext — shared state passed between LoopStrategy steps.

P1-1 introduces a composable ``LoopStrategy`` whose ``Step``
implementations exchange state through this context. Defaults are
chosen so v0.1 callers can build a fresh context in one line.

L7 adds ``result`` and ``hook_ctx`` fields so steps can read the
in-flight AgentLoop result / hook context without a separate
``metadata`` lookup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class LoopContext:
    """Mutable container the steps read/write during a loop run.

    Built fresh per ``AgentLoop.run()`` and threaded through every
    ``Step.execute``. Steps mutate fields rather than returning new
    contexts to keep call sites short.
    """

    task: str
    context: Optional[str] = None
    history: Optional[list[dict[str, Any]]] = None

    messages: list[dict[str, Any]] = field(default_factory=list)
    iteration: int = 0
    t0: float = 0.0

    # Last LLM response (LLMCallStep writes it; StopStep/ContinuationStep
    # read it to decide whether to keep iterating).
    response: Any | None = None
    response_was_tool_call: bool = False
    response_content: str = ""

    # Tool execution state.
    recent_hashes: list[str] = field(default_factory=list)
    tool_calls_made: int = 0

    # Compaction state (CompactionStep writes; later steps read).
    previous_summary: Optional[str] = None
    last_seq: int = 0

    # Stop signals — a step may set should_stop + stop_reason to break
    # the loop early. ``should_continue`` reads these to decide.
    should_stop: bool = False
    stop_reason: Optional[str] = None

    # ── A1 budget accounting (written by AgentLoop / BudgetStopStep) ──
    tokens_used: int = 0
    cost_usd: float = 0.0
    elapsed_seconds: float = 0.0

    # L7: carry the in-flight AgentLoop result so steps can mutate
    # it without round-tripping through metadata. Optional so v0.1
    # callers that only build a transient LoopContext (e.g.
    # ``_make_strategy_ctx`` for stop/continuation reads) keep working.
    result: Any | None = None
    hook_ctx: Any | None = None

    # Free-form bag for step-specific payloads (no per-step field
    # explosion; v0.1 keeps it minimal).
    metadata: dict[str, Any] = field(default_factory=dict)


__all__ = ["LoopContext"]
