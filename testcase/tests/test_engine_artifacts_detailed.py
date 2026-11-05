"""Artifacts 详细单元测试。

覆盖:
- write_equity_curve: empty list, valid snapshots, CSV format
- write_trades_csv: empty list, valid trades, all columns
- write_ohlcv_snapshots: symbols with special chars, subdir creation
- write_metrics_json: NaN→None, Inf→None, indent format
- write_all_artifacts: all files created in one call
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from strategy_research.apps.engine.artifacts import (
    write_all_artifacts,
    write_equity_curve,
    write_metrics_json,
    write_ohlcv_snapshots,
    write_trades_csv,
)
from strategy_research.apps.engine.models import EquitySnapshot, TradeRecord


# ─────────────────────────────────────────────
# write_equity_curve
# ─────────────────────────────────────────────
class TestWriteEquityCurve:
    def test_empty_list(self, tmp_path):
        write_equity_curve(tmp_path, [])
        assert not list(tmp_path.glob("equity_curve.csv"))

    def test_valid_snapshots(self, tmp_path):
        snaps = [
            EquitySnapshot(timestamp=pd.Timestamp("2024-01-02"), capital=1000000.0, unrealized=0.0, equity=1000000.0, positions=0),
            EquitySnapshot(timestamp=pd.Timestamp("2024-01-03"), capital=950000.0, unrealized=50000.0, equity=1000000.0, positions=1),
        ]
        write_equity_curve(tmp_path, snaps)
        csv = pd.read_csv(tmp_path / "equity_curve.csv")
        # CSV written with timestamp as index, but read_csv loads it as column
        assert "timestamp" in csv.columns
        assert "capital" in csv.columns
        assert len(csv) == 2

    def test_single_snapshot(self, tmp_path):
        snaps = [EquitySnapshot(timestamp=pd.Timestamp("2024-01-02"), capital=500000.0, unrealized=10000.0, equity=510000.0, positions=2)]
        write_equity_curve(tmp_path, snaps)
        csv = pd.read_csv(tmp_path / "equity_curve.csv")
        assert len(csv) == 1
        assert csv.iloc[0]["capital"] == pytest.approx(500000.0)


# ─────────────────────────────────────────────
# write_trades_csv
# ─────────────────────────────────────────────
class TestWriteTradesCsv:
    def test_empty_list(self, tmp_path):
        write_trades_csv(tmp_path, [])
        assert not list(tmp_path.glob("trades.csv"))

    def test_valid_trades(self, tmp_path):
        trade = TradeRecord(
            symbol="600000.SH", direction=1, entry_price=10.0, exit_price=11.0,
            entry_time=pd.Timestamp("2024-01-02"), exit_time=pd.Timestamp("2024-01-03"),
            size=1000, leverage=1.0, pnl=1000.0, pnl_pct=10.0,
            exit_reason="signal", holding_bars=1, commission=10.0,
        )
        write_trades_csv(tmp_path, [trade])
        csv = pd.read_csv(tmp_path / "trades.csv")
        assert len(csv) == 1
        assert csv.iloc[0]["symbol"] == "600000.SH"
        assert csv.iloc[0]["entry_price"] == pytest.approx(10.0)
        assert csv.iloc[0]["exit_price"] == pytest.approx(11.0)
        assert csv.iloc[0]["pnl"] == pytest.approx(1000.0)
        assert csv.iloc[0]["commission"] == pytest.approx(10.0)
        assert csv.iloc[0]["exit_reason"] == "signal"

    def test_all_columns_present(self, tmp_path):
        trade = TradeRecord(
            symbol="BTC-USDT", direction=-1, entry_price=40000.0, exit_price=39000.0,
            entry_time=pd.Timestamp("2024-01-02"), exit_time=pd.Timestamp("2024-01-03"),
            size=0.1, leverage=10.0, pnl=100.0, pnl_pct=2.5,
            exit_reason="liquidation", holding_bars=5, commission=50.0,
        )
        write_trades_csv(tmp_path, [trade])
        csv = pd.read_csv(tmp_path / "trades.csv")
        expected_cols = {"symbol", "direction", "entry_price", "exit_price",
                        "entry_time", "exit_time", "size", "leverage", "pnl",
                        "pnl_pct", "exit_reason", "holding_bars", "commission"}
        assert set(csv.columns) == expected_cols


# ─────────────────────────────────────────────
# write_ohlcv_snapshots
# ─────────────────────────────────────────────
class TestWriteOhlcvSnapshots:
    def test_creates_ohlcv_dir(self, tmp_path):
        df = pd.DataFrame({"open": [1.0], "close": [1.0]}, index=pd.bdate_range("2024-01-02", periods=1))
        write_ohlcv_snapshots(tmp_path, {"600000.SH": df}, ["600000.SH"])
        assert (tmp_path / "ohlcv").is_dir()

    def test_single_code(self, tmp_path):
        df = pd.DataFrame({"open": [1.0], "close": [2.0]}, index=pd.bdate_range("2024-01-02", periods=1))
        write_ohlcv_snapshots(tmp_path, {"600000.SH": df}, ["600000.SH"])
        csv = pd.read_csv(tmp_path / "ohlcv" / "600000_SH.csv")
        assert len(csv) == 1

    def test_slash_sanitized(self, tmp_path):
        df = pd.DataFrame({"open": [1.0], "close": [2.0]}, index=pd.bdate_range("2024-01-02", periods=1))
        write_ohlcv_snapshots(tmp_path, {"BTC/USDT": df}, ["BTC/USDT"])
        assert (tmp_path / "ohlcv" / "BTC_USDT.csv").exists()

    def test_dot_sanitized(self, tmp_path):
        df = pd.DataFrame({"open": [1.0], "close": [2.0]}, index=pd.bdate_range("2024-01-02", periods=1))
        write_ohlcv_snapshots(tmp_path, {"000001.SZ": df}, ["000001.SZ"])
        assert (tmp_path / "ohlcv" / "000001_SZ.csv").exists()

    def test_missing_code_skipped(self, tmp_path):
        df = pd.DataFrame({"open": [1.0], "close": [2.0]}, index=pd.bdate_range("2024-01-02", periods=1))
        write_ohlcv_snapshots(tmp_path, {"600000.SH": df}, ["600000.SH", "MISSING"])
        assert len(list((tmp_path / "ohlcv").glob("*.csv"))) == 1


# ─────────────────────────────────────────────
# write_metrics_json
# ─────────────────────────────────────────────
class TestWriteMetricsJson:
    def test_normal_metrics(self, tmp_path):
        metrics = {"total_return": 0.1, "sharpe": 1.5, "max_drawdown": -0.05}
        write_metrics_json(tmp_path, metrics)
        with open(tmp_path / "metrics.json") as f:
            data = json.load(f)
        assert data["total_return"] == pytest.approx(0.1)
        assert data["sharpe"] == pytest.approx(1.5)

    def test_nan_to_none(self, tmp_path):
        metrics = {"val": float("nan"), "ok": 1.0}
        write_metrics_json(tmp_path, metrics)
        with open(tmp_path / "metrics.json") as f:
            data = json.load(f)
        assert data["val"] is None
        assert data["ok"] == pytest.approx(1.0)

    def test_inf_to_none(self, tmp_path):
        metrics = {"pos_inf": float("inf"), "neg_inf": float("-inf")}
        write_metrics_json(tmp_path, metrics)
        with open(tmp_path / "metrics.json") as f:
            data = json.load(f)
        assert data["pos_inf"] is None
        assert data["neg_inf"] is None

    def test_indent_format(self, tmp_path):
        write_metrics_json(tmp_path, {"a": 1})
        content = (tmp_path / "metrics.json").read_text()
        # indent=2 means pretty-printed
        assert "  " in content

    def test_string_values(self, tmp_path):
        metrics = {"name": "strategy_v1"}
        write_metrics_json(tmp_path, metrics)
        with open(tmp_path / "metrics.json") as f:
            data = json.load(f)
        assert data["name"] == "strategy_v1"


# ─────────────────────────────────────────────
# write_all_artifacts
# ─────────────────────────────────────────────
class TestWriteAllArtifacts:
    def test_creates_all_files(self, tmp_path):
        code = "600000.SH"
        dates = pd.bdate_range("2024-01-02", periods=3)
        data_map = {
            code: pd.DataFrame({
                "open": [10.0, 10.1, 10.2],
                "high": [10.5, 10.5, 10.5],
                "low": [9.5, 9.5, 9.5],
                "close": [10.3, 10.4, 10.5],
                "volume": [1e6, 1e6, 1e6],
            }, index=dates),
        }
        class FakeEngine:
            equity_snapshots = [
                EquitySnapshot(timestamp=dates[0], capital=1000000.0, unrealized=0.0, equity=1000000.0),
                EquitySnapshot(timestamp=dates[1], capital=990000.0, unrealized=10000.0, equity=1000000.0),
            ]
            trades = [
                TradeRecord(
                    symbol=code, direction=1, entry_price=10.0, exit_price=10.3,
                    entry_time=dates[0], exit_time=dates[1], size=1000, leverage=1.0,
                    pnl=300.0, pnl_pct=3.0, exit_reason="signal", holding_bars=1, commission=10.0,
                ),
            ]

        metrics = {"total_return": 0.003, "sharpe": 1.2}
        write_all_artifacts(tmp_path, FakeEngine(), data_map, [code], metrics)

        assert (tmp_path / "equity_curve.csv").exists()
        assert (tmp_path / "trades.csv").exists()
        assert (tmp_path / "ohlcv").is_dir()
        assert (tmp_path / "metrics.json").exists()

    def test_auto_creates_parent_dirs(self, tmp_path):
        deep_dir = tmp_path / "strategies" / "my_strat" / "run_001"
        write_all_artifacts(deep_dir, type("E", (), {"equity_snapshots": [], "trades": []})(), {}, [], {})
        assert deep_dir.is_dir()
