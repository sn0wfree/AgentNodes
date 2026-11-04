"""Tests for combiner.py — portfolio weight calculation."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.apps.portfolio.combiner import (
    combine_equity_curves,
    equal_weight,
    risk_parity,
    sharpe_weight,
)


class TestEqualWeight(unittest.TestCase):

    def test_two_assets(self) -> None:
        result = equal_weight(["a", "b"])
        self.assertEqual(result, {"a": 0.5, "b": 0.5})

    def test_three_assets(self) -> None:
        result = equal_weight(["a", "b", "c"])
        self.assertAlmostEqual(result["a"], 1.0 / 3)
        self.assertAlmostEqual(sum(result.values()), 1.0)

    def test_empty(self) -> None:
        self.assertEqual(equal_weight([]), {})

    def test_single_asset(self) -> None:
        self.assertEqual(equal_weight(["a"]), {"a": 1.0})


class TestRiskParity(unittest.TestCase):

    def test_equal_volatility(self) -> None:
        dates = pd.date_range("2020-01-01", periods=100)
        curves = {
            "a": pd.Series(np.random.randn(100).cumsum() + 100, index=dates),
            "b": pd.Series(np.random.randn(100).cumsum() + 100, index=dates),
        }
        weights = risk_parity(curves)
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=4)
        self.assertEqual(set(weights.keys()), {"a", "b"})

    def test_empty(self) -> None:
        self.assertEqual(risk_parity({}), {})

    def test_single_asset(self) -> None:
        dates = pd.date_range("2020-01-01", periods=10)
        curves = {"a": pd.Series(np.random.randn(10).cumsum() + 100, index=dates)}
        weights = risk_parity(curves)
        self.assertAlmostEqual(weights["a"], 1.0)


class TestSharpeWeight(unittest.TestCase):

    def test_positive_sharpes(self) -> None:
        dates = pd.date_range("2020-01-01", periods=252)
        a = pd.Series(np.random.randn(252) * 0.01 + 0.001, index=dates).cumsum() + 100
        b = pd.Series(np.random.randn(252) * 0.01 + 0.0005, index=dates).cumsum() + 100
        curves = {"a": a, "b": b}
        weights = sharpe_weight(curves)
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=4)

    def test_empty(self) -> None:
        self.assertEqual(sharpe_weight({}), {})

    def test_negative_sharpes_fallback_to_equal(self) -> None:
        dates = pd.date_range("2020-01-01", periods=10)
        curves = {
            "a": pd.Series(np.arange(10.0) * -1 + 100, index=dates),
            "b": pd.Series(np.arange(10.0) * -1 + 100, index=dates),
        }
        weights = sharpe_weight(curves)
        self.assertAlmostEqual(weights["a"], 0.5)


class TestCombineEquityCurves(unittest.TestCase):

    def test_basic_combination(self) -> None:
        dates = pd.date_range("2020-01-01", periods=50)
        curves = {
            "a": pd.Series(np.arange(50.0) + 100, index=dates),
            "b": pd.Series(np.arange(50.0) + 100, index=dates),
        }
        weights = {"a": 0.6, "b": 0.4}
        combined = combine_equity_curves(curves, weights)
        self.assertIsNotNone(combined)
        self.assertGreater(len(combined), 0)

    def test_empty_curves(self) -> None:
        combined = combine_equity_curves({}, {})
        self.assertTrue(combined.empty)

    def test_single_curve(self) -> None:
        dates = pd.date_range("2020-01-01", periods=10)
        curves = {"a": pd.Series(np.arange(10.0) + 100, index=dates)}
        combined = combine_equity_curves(curves, {"a": 1.0})
        self.assertAlmostEqual(combined.iloc[-1], 100.0 * (1 + 9 / 100), places=2)


if __name__ == "__main__":
    unittest.main()
