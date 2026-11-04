"""Unit tests for compute_factor expression evaluation (parser path).

Covers evaluate_expression / parse_expression / arithmetic / nesting /
error contracts. Operator math itself lives in test_operators.py;
analyze_complexity lives in test_factor_registry_discipline.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_research.apps.compute_factor import (
    FactorComputeError,
    compute_factor,
    evaluate_expression,
    get_available_operators,
    parse_expression,
)


@pytest.fixture
def prices() -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=30, freq="B")
    rng = np.random.default_rng(7)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, len(idx))), index=idx)
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series(rng.integers(1_000, 10_000, len(idx)), index=idx, dtype=float)
    return pd.DataFrame({"close": close, "open": open_, "volume": volume})


class TestParseExpression:
    def test_simple_call(self):
        op, args = parse_expression("ts_return(close, 20)")
        assert op == "ts_return"
        assert args == ["close", "20"]

    def test_nested_call_keeps_inner(self):
        op, args = parse_expression("rank(ts_return(close, 20))")
        assert op == "rank"
        assert args == ["ts_return(close, 20)"]

    def test_multi_args(self):
        op, args = parse_expression("ts_corr(close, volume, 10)")
        assert op == "ts_corr"
        assert args == ["close", "volume", "10"]

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_expression("close")

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            parse_expression("")


class TestEvaluateSimple:
    def test_function_call(self, prices):
        out = evaluate_expression("ts_return(close, 5)", prices)
        assert isinstance(out, pd.Series)
        assert len(out) == len(prices)

    def test_arithmetic_division(self, prices):
        out = evaluate_expression("close / ts_mean(close, 5)", prices)
        assert isinstance(out, pd.Series)

    def test_parentheses(self, prices):
        out = evaluate_expression("(close - open) / (close + open)", prices)
        assert isinstance(out, pd.Series)

    def test_unary_minus(self, prices):
        out = evaluate_expression("-ts_return(close, 1)", prices)
        assert isinstance(out, pd.Series)

    def test_nested_operator(self, prices):
        out = evaluate_expression("ts_std(ts_return(close, 1), 5)", prices)
        assert isinstance(out, pd.Series)

    def test_integer_literal(self, prices):
        out = evaluate_expression("ts_mean(close, 10)", prices)
        expected = prices["close"].rolling(10).mean()
        assert out.equals(expected) or np.allclose(out.dropna(), expected.dropna())

    def test_power_operator(self, prices):
        out = evaluate_expression("ts_mean(close, 3) ** 2", prices)
        assert isinstance(out, pd.Series)


class TestEvaluateErrors:
    def test_unknown_operator(self, prices):
        with pytest.raises((ValueError, FactorComputeError)):
            evaluate_expression("not_an_op(close)", prices)

    def test_unknown_column(self, prices):
        with pytest.raises((ValueError, FactorComputeError, KeyError)):
            evaluate_expression("ts_return(no_such_col, 5)", prices)

    def test_empty_expr(self, prices):
        with pytest.raises((ValueError, FactorComputeError)):
            evaluate_expression("", prices)

    def test_garbage_expr(self, prices):
        with pytest.raises((ValueError, FactorComputeError)):
            evaluate_expression("###???", prices)


class TestComputeFactor:
    def test_returns_series(self, prices):
        out = compute_factor("ts_return(close, 5)", prices, factor_name="mom5")
        assert isinstance(out, pd.Series)

    def test_factor_name_accepted(self, prices):
        out = compute_factor("rank(close)", prices, factor_name="r")
        assert out is not None


class TestOperatorRegistry:
    def test_get_available_operators_nonempty(self):
        ops = get_available_operators()
        assert isinstance(ops, list)
        assert len(ops) > 10
        assert "ts_return" in ops

    def test_sorted_or_stable(self):
        ops = get_available_operators()
        assert ops == sorted(ops) or len(set(ops)) == len(ops)
