"""H 档补测第二批 —— `core/db.py` 的读取过滤分支与注册表契约。

第一批（test_db_crud_contract.py）覆盖了写→读往返；本文件补：
- `load_ohlcv_data` 的 codes / start / end 过滤（bar-by-bar engine 的数据源）
- `get_price_data_info` 的实际字段
- `save_ohlcv_data`（单资产，允许缺 volume、必须缺 close 即拒）
- `load_weight_history` / `load_nav_history` 的宽表往返
- `register_factor` 的复杂度 fail-closed 契约（complexity=None 时自动分析，
  表达式解析不了 → 注册失败，这是研究纪律的准入门）
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from strategy_research.core import db


@pytest.fixture
def ws(tmp_path) -> Path:
    assert db.init_db(tmp_path) is True
    return tmp_path


def _ohlcv(n=30, base=10.0, with_volume=True) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-01", periods=n)
    data = {"open": base + 0.1, "high": base + 1.1, "low": base - 0.9,
            "close": base + 0.5}
    if with_volume:
        data["volume"] = 1000.0
    return pd.DataFrame(data, index=pd.DatetimeIndex(idx, name="trade_date"))


# ── load_ohlcv_data 过滤分支 ────────────────────────────────


def test_load_ohlcv_filters_by_codes(ws):
    db.save_ohlcv_to_db(ws, {"A": _ohlcv(), "B": _ohlcv(base=20.0)}, "s1")
    out = db.load_ohlcv_data(ws, "s1", codes=["B"])
    assert set(out) == {"B"}
    assert list(out["B"].columns) == ["open", "high", "low", "close", "volume"]


def test_load_ohlcv_filters_by_date_range(ws):
    db.save_ohlcv_to_db(ws, {"A": _ohlcv(30)}, "s1")
    out = db.load_ohlcv_data(ws, "s1", start_date="2024-01-15",
                             end_date="2024-01-20")
    assert set(out) == {"A"}
    assert len(out["A"]) <= 5


def test_load_ohlcv_unknown_strategy_returns_empty(ws):
    assert db.load_ohlcv_data(ws, "ghost") == {}


def test_load_ohlcv_unknown_code_returns_empty(ws):
    db.save_ohlcv_to_db(ws, {"A": _ohlcv()}, "s1")
    assert db.load_ohlcv_data(ws, "s1", codes=["Z"]) == {}


def test_load_ohlcv_numeric_coercion(ws):
    """数值列经 pd.to_numeric coerce——坏值变 NaN 而非炸查询。"""
    db.save_ohlcv_to_db(ws, {"A": _ohlcv()}, "s1")
    out = db.load_ohlcv_data(ws, "s1")
    for col in ("open", "high", "low", "close", "volume"):
        assert pd.api.types.is_numeric_dtype(out["A"][col])


# ── get_price_data_info ─────────────────────────────────────


def test_price_data_info_fields(ws):
    db.save_ohlcv_to_db(ws, {"A": _ohlcv(), "B": _ohlcv()}, "s1")
    info = db.get_price_data_info(ws, "s1")
    assert info["n_assets"] == 2
    assert info["n_dates"] == 30
    assert info["start_date"] is not None
    assert info["end_date"] is not None


def test_price_data_info_unknown_strategy_zero_counts(ws):
    """COUNT(*) 聚合恒返回一行：未知策略是零值行（不是空 dict）。"""
    info = db.get_price_data_info(ws, "ghost")
    assert info["n_assets"] == 0
    assert info["n_dates"] == 0
    assert info["start_date"] is None
    assert info["end_date"] is None


# ── save_ohlcv_data（单资产） ───────────────────────────────


def test_save_ohlcv_data_single_asset_roundtrip(ws):
    ok = db.save_ohlcv_data(ws, "s1", "A", _ohlcv())
    assert ok is True
    out = db.load_ohlcv_data(ws, "s1", codes=["A"])
    assert len(out["A"]) == 30


def test_save_ohlcv_data_allows_missing_volume(ws):
    """volume 缺失 → 默认 0，不拒绝。"""
    ok = db.save_ohlcv_data(ws, "s1", "A", _ohlcv(with_volume=False))
    assert ok is True
    out = db.load_ohlcv_data(ws, "s1", codes=["A"])
    assert (out["A"]["volume"] == 0).all()


def test_save_ohlcv_data_missing_close_rejected(ws):
    """close 缺失 → 拒绝（loader 至少会返回 close 的契约）。"""
    no_close = _ohlcv().drop(columns=["close"])
    assert db.save_ohlcv_data(ws, "s1", "A", no_close) is False


def test_save_ohlcv_data_renames_date_index(ws):
    """index 名不是 date 时自动重命名首列。"""
    df = _ohlcv()
    df.index = pd.DatetimeIndex(df.index, name="trade_date")
    assert db.save_ohlcv_data(ws, "s1", "A", df) is True
    assert len(db.load_ohlcv_data(ws, "s1", codes=["A"])["A"]) == 30


# ── weight / nav history 读回 ───────────────────────────────


def test_load_weight_history_pivot(ws):
    history = [
        (pd.Timestamp("2026-01-01"), {"AAPL": 0.6, "MSFT": 0.4}),
        (pd.Timestamp("2026-01-02"), {"AAPL": 1.0}),
    ]
    assert db.save_weight_history(ws, "s1", "run_1", history) is True
    panel = db.load_weight_history(ws, "s1", "run_1")
    assert panel.shape == (2, 2)
    assert panel.loc[pd.Timestamp("2026-01-02"), "AAPL"] == 1.0


def test_load_weight_history_missing_run_empty(ws):
    db.save_weight_history(ws, "s1", "run_1",
                           [(pd.Timestamp("2026-01-01"), {"A": 1.0})])
    assert db.load_weight_history(ws, "s1", "nope").empty


def test_load_nav_history_roundtrip(ws):
    nav = pd.Series([100.0, 101.5, 99.0],
                    index=pd.DatetimeIndex(
                        ["2026-01-01", "2026-01-02", "2026-01-03"],
                        name="trade_date"))
    assert db.save_nav_history(ws, "s1", "run_1", nav) is True
    out = db.load_nav_history(ws, "s1", "run_1")
    assert list(out) == [100.0, 101.5, 99.0]


def test_load_nav_history_missing_empty(ws):
    out = db.load_nav_history(ws, "ghost", "nope")
    assert out.empty


# ── register_factor 复杂度 fail-closed ──────────────────────


def test_register_factor_auto_complexity(ws):
    """complexity=None → 从表达式自动静态分析。"""
    assert db.register_factor(ws, "s1", "mom20", "ts_mean(close, 20)") is True
    row = db.get_factors(ws, "s1")[0]
    assert row["operator_count"] and row["operator_count"] >= 1
    assert row["complexity_score"] is not None


def test_register_factor_unknown_char_fail_closed(ws):
    """表达式含分词器不认识的字符 → 注册失败（纪律准入门）。

    analyze_complexity 的 ValueError 路径由「未知字符」触发（如 ``!``）；
    空串/纯标点只是产出零复杂度，并不抛错——所以 fail-closed 的真实边界
    是非法字符，不是空输入。probe 实测见
    tmp/probe_complexity.py 的结果：''/,,,,' 均成功返回零值 dict。
    """
    assert db.register_factor(ws, "s1", "bad", "close ! 20") is False
    assert db.get_factors(ws, "s1") == []


def test_register_factor_natural_language_still_registers(ws):
    """自然语言描述可分词 → 注册成功（features 计入复杂度）。"""
    assert db.register_factor(ws, "s1", "manual", "hand written factor") is True


def test_register_factor_explicit_complexity_skips_analysis(ws):
    """显式给 complexity → 跳过静态分析（允许非表达式因子）。"""
    ok = db.register_factor(
        ws, "s1", "manual_f", "hand written",
        complexity={"operator_count": 0, "param_count": 0,
                    "feature_count": 1, "complexity_score": 1.0},
    )
    assert ok is True
    row = db.get_factors(ws, "s1")[0]
    assert row["factor_name"] == "manual_f"


def test_register_factor_lineage_fields(ws):
    db.register_factor(
        ws, "s1", "carry1", "ts_mean(close, 5)",
        info_channel="carry", parent_hypothesis_id="h-42",
    )
    row = db.get_factors(ws, "s1")[0]
    assert row.get("info_channel") == "carry"
    assert row.get("parent_hypothesis_id") == "h-42"
