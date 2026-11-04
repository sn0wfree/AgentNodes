"""H 档补测（P1-7b）—— `config_runner.py` 的因子计算链。

`_compute_factor_values` 有三条路径（表达式 / 单 alpha / alpha 组合），
此前几乎全空。它们的失败处理尤其重要：**单个因子在某资产上失败不能
拖垮整个回测**，必须记到 factor_failures 并继续。

- 表达式因子：每个资产单独跑 DSL；失败按资产记录。
- 单 alpha：adapter 抛错 → 记录失败，其余因子照常。
- alpha 组合：空结果 → 跳过；异常 → 记录失败。
"""
from __future__ import annotations

import logging

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from strategy_research.apps import config_runner as cr


def _panel(cols=("A", "B"), n=60) -> pd.DataFrame:
    rng = np.random.default_rng(9)
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame(
        100 * np.cumprod(1 + rng.normal(0, 0.01, (n, len(cols))), axis=0),
        index=idx, columns=list(cols),
    )


def _ohlcv(index_len=60) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-01", periods=index_len)
    rng = np.random.default_rng(4)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.01, index_len))
    return pd.DataFrame(
        {"open": close * 0.99, "high": close * 1.02,
         "low": close * 0.98, "close": close, "volume": 1_000_000},
        index=idx)


class _Strategy(cr.FactorStrategy):
    """预置 factor_failures，避免依赖真实 __init__ 的其它字段。"""

    def __init__(self, factors, params=None):
        super().__init__(factors, params or {})
        self.factor_failures = []


# ── 表达式因子 ───────────────────────────────────────────


def test_expression_factor_produces_wide_frame():
    panel = _panel()
    s = _Strategy([{"name": "ret", "code": "ts_return(close, 5)"}])
    panels = {c: _ohlcv(len(panel)) for c in panel.columns}

    out = s._compute_factor_values(panel.index[-1], panel) if False else {}
    # 直接调用内部方法（绕过 DuckDB 加载，注入 panels）
    s._compute_expression_factor(
        "ret", "ts_return(close, 5)", panel.index[-1], panel, panels, out)
    assert "ret" in out
    wide = out["ret"]
    assert list(wide.columns) == list(panel.columns)
    assert wide.notna().any().any()


def test_expression_factor_skipped_without_panels(caplog):
    panel = _panel()
    s = _Strategy([{"name": "ret", "code": "ts_return(close, 5)"}])
    out = {}
    with caplog.at_level(logging.WARNING, logger="strategy_research.apps.config_runner"):
        s._compute_expression_factor(
            "ret", "ts_return(close, 5)", panel.index[-1], panel, None, out)
    assert out == {}  # 不写入任何值
    assert "requires workspace_path" in caplog.text


def test_expression_factor_missing_asset_is_skipped():
    """某资产没有 OHLCV 面板 → 该列留空，不影响其他资产。"""
    panel = _panel(cols=("A", "B"))
    s = _Strategy([{"name": "ret", "code": "ts_return(close, 5)"}])
    out = {}
    s._compute_expression_factor(
        "ret", "ts_return(close, 5)", panel.index[-1], panel,
        {"A": _ohlcv(len(panel))}, out)
    assert out["ret"]["A"].notna().any()
    assert out["ret"]["B"].isna().all()


def test_expression_factor_failure_is_recorded_per_asset(caplog):
    panel = _panel(cols=("A",))
    s = _Strategy([{"name": "bad", "code": "nonexistent_func(close)"}])
    out = {}
    with caplog.at_level(logging.WARNING, logger="strategy_research.apps.config_runner"):
        s._compute_expression_factor(
            "bad", "nonexistent_func(close)", panel.index[-1], panel,
            {"A": _ohlcv(len(panel))}, out)
    assert s.factor_failures, "失败必须进 factor_failures"
    assert "bad" in caplog.text


# ── 单 alpha / alpha 组合（adapter 用替身） ───────────────


