"""CircuitBreaker + RetryPolicy for AgentLoop resilience.

CircuitBreaker: 3-state machine (CLOSED → OPEN → HALF_OPEN → CLOSED)
    - Per-tool-name consecutive failure tracking
    - No-progress (same hash) detection
    - Total failure cap
    - Cooldown-based automatic half-open

RetryPolicy: Exponential backoff with jitter for LLM/tool calls.
"""
from __future__ import annotations

import enum
import logging
import random
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


class BreakerState(enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreakerConfig:
    """Configuration for ToolLoopCircuitBreaker."""

    failure_threshold: int = 3
    no_progress_window: int = 3
    max_total_failures: int = 10
    cooldown_seconds: float = 60.0


class ToolLoopCircuitBreaker:
    """3-state circuit breaker for tool execution loops.

    Tracks failures per tool name, total failures, and no-progress
    (identical tool_call hashes). When any threshold is exceeded,
    transitions to OPEN. After cooldown, enters HALF_OPEN for one
    attempt to determine if the system has recovered.
    """

    def __init__(
        self,
        config: CircuitBreakerConfig | None = None,
        session_id: str | None = None,
    ) -> None:
        self._config = config or CircuitBreakerConfig()
        self.session_id = session_id
        self._state: BreakerState = BreakerState.CLOSED
        self._tool_failures: dict[str, int] = {}
        self._total_failures: int = 0
        self._no_progress_count: int = 0
        self._opened_at: float = 0.0

    # ── Public API ──

    def record_success(self, tool_name: str) -> None:
        """Record a successful tool execution.

        Resets the per-tool failure counter. If in HALF_OPEN,
        transitions back to CLOSED.
        """
        self._tool_failures[tool_name] = 0
        if self._state is BreakerState.HALF_OPEN:
            logger.info("Circuit breaker recovered (tool=%s), closing", tool_name)
            self._state = BreakerState.CLOSED
            self._total_failures = 0
            self._no_progress_count = 0

    def record_failure(self, tool_name: str) -> None:
        """Record a failed tool execution.

        Increments per-tool and total failure counters. Opens if
        any threshold is exceeded.
        """
        self._tool_failures[tool_name] = self._tool_failures.get(tool_name, 0) + 1
        self._total_failures += 1
        self._check_and_open()

    def record_no_progress(self) -> None:
        """Record a no-progress event (identical tool_call hash)."""
        self._no_progress_count += 1
        self._check_and_open()

    def is_open(self) -> bool:
        """Check if the circuit is OPEN and handle cooldown."""
        if self._state is BreakerState.OPEN:
            elapsed = time.monotonic() - self._opened_at
            if elapsed >= self._config.cooldown_seconds:
                logger.info(
                    "Circuit breaker half-opening after %.1fs cooldown",
                    elapsed,
                )
                self._state = BreakerState.HALF_OPEN
        return self._state is BreakerState.OPEN

    @property
    def state(self) -> BreakerState:
        return self._state

    def reset(self) -> None:
        """Reset the circuit breaker to CLOSED state."""
        self._state = BreakerState.CLOSED
        self._tool_failures.clear()
        self._total_failures = 0
        self._no_progress_count = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize state for tracing."""
        return {
            "state": self._state.value,
            "tool_failures": dict(self._tool_failures),
            "total_failures": self._total_failures,
            "no_progress_count": self._no_progress_count,
            "opened_at": self._opened_at,
        }

    # ── Internal ──

    def _check_and_open(self) -> None:
        """Check thresholds and open if any exceeded."""
        if self._state is BreakerState.OPEN:
            return

        # Check per-tool failure threshold
        for tool_name, count in self._tool_failures.items():
            if count >= self._config.failure_threshold:
                self._open(f"tool '{tool_name}' failed {count} consecutive times")
                return

        # Check no-progress threshold
        if self._no_progress_count >= self._config.no_progress_window:
            self._open(f"no_progress count {self._no_progress_count} >= {self._config.no_progress_window}")
            return

        # Check total failure threshold
        if self._total_failures >= self._config.max_total_failures:
            self._open(f"total failures {self._total_failures} >= {self._config.max_total_failures}")
            return

    def _open(self, reason: str) -> None:
        """Transition to OPEN state."""
        self._state = BreakerState.OPEN
        self._opened_at = time.monotonic()
        logger.warning("Circuit breaker OPEN: %s", reason)
        from ..observability.hanging_events import record_event
        record_event(
            "circuit_breaker_open",
            session_id=getattr(self, "session_id", None),
            detail=reason,
        )


# ── RetryPolicy ─────────────────────────────────────────────────────


@dataclass
class RetryPolicy:
    """Exponential backoff retry policy for LLM / tool calls.

    Attributes:
        max_retries: Maximum number of retry attempts.
        base_delay: Initial delay in seconds (doubles each attempt).
        max_delay: Maximum delay in seconds (capped).
        jitter: Add random jitter (0.5x-1.5x) to avoid thundering herd.
    """

    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    jitter: bool = True

    def get_delay(self, attempt: int) -> float:
        """Calculate delay for a given attempt number (1-based)."""
        delay = min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)
        if self.jitter:
            delay *= random.uniform(0.5, 1.5)
        return delay

    def should_retry(self, attempt: int) -> bool:
        """Check if another retry should be attempted."""
        return attempt < self.max_retries
