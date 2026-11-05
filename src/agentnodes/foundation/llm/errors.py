"""LLM-specific exception types.

These leaf exceptions are part of the unified ``StrategyResearchError``
hierarchy via :class:`core.errors.ConfigError`. LLM provider failures
typically reflect configuration problems (bad api_key, missing model
settings, etc.) so ``ConfigError`` is the natural parent.
"""

from __future__ import annotations

from agentnodes.foundation.errors import ConfigError


class LLMError(ConfigError):
    """Base class for all LLM-related errors."""


class LLMAuthError(LLMError):
    """401/403 — invalid or missing API key."""


class LLMRateLimitError(LLMError):
    """429 — rate limit hit. Client should back off and retry."""


class LLMQuotaError(LLMError):
    """Quota / billing limit exceeded (distinct from per-minute rate limit).

    Raised when the provider returns a specific quota-exceeded error,
    e.g. MiniMax's 5-hour quota limit. The error body is preserved in
    ``self.args[0]`` for upstream logging.
    """


class LLMTimeoutError(LLMError):
    """Request timed out (after configured timeout_s)."""


class LLMServerError(LLMError):
    """5xx — server-side error. Client may retry."""


class LLMMalformedResponseError(LLMError):
    """Response could not be parsed (unexpected shape or invalid JSON)."""


class LLMConfigError(LLMError):
    """Configuration invalid (e.g., missing api_key at call time)."""


__all__ = [
    "LLMError",
    "LLMAuthError",
    "LLMRateLimitError",
    "LLMQuotaError",
    "LLMTimeoutError",
    "LLMServerError",
    "LLMMalformedResponseError",
    "LLMConfigError",
]
