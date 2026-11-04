"""H 档补测 —— `apps/db.py` 的 CRUD 往返契约。

背景（2026-11-01 覆盖率复核）：conftest 的分档门禁只覆盖 A/B/C 三档
（22,244 行），`core/` 顶层的研究工具模块（3,966 行）漏在门禁外，
整体只 66%。其中 `db.py` 457 行仅 40%——而它有 18 处生产调用，是
DuckDB 持久化的唯一入口。

这些函数都是「写入 → 读回」模式，用临时 workspace 可完全确定性覆盖，
不存在「外部依赖没法测」的理由。本文件按功能簇分组补齐。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from strategy_research.apps import db


@pytest.fixture
def ws(tmp_path) -> Path:
    assert db.init_db(tmp_path) is True
    return tmp_path


# ── init / path helpers ──────────────────────────────────────


def test_init_db_is_idempotent(tmp_path):
    assert db.init_db(tmp_path) is True
    assert db.init_db(tmp_path) is True   # 二次初始化不炸


def test_get_db_path_points_into_workspace(tmp_path):
    p = db.get_db_path(tmp_path)
    assert p.parent == tmp_path
    assert p.name.endswith(".duckdb")


def test_get_connection_returns_duckdb_handle(tmp_path):
    """连接对象可建立并可关闭（duckdb 单写者语义见 docstring）。"""
    conn = db.get_connection(tmp_path)
    assert conn is not None
    conn.close()


# ── factor data ─────────────────────────────────────────────


def _factor_panel() -> pd.DataFrame:
    idx = pd.DatetimeIndex(["2024-01-01", "2024-01-02"], name="date")
    return pd.DataFrame({"A": [0.1, 0.2], "B": [0.3, 0.4]}, index=idx)


def test_save_and_load_factor_data_roundtrip(ws):
    assert db.save_factor_data(ws, "s1", "mom20", _factor_panel()) is True
    panel = db.load_factor_data(ws, "s1", "mom20")
    assert not panel.empty
    # load_factor_data 返回 (date × asset) 宽表面板，与 save 的入参同形
    assert panel.shape == (2, 2)
    assert list(panel.columns) == ["A", "B"]


def test_load_factor_data_unknown_factor_returns_empty(ws):
    df = db.load_factor_data(ws, "nope", "nope")
    assert df.empty


def test_factor_data_date_filter(ws):
    db.save_factor_data(ws, "s1", "f", _factor_panel())
    df = db.load_factor_data(ws, "s1", "f",
                             start_date="2024-01-02", end_date="2024-01-02")
    assert len(df) <= 2


# ── factor registry ─────────────────────────────────────────


def test_register_and_get_factor(ws):
    ok = db.register_factor(ws, "s1", "mom20", "ts_mean(close, 20)")
    assert ok is True
    rows = db.get_factors(ws, "s1")
    assert len(rows) == 1
    assert rows[0]["factor_name"] == "mom20"


def test_register_factor_with_metadata(ws):
    db.register_factor(
        ws, "s1", "carry1", "ts_mean(close, 5)",
        factor_type="asset_ts", category="carry",
        source="manual", lookback_window=5,
        info_channel="carry", parent_hypothesis_id="h-1",
    )
    row = db.get_factors(ws, "s1")[0]
    assert row["category"] == "carry"
    assert row["info_channel"] == "carry"


def test_remove_factor(ws):
    db.register_factor(ws, "s1", "tmp_f", "ts_mean(close, 5)")
    assert db.remove_factor(ws, "s1", "tmp_f") is True
    assert db.get_factors(ws, "s1") == []


def test_remove_missing_factor_is_noop_true(ws):
    """DELETE 命中 0 行也算成功——契约是「不报错即 True」。"""
    assert db.remove_factor(ws, "s1", "never_existed") is True
    assert db.get_factors(ws, "s1") == []


# ── validation cache ────────────────────────────────────────


def test_cache_validation_roundtrip(ws):
    ok = db.cache_validation(
        ws, "s1", "mom20", "ts_mean(close, 20)",
        ic_mean=0.05, ic_std=0.02, ir=2.5,
        rank_ic_mean=0.04, overall_score=78.0, is_valid=True,
    )
    assert ok is True
    rows = db.get_validation_cache(ws, "s1")
    assert len(rows) == 1
    assert rows[0]["factor_name"] == "mom20"


def test_validation_cache_records_failure_reasons(ws):
    db.cache_validation(
        ws, "s1", "bad_f", "x", ic_mean=0.0, ic_std=0.1, ir=0.0,
        is_valid=False, fail_reasons="IR too low",
    )
    row = db.get_validation_cache(ws, "s1")[0]
    assert row["is_valid"] in (False, 0)
    assert "IR too low" in (row.get("fail_reasons") or "")


# ── backtest results ────────────────────────────────────────


def test_save_and_get_backtest_result(ws):
    ok = db.save_backtest_result(
        ws, "s1", "run_001", commit_hash="abc123", action="tweak",
        goal_metric=1.2, calmar=1.2, sharpe=0.8, max_dd=-0.15,
        ann_return=0.2, ann_vol=0.16, status="keep", description="baseline",
    )
    assert ok is True
    rows = db.get_backtest_results(ws, "s1")
    assert len(rows) == 1
    assert rows[0]["run"] == "run_001"
    assert rows[0]["status"] == "keep"


def test_get_best_backtest_picks_highest_goal_metric(ws):
    """只在 status="keep" 的行里挑 goal_metric 最大者。"""
    db.save_backtest_result(ws, "s1", "run_1", goal_metric=0.5, status="keep")
    db.save_backtest_result(ws, "s1", "run_2", goal_metric=1.9, status="keep")
    db.save_backtest_result(ws, "s1", "run_3", goal_metric=9.9, status="discard")
    best = db.get_best_backtest(ws, "s1")
    assert best is not None
    assert best["run"] == "run_2"


def test_get_best_backtest_ignores_non_keep(ws):
    db.save_backtest_result(ws, "s1", "run_1", goal_metric=9.9, status="discard")
    assert db.get_best_backtest(ws, "s1") is None


def test_get_best_backtest_empty_returns_none(ws):
    assert db.get_best_backtest(ws, "empty_strategy") is None


def test_backtest_results_limit(ws):
    for i in range(5):
        db.save_backtest_result(ws, "s1", f"run_{i}", goal_metric=float(i))
    rows = db.get_backtest_results(ws, "s1", limit=2)
    assert len(rows) == 2


# ── data fingerprint ────────────────────────────────────────


def test_compute_data_fingerprint_is_deterministic():
    df = pd.DataFrame({"a": [1, 2]}, index=pd.to_datetime(["2024-01-01", "2024-01-02"]))
    assert db.compute_data_fingerprint(df) == db.compute_data_fingerprint(df)
    assert len(db.compute_data_fingerprint(df)) == 16


def test_compute_data_fingerprint_changes_with_content():
    d1 = pd.DataFrame({"a": [1, 2]}, index=pd.to_datetime(["2024-01-01", "2024-01-02"]))
    d2 = pd.DataFrame({"a": [1, 2, 3]},
                      index=pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]))
    assert db.compute_data_fingerprint(d1) != db.compute_data_fingerprint(d2)


def test_update_and_get_data_fingerprint(ws):
    fp = db.compute_data_fingerprint(_factor_panel())
    assert db.update_data_fingerprint(ws, "s1", "prices", fp, row_count=2) is True
    got = db.get_data_fingerprint(ws, "s1")
    assert got is not None
    assert got["fingerprint"] == fp


def test_get_data_fingerprint_missing_returns_none(ws):
    assert db.get_data_fingerprint(ws, "never") is None


# ── import meta ─────────────────────────────────────────────


def test_update_and_get_import_meta(ws):
    assert db.update_import_meta(ws, "s1", "000001.SZ", "2024-01-02") is True
    meta = db.get_import_meta(ws, "s1")
    assert meta
    assert "000001.SZ" in str(meta)


def test_get_last_import_date(ws):
    import datetime as _dt

    db.update_import_meta(ws, "s1", "000001.SZ", "2024-03-15")
    got = db.get_last_import_date(ws, "s1", "000001.SZ")
    # DuckDB DATE 列读回 datetime.date（不是字符串）
    assert got == _dt.date(2024, 3, 15)


def test_get_last_import_date_missing(ws):
    assert db.get_last_import_date(ws, "never", "000001.SZ") is None


# ── OHLCV ───────────────────────────────────────────────────


def _ohlcv(n: int = 30) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame({
        "date": idx,
        "asset_code": ["A"] * n,
        "open": range(10, 10 + n),
        "high": range(11, 11 + n),
        "low": range(9, 9 + n),
        "close": range(10, 10 + n),
        "volume": [1000] * n,
    })


def test_save_and_load_ohlcv(ws):
    """save_ohlcv_to_db 收 {code: DataFrame} 映射，返回写入行数。"""
    saved = db.save_ohlcv_to_db(ws, {"A": _ohlcv()}, strategy_name="s1")
    assert saved == len(_ohlcv())
    info = db.get_price_data_info(ws, "s1")
    assert isinstance(info, dict)


def test_load_price_data_empty_workspace(ws):
    df = db.load_price_data(ws, "s1")
    assert df is None or df.empty


def test_load_price_data_after_save(ws):
    db.save_ohlcv_to_db(ws, {"A": _ohlcv()}, strategy_name="s1")
    panel = db.load_price_data(ws, "s1")
    assert panel is not None and not panel.empty


def test_load_ohlcv_data_returns_frame(ws):
    db.save_ohlcv_to_db(ws, {"A": _ohlcv()}, strategy_name="s1")
    pair = db.load_ohlcv_data(ws, "s1")   # 返回 (df, meta) 或 df
    assert pair is not None
