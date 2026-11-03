"""Tests for load_ohlcv_data — DuckDB OHLCV 读取 for bar-by-bar engine"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from strategy_research.core.db import (
    init_db,
    load_ohlcv_data,
    load_price_data,
    save_ohlcv_data,
)


def _make_ohlcv(start: str = "2024-01-02", n_days: int = 10, base_price: float = 100.0) -> pd.DataFrame:
    """创建测试用 OHLCV DataFrame。"""
    dates = pd.bdate_range(start, periods=n_days)
    data = {
        "open": [base_price + i * 0.5 for i in range(n_days)],
        "high": [base_price + i * 0.5 + 1.0 for i in range(n_days)],
        "low": [base_price + i * 0.5 - 0.5 for i in range(n_days)],
        "close": [base_price + i * 0.5 + 0.5 for i in range(n_days)],
        "volume": [1000.0 + i * 100 for i in range(n_days)],
    }
    return pd.DataFrame(data, index=dates)


def _setup_workspace(tmp_path: Path, strategy: str, codes: list[str]) -> Path:
    """创建测试工作区并写入 OHLCV 数据。"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    init_db(ws)
    for code in codes:
        ohlcv = _make_ohlcv(base_price=100.0 if code == "A" else 200.0)
        save_ohlcv_data(ws, strategy, code, ohlcv)
    return ws


# ============================================================
# load_ohlcv_data — 基本功能
# ============================================================


class TestLoadOhlcvBasic:
    def test_load_all_codes(self, tmp_path):
        ws = _setup_workspace(tmp_path, "s1", ["A", "B"])
        result = load_ohlcv_data(ws, "s1")
        assert len(result) == 2
        assert "A" in result
        assert "B" in result

    def test_load_single_code(self, tmp_path):
        ws = _setup_workspace(tmp_path, "s1", ["A", "B"])
        result = load_ohlcv_data(ws, "s1", codes=["A"])
        assert len(result) == 1
        assert "A" in result

    def test_returns_ohlcv_columns(self, tmp_path):
        ws = _setup_workspace(tmp_path, "s1", ["A"])
        result = load_ohlcv_data(ws, "s1")
        df = result["A"]
        assert set(df.columns) == {"open", "high", "low", "close", "volume"}

    def test_index_is_datetime(self, tmp_path):
        ws = _setup_workspace(tmp_path, "s1", ["A"])
        result = load_ohlcv_data(ws, "s1")
        assert isinstance(result["A"].index, pd.DatetimeIndex)

    def test_numeric_types(self, tmp_path):
        ws = _setup_workspace(tmp_path, "s1", ["A"])
        result = load_ohlcv_data(ws, "s1")
        for col in ["open", "high", "low", "close", "volume"]:
            assert pd.api.types.is_numeric_dtype(result["A"][col])

    def test_empty_strategy(self, tmp_path):
        ws = _setup_workspace(tmp_path, "s1", ["A"])
        result = load_ohlcv_data(ws, "nonexistent")
        assert result == {}


# ============================================================
# load_ohlcv_data — 过滤
# ============================================================


class TestLoadOhlcvFilter:
    def test_date_filter(self, tmp_path):
        ws = _setup_workspace(tmp_path, "s1", ["A"])
        result = load_ohlcv_data(ws, "s1", start_date="2024-01-05")
        assert len(result["A"]) < 10

    def test_date_range(self, tmp_path):
        ws = _setup_workspace(tmp_path, "s1", ["A"])
        result = load_ohlcv_data(ws, "s1", start_date="2024-01-03", end_date="2024-01-08")
        assert 3 <= len(result["A"]) <= 6

    def test_codes_filter(self, tmp_path):
        ws = _setup_workspace(tmp_path, "s1", ["A", "B", "C"])
        result = load_ohlcv_data(ws, "s1", codes=["A", "C"])
        assert set(result.keys()) == {"A", "C"}


# ============================================================
# load_ohlcv_data — 数据完整性
# ============================================================


