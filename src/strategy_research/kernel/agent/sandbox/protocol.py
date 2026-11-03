"""ExecutionSandbox Protocol (P0-2 Phase G).

Captures the safety surface every sandbox provider must expose:
- ``validate_source`` — static AST analysis (existing capability)
- ``resolve_write`` / ``resolve_read`` — path whitelisting (existing)
- ``execute_strategy`` / ``allow_network`` / ``get_resource_usage`` —
  runtime sandbox hooks (future — RestrictedPython / Docker /
  subprocess-with-timeout). v0.1 implementations raise NotImplementedError.

The Protocol is the seam: switching to RestrictedPython or a container
runtime is a Provider swap, not a rewrite of every tool.

P0-2.G scope:
- Protocol defined with the existing 3 methods (validate + 2 resolve).
- Future 3 methods declared but explicit NotImplementedError on the
  default provider — v0.1 callers cannot invoke them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ExecutionSandbox(Protocol):
    """Sandbox facade for static analysis + path safety + (future) runtime."""

    # ── Static analysis (existing capability) ──────────────

    def validate_source(self, source: str) -> tuple[bool, str]:
        """Return ``(ok, message)`` — True if source passes AST checks.

        ``message`` carries the human-readable reason when ``ok`` is
        False; callers surface this to the LLM so it can self-correct.
        """
        ...

    # ── Path safety (existing capability) ──────────────────

    def resolve_write(self, rel_path: str) -> Path:
        """Resolve ``rel_path`` inside the sandbox's writable roots.

        Raises ``PathValidationError`` (or subclass) if the path
        escapes the whitelist (parent traversal, absolute, UNC, etc.).
        """
        ...

    def resolve_read(self, rel_path: str) -> Path:
        """Resolve ``rel_path`` inside the sandbox's readable roots."""
        ...

    # ── Runtime execution (future — v0.1 NotImplementedError) ─

    def execute_strategy(
        self,
        source: str,
        *,
        env: dict[str, str] | None = None,
        timeout: float = 30.0,
        memory_limit_mb: int = 512,
    ) -> Any:
        """Run the strategy source under the sandbox.

        Returns whatever the strategy produces (typically a BacktestResult).
        v0.1 implementations raise NotImplementedError; consumers
        should fall back to the existing ``backtest.py`` subprocess path
        until v0.2 ships the runtime replacement.
        """
        ...

    def allow_network(self, hosts: list[str] | None = None) -> None:
        """Restrict network egress to ``hosts`` (None = no network)."""
        ...

    def get_resource_usage(self) -> dict[str, Any]:
        """Return a snapshot of CPU / memory / wall-clock for the sandbox."""
        ...


__all__ = ["ExecutionSandbox"]
