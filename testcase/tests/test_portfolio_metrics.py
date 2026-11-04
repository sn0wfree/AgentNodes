"""Unit tests for portfolio.metrics.portfolio_metrics."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_research.apps.portfolio.metrics import portfolio_metrics
from strategy_research.apps.portfolio.models import PortfolioMetrics


def _curve(values, start="2024-01-01") -> pd.Series:
    idx = pd.date_range(start, periods=len(values), freq="D")
    return pd.Series(values, dtype=float, index=idx)


class TestShortSeries:
    def test_single_point_returns_defaults(self):
        m = portfolio_metrics(_curve([100.0]), {}, {})
        assert isinstance(m, PortfolioMetrics)
        assert m.sharpe == 0.0
        assert m.n_strategies == 0

    def test_empty_series(self):
        m = portfolio_metrics(pd.Series(dtype=float), {}, {})
        assert m.n_strategies == 0

    def test_n_strategies_counts_components(self):
        comps = {"a": _curve([1, 2, 3]), "b": _curve([1, 2, 3])}
        m = portfolio_metrics(_curve([100.0]), comps, {})
        assert m.n_strategies == 2


class TestBasicMetrics:
    def test_uptrend_positive_return(self):
        m = portfolio_metrics(_curve([100, 110, 121]), {}, {})
        assert m.total_return > 0
        assert m.annual_return > 0

    def test_flat_portfolio(self):
        m = portfolio_metrics(_curve([100.0] * 20), {}, {})
        assert m.total_return == pytest.approx(0.0)
        assert m.max_drawdown == pytest.approx(0.0)

    def test_drawdown_negative(self):
        m = portfolio_metrics(_curve([100, 120, 80, 90]), {}, {})
        assert m.max_drawdown < 0

    def test_var_cvar_finite(self):
        rng = np.random.default_rng(0)
        vals = 100 + np.cumsum(rng.normal(0, 1, 50))
        m = portfolio_metrics(_curve(vals), {}, {})
        assert np.isfinite(m.var_95)
        assert np.isfinite(m.cvar_95)
        assert m.cvar_95 <= m.var_95 + 1e-9

    def test_to_dict_rounding(self):
        m = portfolio_metrics(_curve([100, 105, 110]), {}, {})
        d = m.to_dict()
        assert set(d) == {
            "sharpe", "annual_return", "max_drawdown", "total_return",
            "var_95", "cvar_95", "turnover", "diversification_ratio",
            "component_correlation", "n_strategies",
        }


class TestWithComponents:
    def test_diversification_ratio_positive(self):
        a = _curve([100, 101, 102, 103, 104])
        b = _curve([100, 102, 101, 104, 105])
        m = portfolio_metrics(
            _curve([100, 101.5, 101.5, 103.5, 104.5]),
            {"a": a, "b": b},
            {"a": 0.5, "b": 0.5},
        )
        assert m.n_strategies == 2
        assert np.isfinite(m.diversification_ratio)
        assert np.isfinite(m.component_correlation)

    def test_single_component_no_pairwise_corr(self):
        a = _curve([100, 101, 102, 103])
        m = portfolio_metrics(
            _curve([100, 101, 102, 103]), {"a": a}, {"a": 1.0}
        )
        assert m.component_correlation == 0.0

    def test_weight_zero_component_ignored_in_div(self):
        a = _curve([100, 101, 102, 103, 104])
        m = portfolio_metrics(
            _curve([100, 101, 102, 103, 104]),
            {"a": a},
            {"a": 0.0},
        )
        assert np.isfinite(m.diversification_ratio)

    def test_custom_bars_per_year(self):
        m = portfolio_metrics(
            _curve([100, 110, 120, 130]), {}, {}, bars_per_year=12
        )
        assert np.isfinite(m.annual_return)
