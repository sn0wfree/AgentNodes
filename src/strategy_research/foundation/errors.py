"""Unified error hierarchy (Phase 3.3).

Every error raised by the ``strategy_research`` package derives from
:class:`StrategyResearchError`, so callers can catch the whole family
with a single ``except`` clause:

    from strategy_research.core.errors import StrategyResearchError

    try:
        run_backtest(...)
    except StrategyResearchError as exc:
        log.exception("Backtest failed: %s", exc)

The hierarchy is intentionally shallow — broad categories that span the
existing subsystem-specific exception classes (``LLMError``,
``StaleGoalError``, etc.). The leaf exceptions continue to live in
their respective modules; this module provides the common root and
mid-level abstract classes.

Hierarchy
---------
    StrategyResearchError                  (root)
    ├── ConfigError                        — invalid configuration
    │   ├── LLMError (from core.llm.errors)
    │   │   ├── LLMAuthError
    │   │   ├── LLMRateLimitError
    │   │   ├── LLMQuotaError
    │   │   ├── LLMTimeoutError
    │   │   ├── LLMServerError
    │   │   ├── LLMMalformedResponseError
    │   │   └── LLMConfigError
    │   └── (future) WorkflowConfigError, etc.
    ├── ProviderError                      — data source or provider failure
    │   └── (future) NoAvailableSourceError, etc.
    ├── SessionError                       — chat / session DB failures
    │   └── (future) SessionNotFoundError, etc.
    ├── BacktestError                      — strategy execution failures
    │   └── (future) InsufficientDataError, etc.
    └── (future) GoalError, SwarmError, etc.

Adding a new subsystem error
----------------------------
1. Subclass the appropriate mid-level error (e.g. ``SessionError``).
2. Optionally raise from a place that already raises a non-hierarchical
   exception (``RuntimeError``, ``ValueError``) to preserve callers.

The leaf exceptions (``LLMError`` family, ``StaleGoalError``, etc.)
remain in their existing modules. They are NOT re-exported here to
avoid import cycles — users import them from where they already live.
"""

from __future__ import annotations

from typing import Any


class StrategyResearchError(Exception):
    """Base class for all errors raised by the strategy_research package.

    Catching this exception catches every error this package can raise
    *except* pre-existing non-hierarchical exceptions (``RuntimeError``,
    ``ValueError``, ``NotImplementedError``, etc.) used by older code
    paths. New code should always raise a subclass of this hierarchy.

    Args:
        message: Human-readable error description.
        details: Optional dict with structured context (cause, retry hints,
                 provider response, etc.). Subclasses may inspect this.
    """

    def __init__(self, message: str = "", *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details: dict[str, Any] = dict(details) if details else {}

    @property
    def code(self) -> str:
        """Machine-readable error code (default: ``__class__.__name__``).

        Subclasses can override to give stable codes (e.g. ``AUTH_FAILED``)
        that callers can match on without coupling to Python class names.
        """
        return type(self).__name__

    def to_dict(self) -> dict[str, Any]:
        """Serialise for JSON / API responses."""
        return {
            "code": self.code,
            "message": str(self),
            "details": dict(self.details),
        }


# ── Mid-level categories ────────────────────────────────────────────


class ConfigError(StrategyResearchError):
    """Configuration invalid (missing required field, bad path, etc.)."""


class ProviderError(StrategyResearchError):
    """External data source / LLM provider failure.

    Distinct from ``ConfigError`` (provider is reachable but misbehaves)
    and from ``SessionError`` (our own state is corrupted).
    """


class SessionError(StrategyResearchError):
    """Session / chat state failures (DB locked, session not found, etc.)."""


class BacktestError(StrategyResearchError):
    """Strategy backtest failures (data insufficient, computation error, etc.)."""


class GoalError(StrategyResearchError):
    """Goal workflow failures (claim rejected, criteria unmet, etc.)."""


class SwarmError(StrategyResearchError):
    """Multi-agent swarm coordination failures."""


class NotFoundError(StrategyResearchError):
    """Generic not-found. Subclasses can specialise (SessionNotFound, etc.)."""

    def __init__(self, what: str = "resource", *, details: dict[str, Any] | None = None) -> None:
        super().__init__(f"{what} not found", details=details)
        self.what = what


# ── Helpers ──────────────────────────────────────────────────────────


def wrap_exception(
    exc: BaseException,
    *,
    into: type[StrategyResearchError],
    message: str | None = None,
) -> StrategyResearchError:
    """Re-raise ``exc`` as ``into`` while preserving the chain (``raise from``).

    Use at API / framework boundaries where you want callers to see a
    uniform ``StrategyResearchError`` but still get the original cause
    via ``__cause__`` / ``__context__``.

    Example::

        try:
            call_provider(...)
        except httpx.HTTPError as exc:
            raise wrap_exception(exc, into=ProviderError,
                                 message="LLM call failed") from exc
    """
    msg = message or str(exc) or exc.__class__.__name__
    wrapped = into(msg, details={"cause": exc.__class__.__name__})
    raise wrapped from exc


__all__ = [
    "BacktestError",
    "ConfigError",
    "GoalError",
    "NotFoundError",
    "ProviderError",
    "SessionError",
    "StrategyResearchError",
    "SwarmError",
    "wrap_exception",
]
