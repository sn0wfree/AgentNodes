"""Tests for config_runner.py on_risk_check import path.

Regression test for the bug where ``from .backtest_utils import`` failed
because the module actually lives at ``strategy_research.core.utils.backtest_utils``.
"""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pytest


class TestConfigRunnerImport:
    """Verify the on_risk_check import path is correct."""

    def test_on_risk_check_imports_backtest_utils(self):
        """The on_risk_check method should resolve backtest_utils without error."""
        from strategy_research.apps.config_runner import FactorStrategy

        inst = FactorStrategy.__new__(FactorStrategy)
        inst.params = {"max_weight": 0.25}

        # This must not raise ModuleNotFoundError
        weights = {"A": 0.4, "B": 0.4, "C": 0.4}  # over max_weight
        nav = pd.Series([1.0, 1.1, 1.05])
        date = pd.Timestamp("2024-01-01")

        result = inst.on_risk_check(weights, nav, date)
        assert isinstance(result, dict)
        # After apply_max_weight + normalize, sum should be 1.0
        assert abs(sum(result.values()) - 1.0) < 1e-9

    def test_no_legacy_backtest_utils_module(self):
        """Confirm there is no module at strategy_research.core.backtest_utils
        (so the original import would have failed).
        """
        import importlib

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("strategy_research.core.backtest_utils")

    def test_correct_module_path_exists(self):
        """The actual module path is strategy_research.core.utils.backtest_utils."""
        from strategy_research.core.utils import backtest_utils

        assert hasattr(backtest_utils, "apply_max_weight")
        assert hasattr(backtest_utils, "normalize_weights")


# ============================================================
# run_from_yaml with expression factors on price_data
# ============================================================


def _write_price_data_fixture(
    workspace: Path,
    strategy_name: str = "e2e_strat",
    stagger: int = 0,
    codes: list[str] | None = None,
) -> None:
    """Seed DuckDB ``price_data`` (asset_code column) like commit_market_data does.

    ``stagger``: number of leading trading days to drop for the first code,
    simulating assets that list at different dates (real-world production
    data has such leading-NaN gaps in the wide panel).
    """
    import numpy as np

    from strategy_research.core.db import save_ohlcv_to_db

    codes = codes or ["000001.SZ", "600519.SH", "300015.SZ"]
    dates = pd.bdate_range("2023-01-02", periods=300)
    rng = np.random.default_rng(7)
    data_map: dict[str, pd.DataFrame] = {}
    for i, code in enumerate(codes):
        start = stagger if i == 0 else 0
        sub_dates = dates[start:]
        if i == 0 and stagger:
            # Deterministic strong uptrend so the momentum factor ranks this
            # asset first under top_n=1 (random walk would be drowned out by
            # 2% daily noise). Its leading-NaN column must then be selected.
            close = 100 * np.cumprod(np.full(len(sub_dates), 1.01))
        else:
            close = 100 * np.cumprod(
                1 + rng.normal(0.0005, 0.02, len(sub_dates))
            )
        df = pd.DataFrame(
            {
                "open": close * 0.99,
                "high": close * 1.02,
                "low": close * 0.98,
                "close": close,
                "volume": 1_000_000 + rng.integers(0, 500_000, len(sub_dates)),
            },
            index=pd.DatetimeIndex(sub_dates, name="trade_date"),
        )
        data_map[code] = df
    save_ohlcv_to_db(workspace, data_map, strategy_name)


