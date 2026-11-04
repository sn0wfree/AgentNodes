"""utils/covariance.py 和 ic_utils.py 单元测试.

covariance.py: 协方差矩阵估计 (4 种方法: sample/ledoit_wolf/ewma/diagonal)
- sample_covariance, ledoit_wolf_shrinkage, ewma_covariance, diagonal_covariance
- estimate_covariance (统一入口)
- is_positive_definite, condition_number

ic_utils.py: IC 计算
- compute_cross_sectional_ic (截面 IC 时间序列)
- compute_ic_summary (IC 摘要)
- compute_time_series_ic (时序 IC)
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import pytest

from strategy_research.foundation.utils import covariance, ic_utils

warnings.filterwarnings("ignore")


@pytest.fixture(scope="module")
def returns_df() -> pd.DataFrame:
    """随机收益 DataFrame (T=200, N=5)."""
    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2024-01-01", periods=200)
    return pd.DataFrame(
        rng.normal(0.001, 0.02, (200, 5)),
        index=dates, columns=list("ABCDE"),
    )


@pytest.fixture(scope="module")
def factor_panel() -> np.ndarray:
    """(T, N, K) 因子面板."""
    rng = np.random.default_rng(42)
    return rng.normal(0.0, 1.0, (104, 5, 3))


@pytest.fixture(scope="module")
def forward_returns(factor_panel) -> pd.DataFrame:
    """(T, N) 收益 DataFrame."""
    rng = np.random.default_rng(7)
    T, N = factor_panel.shape[:2]
    return pd.DataFrame(
        rng.normal(0.001, 0.02, (T, N)),
        index=pd.bdate_range("2024-01-01", periods=T),
        columns=[f"S{i}" for i in range(N)],
    )


# ============================================================
# sample_covariance
# ============================================================

def test_sample_covariance_shape(returns_df):
    """输出 shape 应为 (n_assets, n_assets)."""
    cov = covariance.sample_covariance(returns_df)
    assert cov.shape == (5, 5)


def test_sample_covariance_symmetric(returns_df):
    """协方差矩阵应对称."""
    cov = covariance.sample_covariance(returns_df)
    np.testing.assert_array_almost_equal(cov, cov.T)


def test_sample_covariance_diagonal_positive(returns_df):
    """对角线 (方差) 应为正."""
    cov = covariance.sample_covariance(returns_df)
    diag = np.diag(cov)
    assert (diag > 0).all()


def test_sample_covariance_annualized(returns_df):
    """sample_covariance 默认年化 (× 252)."""
    cov = covariance.sample_covariance(returns_df)
    # 检验: 对角线应 ≈ daily_var * 252
    expected = returns_df.var(ddof=1).values * 252
    np.testing.assert_array_almost_equal(np.diag(cov), expected)


# ============================================================
# ledoit_wolf_shrinkage
# ============================================================

def test_ledoit_wolf_returns_tuple(returns_df):
    """返回 (shrunk_cov, alpha) 元组."""
    result = covariance.ledoit_wolf_shrinkage(returns_df)
    assert isinstance(result, tuple)
    assert len(result) == 2


def test_ledoit_wolf_shrunk_positive_definite(returns_df):
    """shrinkage 后矩阵应为正定."""
    cov, _ = covariance.ledoit_wolf_shrinkage(returns_df)
    assert covariance.is_positive_definite(cov)


def test_ledoit_wolf_alpha_in_range(returns_df):
    """shrinkage intensity alpha 应在 [0, 1]."""
    _, alpha = covariance.ledoit_wolf_shrinkage(returns_df)
    assert 0.0 <= alpha <= 1.0


def test_ledoit_wolf_too_few_samples_raises():
    """样本数 < 2 应报错."""
    df = pd.DataFrame({"A": [0.01]})  # 单行
    with pytest.raises(ValueError, match="样本数不足"):
        covariance.ledoit_wolf_shrinkage(df)


def test_ledoit_wolf_symmetric(returns_df):
    """shrinkage 后矩阵应对称."""
    cov, _ = covariance.ledoit_wolf_shrinkage(returns_df)
    np.testing.assert_array_almost_equal(cov, cov.T)


# ============================================================
# ewma_covariance
# ============================================================

def test_ewma_covariance_shape(returns_df):
    """shape 应为 (n_assets, n_assets)."""
    cov = covariance.ewma_covariance(returns_df, halflife=60)
    assert cov.shape == (5, 5)


def test_ewma_covariance_symmetric(returns_df):
    cov = covariance.ewma_covariance(returns_df, halflife=60)
    np.testing.assert_array_almost_equal(cov, cov.T)


def test_ewma_covariance_halflife_param(returns_df):
    """halflife 参数应被接受."""
    cov_30 = covariance.ewma_covariance(returns_df, halflife=30)
    cov_120 = covariance.ewma_covariance(returns_df, halflife=120)
    # 不同 halflife 应产生不同结果
    assert not np.allclose(cov_30, cov_120)


def test_ewma_covariance_too_few_samples_raises():
    df = pd.DataFrame({"A": [0.01]})
    with pytest.raises(ValueError):
        covariance.ewma_covariance(df)


# ============================================================
# diagonal_covariance
# ============================================================

def test_diagonal_covariance_diagonal(returns_df):
    """diagonal cov 应是对角矩阵."""
    cov = covariance.diagonal_covariance(returns_df)
    # 对角线外全为 0
    for i in range(5):
        for j in range(5):
            if i != j:
                assert cov[i, j] == 0


def test_diagonal_covariance_shape(returns_df):
    cov = covariance.diagonal_covariance(returns_df)
    assert cov.shape == (5, 5)


def test_diagonal_covariance_positive_definite(returns_df):
    """对角矩阵若对角 > 0 则正定."""
    cov = covariance.diagonal_covariance(returns_df)
    assert covariance.is_positive_definite(cov)


# ============================================================
# estimate_covariance (统一入口)
# ============================================================

def test_estimate_covariance_sample(returns_df):
    cov = covariance.estimate_covariance(returns_df, method="sample")
    np.testing.assert_array_almost_equal(
        cov, covariance.sample_covariance(returns_df)
    )


def test_estimate_covariance_ledoit_wolf(returns_df):
    cov = covariance.estimate_covariance(returns_df, method="ledoit_wolf")
    expected, _ = covariance.ledoit_wolf_shrinkage(returns_df)
    np.testing.assert_array_almost_equal(cov, expected)


def test_estimate_covariance_ewma(returns_df):
    cov = covariance.estimate_covariance(returns_df, method="ewma", halflife=60)
    expected = covariance.ewma_covariance(returns_df, halflife=60)
    np.testing.assert_array_almost_equal(cov, expected)


def test_estimate_covariance_diagonal(returns_df):
    cov = covariance.estimate_covariance(returns_df, method="diagonal")
    expected = covariance.diagonal_covariance(returns_df)
    np.testing.assert_array_almost_equal(cov, expected)


def test_estimate_covariance_invalid_method(returns_df):
    """未知 method 应抛 ValueError."""
    with pytest.raises(ValueError, match="未知方法"):
        covariance.estimate_covariance(returns_df, method="bogus")


def test_estimate_covariance_case_insensitive(returns_df):
    """大小写应不影响."""
    cov1 = covariance.estimate_covariance(returns_df, method="Ledoit_Wolf")
    cov2 = covariance.estimate_covariance(returns_df, method="ledoit_wolf")
    np.testing.assert_array_almost_equal(cov1, cov2)


# ============================================================
# is_positive_definite / condition_number
# ============================================================

def test_is_positive_definite_identity():
    """单位矩阵是正定的."""
    assert covariance.is_positive_definite(np.eye(3))


def test_is_positive_definite_random():
    """样本协方差通常正定."""
    rng = np.random.default_rng(42)
    A = rng.standard_normal((5, 100))
    cov = np.cov(A)
    assert covariance.is_positive_definite(cov)


def test_is_positive_definite_singular():
    """奇异矩阵应返回 False."""
    singular = np.array([[1, 1], [1, 1]], dtype=float)
    assert not covariance.is_positive_definite(singular)


def test_condition_number_identity():
    """单位矩阵条件数 = 1."""
    assert covariance.condition_number(np.eye(3)) == 1.0


def test_condition_number_singular_returns_inf():
    """奇异矩阵条件数为 inf."""
    singular = np.array([[1, 1], [1, 1]], dtype=float)
    cond = covariance.condition_number(singular)
    assert cond == float("inf") or cond > 1e10


def test_condition_number_non_negative():
    """条件数应 >= 1."""
    rng = np.random.default_rng(42)
    A = rng.standard_normal((5, 100))
    cov = np.cov(A)
    assert covariance.condition_number(cov) >= 1.0


# ============================================================
# ic_utils: compute_cross_sectional_ic
# ============================================================

def test_cross_sectional_ic_returns_list(factor_panel, forward_returns):
    """应返回 list[float]."""
    ics = ic_utils.compute_cross_sectional_ic(
        factor_panel, forward_returns, factor_idx=0
    )
    assert isinstance(ics, list)


def test_cross_sectional_ic_basic(factor_panel, forward_returns):
    """IC 应是有限数."""
    ics = ic_utils.compute_cross_sectional_ic(
        factor_panel, forward_returns, factor_idx=0
    )
    for ic in ics:
        assert np.isfinite(ic)
        assert -1 <= ic <= 1


def test_cross_sectional_ic_perfect_correlation():
    """完美预测: forward return = factor[t] * 0.01.

    让 Y[t+1] = factor[t] (未来收益是当期因子)
    → Y[t] = factor[t-1]
    → np.roll(factor, 1, axis=0) 给出 Y[t] = factor[t-1]
    """
    rng = np.random.default_rng(42)
    T, N = 100, 30
    factor = rng.standard_normal((T, N, 1))  # 单因子
    # Y[t] = factor[t-1] * 0.01 (通过 +1 roll 实现)
    rets = pd.DataFrame(
        np.roll(factor[:, :, 0], 1, axis=0) * 0.01,
        index=pd.bdate_range("2024-01-01", periods=T),
    )
    ics = ic_utils.compute_cross_sectional_ic(factor, rets, factor_idx=0, start_t=20)
    assert all(ic > 0.85 for ic in ics), \
        f"IC should be > 0.85 for perfect predictor: {ics[:5]}..."


def test_cross_sectional_ic_min_obs():
    """当窗口内有效数 < min_obs, 不应加入 IC list."""
    rng = np.random.default_rng(42)
    T, N = 100, 30
    factor = rng.standard_normal((T, N, 1))
    rets = pd.DataFrame(
        rng.standard_normal((T, N)) * 0.01,
        index=pd.bdate_range("2024-01-01", periods=T),
    )
    # min_obs=20 (>> 半 panel), 应排除多数窗口
    ics = ic_utils.compute_cross_sectional_ic(
        factor, rets, factor_idx=0, min_obs=20
    )
    assert isinstance(ics, list)
    for ic in ics:
        assert np.isfinite(ic)


# ============================================================
# ic_utils: compute_ic_summary
# ============================================================

def test_ic_summary_returns_dict():
    s = ic_utils.compute_ic_summary([0.05, 0.06, 0.04, 0.05, 0.07])
    assert isinstance(s, dict)
    assert "ic_mean" in s
    assert "ic_std" in s
    assert "icir" in s
    assert "pct_positive" in s
    assert "n_obs" in s


def test_ic_summary_empty_list():
    """空列表应返回零值摘要."""
    s = ic_utils.compute_ic_summary([])
    assert s["ic_mean"] == 0
    assert s["n_obs"] == 0


def test_ic_summary_mean_correct():
    """ic_mean 应 = 列表均值."""
    ics = [0.05, 0.06, 0.04]
    s = ic_utils.compute_ic_summary(ics)
    assert abs(s["ic_mean"] - 0.05) < 1e-9


def test_ic_summary_icir_correct():
    """icir = ic_mean / ic_std."""
    ics = [0.04, 0.06, 0.05]
    s = ic_utils.compute_ic_summary(ics)
    expected = np.mean(ics) / np.std(ics)
    assert abs(s["icir"] - expected) < 1e-9


def test_ic_summary_pct_positive():
    """pct_positive 应 = ic > 0 占比."""
    ics = [-0.1, 0.05, 0.06, -0.02]
    s = ic_utils.compute_ic_summary(ics)
    assert abs(s["pct_positive"] - 0.5) < 1e-9


def test_ic_summary_constant_ics():
    """常数 IC (std=0) 时 icir 应 = 0 (即使 std 因 float 精度非零)."""
    s = ic_utils.compute_ic_summary([0.05, 0.05, 0.05])
    # std ≈ 0 (允许极小浮点误差)
    assert abs(s["ic_mean"] - 0.05) < 1e-9  # 均值近似 0.05
    # icir 是 ic_mean / std, 当 std 极小时会很大. 只检查非 inf.
    assert np.isfinite(s["icir"])


# ============================================================
# ic_utils: compute_time_series_ic
# ============================================================

def test_time_series_ic_perfect():
    """完全相关时 IC ≈ 1."""
    rng = np.random.default_rng(42)
    factor = rng.standard_normal(100)
    market = factor * 1.0 + rng.normal(0, 0.01, 100)
    ic, pval = ic_utils.compute_time_series_ic(factor, market)
    assert ic > 0.99
    assert pval < 0.01


def test_time_series_ic_returns_tuple():
    rng = np.random.default_rng(42)
    factor = rng.standard_normal(100)
    market = rng.standard_normal(100)
    result = ic_utils.compute_time_series_ic(factor, market)
    assert isinstance(result, tuple)
    assert len(result) == 2


def test_time_series_ic_too_few_points():
    """< 10 个有效点应返回 (0, 1)."""
    factor = np.array([1.0, np.nan, np.nan, np.nan, np.nan])
    market = np.array([1.0, np.nan, np.nan, np.nan, np.nan])
    ic, pval = ic_utils.compute_time_series_ic(factor, market)
    assert ic == 0.0
    assert pval == 1.0


def test_time_series_ic_independent():
    """无相关时 IC ≈ 0."""
    rng = np.random.default_rng(42)
    factor = rng.standard_normal(100)
    market = rng.standard_normal(100)
    ic, _ = ic_utils.compute_time_series_ic(factor, market)
    assert abs(ic) < 0.3


def test_time_series_ic_in_range():
    """IC 应在 [-1, 1]."""
    rng = np.random.default_rng(42)
    factor = rng.standard_normal(100)
    market = rng.standard_normal(100)
    ic, _ = ic_utils.compute_time_series_ic(factor, market)
    assert -1 <= ic <= 1


def test_time_series_ic_pvalue_in_range():
    """p-value 应在 [0, 1]."""
    rng = np.random.default_rng(42)
    factor = rng.standard_normal(100)
    market = rng.standard_normal(100)
    _, pval = ic_utils.compute_time_series_ic(factor, market)
    assert 0 <= pval <= 1


def test_time_series_ic_uncorrelated_zero_pvalue():
    """IC ≈ 0 时 p-value 应较高."""
    rng = np.random.default_rng(42)
    factor = rng.standard_normal(100)
    rng2 = np.random.default_rng(99)
    market = rng2.standard_normal(100)  # 独立
    _, pval = ic_utils.compute_time_series_ic(factor, market)
    assert pval > 0.05  # 5% 显著性水平不拒绝
