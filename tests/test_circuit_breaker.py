"""Tests for CircuitBreaker + RetryPolicy (Phase 10)."""
from __future__ import annotations

import time

from strategy_research.kernel.agent.circuit_breaker import (
    BreakerState,
    CircuitBreakerConfig,
    RetryPolicy,
    ToolLoopCircuitBreaker,
)

# ── ToolLoopCircuitBreaker ──────────────────────────────────────────


class TestCircuitBreakerState:
    """Test 3-state machine transitions."""

    def test_initial_state_is_closed(self):
        cb = ToolLoopCircuitBreaker()
        assert cb.state is BreakerState.CLOSED
        assert not cb.is_open()

    def test_record_success_keeps_closed(self):
        cb = ToolLoopCircuitBreaker()
        cb.record_success("read")
        assert cb.state is BreakerState.CLOSED

    def test_failure_threshold_opens(self):
        cb = ToolLoopCircuitBreaker(
            config=CircuitBreakerConfig(failure_threshold=2, cooldown_seconds=999)
        )
        cb.record_failure("read")
        assert cb.state is BreakerState.CLOSED
        cb.record_failure("read")
        assert cb.state is BreakerState.OPEN
        assert cb.is_open()

    def test_max_total_failures_opens(self):
        cb = ToolLoopCircuitBreaker(
            config=CircuitBreakerConfig(max_total_failures=3, cooldown_seconds=999)
        )
        cb.record_failure("tool_a")
        cb.record_failure("tool_b")
        assert cb.state is BreakerState.CLOSED
        cb.record_failure("tool_c")
        assert cb.state is BreakerState.OPEN

    def test_no_progress_opens(self):
        cb = ToolLoopCircuitBreaker(
            config=CircuitBreakerConfig(no_progress_window=2, cooldown_seconds=999)
        )
        cb.record_no_progress()
        assert cb.state is BreakerState.CLOSED
        cb.record_no_progress()
        assert cb.state is BreakerState.OPEN

    def test_half_open_on_cooldown(self):
        cb = ToolLoopCircuitBreaker(
            config=CircuitBreakerConfig(failure_threshold=1, cooldown_seconds=0.001)
        )
        cb.record_failure("tool")
        assert cb.state is BreakerState.OPEN
        # is_open() should transition to HALF_OPEN after cooldown
        time.sleep(0.005)
        assert not cb.is_open()
        assert cb.state is BreakerState.HALF_OPEN

    def test_half_open_success_closes(self):
        cb = ToolLoopCircuitBreaker(
            config=CircuitBreakerConfig(failure_threshold=1, cooldown_seconds=0.001)
        )
        cb.record_failure("tool")
        time.sleep(0.005)
        cb.is_open()  # triggers half-open
        assert cb.state is BreakerState.HALF_OPEN
        cb.record_success("tool")
        assert cb.state is BreakerState.CLOSED

    def test_half_open_failure_reopens(self):
        cb = ToolLoopCircuitBreaker(
            config=CircuitBreakerConfig(failure_threshold=1, cooldown_seconds=0.001)
        )
        cb.record_failure("tool")
        time.sleep(0.005)
        cb.is_open()  # triggers half-open
        cb.record_failure("tool")
        assert cb.state is BreakerState.OPEN

    def test_reset(self):
        cb = ToolLoopCircuitBreaker(
            config=CircuitBreakerConfig(failure_threshold=1, cooldown_seconds=999)
        )
        cb.record_failure("tool")
        assert cb.state is BreakerState.OPEN
        cb.reset()
        assert cb.state is BreakerState.CLOSED
        assert not cb.is_open()

    def test_to_dict(self):
        cb = ToolLoopCircuitBreaker()
        d = cb.to_dict()
        assert d["state"] == "closed"
        assert d["total_failures"] == 0
        assert d["tool_failures"] == {}


# ── RetryPolicy ─────────────────────────────────────────────────────


class TestRetryPolicy:
    """Test exponential backoff with jitter."""

    def test_default_max_retries(self):
        policy = RetryPolicy()
        assert policy.max_retries == 3
        assert policy.should_retry(0)
        assert policy.should_retry(1)
        assert policy.should_retry(2)
        assert not policy.should_retry(3)

    def test_delay_increases_exponentially(self):
        policy = RetryPolicy(jitter=False)
        d1 = policy.get_delay(1)
        d2 = policy.get_delay(2)
        d3 = policy.get_delay(3)
        assert d1 == 1.0
        assert d2 == 2.0
        assert d3 == 4.0

    def test_delay_capped(self):
        policy = RetryPolicy(base_delay=1.0, max_delay=3.0, jitter=False)
        d3 = policy.get_delay(3)
        assert d3 == 3.0  # 2^2=4 → capped to 3

    def test_jitter_applied(self):
        policy = RetryPolicy(jitter=True)
        delays = [policy.get_delay(1) for _ in range(10)]
        # With jitter, at least one should differ from 1.0
        assert any(d != 1.0 for d in delays)

    def test_should_retry(self):
        policy = RetryPolicy(max_retries=2)
        assert policy.should_retry(0)
        assert policy.should_retry(1)
        assert not policy.should_retry(2)
        assert not policy.should_retry(5)