class TestRunFromYamlPriceDataRegression:
    """Regression: run_from_yaml with expression factors must read OHLCV from
    the ``price_data`` table (``asset_code`` column).

    Previously ``long_to_wide_ohlcv_per_asset(long_ohlcv)`` used the default
    ``asset_col="asset"`` and failed with::

        long format requires columns [date, asset, ...]; got ['date', 'asset_code', ...]
    """

    @pytest.fixture
    def strategy_workspace(self, tmp_path: Path) -> Path:
        ws = tmp_path / "ws"
        ws.mkdir()
        _write_price_data_fixture(ws)
        sdir = ws / "strategies" / "e2e_strat"
        sdir.mkdir(parents=True)
        (sdir / "config.yaml").write_text("""strategy:
  name: e2e_strat
  type: rotation
data:
  source: duckdb
rebalance:
  freq: M
  min_history: 60
factors:
  - name: momentum_20d
    code: ts_return(close, 20)
    weight: 1.0
""")
        return ws

    def test_run_from_yaml_with_expression_factor(self, strategy_workspace: Path):
        from strategy_research.apps.config_runner import run_from_yaml

        result = run_from_yaml(
            strategy_workspace / "strategies" / "e2e_strat" / "config.yaml",
            strategy_workspace,
        )
        assert not result.nav_daily.empty
        assert "ann_return" in result.metrics

    def test_compute_weights_loads_price_data(self, strategy_workspace: Path):
        from strategy_research.apps.config_runner import (
            create_engine,
            create_strategy,
            load_data,
        )

        cfg_yaml = strategy_workspace / "strategies" / "e2e_strat" / "config.yaml"
        from strategy_research.apps.config_runner import load_yaml_config

        cfg = load_yaml_config(cfg_yaml)
        strategy = create_strategy(cfg, workspace_path=strategy_workspace)
        engine = create_engine(cfg)
        data = load_data(cfg, strategy_workspace)
        assert not data.empty
        result = engine.run(
            data / data.iloc[0], strategy=strategy, rebal_freq="M", min_history=60
        )
        assert "ann_return" in result.metrics

    def test_run_from_yaml_with_staggered_start_dates(self, tmp_path: Path):
        """Regression: assets listing on different dates (leading NaN gap in
        the wide panel) must not poison normalization via ``data.iloc[0]``.

        Previously ``data / data.iloc[0]`` divided every column by the very
        first row's value; a column whose asset started later got NaN on the
        first row → the whole column became NaN → inverse_vol weights came
        out NaN → nav stayed flat at 1.0 (ann_return 0).
        """
        from strategy_research.apps.config_runner import run_from_yaml

        ws = tmp_path / "ws"
        ws.mkdir()
        # Two assets from day 1, one (the top-return asset) listing 10 days
        # later: with top_n=1 the later-listed asset is selected, so a
        # leading-NaN normalization bug must turn its weights NaN and
        # flatten the nav.
        _write_price_data_fixture(ws, stagger=10)
        sdir = ws / "strategies" / "e2e_strat"
        sdir.mkdir(parents=True)
        (sdir / "config.yaml").write_text("""strategy:
  name: e2e_strat
  type: rotation
data:
  source: duckdb
rebalance:
  freq: M
  min_history: 60
top_n: 1
max_weight: 1.0
factors:
  - name: momentum_20d
    code: ts_return(close, 20)
    weight: 1.0
""")
        result = run_from_yaml(
            sdir / "config.yaml",
            ws,
        )
        assert not result.nav_daily.empty
        last_nav = float(result.nav_daily.iloc[-1])
        assert math.isfinite(last_nav), (
            f"nav became NaN (normalization poisoned by leading NaN): "
            f"{result.nav_daily.tail(3).to_dict()}"
        )
        assert last_nav != 1.0, "nav stayed flat at 1.0"
        ann_return = float(result.metrics.get("ann_return", 0.0))
        assert math.isfinite(ann_return), f"metrics={result.metrics}"


