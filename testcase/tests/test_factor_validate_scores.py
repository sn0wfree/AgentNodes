"""H 档补测 —— `apps/factor_validate.py` 的评分子函数。

为什么优先这个文件：它是**纯数学**（IC/IR/六个维度的 0–1 评分），没有
网络、没有 DB、没有时钟依赖——不存在「外部依赖没法测」的理由。此前
67%，缺的正是各评分的边界分支（空序列、短序列、MultiIndex 与非
MultiIndex 两条路径）。

docs/phase1-research-discipline.md §2「单源评分铁律」把评分当作研究
纪律的执行点，所以这些函数的边界行为值得被锁死。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_research.apps import factor_validate as fv


# ── score_stability ─────────────────────────────────────────


def test_stability_empty_series_is_zero():
    assert fv.score_stability(pd.Series(dtype=float)) == 0.0


def test_stability_short_series_is_zero():
    """少于 5 个观测不足以谈稳定性。"""
    assert fv.score_stability(pd.Series([0.1, 0.2, 0.3])) == 0.0


def test_stability_all_positive_is_high():
    series = pd.Series([0.1, 0.2, 0.15, 0.3, 0.25, 0.2])
    score = fv.score_stability(series)
    assert 0.0 < score <= 1.0
    assert score > 0.6      # 全正且波动小 → 高分


def test_stability_all_negative_is_low():
    series = pd.Series([-0.1, -0.2, -0.15, -0.3, -0.25, -0.2])
    assert fv.score_stability(series) < 0.5


def test_stability_noisy_series_lower_than_smooth():
    smooth = pd.Series([0.1] * 8)
    noisy = pd.Series([0.1, -0.5, 0.9, -0.8, 0.3, -0.9, 0.7, -0.6])
    assert fv.score_stability(smooth) > fv.score_stability(noisy)


# ── score_diversification ───────────────────────────────────


def test_diversification_single_cross_section():
    s = pd.Series([1.0, 2.0, 3.0])
    score = fv.score_diversification(s)
    assert 0.0 <= score <= 1.0


def test_diversification_multiindex_path():
    idx = pd.MultiIndex.from_product(
        [pd.to_datetime(["2024-01-01", "2024-01-02"]), ["A", "B", "C"]])
    s = pd.Series(np.random.default_rng(1).normal(size=6), index=idx)
    score = fv.score_diversification(s)
    assert 0.0 <= score <= 1.0


def test_diversification_is_capped_at_one():
    s = pd.Series([0.0, 100.0])   # std 巨大
    assert fv.score_diversification(s) <= 1.0


def test_diversification_constant_series_is_zero():
    assert fv.score_diversification(pd.Series([5.0, 5.0, 5.0])) == 0.0


# ── score_turnover ──────────────────────────────────────────


def test_turnover_single_index_returns_neutral():
    """非 MultiIndex（无法按日期分组）→ 中性分 0.5，不判优也不判劣。"""
    assert fv.score_turnover(pd.Series([1.0, 2.0, 3.0])) == 0.5


def test_turnover_multiindex_two_dates():
    idx = pd.MultiIndex.from_product(
        [pd.to_datetime(["2024-01-01", "2024-01-02"]),
         [f"A{i}" for i in range(8)]])
    rng = np.random.default_rng(2)
    s = pd.Series(rng.normal(size=16), index=idx)
    score = fv.score_turnover(s)
    assert 0.0 <= score <= 1.0


def test_turnover_identical_dates_is_high():
    """两期排序完全一致 → 低换手 → 高分。"""
    vals = list(range(10))
    idx = pd.MultiIndex.from_product(
        [pd.to_datetime(["2024-01-01", "2024-01-02"]), [f"A{i}" for i in range(10)]])
    s = pd.Series(vals + vals, index=idx)
    assert fv.score_turnover(s) > 0.9


# ── score_coverage ──────────────────────────────────────────


def test_coverage_all_present():
    assert fv.score_coverage(pd.Series([1.0, 2.0, 3.0])) == 1.0


def test_coverage_with_nan():
    s = pd.Series([1.0, np.nan, 3.0, np.nan])
    score = fv.score_coverage(s)
    assert 0.0 <= score < 1.0


def test_coverage_all_nan():
    assert fv.score_coverage(pd.Series([np.nan, np.nan])) == 0.0


def test_coverage_empty():
    assert fv.score_coverage(pd.Series(dtype=float)) == 0.0


# ── score_rank_ic ───────────────────────────────────────────


@pytest.mark.parametrize("ic,expect_high", [
    (0.10, True),
    (0.05, True),
    (0.0, False),
    # 设计如此：评分基于 |IC|，负 IC 说明因子可反向使用，同样有效
    (-0.05, True),
])
def test_rank_ic_monotonic_in_ic(ic, expect_high):
    score = fv.score_rank_ic(ic)
    assert 0.0 <= score <= 1.0
    assert (score >= 0.5) is expect_high


def test_rank_ic_is_symmetric_in_sign():
    """|IC| 语义：+0.06 与 -0.06 同分。"""
    assert fv.score_rank_ic(0.06) == fv.score_rank_ic(-0.06)


def test_rank_ic_larger_ic_scores_higher():
    assert fv.score_rank_ic(0.15) >= fv.score_rank_ic(0.05)


# ── compute_overall_score ───────────────────────────────────


def test_overall_score_of_empty_is_zero():
    assert fv.compute_overall_score({}) == 0.0


def test_overall_score_is_normalized_to_unit_range():
    """加权后在 0–1 之间（不是 0–100 的百分制）。"""
    scores = {"stability": 0.8, "diversification": 0.6, "turnover": 0.7,
              "monotonicity": 0.5, "coverage": 0.9, "rank_ic": 0.4}
    total = fv.compute_overall_score(scores)
    assert 0.0 <= total <= 1.0


def test_overall_score_all_perfect_is_one():
    scores = {k: 1.0 for k in
              ("stability", "diversification", "turnover",
               "monotonicity", "coverage", "rank_ic")}
    assert fv.compute_overall_score(scores) == pytest.approx(1.0)


# ── compute_6d_scores ───────────────────────────────────────


def _panel(n_dates=20, n_assets=10, seed=3):
    rng = np.random.default_rng(seed)
    idx = pd.MultiIndex.from_product(
        [pd.bdate_range("2024-01-01", periods=n_dates),
         [f"A{i}" for i in range(n_assets)]])
    return pd.Series(rng.normal(size=len(idx)), index=idx)


def test_compute_6d_scores_returns_all_dimensions():
    """签名是 (ic_series, factor_values, forward_returns)。"""
    factor = _panel()
    dates = factor.index.get_level_values(0).unique()
    ic_series = pd.Series(np.random.default_rng(4).normal(size=len(dates)),
                          index=dates)
    fwd = pd.Series(np.random.default_rng(5).normal(size=len(factor)),
                    index=factor.index)
    scores = fv.compute_6d_scores(ic_series, factor, fwd)
    assert isinstance(scores, dict)
    for key in ("stability", "diversification", "turnover", "coverage"):
        assert key in scores
        assert 0.0 <= scores[key] <= 1.0


# ── compute_data_fingerprint ────────────────────────────────


def test_factor_fingerprint_is_stable():
    df = pd.DataFrame({"A": [1.0, 2.0], "B": [3.0, 4.0]},
                      index=pd.to_datetime(["2024-01-01", "2024-01-02"]))
    assert fv.compute_data_fingerprint(df) == fv.compute_data_fingerprint(df)


def test_factor_fingerprint_covers_shape_not_values():
    """指纹只由 (列名, 行数, 日期范围) 决定。

    已知局限（不是本测试要修的行为）：数值内容变化不会改变指纹，所以它
    能检出「换了数据区间/资产集合」，检不出「同一区间数据被修订」。若将来
    用于缓存失效判定，需要把数值摘要（如 sum/哈希）也纳入。
    """
    idx = pd.to_datetime(["2024-01-01", "2024-01-02"])
    a = pd.DataFrame({"A": [1.0, 2.0]}, index=idx)
    b = pd.DataFrame({"A": [1.0, 9.0]}, index=idx)
    assert fv.compute_data_fingerprint(a) == fv.compute_data_fingerprint(b)


def test_factor_fingerprint_detects_shape_change():
    idx = pd.to_datetime(["2024-01-01", "2024-01-02"])
    a = pd.DataFrame({"A": [1.0, 2.0]}, index=idx)
    c = pd.DataFrame({"A": [1.0, 2.0, 3.0]},
                     index=pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]))
    assert fv.compute_data_fingerprint(a) != fv.compute_data_fingerprint(c)


def test_factor_fingerprint_detects_column_change():
    idx = pd.to_datetime(["2024-01-01", "2024-01-02"])
    a = pd.DataFrame({"A": [1.0, 2.0]}, index=idx)
    b = pd.DataFrame({"B": [1.0, 2.0]}, index=idx)
    assert fv.compute_data_fingerprint(a) != fv.compute_data_fingerprint(b)
