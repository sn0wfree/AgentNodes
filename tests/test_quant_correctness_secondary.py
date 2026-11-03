"""量化正确性次级项回归:risk-free rate / trades 语义 / 复权配置。

- metrics.py: sharpe/sortino 用超额收益 (ann_return − rf),rf 回传进
  metrics dict;StrategyEngine 与 YAML backtest.risk_free_rate 透传。
- templates/prepare.py: trades = 实际变动持仓数(不是调仓事件数);
  turnover 年化按调仓周期数 (252/rebalance_freq),不再 ×252。
- data_source: tushare 个股 pro_bar(qfq)、yfinance auto_adjust=True
  (显式声明,消除版本默认值歧义)。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = REPO / "templates"


# ── metrics.py 无风险利率 ────────────────────────────────────


def _mostly_up_nav(n: int = 504, daily: float = 0.001) -> pd.Series:
    """逐日上涨、每 20 日一个 -0.5% 下跌日(让 sortino 的下行统计非空)。"""
    rets = np.full(n, daily)
    rets[::20] = -0.005
    idx = pd.bdate_range("2023-01-01", periods=n)
    return pd.Series(np.cumprod(1.0 + rets), index=idx)


class TestRiskFreeRate:
    def test_sharpe_excess_return(self):
        from strategy_research.core.utils.metrics import extended_metrics

        nav = _mostly_up_nav()
        m0 = extended_metrics(nav)
        m3 = extended_metrics(nav, risk_free_rate=0.03)
        vol = m0["ann_vol"]
        expected = (m0["ann_return"] - 0.03) / vol
        assert m3["sharpe"] == pytest.approx(expected)
        assert m3["sharpe"] < m0["sharpe"]
        # rf 回传,验收链可审计用的是哪个利率
        assert m3["risk_free_rate"] == 0.03

    def test_sortino_excess_return(self):
        from strategy_research.core.utils.metrics import _sortino

        nav = _mostly_up_nav()
        s0 = _sortino(nav)
        s3 = _sortino(nav, risk_free_rate=0.03)
        assert s0 > 0  # 存在下行样本,sortino 有定义
        assert s3 < s0

    def test_engine_threads_rf(self):
        from strategy_research.core.utils.strategy_engine import StrategyEngine

        assert StrategyEngine().risk_free_rate == 0.0
        assert StrategyEngine(risk_free_rate=0.05).risk_free_rate == 0.05

    def test_yaml_backtest_rf_wiring(self):
        from strategy_research.apps.config_runner import create_engine

        eng = create_engine({"backtest": {"risk_free_rate": 0.025}})
        assert eng.risk_free_rate == pytest.approx(0.025)
        # 缺省/None 均安全回退 0.0
        assert create_engine({}).risk_free_rate == 0.0
        assert create_engine({"backtest": {"risk_free_rate": None}}).risk_free_rate == 0.0


# ── prepare.py trades 语义 / turnover 年化 ───────────────────


def _load_prepare():
    spec = importlib.util.spec_from_file_location(
        "prepare_trades_semantics_fixture", TEMPLATES / "prepare.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _rotating_factor_prices(n_days: int = 80) -> tuple:
    """因子前段选 AAA、后段选 BBB → 中途一次真实换仓。"""
    idx = pd.bdate_range("2024-01-01", periods=n_days)
    prices = pd.DataFrame({"AAA": 100.0, "BBB": 100.0}, index=idx)
    factor = pd.DataFrame({"AAA": 0.0, "BBB": 0.0}, index=idx)
    factor.loc[: idx[35], "AAA"] = 1.0   # 调仓日 i=20 选 AAA
    factor.loc[idx[36]:, "BBB"] = 1.0    # 调仓日 i=40 起选 BBB
    return prices, factor


class TestTradesSemantics:
    def test_trades_count_real_position_changes(self):
        """trades = 实际变动持仓数: 建仓 1 + 换仓 2 = 3,而非调仓事件数。"""
        prices, factor = _rotating_factor_prices()
        mod = _load_prepare()
        mod.compute_factors = lambda _p, _e: {"rot": factor}

        metrics = mod.evaluate(
            {"top_n": 1, "max_weight": 1.0, "rebalance_freq": 20},
            [{"factor_name": "rot", "factor_code": "x", "weight": 1.0}],
            "equal", {"prices": prices},
        )
        # i=20: 建仓 AAA (1 笔);i=40: 卖 AAA 买 BBB (2 笔);i=60: 无变动 (0)
        assert metrics["trades"] == 3

    def test_turnover_annualized_by_rebalance_periods(self):
        prices, factor = _rotating_factor_prices()
        mod = _load_prepare()
        mod.compute_factors = lambda _p, _e: {"rot": factor}

        metrics = mod.evaluate(
            {"top_n": 1, "max_weight": 1.0, "rebalance_freq": 20},
            [{"factor_name": "rot", "factor_code": "x", "weight": 1.0}],
            "equal", {"prices": prices},
        )
        # weight_changes = [0.5(建仓除2), 1.0(换仓), 0.0] → mean 0.5;
        # 年化 = ×(252/20) = 6.3
        expected = 0.5 * (252 / 20)
        assert metrics["turnover"] == pytest.approx(expected)

    def test_rf_from_params(self):
        """PARAMS.risk_free_rate 进入 sharpe/sortino 超额分子。"""
        idx = pd.bdate_range("2024-01-01", periods=504)
        nav_daily = 0.001
        prices = pd.DataFrame(
            {"AAA": np.cumprod(np.full(len(idx), 1.0 + nav_daily))},
            index=idx,
        )
        mod = _load_prepare()
        factor = pd.DataFrame({"AAA": 1.0}, index=idx)
        mod.compute_factors = lambda _p, _e: {"c": factor}
        exprs = [{"factor_name": "c", "factor_code": "x", "weight": 1.0}]

        m0 = mod.evaluate({"top_n": 1, "max_weight": 1.0,
                           "rebalance_freq": 20},
                          exprs, "equal", {"prices": prices})
        m3 = mod.evaluate({"top_n": 1, "max_weight": 1.0,
                           "rebalance_freq": 20, "risk_free_rate": 0.03},
                          exprs, "equal", {"prices": prices})
        assert m3["sharpe"] < m0["sharpe"]
        assert m3["risk_free_rate"] == 0.03


# ── 数据源复权配置 ────────────────────────────────────────────


class TestAdjustmentWiring:
    def test_yfinance_explicit_auto_adjust(self):
        src = (REPO / "src/strategy_research/core/data_source"
               / "yfinance_loader.py").read_text(encoding="utf-8")
        assert "auto_adjust=True" in src

    def test_tushare_cn_stock_uses_qfq_with_fallback(self):
        src = (REPO / "src/strategy_research/core/data_source"
               / "tushare_loader.py").read_text(encoding="utf-8")
        assert 'adj="qfq"' in src
        assert "api.daily(ts_code=code" in src  # 回退路径保留