class TestAssetFilteringAndNaNDefenses:
    """Commit 1 root-cause fixes:
    - orphan assets (not in config.codes) never enter the panel / OHLCV load
    - ghost assets (1-row NaN history) cannot poison scores/weights/nav
    - runtime factor failures are collected on the strategy instance
    """

    def _make_workspace(self, tmp_path, config_text: str, codes, strategy="e2e_strat"):
        import numpy as np

        from strategy_research.core.db import save_ohlcv_to_db

        ws = tmp_path / "ws"
        ws.mkdir()
        dates = pd.bdate_range("2023-01-02", periods=200)
        rng = np.random.default_rng(3)
        data_map: dict[str, pd.DataFrame] = {}
        for code in codes:
            close = 100 * np.cumprod(1 + rng.normal(0.0005, 0.015, len(dates)))
            data_map[code] = pd.DataFrame(
                {
                    "open": close * 0.99,
                    "high": close * 1.02,
                    "low": close * 0.98,
                    "close": close,
                    "volume": 1_000_000,
                },
                index=pd.DatetimeIndex(dates, name="trade_date"),
            )
        # ghost asset: a single row of NaN data (historical residue)
        ghost = pd.DataFrame(
            {"open": [float("nan")], "high": [float("nan")], "low": [float("nan")],
             "close": [float("nan")], "volume": [0.0]},
            index=pd.DatetimeIndex([dates[0]], name="trade_date"),
        )
        data_map["999999.XX"] = ghost
        save_ohlcv_to_db(ws, data_map, strategy)

        sdir = ws / "strategies" / strategy
        sdir.mkdir(parents=True)
        (sdir / "config.yaml").write_text(config_text)
        return ws

    def test_orphan_assets_dropped_from_panel(self, tmp_path):
        cfg_text = """strategy:
  name: e2e_strat
  type: rotation
data:
  source: duckdb
  codes: ['000001.SZ', '600519.SH']
rebalance:
  freq: M
  min_history: 60
factors:
  - name: momentum_20d
    code: ts_return(close, 20)
    weight: 1.0
"""
        from strategy_research.apps.config_runner import load_data, load_yaml_config

        ws = self._make_workspace(tmp_path, cfg_text, ["000001.SZ", "600519.SH"])
        cfg = load_yaml_config(ws / "strategies" / "e2e_strat" / "config.yaml")
        data = load_data(cfg, ws)
        # ghost 999999.XX + third seeded code must be gone; only the 2 config codes remain
        assert list(data.columns) == ["000001.SZ", "600519.SH"]

    def test_ghost_asset_no_nan_weights_and_failures_collected(self, tmp_path):
        cfg_text = """strategy:
  name: e2e_strat
  type: rotation
data:
  source: duckdb
  codes: ['000001.SZ', '600519.SH']
rebalance:
  freq: M
  min_history: 60
factors:
  - name: momentum_20d
    code: ts_return(close, 20)
    weight: 1.0
"""
        from strategy_research.apps.config_runner import run_from_yaml

        ws = self._make_workspace(tmp_path, cfg_text, ["000001.SZ", "600519.SH"])
        result = run_from_yaml(
            ws / "strategies" / "e2e_strat" / "config.yaml", ws
        )
        nav = result.nav_daily
        assert int(nav.isna().sum()) == 0, "nav must not contain NaN"
        last_nav = float(nav.iloc[-1])
        assert last_nav != 1.0, "nav stayed flat — weights never applied"
        # factor failures: ghost asset is not in codes anymore → no failures
        assert result.factor_failures == []

    def test_factor_failures_collected_when_asset_has_nan_history(self, tmp_path):
        cfg_text = """strategy:
  name: e2e_strat
  type: rotation
data:
  source: duckdb
  codes: ['000001.SZ', '999999.XX']
rebalance:
  freq: M
  min_history: 60
factors:
  - name: momentum_20d
    code: ts_return(close, 20)
    weight: 1.0
"""
        from strategy_research.apps.config_runner import run_from_yaml

        ws = self._make_workspace(tmp_path, cfg_text, ["000001.SZ"])
        result = run_from_yaml(
            ws / "strategies" / "e2e_strat" / "config.yaml", ws
        )
        # ghost asset IS in codes here → factor on it fails → collected
        assert result.factor_failures, "expected factor failures to be collected"
        rec = result.factor_failures[0]
        assert rec["factor"] == "momentum_20d"
        assert rec["asset"] == "999999.XX"
        assert rec["occurrences"] >= 1
        assert "数据不足" in rec["error"] or "无法解析" in rec["error"]

    def test_all_scores_invalid_returns_empty_weights(self, tmp_path):
        """Every factor fails on every asset → scores empty → empty weights
        (engine keeps nav flat) instead of selecting score=0 ghosts."""
        from strategy_research.apps.config_runner import run_from_yaml

        cfg_text = """strategy:
  name: e2e_strat
  type: rotation
data:
  source: duckdb
  codes: ['000001.SZ', '600519.SH']
rebalance:
  freq: M
  min_history: 60
factors:
  - name: bad_factor
    code: unknown_op(close, 20)
    weight: 1.0
"""
        ws = self._make_workspace(tmp_path, cfg_text, ["000001.SZ", "600519.SH"])
        result = run_from_yaml(
            ws / "strategies" / "e2e_strat" / "config.yaml", ws
        )
        assert int(result.nav_daily.isna().sum()) == 0
        assert result.factor_failures, "bad factor must be recorded"
        assert any(rec["factor"] == "bad_factor" for rec in result.factor_failures)
