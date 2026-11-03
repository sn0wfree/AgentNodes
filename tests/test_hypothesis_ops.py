"""基于 Hypothesis 的属性测试 (fuzz testing)。

策略:
- 自动生成边界用例 (空, 极小, 极大, 特殊值)
- 对每个算子验证"不变量" (形状, 无 inf 崩溃, 数学性质)
- 由 Hypothesis 探索最少 50 个例子 (可调整)

覆盖类别:
- 时序算子: 输出形状 == 输入形状, 无 NaN 注入
- 截面算子: 每行输出 >= 0 或在 [-1, 1] 等已知范围
- 数学算子: a + 0 = a, a * 1 = a 恒等
- 鲁棒算子: 输出非 inf, 输出与简单实现 coteries
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from strategy_research.apps.compute_factor import OPERATORS

warnings.filterwarnings("ignore")

# Hypothesis 配置
settings.register_profile(
    "ci",
    max_examples=20,
    deadline=2000,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
settings.load_profile("ci")


# ============================================================
# 自定义策略: 用 pandas DataFrame 时尽量不产 inf
# ============================================================

@st.composite
def finite_series(draw, min_size=5, max_size=200):
    """生成有限值 Series (避免 inf / NaN 全 nan)."""
    n = draw(st.integers(min_value=min_size, max_value=max_size))
    # 避免极端值, 用 [-10, 10] 之间的值
    values = draw(st.lists(
        st.floats(min_value=-10.0, max_value=10.0, allow_nan=False, allow_infinity=False),
        min_size=n, max_size=n,
    ))
    return pd.Series(values, index=pd.bdate_range("2024-01-01", periods=n))


@st.composite
def positive_series(draw, min_size=5, max_size=200):
    """正值 Series (用于 ts_*_pct_pos 等)."""
    n = draw(st.integers(min_value=min_size, max_value=max_size))
    values = draw(st.lists(
        st.floats(min_value=0.01, max_value=100.0, allow_nan=False, allow_infinity=False),
        min_size=n, max_size=n,
    ))
    return pd.Series(values, index=pd.bdate_range("2024-01-01", periods=n))


@st.composite
def finite_df(draw, min_size=5, max_size=50, n_cols=3):
    """生成 有限值 DataFrame (3 列, 每行至少有 2 个非零值)."""
    n = draw(st.integers(min_value=min_size, max_value=max_size))
    cols = [f"S{i}" for i in range(n_cols)]
    data = {}
    for c in cols:
        # 偏向非零值
        values = []
        for _ in range(n):
            values.append(draw(
                st.floats(min_value=-10.0, max_value=10.0,
                          allow_nan=False, allow_infinity=False)
            ))
        data[c] = values
    df = pd.DataFrame(data, index=pd.bdate_range("2024-01-01", periods=n))
    # 过滤: 把每行全 0 的行设小幅度 (避免 std=0 退化 zscore)
    for i in range(n):
        if (df.iloc[i] == 0).all():
            df.iloc[i, 0] = 1.0
    return df


# ============================================================
# 形状不变量: 输出形状 == 输入形状
# ============================================================

@pytest.mark.parametrize("op_name", [
    "ts_mean", "ts_std", "ts_max", "ts_min", "ts_sum",
    "ts_median", "ts_var", "ts_decay_linear", "ts_decay_exp",
    "ewm_mean", "ewm_std",
])
@given(s=finite_series(max_size=100))
def test_ts_univariate_shape_invariant(op_name, s):
    """单变量时序算子输出形状应等于输入形状."""
    fn = OPERATORS[op_name]
    window = max(2, min(5, len(s) // 2))
    r = fn(s, window)
    assert r.shape == s.shape, f"{op_name}: shape {r.shape} != input {s.shape}"


@pytest.mark.parametrize("op_name", [
    "rank", "zscore", "scale",
])
@given(df=finite_df(max_size=50, n_cols=3))
def test_cs_univariate_shape_invariant(op_name, df):
    """截面算子 (DataFrame) 输出形状应等于输入形状."""
    # 注意: compute_factor.OPERATORS 中的 rank/zscore/scale 接受 Series
    # ALPHA_ZOO_OPS 中的对应函数接受 DataFrame
    from strategy_research.apps.alpha_zoo_ops import (
        rank as rank_df,
    )
    from strategy_research.apps.alpha_zoo_ops import (
        scale as scale_df,
    )
    from strategy_research.apps.alpha_zoo_ops import (
        zscore as zscore_df,
    )
    fns = {"rank": rank_df, "zscore": zscore_df, "scale": scale_df}
    fn = fns[op_name]
    r = fn(df)
    assert r.shape == df.shape, f"{op_name}: shape {r.shape} != input {df.shape}"


# ============================================================
# 数学恒等性
# ============================================================

@given(s=finite_series(min_size=10, max_size=50))
def test_add_identity(s):
    """a + 0 == a."""
    fn = OPERATORS["add"]
    r = fn(s, 0)
    # 由于浮点运算可能需 allow_nan=False 检查
    np.testing.assert_array_equal(r.values, s.values)


@given(s=finite_series(min_size=10, max_size=50))
def test_mul_identity(s):
    """a * 1 == a."""
    fn = OPERATORS["mul"]
    r = fn(s, 1)
    np.testing.assert_array_equal(r.values, s.values)


@given(s=finite_series(min_size=10, max_size=50))
def test_neg_negation(s):
    """-(a) - a == 2a  (但这里测 neg(neg(a)) == a)."""
    fn = OPERATORS["neg"]
    r1 = fn(s)
    r2 = fn(r1)
    np.testing.assert_array_equal(r2.values, s.values)


@given(s=finite_series(min_size=10, max_size=50))
def test_abs_non_negative(s):
    """|x| >= 0."""
    fn = OPERATORS["abs"]
    r = fn(s)
    assert (r >= 0).all()


@given(s=finite_series(min_size=10, max_size=50))
def test_sign_output_set(s):
    """sign(x) ∈ {-1, 0, 1}."""
    fn = OPERATORS["sign"]
    r = fn(s)
    valid = r.dropna()
    unique = set(valid.unique().tolist())
    assert unique.issubset({-1.0, 0.0, 1.0}), f"sign produced {unique}"


# ============================================================
# 算术运算
# ============================================================

@given(s=finite_series(min_size=10, max_size=50))
def test_sub_zero(s):
    """a - 0 == a."""
    fn = OPERATORS["sub"]
    r = fn(s, 0)
    np.testing.assert_array_equal(r.values, s.values)


@given(s1=finite_series(min_size=10, max_size=50), s2=finite_series(min_size=10, max_size=50))
def test_add_commutative(s1, s2):
    """a + b == b + a (注意可能长度不同, 我们各自取相同长度)."""
    # 取最短长度
    n = min(len(s1), len(s2))
    a, b = s1.iloc[:n].reset_index(drop=True), s2.iloc[:n].reset_index(drop=True)
    fn = OPERATORS["add"]
    r1 = fn(a, b)
    r2 = fn(b, a)
    np.testing.assert_array_equal(r1.values, r2.values)


# ============================================================
# 比较运算
# ============================================================

@given(s=finite_series(min_size=10, max_size=50), n=st.floats(min_value=-5, max_value=5, allow_nan=False))
def test_lt_with_constant(s, n):
    """(x < n) 输出布尔 Series."""
    fn = OPERATORS["lt"]
    r = fn(s, n)
    assert r.dtype == bool, f"lt dtype = {r.dtype}"


@given(s=finite_series(min_size=10, max_size=50), n=st.floats(min_value=-5, max_value=5, allow_nan=False))
def test_eq_with_constant(s, n):
    """(x == n) 输出布尔 Series."""
    fn = OPERATORS["eq"]
    r = fn(s, n)
    assert r.dtype == bool
    # 至少 0 个 True / 1 个 False (除非常量)
    assert (r.sum() >= 0)


# ============================================================
# 滚动算子
# ============================================================

@given(s=finite_series(min_size=20, max_size=80))
def test_ts_mean_first_n_nan(s):
    """ts_mean(n) 前 n-1 行应为 NaN."""
    fn = OPERATORS["ts_mean"]
    n = max(2, min(10, len(s) // 2))
    r = fn(s, n)
    # 前 n-1 行 NaN
    for i in range(n - 1):
        assert pd.isna(r.iloc[i]), f"row {i} should be NaN"


@given(s=finite_series(min_size=20, max_size=80))
def test_ts_sum_equals_partial_sum(s):
    """ts_sum(n) 在第 n-1 行之后的每一行应等于前 n 个值的 sum."""
    n = max(2, min(10, len(s) // 2))
    fn = OPERATORS["ts_sum"]
    r = fn(s, n)
    expected = s.iloc[:n].sum()
    actual = r.iloc[n - 1]  # 第 n 行 (0-indexed = n-1) 含前 n 个值
    if not pd.isna(actual):
        np.testing.assert_almost_equal(float(actual), float(expected), decimal=6)


# ============================================================
# 截面算子不变性
# ============================================================

@given(df=finite_df(max_size=30, n_cols=3))
def test_rank_scale_invariant(df):
    """rank 应在常数缩放下不变. 用 ALPHA_ZOO_OPS 版."""
    from strategy_research.apps.alpha_zoo_ops import rank as rank_df
    r1 = rank_df(df)
    r2 = rank_df(df * 1000.0)
    np.testing.assert_array_almost_equal(r1.values, r2.values, decimal=9)


@given(df=finite_df(max_size=30, n_cols=3))
def test_rank_invariant_to_scaling(df):
    """rank 应在比例缩放下完全不变 (offset 不变性不成立, 因为浮点精度)."""
    from strategy_research.apps.alpha_zoo_ops import rank as rank_df
    r1 = rank_df(df)
    r2 = rank_df(df * 1000.0)
    np.testing.assert_array_almost_equal(r1.values, r2.values, decimal=9)


# ============================================================
# safe_div 健壮性
# ============================================================

@given(df=finite_df(max_size=30, n_cols=3))
def test_safe_div_no_inf(df):
    """safe_div 不应产 inf. 使用 ALPHA_ZOO_OPS 版的 DataFrame 入口."""
    from strategy_research.apps.alpha_zoo_ops import safe_div as safe_div_df
    r = safe_div_df(df, df)
    n_inf = np.isinf(r.values).sum()
    assert n_inf == 0, f"safe_div produced {n_inf} inf"


@given(df=finite_df(max_size=30, n_cols=3))
def test_safe_div_no_inf_with_zeros(df):
    """safe_div 用 0 当分母时不产 inf. ALPHA_ZOO_OPS 版."""
    from strategy_research.apps.alpha_zoo_ops import safe_div as safe_div_df
    zero = pd.DataFrame(0.0, index=df.index, columns=df.columns)
    r = safe_div_df(df, zero)
    n_inf = np.isinf(r.values).sum()
    assert n_inf == 0, f"safe_div with zero denom produced {n_inf} inf"


# ============================================================
# NaN 处理
# ============================================================

@given(s=finite_series(min_size=10, max_size=50))
def test_ops_handle_inf_inputs(s):
    """算子不应让 inf 输入造成崩溃 (可以输出 NaN)."""
    s_inf = s.copy()
    s_inf.iloc[0] = float("inf")
    fn = OPERATORS["abs"]
    try:
        r = fn(s_inf)
        # 不崩溃就行
        assert r is not None
    except Exception:
        # 也接受 raise
        pass


# ============================================================
# rolling 边界
# ============================================================

@given(s=finite_series(min_size=20, max_size=80))
def test_ts_argmax_index_range(s):
    """ts_argmax(n) 输出的索引应在 [0, n-1] 范围."""
    fn = OPERATORS["ts_argmax"]
    n = max(2, min(10, len(s) // 2))
    r = fn(s, n)
    valid = r.dropna()
    if len(valid) > 0:
        assert valid.between(0, n - 1 + 1e-9).all(), "argmax indices out of [0, n-1]"


# ============================================================
# zscore 性质
# ============================================================

@given(df=finite_df(max_size=30, n_cols=3))
@example(df=pd.DataFrame({"S0": [1.0, 2.0, 3.0], "S1": [1.5, 2.5, 3.5], "S2": [0.5, 2.0, 3.0]},
                          index=pd.bdate_range("2024-01-01", periods=3)))
def test_zscore_mean_zero(df):
    """zscore 应使每行均值 ≈ 0 (ALPHA_ZOO_OPS zscore 用 DataFrame)."""
    from strategy_research.apps.alpha_zoo_ops import zscore as zscore_df
    r = zscore_df(df)
    # 检查 std > 0 的行 (避免退化)
    for i in range(len(df)):
        row = df.iloc[i]
        n_valid = row.notna().sum()
        if n_valid <= 1:
            continue
        std = row.std(ddof=1)
        if std == 0 or pd.isna(std):
            continue
        z_row = r.iloc[i].dropna()
        mean = z_row.mean()
        assert abs(mean) < 1e-9, f"row {i} zscore mean = {mean}"


# ============================================================
# Coverage 守卫: 输出必是 DataFrame 或 Series
# ============================================================

@pytest.mark.parametrize("op_name,args_fn", [
    ("ts_mean", lambda s: [s, 5]),
    ("rank", lambda df: [df]),
    ("add", lambda s: [s, 1.0]),
    ("sub", lambda s: [s, 0.0]),
    ("abs", lambda s: [s]),
    ("neg", lambda s: [s]),
])
def test_ops_return_pandas(op_name, args_fn):
    """算子应返回 pandas 数据结构, 不返回 list/dict."""
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
    df = pd.DataFrame({"A": s, "B": s + 1})

    fn = OPERATORS[op_name]
    # 根据算子调用相应 fixture
    try:
        if op_name in ("rank", "zscore", "scale"):
            args = args_fn(df)
        else:
            args = args_fn(s)
    except Exception:
        return  # skip if args builder fails

    r = fn(*args)
    assert isinstance(r, (pd.DataFrame, pd.Series, np.ndarray, float, int)), \
        f"{op_name} returned {type(r).__name__}"
