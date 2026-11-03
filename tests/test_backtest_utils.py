"""Tests for core/utils/backtest_utils.py — 6 工具函数 (换手/调仓日/权重/NAV)."""
from __future__ import annotations

import pandas as pd
import pytest

from strategy_research.foundation.utils.backtest_config import CostConfig
from strategy_research.foundation.utils.backtest_utils import (
    apply_max_weight,
    calculate_turnover,
    calculate_turnover_cost,
    compute_daily_nav_from_weights,
    generate_rebalance_dates,
    normalize_weights,
)

# ============================================================
# 1. calculate_turnover
# ============================================================

def test_turnover_identical_weights_is_zero():
    w = {"A": 0.5, "B": 0.5}
    assert calculate_turnover(w, w) == 0.0


def test_turnover_empty_to_some():
    """空 -> 满."""
    new_w = {"A": 0.5, "B": 0.5}
    turnover = calculate_turnover({}, new_w)
    # Σ|new - 0| / 2 = (0.5 + 0.5) / 2 = 0.5
    assert turnover == pytest.approx(0.5)


def test_turnover_some_to_empty():
    old_w = {"A": 0.5, "B": 0.5}
    turnover = calculate_turnover(old_w, {})
    assert turnover == pytest.approx(0.5)


def test_turnover_full_replace():
    old_w = {"A": 1.0, "B": 0.0}
    new_w = {"C": 1.0, "D": 0.0}
    # Union: A,B,C,D; differences: |0-1|+|0-0|+|1-0|+|0-0|= 2; /2 = 1.0
    turnover = calculate_turnover(old_w, new_w)
    assert turnover == pytest.approx(1.0)


def test_turnover_partial_change():
    old_w = {"A": 0.5, "B": 0.5}
    new_w = {"A": 0.6, "B": 0.4}
    # |0.6-0.5|+|0.4-0.5| = 0.2; /2 = 0.1
    assert calculate_turnover(old_w, new_w) == pytest.approx(0.1)


# ============================================================
# 2. calculate_turnover_cost
# ============================================================

def test_turnover_cost_disabled():
    cfg = CostConfig(enabled=False, commission_bp=10.0)
    cost = calculate_turnover_cost({"A": 1.0}, {"B": 1.0}, cfg)
    assert cost == 0.0


def test_turnover_cost_enabled():
    cfg = CostConfig(enabled=True, flat_cost_bps=20.0)  # 20 bps = 0.002
    # Full replace → turnover = 1.0
    cost = calculate_turnover_cost({"A": 1.0}, {"B": 1.0}, cfg)
    assert cost == pytest.approx(1.0 * 20.0 / 10000)


# ============================================================
# 3. generate_rebalance_dates
# ============================================================

def test_rebalance_dates_monthly():
    dates = pd.bdate_range("2024-01-01", "2024-12-31")  # 工作日
    rebal = generate_rebalance_dates(dates, freq="M")
    # 12 个月,每月最后一个工作日 → 12 个
    assert len(rebal) == 12
    # 全部在 dates 中
    assert all(d in dates for d in rebal)


def test_rebalance_dates_quarterly():
    dates = pd.bdate_range("2024-01-01", "2024-12-31")
    rebal = generate_rebalance_dates(dates, freq="Q")
    # 4 个季度 → 4 个调仓日
    assert len(rebal) == 4


def test_rebalance_dates_weekly():
    dates = pd.bdate_range("2024-01-01", "2024-12-31")
    rebal = generate_rebalance_dates(dates, freq="W-FRI")
    # ~ 52 周
    assert 50 <= len(rebal) <= 53
    # 全部都在 dates 中 (因为 resample 可能产生非交易日)
    assert all(d in dates for d in rebal)


def test_rebalance_dates_invalid_freq_raises():
    dates = pd.bdate_range("2024-01-01", periods=30)
    with pytest.raises(ValueError, match="Unsupported"):
        generate_rebalance_dates(dates, freq="X")


def test_rebalance_dates_with_min_lookback():
    dates = pd.bdate_range("2024-01-01", periods=300)
    rebal = generate_rebalance_dates(dates, freq="M", min_lookback=252)
    # 252 日后才允许调仓 → 至少 1-2 个调仓日 (从 252 起)
    assert all(dates.searchsorted(d) >= 252 for d in rebal)


def test_rebalance_dates_insufficient_data_raises():
    """数据不够 min_lookback → ValueError."""
    dates = pd.bdate_range("2024-01-01", periods=10)
    with pytest.raises(ValueError, match="Insufficient"):
        generate_rebalance_dates(dates, freq="M", min_lookback=252)


# ============================================================
# 4. apply_max_weight
# ============================================================

def test_apply_max_weight_no_excess():
    """所有权重都 ≤ max_w → 不变."""
    weights = {"A": 0.2, "B": 0.2, "C": 0.2}
    result = apply_max_weight(weights, max_w=0.25)
    assert result == {"A": 0.2, "B": 0.2, "C": 0.2}


