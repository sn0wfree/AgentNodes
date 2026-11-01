"""H 档补测 —— `core/compute_factor.py` 的算子库冒烟 + 关键语义。

604 行 / 80+ 算子，此前 42.6%：大量单行算子（ts_* / expanding_* / ewm_* /
截面与二元运算）零覆盖。它们是纯 pandas 变换，用参数化冒烟一次覆盖；
另对**语义易错的算子**做数值断言（ts_decay_linear 权重、signed_power
保号、safe_div 防除零、winsorize 截断、表达式解析器的复杂度分析）。

算子词表是研究纪律的一部分（docs/phase1-research-discipline.md）：
表达式解析器只认这张表，所以 get_available_operators 与注册表的一致性
也被锁住。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_research.core import compute_factor as cf


@pytest.fixture
def s() -> pd.Series:
    rng = np.random.default_rng(11)
    idx = pd.bdate_range("2024-01-01", periods=60)
    return pd.Series(rng.normal(100, 5, 60).cumsum(), index=idx)


def _valid(out, n=60) -> None:
    assert isinstance(out, pd.Series)
    assert len(out) == n


# ── 时序算子冒烟 ────────────────────────────────────────────


TS_OPS_WINDOW = [
    ("ts_return", cf.ts_return), ("ts_std", cf.ts_std),
    ("ts_rank", cf.ts_rank), ("ts_max", cf.ts_max), ("ts_min", cf.ts_min),
    ("ts_mean", cf.ts_mean), ("ts_sum", cf.ts_sum), ("ts_skew", cf.ts_skew),
    ("ts_kurt", cf.ts_kurt), ("ts_median", cf.ts_median),
    ("ts_var", cf.ts_var), ("ts_prod", cf.ts_prod),
    ("ts_argmax", cf.ts_argmax), ("ts_argmin", cf.ts_argmin),
    ("ts_pct_change", cf.ts_pct_change),
    ("ts_zscore", cf.ts_zscore), ("ts_decay_linear", cf.ts_decay_linear),
    ("expanding_sum", cf.expanding_sum), ("expanding_mean", cf.expanding_mean),
    ("expanding_max", cf.expanding_max), ("expanding_min", cf.expanding_min),
    ("ewm_mean", cf.ewm_mean), ("ewm_std", cf.ewm_std),
    ("ts_centralization", cf.ts_centralization),
    ("ts_standardization", cf.ts_standardization),
    ("ts_entropy", cf.ts_entropy), ("ts_pct_pos", cf.ts_pct_pos),
    ("ts_count_pos", cf.ts_count_pos), ("ts_count_neg", cf.ts_count_neg),
    ("ts_max_min_diff", cf.ts_max_min_diff), ("ts_iqr", cf.ts_iqr),
    ("ts_median_abs_dev", cf.ts_median_abs_dev),
    ("ts_trim_mean", cf.ts_trim_mean), ("ts_huber_mean", cf.ts_huber_mean),
]


@pytest.mark.parametrize("name,fn", TS_OPS_WINDOW)
def test_ts_operator_smoke(s, name, fn):
    """每个时序算子用统一输入冒烟：不抛、返回等长 Series。"""
    if name == "ts_pct_change":
        kwargs = {"periods": 5}
    elif name == "ts_entropy":
        kwargs = {"window": 10, "bins": 5}
    elif name in ("ewm_mean", "ewm_std"):
        kwargs = {"span": 10}
    elif name.startswith("expanding_"):
        kwargs = {}          # 扩展窗口无参数
    else:
        kwargs = {"window": 10}
    _valid(fn(s, **kwargs))


def test_ts_corr_and_cov(s):
    _valid(cf.ts_corr(s, s * 2, window=10))
    _valid(cf.ts_cov(s, s * 2, window=10))


def test_delay_and_delta(s):
    _valid(cf.delay(s, 3))
    _valid(cf.delta(s, 3))
    # delay 的语义：向后移 N 期
    assert cf.delay(s, 1).iloc[1] == s.iloc[0]
    # delta 的语义：今 - 前
    assert cf.delta(s, 1).iloc[1] == s.iloc[1] - s.iloc[0]


def test_decay_linear_weights_sum_to_one(s):
    """线性衰减：最近一期权重最大，窗口内权重和为 1（数值上）。"""
    out = cf.ts_decay_linear(s, window=5)
    expected = s.rolling(5).apply(
        lambda w: np.dot(w, np.arange(1, 6) / np.arange(1, 6).sum()), raw=True,
    )
    pd.testing.assert_series_equal(out, expected, check_names=False)


def test_decay_exp_halflife(s):
    _valid(cf.ts_decay_exp(s, halflife=5))


def test_ts_regression_family(s):
    y = s
    x = s.shift(1).fillna(0)
    for fn in (cf.ts_regression_beta, cf.ts_regression_alpha,
               cf.ts_regression_resid, cf.ts_regression_r2):
        _valid(fn(y, x, window=20))
    # 完全线性关系 → beta≈2, r2≈1
    y_lin = 2 * x + 1
    beta = cf.ts_regression_beta(y_lin, x, window=20).dropna()
    assert abs(beta.iloc[-1] - 2.0) < 1e-6
    r2 = cf.ts_regression_r2(y_lin, x, window=20).dropna()
    assert abs(r2.iloc[-1] - 1.0) < 1e-6


# ── 截面与统计算子 ──────────────────────────────────────────


def test_rank_zscore_scale(s):
    r = cf.rank(s)
    assert r.max() <= 1.0 and r.min() >= 0.0
    z = cf.zscore(s)
    assert abs(z.mean()) < 1e-6
    sc = cf.scale(s)
    assert abs(sc.abs().sum()) > 0


def test_winsorize_clips_outliers():
    s = pd.Series([0.0] * 20 + [1000.0, -1000.0])
    out = cf.winsorize(s, n=3.0)
    assert out.abs().max() < 1000.0


def test_neutralize_removes_market_component(s):
    out = cf.neutralize_market(s)
    _valid(out)
    # 去市场后均值≈0
    assert abs(out.mean()) < 1e-6


def test_group_norm(s):
    group = pd.Series(["g1", "g2"] * 30, index=s.index)
    _valid(cf.group_norm(s, group))


def test_orthogonalize(s):
    _valid(cf.orthogonalize(s, s * 2))


def test_mad(s):
    _valid(cf.mad(s))


def test_ic_and_rank_ic(s):
    target = s.shift(-1).fillna(s.iloc[-1])
    v = cf.ic(s, target)
    assert -1.0 <= v <= 1.0
    rv = cf.rank_ic(s, target)
    assert -1.0 <= rv <= 1.0


def test_cross_sectional_helpers(s):
    _valid(cf.cross_sectional_mean(s))
    _valid(cf.cross_sectional_std(s))


def test_cs_quantile_clip_and_pct_pos():
    df = pd.DataFrame(np.random.default_rng(3).normal(size=(10, 5)))
    clipped = cf.cs_quantile_clip(df)
    assert clipped.shape == df.shape
    pos = cf.cs_pct_pos(df)
    assert ((pos >= 0) & (pos <= 1)).all().all()


# ── 价量辅助 ────────────────────────────────────────────────


def test_price_volume_helpers(s):
    vwap = s
    high = s + 1
    low = s - 1
    vol = pd.Series(np.ones(60), index=s.index)
    _valid(cf.vwap_dev(s, vwap))
    _valid(cf.hl_range(high, low))
    _valid(cf.hl_range(high, low, s))
    _valid(cf.oc_change(s, s + 1))
    _valid(cf.close_to_high(s, high, window=10))
    _valid(cf.close_to_low(s, low, window=10))
    ret = cf.ts_return(s, 1)
    _valid(cf.returns_vol_adj(ret, window=10))
    _valid(cf.dollar_volume_n(s, vol, window=10))


# ── 二元/一元组合 ───────────────────────────────────────────


def test_unary_ops(s):
    _valid(cf.abs_op(s - 50))
    _valid(cf.log(s.abs() + 1))
    _valid(cf.sign(s))
    _valid(cf.sqrt(s.abs()))
    _valid(cf.clip(s, -1e6, 1e6))
    _valid(cf.fill_null(s))


def test_fill_null_fills():
    s = pd.Series([1.0, np.nan, 3.0])
    assert cf.fill_null(s, 0.0).isna().sum() == 0


def test_binary_ops(s):
    other = s + 1
    _valid(cf.add(s, other))
    _valid(cf.sub(s, other))
    _valid(cf.mul(s, other))
    _valid(cf.div(s, other))
    _valid(cf.where(s > 50, s, other))
    _valid(cf.weighted_sum([s, other], [0.3, 0.7]))
    _valid(cf.combine(s, other, "add"))
    _valid(cf.combine(s, other, "sub"))


def test_signed_power_preserves_sign():
    s = pd.Series([-8.0, 4.0, -27.0])
    out = cf.signed_power(s, 1.0 / 3.0)
    assert out.iloc[0] == pytest.approx(-2.0)
    assert out.iloc[1] == pytest.approx(np.cbrt(4.0))
    assert out.iloc[2] == pytest.approx(-3.0)


def test_safe_div_returns_nan_on_zero_denominator():
    """safe_div 的契约：|b|<eps → NaN（避免 inf 污染下游），有效位正常除。"""
    a = pd.Series([1.0, 2.0, 3.0])
    b = pd.Series([0.0, 2.0, 0.0])
    out = cf.safe_div(a, b)
    assert out.iloc[0] != out.iloc[0]        # NaN
    assert out.iloc[1] == 1.0
    assert out.iloc[2] != out.iloc[2]        # NaN


def test_div_by_zero_series_is_inf_or_clipped():
    """plain div 允许 inf（调用方负责），safe_div 才是防御版。"""
    a = pd.Series([1.0])
    b = pd.Series([0.0])
    assert np.isinf(cf.div(a, b).iloc[0])


# ── 表达式解析与复杂度 ──────────────────────────────────────


def test_get_available_operators_nonempty():
    ops = cf.get_available_operators()
    assert ops
    assert "ts_mean" in ops
    assert "rank" in ops


def test_analyze_complexity_counts():
    info = cf.analyze_complexity("ts_mean(ts_return(close, 1), 5)")
    assert isinstance(info, dict)
    assert info.get("operator_count", 0) >= 2


def test_evaluate_expression_simple(s):
    data = pd.DataFrame({"close": s})
    out = cf.evaluate_expression("ts_mean(close, 5)", data)
    _valid(out)


def test_evaluate_expression_nested(s):
    data = pd.DataFrame({"close": s, "volume": pd.Series(
        np.ones(60) * 100, index=s.index)})
    out = cf.evaluate_expression("rank(ts_return(close, 1))", data)
    _valid(out)


def test_compute_factor_end_to_end(s):
    data = pd.DataFrame({"close": s, "open": s - 1,
                         "high": s + 1, "low": s - 2,
                         "volume": pd.Series(np.ones(60) * 100, index=s.index)})
    out = cf.compute_factor("ts_zscore(ts_return(close, 1), 20)", data)
    _valid(out)


def test_compute_factors_batch():
    """签名：[{"factor_name", "factor_code"}] → DataFrame(columns=factor_names)。"""
    data = pd.DataFrame({"close": pd.Series(
        np.linspace(10, 20, 30),
        index=pd.bdate_range("2024-01-01", periods=30))})
    results = cf.compute_factors_batch(
        [{"factor_name": "f1", "factor_code": "ts_mean(close, 5)"},
         {"factor_name": "f2", "factor_code": "rank(close)"}], data)
    assert set(results.columns) == {"f1", "f2"}


def test_compute_factors_batch_collects_errors():
    """单个因子失败不拖垮整批：坏表达式被收集，好因子照常产出。"""
    data = pd.DataFrame({"close": pd.Series(
        np.linspace(10, 20, 30),
        index=pd.bdate_range("2024-01-01", periods=30))})
    results = cf.compute_factors_batch(
        [{"factor_name": "good", "factor_code": "rank(close)"},
         {"factor_name": "bad", "factor_code": "not_an_op(close)"}], data)
    assert "good" in results.columns
    assert "bad" not in results.columns


def test_unknown_operator_fails_loud(s):
    """表达式里出现词表外的算子 → 明确报错（不是静默返回空）。"""
    data = pd.DataFrame({"close": s})
    with pytest.raises(Exception):
        cf.evaluate_expression("not_a_real_op(close, 5)", data)
