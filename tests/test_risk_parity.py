"""Tests for utils/risk_parity.py — risk parity optimization."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.foundation.utils.risk_parity import (
    risk_contribution,
    risk_parity_objective,
    solve_max_diversification,
    solve_risk_parity,
)


class TestRiskContribution(unittest.TestCase):

    def test_equal_weights(self) -> None:
        n = 3
        np.random.seed(42)
        cov = np.random.randn(n, n)
        cov = cov @ cov.T + np.eye(n) * 0.1
        w = np.ones(n) / n
        rc = risk_contribution(w, cov)
        self.assertEqual(len(rc), n)
        self.assertAlmostEqual(rc.sum(), 1.0, places=4)

    def test_zero_variance(self) -> None:
        cov = np.zeros((3, 3))
        w = np.ones(3) / 3
        rc = risk_contribution(w, cov)
        self.assertTrue(np.all(rc == 0.0))


class TestRiskParityObjective(unittest.TestCase):

    def test_perfect_parity(self) -> None:
        n = 3
        cov = np.eye(n) * 2.0 + np.ones((n, n))
        w = np.ones(n) / n
        obj = risk_parity_objective(w, cov)
        self.assertLess(obj, 0.01)

    def test_imbalanced(self) -> None:
        cov = np.array([[1.0, 0.0], [0.0, 100.0]])
        w = np.array([0.9, 0.1])
        obj = risk_parity_objective(w, cov)
        self.assertGreater(obj, 0.001)


class TestSolveRiskParity(unittest.TestCase):

    def test_two_assets(self) -> None:
        np.random.seed(42)
        cov = np.array([[1.0, 0.5], [0.5, 2.0]])
        w = solve_risk_parity(cov)
        self.assertEqual(len(w), 2)
        self.assertAlmostEqual(w.sum(), 1.0, places=4)
        self.assertTrue(np.all(w >= 0))

    def test_three_assets(self) -> None:
        np.random.seed(42)
        n = 3
        cov = np.random.randn(n, n)
        cov = cov @ cov.T + np.eye(n) * 0.5
        w = solve_risk_parity(cov)
        self.assertAlmostEqual(w.sum(), 1.0, places=4)
        self.assertTrue(np.all(w >= 0))

    def test_non_positive_definite(self) -> None:
        cov = np.ones((2, 2))
        with self.assertRaises(ValueError):
            solve_risk_parity(cov)

    def test_non_square_matrix(self) -> None:
        cov = np.ones((2, 3))
        with self.assertRaises(ValueError):
            solve_risk_parity(cov)

    def test_optimization_fallback(self) -> None:
        np.random.seed(42)
        cov = np.array([[1.0, 0.999], [0.999, 1.0]])
        w = solve_risk_parity(cov, max_iter=1, tol=1e-1)
        self.assertAlmostEqual(w.sum(), 1.0, places=4)


class TestSolveMaxDiversification(unittest.TestCase):

    def test_two_assets(self) -> None:
        np.random.seed(42)
        cov = np.array([[1.0, 0.5], [0.5, 2.0]])
        w = solve_max_diversification(cov)
        self.assertEqual(len(w), 2)
        self.assertAlmostEqual(w.sum(), 1.0, places=4)
        self.assertTrue(np.all(w >= 0))

    def test_three_assets(self) -> None:
        np.random.seed(42)
        n = 3
        cov = np.random.randn(n, n)
        cov = cov @ cov.T + np.eye(n) * 0.5
        w = solve_max_diversification(cov)
        self.assertAlmostEqual(w.sum(), 1.0, places=4)
        self.assertTrue(np.all(w >= 0))


if __name__ == "__main__":
    unittest.main()
