"""Tests for ic_utils.py — IC computation tools."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.foundation.utils.ic_utils import (
    compute_cross_sectional_ic,
    compute_ic_summary,
    compute_time_series_ic,
)


class TestComputeCrossSectionalIC(unittest.TestCase):

    def test_basic_ic(self) -> None:
        T, N, K = 60, 20, 3
        np.random.seed(42)
        X = np.random.randn(T, N, K)
        Y = pd.DataFrame(np.random.randn(T, N))
        ic_list = compute_cross_sectional_ic(X, Y, factor_idx=0)
        self.assertGreater(len(ic_list), 0)
        self.assertTrue(all(-1 <= v <= 1 for v in ic_list))

    def test_min_obs_filter(self) -> None:
        T, N, K = 60, 3, 1
        X = np.random.randn(T, N, K)
        Y = pd.DataFrame(np.random.randn(T, N))
        ic_list = compute_cross_sectional_ic(X, Y, factor_idx=0, min_obs=10)
        self.assertEqual(len(ic_list), 0)


class TestComputeICSummary(unittest.TestCase):

    def test_empty_list(self) -> None:
        summary = compute_ic_summary([])
        self.assertEqual(summary["ic_mean"], 0)
        self.assertEqual(summary["n_obs"], 0)

    def test_basic_summary(self) -> None:
        ic_list = [0.1, 0.2, -0.1, 0.05, 0.15]
        summary = compute_ic_summary(ic_list)
        self.assertAlmostEqual(summary["ic_mean"], 0.08, places=4)
        self.assertGreater(summary["ic_std"], 0)
        self.assertGreater(summary["icir"], 0)
        self.assertGreater(summary["pct_positive"], 0.5)
        self.assertEqual(summary["n_obs"], 5)

    def test_constant_ic(self) -> None:
        ic_list = [0.1, 0.1, 0.1]
        summary = compute_ic_summary(ic_list)
        self.assertAlmostEqual(summary["ic_std"], 0.0, places=10)
        self.assertGreater(summary["icir"], 0)


class TestComputeTimeSeriesIC(unittest.TestCase):

    def test_positive_correlation(self) -> None:
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0])
        y = np.array([2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0, 24.0])
        corr, pval = compute_time_series_ic(x, y)
        self.assertAlmostEqual(corr, 1.0, places=4)
        self.assertLess(pval, 0.05)

    def test_zero_correlation(self) -> None:
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0])
        y = np.array([1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
        corr, _ = compute_time_series_ic(x, y)
        self.assertTrue(np.isnan(corr) or abs(corr) < 0.1)

    def test_few_observations(self) -> None:
        x = np.array([1.0, 2.0, 3.0])
        y = np.array([4.0, 5.0, 6.0])
        corr, pval = compute_time_series_ic(x, y)
        self.assertEqual(corr, 0.0)
        self.assertEqual(pval, 1.0)

    def test_nan_handling(self) -> None:
        x = np.array([1.0, np.nan, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0])
        y = np.array([2.0, 4.0, np.nan, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0, 24.0])
        corr, pval = compute_time_series_ic(x, y)
        self.assertNotEqual(corr, 0.0)


if __name__ == "__main__":
    unittest.main()
