"""factor_tools 纯算法单元测试。

覆盖 docs/coverage-90-plan-20261030.md §3.2：该模块原 18.4%，是 C 档最大
单点缺口（371 条）。这里只测**纯 DataFrame 算法**（IC / 分位 / 衰减 /
换手），不触 DuckDB、不触网——工具类的 execute 走集成测试。

意义：这些是因子分析的正确性核心，覆盖它们比覆盖 CLI 排版更有价值。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_research.core.agent.builtin_tools.factor_tools import (
    _build_ret_panel,
    _compute_daily_ic_series,
    _compute_quintile_returns,
    _compute_spearman_ic,
    _resolve_asset_universe,
)


def _panel(assets, dates, seed=0):
    """构造 (dates × assets) 的 DataFrame，用因子/收益同构的确定性数据。"""
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        rng.normal(size=(len(dates), len(assets))),
        index=pd.Index(dates, name="date"),
        columns=list(assets),
    )


# ── _compute_spearman_ic ────────────────────────────────────


def test_spearman_ic_perfect_positive():
    dates = pd.date_range("2024-01-01", periods=3)
    assets = list("ABCDE")
    f = _panel(assets, dates, seed=1)
    ic = _compute_spearman_ic(f, f.copy(), f.index)  # 完全相关
    assert len(ic) == 3
    assert all(abs(x - 1.0) < 1e-9 for x in ic)


def test_spearman_ic_perfect_negative():
    dates = pd.date_range("2024-01-01", periods=2)
    assets = list("ABCDE")
    f = _panel(assets, dates, seed=2)
    ic = _compute_spearman_ic(f, -f, f.index)  # 完全负相关
    assert len(ic) == 2
    assert all(abs(x + 1.0) < 1e-9 for x in ic)


def test_spearman_ic_skips_thin_cross_sections():
    """当日有效资产 < 3 时跳过（截面相关性不可信）。"""
    dates = pd.date_range("2024-01-01", periods=2)
    f = pd.DataFrame(
        [[1.0, np.nan], [2.0, np.nan]], index=dates, columns=["A", "B"]
    )
    assert _compute_spearman_ic(f, f.copy(), f.index) == []


def test_spearman_ic_all_nan_returns_empty():
    dates = pd.date_range("2024-01-01", periods=2)
    f = pd.DataFrame(np.nan, index=dates, columns=list("ABC"))
    assert _compute_spearman_ic(f, f.copy(), f.index) == []


# ── _compute_quintile_returns ───────────────────────────────


def test_quintile_returns_monotonic_signal():
    """因子与收益完全同向 → 最高分位组收益应最高（排序有效性）。"""
    dates = pd.date_range("2024-01-01", periods=3)
    assets = [f"A{i}" for i in range(10)]
    f = pd.DataFrame(
        [list(range(10))] * 3, index=dates, columns=assets, dtype=float
    )
    ret = f / 100.0
    out = _compute_quintile_returns(f, ret, f.index, n_groups=5)
    assert set(out) == {0, 1, 2, 3, 4}
    # 每组 3 个日期
    assert all(len(v) == 3 for v in out.values())
    # 低分位 < 高分位
    assert out[0][0] < out[4][0]


def test_quintile_returns_skips_thin_dates():
    dates = pd.date_range("2024-01-01", periods=2)
    f = pd.DataFrame([[1.0, 2.0]] * 2, index=dates, columns=["A", "B"])
    # n_groups=5 需要 ≥10 个资产，2 个必然跳过
    out = _compute_quintile_returns(f, f, f.index, n_groups=5)
    assert all(v == [] for v in out.values())


def test_quintile_returns_handles_nan_returns():
    dates = pd.date_range("2024-01-01", periods=1)
    assets = [f"A{i}" for i in range(10)]
    f = pd.DataFrame([range(10)], index=dates, columns=assets, dtype=float)
    ret = pd.DataFrame(
        [[np.nan] * 10], index=dates, columns=assets, dtype=float
    )
    out = _compute_quintile_returns(f, ret, f.index, n_groups=5)
    assert all(v == [] for v in out.values())


# ── _compute_daily_ic_series ────────────────────────────────


def test_ic_series_returns_three_lists():
    dates = pd.date_range("2024-01-01", periods=4)
    assets = list("ABCDE")
    f = _panel(assets, dates, seed=7)
    p, s, d = _compute_daily_ic_series(f, f.copy(), f.index)
    assert len(p) == len(s) == len(d) == 4
    assert all(abs(x - 1.0) < 1e-9 for x in p)
    assert all(abs(x - 1.0) < 1e-9 for x in s)
    assert list(d) == list(f.index)


def test_ic_series_skips_thin_cross_sections():
    dates = pd.date_range("2024-01-01", periods=2)
    f = pd.DataFrame([[1.0, 2.0]] * 2, index=dates, columns=["A", "B"])
    p, s, d = _compute_daily_ic_series(f, f.copy(), f.index)
    assert (p, s, d) == ([], [], [])


def test_ic_series_skips_nan_ic():
    """全 NaN 截面 → corr 为 NaN → 不计入。"""
    dates = pd.date_range("2024-01-01", periods=2)
    f = pd.DataFrame(
        [[1.0, np.nan, np.nan], [2.0, np.nan, np.nan]],
        index=dates, columns=list("ABC"),
    )
    p, _s, _d = _compute_daily_ic_series(f, f.copy(), f.index)
    assert p == []


# ── _resolve_asset_universe ─────────────────────────────────

class _FakePrices:
    """最小 pandas Series 替身：模拟 `df["asset"].unique()` 的去重语义。"""

    def __init__(self, assets):
        self._assets = list(dict.fromkeys(assets))  # 保持首次出现顺序，去重

    def __getitem__(self, key):
        assert key == "asset"
        return self

    def unique(self):
        return np.array(self._assets)


def test_resolve_universe_all_sorted_unique():
    """'all' → 去重并排序（稳定顺序，便于结果可复现）。"""
    assets = ["600000", "000001", "600000", "000002"]
    out = _resolve_asset_universe(_FakePrices(assets), "all")
    assert out == ["000001", "000002", "600000"]


def test_resolve_universe_comma_list():
    """非 'all' → 按逗号切分并去空白（**不校验资产是否存在**）。"""
    out = _resolve_asset_universe(_FakePrices(["a", "b"]), "b, a ,c")
    assert out == ["b", "a", "c"]


def test_resolve_universe_single_entry():
    assert _resolve_asset_universe(_FakePrices(["a"]), "a") == ["a"]


def test_resolve_universe_empty_spec():
    assert _resolve_asset_universe(_FakePrices(["a"]), "") == [""]


# ── _build_ret_panel ────────────────────────────────────────


def test_build_ret_panel_forward_shift():
    """前瞻收益：pct_change(n).shift(-n) —— 最后 n 天应为 NaN。"""
    dates = pd.date_range("2024-01-01", periods=8)
    long = pd.DataFrame({
        "date": list(dates) * 2,
        "asset": ["X"] * 8 + ["Y"] * 8,
        "open": 1.0, "high": 1.0, "low": 1.0,
        "close": [10, 11, 12, 13, 14, 15, 16, 17] * 2,
        "volume": 1.0,
    })
    out = _build_ret_panel(long, ["X", "Y"], forward_days=2)
    assert set(out) == {"X", "Y"}
    series = out["X"]
    # 前 6 个有值，后 2 个（前瞻不可得）为 NaN
    assert series.iloc[:6].notna().all()
    assert series.iloc[-2:].isna().all()
    # 第二个值 = 13/11 - 1
    assert abs(series.iloc[1] - (13 / 11 - 1)) < 1e-12