class _FakeAdapter:
    def __init__(self, mode="ok"):
        self.mode = mode
        self.calls = []

    def compute_as_wide(self, alpha_id, panel):
        self.calls.append(("single", alpha_id))
        if self.mode == "boom":
            raise RuntimeError("alpha crashed")
        return pd.Series(1.0, index=panel.index)

    def compute_batch(self, alpha_ids, panel):
        self.calls.append(("batch", tuple(alpha_ids)))
        if self.mode == "boom":
            raise RuntimeError("batch crashed")
        if self.mode == "empty":
            return pd.DataFrame()
        # batch 的约定形状是 DataFrame（调用方用 .mean(axis=1)），
        # 索引是 (date, asset) 的 MultiIndex，列是各 alpha。
        idx = pd.MultiIndex.from_product(
            [panel.index, panel.columns], names=["date", "asset"])
        return pd.DataFrame(
            {aid: 0.5 for aid in alpha_ids}, index=idx)


def test_single_alpha_factor_written(monkeypatch):
    import strategy_research.apps.alpha_zoo_adapter as adapter_mod

    fake = _FakeAdapter()
    monkeypatch.setattr(adapter_mod, "AlphaZooAdapter", lambda: fake)

    panel = _panel()
    s = _Strategy([{"name": "a1", "alpha_id": "gtja191_001"}])
    out = s._compute_factor_values(panel.index[-1], panel)
    assert "a1" in out
    assert fake.calls[0] == ("single", "gtja191_001")


def test_single_alpha_failure_is_recorded(monkeypatch, caplog):
    import strategy_research.apps.alpha_zoo_adapter as adapter_mod
    monkeypatch.setattr(adapter_mod, "AlphaZooAdapter", lambda: _FakeAdapter("boom"))

    panel = _panel()
    s = _Strategy([{"name": "a1", "alpha_id": "gtja191_001"}])
    with caplog.at_level(logging.WARNING, logger="strategy_research.apps.config_runner"):
        out = s._compute_factor_values(panel.index[-1], panel)
    assert "a1" not in out
    assert s.factor_failures
    assert "计算失败" in caplog.text


def test_alpha_combination_is_unstacked(monkeypatch):
    import strategy_research.apps.alpha_zoo_adapter as adapter_mod
    monkeypatch.setattr(adapter_mod, "AlphaZooAdapter", lambda: _FakeAdapter())

    panel = _panel()
    s = _Strategy([{"name": "combo", "alpha_ids": ["x", "y"],
                    "combination": "equal"}])
    out = s._compute_factor_values(panel.index[-1], panel)
    assert "combo" in out
    assert list(out["combo"].columns) == list(panel.columns)


def test_empty_batch_result_is_skipped(monkeypatch):
    import strategy_research.apps.alpha_zoo_adapter as adapter_mod
    monkeypatch.setattr(adapter_mod, "AlphaZooAdapter", lambda: _FakeAdapter("empty"))

    panel = _panel()
    s = _Strategy([{"name": "combo", "alpha_ids": ["x"]}])
    out = s._compute_factor_values(panel.index[-1], panel)
    assert "combo" not in out
    assert s.factor_failures == []


def test_batch_failure_is_recorded(monkeypatch, caplog):
    import strategy_research.apps.alpha_zoo_adapter as adapter_mod
    monkeypatch.setattr(adapter_mod, "AlphaZooAdapter", lambda: _FakeAdapter("boom"))

    panel = _panel()
    s = _Strategy([{"name": "combo", "alpha_ids": ["x"]}])
    with caplog.at_level(logging.WARNING, logger="strategy_research.apps.config_runner"):
        out = s._compute_factor_values(panel.index[-1], panel)
    assert "combo" not in out
    assert s.factor_failures
    assert "组合" in caplog.text


def test_adapter_is_lazily_created_once(monkeypatch):
    """多个 alpha 因子共用同一个 adapter 实例（只构造一次）。"""
    import strategy_research.apps.alpha_zoo_adapter as adapter_mod

    created = {"n": 0}

    def make():
        created["n"] += 1
        return _FakeAdapter()

    monkeypatch.setattr(adapter_mod, "AlphaZooAdapter", make)
    panel = _panel()
    s = _Strategy([
        {"name": "a1", "alpha_id": "x"},
        {"name": "a2", "alpha_id": "y"},
    ])
    s._compute_factor_values(panel.index[-1], panel)
    assert created["n"] == 1


# ── _has_expression_factors ─────────────────────────────


def test_has_expression_factors_detection():
    assert cr.FactorStrategy([{"code": "close"}], {})._has_expression_factors()
    assert not cr.FactorStrategy([{"alpha_id": "x"}], {})._has_expression_factors()
    assert not cr.FactorStrategy([], {})._has_expression_factors()
