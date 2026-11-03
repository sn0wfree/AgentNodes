"""Default ResilienceStep (L7 v0.2).

Delegates the circuit-breaker gate to AgentLoop's ``_circuit_breaker``.
The *decision* (is_open) lives in the step; the breaker state is
updated by AgentLoop's existing tool-execution path (record_failure /
record_success calls). v0.2 only migrates the decision, not the state
updates.
"""

from __future__ import annotations

from typing import Any

from ..loop_context import LoopContext


class DefaultResilienceStep:
    """Circuit-breaker gate backed by AgentLoop._circuit_breaker."""

    def __init__(self) -> None:
        self._loop: Any = None

    def bind_agent_loop(self, agent_loop: Any) -> None:
        self._loop = agent_loop

    @property
    def name(self) -> str:
        return "resilience"

    def is_open(self, ctx: LoopContext) -> bool:
        loop = self._loop
        if loop is None:
            return False
        cb = getattr(loop, "_circuit_breaker", None)
        return cb is not None and cb.is_open()

    def record_success(self, ctx: LoopContext, tool_name: str) -> None:
        # v0.2: state updates stay in AgentLoop's tool-execution path;
        # this is a hook for future strategies that gate by tool name.
        return None

    def record_failure(self, ctx: LoopContext, tool_name: str) -> None:
        return None


__all__ = ["DefaultResilienceStep"]
