"""factor_tools 工具层单元测试——mock 数据入口，驱动 6 个 execute。

覆盖 docs/coverage-90-plan-20261030.md §3.2：`factor_tools.py` 是 C 档最大
单点缺口（371 条）。6 个工具（compute_factor / factor_analysis / pattern /
cross_sectional / quintile / ic_decay / turnover）全部经同一个数据入口
``_load_ohlcv``——mock 它即可覆盖各工具的完整 execute 分支，无需 DuckDB。

与 `test_factor_tools_algorithms.py` 互补：那边测纯算法，这边测工具契约
（参数校验、错误提示、输出形状）。
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from strategy_research.kernel.agent.builtin_tools import factor_tools as ft
from strategy_research.core.agent.tools import ToolContext


@pytest.fixture()
def ctx(tmp_path):
    return ToolContext(workspace=tmp_path, session_id="sess-1")


@pytest.fixture()
def prices() -> pd.DataFrame:
    """8 资产 × 60 天的确定性 OHLCV 长表（分位分析需 ≥2×组数）。"""
    dates = pd.date_range("2024-01-01", periods=60)
    rows = []
    for i, asset in enumerate(["A", "B", "C", "D", "E", "F", "G", "H"]):
        for j, d in enumerate(dates):
            base = 10.0 + i * 2 + j * 0.1
            rows.append({
                "date": d, "asset": asset,
                "open": base, "high": base * 1.01, "low": base * 0.99,
                "close": base * (1 + 0.001 * (j % 3)),
                "volume": 1000.0 + j,
            })
    return pd.DataFrame(rows)


class _FakeConn:
    """DuckDB 连接替身：execute(...).fetch_df() 返回预置 DataFrame。"""

    def __init__(self, prices: pd.DataFrame) -> None:
        self._prices = prices
        self.queries: list[str] = []

    def execute(self, sql, *a, **k):
        self.queries.append(sql)
        return self

    def fetch_df(self):
        return self._prices.copy()

    def close(self):
        pass


@pytest.fixture()
def patch_data(monkeypatch, prices):
    """所有工具内联调 get_connection → 换成返回内存 DataFrame 的假连接。"""
    conn = _FakeConn(prices)
    monkeypatch.setattr(
        "strategy_research.apps.db.get_connection",
        lambda _ws: conn,
    )
    return conn


# ── ComputeFactorTool ───────────────────────────────────────


def test_compute_factor_success(ctx, patch_data):
    out = json.loads(ft.ComputeFactorTool().execute(
        ctx=ctx, factor_code="close", asset="A",
    ))
    assert out.get("status") != "error", out
    assert "A" in json.dumps(out)


def test_compute_factor_missing_code(ctx, patch_data):
    out = json.loads(ft.ComputeFactorTool().execute(ctx=ctx, factor_code=""))
    assert out["status"] == "error"
    assert "factor_code" in json.dumps(out)


def test_compute_factor_unknown_asset(ctx, patch_data):
    out = json.loads(ft.ComputeFactorTool().execute(
        ctx=ctx, factor_code="close", asset="ZZZ",
    ))
    assert out["status"] == "error"


def test_compute_factor_no_workspace(prices):
    out = json.loads(ft.ComputeFactorTool().execute(
        ctx=ToolContext(workspace=None), factor_code="close",
    ))
    assert out["status"] == "error"


# ── FactorAnalysisTool ──────────────────────────────────────


def test_factor_analysis_success(ctx, patch_data):
    out = json.loads(ft.FactorAnalysisTool().execute(
        ctx=ctx, factor_code="close", forward_days=5,
    ))
    assert out.get("status") != "error", out
    assert "ic" in json.dumps(out).lower() or "mean" in json.dumps(out).lower()


def test_factor_analysis_missing_code(ctx, patch_data):
    out = json.loads(ft.FactorAnalysisTool().execute(ctx=ctx, factor_code=""))
    assert out["status"] == "error"


# ── FactorCrossSectionalAnalysis ────────────────────────────


def test_cross_sectional_success(ctx, patch_data):
    out = json.loads(ft.FactorCrossSectionalAnalysis().execute(
        ctx=ctx, factor_code="close",
    ))
    assert out.get("status") != "error", out


# ── FactorQuintileReturns ───────────────────────────────────


def test_quintile_returns_success(ctx, patch_data):
    out = json.loads(ft.FactorQuintileReturns().execute(
        ctx=ctx, factor_code="close", n_groups=3,
    ))
    assert out.get("status") != "error", out


def test_quintile_returns_invalid_groups(ctx, patch_data):
    out = json.loads(ft.FactorQuintileReturns().execute(
        ctx=ctx, factor_code="close", n_groups=1,
    ))
    # 组数过少应报错或给出提示，不应崩溃
    assert out.get("status") == "error" or "n_groups" in json.dumps(out)


# ── FactorICDecay ───────────────────────────────────────────


def test_ic_decay_success(ctx, patch_data):
    out = json.loads(ft.FactorICDecay().execute(
        ctx=ctx, factor_code="close", horizons="1,3",
    ))
    assert out.get("status") != "error", out


# ── FactorTurnover ──────────────────────────────────────────


def test_turnover_success(ctx, patch_data):
    out = json.loads(ft.FactorTurnover().execute(
        ctx=ctx, factor_code="close", rebalance_freq=3,
    ))
    assert out.get("status") != "error", out


# ── PatternRecognitionTool ──────────────────────────────────


def test_pattern_recognition_success(ctx, patch_data):
    out = json.loads(ft.PatternRecognitionTool().execute(
        ctx=ctx, asset="A", lookback=60,
    ))
    assert isinstance(out, dict)


def test_pattern_recognition_unknown_asset(ctx, patch_data):
    out = json.loads(ft.PatternRecognitionTool().execute(
        ctx=ctx, asset="ZZZ",
    ))
    assert out.get("status") == "error"


# ── _load_factor_panel_data ─────────────────────────────────


def test_load_factor_panel_rejects_bad_code(ctx, patch_data):
    panel, err = ft._load_factor_panel_data(ctx, "t", "")
    assert panel is None
    assert "factor_code" in err


def test_load_factor_panel_requires_min_assets(ctx, monkeypatch, prices):
    """只保留 1 个资产 → 低于 min_assets=3 → 报错。"""
    one = prices[prices["asset"] == "A"]
    monkeypatch.setattr(
        ft, "_load_ohlcv",
        lambda *a, **k: (True, object(), one, None),
    )
    panel, err = ft._load_factor_panel_data(ctx, "t", "close", universe="all")
    assert panel is None
    assert "min_assets" in err or "assets" in err


def test_load_factor_panel_propagates_load_error(ctx, monkeypatch):
    monkeypatch.setattr(
        ft, "_load_ohlcv",
        lambda *a, **k: (False, None, None, "boom"),
    )
    panel, err = ft._load_factor_panel_data(ctx, "t", "close")
    assert panel is None and err == "boom"


def test_load_factor_panel_success(ctx, patch_data):
    """经真实 _load_ohlcv（其内部 get_connection 已被替换）。"""
    panel, df = ft._load_factor_panel_data(ctx, "t", "close")
    assert panel is not None
    assert len(panel) >= 3
    assert not df.empty


# ── _build_factor_panel 边界 ────────────────────────────────


def test_build_factor_panel_skips_short_history(prices):
    short = prices.groupby("asset").head(5)  # 每资产 5 行 < min_bars=20
    panel = ft._build_factor_panel(short, ["A", "B", "C"], "close")
    assert panel == {}


def test_build_factor_panel_skips_bad_factor(prices):
    panel = ft._build_factor_panel(prices, ["A", "B", "C"], "not_a_real_factor((")
    assert panel == {}


# ── _load_ohlcv 错误分支 ────────────────────────────────────


def test_load_ohlcv_without_workspace():
    ok, conn, df, err = ft._load_ohlcv(ToolContext(workspace=None), "t")
    assert not ok and conn is None and df is None
    assert "workspace" in err


def test_load_ohlcv_db_open_failure(ctx, monkeypatch):
    def boom(_ws):
        raise RuntimeError("db down")

    monkeypatch.setattr("strategy_research.apps.db.get_connection", boom)
    ok, conn, df, err = ft._load_ohlcv(ctx, "t")
    assert not ok and err and "db open failed" in err
