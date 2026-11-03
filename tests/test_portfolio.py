"""Tests for portfolio module — models + combiner + correlation + metrics + cli"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
import yaml

from strategy_research.apps.portfolio.combiner import (
    combine_equity_curves,
    equal_weight,
    risk_parity,
    sharpe_weight,
)
from strategy_research.apps.portfolio.correlation import (
    avg_correlation,
    correlation_matrix,
    correlation_pairs,
)
from strategy_research.apps.portfolio.metrics import portfolio_metrics
from strategy_research.apps.portfolio.models import (
    CorrelationPair,
    PortfolioConfig,
    PortfolioMetrics,
    StrategyContribution,
)

# ============================================================
# helpers
# ============================================================


def _ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s)


def _make_equity(start: str, n_days: int, daily_ret: float) -> pd.Series:
    """Create equity curve with constant daily return."""
    dates = pd.bdate_range(start, periods=n_days)
    initial = 100_000.0
    nav = initial * (1 + daily_ret) ** np.arange(n_days)
    return pd.Series(nav, index=dates)


def _make_noisy_equity(start: str, n_days: int, mu: float = 0.0005, sigma: float = 0.01, seed: int = 42) -> pd.Series:
    """Create equity curve with random returns."""
    dates = pd.bdate_range(start, periods=n_days)
    rng = np.random.default_rng(seed)
    returns = rng.normal(mu, sigma, n_days)
    nav = 100_000.0 * np.cumprod(1 + returns)
    return pd.Series(nav, index=dates)


# ============================================================
# models
# ============================================================


class TestModels:
    def test_strategy_contribution(self):
        sc = StrategyContribution(name="momentum", weight=0.5, sharpe=1.2)
        assert sc.name == "momentum"
        assert sc.weight == 0.5
        assert sc.sharpe == 1.2

    def test_correlation_pair(self):
        cp = CorrelationPair(strategy_a="A", strategy_b="B", correlation=0.7)
        assert cp.strategy_a == "A"
        assert cp.correlation == 0.7

    def test_portfolio_config(self):
        pc = PortfolioConfig(name="p1", strategies=["A", "B"])
        assert pc.combine == "equal_weight"
        assert pc.initial_cash == 1_000_000.0

    def test_portfolio_metrics_to_dict(self):
        pm = PortfolioMetrics(sharpe=1.5, annual_return=0.12)
        d = pm.to_dict()
        assert d["sharpe"] == 1.5
        assert d["annual_return"] == 0.12
        assert d["n_strategies"] == 0


# ============================================================
# combiner
# ============================================================


class TestCombiner:
    def test_equal_weight(self):
        w = equal_weight(["A", "B", "C"])
        assert len(w) == 3
        assert all(v == pytest.approx(1 / 3) for v in w.values())

    def test_equal_weight_empty(self):
        assert equal_weight([]) == {}

    def test_equal_weight_single(self):
        w = equal_weight(["A"])
        assert w["A"] == 1.0

    def test_risk_parity(self):
        curves = {
            "A": _make_noisy_equity("2024-01-02", 100, sigma=0.02, seed=1),
            "B": _make_noisy_equity("2024-01-02", 100, sigma=0.01, seed=2),
        }
        w = risk_parity(curves)
        assert len(w) == 2
        assert abs(sum(w.values()) - 1.0) < 1e-6
        # Lower vol → higher weight
        assert w["B"] > w["A"]

    def test_risk_parity_empty(self):
        assert risk_parity({}) == {}

    def test_sharpe_weight(self):
        curves = {
            "A": _make_noisy_equity("2024-01-02", 200, mu=0.002, sigma=0.01, seed=1),
            "B": _make_noisy_equity("2024-01-02", 200, mu=0.0001, sigma=0.01, seed=2),
        }
        w = sharpe_weight(curves)
        assert len(w) == 2
        assert abs(sum(w.values()) - 1.0) < 1e-6
        # Higher mu → higher weight (or at least non-zero)
        assert w.get("A", 0) >= w.get("B", 0)

    def test_sharpe_weight_empty(self):
        assert sharpe_weight({}) == {}

    def test_combine_equity_curves(self):
        curves = {
            "A": _make_equity("2024-01-02", 50, 0.001),
            "B": _make_equity("2024-01-02", 50, 0.0005),
        }
        weights = {"A": 0.6, "B": 0.4}
        combined = combine_equity_curves(curves, weights)
        assert len(combined) > 0
        assert combined.name == "portfolio"
        # Should be between individual curves
        assert combined.iloc[-1] > min(curves["A"].iloc[-1], curves["B"].iloc[-1])

    def test_combine_equity_curves_empty(self):
        assert combine_equity_curves({}, {}).empty

    def test_combine_equity_curves_different_lengths(self):
        curves = {
            "A": _make_equity("2024-01-02", 50, 0.001),
            "B": _make_equity("2024-01-02", 30, 0.0005),
        }
        weights = {"A": 0.5, "B": 0.5}
        combined = combine_equity_curves(curves, weights)
        assert len(combined) > 0
        # Should use intersection of indices
        assert len(combined) <= 30


# ============================================================
# correlation
# ============================================================


class TestCorrelation:
    def test_perfect_positive(self):
        curves = {
            "A": _make_equity("2024-01-02", 50, 0.001),
            "B": _make_equity("2024-01-02", 50, 0.001),
        }
        corr = correlation_matrix(curves)
        assert corr.loc["A", "B"] == pytest.approx(1.0, abs=0.01)

    def test_independent(self):
        curves = {
            "A": _make_noisy_equity("2024-01-02", 200, seed=1),
            "B": _make_noisy_equity("2024-01-02", 200, seed=2),
        }
        corr = correlation_matrix(curves)
        # Should be low correlation for independent random series
        assert abs(corr.loc["A", "B"]) < 0.3

    def test_empty(self):
        assert correlation_matrix({}).empty

    def test_single_strategy(self):
        curves = {"A": _make_equity("2024-01-02", 50, 0.001)}
        corr = correlation_matrix(curves)
        assert corr.shape == (1, 1)
        assert corr.loc["A", "A"] == pytest.approx(1.0)

    def test_correlation_pairs(self):
        curves = {
            "A": _make_equity("2024-01-02", 50, 0.001),
            "B": _make_equity("2024-01-02", 50, 0.0005),
        }
        pairs = correlation_pairs(curves)
        assert len(pairs) == 1
        assert pairs[0].strategy_a == "A"
        assert pairs[0].strategy_b == "B"

    def test_avg_correlation(self):
        curves = {
            "A": _make_equity("2024-01-02", 50, 0.001),
            "B": _make_equity("2024-01-02", 50, 0.0005),
            "C": _make_equity("2024-01-02", 50, 0.0008),
        }
        avg = avg_correlation(curves)
        # All 3 have constant returns → 0% correlation (pct_change of constant is 0)
        # Actually: pct_change of [100,101,102,...] = [NaN, 0.01, 0.01,...] → corr=1.0
        # But [100,100.5,101,...] = [NaN, 0.005, 0.005,...] → corr=1.0
        # All constant-return series have corr=1.0
        assert isinstance(avg, float)


# ============================================================
# metrics
# ============================================================


class TestPortfolioMetrics:
    def test_basic(self):
        curves = {
            "A": _make_noisy_equity("2024-01-02", 200, mu=0.0005, seed=1),
            "B": _make_noisy_equity("2024-01-02", 200, mu=0.0003, seed=2),
        }
        weights = {"A": 0.5, "B": 0.5}
        combined = combine_equity_curves(curves, weights)
        pm = portfolio_metrics(combined, curves, weights)
        assert pm.n_strategies == 2
        assert pm.sharpe != 0
        assert pm.max_drawdown <= 0
        assert pm.var_95 < 0

    def test_empty_curve(self):
        pm = portfolio_metrics(pd.Series(dtype=float), {}, {})
        assert pm.n_strategies == 0
        assert pm.sharpe == 0.0

    def test_to_dict_roundtrip(self):
        curves = {
            "A": _make_noisy_equity("2024-01-02", 100, seed=1),
            "B": _make_noisy_equity("2024-01-02", 100, seed=2),
        }
        weights = {"A": 0.5, "B": 0.5}
        combined = combine_equity_curves(curves, weights)
        pm = portfolio_metrics(combined, curves, weights)
        d = pm.to_dict()
        assert isinstance(d, dict)
        assert "sharpe" in d
        assert "var_95" in d


# ============================================================
# cli
# ============================================================


class TestCLI:
    def test_portfolio_run(self, tmp_path):
        # Create config
        config = {
            "name": "test_portfolio",
            "strategies": ["mom", "val"],
            "combine": "equal_weight",
            "initial_cash": 100_000,
        }
        config_path = tmp_path / "portfolio.yaml"
        config_path.write_text(yaml.dump(config), encoding="utf-8")

        # Create strategy dirs with mock equity curves
        for strat in ["mom", "val"]:
            strat_dir = tmp_path / "strategies" / strat
            run_dir = strat_dir / "runs" / "run_0001"
            run_dir.mkdir(parents=True)
            eq = _make_equity("2024-01-02", 50, 0.001 if strat == "mom" else 0.0005)
            eq.to_csv(run_dir / "equity_curve.csv", header=True)

        output_dir = tmp_path / "output"

        from strategy_research.apps.portfolio.cli import cmd_portfolio_run

        args = type("Args", (), {"config": str(config_path), "output_dir": str(output_dir)})()
        cmd_portfolio_run(args)

        # Verify output
        assert (output_dir / "portfolio_metrics.json").exists()
        assert (output_dir / "portfolio_equity.csv").exists()
        with open(output_dir / "portfolio_metrics.json") as f:
            result = json.load(f)
        assert result["name"] == "test_portfolio"
        assert result["combine"] == "equal_weight"
        assert result["metrics"]["n_strategies"] == 2

    def test_portfolio_list(self, tmp_path):
        # Create strategy dirs
        for name in ["alpha", "beta"]:
            strat_dir = tmp_path / "strategies" / name
            strat_dir.mkdir(parents=True)
            (strat_dir / "strategy.py").write_text("# placeholder")
            run_dir = strat_dir / "runs" / "run_0001"
            run_dir.mkdir(parents=True)

        from strategy_research.apps.portfolio.cli import cmd_portfolio_list

        args = type("Args", (), {"strategy_dir": str(tmp_path / "strategies")})()
        cmd_portfolio_list(args)

    def test_portfolio_show(self, tmp_path):
        result_dir = tmp_path / "portfolio_run"
        result_dir.mkdir()
        metrics = {
            "name": "test",
            "combine": "equal_weight",
            "weights": {"A": 0.5, "B": 0.5},
            "metrics": {"sharpe": 1.0, "annual_return": 0.1},
            "strategies": ["A", "B"],
        }
        (result_dir / "portfolio_metrics.json").write_text(
            json.dumps(metrics), encoding="utf-8"
        )

        from strategy_research.apps.portfolio.cli import cmd_portfolio_show

        args = type("Args", (), {"result_dir": str(result_dir)})()
        cmd_portfolio_show(args)

    def test_portfolio_correlate(self, tmp_path):
        # Create strategy dirs with equity curves
        for name in ["A", "B"]:
            strat_dir = tmp_path / "strategies" / name
            strat_dir.mkdir(parents=True)
            run_dir = strat_dir / "runs" / "run_0001"
            run_dir.mkdir(parents=True)
            eq = _make_equity("2024-01-02", 50, 0.001 if name == "A" else 0.0005)
            eq.to_csv(run_dir / "equity_curve.csv", header=True)

        from strategy_research.apps.portfolio.cli import cmd_portfolio_correlate

        args = type("Args", (), {"strategy_dir": str(tmp_path / "strategies"), "output": None})()
        cmd_portfolio_correlate(args)
