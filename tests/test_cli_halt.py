"""Tests for ``cli.halt`` — kill-switch sentinel."""

from __future__ import annotations

import pytest

from strategy_research.interfaces.cli.halt import (
    HALT,
    HaltError,
    clear_halt,
    is_halted,
    require_not_halted,
    trip_halt,
)

# ─── Trip / clear / is_halted ──────────────────────────────────────────


class TestSentinel:
    def test_initial_state_unhalted(self):
        # After autouse fixture reset
        assert is_halted() is False

    def test_trip_sets_halt(self):
        trip_halt()
        assert is_halted() is True

    def test_clear_resets_halt(self):
        trip_halt()
        clear_halt()
        assert is_halted() is False

    def test_trip_idempotent(self):
        trip_halt()
        trip_halt()
        trip_halt()
        assert is_halted() is True

    def test_clear_when_not_halted_noop(self):
        # Already unhalted → clearing is fine
        clear_halt()
        assert is_halted() is False


# ─── require_not_halted ───────────────────────────────────────────────


class TestRequire:
    def test_unhalted_does_nothing(self):
        require_not_halted(operation="backtest")
        # No exception raised

    def test_halted_raises(self):
        trip_halt()
        with pytest.raises(HaltError, match="aborted"):
            require_not_halted(operation="backtest")

    def test_halted_error_includes_operation_name(self):
        trip_halt()
        with pytest.raises(HaltError, match="specific"):
            require_not_halted(operation="specific operation")


# ─── Module-level constant ────────────────────────────────────────────


class TestModuleConstant:
    def test_HALT_is_boolean(self):
        # HALT is the initial value at import time; subsequent trips don't
        # update this imported binding. Use is_halted() to read live state.
        assert isinstance(HALT, bool)

    def test_HALT_initial_value(self):
        # Default starting state is False.
        assert HALT is False

    def test_live_state_via_is_halted(self):
        clear_halt()
        assert is_halted() is False
        trip_halt()
        assert is_halted() is True
