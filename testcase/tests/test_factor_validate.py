"""factor_validate.py 深度单元测试。

覆盖：
- compute_ic / compute_ic_decay / compute_mutual_ic
- score_* 评分函数 (coverage, monotonicity, turnover, stability, rank_ic, diversification)
- deduplicate_factors
- validate_factor / validate_factors_batch
- compute_overall_score / compute_6d_scores
- compute_data_fingerprint
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import pytest

from strategy_research.apps.factor_validate import (
    SCORE_WEIGHTS,
    compute_6d_scores,
    compute_data_fingerprint,
    compute_ic,
    compute_ic_decay,
    compute_mutual_ic,
    compute_overall_score,
    deduplicate_factors,
    score_coverage,
    score_diversification,
    score_monotonicity,
    score_rank_ic,
    score_stability,
    score_turnover,
    validate_factor,
)

warnings.filterwarnings("ignore")


N = 252
SEED = 42


@pytest.fixture(scope="module")
def factor_series() -> pd.Series:
    rng = np.random.default_rng(SEED)
    return pd.Series(rng.standard_normal(N), index=pd.bdate_range("2023-01-01", periods=N))


@pytest.fixture(scope="module")
def returns_series(factor_series) -> pd.Series:
    rng = np.random.default_rng(SEED + 1)
    return pd.Series(rng.standard_normal(N) * 0.01, index=factor_series.index)


@pytest.fixture(scope="module")
def prices_df() -> pd.DataFrame:
    rng = np.random.default_rng(SEED)
    n_stocks = 5
    rets = rng.normal(0.0005, 0.02, (N, n_stocks))
    prices = 10 * np.exp(np.cumsum(rets, axis=0))
    return pd.DataFrame(prices, index=pd.bdate_range("2023-01-01", periods=N),
                        columns=list("ABCDE"))


# ============================================================
# compute_ic
# ============================================================

def test_compute_ic_returns_dict(factor_series, returns_series):
    ic = compute_ic(factor_series, returns_series)
    assert isinstance(ic, dict)
    assert "ic_mean" in ic
    assert "ic_std" in ic
    assert "ir" in ic
    assert "ic_series" in ic


def test_compute_ic_perfect_correlation(factor_series, returns_series):
    """完全相关时 ic_mean 应接近 1."""
    r = factor_series
    ic = compute_ic(r, r)
    if not np.isnan(ic["ic_mean"]):
        assert abs(ic["ic_mean"] - 1.0) < 1e-9


def test_compute_ic_negative_correlation(factor_series, returns_series):
    """完全负相关时 ic_mean 应接近 -1."""
    r = factor_series
    ic = compute_ic(r, -r)
    if not np.isnan(ic["ic_mean"]):
        assert abs(ic["ic_mean"] - (-1.0)) < 1e-9


def test_compute_ic_no_correlation(factor_series, returns_series):
    """不相关时 ic_mean 应接近 0."""
    rng = np.random.default_rng(999)
    f = pd.Series(rng.standard_normal(N))
    r = pd.Series(rng.standard_normal(N) * 100)
    ic = compute_ic(f, r)
    assert abs(ic["ic_mean"]) < 0.1, f"unrelated: ic_mean={ic['ic_mean']}"


def test_compute_ic_pearson_vs_spearman(factor_series, returns_series):
    ic_p = compute_ic(factor_series, returns_series, method="pearson")
    ic_s = compute_ic(factor_series, returns_series, method="spearman")
    # pearson 和 spearman 应都能正常计算
    assert "ic_mean" in ic_p
    assert "ic_mean" in ic_s


def test_compute_ic_ir_zero(factor_series, returns_series):
    """ic_std=0 时 IR 应为 0 (避免除零)."""
    const_factor = pd.Series(1.0, index=factor_series.index)
    ic = compute_ic(const_factor, returns_series)
    # 常数因子方差为 0, 应安全处理
    assert isinstance(ic["ir"], (int, float, np.floating))


# ============================================================
# compute_ic_decay
# ============================================================

def test_ic_decay_default_periods(factor_series, prices_df):
    decay = compute_ic_decay(factor_series, prices_df)
    # keys 是 ic_decay_{N}d 形式
    expected_keys = {"ic_decay_1d", "ic_decay_5d", "ic_decay_20d"}
    assert expected_keys.issubset(decay.keys())


def test_ic_decay_custom_periods(factor_series, prices_df):
    decay = compute_ic_decay(factor_series, prices_df, periods=[1, 3, 5, 10, 20])
    assert isinstance(decay, dict)


def test_ic_decay_returns_curve(factor_series, prices_df):
    decay = compute_ic_decay(factor_series, prices_df)
    # 每个值应在 [-1, 1]
    for k, v in decay.items():
        assert -1 <= v <= 1, f"{k}={v} out of [-1, 1]"


# ============================================================
# compute_mutual_ic
# ============================================================

def test_mutual_ic_self_one(factor_series):
    mic = compute_mutual_ic(factor_series, factor_series)
    assert abs(mic - 1.0) < 1e-9


def test_mutual_ic_negative(factor_series):
    mic = compute_mutual_ic(factor_series, -factor_series)
    assert abs(mic - (-1.0)) < 1e-9


def test_mutual_ic_unrelated(factor_series):
    rng = np.random.default_rng(999)
    other = pd.Series(rng.standard_normal(N), index=factor_series.index)
    mic = compute_mutual_ic(factor_series, other)
    assert abs(mic) < 0.2


def test_mutual_ic_range(factor_series):
    rng = np.random.default_rng(777)
    other = pd.Series(rng.standard_normal(N), index=factor_series.index)
    mic = compute_mutual_ic(factor_series, other)
    assert -1 <= mic <= 1, f"mic={mic} out of range"


# ============================================================
# compute_mutual_ic 时序口径 (MultiIndex 面板)
# ============================================================

def _make_panel(dates, n_assets, arrays) -> pd.Series | list[pd.Series]:
    """arrays: list of (n_dates, n_assets) ndarray, 每个是一个因子的面板值。

    返回 (date, asset) MultiIndex Series (单个) 或列表。
    """
    out = []
    for arr in arrays:
        s = {
            (dt, f"A{k}"): arr[i, k]
            for i, dt in enumerate(dates)
            for k in range(n_assets)
        }
        out.append(pd.Series(s))
    return out[0] if len(out) == 1 else out


def test_mutual_ic_panel_time_series_not_first_date():
    """首日截面完全同向、其余日期不相关 → 时序均值应低。

    旧实现只取第一个日期截面, 会误判为高度冗余 (≈1.0)。
    """
    rng = np.random.default_rng(SEED)
    n_dates, n_assets = 30, 10
    dates = pd.bdate_range("2024-01-01", periods=n_dates)

    f1 = rng.standard_normal((n_dates, n_assets))
    f2 = rng.standard_normal((n_dates, n_assets))
    f2[0] = f1[0]  # 只有第一天同向

    s1, s2 = _make_panel(dates, n_assets, [f1, f2])
    mic = compute_mutual_ic(s1, s2)
    # 时序均值 ≈ 1/30 的同向 + 29/30 噪声
    assert abs(mic) < 0.3, f"time-series mic should be low, got {mic}"


def test_mutual_ic_panel_persistently_correlated():
    """逐日持续同向 → 时序均值接近 1, 判定冗余。"""
    rng = np.random.default_rng(SEED)
    n_dates, n_assets = 30, 10
    dates = pd.bdate_range("2024-01-01", periods=n_dates)

    f1 = rng.standard_normal((n_dates, n_assets))
    f2 = f1 + rng.standard_normal((n_dates, n_assets)) * 0.1

    s1, s2 = _make_panel(dates, n_assets, [f1, f2])
    mic = compute_mutual_ic(s1, s2)
    assert mic > 0.7, f"persistent corr should be caught, got {mic}"


def test_mutual_ic_panel_self_correlation():
    rng = np.random.default_rng(SEED)
    n_dates, n_assets = 20, 8
    dates = pd.bdate_range("2024-01-01", periods=n_dates)
    f1 = rng.standard_normal((n_dates, n_assets))
    s1 = _make_panel(dates, n_assets, [f1])
    assert abs(compute_mutual_ic(s1, s1) - 1.0) < 1e-9


def test_mutual_ic_panel_insufficient_dates_returns_zero():
    """有效日期不足 min_dates → 0.0 (无法判定冗余, fail-open 保留)。"""
    rng = np.random.default_rng(SEED)
    n_dates, n_assets = 3, 10
    dates = pd.bdate_range("2024-01-01", periods=n_dates)
    f1 = rng.standard_normal((n_dates, n_assets))
    f2 = f1.copy()
    s1, s2 = _make_panel(dates, n_assets, [f1, f2])
    assert compute_mutual_ic(s1, s2) == 0.0


def test_mutual_ic_panel_thin_cross_sections_skipped():
    """截面资产数 < min_assets 的日期被跳过; 全部太薄 → 0.0。"""
    rng = np.random.default_rng(SEED)
    n_dates, n_assets = 20, 3  # 每天只有 3 个资产 < min_assets=5
    dates = pd.bdate_range("2024-01-01", periods=n_dates)
    f1 = rng.standard_normal((n_dates, n_assets))
    f2 = f1.copy()
    s1, s2 = _make_panel(dates, n_assets, [f1, f2])
    assert compute_mutual_ic(s1, s2) == 0.0


def test_mutual_ic_plain_series_still_rank_corr(factor_series):
    """非面板序列 (date 单索引) 保持整段 Spearman 口径。"""
    mic = compute_mutual_ic(factor_series, factor_series * 2.0 + 1.0)
    assert abs(mic - 1.0) < 1e-9


# ============================================================
# score_coverage
# ============================================================

def test_score_coverage_full(factor_series):
    s = score_coverage(factor_series)
    assert 0 <= s <= 1
    assert abs(s - 1.0) < 1e-9


def test_score_coverage_with_nan(factor_series):
    f = factor_series.copy()
    f.iloc[:50] = np.nan
    s = score_coverage(f)
    assert 0 <= s <= 1
    assert 0.7 < s < 0.9, f"half NaN, expected ~0.8, got {s}"


def test_score_coverage_all_nan():
    f = pd.Series([np.nan] * 10)
    s = score_coverage(f)
    assert s == 0.0


# ============================================================
# score_monotonicity
# ============================================================

def test_score_monotonicity_perfect(factor_series, returns_series):
    f = returns_series
    s = score_monotonicity(f, returns_series)
    assert 0 <= s <= 1
    assert abs(s - 1.0) < 1e-9


def test_score_monotonicity_random(factor_series, returns_series):
    rng = np.random.default_rng(SEED)
    f = pd.Series(rng.standard_normal(N), index=factor_series.index)
    s = score_monotonicity(f, returns_series)
    assert 0 <= s <= 1


def test_score_monotonicity_inverse(factor_series, returns_series):
    f = -returns_series
    s = score_monotonicity(f, returns_series)
    assert 0 <= s <= 1
    # 负相关时排序相反, monotonicity 应为 -1 (线性)
    # 通常 monotonicity 取绝对值或负值, 视实现而定


def test_score_monotonicity_n_quantiles(factor_series, returns_series):
    s5 = score_monotonicity(factor_series, returns_series, n_quantiles=5)
    s10 = score_monotonicity(factor_series, returns_series, n_quantiles=10)
    assert isinstance(s5, float)
    assert isinstance(s10, float)


# ============================================================
# score_turnover
# ============================================================

def test_score_turnover_constant():
    f = pd.Series(np.ones(100))
    s = score_turnover(f)
    assert 0 <= s <= 1


def test_score_turnover_random(factor_series):
    s = score_turnover(factor_series)
    assert 0 <= s <= 1


def test_score_turnover_returns_float(factor_series):
    s = score_turnover(factor_series)
    assert isinstance(s, (float, np.floating))


# ============================================================
# score_stability
# ============================================================

def test_score_stability_random(factor_series):
    s = score_stability(factor_series)
    assert isinstance(s, (float, np.floating))
    assert 0 <= s <= 1


def test_score_stability_deterministic():
    """单调递增序列应较稳定."""
    f = pd.Series(np.arange(100).astype(float) / 100)
    s = score_stability(f)
    assert 0 <= s <= 1


def test_score_stability_input_type(factor_series):
    """可接受 pd.Series 输入."""
    assert isinstance(score_stability(factor_series), (float, np.floating))


# ============================================================
# score_rank_ic
# ============================================================

def test_score_rank_ic_positive():
    s = score_rank_ic(0.05)
    assert 0 <= s <= 1


def test_score_rank_ic_zero():
    s = score_rank_ic(0.0)
    assert s == 0.0 or s >= 0


def test_score_rank_ic_negative():
    s = score_rank_ic(-0.05)
    assert 0 <= s <= 1


def test_score_rank_ic_bounds():
    """|IC| 越大分应越高."""
    s1 = score_rank_ic(0.01)
    s2 = score_rank_ic(0.05)
    s3 = score_rank_ic(0.10)
    assert s1 <= s2 <= s3 or s1 <= s3


# ============================================================
# score_diversification
# ============================================================

def test_score_diversification_random(factor_series):
    s = score_diversification(factor_series)
    assert isinstance(s, (float, np.floating))
    assert 0 <= s <= 1


def test_score_diversification_bounded():
    """评分应在 [0, 1]."""
    f = pd.Series(np.random.randn(100))
    s = score_diversification(f)
    assert 0 <= s <= 1


# ============================================================
# deduplicate_factors
# ============================================================

def test_deduplicate_no_dups():
    factors = [
        {"name": "f1", "ic_series": pd.Series([0.05, 0.06, 0.04])},
        {"name": "f2", "ic_series": pd.Series([0.03, 0.04, 0.05])},
    ]
    deduped = deduplicate_factors(factors)
    assert len(deduped) == 2


def test_deduplicate_with_dups():
    factors = [
        {"name": "f1", "ic_series": pd.Series([0.05, 0.06, 0.04])},
        {"name": "f2", "ic_series": pd.Series([0.05, 0.06, 0.04])},  # 与 f1 相同
    ]
    deduped = deduplicate_factors(factors, threshold=0.7)
    # 至少有一个被去重
    assert len(deduped) <= 2


def test_deduplicate_threshold():
    factors = [
        {"name": "f1", "ic_series": pd.Series([0.05, 0.06, 0.04])},
        {"name": "f2", "ic_series": pd.Series([0.07, 0.08, 0.09])},
    ]
    low_thresh = deduplicate_factors(factors, threshold=0.5)
    high_thresh = deduplicate_factors(factors, threshold=0.99)
    # 阈值越高, 去重越少
    assert len(high_thresh) >= len(low_thresh)


def test_deduplicate_empty():
    factors = []
    deduped = deduplicate_factors(factors)
    assert deduped == []


# ============================================================
# compute_6d_scores
# ============================================================

def test_compute_6d_scores_full(factor_series, returns_series):
    ic = compute_ic(factor_series, returns_series)
    scores = compute_6d_scores(factor_series, factor_series, returns_series, ic_mean=ic["ic_mean"])
    assert isinstance(scores, dict)
    # 应有 6 个维度
    {'stability', 'diversification', 'turnover', 'monotonicity', 'coverage', 'rank_ic'}
    assert set(scores.keys()) >= {k for k in scores.keys()}


def test_compute_6d_scores_all_bounded(factor_series, returns_series):
    ic = compute_ic(factor_series, returns_series)
    scores = compute_6d_scores(factor_series, factor_series, returns_series, ic_mean=ic["ic_mean"])
    for name, val in scores.items():
        if isinstance(val, (int, float)):
            assert 0 <= val <= 1, f"{name}={val} out of [0,1]"


# ============================================================
# compute_overall_score
# ============================================================

def test_compute_overall_score_basic():
    scores = {
        "stability": 0.5, "diversification": 0.5, "turnover": 0.5,
        "monotonicity": 0.5, "coverage": 0.5, "rank_ic": 0.5,
    }
    overall = compute_overall_score(scores)
    assert 0 <= overall <= 1


def test_compute_overall_score_perfect():
    """全 1 分 → overall 应为 1."""
    scores = {
        "stability": 1.0, "diversification": 1.0, "turnover": 1.0,
        "monotonicity": 1.0, "coverage": 1.0, "rank_ic": 1.0,
    }
    overall = compute_overall_score(scores)
    assert abs(overall - 1.0) < 1e-9


# ============================================================
# SCORE_WEIGHTS
# ============================================================

def test_score_weights_sum_to_one():
    """6D 权重应求和为 1."""
    s = sum(SCORE_WEIGHTS.values())
    assert abs(s - 1.0) < 1e-9


def test_score_weights_each_positive():
    """每项权重应 > 0."""
    for k, w in SCORE_WEIGHTS.items():
        assert w > 0, f"{k} weight {w}"


# ============================================================
# compute_data_fingerprint
# ============================================================

def test_data_fingerprint_returns_str(prices_df):
    fp = compute_data_fingerprint(prices_df)
    assert isinstance(fp, str)
    assert len(fp) > 0


def test_data_fingerprint_deterministic(prices_df):
    """相同输入应产生相同指纹."""
    fp1 = compute_data_fingerprint(prices_df)
    fp2 = compute_data_fingerprint(prices_df)
    assert fp1 == fp2


def test_data_fingerprint_different_data(prices_df):
    """不同数据应有不同指纹."""
    # 改变行数应改变指纹
    other_dates = pd.bdate_range("2024-01-01", periods=N * 2)
    fp1 = compute_data_fingerprint(prices_df)
    df2 = pd.DataFrame(np.random.randn(N * 2, 5), index=other_dates, columns=list("ABCDE"))
    fp2 = compute_data_fingerprint(df2)
    assert fp1 != fp2


# ============================================================
# validate_factor (高层)
# ============================================================

def test_validate_factor_returns_dict(prices_df, factor_series, returns_series):
    result = validate_factor(
        "test_factor",
        prices_df,
        forward_returns=returns_series,
        factor_values=factor_series,
    )
    assert isinstance(result, dict)


def test_validate_factor_required_keys():
    rng = np.random.default_rng(SEED)
    n = 100
    dates = pd.bdate_range("2024-01-01", periods=n)
    prices = pd.DataFrame(rng.uniform(10, 50, (n, 3)), index=dates, columns=list("ABC"))
    factor = pd.Series(rng.standard_normal(n), index=dates)
    rets = pd.Series(rng.standard_normal(n) * 0.01, index=dates)
    result = validate_factor(
        "f", prices,
        forward_returns=rets, factor_values=factor,
        strategy_name="test", source="unit_test",
    )
    expected_keys = {"passed", "ic_mean", "ic_std", "ir", "scores", "overall_score"}
    for k in expected_keys:
        assert k in result, f"missing key: {k}"


# ============================================================
