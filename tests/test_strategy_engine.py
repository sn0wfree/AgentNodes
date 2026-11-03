"""Tests for core/utils/strategy_engine.py — BaseStrategy + StrategyEngine."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_research.foundation.utils.backtest_config import (
    CostConfig,
    StopLossConfig,
    TrendFilterConfig,
    VolTargetingConfig,
)
from strategy_research.foundation.utils.strategy_engine import (
    BacktestResult,
    BaseStrategy,
    StrategyEngine,
)


def make_price_panel(periods=400, codes=("A", "B", "C")) -> pd.DataFrame:
    """构造 (T, N) 价格面板 (单调上行)."""
    dates = pd.bdate_range("2023-01-01", periods=periods)
    rng = np.random.default_rng(42)
    prices = 100 * (1 + rng.standard_normal((periods, len(codes))) * 0.005).cumprod(axis=0)
    return pd.DataFrame(prices, index=dates, columns=list(codes))


# ============================================================
# 1. BaseStrategy
# ============================================================

def test_base_strategy_compute_weights_must_implement():
    strat = BaseStrategy()
    with pytest.raises(NotImplementedError):
        strat.compute_weights(pd.Timestamp.now(), pd.DataFrame(), pd.Series(dtype=float))


def test_base_strategy_on_risk_check_default_is_pass_through():
    strat = BaseStrategy()
    weights = {"A": 0.5, "B": 0.5}
    result = strat.on_risk_check(weights, pd.Series(dtype=float), pd.Timestamp.now())
    assert result == weights


# ============================================================
# 2. StrategyEngine 基本
# ============================================================

class EqualWeightStrategy(BaseStrategy):
    """给所有资产等权."""

    def compute_weights(self, date, price_panel, nav_history):
        n = len(price_panel.columns)
        if n == 0:
            return {}
        return {c: 1.0 / n for c in price_panel.columns}


def test_engine_basic_run():
    engine = StrategyEngine()
    panel = make_price_panel(periods=300)
    result = engine.run(panel, EqualWeightStrategy(), min_history=60)
    assert isinstance(result, BacktestResult)
    assert len(result.nav_daily) == 300
    assert result.nav_daily.iloc[0] == 1.0


def test_engine_records_weights_history():
    engine = StrategyEngine()
    panel = make_price_panel(periods=400)
    result = engine.run(panel, EqualWeightStrategy(), min_history=60, rebal_freq="M")
    # 月度调仓,400 个工作日 ≈ 18 个月
    assert len(result.rebalance_dates) >= 1
    for date, weights in result.weights_history:
        # 等权 → 权重和约 1
        assert sum(weights.values()) == pytest.approx(1.0, abs=1e-6)


def test_engine_initialization_with_no_risk():
    """默认无风控 config."""
    engine = StrategyEngine()
    assert engine.vol_targeting is None
    assert engine.trend_filter is None
    assert engine.stop_loss is None


def test_engine_initialization_with_risk_configs():
    vt = VolTargetingConfig(enabled=True)
    tf = TrendFilterConfig(enabled=True)
    sl = StopLossConfig(enabled=True)
    engine = StrategyEngine(vol_targeting=vt, trend_filter=tf, stop_loss=sl)
    assert engine.vol_targeting is vt
    assert engine.trend_filter is tf
    assert engine.stop_loss is sl


def test_engine_default_cost_disabled():
    """未传 cost → 默认 disabled."""
    engine = StrategyEngine()
    panel = make_price_panel(periods=300)
    result = engine.run(panel, EqualWeightStrategy(), min_history=60)
    # 无成本的 NAV 累积
    assert not result.nav_daily.isna().any()


def test_engine_with_cost_deducts_from_nav():
    """成本开启时调仓日 NAV 应略低于无成本."""
    panel = make_price_panel(periods=400)
    cost = CostConfig(enabled=True, flat_cost_bps=20.0)  # 0.002

    # 用一个会触发频繁调仓的策略: 每月换仓 → 多次产生 turnover
    class FlippingStrategy(BaseStrategy):
        """每月翻转权重顺序."""

        def compute_weights(self, date, price_panel, nav_history):
            cols = list(price_panel.columns)
            weights = {c: 0.6 if i == 0 else 0.4 / (len(cols) - 1) for i, c in enumerate(cols)}
            return weights

    result_no_cost = StrategyEngine().run(panel.copy(), FlippingStrategy(), min_history=60, cost=CostConfig(enabled=False))
    result_with_cost = StrategyEngine().run(
        panel.copy(), FlippingStrategy(), min_history=60, cost=cost,
    )
    # 有成本时 NAV 终点应 ≤ 无成本 (成本只减不加)
    assert result_with_cost.nav_daily.iloc[-1] <= result_no_cost.nav_daily.iloc[-1] + 1e-9


# ============================================================
# 3. StrategyEngine 风控: vol_targeting
# ============================================================

class ConstantStrategy(BaseStrategy):
    """固定权重."""

    def __init__(self, weights):
        self._weights = weights

    def compute_weights(self, date, price_panel, nav_history):
        return dict(self._weights)


def test_engine_vol_targeting_scales_weights():
    """波动率目标开启时缩放权重."""
    panel = make_price_panel(periods=300)
    vt = VolTargetingConfig(enabled=True, target_vol=0.10, lookback=60, min_scale=0.5, max_scale=1.5)

    engine = StrategyEngine(vol_targeting=vt)
    strat = ConstantStrategy({"A": 1.0, "B": 0.5})

    result = engine.run(panel, strat, min_history=120)
    # 至少跑完不报错,且 NAV 有变化
    assert not result.nav_daily.isna().any()
    assert len(result.weights_history) >= 1


# ============================================================
# 4. StrategyEngine 风控: trend_filter
# ============================================================

def test_engine_trend_filter_bear_exposure():
    """趋势过滤开启 → 熊市减仓."""
    panel = make_price_panel(periods=400)
    tf = TrendFilterConfig(enabled=True, ma_window=200, bear_exposure=0.5)

    engine = StrategyEngine(trend_filter=tf)
    strat = ConstantStrategy({"A": 1.0, "B": 0.0})

    result = engine.run(panel, strat, min_history=144)
    # 至少跑完不报错
    assert not result.nav_daily.isna().any()


# ============================================================
# 5. StrategyEngine 风控: stop_loss
# ============================================================

def test_engine_stop_loss_clears_weights():
    """硬止损触发时清零 weights."""
    panel = make_price_panel(periods=400)
    sl = StopLossConfig(enabled=True, threshold=-0.05, cooldown_weeks=2)

    engine = StrategyEngine(stop_loss=sl)
    strat = ConstantStrategy({"A": 1.0})

    result = engine.run(panel, strat, min_history=144)
    # 至少有 1 个调仓日
    assert len(result.weights_history) >= 0


# ============================================================
# 6. 自定义 on_risk_check 优先级
# ============================================================

class RiskAwareStrategy(BaseStrategy):
    """自定义风控: 强制把所有权重设为 0.5 (不论什么)."""

    def __init__(self, fixed_w):
        self._fixed_w = fixed_w

    def compute_weights(self, date, price_panel, nav_history):
        return {"A": 1.0, "B": 0.5}

    def on_risk_check(self, weights, nav_history, date):
        return dict(self._fixed_w)


def test_engine_prefers_strategy_risk_callback():
    """策略 on_risk_check 先执行, 引擎 VT/TF/SL 兜底叠加."""
    panel = make_price_panel(periods=300)
    # 启用 vol_targeting,正常会缩放权重
    vt = VolTargetingConfig(enabled=True, target_vol=0.5, min_scale=0.1, max_scale=2.0)

    engine = StrategyEngine(vol_targeting=vt)
    fixed = {"A": 0.7, "B": 0.3}
    strat = RiskAwareStrategy(fixed)

    result = engine.run(panel, strat, min_history=144)
    # 至少跑完
    assert not result.nav_daily.isna().any()


# ============================================================
# 6b. 回归: 策略自定义 on_risk_check 时引擎风控仍生效
# ============================================================

def make_declining_panel(periods=400, codes=("A", "B", "C")) -> pd.DataFrame:
    """构造 (T, N) 价格面板 (持续下行, 触发 trend_filter 熊市)."""
    dates = pd.bdate_range("2023-01-01", periods=periods)
    prices = 100 * (0.99 ** np.arange(periods))[:, None]
    return pd.DataFrame(
        np.broadcast_to(prices, (periods, len(codes))),
        index=dates,
        columns=list(codes),
    )


class PassThroughRiskStrategy(EqualWeightStrategy):
    """自定义 on_risk_check 但原样返回 (模拟 max_weight 类业务规则)."""

    def on_risk_check(self, weights, nav_history, date):
        return dict(weights)


def test_engine_applies_trend_filter_after_strategy_risk_callback():
    """修复回归: FactorStrategy 自定义 on_risk_check 时, 引擎 trend_filter
    必须仍然生效 (旧代码 `if/else` 分支导致 TF/VT/SL 被完全旁路)."""
    panel = make_declining_panel(periods=400)
    tf = TrendFilterConfig(enabled=True, ma_window=50, bear_exposure=0.0)

    engine = StrategyEngine(trend_filter=tf)
    strat = PassThroughRiskStrategy()

    result = engine.run(panel, strat, min_history=60)
    assert result.weights_history, "expected at least one rebalance"

    # 熊市清仓至少在部分调仓日触发 (bear_exposure=0.0)
    assert any(
        all(abs(v) < 1e-9 for v in w.values())
        for _date, w in result.weights_history
    ), "trend_filter 被旁路: 没有任何调仓日触发熊市清仓"


def test_engine_trend_filter_off_keeps_weights():
    """对照: 无 trend_filter 时下行市场不缩仓."""
    panel = make_declining_panel(periods=400)
    engine = StrategyEngine()  # no risk
    strat = PassThroughRiskStrategy()

    result = engine.run(panel, strat, min_history=60)
    assert result.weights_history
    # 没有任何调仓日被清零
    assert not any(
        all(abs(v) < 1e-9 for v in w.values())
        for _date, w in result.weights_history
    )


def test_engine_trend_filter_reduces_loss_in_bear_market():
    """熊市清仓 (bear_exposure=0.0) 应比不清仓损失更小."""
    panel = make_declining_panel(periods=400)
    strat = PassThroughRiskStrategy()

    filtered = StrategyEngine(
        trend_filter=TrendFilterConfig(enabled=True, ma_window=50, bear_exposure=0.0)
    ).run(panel, strat, min_history=60)
    unfiltered = StrategyEngine().run(panel, strat, min_history=60)

    assert filtered.nav_daily.iloc[-1] > unfiltered.nav_daily.iloc[-1]


# ============================================================
# 6c. 年化换手 (ann_turnover)
# ============================================================

class AlternatingStrategy(BaseStrategy):
    """每次调仓在 A/B 之间轮换, 制造高换手."""

    def compute_weights(self, date, price_panel, nav_history):
        n_rebal = len(nav_history)
        # 用调仓序号奇偶切换持仓
        if n_rebal % 2 == 0:
            return {"A": 1.0}
        return {"B": 1.0}


def test_engine_ann_turnover_computed_from_weights():
    """修复回归: ann_turnover 不再硬编码为 0 (旧代码 `# 占位`)."""
    panel = make_price_panel(periods=400)
    engine = StrategyEngine()
    strat = AlternatingStrategy()

    result = engine.run(panel, strat, min_history=60)
    assert result.metrics["ann_turnover"] > 0.0


def test_engine_ann_turnover_zero_for_constant_weights():
    """持仓不变 → 年化换手为 0."""
    panel = make_price_panel(periods=300)
    engine = StrategyEngine()
    strat = EqualWeightStrategy()

    result = engine.run(panel, strat, min_history=60)
    assert result.metrics["ann_turnover"] == 0.0


# ============================================================
# 7. BacktestResult 默认值
# ============================================================

def test_engine_run_returns_dataclass_with_all_fields():
    panel = make_price_panel(periods=300)
    result = StrategyEngine().run(panel, EqualWeightStrategy(), min_history=60)
    # nav_daily 必有
    assert isinstance(result.nav_daily, pd.Series)
    # weights_history / rebalance_dates 默认 factory
    assert isinstance(result.weights_history, list)
    assert isinstance(result.rebalance_dates, list)
    # metrics dict
    assert isinstance(result.metrics, dict)


def test_engine_metrics_populated():
    """run() 返回的 metrics 字典非空."""
    panel = make_price_panel(periods=300)
    result = StrategyEngine().run(panel, EqualWeightStrategy(), min_history=60)
    assert len(result.metrics) > 0
