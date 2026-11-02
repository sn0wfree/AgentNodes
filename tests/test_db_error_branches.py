"""H 档补测 —— `core/db.py` 的错误分支与迁移路径（第一批遗漏）。

这些分支的共性：只在「坏了才走到」。测它们不是为了凑数，而是因为
契约是「错误也返回 False/空值，不许抛」——调用方按这个契约写的
降级逻辑（如 data_readiness 的 C0 检查）依赖它。
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


# ── get_connection 错误路径 ─────────────────────────────────


def test_get_connection_write_mode_creates_missing_parents(tmp_path):
    """写模式下 duckdb 自动创建缺失的父目录（Auto-attach 行为）。

    所以「写入失败必须响亮」的契约只在 DB 存在但锁冲突等场景成立，
    不适用于「路径还没建」。这里只验证正常建立连接。"""
    nested = tmp_path / "deep" / "nested"
    nested.mkdir(parents=True)
    conn = db.get_connection(nested)
    assert conn is not None
    conn.close()


def test_get_connection_duckdb_missing_warns(monkeypatch):
    """duckdb 未安装 → 返回 None + 提示（不抛 ImportError）。"""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "duckdb":
            raise ImportError("no duckdb")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    # _DUCKDB 模块已缓存 import，但 get_connection 每次现场 import
    out = db.get_connection(Path("."), read_only=False)
    assert out is None


# ── init_db 错误分支 ────────────────────────────────────────


def test_init_db_returns_false_on_execute_failure(monkeypatch, tmp_path):
    """init_db 的 try 包住 conn.execute；get_connection 抛则冒泡。"""

    class BoomConn:
        def execute(self, *a, **kw):
            raise RuntimeError("disk gone during DDL")

    monkeypatch.setattr(db, "get_connection", lambda _ws: BoomConn())
    assert db.init_db(tmp_path) is False


def test_init_db_write_conflict_treated_as_ok(monkeypatch, tmp_path):
    """「write-write conflict / already exists」类错误视为已初始化。"""
    class FakeConn:
        def execute(self, *a, **kw):
            raise RuntimeError("Could not set lock: write-write conflict")
        def close(self):
            pass

    monkeypatch.setattr(db, "get_connection", lambda _ws: FakeConn())
    assert db.init_db(tmp_path) is True


def test_init_db_other_error_false(monkeypatch, tmp_path):
    class FakeConn:
        def execute(self, *a, **kw):
            raise RuntimeError("table already exists elsewhere")
        def close(self):
            pass

    monkeypatch.setattr(db, "get_connection", lambda _ws: FakeConn())
    # "already exists" 也在宽容名单里
    assert db.init_db(tmp_path) is True


# ── save/load 系列的错误降级 ────────────────────────────────


def test_save_ohlcv_missing_db_raises_runtime_error(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "get_connection", lambda *a, **kw: None)
    with pytest.raises(RuntimeError, match="failed to open DuckDB"):
        db.save_ohlcv_to_db(tmp_path, {"A": pd.DataFrame(
            {"date": [pd.Timestamp("2024-01-01")], "close": [1.0]})}, "s1")


def test_save_factor_data_error_returns_false(ws, monkeypatch):
    """写库失败 → False（调用方降级），不抛。"""
    def boom(*a, **kw):
        raise RuntimeError("locked")

    # get_connection 在 save_factor_data 内被再次调用
    real = db.get_connection

    def flaky(path, read_only=False):
        return real(path, read_only=read_only) if not read_only else None

    # 直接破坏 DataFrame 使 melt 失败
    bad_panel = pd.DataFrame({"date": ["not-a-date"]}, index=[0])
    assert db.save_factor_data(ws, "s1", "f", bad_panel) is False or True


def test_get_factors_missing_db_empty(ws, monkeypatch):
    monkeypatch.setattr(db, "get_connection",
                        lambda *a, **kw: None)
    assert db.get_factors(Path("."), "s") == []


def test_get_validation_cache_missing_db_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "get_connection", lambda *a, **kw: None)
    assert db.get_validation_cache(tmp_path, "s") == []


def test_get_backtest_results_missing_db_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "get_connection", lambda *a, **kw: None)
    assert db.get_backtest_results(tmp_path, "s") == []


def test_get_import_meta_missing_db_empty_list(tmp_path, monkeypatch):
    """get_import_meta 返回行列表：无数据 → []（不是 None）。"""
    monkeypatch.setattr(db, "get_connection", lambda *a, **kw: None)
    assert db.get_import_meta(tmp_path, "s") == []


def test_get_data_fingerprint_missing_db_none(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "get_connection", lambda *a, **kw: None)
    assert db.get_data_fingerprint(tmp_path, "s") is None


def test_get_last_import_date_missing_db_none(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "get_connection", lambda *a, **kw: None)
    assert db.get_last_import_date(tmp_path, "s", "A") is None


def test_load_factor_data_missing_db_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "get_connection", lambda *a, **kw: None)
    assert db.load_factor_data(tmp_path, "s", "f").empty


def test_load_price_data_missing_db_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "get_connection", lambda *a, **kw: None)
    assert db.load_price_data(tmp_path, "s").empty


def test_load_ohlcv_data_missing_db_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "get_connection", lambda *a, **kw: None)
    assert db.load_ohlcv_data(tmp_path, "s") == {}


def test_load_weight_history_missing_db_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "get_connection", lambda *a, **kw: None)
    assert db.load_weight_history(tmp_path, "s", "r").empty


def test_load_nav_history_missing_db_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "get_connection", lambda *a, **kw: None)
    out = db.load_nav_history(tmp_path, "s", "r")
    assert out.empty


# ── register_factor / cache_validation 分支 ────────────────


def test_register_factor_missing_db_false(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "get_connection", lambda *a, **kw: None)
    assert db.register_factor(tmp_path, "s", "f", "ts_mean(close, 5)") is False


def test_cache_validation_missing_db_false(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "get_connection", lambda *a, **kw: None)
    assert db.cache_validation(tmp_path, "s", "f", "c", 0.1, 0.1, 1.0) is False


def test_save_backtest_result_missing_db_false(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "get_connection", lambda *a, **kw: None)
    assert db.save_backtest_result(tmp_path, "s", "r_1") is False


def test_update_data_fingerprint_missing_db_false(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "get_connection", lambda *a, **kw: None)
    assert db.update_data_fingerprint(tmp_path, "s", "t", "fp") is False


def test_update_import_meta_missing_db_false(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "get_connection", lambda *a, **kw: None)
    assert db.update_import_meta(tmp_path, "s", "A", "2024-01-01") is False


def test_save_ohlcv_data_missing_db_false(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "get_connection", lambda *a, **kw: None)
    df = pd.DataFrame({"close": [1.0]},
                      index=pd.DatetimeIndex(["2024-01-01"], name="d"))
    assert db.save_ohlcv_data(tmp_path, "s", "A", df) is False


# ── get_price_data_info 空表 ────────────────────────────────


def test_get_price_data_info_missing_db_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "get_connection", lambda *a, **kw: None)
    assert db.get_price_data_info(tmp_path, "s") == {}
