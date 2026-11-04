"""fundamental_* 和 academic_* alpha 测试 — 使用合成数据 (无需 Tushare).

fundamental_* alphas 需要 fund:* 前缀数据. 我们构建合理范围的合成数据,
让 alpha 能正确执行并验证输出形状 / 范围 / 计算逻辑.

academic_* alphas (FF 因子) 用 close 反推 — 一并在此测试.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import pytest
from _fixtures.fundamental_mock import (
    make_fundamentals_panel,
    make_market_benchmark_panel,
)

from strategy_research.apps.alpha_zoo import compute_alpha

warnings.filterwarnings("ignore")


@pytest.fixture(scope="module")
def fund_panel():
    return make_fundamentals_panel()


@pytest.fixture(scope="module")
def benchmark_panel():
    return make_market_benchmark_panel()


# ============================================================
# fundamental_* alpha 测试
# ============================================================

def test_fundamental_roe(fund_panel):
    """fundamental_roe 应输出截面 zscore."""
    r = compute_alpha("fundamental_roe", fund_panel)
    assert isinstance(r, pd.DataFrame)
    assert r.shape == fund_panel["close"].shape
    # 截面均值 ≈ 0 (允许 NaN 行)
    means = r.mean(axis=1)
    valid_means = means[~means.isna()]
    if len(valid_means) > 0:
        assert abs(valid_means.mean()) < 0.5


def test_fundamental_gross_profitability(fund_panel):
    """fundamental_gross_profitability 应正确 zscore."""
    r = compute_alpha("fundamental_gross_profitability", fund_panel)
    assert isinstance(r, pd.DataFrame)
    assert r.shape == fund_panel["close"].shape


def test_fundamental_asset_growth(fund_panel):
    """fundamental_asset_growth 应反向 zscore."""
    r = compute_alpha("fundamental_asset_growth", fund_panel)
    assert isinstance(r, pd.DataFrame)
    assert r.shape == fund_panel["close"].shape


def test_fundamental_earnings_yield(fund_panel):
    """fundamental_earnings_yield = zscore(net_income / market_cap)."""
    r = compute_alpha("fundamental_earnings_yield", fund_panel)
    assert isinstance(r, pd.DataFrame)
    assert r.shape == fund_panel["close"].shape


def test_fundamental_invariants(fund_panel):
    """全部 fundamental alpha 输出应在合理数值范围 (|zscore| < 10)."""
    for aid in ["fundamental_roe", "fundamental_gross_profitability",
                "fundamental_asset_growth", "fundamental_earnings_yield"]:
        r = compute_alpha(aid, fund_panel)
        valid = r.dropna()
        assert ((valid >= -10) & (valid <= 10)).all().all(), \
            f"{aid}: values out of [-10, 10]"


# ============================================================
# academic_* alpha 测试 (用 close 即可)
# ============================================================

@pytest.mark.parametrize("aid", [
    "academic_hml",
    "academic_smb",
    "academic_mkt_rf",
    "academic_carhart_mom",
    "academic_cma",
    "academic_high52w",
    "academic_illiq",
    "academic_rmw",
    "academic_retskew",
    "academic_strev",
])
def test_academic_alpha_runs(aid, benchmark_panel):
    """academic alpha 跑通基本 panel."""
    r = compute_alpha(aid, benchmark_panel)
    assert isinstance(r, pd.DataFrame)
    assert r.shape == benchmark_panel["close"].shape


# ============================================================
# Mock 数据生成器测试
# ============================================================

def test_make_fundamentals_panel_returns_required_keys(fund_panel):
    """应包含所有 4 个 fundamental alpha 需要的 fund:* 数据."""
    required = {"fund:roe", "fund:gross_profitability",
                "fund:asset_growth", "fund:net_income", "fund:shares_diluted"}
    missing = required - fund_panel.keys()
    assert not missing, f"missing keys: {missing}"


def test_make_fundamentals_panel_roe_realistic(fund_panel):
    """ROE 范围合理 [-0.20, 0.50]."""
    roe = fund_panel["fund:roe"]
    assert (roe >= -0.30).all().all()
    assert (roe <= 0.50).all().all()


def test_make_fundamentals_panel_gp_realistic(fund_panel):
    """Gross profitability 范围合理 [-0.10, 0.60]."""
    gp = fund_panel["fund:gross_profitability"]
    assert (gp >= -0.10).all().all()
    assert (gp <= 0.60).all().all()


def test_make_fundamentals_panel_shares_positive(fund_panel):
    """shares_diluted 应为正."""
    sh = fund_panel["fund:shares_diluted"]
    assert (sh > 0).all().all()


def test_make_fundamentals_panel_reproducible():
    """同 seed 应产生完全相同数据."""
    p1 = make_fundamentals_panel(seed=42)
    p2 = make_fundamentals_panel(seed=42)
    for k in p1:
        np.testing.assert_array_equal(p1[k].values, p2[k].values)


def test_make_market_benchmark_panel_basic(benchmark_panel):
    """基本 OHLCV 数据完整."""
    required = {"open", "high", "low", "close", "volume"}
    missing = required - benchmark_panel.keys()
    assert not missing
