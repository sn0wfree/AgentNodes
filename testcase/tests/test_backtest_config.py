"""Tests for core/utils/backtest_config.py — 5 个 dataclass + cost_rate()."""
from __future__ import annotations

import dataclasses

import pytest

from agentnodes.foundation.utils.backtest_config import (
    BacktestConfig,
    CostConfig,
    StopLossConfig,
    TrendFilterConfig,
    VolTargetingConfig,
)

# ============================================================
# CostConfig
# ============================================================

def test_cost_config_defaults():
    cfg = CostConfig()
    assert cfg.enabled is False
    assert cfg.commission_bp == 5.0
    assert cfg.slippage_bp == 10.0
    assert cfg.impact_factor == 0.1
    assert cfg.flat_cost_bps is None


def test_cost_config_cost_rate_with_flat():
    """flat_cost_bps 设置时优先用 flat."""
    cfg = CostConfig(flat_cost_bps=20.0)
    expected = 20.0 / 10000  # = 0.002
    assert cfg.cost_rate() == pytest.approx(expected)


def test_cost_config_cost_rate_default():
    """无 flat → (commission + slippage*impact) / 10000."""
    cfg = CostConfig(commission_bp=5.0, slippage_bp=10.0, impact_factor=0.1)
    expected = (5.0 + 10.0 * 0.1) / 10000  # = 0.0006
    assert cfg.cost_rate() == pytest.approx(expected)


def test_cost_config_cost_rate_flat_takes_priority():
    """flat 和其他字段都设置时, flat 优先."""
    cfg = CostConfig(flat_cost_bps=50.0, commission_bp=999.0)
    assert cfg.cost_rate() == pytest.approx(50.0 / 10000)


# ============================================================
# VolTargetingConfig
# ============================================================

def test_vol_targeting_config_defaults():
    cfg = VolTargetingConfig()
    assert cfg.enabled is False
    assert cfg.target_vol == 0.15
    assert cfg.lookback == 60
    assert cfg.min_scale == 0.3
    assert cfg.max_scale == 2.0


def test_vol_targeting_config_custom():
    cfg = VolTargetingConfig(enabled=True, target_vol=0.10, lookback=120, min_scale=0.2)
    assert cfg.enabled is True
    assert cfg.target_vol == 0.10


# ============================================================
# TrendFilterConfig
# ============================================================

def test_trend_filter_config_defaults():
    cfg = TrendFilterConfig()
    assert cfg.enabled is False
    assert cfg.benchmark_col is None
    assert cfg.ma_window == 200
    assert cfg.bear_exposure == 0.5


def test_trend_filter_config_with_benchmark():
    cfg = TrendFilterConfig(enabled=True, benchmark_col="benchmark_close")
    assert cfg.benchmark_col == "benchmark_close"


# ============================================================
# StopLossConfig
# ============================================================

def test_stop_loss_config_defaults():
    cfg = StopLossConfig()
    assert cfg.enabled is False
    assert cfg.threshold == -0.10
    assert cfg.cooldown_weeks == 5


def test_stop_loss_config_custom():
    cfg = StopLossConfig(enabled=True, threshold=-0.05, cooldown_weeks=3)
    assert cfg.enabled is True
    assert cfg.threshold == -0.05


# ============================================================
# BacktestConfig
# ============================================================

def test_backtest_config_defaults():
    cfg = BacktestConfig()
    # 调仓
    assert cfg.rebal_freq == "M"
    assert cfg.min_history == 252
    # 选择
    assert cfg.top_n == 10
    assert cfg.max_weight == 0.25
    # 权重
    assert cfg.weight_method == "inverse_vol"
    assert cfg.vol_window == 60
    assert cfg.vol_floor == 0.01
    # 嵌套 dataclass — 用 default_factory 初始化为独立实例
    assert isinstance(cfg.cost, CostConfig)
    assert isinstance(cfg.vol_targeting, VolTargetingConfig)
    assert isinstance(cfg.trend_filter, TrendFilterConfig)
    assert isinstance(cfg.stop_loss, StopLossConfig)
    # 执行
    assert cfg.execution_lag == 0
    # 输出
    assert cfg.return_detail is False


def test_backtest_config_costs_independent_across_instances():
    """验证 default_factory: 不同实例的 cost dataclass 互不影响."""
    cfg1 = BacktestConfig()
    cfg2 = BacktestConfig()
    cfg1.cost.enabled = True
    assert cfg2.cost.enabled is False


def test_backtest_config_vol_targeting_independent():
    """同上的 vol_targeting 验证."""
    cfg1 = BacktestConfig()
    cfg2 = BacktestConfig()
    cfg1.vol_targeting.enabled = True
    assert cfg2.vol_targeting.enabled is False


def test_backtest_config_can_override_nested():
    cfg = BacktestConfig(
        cost=CostConfig(enabled=True, commission_bp=3.0),
        rebal_freq="W",
        top_n=5,
    )
    assert cfg.rebal_freq == "W"
    assert cfg.top_n == 5
    assert cfg.cost.enabled is True
    assert cfg.cost.commission_bp == 3.0


def test_all_configs_are_dataclass():
    """5 个 config 都是 dataclass."""
    assert dataclasses.is_dataclass(CostConfig)
    assert dataclasses.is_dataclass(VolTargetingConfig)
    assert dataclasses.is_dataclass(TrendFilterConfig)
    assert dataclasses.is_dataclass(StopLossConfig)
    assert dataclasses.is_dataclass(BacktestConfig)
