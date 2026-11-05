"""P0-2 F — unified BacktestResult tests.

Covers: canonical dataclass in apps/backtest_models.py, both legacy
import paths (``utils.backtest_engine`` and ``utils.strategy_engine``)
still work and resolve to the same class with the unified 5-field
shape.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd

from strategy_research.apps.backtest_models import BacktestResult


class TestCanonicalBacktestResult:
    def test_default_fields(self):
        nav = pd.Series([1.0, 1.05, 1.1])
        r = BacktestResult(nav_daily=nav)
        assert r.nav_daily.equals(nav)
        assert r.weights_history == []
        assert r.rebalance_dates == []
        assert r.metrics == {}
        assert r.factor_failures == []

    def test_fields_assignable(self):
        r = BacktestResult(
            nav_daily=pd.Series([1.0]),
            weights_history=[(pd.Timestamp("2024-01-01"), {"A": 0.5, "B": 0.5})],
            rebalance_dates=[pd.Timestamp("2024-01-01")],
            metrics={"sharpe": 1.5},
            factor_failures=[{"factor": "mom_20", "asset": "XSHG_001", "error": "NaN"}],
        )
        assert r.metrics["sharpe"] == 1.5
        assert r.factor_failures[0]["factor"] == "mom_20"


class TestLegacyAliases:
    def test_backtest_engine_path_resolves_to_canonical(self):
        from strategy_research.foundation.utils.backtest_engine import (
            BacktestResult as Legacy,
        )
        assert Legacy is BacktestResult

    def test_strategy_engine_path_resolves_to_canonical(self):
        from strategy_research.foundation.utils.strategy_engine import (
            BacktestResult as Legacy,
        )
        assert Legacy is BacktestResult

    def test_all_three_import_paths_are_the_same_class(self):
        from strategy_research.foundation.utils.backtest_engine import (
            BacktestResult as A,
        )
        from strategy_research.foundation.utils.strategy_engine import (
            BacktestResult as B,
        )
        assert A is B is BacktestResult

    def test_legacy_alias_preserves_factor_failures_field(self):
        """Both legacy import paths must expose ``factor_failures``."""
        from strategy_research.foundation.utils.backtest_engine import (
            BacktestResult as CallbackEngine,
        )
        from strategy_research.foundation.utils.strategy_engine import (
            BacktestResult as StrategyEngine,
        )
        # Building via either path produces a dataclass with the new
        # field available (default empty).
        r1 = CallbackEngine(nav_daily=pd.Series([1.0]))
        r2 = StrategyEngine(nav_daily=pd.Series([1.0]))
        assert hasattr(r1, "factor_failures")
        assert hasattr(r2, "factor_failures")
        assert r1.factor_failures == []
        assert r2.factor_failures == []
