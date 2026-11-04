"""Tests for core.validation.trade_input + utils (P3-c)."""

from __future__ import annotations

import math
from datetime import datetime

import numpy as np
import pytest

from strategy_research.apps.validation.trade_input import TradeInput
from strategy_research.apps.validation.utils import _json_safe, _sharpe

# ─── TradeInput ──────────────────────────────────────────────────────────


class TestTradeInput:
    def test_basic_creation(self):
        t = TradeInput(
            symbol="AAPL",
            direction=1,
            entry_price=100.0,
            exit_price=105.0,
            entry_time=datetime(2024, 1, 1),
            exit_time=datetime(2024, 2, 1),
            size=10.0,
            pnl=50.0,
            pnl_pct=0.05,
        )
        assert t.symbol == "AAPL"
        assert t.pnl == 50.0
        assert t.exit_reason == "signal"
        assert t.holding_bars == 0

    def test_frozen(self):
        t = TradeInput(
            symbol="x", direction=1,
            entry_price=1.0, exit_price=2.0,
            entry_time=datetime(2024, 1, 1), exit_time=datetime(2024, 1, 2),
            size=1.0, pnl=1.0, pnl_pct=0.01,
        )
        with pytest.raises(Exception):
            t.pnl = 999  # type: ignore[misc]


# ─── _json_safe ─────────────────────────────────────────────────────────


class TestJsonSafe:
    def test_finite_floats_unchanged(self):
        assert _json_safe(1.5) == 1.5
        assert _json_safe(0.0) == 0.0
        assert _json_safe(-1.5) == -1.5

    def test_nan_becomes_none(self):
        assert _json_safe(float("nan")) is None
        assert _json_safe(np.nan) is None
        assert _json_safe(np.float64("nan")) is None

    def test_inf_becomes_none(self):
        assert _json_safe(float("inf")) is None
        assert _json_safe(float("-inf")) is None
        assert _json_safe(np.inf) is None

    def test_dict_recursion(self):
        d = {"a": 1.0, "b": float("nan"), "c": [1.0, float("inf"), 2.0]}
        result = _json_safe(d)
        assert result == {"a": 1.0, "b": None, "c": [1.0, None, 2.0]}

    def test_numpy_types(self):
        assert _json_safe(np.int64(5)) == 5
        assert _json_safe(np.bool_(True)) is True


# ─── _sharpe ────────────────────────────────────────────────────────────


class TestSharpe:
    def test_zero_returns(self):
        r = np.zeros(100)
        assert _sharpe(r, 252) == 0.0

    def test_positive_returns(self):
        r = np.full(100, 0.001)
        # std = 0, so numerator / (0 + 1e-10) * sqrt(252) → very large but finite
        s = _sharpe(r, 252)
        assert s > 0

    def test_mixed_returns(self):
        rng = np.random.default_rng(42)
        r = rng.normal(0.001, 0.01, 252)
        s = _sharpe(r, 252)
        assert math.isfinite(s)
