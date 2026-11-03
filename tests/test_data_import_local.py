"""H 档补测 —— `apps/data_import.py` 的本地文件导入路径。

此前 21%：全靠 monkeypatch 掉网络 loader 才跑到一点。真实可测的是
**本地文件导入**（CSV / Parquet）与两个纯转换函数——它们不碰网络，
不碰外部数据源，只是「读文件 → 校验长格式 → 转宽表映射 → 落 DuckDB」。

docs 里写着「缺任一必需列会抛 ValueError；不会合成假数据」，这条
fail-loud 契约此前无测试覆盖。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from strategy_research.apps import data_import as di


# ── _validate_long_ohlcv（fail-loud 契约） ───────────────────


def _long_df(n=6, assets=("A", "B")) -> pd.DataFrame:
    rows = []
    for i in range(n):
        for a in assets:
            rows.append({
                "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=i),
                "asset": a,
                "open": 10.0 + i, "high": 11.0 + i, "low": 9.0 + i,
                "close": 10.5 + i, "volume": 1000.0,
            })
    return pd.DataFrame(rows)


def test_validate_accepts_complete_long_frame():
    di._validate_long_ohlcv(_long_df(), "date", "asset")   # 不抛即通过


def test_validate_rejects_missing_ohlc_column():
    df = _long_df().drop(columns=["close"])
    with pytest.raises(ValueError, match="missing required columns"):
        di._validate_long_ohlcv(df, "date", "asset")


def test_validate_rejects_missing_date_column():
    df = _long_df().rename(columns={"date": "trade_date"})
    with pytest.raises(ValueError, match="missing required columns"):
        di._validate_long_ohlcv(df, "date", "asset")


def test_validate_error_lists_actual_columns():
    df = pd.DataFrame({"x": [1], "y": [2]})
    with pytest.raises(ValueError) as exc:
        di._validate_long_ohlcv(df, "date", "asset")
    assert "'x'" in str(exc.value) and "'y'" in str(exc.value)


def test_validate_custom_column_names():
    df = _long_df().rename(columns={"date": "trade_date", "asset": "code"})
    di._validate_long_ohlcv(df, "trade_date", "code")


# ── _long_to_ohlcv_map ──────────────────────────────────────


def test_long_to_map_splits_per_asset():
    m = di._long_to_ohlcv_map(_long_df(), "date", "asset")
    assert set(m) == {"A", "B"}
    for df in m.values():
        assert isinstance(df.index, pd.DatetimeIndex)
        assert "close" in df.columns


def test_long_to_map_sorted_by_date():
    df = _long_df().sample(frac=1.0, random_state=0)   # 打乱顺序
    m = di._long_to_ohlcv_map(df, "date", "asset")
    for sub in m.values():
        assert sub.index.is_monotonic_increasing


def test_long_to_map_dedupes_dates_keeping_last():
    df = _long_df(assets=("A",))
    dup = pd.concat([df, df.assign(close=99.0)], ignore_index=True)
    m = di._long_to_ohlcv_map(dup, "date", "asset")
    # 同一天只留一行（keep="last" → 后写入的 99.0）
    a = m["A"]
    assert len(a) == len(df)
    assert (a["close"] == 99.0).all()


def test_long_to_map_without_volume():
    df = _long_df().drop(columns=["volume"])
    m = di._long_to_ohlcv_map(df, "date", "asset")
    assert "volume" not in m["A"].columns


def test_long_to_map_casts_volume_to_float():
    df = _long_df()
    df["volume"] = df["volume"].astype(int)
    m = di._long_to_ohlcv_map(df, "date", "asset")
    assert m["A"]["volume"].dtype == float


# ── import_csv_ohlcv ────────────────────────────────────────


@pytest.fixture
def ws(tmp_path):
    from strategy_research.core import db
    assert db.init_db(tmp_path) is True
    return tmp_path


def test_import_csv_roundtrip(ws):
    csv = ws / "prices.csv"
    _long_df().to_csv(csv, index=False)
    ok = di.import_csv_ohlcv(ws, "s1", csv)
    assert ok is True

    from strategy_research.core import db
    panel = db.load_price_data(ws, "s1")
    assert panel is not None and not panel.empty


def test_import_csv_custom_columns(ws):
    csv = ws / "prices.csv"
    _long_df().rename(columns={"date": "trade_date", "asset": "code"}).to_csv(
        csv, index=False)
    ok = di.import_csv_ohlcv(ws, "s1", csv,
                             date_column="trade_date", asset_column="code")
    assert ok is True


def test_import_csv_missing_file_raises_or_false(ws):
    """文件不存在 → 明确失败（抛 or False），不静默成功。"""
    try:
        result = di.import_csv_ohlcv(ws, "s1", ws / "nope.csv")
    except (FileNotFoundError, OSError, ValueError):
        return
    assert result is False


def test_import_csv_bad_schema_raises(ws):
    """列缺失 → ValueError（fail-loud，不合成假数据）。"""
    csv = ws / "bad.csv"
    pd.DataFrame({"date": ["2024-01-01"], "asset": ["A"]}).to_csv(
        csv, index=False)
    with pytest.raises(ValueError):
        di.import_csv_ohlcv(ws, "s1", csv)


# ── import_parquet_ohlcv ────────────────────────────────────


def test_import_parquet_roundtrip(ws):
    pq = ws / "prices.parquet"
    _long_df().to_parquet(pq, index=False)
    ok = di.import_parquet_ohlcv(ws, "s1", pq)
    assert ok is True

    from strategy_research.core import db
    panel = db.load_price_data(ws, "s1")
    assert panel is not None and not panel.empty


def test_import_parquet_bad_schema_raises(ws):
    pq = ws / "bad.parquet"
    pd.DataFrame({"date": ["2024-01-01"], "asset": ["A"]}).to_parquet(
        pq, index=False)
    with pytest.raises(ValueError):
        di.import_parquet_ohlcv(ws, "s1", pq)


# ── generate_sample_data（无外部依赖的合成器） ─────────────


def test_generate_sample_data_shape():
    """宽面板：index=date，columns=asset_NNN，值=close。"""
    df = di.generate_sample_data(n_assets=3, n_days=30)
    assert isinstance(df, pd.DataFrame)
    assert df.shape == (30, 3)
    assert isinstance(df.index, pd.DatetimeIndex)


def test_generate_sample_data_is_seeded():
    """固定 seed：两次调用结果一致（可重放）。"""
    a = di.generate_sample_data(n_assets=2, n_days=10)
    b = di.generate_sample_data(n_assets=2, n_days=10)
    pd.testing.assert_frame_equal(a, b)


def test_generate_sample_ohlcv_data_returns_code_map():
    """返回 dict[code -> DataFrame]，与 loader.fetch() 同形。"""
    m = di.generate_sample_ohlcv_data(n_assets=2, n_days=20)
    assert isinstance(m, dict) and m
    for df in m.values():
        for col in ("open", "high", "low", "close"):
            assert col in df.columns


def test_generate_sample_ohlcv_respects_price_invariants():
    """high >= max(open, close) 且 low <= min(open, close)。"""
    m = di.generate_sample_ohlcv_data(n_assets=2, n_days=20)
    for df in m.values():
        hi = df["high"]
        lo = df["low"]
        assert (hi >= df[["open", "close"]].max(axis=1) - 1e-9).all()
        assert (lo <= df[["open", "close"]].min(axis=1) + 1e-9).all()
