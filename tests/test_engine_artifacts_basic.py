"""Tests for engine artifacts module."""

from __future__ import annotations

import json

import pandas as pd

from strategy_research.core.engine.artifacts import (
    write_all_artifacts,
    write_equity_curve,
    write_metrics_json,
    write_ohlcv_snapshots,
    write_trades_csv,
)
from strategy_research.foundation.utils.backtest_models import (
    EquitySnapshot,
    TradeRecord,
)


class TestWriteEquityCurve:
    def test_write_empty(self, tmp_path):
        write_equity_curve(tmp_path, [])
        # No file should be written
        assert not (tmp_path / "equity_curve.csv").exists()

    def test_write_snapshots(self, tmp_path):
        snapshots = [
            EquitySnapshot(timestamp=pd.Timestamp("2023-01-01"), capital=100000, unrealized=0, equity=100000),
            EquitySnapshot(timestamp=pd.Timestamp("2023-01-02"), capital=100000, unrealized=500, equity=100500),
        ]
        write_equity_curve(tmp_path, snapshots)

        assert (tmp_path / "equity_curve.csv").exists()
        df = pd.read_csv(tmp_path / "equity_curve.csv", index_col=0)
        assert len(df) == 2
        assert df.iloc[0]["equity"] == 100000
        assert df.iloc[1]["equity"] == 100500


class TestWriteTradesCsv:
    def test_write_empty(self, tmp_path):
        write_trades_csv(tmp_path, [])
        assert not (tmp_path / "trades.csv").exists()

    def test_write_trades(self, tmp_path):
        trades = [
            TradeRecord(
                symbol="AAPL",
                direction=1,
                entry_price=100,
                exit_price=110,
                entry_time=pd.Timestamp("2023-01-01"),
                exit_time=pd.Timestamp("2023-01-10"),
                size=10,
                leverage=1.0,
                pnl=100,
                pnl_pct=0.10,
                exit_reason="signal",
                holding_bars=10,
                commission=1.0,
            ),
        ]
        write_trades_csv(tmp_path, trades)

        assert (tmp_path / "trades.csv").exists()
        df = pd.read_csv(tmp_path / "trades.csv")
        assert len(df) == 1
        assert df.iloc[0]["symbol"] == "AAPL"
        assert df.iloc[0]["pnl"] == 100


class TestWriteOhlcvSnapshots:
    def test_write_ohlcv(self, tmp_path):
        dates = pd.date_range("2023-01-01", periods=5)
        df = pd.DataFrame({
            "open": [100, 101, 102, 103, 104],
            "high": [101, 102, 103, 104, 105],
            "low": [99, 100, 101, 102, 103],
            "close": [100.5, 101.5, 102.5, 103.5, 104.5],
            "volume": [1000, 1100, 1200, 1300, 1400],
        }, index=dates)

        data_map = {"AAPL": df}
        write_ohlcv_snapshots(tmp_path, data_map, ["AAPL"])

        ohlcv_dir = tmp_path / "ohlcv"
        assert ohlcv_dir.is_dir()
        assert (ohlcv_dir / "AAPL.csv").exists()

    def test_safe_filename(self, tmp_path):
        dates = pd.date_range("2023-01-01", periods=3)
        df = pd.DataFrame({"close": [100, 101, 102]}, index=dates)

        data_map = {"BRK.B": df, "HK/00700": df}
        write_ohlcv_snapshots(tmp_path, data_map, ["BRK.B", "HK/00700"])

        ohlcv_dir = tmp_path / "ohlcv"
        assert (ohlcv_dir / "BRK_B.csv").exists()
        assert (ohlcv_dir / "HK_00700.csv").exists()


class TestWriteMetricsJson:
    def test_basic_metrics(self, tmp_path):
        metrics = {
            "sharpe": 1.5,
            "max_dd": -0.15,
            "n_trades": 10,
        }
        write_metrics_json(tmp_path, metrics)

        assert (tmp_path / "metrics.json").exists()
        loaded = json.loads((tmp_path / "metrics.json").read_text())
        assert loaded["sharpe"] == 1.5
        assert loaded["n_trades"] == 10

    def test_nan_to_none(self, tmp_path):
        metrics = {
            "sharpe": float("nan"),
            "inf_value": float("inf"),
            "neg_inf": float("-inf"),
            "valid": 1.5,
        }
        write_metrics_json(tmp_path, metrics)

        loaded = json.loads((tmp_path / "metrics.json").read_text())
        assert loaded["sharpe"] is None
        assert loaded["inf_value"] is None
        assert loaded["neg_inf"] is None
        assert loaded["valid"] == 1.5


class TestWriteAllArtifacts:
    def test_write_all(self, tmp_path):
        # Create a mock engine
        class MockEngine:
            equity_snapshots = [
                EquitySnapshot(timestamp=pd.Timestamp("2023-01-01"), capital=100000, unrealized=0, equity=100000),
            ]
            trades = [
                TradeRecord(
                    symbol="AAPL",
                    direction=1,
                    entry_price=100,
                    exit_price=110,
                    entry_time=pd.Timestamp("2023-01-01"),
                    exit_time=pd.Timestamp("2023-01-10"),
                    size=10,
                    leverage=1.0,
                    pnl=100,
                    pnl_pct=0.10,
                    exit_reason="signal",
                    holding_bars=10,
                ),
            ]

        dates = pd.date_range("2023-01-01", periods=5)
        df = pd.DataFrame({"close": [100, 101, 102, 103, 104]}, index=dates)
        data_map = {"AAPL": df}
        metrics = {"sharpe": 1.5}

        write_all_artifacts(tmp_path, MockEngine(), data_map, ["AAPL"], metrics)

        assert (tmp_path / "equity_curve.csv").exists()
        assert (tmp_path / "trades.csv").exists()
        assert (tmp_path / "ohlcv" / "AAPL.csv").exists()
        assert (tmp_path / "metrics.json").exists()
