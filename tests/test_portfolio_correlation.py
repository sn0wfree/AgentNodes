"""Unit tests for portfolio.correlation — strategy correlation helpers."""
from __future__ import annotations

import pandas as pd
import pytest

from strategy_research.apps.portfolio.correlation import (
    avg_correlation,
    correlation_matrix,
    correlation_pairs,
)


def _curve(values, start="2024-01-01") -> pd.Series:
    idx = pd.date_range(start, periods=len(values), freq="D")
    return pd.Series(values, dtype=float, index=idx)


class TestCorrelationMatrix:
    def test_empty_input(self):
        assert correlation_matrix({}).empty

    def test_single_strategy_self_corr_is_one(self):
        out = correlation_matrix({"a": _curve([100, 101, 102, 103])})
        assert list(out.index) == ["a"]
        assert float(out.loc["a", "a"]) == pytest.approx(1.0)

    def test_too_short_curves(self):
        out = correlation_matrix({"a": _curve([1.0])})
        assert out.empty

    def test_perfectly_correlated(self):
        a = _curve([100, 101, 102, 103, 104, 105])
        b = _curve([200, 202, 204, 206, 208, 210])
        out = correlation_matrix({"a": a, "b": b})
        assert out.loc["a", "b"] == pytest.approx(1.0)

    def test_perfectly_anticorrelated(self):
        a = _curve([100, 101, 100, 101, 100, 101])
        b = _curve([100, 99, 100, 99, 100, 99])
        out = correlation_matrix({"a": a, "b": b})
        assert out.loc["a", "b"] == pytest.approx(-1.0)

    def test_shape_and_labels(self):
        curves = {
            "x": _curve([1, 2, 3, 4, 5]),
            "y": _curve([2, 2, 3, 5, 5]),
        }
        out = correlation_matrix(curves)
        assert list(out.index) == ["x", "y"]
        assert list(out.columns) == ["x", "y"]


class TestCorrelationPairs:
    def test_empty(self):
        assert correlation_pairs({}) == []

    def test_single_returns_empty(self):
        assert correlation_pairs({"a": _curve([1, 2, 3, 4])}) == []

    def test_pair_count_n_choose_2(self):
        curves = {
            "a": _curve([100, 101, 103, 104, 106]),
            "b": _curve([100, 102, 102, 105, 105]),
            "c": _curve([50, 51, 50, 52, 53]),
        }
        pairs = correlation_pairs(curves)
        assert len(pairs) == 3
        names = {p.strategy_a for p in pairs} | {p.strategy_b for p in pairs}
        assert names == {"a", "b", "c"}

    def test_no_self_pairs(self):
        curves = {"a": _curve([1, 2, 3, 4, 5]), "b": _curve([5, 4, 5, 6, 5])}
        for p in correlation_pairs(curves):
            assert p.strategy_a != p.strategy_b


class TestAvgCorrelation:
    def test_empty_is_zero(self):
        assert avg_correlation({}) == 0.0

    def test_single_is_zero(self):
        assert avg_correlation({"a": _curve([1, 2, 3, 4])}) == 0.0

    def test_two_identical_is_one(self):
        a = _curve([100, 102, 104, 106, 108])
        assert avg_correlation({"a": a, "b": a * 2}) == pytest.approx(1.0)

    def test_mean_of_pairs(self):
        curves = {
            "a": _curve([100, 101, 102, 103, 104, 105]),
            "b": _curve([100, 101, 102, 103, 104, 105]),
            "c": _curve([100, 99, 100, 99, 100, 99]),
        }
        val = avg_correlation(curves)
        pairs = correlation_pairs(curves)
        assert val == pytest.approx(sum(p.correlation for p in pairs) / len(pairs))
