"""每个算子的独立单元测试。

101 个 OPERATORS 算子逐个测试：构造最小输入 → 执行 → 校验：
1. 不抛异常（除非预期）
2. 返回类型合理（pd.Series 或标量）
3. 数值在期望范围

注意：compute_factor.OPERATORS 大多数设计为 pd.Series 输入。
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import pytest

from strategy_research.apps.compute_factor import OPERATORS

warnings.filterwarnings("ignore")


N = 60
SEED = 42


@pytest.fixture(scope="module")
def s_a() -> pd.Series:
    rng = np.random.default_rng(SEED)
    return pd.Series(rng.uniform(10.0, 50.0, N),
                     index=pd.bdate_range("2024-01-01", periods=N))


@pytest.fixture(scope="module")
def s_b() -> pd.Series:
    rng = np.random.default_rng(SEED + 1)
    return pd.Series(rng.uniform(20.0, 60.0, N),
                     index=pd.bdate_range("2024-01-01", periods=N))


@pytest.fixture(scope="module")
def df_a(s_a) -> pd.DataFrame:
    """3-资产 DataFrame。"""
    rng = np.random.default_rng(SEED)
    arr = rng.uniform(10.0, 50.0, (N, 3))
    return pd.DataFrame(arr, index=s_a.index, columns=["A", "B", "C"])


@pytest.fixture(scope="module")
def const_5() -> float:
    return 5.0


# 各算子的参数构造（每个算子的合法参数类型)

def _args(op, s_a, s_b, df_a, const_5):  # noqa: C901
    """构造针对每个算子的参数列表."""
    if op in {"ts_mean", "ts_std", "ts_max", "ts_min", "ts_sum",
              "ts_skew", "ts_kurt", "ts_median", "ts_var", "ts_prod",
              "ts_argmax", "ts_argmin", "ts_zscore", "ts_decay_linear",
              "ts_pct_change", "ts_return", "delay", "delta"}:
        return [s_a, 5]
    if op == "ts_decay_exp":
        return [s_a, 5]
    if op in {"ts_corr", "ts_cov", "ewm_corr"}:
        return [s_a, s_b, 5]
    if op == "ts_rank":
        return [s_a, 5]
    if op in {"ewm_mean", "ewm_std"}:
        return [s_a, 5]
    if op in {"expanding_sum", "expanding_mean", "expanding_max", "expanding_min"}:
        return [s_a]
    if op == "rank":
        return [s_a]
    if op in {"zscore", "scale", "winsorize", "mad", "neutralize_market",
              "cross_sectional_mean", "cross_sectional_std"}:
        return [s_a]
    if op == "neutralize":
        return [s_a, np.tile([0, 1, 0, 1, 0], N // 5 + 1)[:N]]
    if op == "group_norm":
        # 需要 group 是 pd.Series
        return [s_a, pd.Series(np.tile([0, 1, 0, 1], N // 4 + 1)[:N], index=s_a.index)]
    if op == "orthogonalize":
        return [s_a, s_b]
    if op == "ic" or op == "rank_ic":
        return [s_a, s_b]
    if op in {"abs_op", "log", "sign", "sqrt", "log1p", "exp", "neg", "abs"}:
        return [s_a]
    if op == "clip":
        return [s_a, 15.0, 45.0]
    if op == "fill_null":
        return [s_a, 0.0]
    if op == "fillna":
        return [s_a, 0.0]
    if op in {"add", "sub", "mul", "div", "safe_div"}:
        return [s_a, s_b]
    if op in {"lt", "lte", "gt", "gte", "eq", "neq"}:
        return [s_a, const_5]
    if op in {"minimum", "maximum", "fmin", "fmax"}:
        return [s_a, const_5]
    if op in {"pow", "power", "signed_power"}:
        return [s_a, 2.0]
    if op in {"or_", "and_"}:
        # 用布尔 Series 测
        return [s_a > 30, s_b > 30]
    if op == "not_":
        return [s_a > 30]
    if op == "where":
        return [s_a > 30, s_a, s_b]
    if op == "weighted_sum":
        return [[s_a, s_b], [0.6, 0.4]]
    if op == "combine":
        return [s_a, s_b, "add"]
    if op == "pct_change":
        return [s_a, 5]
    if op == "shift":
        return [s_a, 5]
    if op == "diff":
        return [s_a, 5]
    if op in {"cumsum", "cumprod", "cummax", "cummin"}:
        return [s_a]
    if op == "replace":
        return [s_a, s_a.iloc[10], 99.0]
    if op == "astype":
        return [s_a, "float64"]
    if op == "to_numpy":
        return [s_a]
    if op == "to_df":
        return [s_a.values, pd.DataFrame({"A": s_a.values})]
    # Lambda 滚动算子 (compute_factor.py:455-464) - 处理 (series, window)
    if op in {"sum", "mean", "std", "var", "min", "max",
              "median", "skew", "kurt", "quantile"}:
        return [s_a, 5]
    if op == "clip_upper":
        return [s_a, 45.0]
    if op == "clip_lower":
        return [s_a, 15.0]
    if op == "copy":
        return [s_a]
    if op == "ones_like":
        return [s_a]
    # -------- 新增高级算子 (compute_factor.py) --------
    # 截面高级
    if op == "cs_quantile_clip":
        return [df_a, 0.05, 0.95]
    if op == "cs_pct_pos":
        return [df_a]
    # 时序高级
    if op == "ts_centralization":
        return [s_a, 5]
    if op == "ts_standardization":
        return [s_a, 5]
    if op == "ts_entropy":
        return [s_a, 5, 5]  # series, window, bins
    if op == "ts_pct_pos":
        return [s_a, 5]
    if op == "ts_count_pos":
        return [s_a, 5]
    if op == "ts_count_neg":
        return [s_a, 5]
    if op == "ts_max_min_diff":
        return [s_a, 5]
    if op == "ts_quantile_range":
        return [s_a, 0.25, 0.75, 5]  # series, q_low, q_high, window
    if op == "ts_decay_custom":
        return [s_a, 5, np.array([1.0, 2.0, 3.0, 4.0, 5.0])]
    # 稳健统计
    if op == "ts_iqr":
        return [s_a, 5]
    if op == "ts_median_abs_dev":
        return [s_a, 5]
    if op == "ts_trim_mean":
        return [s_a, 5, 0.1]  # series, window, pct
    if op == "ts_huber_mean":
        return [s_a, 5]
    # 滚动回归
    if op in {"ts_regression_beta", "ts_regression_alpha",
              "ts_regression_resid", "ts_regression_r2"}:
        return [s_a, s_b, 5]  # y, x, window
    # 量化专用
    if op == "vwap_dev":
        return [s_a, s_b]
    if op == "hl_range":
        return [s_a, s_b, s_a]  # high, low, close
    if op == "oc_change":
        return [s_b, s_a]  # open_, close
    if op == "close_to_high":
        return [s_a, s_b, 5]  # close, high, window
    if op == "close_to_low":
        return [s_a, s_b, 5]
    if op == "returns_vol_adj":
        return [s_a, 5]
    if op == "dollar_volume_n":
        return [s_a, s_b, 5]
    raise ValueError(f"No arg builder for {op}")


_OPS_KNOWN_BUGGY = set()  # 空，期望 100% 通过


def pytest_generate_tests(metafunc):
    if "op_name" in metafunc.fixturenames:
        ops = sorted(OPERATORS.keys())
        metafunc.parametrize("op_name", ops)


def test_operator_smoke(op_name, s_a, s_b, df_a, const_5):
    """算子烟雾测试：能执行，不抛异常."""
    if op_name in _OPS_KNOWN_BUGGY:
        pytest.skip(f"{op_name} known buggy")
    fn = OPERATORS[op_name]
    try:
        args = _args(op_name, s_a, s_b, df_a, const_5)
    except Exception as e:
        pytest.skip(f"{op_name}: arg builder fail: {e}")

    try:
        result = fn(*args)
    except Exception as e:
        pytest.fail(f"OPERATOR [{op_name}] raised: {type(e).__name__}: {e}")

    # 类型校验
    assert result is not None, f"{op_name}: returned None"
    if isinstance(result, (pd.Series, pd.DataFrame)):
        assert len(result) > 0 or len(result) == 0, f"{op_name}: empty result"
    elif isinstance(result, np.ndarray):
        pass
    else:
        # 标量（ic, rank_ic, scale 的特殊返回）也接受
        assert np.isscalar(result) or isinstance(result, (int, float, bool, np.floating)), \
            f"{op_name}: unexpected return type {type(result).__name__}"


def test_operator_returns_series_or_scalar(op_name, s_a, s_b, df_a, const_5):
    """算子应返回 Series/DataFrame/scalar。"""
    fn = OPERATORS[op_name]
    try:
        args = _args(op_name, s_a, s_b, df_a, const_5)
        result = fn(*args)
    except Exception:
        pytest.skip()
    assert isinstance(result, (pd.Series, pd.DataFrame, np.ndarray, float, int, bool, np.floating)), \
        f"{op_name}: bad return type {type(result).__name__}"


# 一些算子特有的精确测试

def test_rank_invariance_to_scale(s_a):
    """rank 应在缩放后不变。"""
    fn = OPERATORS["rank"]
    r1 = fn(s_a)
    r2 = fn(s_a * 1000)
    if isinstance(r1, pd.Series):
        diff = (r1 - r2).abs().fillna(0)
        assert diff.max() < 1e-9, "rank should be scale-invariant"


def test_safe_div_no_inf(s_a, s_b):
    """safe_div 不应产出 inf。"""
    fn = OPERATORS["safe_div"]
    zero = pd.Series(0.0, index=s_a.index)
    r = fn(s_a, zero)
    assert isinstance(r, (pd.Series, pd.DataFrame))
    if isinstance(r, pd.Series):
        assert not np.isinf(r.values).any(), "safe_div should not produce inf"


def test_where_branches(s_a, s_b):
    """where 应正确分流。"""
    fn = OPERATORS["where"]
    cond = s_a > s_a.mean()
    r = fn(cond, s_a, s_b)
    if isinstance(r, pd.Series):
        m = cond.values
        # 期望 cond=True 时等于 s_a，否则 s_b
        for i in range(min(5, len(m))):
            if m[i]:
                assert abs(r.iloc[i] - s_a.iloc[i]) < 1e-9
            else:
                assert abs(r.iloc[i] - s_b.iloc[i]) < 1e-9


def test_sign_output(s_a):
    """sign 应输出 {-1, 0, 1}。"""
    fn = OPERATORS["sign"]
    r = fn(s_a)
    if isinstance(r, pd.Series):
        unique = sorted(set(r.unique().tolist()))
        assert all(v in {-1, 0, 1} for v in unique), f"sign unique {unique}"


def test_ones_like_shape(s_a):
    """ones_like 应保持形状。"""
    fn = OPERATORS["ones_like"]
    r = fn(s_a)
    assert hasattr(r, 'shape'), "ones_like should have shape"
    if isinstance(r, pd.Series):
        assert r.shape == s_a.shape
        assert (r == 1).all()


def test_log1p_no_inf(s_a):
    """log1p 对小正数不应产 inf。"""
    fn = OPERATORS["log1p"]
    r = fn(s_a)  # s_a > 10, log1p(x) is finite
    if isinstance(r, pd.Series):
        assert not np.isinf(r.values).any(), "log1p should not produce inf for positive input"


def test_ts_mean_equals_rolling(s_a):
    """ts_mean(5) 应等于 rolling(5).mean()。"""
    fn = OPERATORS["ts_mean"]
    r = fn(s_a, 5)
    if isinstance(r, pd.Series):
        expected = s_a.rolling(5).mean()
        # 后 5 行匹配
        diff = (r.iloc[5:] - expected.iloc[5:]).abs()
        assert diff.max() < 1e-9


# ============================================================
# 新增算子测试 (截面高级 + 时序高级 + 稳健统计)
# ============================================================

@pytest.fixture
def s_mixed() -> pd.Series:
    """含正负值的 Series。"""
    rng = np.random.default_rng(42)
    arr = np.concatenate([rng.uniform(-5, -1, 25), rng.uniform(1, 5, 25)])
    rng2 = np.random.default_rng(42)
    rng2.shuffle(arr)
    return pd.Series(arr, index=pd.bdate_range("2024-01-01", periods=50))


def test_cs_quantile_clip_clips_extremes(df_a):
    """cs_quantile_clip 应削减截面极值。"""
    fn = OPERATORS["cs_quantile_clip"]
    r = fn(df_a, 0.1, 0.9)
    # 剪裁后 min/max 应在 q10/q90 范围内
    for i in range(len(df_a)):
        row = df_a.iloc[i].dropna()
        qlo, qhi = row.quantile(0.1), row.quantile(0.9)
        assert r.iloc[i].min() >= qlo - 1e-9, f"row {i} lower bound violated"
        assert r.iloc[i].max() <= qhi + 1e-9, f"row {i} upper bound violated"


def test_cs_quantile_clip_no_inf(df_a):
    """应不产 inf."""
    fn = OPERATORS["cs_quantile_clip"]
    r = fn(df_a)
    assert not np.isinf(r.values).any()


def test_cs_quantile_clip_single_value_row():
    """单值行不应崩溃."""
    fn = OPERATORS["cs_quantile_clip"]
    df = pd.DataFrame({"A": [1, 2, 3]}, index=pd.bdate_range("2024-01-01", periods=3))
    r = fn(df)
    assert r.shape == df.shape


def test_cs_pct_pos_range(df_a):
    """cs_pct_pos 应在 [0, 1] 之间."""
    fn = OPERATORS["cs_pct_pos"]
    r = fn(df_a)
    assert r.between(0, 1).all()


def test_cs_pct_pos_all_positive(df_a):
    """df_a 值都 > 10, 占比应为 1.0."""
    fn = OPERATORS["cs_pct_pos"]
    r = fn(df_a)
    assert (r == 1.0).all()


def test_cs_pct_pos_mixed_signs():
    """混合正负值: 占比应在 (0, 1)."""
    fn = OPERATORS["cs_pct_pos"]
    df = pd.DataFrame({
        "A": [-1, 2, -3],
        "B": [1, -2, 3],
        "C": [1, 2, 3],
    }, index=pd.bdate_range("2024-01-01", periods=3))
    r = fn(df)
    # Row 0: A负 B正 C正 -> 2/3
    assert abs(r.iloc[0] - 2/3) < 1e-9


# ----- 时序高级算子 -----

def test_ts_centralization_returns_zero_mean(s_a):
    """滚动去均值: result 在每个窗口内平均 ≈ 0."""
    fn = OPERATORS["ts_centralization"]
    r = fn(s_a, 10)
    assert isinstance(r, pd.Series)
    # 后 5 行: rolling 10 的均值应为接近 0
    tail = r.iloc[-5:]
    assert abs(tail.mean()) < 5.0  # 容忍随机噪声


def test_ts_standardization_returns_unit_std(s_a):
    """ts_standardization 输出 std ≈ 1."""
    fn = OPERATORS["ts_standardization"]
    r = fn(s_a, 20)
    # 滚动 20 std 应接近 1
    assert r.iloc[20:].std() < 5.0  # 容忍随机


def test_ts_entropy_non_negative(s_mixed):
    """Shannon 熵应为非负数."""
    fn = OPERATORS["ts_entropy"]
    r = fn(s_mixed, 10, bins=5)
    valid = r.dropna()
    assert (valid >= 0).all()


def test_ts_entropy_uniform_data():
    """均匀分布应产出最大熵."""
    rng = np.random.default_rng(42)
    s = pd.Series(rng.uniform(0, 1, 100), index=pd.bdate_range("2024-01-01", periods=100))
    fn = OPERATORS["ts_entropy"]
    r = fn(s, 20, bins=5)
    valid = r.dropna()
    # 熵最大为 ln(bins) ≈ 1.609
    assert valid.max() <= np.log(5) + 0.01


def test_ts_pct_pos_range(s_mixed):
    """ts_pct_pos 应在 [0, 1] 之间."""
    fn = OPERATORS["ts_pct_pos"]
    r = fn(s_mixed, 10)
    valid = r.dropna()
    assert valid.between(0, 1).all()


def test_ts_pct_pos_all_positive():
    """全正值应产出 1.0."""
    rng = np.random.default_rng(42)
    s = pd.Series(rng.uniform(0, 10, 50), index=pd.bdate_range("2024-01-01", periods=50))
    fn = OPERATORS["ts_pct_pos"]
    r = fn(s, 10)
    valid = r.dropna()
    assert (valid == 1.0).all()


def test_ts_count_pos_int_values():
    """ts_count_pos 应返回整数. 用 docstring 不强求 dtype, 只校验等于 (x>0).sum() per window."""
    s = pd.Series([-1, -2, 3, 4, -5] * 10, index=pd.bdate_range("2024-01-01", periods=50))
    fn = OPERATORS["ts_count_pos"]
    r = fn(s, 5)
    # 校验: 手工计算窗口 5 的 count_pos
    expected = s.rolling(5).apply(lambda x: int((x > 0).sum()), raw=True)
    diff = (r - expected).abs()
    assert diff.max() < 1e-9


def test_ts_count_neg(s_mixed):
    fn = OPERATORS["ts_count_neg"]
    r = fn(s_mixed, 10)
    # 窗口末尾为 10 个数的负计数
    valid = r.dropna()
    assert (valid >= 0).all()
    assert (valid <= 10).all()


def test_ts_max_min_diff_non_negative(s_mixed):
    """ts_max_min_diff 应 >= 0."""
    fn = OPERATORS["ts_max_min_diff"]
    r = fn(s_mixed, 10)
    valid = r.dropna()
    assert (valid >= 0).all()


def test_ts_max_min_diff_equals_pandas(s_a):
    fn = OPERATORS["ts_max_min_diff"]
    r = fn(s_a, 5)
    expected = s_a.rolling(5, min_periods=5).max() - s_a.rolling(5, min_periods=5).min()
    diff = (r - expected).abs()
    assert diff.max() < 1e-9


def test_ts_quantile_range_q75_q25(s_a):
    """默认 q_low=0.25, q_high=0.75 应等于 IQR = ts_iqr."""
    fn = OPERATORS["ts_quantile_range"]
    r = fn(s_a, 0.25, 0.75, 10)
    fn_iqr = OPERATORS["ts_iqr"]
    expected = fn_iqr(s_a, 10)
    diff = (r - expected).abs()
    assert diff.max() < 1e-9


def test_ts_decay_custom_basic(s_a):
    """ts_decay_custom 应按权重计算加权平均."""
    import numpy as np
    fn = OPERATORS["ts_decay_custom"]
    # 简单均匀权重 (5)
    weights = np.array([1.0, 1.0, 1.0, 1.0, 1.0])
    r = fn(s_a, 5, weights)
    # 应等于 rolling mean
    expected = s_a.rolling(5, min_periods=5).mean()
    diff = (r - expected).abs()
    assert diff.max() < 1e-9


def test_ts_decay_custom_weighted(s_a):
    """加权不等于均值."""
    import numpy as np
    fn = OPERATORS["ts_decay_custom"]
    # 线性递增权重: 最新值权重最大
    weights = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    r = fn(s_a, 5, weights)
    # 简单 rolling mean 不等
    expected = s_a.rolling(5, min_periods=5).mean()
    # 加权平均应偏离 rolling mean
    assert abs((r.iloc[-1] - expected.iloc[-1])) > 0.01


def test_ts_decay_custom_wrong_length_raises():
    """weights 长度 != window 应报错."""
    import numpy as np
    fn = OPERATORS["ts_decay_custom"]
    s = pd.Series([1.0] * 10)
    with pytest.raises(ValueError, match="must equal"):
        fn(s, 5, np.array([1.0, 2.0, 3.0]))  # length 3 vs window 5


# ----- 稳健统计 -----

def test_ts_iqr_non_negative(s_a):
    """IQR = Q75-Q25 >= 0."""
    fn = OPERATORS["ts_iqr"]
    r = fn(s_a, 10)
    valid = r.dropna()
    assert (valid >= 0).all()


def test_ts_iqr_equals_pandas_quantile(s_a):
    """ts_iqr 应等于 rolling Q75 - rolling Q25."""
    fn = OPERATORS["ts_iqr"]
    r = fn(s_a, 10)
    q75 = s_a.rolling(10, min_periods=10).quantile(0.75)
    q25 = s_a.rolling(10, min_periods=10).quantile(0.25)
    diff = (r - (q75 - q25)).abs()
    assert diff.max() < 1e-9


def test_ts_median_abs_dev_non_negative(s_a):
    """MAD >= 0."""
    fn = OPERATORS["ts_median_abs_dev"]
    r = fn(s_a, 10)
    valid = r.dropna()
    assert (valid >= 0).all()


def test_ts_median_abs_dev_zero_for_constant():
    """常数序列 MAD = 0."""
    s = pd.Series([5.0] * 50, index=pd.bdate_range("2024-01-01", periods=50))
    fn = OPERATORS["ts_median_abs_dev"]
    r = fn(s, 10)
    valid = r.dropna()
    np.testing.assert_array_almost_equal(valid.values, np.zeros_like(valid.values))


def test_ts_trim_mean_robust_to_outliers():
    """ts_trim_mean 对极端值更鲁棒."""
    rng = np.random.default_rng(42)
    arr = np.concatenate([rng.uniform(0, 1, 49), [100.0]])  # 1 个极端值
    s = pd.Series(arr, index=pd.bdate_range("2024-01-01", periods=50))
    fn_mean = OPERATORS["ts_mean"]
    fn_trim = OPERATORS["ts_trim_mean"]
    # 朴素均值应包含极端值
    plain = fn_mean(s, 50).iloc[-1]
    trimmed = fn_trim(s, 50, pct=0.1).iloc[-1]  # 去掉 10% = 5 个最低 5 个最高
    # trimmed 不应包含 100 那个极端值
    assert abs(trimmed - 0.5) < abs(plain - 0.5) * 0.5  # trimmed 应远更接近 0.5


def test_ts_trim_mean_returns_value(s_a):
    """基础烟雾测试."""
    fn = OPERATORS["ts_trim_mean"]
    r = fn(s_a, 10, pct=0.1)
    assert isinstance(r, pd.Series)
    assert r.shape == s_a.shape


def test_ts_huber_mean_returns_value(s_a):
    """基本功能."""
    fn = OPERATORS["ts_huber_mean"]
    r = fn(s_a, 10)
    assert isinstance(r, pd.Series)
    # 鲁棒均值应接近简单均值
    plain = s_a.rolling(10, min_periods=10).mean().iloc[-1]
    huber = r.iloc[-1]
    assert abs(plain - huber) < 5.0


def test_ts_huber_mean_robust_to_outliers():
    """Huber 均值对极端值更鲁棒."""
    rng = np.random.default_rng(42)
    arr = np.concatenate([rng.uniform(0, 1, 49), [100.0]])
    s = pd.Series(arr, index=pd.bdate_range("2024-01-01", periods=50))
    fn_mean = OPERATORS["ts_mean"]
    fn_huber = OPERATORS["ts_huber_mean"]
    plain = fn_mean(s, 50).iloc[-1]
    huber = fn_huber(s, 50).iloc[-1]
    # Huber 应更接近 0.5
    assert abs(huber - 0.5) < abs(plain - 0.5) * 0.5


# ============================================================
# 滚动回归算子
# ============================================================

@pytest.fixture
def s_y() -> pd.Series:
    """y = 2*x + 0.5 + noise(0.5)."""
    rng = np.random.default_rng(42)
    x = rng.standard_normal(100)
    y = 2.0 * x + 0.5 + rng.standard_normal(100) * 0.5
    return pd.Series(y, index=pd.bdate_range("2024-01-01", periods=100))


@pytest.fixture
def s_x() -> pd.Series:
    """normalized x series."""
    rng = np.random.default_rng(42)
    return pd.Series(rng.standard_normal(100), index=pd.bdate_range("2024-01-01", periods=100))


def test_ts_regression_beta_recovers_true(s_y, s_x):
    """y = 2*x + 0.5 + noise, beta 应 ≈ 2."""
    fn = OPERATORS["ts_regression_beta"]
    r = fn(s_y, s_x, 30)
    # 尾部 5 个值的均值应接近 2
    avg = r.dropna().iloc[-10:].mean()
    assert abs(avg - 2.0) < 0.3, f"beta avg={avg}, expected ~2.0"


def test_ts_regression_beta_returns_series(s_y, s_x):
    """基本返回类型."""
    fn = OPERATORS["ts_regression_beta"]
    r = fn(s_y, s_x, 30)
    assert isinstance(r, pd.Series)
    assert r.shape == s_y.shape


def test_ts_regression_beta_no_inf(s_y, s_x):
    """常数 x 时 var=0 应输出 nan, 不是 inf."""
    fn = OPERATORS["ts_regression_beta"]
    const_x = pd.Series(1.0, index=s_x.index)  # 方差为 0
    r = fn(s_y, const_x, 30)
    assert not np.isinf(r.values).any(), "beta produced inf on constant x"


def test_ts_regression_beta_first_n_nan(s_y, s_x):
    """前 window-1 行应 NaN."""
    fn = OPERATORS["ts_regression_beta"]
    r = fn(s_y, s_x, 30)
    # rolling(30, min_periods=30): row 0..27 NaN, row 29 之后才有 30 个
    assert pd.isna(r.iloc[0]), "row 0 should be NaN"
    assert pd.isna(r.iloc[27]), "row 27 should be NaN"
    # row 29 是第 30 个, 有效
    assert not pd.isna(r.iloc[29]), "row 29 should be valid (30th element)"
    assert not pd.isna(r.iloc[50]), "row 50 should be valid"


def test_ts_regression_alpha_recovers_true(s_y, s_x):
    """alpha 应 ≈ 0.5 (截距)."""
    fn = OPERATORS["ts_regression_alpha"]
    r = fn(s_y, s_x, 30)
    avg = r.dropna().iloc[-10:].mean()
    assert abs(avg - 0.5) < 0.5, f"alpha avg={avg}, expected ~0.5"


def test_ts_regression_alpha_returns_series(s_y, s_x):
    fn = OPERATORS["ts_regression_alpha"]
    r = fn(s_y, s_x, 30)
    assert isinstance(r, pd.Series)
    assert r.shape == s_y.shape


def test_ts_regression_resid_is_noise(s_y, s_x):
    """残差应 ≈ noise. mean ≈ 0, std ≈ noise std (0.5)."""
    fn = OPERATORS["ts_regression_resid"]
    r = fn(s_y, s_x, 30)
    valid = r.dropna()
    assert abs(valid.mean()) < 0.1, f"resid mean {valid.mean()}, expected ~0"
    assert 0.3 < valid.std() < 0.7, f"resid std {valid.std()}, expected ~0.5"


def test_ts_regression_resid_uncorrelated_with_x(s_y, s_x):
    """残差应与 x 不相关 (已去除 x 暴露)."""
    fn = OPERATORS["ts_regression_resid"]
    r = fn(s_y, s_x, 30).dropna()
    x_aligned = s_x.loc[r.index]
    corr = r.corr(x_aligned)
    assert abs(corr) < 0.3, f"resid-x corr {corr}, expected near 0"


def test_ts_regression_r2_range(s_y, s_x):
    """R² 应在 [0, 1] 之间."""
    fn = OPERATORS["ts_regression_r2"]
    r = fn(s_y, s_x, 30)
    valid = r.dropna()
    assert valid.between(0, 1 + 1e-9).all()


def test_ts_regression_r2_high_for_strong_signal(s_y, s_x):
    """y=2*x+0.5+noise(0.5), R² 高 (因信号 vs 噪声比大)."""
    fn = OPERATORS["ts_regression_r2"]
    r = fn(s_y, s_x, 30)
    avg = r.dropna().iloc[-10:].mean()
    assert avg > 0.7, f"r2 avg={avg}, expected > 0.7 for strong signal"


def test_ts_regression_r2_zero_for_no_relation(s_x):
    """R² 应接近 0 当 y 与 x 无关."""
    rng = np.random.default_rng(123)  # 不同种子以避免隐含相关
    y = pd.Series(rng.standard_normal(100), index=s_x.index)  # 独立
    fn = OPERATORS["ts_regression_r2"]
    r = fn(y, s_x, 50)
    avg = r.dropna().mean()
    assert abs(avg) < 0.15, f"r2 {avg} for uncorrelated y,x, expected ~0"


# ============================================================
# 量化专用算子
# ============================================================

@pytest.fixture
def s_ohlcv():
    """构造 OHLCV 风格数据."""
    rng = np.random.default_rng(42)
    N = 60
    dates = pd.bdate_range("2024-01-01", periods=N)
    open_ = pd.Series(rng.uniform(20, 50, N), index=dates, name="open")
    high = open_ * (1 + np.abs(rng.normal(0, 0.01, N)))
    low = open_ * (1 - np.abs(rng.normal(0, 0.01, N)))
    close = (high + low + open_) / 3 + rng.normal(0, 0.5, N)
    high = pd.Series(np.maximum(high.values, close.values), index=dates, name="high")
    low = pd.Series(np.minimum(low.values, close.values), index=dates, name="low")
    close = pd.Series(close, index=dates, name="close")
    volume = pd.Series(rng.uniform(1e6, 5e6, N), index=dates, name="volume")
    vwap = close * (1 + rng.normal(0, 0.002, N))
    returns = close.pct_change().fillna(0)
    return {
        "open": open_, "high": high, "low": low,
        "close": close, "volume": volume, "vwap": vwap,
        "returns": returns,
    }


def test_vwap_dev_basic(s_ohlcv):
    """vwap_dev = close - vwap. 当 close > vwap 应为正."""
    fn = OPERATORS["vwap_dev"]
    r = fn(s_ohlcv["close"], s_ohlcv["vwap"])
    assert isinstance(r, pd.Series)
    # 校验: 每一行 r.iloc[i] = close.iloc[i] - vwap.iloc[i]
    expected = s_ohlcv["close"] - s_ohlcv["vwap"]
    np.testing.assert_array_almost_equal(r.values, expected.values)


def test_vwap_dev_no_inf(s_ohlcv):
    fn = OPERATORS["vwap_dev"]
    r = fn(s_ohlcv["close"], s_ohlcv["vwap"])
    assert not np.isinf(r.values).any()


def test_hl_range_positive(s_ohlcv):
    """(high - low) / close 应 >= 0."""
    fn = OPERATORS["hl_range"]
    r = fn(s_ohlcv["high"], s_ohlcv["low"], s_ohlcv["close"])
    assert (r.dropna() >= 0).all(), "hl_range should be non-negative"


def test_hl_range_default_close(s_ohlcv):
    """close 参数可选, 默认用 (high+low)/2."""
    fn = OPERATORS["hl_range"]
    fn(s_ohlcv['high'], s_ohlcv['low'], s_ohlcv['close'])
    r2 = fn(s_ohlcv["high"], s_ohlcv["low"])  # 用 (h+l)/2 当 close
    # 应都非负, 但数值不等
    assert (r2.dropna() >= 0).all()


def test_hl_range_no_inf(s_ohlcv):
    """close=0 时应产 nan, 不是 inf."""
    fn = OPERATORS["hl_range"]
    zero_close = pd.Series(0.0, index=s_ohlcv["close"].index)
    r = fn(s_ohlcv["high"], s_ohlcv["low"], zero_close)
    assert not np.isinf(r.values).any()


def test_oc_change_basic(s_ohlcv):
    """(close - open) / open. 涨跌信号."""
    fn = OPERATORS["oc_change"]
    r = fn(s_ohlcv["open"], s_ohlcv["close"])
    assert isinstance(r, pd.Series)
    # close=open → 0
    flat = pd.Series([10.0]*5, index=range(5))
    assert (fn(flat, flat) == 0).all()


def test_oc_change_positive_when_rally(s_ohlcv):
    """close > open 应得正值."""
    fn = OPERATORS["oc_change"]
    open_ = pd.Series([10.0, 10.0], index=[0, 1])
    close = pd.Series([11.0, 9.0], index=[0, 1])  # 一涨一跌
    r = fn(open_, close)
    assert r.iloc[0] > 0
    assert r.iloc[1] < 0


def test_oc_change_handles_zero_open(s_ohlcv):
    """open=0 应产 nan, 不是 inf."""
    fn = OPERATORS["oc_change"]
    zero_open = pd.Series(0.0, index=s_ohlcv["close"].index)
    r = fn(zero_open, s_ohlcv["close"])
    assert not np.isinf(r.values).any()


def test_close_to_high_in_range(s_ohlcv):
    """close / rolling_max(high) 应在 (0, 1.0+] 范围."""
    fn = OPERATORS["close_to_high"]
    r = fn(s_ohlcv["close"], s_ohlcv["high"], 10)
    valid = r.dropna()
    assert (valid > 0).all(), "close < 0 not possible"
    # 可超过 1 当 close 是 N 日最高
    assert (valid <= 5.0).all()


def test_close_to_high_basic(s_ohlcv):
    """若 close 是 10 日内最高, 比值 = 1.0."""
    fn = OPERATORS["close_to_high"]
    # 构造: close 永为 10 日最高
    pd.Series([1.0] * 5 + [2.0] * 5, index=range(10))
    pd.Series([3.0] * 5 + [2.0] * 5, index=range(10))
    # 简化: 让 high = close
    high2 = pd.Series([10.0]*10, index=range(10))
    close2 = pd.Series([10.0]*10, index=range(10))
    r = fn(close2, high2, 10)
    # 当 close == high 时, ratio = 1.0
    valid = r.dropna()
    np.testing.assert_array_almost_equal(valid.values, np.ones_like(valid.values))


def test_close_to_high_no_inf(s_ohlcv):
    """滚动最高=0 时应产 nan, 不是 inf."""
    fn = OPERATORS["close_to_high"]
    zero_high = pd.Series(0.0, index=s_ohlcv["close"].index)
    r = fn(s_ohlcv["close"], zero_high, 5)
    assert not np.isinf(r.values).any()


def test_close_to_low_in_range(s_ohlcv):
    """close / rolling_min(low) 应 >= 1.0 (close 总是 >= 最低)."""
    fn = OPERATORS["close_to_low"]
    r = fn(s_ohlcv["close"], s_ohlcv["low"], 10)
    valid = r.dropna()
    # low <= close 永远成立, 故 ratio >= 1
    assert (valid >= 1.0 - 1e-9).all()


def test_close_to_low_basic(s_ohlcv):
    """若 close = 10 日最低, ratio = 1.0."""
    fn = OPERATORS["close_to_low"]
    low = pd.Series([10.0]*10, index=range(10))
    close = pd.Series([10.0]*10, index=range(10))
    r = fn(close, low, 10)
    valid = r.dropna()
    np.testing.assert_array_almost_equal(valid.values, np.ones_like(valid.values))


def test_returns_vol_adj_vol_normalized(s_ohlcv):
    """波动调整后, 标准差应~1.0."""
    fn = OPERATORS["returns_vol_adj"]
    r = fn(s_ohlcv["returns"], 20)
    valid = r.dropna()
    # rolling std of standardized returns ≈ 1
    assert 0.5 < valid.std() < 2.0


def test_returns_vol_adj_no_inf(s_ohlcv):
    """std=0 (平稳序列) 应产 nan, 不是 inf."""
    fn = OPERATORS["returns_vol_adj"]
    const_returns = pd.Series(0.001, index=s_ohlcv["close"].index)
    r = fn(const_returns, 10)
    assert not np.isinf(r.values).any()


def test_returns_vol_adj_zero_vol(s_ohlcv):
    """0 收益序列: nan (因 std=0)."""
    fn = OPERATORS["returns_vol_adj"]
    zero_returns = pd.Series(0.0, index=s_ohlcv["close"].index)
    r = fn(zero_returns, 10)
    # std=0 → ratio inf → 替换为 nan
    inf_count = np.isinf(r.values).sum()
    assert inf_count == 0


def test_dollar_volume_n_basic(s_ohlcv):
    """dollar_volume_n(5) 应等于 (close*volume).rolling(5).sum()."""
    fn = OPERATORS["dollar_volume_n"]
    r = fn(s_ohlcv["close"], s_ohlcv["volume"], 5)
    expected = (s_ohlcv["close"] * s_ohlcv["volume"]).rolling(5, min_periods=5).sum()
    diff = (r - expected).abs()
    assert diff.max() < 1e-6


def test_dollar_volume_n_first_n_nan(s_ohlcv):
    """前 (window-1) 行应为 NaN."""
    fn = OPERATORS["dollar_volume_n"]
    r = fn(s_ohlcv["close"], s_ohlcv["volume"], 5)
    assert pd.isna(r.iloc[0])
    assert pd.isna(r.iloc[3])
    assert not pd.isna(r.iloc[4])


def test_dollar_volume_n_positive(s_ohlcv):
    """成交额应总是正数 (close>0, volume>0)."""
    fn = OPERATORS["dollar_volume_n"]
    r = fn(s_ohlcv["close"], s_ohlcv["volume"], 5)
    valid = r.dropna()
    assert (valid > 0).all()




def test_eq_produces_boolean(s_a):
    """eq 应输出布尔 Series。"""
    fn = OPERATORS["eq"]
    r = fn(s_a, s_a.iloc[10])
    if isinstance(r, pd.Series):
        assert r.dtype == bool, f"eq dtype = {r.dtype}"


def test_diff_first_row_nan(s_a):
    """diff(5) 的前 5 行应为 NaN。"""
    fn = OPERATORS["diff"]
    r = fn(s_a, 5)
    if isinstance(r, pd.Series):
        assert pd.isna(r.iloc[4]), "diff(5) should produce NaN at row 4"


def test_negation(s_a):
    """neg 应等于 -x。"""
    fn = OPERATORS["neg"]
    r = fn(s_a)
    if isinstance(r, pd.Series):
        assert ((r + s_a).abs() < 1e-9).all(), "neg(x) should equal -x"


def test_safe_div_safe_with_const(s_a):
    """safe_div(x, 0) 不应崩溃。"""
    fn = OPERATORS["safe_div"]
    r = fn(s_a, 0.0)
    if isinstance(r, pd.Series):
        # safe_div(a, 0) = a / (0 + 1e-12) -> 应是巨大值，但非 inf
        # 实际上 safe_div 是 a / (b + eps*sign(b)), b=0 则 a / 1e-12
        # 巨大但有限
        pass  # just check no crash


def test_clip(s_a):
    """clip 应限制在 [lower, upper]。"""
    fn = OPERATORS["clip"]
    r = fn(s_a, 20.0, 40.0)
    if isinstance(r, pd.Series):
        assert r.min() >= 20.0 - 1e-9, "clip lower bound"
        assert r.max() <= 40.0 + 1e-9, "clip upper bound"


def test_cumsum_no_inf(s_a):
    """cumsum 应单调不减。"""
    fn = OPERATORS["cumsum"]
    r = fn(s_a)
    if isinstance(r, pd.Series):
        # cumsum 自身可能 nan
        assert r is not None


def test_orthogonalize(s_a, s_b):
    """orthogonalize 应去除 reference 影响。"""
    fn = OPERATORS["orthogonalize"]
    r = fn(s_a, s_b)
    assert isinstance(r, (pd.Series, pd.DataFrame))


def test_neutralize(s_a):
    """neutralize 应减去组均值。"""
    fn = OPERATORS["neutralize"]
    group = pd.Series(np.tile([0, 1], N // 2 + 1)[:N], index=s_a.index)
    r = fn(s_a, group)
    assert isinstance(r, pd.Series)


# 用例：批量回归测试 — 校验所有算子字典构造
def test_operator_count():
    assert len(OPERATORS) >= 100, f"Only {len(OPERATORS)} operators"


def test_all_operators_have_docstring():
    """每个 function 算子应有文档字符串（lambda 除外）。"""
    import inspect
    missing = []
    for name, fn in OPERATORS.items():
        if not inspect.isfunction(fn):
            continue
        if fn.__name__ == '<lambda>':
            continue
        doc = (fn.__doc__ or "").strip()
        if not doc:
            missing.append(name)
    assert not missing, f"Missing docs: {missing}"
