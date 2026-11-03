"""Tests for core/utils/backtest_engine.py — BacktestCallbacks + run_backtest()."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_research.foundation.utils.backtest_config import BacktestConfig, CostConfig
from strategy_research.foundation.utils.backtest_engine import (
    BacktestCallbacks,
    BacktestResult,
    run_backtest,
)


def make_price_panel(periods=300, codes=("A", "B", "C")) -> pd.DataFrame:
    """构造一个 (T, N) 价格面板."""
    dates = pd.bdate_range("2024-01-01", periods=periods)
    rng = np.random.default_rng(42)
    prices = 100 * (1 + rng.standard_normal((periods, len(codes))) * 0.01).cumprod(axis=0)
    return pd.DataFrame(prices, index=dates, columns=list(codes))


# ============================================================
# 1. BacktestCallbacks 默认实现
# ============================================================

def test_callbacks_compute_signals_must_implement():
    """基类 compute_signals 必须实现."""
    cb = BacktestCallbacks()
    with pytest.raises(NotImplementedError):
        cb.compute_signals(pd.DataFrame(), pd.Timestamp.now(), {}, {})


def test_callbacks_select_assets_default_sorts_desc_and_takes_top():
    cb = BacktestCallbacks()
    signals = {"A": 0.1, "B": 0.5, "C": 0.3, "D": 0.4}
    cfg = BacktestConfig(top_n=2)
    selected = cb.select_assets(signals, cfg)
    # 排序: B=0.5, D=0.4, C=0.3, A=0.1 → top 2: B, D
    assert selected == ["B", "D"]


def test_callbacks_select_assets_with_top_n_greater():
    cb = BacktestCallbacks()
    signals = {"A": 0.1, "B": 0.5}
    cfg = BacktestConfig(top_n=10)  # 比 signals 多
    selected = cb.select_assets(signals, cfg)
    assert set(selected) == set(signals.keys())


def test_callbacks_compute_weights_default_equal_weight():
    cb = BacktestCallbacks()
    weights = cb.compute_weights(["A", "B", "C", "D"], pd.DataFrame(), pd.Timestamp.now(), BacktestConfig())
    assert weights == {"A": 0.25, "B": 0.25, "C": 0.25, "D": 0.25}


def test_callbacks_compute_weights_empty_selected_returns_empty():
    cb = BacktestCallbacks()
    weights = cb.compute_weights([], pd.DataFrame(), pd.Timestamp.now(), BacktestConfig())
    assert weights == {}


def test_callbacks_apply_risk_controls_default_is_pass_through():
    cb = BacktestCallbacks()
    weights = {"A": 0.5, "B": 0.5}
    result = cb.apply_risk_controls(weights, pd.Series(dtype=float), pd.Timestamp.now(), BacktestConfig())
    assert result == weights


def test_callbacks_post_weights_applies_max_weight():
    """post_weights 默认 max_weight + normalize."""
    cb = BacktestCallbacks()
    weights = {"A": 0.5, "B": 0.5}
    cfg = BacktestConfig(max_weight=0.3)
    result = cb.post_weights(weights, cfg)
    # apply_max_weight 截断到 0.3 → {A:0.3, B:0.3}, normalize 缩放 → {A:0.5, B:0.5}
    # 验证总和为 1
    assert sum(result.values()) == pytest.approx(1.0)
    # apply_max_weight 之后 normalize: 两个等权重
    assert result["A"] == pytest.approx(0.5)
    assert result["B"] == pytest.approx(0.5)


def test_callbacks_post_weights_normalizes_unequal():
    """max_weight 截断后 normalize 重新分配."""
    cb = BacktestCallbacks()
    weights = {"A": 0.7, "B": 0.3}
    cfg = BacktestConfig(max_weight=0.4)
    result = cb.post_weights(weights, cfg)
    # apply_max_weight: A→0.4, B→0.3 (B already ≤ 0.4)
    # 但迭代后 B 也会被截到 0.4, 总=0.8; normalize → A=0.5, B=0.5
    assert result["A"] == pytest.approx(0.5)
    assert result["B"] == pytest.approx(0.5)
    assert sum(result.values()) == pytest.approx(1.0)


# ============================================================
# 2. BacktestResult dataclass
# ============================================================

def test_backtest_result_defaults():
    res = BacktestResult(nav_daily=pd.Series([1.0, 1.01]))
    assert res.nav_daily.iloc[0] == 1.0
    assert res.weights_history == []
    assert res.rebalance_dates == []
    assert res.metrics == {}


def test_backtest_result_with_data():
    nav = pd.Series([1.0, 1.01, 1.02], name="nav")
    wh = [(pd.Timestamp("2024-01-15"), {"A": 0.5, "B": 0.5})]
    res = BacktestResult(nav_daily=nav, weights_history=wh)
    assert len(res.weights_history) == 1


# ============================================================
# 3. run_backtest — 完整集成
# ============================================================

class ConstantSignals(BacktestCallbacks):
    """每个调仓日返回固定信号."""

    def __init__(self, signals):
        self.signals = signals
        self.call_count = 0

    def compute_signals(self, price_panel, date, state, context):
        self.call_count += 1
        return dict(self.signals)


def test_run_backtest_basic_returns_nav():
    panel = make_price_panel(periods=300, codes=("A", "B", "C"))
    signals_callback = ConstantSignals({"A": 0.5, "B": 0.3, "C": 0.1})
    cfg = BacktestConfig(min_history=60, top_n=2)

    result = run_backtest(panel, config=cfg, callbacks=signals_callback)

    assert isinstance(result, BacktestResult)
    assert len(result.nav_daily) == 300
    assert result.nav_daily.iloc[0] == 1.0
    # 信号至少被调用过几次
    assert signals_callback.call_count > 0


def test_run_backtest_auto_calculates_daily_returns():
    """daily_returns=None → 自动从 price_panel 计算."""
    panel = make_price_panel(periods=300)
    signals_callback = ConstantSignals({"A": 1.0, "B": 0.0, "C": 0.0})

    result = run_backtest(
        panel,
        config=BacktestConfig(min_history=60, top_n=1),
        callbacks=signals_callback,
    )
    # NAV 从 1.0 开始, 累积日收益
    assert result.nav_daily.iloc[0] == 1.0
    assert result.nav_daily.iloc[-1] != 0


def test_run_backtest_rebalance_dates_recorded():
    panel = make_price_panel(periods=300)
    cfg = BacktestConfig(min_history=60, rebal_freq="M", top_n=1)
    result = run_backtest(
        panel,
        config=cfg,
        callbacks=ConstantSignals({"A": 1.0}),
    )
    # 月度调仓, 约 12-13 个月
    assert len(result.rebalance_dates) >= 1
    assert all(d in panel.index for d in result.rebalance_dates)


def test_run_backtest_weights_history_consistent():
    """weights_history 与 rebalance_dates 对齐."""
    panel = make_price_panel(periods=300)
    cfg = BacktestConfig(min_history=60, top_n=2)
    result = run_backtest(
        panel,
        config=cfg,
        callbacks=ConstantSignals({"A": 0.5, "B": 0.3, "C": 0.1}),
    )
    assert len(result.weights_history) == len(result.rebalance_dates)
    # 每个 weights_history 权重和为 1
    for date, weights in result.weights_history:
        assert sum(weights.values()) == pytest.approx(1.0, abs=1e-6)


def test_run_backtest_with_cost_enabled():
    panel = make_price_panel(periods=300)
    cfg = BacktestConfig(
        min_history=60,
        top_n=1,
        cost=CostConfig(enabled=True, flat_cost_bps=10.0),
    )
    result_no_cost = run_backtest(
        panel,
        config=BacktestConfig(min_history=60, top_n=1),
        callbacks=ConstantSignals({"A": 1.0}),
    )
    result_with_cost = run_backtest(
        panel,
        config=cfg,
        callbacks=ConstantSignals({"A": 1.0}),
    )
    # 有成本时 NAV 终点应略低
    assert result_with_cost.nav_daily.iloc[-1] <= result_no_cost.nav_daily.iloc[-1]


def test_run_backtest_default_config_only():
    """config=None 用 default; callbacks 必须显式传."""
    panel = make_price_panel(periods=300)
    result = run_backtest(
        panel,
        config=None,
        callbacks=ConstantSignals({"A": 1.0}),
    )
    assert isinstance(result, BacktestResult)
    # 默认 min_history=252 → 至少 1-2 个调仓日


def test_run_backtest_includes_metrics():
    """backtest result 包含 metrics dict."""
    panel = make_price_panel(periods=300)
    result = run_backtest(
        panel,
        config=BacktestConfig(min_history=60),
        callbacks=ConstantSignals({"A": 1.0, "B": 0.0}),
    )
    assert isinstance(result.metrics, dict)
    # metrics 可能含多个指标
    assert len(result.metrics) > 0


# ============================================================
# 4. Custom callbacks — 子类化验证
# ============================================================

class AddOneBasisPointCallback(BacktestCallbacks):
    """每个调仓日给所有权重加 0.0001 来测试 on_risk_override."""

    def __init__(self):
        self.captured_weights = None

    def compute_signals(self, price_panel, date, state, context):
        return {c: 1.0 for c in price_panel.columns}

    def post_weights(self, weights, config):
        self.captured_weights = dict(weights)
        return dict(weights)


def test_custom_callback_subclass_post_weights_called():
    panel = make_price_panel(periods=300, codes=("A", "B"))
    cb = AddOneBasisPointCallback()
    run_backtest(
        panel,
        config=BacktestConfig(min_history=60, top_n=1, max_weight=0.6),
        callbacks=cb,
    )
    # 至少被调用一次
    assert cb.captured_weights is not None
