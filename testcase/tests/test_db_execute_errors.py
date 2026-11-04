"""H 档补测 —— `apps/db.py` 的 execute 异常降级分支（FakeConn 模式）。

这批分支的契约：**execute 抛异常 → print 警告 + 关连接 + 返回 False/空值，
绝不向上抛**。每个写函数和读函数各有一份，此前全部零覆盖——它们是
「DB 坏了但进程必须活着」场景的实际执行路径。

用 FakeConn（execute 即抛）+ monkeypatch get_connection 逐函数触发。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from strategy_research.apps import db


class BoomConn:
    """execute 即抛；close 必须被调用（记入 flag 供断言）。"""

    def __init__(self):
        self.closed = False

    def execute(self, *a, **kw):
        raise RuntimeError("simulated db failure")

    def close(self):
        self.closed = True


@pytest.fixture
def boom_conn(monkeypatch):
    conn = BoomConn()
    monkeypatch.setattr(db, "get_connection", lambda *a, **kw: conn)
    return conn


@pytest.fixture
def ws(tmp_path) -> Path:
    assert db.init_db(tmp_path) is True
    return tmp_path


def _ohlcv(n=5) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame(
        {"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0, "volume": 1.0},
        index=pd.DatetimeIndex(idx, name="trade_date"),
    )


# ── 写函数：异常 → False ────────────────────────────────────


def test_save_ohlcv_data_error_false(tmp_path, boom_conn):
    df = pd.DataFrame({"close": [1.0]},
                      index=pd.DatetimeIndex(["2024-01-01"], name="d"))
    assert db.save_ohlcv_data(tmp_path, "s", "A", df) is False
    assert boom_conn.closed


def test_save_factor_data_error_false(ws, boom_conn, monkeypatch):
    # save_factor_data 先 get_connection 再 melt；让 melt 成功但 execute 抛
    panel = pd.DataFrame(
        {"A": [1.0]}, index=pd.DatetimeIndex(["2024-01-01"], name="date"))
    assert db.save_factor_data(ws, "s1", "f", panel) is False
    assert boom_conn.closed


def test_save_weight_history_error_false(ws, boom_conn):
    history = [(pd.Timestamp("2026-01-01"), {"A": 1.0})]
    assert db.save_weight_history(ws, "s", "r", history) is False


def test_save_nav_history_error_false(ws, boom_conn):
    nav = pd.Series([1.0], index=pd.DatetimeIndex(["2026-01-01"]))
    assert db.save_nav_history(ws, "s", "r", nav) is False


def test_register_factor_error_false(ws, boom_conn):
    assert db.register_factor(ws, "s", "f", "ts_mean(close, 5)",
                              complexity={"operator_count": 1}) is False


def test_cache_validation_error_false(ws, boom_conn):
    assert db.cache_validation(ws, "s", "f", "c", 0.1, 0.1, 1.0) is False


def test_save_backtest_result_error_false(ws, boom_conn):
    assert db.save_backtest_result(ws, "s", "r_1") is False


def test_update_data_fingerprint_error_false(ws, boom_conn):
    assert db.update_data_fingerprint(ws, "s", "t", "fp") is False


def test_update_import_meta_error_false(ws, boom_conn):
    assert db.update_import_meta(ws, "s", "A", "2024-01-01") is False


# ── save_ohlcv_to_db：成功路径的分支 ────────────────────────


def test_save_ohlcv_skips_empty_and_none_entries(ws):
    """空 DataFrame / None 条目跳过，不计行数。"""
    n = db.save_ohlcv_to_db(ws, {"A": _ohlcv(), "B": pd.DataFrame(),
                                 "C": None}, "s1")
    assert n == 5   # 只有 A 写入


def test_save_ohlcv_normalizes_index_and_columns(ws):
    """trade_date 索引 + 大写列名 → 归一化为 date + 小写。"""
    df = _ohlcv(3)
    df.index = pd.DatetimeIndex(df.index, name="Trade_Date")
    df = df.rename(columns={"close": "Close"})
    n = db.save_ohlcv_to_db(ws, {"A": df}, "s1")
    assert n == 3
    out = db.load_ohlcv_data(ws, "s1", codes=["A"])
    assert len(out["A"]) == 3


def test_save_ohlcv_fills_missing_ohlcv_columns(ws):
    """缺 open/high/low → 用 close 值补；缺 volume → 0。"""
    df = pd.DataFrame({"close": [10.0, 11.0]},
                      index=pd.DatetimeIndex(
                          pd.bdate_range("2024-01-01", periods=2),
                          name="trade_date"))
    n = db.save_ohlcv_to_db(ws, {"A": df}, "s1")
    assert n == 2
    out = db.load_ohlcv_data(ws, "s1", codes=["A"])["A"]
    # open/high/low 都拿 close 的值补齐
    assert (out["open"] == out["close"]).all()
    assert (out["high"] == out["close"]).all()
    assert (out["low"] == out["close"]).all()
    assert (out["volume"] == 0).all()


def test_save_ohlcv_skips_rows_without_date(ws):
    """无 date 列也无日期索引 → 该资产跳过。"""
    df = pd.DataFrame({"close": [1.0, 2.0]})   # 默认 RangeIndex，无日期
    n = db.save_ohlcv_to_db(ws, {"A": df}, "s1")
    assert n == 0


# ── 读函数：异常 → 空值 ─────────────────────────────────────


def test_load_price_data_error_empty(ws, boom_conn):
    assert db.load_price_data(ws, "s").empty


def test_load_ohlcv_data_error_empty(ws, boom_conn):
    assert db.load_ohlcv_data(ws, "s") == {}


def test_get_price_data_info_error_empty(ws, boom_conn):
    assert db.get_price_data_info(ws, "s") == {}


def test_load_factor_data_error_empty(ws, boom_conn):
    assert db.load_factor_data(ws, "s", "f").empty


def test_load_weight_history_error_empty(ws, boom_conn):
    assert db.load_weight_history(ws, "s", "r").empty


def test_load_nav_history_error_empty(ws, boom_conn):
    assert db.load_nav_history(ws, "s", "r").empty


def test_get_factors_error_empty(ws, boom_conn):
    assert db.get_factors(ws, "s") == []


def test_get_validation_cache_error_empty(ws, boom_conn):
    assert db.get_validation_cache(ws, "s") == []


def test_get_backtest_results_error_empty(ws, boom_conn):
    assert db.get_backtest_results(ws, "s") == []


def test_get_data_fingerprint_error_none(ws, boom_conn):
    assert db.get_data_fingerprint(ws, "s") is None


def test_get_import_meta_error_empty_list(ws, boom_conn):
    assert db.get_import_meta(ws, "s") == []


def test_get_last_import_date_error_none(ws, boom_conn):
    assert db.get_last_import_date(ws, "s", "A") is None


# ── remove_factor 成功/异常 ─────────────────────────────────


def test_remove_factor_error_false(ws, boom_conn, monkeypatch):
    # remove_factor 用 get_connection（写模式）；boom 后返回 False
    assert db.remove_factor(ws, "s", "f") is False


def test_remove_factor_success(ws):
    db.register_factor(ws, "s", "f", "ts_mean(close, 5)")
    assert db.remove_factor(ws, "s", "f") is True