class TestLoadOhlcvIntegrity:
    def test_ohlcv_values_match_saved(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        init_db(ws)
        ohlcv = _make_ohlcv(start="2024-01-02", n_days=5, base_price=50.0)
        save_ohlcv_data(ws, "s1", "TEST", ohlcv)

        result = load_ohlcv_data(ws, "s1", codes=["TEST"])
        df = result["TEST"]

        # 验证 close 值
        expected_close = [50.5, 51.0, 51.5, 52.0, 52.5]
        assert list(df["close"]) == pytest.approx(expected_close)

        # 验证 volume
        expected_vol = [1000.0, 1100.0, 1200.0, 1300.0, 1400.0]
        assert list(df["volume"]) == pytest.approx(expected_vol)

    def test_high_gte_low(self, tmp_path):
        ws = _setup_workspace(tmp_path, "s1", ["A"])
        result = load_ohlcv_data(ws, "s1")
        df = result["A"]
        assert (df["high"] >= df["low"]).all()

    def test_high_gte_close(self, tmp_path):
        ws = _setup_workspace(tmp_path, "s1", ["A"])
        result = load_ohlcv_data(ws, "s1")
        df = result["A"]
        assert (df["high"] >= df["close"]).all()

    def test_low_lte_close(self, tmp_path):
        ws = _setup_workspace(tmp_path, "s1", ["A"])
        result = load_ohlcv_data(ws, "s1")
        df = result["A"]
        assert (df["low"] <= df["close"]).all()


# ============================================================
# load_ohlcv_data vs load_price_data 一致性
# ============================================================


class TestOhlcvVsPriceData:
    def test_close_matches_price_panel(self, tmp_path):
        """load_ohlcv_data 的 close 应与 load_price_data 的 panel 一致。"""
        ws = _setup_workspace(tmp_path, "s1", ["A", "B"])

        ohlcv = load_ohlcv_data(ws, "s1")
        panel = load_price_data(ws, "s1")

        for code in ["A", "B"]:
            ohlcv_close = ohlcv[code]["close"]
            panel_close = panel[code]
            pd.testing.assert_series_equal(ohlcv_close, panel_close, check_names=False)

    def test_save_price_data_was_removed(self, tmp_path):
        """save_price_data removed 2026-08-05 — must no longer be importable.

        Replaces the old test_ohlcv_preserves_open_high_low which exercised
        save_price_data's fake-OHLCV path.  That path was a source of
        downstream pollution and has been deleted; callers should use
        save_ohlcv_data / save_ohlcv_to_db which preserve real OHLCV.
        """
        import pytest as _pytest

        with _pytest.raises(ImportError):
            from strategy_research.core.db import save_price_data  # noqa: F401


# ============================================================
# load_ohlcv_data — 空输入
# ============================================================


class TestLoadOhlcvEmpty:
    def test_no_data_in_db(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        init_db(ws)
        result = load_ohlcv_data(ws, "nonexistent")
        assert result == {}

    def test_empty_codes_list(self, tmp_path):
        ws = _setup_workspace(tmp_path, "s1", ["A"])
        result = load_ohlcv_data(ws, "s1", codes=[])
        # Empty codes list means no IN filter → returns all
        assert len(result) == 1


# ============================================================
# load_ohlcv_data — 多策略隔离
# ============================================================


class TestLoadOhlcvIsolation:
    def test_different_strategies_isolated(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        init_db(ws)
        save_ohlcv_data(ws, "s1", "A", _make_ohlcv(base_price=100.0))
        save_ohlcv_data(ws, "s2", "A", _make_ohlcv(base_price=200.0))

        r1 = load_ohlcv_data(ws, "s1")
        r2 = load_ohlcv_data(ws, "s2")

        assert r1["A"]["close"].iloc[0] == pytest.approx(100.5)
        assert r2["A"]["close"].iloc[0] == pytest.approx(200.5)


# ============================================================
# import_csv_ohlcv — long format OHLCV CSV import
# ============================================================


class TestImportCsvOhlcv:
    def _write_csv(self, tmp_path: Path, name: str, df: pd.DataFrame) -> Path:
        p = tmp_path / name
        df.to_csv(p, index=False)
        return p

    def _setup_ws(self, tmp_path: Path, strategy: str = "s1") -> Path:
        ws = tmp_path / "ws"
        ws.mkdir()
        init_db(ws)
        return ws

    def test_happy_path_with_volume(self, tmp_path):
        from strategy_research.apps.data_import import import_csv_ohlcv

        ws = self._setup_ws(tmp_path)
        dates = pd.bdate_range("2024-01-02", periods=5)
        long = pd.DataFrame(
            {
                "date": list(dates) * 2,
                "asset": ["A"] * 5 + ["B"] * 5,
                "open": [100.0] * 5 + [200.0] * 5,
                "high": [101.0] * 5 + [201.0] * 5,
                "low": [99.0] * 5 + [199.0] * 5,
                "close": [100.5] * 5 + [200.5] * 5,
                "volume": [1000.0] * 5 + [2000.0] * 5,
            }
        )
        csv = self._write_csv(tmp_path, "ohlcv.csv", long)

        success = import_csv_ohlcv(ws, "s1", csv)
        assert success is True

        ohlcv = load_ohlcv_data(ws, "s1")
        assert set(ohlcv.keys()) == {"A", "B"}
        assert ohlcv["A"]["close"].iloc[0] == 100.5
        assert ohlcv["B"]["open"].iloc[0] == 200.0
        assert ohlcv["A"]["volume"].iloc[0] == 1000.0

    def test_missing_close_column_raises(self, tmp_path):
        from strategy_research.apps.data_import import import_csv_ohlcv

        ws = self._setup_ws(tmp_path)
        long = pd.DataFrame(
            {
                "date": pd.bdate_range("2024-01-02", periods=3),
                "asset": ["A"] * 3,
                "open": [100.0] * 3,
                "high": [101.0] * 3,
                "low": [99.0] * 3,
                # close MISSING
            }
        )
        csv = self._write_csv(tmp_path, "bad.csv", long)

        with pytest.raises(ValueError, match="missing required columns"):
            import_csv_ohlcv(ws, "s1", csv)

    def test_missing_file_raises(self, tmp_path):
        from strategy_research.apps.data_import import import_csv_ohlcv

        ws = self._setup_ws(tmp_path)
        with pytest.raises(FileNotFoundError, match="不存在"):
            import_csv_ohlcv(ws, "s1", tmp_path / "nope.csv")

    def test_no_volume_column_works(self, tmp_path):
        from strategy_research.apps.data_import import import_csv_ohlcv

        ws = self._setup_ws(tmp_path)
        dates = pd.bdate_range("2024-01-02", periods=3)
        long = pd.DataFrame(
            {
                "date": list(dates),
                "asset": ["A"] * 3,
                "open": [100.0] * 3,
                "high": [101.0] * 3,
                "low": [99.0] * 3,
                "close": [100.5] * 3,
                # volume optional
            }
        )
        csv = self._write_csv(tmp_path, "no_vol.csv", long)
        assert import_csv_ohlcv(ws, "s1", csv) is True
        # volume is part of DuckDB price_data schema (NOT NULL with default
        # 0), so load_ohlcv_data always returns it.  The default is 0.0
        # when input CSV omitted the column.
        ohlcv = load_ohlcv_data(ws, "s1")
        assert "volume" in ohlcv["A"].columns
        assert (ohlcv["A"]["volume"] == 0.0).all()
