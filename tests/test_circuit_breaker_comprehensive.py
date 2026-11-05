"""Circuit Breaker comprehensive tests — edge cases, per-tool reset, session_id."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentnodes.kernel.agent.circuit_breaker import (
    BreakerState,
    CircuitBreakerConfig,
    RetryPolicy,
    ToolLoopCircuitBreaker,
)


# ── ToolLoopCircuitBreaker ───────────────────────────────────────


class TestCircuitBreakerAdvanced:
    def test_initial_state_closed(self):
        cb = ToolLoopCircuitBreaker()
        assert cb.state == BreakerState.CLOSED

    def test_record_failure_increments_per_tool(self):
        cb = ToolLoopCircuitBreaker()
        cb.record_failure("tool_a")
        cb.record_failure("tool_a")
        assert cb._tool_failures["tool_a"] == 2

    def test_record_failure_increments_total(self):
        cb = ToolLoopCircuitBreaker()
        cb.record_failure("tool_a")
        cb.record_failure("tool_b")
        assert cb._total_failures == 2

    def test_record_success_resets_per_tool_counter(self):
        cb = ToolLoopCircuitBreaker()
        cb.record_failure("tool_a")
        cb.record_failure("tool_a")
        cb.record_success("tool_a")
        assert cb._tool_failures.get("tool_a", 0) == 0

    def test_per_tool_threshold_opens_breaker(self):
        config = CircuitBreakerConfig(failure_threshold=3)
        cb = ToolLoopCircuitBreaker(config=config)
        for _ in range(3):
            cb.record_failure("tool_a")
        assert cb.state == BreakerState.OPEN

    def test_no_progress_threshold_opens_breaker(self):
        config = CircuitBreakerConfig(no_progress_window=3)
        cb = ToolLoopCircuitBreaker(config=config)
        for _ in range(3):
            cb.record_no_progress()
        assert cb.state == BreakerState.OPEN

    def test_max_total_failures_opens_breaker(self):
        config = CircuitBreakerConfig(max_total_failures=5)
        cb = ToolLoopCircuitBreaker(config=config)
        for i in range(5):
            cb.record_failure(f"tool_{i}")
        assert cb.state == BreakerState.OPEN

    def test_is_open_returns_false_when_closed(self):
        cb = ToolLoopCircuitBreaker()
        assert cb.is_open() is False

    def test_is_open_returns_true_when_open(self):
        config = CircuitBreakerConfig(failure_threshold=1, cooldown_seconds=999)
        cb = ToolLoopCircuitBreaker(config=config)
        cb.record_failure("tool_a")
        assert cb.state == BreakerState.OPEN
        assert cb.is_open() is True

    def test_cooldown_transitions_to_half_open(self):
        config = CircuitBreakerConfig(failure_threshold=1, cooldown_seconds=0.001)
        cb = ToolLoopCircuitBreaker(config=config)
        cb.record_failure("tool_a")
        assert cb.state == BreakerState.OPEN
        time.sleep(0.01)
        assert cb.is_open() is False
        assert cb.state == BreakerState.HALF_OPEN

    def test_half_open_success_closes(self):
        config = CircuitBreakerConfig(failure_threshold=1, cooldown_seconds=0.001)
        cb = ToolLoopCircuitBreaker(config=config)
        cb.record_failure("tool_a")
        time.sleep(0.01)
        cb.is_open()  # transition to HALF_OPEN
        cb.record_success("tool_a")
        assert cb.state == BreakerState.CLOSED
        assert cb._total_failures == 0
        assert cb._no_progress_count == 0

    def test_half_open_failure_reopens(self):
        config = CircuitBreakerConfig(failure_threshold=1, cooldown_seconds=0.001)
        cb = ToolLoopCircuitBreaker(config=config)
        cb.record_failure("tool_a")
        time.sleep(0.01)
        cb.is_open()  # transition to HALF_OPEN
        cb.record_failure("tool_b")
        assert cb.state == BreakerState.OPEN

    def test_reset(self):
        cb = ToolLoopCircuitBreaker()
        cb.record_failure("tool_a")
        cb.record_no_progress()
        cb.reset()
        assert cb.state == BreakerState.CLOSED
        assert cb._total_failures == 0
        assert cb._no_progress_count == 0

    def test_to_dict(self):
        cb = ToolLoopCircuitBreaker()
        cb.record_failure("tool_a")
        d = cb.to_dict()
        assert "state" in d
        assert "tool_failures" in d
        assert "total_failures" in d
        assert d["total_failures"] == 1

    def test_session_id_stored(self):
        cb = ToolLoopCircuitBreaker(session_id="my-session")
        assert cb.session_id == "my-session"

    def test_mixed_tool_failures(self):
        """Different tools contribute to total failures."""
        config = CircuitBreakerConfig(max_total_failures=5)
        cb = ToolLoopCircuitBreaker(config=config)
        for i in range(5):
            cb.record_failure(f"tool_{i}")
        assert cb._total_failures == 5
        assert cb.state == BreakerState.OPEN


# ── RetryPolicy ──────────────────────────────────────────────────


class TestRetryPolicyAdvanced:
    def test_get_delay_no_jitter(self):
        rp = RetryPolicy(base_delay=1.0, jitter=False)
        assert rp.get_delay(0) == 0.5
        assert rp.get_delay(1) == 1.0
        assert rp.get_delay(2) == 2.0

    def test_get_delay_max_cap(self):
        rp = RetryPolicy(base_delay=1.0, max_delay=5.0, jitter=False)
        assert rp.get_delay(10) == 5.0

    def test_should_retry_within_limit(self):
        rp = RetryPolicy(max_retries=3)
        assert rp.should_retry(0) is True
        assert rp.should_retry(1) is True
        assert rp.should_retry(2) is True

    def test_should_retry_exceeds_limit(self):
        rp = RetryPolicy(max_retries=3)
        assert rp.should_retry(3) is False
        assert rp.should_retry(10) is False

    def test_get_delay_with_jitter(self):
        rp = RetryPolicy(base_delay=1.0, jitter=True)
        delay = rp.get_delay(0)
        assert 0.0 <= delay <= 2.0
