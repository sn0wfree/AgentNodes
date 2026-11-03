"""utils/metrics.py 单元测试 — 17 个业绩指标.

覆盖 extended_metrics 公共 API + 内部辅助函数:
- _ann_return / _ann_vol / _sharpe / _sortino / _calmar
- _max_drawdown / _info_ratio / _downside_dev
- _var_cvar / _win_rate / _profit_loss_ratio
- _max_monthly_loss / _profit_months_ratio / _avg_dd
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import pytest

from strategy_research.foundation.utils import metrics

warnings.filterwarnings("ignore")


@pytest.fixture(scope="module")
def nav():
    """构造 NAV 序列 (含上涨 + 下跌 + 横盘)."""
    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2024-01-01", periods=252)
    rets = rng.normal(0.0005, 0.01, 252)
    nav_values = (1 + pd.Series(rets, index=dates)).cumprod() * 100
    return nav_values


@pytest.fixture(scope="module")
def nav_uptrending():
    """上行 NAV (随机漫步 + 微正漂移)."""
    rng = np.random.default_rng(123)
    dates = pd.bdate_range("2024-01-01", periods=252)
    rets = rng.normal(0.001, 0.005, 252)
    return (1 + pd.Series(rets, index=dates)).cumprod() * 100


@pytest.fixture(scope="module")
def nav_downtrending():
    """下行 NAV (持续下跌)."""
    dates = pd.bdate_range("2024-01-01", periods=252)
    rets = np.full(252, -0.005)
    rets[::10] = 0.001  # 偶尔反弹
    return (1 + pd.Series(rets, index=dates)).cumprod() * 100


@pytest.fixture(scope="module")
def benchmark_nav():
    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2024-01-01", periods=252)
    rets = rng.normal(0.0003, 0.01, 252)
    return (1 + pd.Series(rets, index=dates)).cumprod() * 100


# ============================================================
# _ann_return
# ============================================================

def test_ann_return_positive(nav_uptrending):
    """上行 NAV 年化收益应为正."""
    r = metrics._ann_return(nav_uptrending)
    assert r > 0


def test_ann_return_negative(nav_downtrending):
    """下行 NAV 年化收益应为负."""
    r = metrics._ann_return(nav_downtrending)
    assert r < 0


def test_ann_return_empty():
    """空 NAV 应返回 0."""
    assert metrics._ann_return(pd.Series(dtype=float)) == 0.0


def test_ann_return_single_value():
    """单值 NAV 应返回 0."""
    s = pd.Series([100.0])
    assert metrics._ann_return(s) == 0.0


# ============================================================
# _ann_vol
# ============================================================

def test_ann_vol_positive(nav):
    """年化波动率应 > 0."""
    v = metrics._ann_vol(nav)
    assert v > 0


def test_ann_vol_constant_zero():
    """常数 NAV 波动率应为 0."""
    s = pd.Series([100.0] * 100, index=pd.bdate_range("2024-01-01", periods=100))
    v = metrics._ann_vol(s)
    assert v == 0.0


# ============================================================
# _sharpe / _sortino
# ============================================================

def test_sharpe_positive_uptrend(nav_uptrending):
    """上行 NAV 的 Sharpe 应为正."""
    s = metrics._sharpe(nav_uptrending)
    assert s > 0


def test_sharpe_zero_vol_returns_zero(nav_uptrending):
    """波动率 = 0 时 Sharpe 应返回 0 (避免除零)."""
    s = pd.Series([100.0] * 100, index=pd.bdate_range("2024-01-01", periods=100))
    assert metrics._sharpe(s) == 0.0


def test_sortino_basic(nav):
    """Sortino 应该是有限数值."""
    s = metrics._sortino(nav)
    assert np.isfinite(s)


def test_sortino_no_downside_returns_zero(nav_uptrending):
    """下行收益为空时 Sortino 返回 0."""
    # 构造只上行序列
    s = pd.Series(np.linspace(100, 200, 100), index=pd.bdate_range("2024-01-01", periods=100))
    r = metrics._sortino(s)
    assert r == 0.0


# ============================================================
# _max_drawdown
# ============================================================

def test_max_drawdown_negative(nav):
    """max_drawdown 应返回负值或 0."""
    md, duration = metrics._max_drawdown(nav)
    assert md <= 0


def test_max_drawdown_monotonic_up():
    """单调上行应有 dd = 0, duration = 0."""
    s = pd.Series(np.linspace(100, 200, 100), index=pd.bdate_range("2024-01-01", periods=100))
    md, duration = metrics._max_drawdown(s)
    assert md == 0
    assert duration == 0


def test_max_drawdown_constant():
    """常数序列 md=0."""
    s = pd.Series([100.0] * 50)
    md, duration = metrics._max_drawdown(s)
    assert md == 0


def test_max_drawdown_duration(nav_downtrending):
    """持续下行时长应计入 duration."""
    md, duration = metrics._max_drawdown(nav_downtrending)
    assert duration > 0


# ============================================================
# _calmar
# ============================================================

def test_calmar_basic(nav):
    """Calmar 是有限数值."""
    c = metrics._calmar(nav)
    assert np.isfinite(c)


def test_calmar_monotonic_up_returns_zero():
    """dd < 0 时 calmar = ann_return / |dd|, 但若 ann_return 也 ≈ 0 → 0."""
    s = pd.Series(np.linspace(100, 110, 100), index=pd.bdate_range("2024-01-01", periods=100))
    c = metrics._calmar(s)
    # 单调上行, dd = 0 (因为不跌破前高), 应返回 0
    assert c == 0.0


# ============================================================
# _info_ratio
# ============================================================

def test_info_ratio_with_benchmark(nav, benchmark_nav):
    """有 benchmark 时返回有限数."""
    ir = metrics._info_ratio(nav, benchmark_nav)
    assert np.isfinite(ir)


def test_info_ratio_no_benchmark(nav):
    """无 benchmark 时返回 0."""
    assert metrics._info_ratio(nav, None) == 0.0
    assert metrics._info_ratio(nav, pd.Series(dtype=float)) == 0.0


def test_info_ratio_identical_with_benchmark(nav, benchmark_nav):
    """nav == benchmark 时 IR = 0."""
    ir = metrics._info_ratio(nav, nav)
    assert abs(ir) < 0.1


# ============================================================
# _downside_dev
# ============================================================

def test_downside_dev_basic(nav):
    """下行偏差应 >= 0."""
    dd = metrics._downside_dev(nav)
    assert dd >= 0


def test_downside_dev_monotonic_up_returns_zero():
    """单调上行序列下行偏差 = 0."""
    s = pd.Series(np.linspace(100, 200, 100), index=pd.bdate_range("2024-01-01", periods=100))
    assert metrics._downside_dev(s) == 0.0


# ============================================================
# _var_cvar
# ============================================================

def test_var_cvar_returns_tuple(nav):
    """应返回 (var, cvar) 元组."""
    v, cv = metrics._var_cvar(nav)
    assert isinstance(v, float)
    assert isinstance(cv, float)


def test_var_cvar_negative(nav):
    """VaR 应 <= 0."""
    v, cv = metrics._var_cvar(nav)
    assert v <= 0


def test_var_cvar_cvar_worse_than_var(nav):
    """CVaR 应 <= VaR (CVaR 是更坏情况)."""
    v, cv = metrics._var_cvar(nav)
    assert cv <= v


def test_var_cvar_empty():
    """空 Series 应返回 (0, 0)."""
    v, cv = metrics._var_cvar(pd.Series(dtype=float))
    assert (v, cv) == (0.0, 0.0)


def test_var_cvar_alpha_param(nav):
    """alpha 参数应改变 VaR 值."""
    v5, _ = metrics._var_cvar(nav, alpha=0.05)
    v10, _ = metrics._var_cvar(nav, alpha=0.10)
    # alpha 越大 (10% 比 5% 阈值宽松), VaR 应更接近 0
    assert v10 >= v5, f"VaR(10%) should be larger than VaR(5%): {v10} vs {v5}"


# ============================================================
# _win_rate / _profit_loss_ratio
# ============================================================

def test_win_rate_in_range(nav):
    """win_rate 应在 [0, 1]."""
    wr = metrics._win_rate(nav)
    assert 0 <= wr <= 1


def test_win_rate_monotonic_up():
    """单调上行 win_rate = 1.0."""
    s = pd.Series(np.linspace(100, 200, 100), index=pd.bdate_range("2024-01-01", periods=100))
    assert metrics._win_rate(s) == 1.0


def test_profit_loss_ratio_positive(nav):
    """盈亏比应 > 0."""
    plr = metrics._profit_loss_ratio(nav)
    assert plr >= 0


def test_profit_loss_ratio_no_losses_returns_zero():
    """无亏损序列 → 0."""
    s = pd.Series(np.linspace(100, 200, 100), index=pd.bdate_range("2024-01-01", periods=100))
    assert metrics._profit_loss_ratio(s) == 0.0


def test_profit_loss_ratio_no_wins_returns_zero():
    """无盈利序列 → 0."""
    s = pd.Series(np.linspace(200, 100, 100), index=pd.bdate_range("2024-01-01", periods=100))
    assert metrics._profit_loss_ratio(s) == 0.0


# ============================================================
# _max_monthly_loss / _profit_months_ratio
# ============================================================

def test_max_monthly_loss_negative(nav):
    """max_monthly_loss 应 <= 0."""
    assert metrics._max_monthly_loss(nav) <= 0


def test_profit_months_ratio_range(nav):
    """profit_months_ratio 应在 [0, 1]."""
    pmr = metrics._profit_months_ratio(nav)
    assert 0 <= pmr <= 1


def test_max_monthly_loss_monotonic_up_returns_zero():
    """单调上行 NAV: 无亏损月, 应返回 0 (修复后行为)."""
    s = pd.Series(np.linspace(100, 200, 252), index=pd.bdate_range("2020-01-01", periods=252))
    assert metrics._max_monthly_loss(s) == 0.0


# ============================================================
# _avg_dd
# ============================================================

def test_avg_dd_negative_or_zero(nav):
    """平均回撤应 <= 0 (或 0 当无回撤)."""
    avg = metrics._avg_dd(nav)
    assert avg <= 0


def test_avg_dd_monotonic_up_zero():
    """单调上行应 = 0."""
    s = pd.Series(np.linspace(100, 200, 100), index=pd.bdate_range("2024-01-01", periods=100))
    assert metrics._avg_dd(s) == 0.0


# ============================================================
# extended_metrics (公共 API)
# ============================================================

def test_extended_metrics_returns_dict(nav):
    """返回 dict 应有所有指标键 (含 ann_turnover 占位 + risk_free_rate 回传)."""
    result = metrics.extended_metrics(nav)
    assert isinstance(result, dict)
    expected_keys = {
        "ann_return", "ann_vol", "sharpe", "max_drawdown",
        "calmar", "sortino", "downside_dev", "info_ratio",
        "win_rate", "profit_loss_ratio", "max_dd_duration",
        "calmar_avg_dd", "var_95", "cvar_95", "ann_turnover",
        "max_monthly_loss", "profit_months_ratio",
        "risk_free_rate",
    }
    assert set(result.keys()) == expected_keys


def test_extended_metrics_with_benchmark(nav, benchmark_nav):
    """带 benchmark 应正常计算并填充 info_ratio."""
    result = metrics.extended_metrics(nav, benchmark_nav)
    assert isinstance(result["info_ratio"], float)


def test_extended_metrics_empty():
    """空 NAV 返回空 dict."""
    result = metrics.extended_metrics(pd.Series(dtype=float))
    assert result == {}


def test_extended_metrics_short():
    """单点 NAV 返回空 dict."""
    result = metrics.extended_metrics(pd.Series([100.0]))
    assert result == {}


def test_extended_metrics_ann_turnover_zero(nav):
    """ann_turnover 暂时是占位 0."""
    result = metrics.extended_metrics(nav)
    assert result["ann_turnover"] == 0.0


def test_extended_metrics_values_in_ranges(nav):
    """所有指标应在合理范围内."""
    result = metrics.extended_metrics(nav)

    assert 0 <= result["win_rate"] <= 1
    assert 0 <= result["profit_months_ratio"] <= 1
    assert result["max_drawdown"] <= 0
    assert result["var_95"] <= 0
    assert result["cvar_95"] <= 0
    assert result["ann_vol"] >= 0
    assert result["downside_dev"] >= 0
    assert result["info_ratio"] >= 0 or np.isnan(result["info_ratio"])  # IR can be negative if underperforming


def test_extended_metrics_rebalance_dates_ignored(nav):
    """rebalance_dates 参数不影响结果 (占位)."""
    r1 = metrics.extended_metrics(nav)
    r2 = metrics.extended_metrics(nav, rebalance_dates=[10, 20, 30])
    # 除 ann_turnover 外应一致
    for k in r1:
        if k != "ann_turnover":
            assert r1[k] == r2[k], f"{k} differs"


# ============================================================
# 鲁棒性: 各种边界输入
# ============================================================

def test_empty_nav_always_zero():
    """空 Series, 所有指标应返回 0 而不崩溃."""
    empty = pd.Series(dtype=float)
    assert metrics._ann_return(empty) == 0.0
    assert metrics._ann_vol(empty) == 0.0
    assert metrics._sharpe(empty) == 0.0
    assert metrics._sortino(empty) == 0.0
    assert metrics._downside_dev(empty) == 0.0
    assert metrics._win_rate(empty) == 0.0
    assert metrics._profit_loss_ratio(empty) == 0.0


def test_single_value_nav_always_zero():
    """单值 NAV, 所有指标应返回 0."""
    s = pd.Series([100.0])
    assert metrics._ann_return(s) == 0.0
    assert metrics._ann_vol(s) == 0.0
    assert metrics._sharpe(s) == 0.0


def test_nan_in_nav_handled():
    """NAV 含 NaN 应不崩溃."""
    s = pd.Series([100.0, np.nan, 102.0, 105.0, np.nan, 110.0])
    # 不应崩溃
    try:
        r = metrics._ann_return(s)
        # 返回 float 或 0
        assert np.isfinite(r) or r == 0.0
    except Exception:
        pytest.fail("Should not raise on NaN NAV")