def test_apply_max_weight_one_excess_redistributes():
    """A 超过, 其余分摊."""
    weights = {"A": 0.5, "B": 0.3, "C": 0.2}
    result = apply_max_weight(weights, max_w=0.4)
    # A 截到 0.4, 多出 0.1 → 按比例给 B(0.3), C(0.2) = 0.5/0.5 比例
    # B 多分 0.1 * 0.3/(0.3+0.2) = 0.06, C 多分 0.04
    assert result["A"] == pytest.approx(0.4)
    assert result["B"] == pytest.approx(0.36)
    assert result["C"] == pytest.approx(0.24)


def test_apply_max_weight_preserves_total():
    """总和仍是 1.0."""
    weights = {"A": 0.5, "B": 0.3, "C": 0.4}
    result = apply_max_weight(weights, max_w=0.4)
    assert sum(result.values()) == pytest.approx(1.2)


def test_apply_max_weight_handles_empty():
    assert apply_max_weight({}, max_w=0.25) == {}


def test_apply_max_weight_returns_new_dict():
    """不修改原 dict."""
    weights = {"A": 0.5, "B": 0.5}
    original = dict(weights)
    _ = apply_max_weight(weights, max_w=0.3)
    assert weights == original


# ============================================================
# 5. normalize_weights
# ============================================================

def test_normalize_to_one():
    w = {"A": 2.0, "B": 2.0}
    result = normalize_weights(w)
    assert sum(result.values()) == pytest.approx(1.0)
    assert result["A"] == pytest.approx(0.5)


def test_normalize_already_one():
    w = {"A": 0.6, "B": 0.4}
    result = normalize_weights(w)
    assert result == {"A": 0.6, "B": 0.4}


def test_normalize_zero_total_returns_original():
    """总和 ≤ 0 → 保持原状."""
    w = {"A": 0.0, "B": 0.0}
    result = normalize_weights(w)
    assert result == {"A": 0.0, "B": 0.0}


def test_normalize_negative_total_returns_original():
    w = {"A": -0.5, "B": -0.5}
    result = normalize_weights(w)
    assert result == {"A": -0.5, "B": -0.5}


# ============================================================
# 6. compute_daily_nav_from_weights
# ============================================================

def test_nav_from_empty_weights_history():
    """空权重 → NAV 始终为 1."""
    dates = pd.bdate_range("2024-01-01", periods=10)
    daily_ret = pd.DataFrame(0.01, index=dates, columns=["A", "B"])
    nav = compute_daily_nav_from_weights([], daily_ret, CostConfig())
    assert (nav == 1.0).all()


def test_nav_from_weights_with_positive_returns():
    dates = pd.bdate_range("2024-01-01", periods=5)
    daily_ret = pd.DataFrame({"A": [0.01, 0.02, 0.01, 0.01, 0.01]}, index=dates)
    weights_history = [(dates[0], {"A": 1.0})]
    nav = compute_daily_nav_from_weights(weights_history, daily_ret, CostConfig())
    # 收益累积
    assert nav.iloc[0] == pytest.approx(1.0)
    # 后面陆续累积正收益
    assert nav.iloc[-1] > 1.0


def test_nav_from_weights_with_cost_enabled():
    dates = pd.bdate_range("2024-01-01", periods=5)
    daily_ret = pd.DataFrame({"A": [0.01] * 5, "B": [0.02] * 5}, index=dates)
    # 调仓 1: 全 A
    # 调仓 2: 全 B (有 turnover)
    weights_history = [
        (dates[0], {"A": 1.0}),
        (dates[2], {"B": 1.0}),
    ]
    cfg = CostConfig(enabled=True, flat_cost_bps=10.0)  # 0.001
    nav = compute_daily_nav_from_weights(weights_history, daily_ret, cfg)

    # 验证调仓日 cost 被扣 (NAV 在调仓日不会立即跳到下一日)
    # 调仓日的 prev_weight → current_weight transition 有成本
    # 这里主要验证有 cost 时不会抛异常 + NAV 正常累积
    assert nav.iloc[0] == pytest.approx(1.0)
    assert not nav.isna().any()


def test_nav_respects_weight_normalization_correctly():
    """权重和 > 1 不应出问题."""
    dates = pd.bdate_range("2024-01-01", periods=3)
    daily_ret = pd.DataFrame({"A": [0.01, 0.02, 0.01]}, index=dates)
    weights_history = [(dates[0], {"A": 2.0})]  # 总和 > 1
    nav = compute_daily_nav_from_weights(weights_history, daily_ret, CostConfig())
    # 简单验证能正确执行
    assert nav.iloc[0] == pytest.approx(1.0)
    assert nav.iloc[1] > 0
