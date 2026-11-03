"""Default ProgressStep (L7 v0.2).

Detects no-progress (same tool call repeated N times) by delegating to
the AgentLoop's existing ``_recent_hashes`` + ``_detect_no_progress``
state. The *decision* (is_no_progress) lives in the step; the
*side-effects* of triggering (record_event, circuit_breaker, emit)
stay in AgentLoop's legacy ``_check_no_progress`` — v0.2 only migrates
the decision, not the fallout.

Custom strategies can override ``record_hash`` / ``is_no_progress`` to
change the detection semantics (window size, hash function, etc.)
without touching AgentLoop.
"""

from __future__ import annotations

from typing import Any

from ..loop_context import LoopContext


class DefaultProgressStep:
    """Hash-window no-progress detection backed by AgentLoop state."""

    def __init__(self) -> None:
        self._loop: Any = None

    def bind_agent_loop(self, agent_loop: Any) -> None:
        self._loop = agent_loop

    @property
    def name(self) -> str:
        return "progress"

    def record_hash(self, ctx: LoopContext, hash_value: str) -> None:
        """Append a tool-call hash to AgentLoop's window.

        Mirrors ``AgentLoop._check_no_progress``'s window trimming so
        the strategy decision matches the legacy behaviour exactly.
        """
        loop = self._loop
        if loop is None:
            # No loop bound — fall back to ctx.recent_hashes (keeps the
            # strategy standalone-testable).
            ctx.recent_hashes.append(hash_value)
            window = ctx.metadata.get("progress_window", 3)
            if len(ctx.recent_hashes) > window:
                ctx.recent_hashes = ctx.recent_hashes[-window:]
            return
        loop._recent_hashes.append(hash_value)
        window = getattr(loop, "no_progress_window", 3)
        if len(loop._recent_hashes) > window:
            loop._recent_hashes = loop._recent_hashes[-window:]

    def is_no_progress(self, ctx: LoopContext) -> bool:
        """True if the last N tool-call hashes are all identical."""
        loop = self._loop
        if loop is not None and hasattr(loop, "_detect_no_progress"):
            return loop._detect_no_progress()
        # Standalone fallback (no loop): use ctx.recent_hashes.
        window = ctx.metadata.get("progress_window", 3)
        if len(ctx.recent_hashes) < window:
            return False
        recent = ctx.recent_hashes[-window:]
        return len(set(recent)) == 1


__all__ = ["DefaultProgressStep"]
