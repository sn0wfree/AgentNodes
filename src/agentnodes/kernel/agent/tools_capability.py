"""Tool capability accessors (P0-2 D).

Small helpers for tools that want to consume the capability-seam
injection on ``ToolContext``. Each helper:
- Returns the seam if the context has it.
- Raises a ``ToolCapabilityError`` with a helpful message when the
  seam is missing — callers should either fall back or surface the
  error to the LLM.

Why a separate helper instead of inline ``ctx.data_store`` access?
Centralising the error path keeps call sites short and ensures every
tool reports the same message ("ToolContext.data_store missing — was
the agent loop constructed with capability injection?").
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .tools import ToolContext


class ToolCapabilityError(RuntimeError):
    """Raised when a required capability seam is absent on ToolContext."""


def get_data_store(ctx: "ToolContext"):
    """Return the ``DataStore`` provider injected on ``ctx``.

    Raises ``ToolCapabilityError`` when ``ctx.data_store`` is None.
    """
    if ctx is None or ctx.data_store is None:
        raise ToolCapabilityError(
            "ToolContext.data_store is None; the agent loop did not "
            "inject a DataStore provider. New tools should not call "
            "this directly without first checking ctx.data_store."
        )
    return ctx.data_store


def get_sandbox(ctx: "ToolContext"):
    """Return the ``ExecutionSandbox`` injected on ``ctx``.

    Raises ``ToolCapabilityError`` when ``ctx.sandbox`` is None.
    """
    if ctx is None or ctx.sandbox is None:
        raise ToolCapabilityError(
            "ToolContext.sandbox is None; the agent loop did not "
            "inject an ExecutionSandbox. New tools should not call "
            "this directly without first checking ctx.sandbox."
        )
    return ctx.sandbox


__all__ = [
    "ToolCapabilityError",
    "get_data_store",
    "get_sandbox",
]
