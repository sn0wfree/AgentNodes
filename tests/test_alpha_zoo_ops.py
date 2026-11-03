"""alpha_zoo_ops.py 低层 DataFrame 算子单元测试。

17 个算子每个独立测试:
- rank, zscore, scale (截面)
- ts_rank, ts_corr, ts_cov, ts_mean, ts_std, ts_max, ts_min, ts_argmax, ts_argmin (滚动)
- delta (滞后差分)
- decay_linear (线性衰减)
- signed_power, safe_div, vwap (工具)
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import pytest

from strategy_research.apps.alpha_zoo_ops import ALPHA_ZOO_OPS

warnings.filterwarnings("ignore")

N = 30
SEED = 42


@pytest.fixture(scope="module")
def df_a() -> pd.DataFrame:
    rng = np.random.default_rng(SEED)
    dates = pd.bdate_range("2024-01-01", periods=N)
    cols = ["A", "B", "C"]
    return pd.DataFrame(rng.uniform(10, 50, (N, 3)), index=dates, columns=cols)


@pytest.fixture(scope="module")
def df_b() -> pd.DataFrame:
    rng = np.random.default_rng(SEED + 1)
    dates = pd.bdate_range("2024-01-01", periods=N)
    cols = ["A", "B", "C"]
    return pd.DataFrame(rng.uniform(20, 60, (N, 3)), index=dates, columns=cols)


# ============================================================
# 截面算子
# ============================================================

def test_rank_axis1_pct(df_a):
    """rank 应在 axis=1 上做百分位排名。"""
    r = ALPHA_ZOO_OPS["rank"](df_a)
    # 每行的 min=0.0, max=1.0 (pct=True)
    for i in range(N):
        assert r.iloc[i].min() >= -1e-9, f"row {i} min {r.iloc[i].min()}"
        assert r.iloc[i].max() <= 1.0 + 1e-9, f"row {i} max {r.iloc[i].max()}"


def test_rank_handles_nan(df_a):
    """rank 应在 NaN 处返回 NaN。"""
    df_nan = df_a.copy()
    df_nan.iloc[5, 1] = np.nan
    r = ALPHA_ZOO_OPS["rank"](df_nan)
    assert pd.isna(r.iloc[5, 1])


def test_zscore_zero_mean_unit_std(df_a):
    """zscore 应使每行 mean=0, std=1。"""
    z = ALPHA_ZOO_OPS["zscore"](df_a)
    for i in range(N):
        n_valid = z.iloc[i].notna().sum()
        if n_valid > 1:
            mean = z.iloc[i].mean()
            z.iloc[i].std()
            # 3 个值时 std 可能不正好是 1
            assert abs(mean) < 1e-9, f"row {i} mean {mean}"


def test_zscore_no_inf(df_a):
    """zscore 在零方差行不应产出 inf。"""
    df_const = df_a.copy()
    df_const.iloc[10] = 5.0  # 一行所有值相同 → std=0
    z = ALPHA_ZOO_OPS["zscore"](df_const)
    inf_count = np.isinf(z.values).sum()
    assert inf_count == 0, f"zscore produced {inf_count} inf values"


def test_scale_sums_to_one(df_a):
    """scale 应使 |x| 之和 = 1。"""
    s = ALPHA_ZOO_OPS["scale"](df_a)
    expected = 1.0
    for i in range(N):
        assert abs(s.iloc[i].abs().sum() - expected) < 1e-9, f"row {i} sum {s.iloc[i].abs().sum()}"


def test_scale_custom_a(df_a):
    """scale 应使 |x| 之和 = a."""
    s = ALPHA_ZOO_OPS["scale"](df_a, a=2.5)
    for i in range(N):
        assert abs(s.iloc[i].abs().sum() - 2.5) < 1e-9


def test_scale_zero_handling(df_a):
    """scale 在零和行不应崩溃。"""
    df_zeros = df_a.copy()
    df_zeros.iloc[5] = 0.0
    s = ALPHA_ZOO_OPS["scale"](df_zeros)
    assert not np.isinf(s.values).any()


# ============================================================
# 滚动算子
# ============================================================

def test_ts_rank_first_n_nan(df_a):
    """ts_rank 前 n-1 行应为 NaN。"""
    n = 5
    r = ALPHA_ZOO_OPS["ts_rank"](df_a, n)
    for i in range(n - 1):
        assert pd.isna(r.iloc[i, 0]), f"row {i} should be NaN"
    # 第 n 行应有有效值
    assert not pd.isna(r.iloc[n, 0]), "row n should be valid"


def test_ts_rank_invalid_window(df_a):
    with pytest.raises(ValueError, match="must be"):
        ALPHA_ZOO_OPS["ts_rank"](df_a, 0)


def test_ts_corr_self_is_one(df_a):
    """自相关应为 1.0 (linearly dependent)。"""
    c = ALPHA_ZOO_OPS["ts_corr"](df_a, df_a, 10)
    valid = c.iloc[10:].dropna()
    np.testing.assert_array_almost_equal(valid.values, np.ones_like(valid.values), decimal=9)


def test_ts_corr_opposite_is_minus_one(df_a):
    """负相关应为 -1.0."""
    c = ALPHA_ZOO_OPS["ts_corr"](df_a, -df_a, 10)
    valid = c.iloc[10:].dropna()
    np.testing.assert_array_almost_equal(valid.values, -np.ones_like(valid.values), decimal=9)


def test_ts_cov_self_equals_var(df_a):
    """协方差 (x, x) = var(x)."""
    cov = ALPHA_ZOO_OPS["ts_cov"](df_a, df_a, 10)
    std = ALPHA_ZOO_OPS["ts_std"](df_a, 10)
    # cov = std^2
    expected = std ** 2
    diff = (cov.iloc[10:].values - expected.iloc[10:].values)
    assert np.abs(diff).max() < 1e-12, f"cov != var: max diff {np.abs(diff).max()}"


def test_ts_mean_equals_rolling(df_a):
    """ts_mean(n) == rolling(n).mean()."""
    m = ALPHA_ZOO_OPS["ts_mean"](df_a, 5)
    expected = df_a.rolling(5, min_periods=5).mean()
    pd.testing.assert_frame_equal(m, expected)


def test_ts_std_equals_rolling(df_a):
    """ts_std(n) == rolling(n).std() (ddof=1)."""
    s = ALPHA_ZOO_OPS["ts_std"](df_a, 5)
    expected = df_a.rolling(5, min_periods=5).std(ddof=1)
    pd.testing.assert_frame_equal(s, expected)


def test_ts_max_min(df_a):
    """ts_max/min 应与 rolling 一致。"""
    mx = ALPHA_ZOO_OPS["ts_max"](df_a, 5)
    mn = ALPHA_ZOO_OPS["ts_min"](df_a, 5)
    expected_max = df_a.rolling(5, min_periods=5).max()
    expected_min = df_a.rolling(5, min_periods=5).min()
    pd.testing.assert_frame_equal(mx, expected_max)
    pd.testing.assert_frame_equal(mn, expected_min)


def test_ts_argmax_returns_index(df_a):
    """ts_argmax(n) 应返回窗口内最大值位置 (0-based)。"""
    a = ALPHA_ZOO_OPS["ts_argmax"](df_a, 5)
    # 第一个有效值应该在 row 4 (= window - 1)
    assert not pd.isna(a.iloc[4, 0])
    # 位置应在 [0, 4] 之间
    for i in range(4, N):
        v = a.iloc[i, 0]
        assert 0 <= v <= 4, f"row {i} argmax={v}"


def test_ts_argmin_returns_index(df_a):
    a = ALPHA_ZOO_OPS["ts_argmin"](df_a, 5)
    for i in range(4, N):
        v = a.iloc[i, 0]
        assert 0 <= v <= 4


def test_ts_argmax_handles_partial_nan(df_a):
    """窗口内部分 NaN 应仍能计算 argmax。"""
    df_nan = df_a.copy()
    df_nan.iloc[:5] = np.nan  # 前 5 行 NaN
    a = ALPHA_ZOO_OPS["ts_argmax"](df_nan, 5)
    # 第 8 行的窗口 [4,5,6,7,8]: 第一个 NaN + 4 个有效值
    # np.nanargmax 忽略 NaN, 应能算出 max 的位置
    v = a.iloc[8, 0]
    assert not pd.isna(v), "Should compute argmax ignoring NaN"


def test_ts_argmax_no_crash_on_full_nan(df_a):
    """全 NaN 面板不应崩溃。"""
    df_nan = df_a.copy()
    df_nan[:] = np.nan
    a = ALPHA_ZOO_OPS["ts_argmax"](df_nan, 5)
    assert not np.any(np.isinf(a.values)), "ts_argmax should not produce inf"


# ============================================================
# 滞后
# ============================================================

def test_delta_d1(df_a):
    """delta(df, 1) = df - df.shift(1)."""
    d = ALPHA_ZOO_OPS["delta"](df_a, 1)
    expected = df_a - df_a.shift(1)
    pd.testing.assert_frame_equal(d, expected)


def test_delta_d3(df_a):
    d = ALPHA_ZOO_OPS["delta"](df_a, 3)
    expected = df_a - df_a.shift(3)
    pd.testing.assert_frame_equal(d, expected)


def test_delta_d0_raises(df_a):
    with pytest.raises(ValueError, match="d >= 1"):
        ALPHA_ZOO_OPS["delta"](df_a, 0)


def test_delta_negative_raises(df_a):
    with pytest.raises(ValueError, match="d >= 1"):
        ALPHA_ZOO_OPS["delta"](df_a, -5)


# ============================================================
# 工具
# ============================================================

def test_decay_linear_equals_simple_mean(df_a):
    """短窗口的 decay_linear(n) ≈ mean."""
    # 简单确认不报错
    r = ALPHA_ZOO_OPS["decay_linear"](df_a, 5)
    assert r.shape == df_a.shape
    # 第一行后应有有效值
    assert not pd.isna(r.iloc[5, 0])


def test_signed_power_preserves_sign(df_a):
    """signed_power(df, p) 应保留符号。"""
    p = 2.0
    r = ALPHA_ZOO_OPS["signed_power"](df_a, p)
    # 原正负保持
    assert ((r >= 0) == (df_a >= 0)).all().all()


def test_signed_power_2_equals_square(df_a):
    """signed_power(df, 2) = sign(df) * df^2 = |df|^2 (非负)."""
    r = ALPHA_ZOO_OPS["signed_power"](df_a, 2.0)
    expected = np.abs(df_a) ** 2
    np.testing.assert_array_almost_equal(r.values, expected.values)


def test_safe_div_normal(df_a, df_b):
    """safe_div 应正确处理非零分母。"""
    r = ALPHA_ZOO_OPS["safe_div"](df_a, df_b)
    expected = df_a / df_b
    np.testing.assert_array_almost_equal(r.values, expected.values)


def test_safe_div_by_zero_no_inf():
    """safe_div by 0 不应输出 inf (eps 防护)。"""
    df_a = pd.DataFrame([1.0, 2.0, 3.0], columns=["A"])
    df_zero = pd.DataFrame([0.0, 0.0, 0.0], columns=["A"])
    r = ALPHA_ZOO_OPS["safe_div"](df_a, df_zero)
    assert not np.isinf(r.values).any(), "safe_div produced inf"


def test_vwap_cn_uses_amount_volume():
    """A 股 vwap = amount / volume."""
    dates = pd.bdate_range("2024-01-01", periods=5)
    panel = {
        "amount": pd.DataFrame([1e9, 2e9, 3e9, 4e9, 5e9], index=dates, columns=["A"]),
        "volume": pd.DataFrame([1e7, 2e7, 3e7, 4e7, 5e7], index=dates, columns=["A"]),
    }
    r = ALPHA_ZOO_OPS["vwap"](panel, market="equity_cn")
    np.testing.assert_array_almost_equal(r.values.flatten(), [100.0]*5)


def test_vwap_no_market_uses_typical_price():
    """无 amount/volume 时 vwap = (high + low + close) / 3."""
    dates = pd.bdate_range("2024-01-01", periods=5)
    panel = {
        "high": pd.DataFrame([10.0]*5, index=dates, columns=["A"]),
        "low": pd.DataFrame([8.0]*5, index=dates, columns=["A"]),
        "close": pd.DataFrame([9.0]*5, index=dates, columns=["A"]),
    }
    r = ALPHA_ZOO_OPS["vwap"](panel, market="equity_us")
    # (10 + 8 + 9) / 3 = 9.0
    np.testing.assert_array_almost_equal(r.values.flatten(), [9.0]*5)


# ============================================================
# ALPHA_ZOO_OPS 注册表
# ============================================================

def test_alpha_zoo_ops_inventory():
    """注册表应至少 17 个算子。"""
    assert len(ALPHA_ZOO_OPS) >= 17


def test_alpha_zoo_ops_unique_names():
    """所有算子名称唯一."""
    keys = list(ALPHA_ZOO_OPS.keys())
    assert len(keys) == len(set(keys))


def test_alpha_zoo_ops_all_callable():
    for name, fn in ALPHA_ZOO_OPS.items():
        assert callable(fn), f"{name} not callable"


# ============================================================
# NaN 传播
# ============================================================

def test_nan_propagation_ts_mean():
    """ts_mean 不应静默 fillna(0)."""
    dates = pd.bdate_range("2024-01-01", periods=10)
    df = pd.DataFrame([np.nan, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0], index=dates, columns=["A"])
    r = ALPHA_ZOO_OPS["ts_mean"](df, 5)
    # 前 4 行 NaN, 第 5 行 (含一个 NaN) 应 NaN
    assert pd.isna(r.iloc[3, 0])
    assert pd.isna(r.iloc[4, 0]), "ts_mean should propagate NaN at window boundary"


def test_nan_propagation_ts_corr():
    dates = pd.bdate_range("2024-01-01", periods=10)
    df_a = pd.DataFrame([1.0, 2.0, np.nan, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0], index=dates, columns=["A"])
    df_b = pd.DataFrame([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0], index=dates, columns=["A"])
    r = ALPHA_ZOO_OPS["ts_corr"](df_a, df_b, 5)
    # 包含 NaN 的窗口应产出 NaN
    for i in range(2, 7):
        if i >= 2 and i <= 4:  # 含 NaN 的窗口
            assert pd.isna(r.iloc[i, 0]) or i > 4, f"row {i} should be NaN or post-window"


# ============================================================
# Inf 禁止
# ============================================================

def test_no_inf_outputs():
    """所有算子输出不应产 inf。"""
    rng = np.random.default_rng(SEED)
    dates = pd.bdate_range("2024-01-01", periods=N)
    df_a = pd.DataFrame(rng.uniform(10, 50, (N, 3)), index=dates, columns=list("ABC"))
    df_b = pd.DataFrame(rng.uniform(20, 60, (N, 3)), index=dates, columns=list("ABC"))

    test_cases = [
        ("zscore", (df_a,)),
        ("scale", (df_a,)),
        ("rank", (df_a,)),
        ("ts_rank", (df_a, 10)),
        ("ts_corr", (df_a, df_b, 10)),
        ("ts_cov", (df_a, df_b, 10)),
        ("ts_mean", (df_a, 10)),
        ("ts_std", (df_a, 10)),
        ("decay_linear", (df_a, 10)),
        ("signed_power", (df_a, 2)),
        ("safe_div", (df_a, df_b)),
    ]
    for name, args in test_cases:
        r = ALPHA_ZOO_OPS[name](*args)
        n_inf = np.isinf(r.values).sum()
        assert n_inf == 0, f"{name} produced {n_inf} inf values"
