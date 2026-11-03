"""Tests for artifacts — run card / equity / trades / metrics 输出"""

from __future__ import annotations

import json

import pandas as pd

from strategy_research.apps.engine.artifacts import (
    write_all_artifacts,
    write_equity_curve,
    write_metrics_json,
    write_ohlcv_snapshots,
    write_trades_csv,
)
from strategy_research.apps.engine.models import EquitySnapshot, TradeRecord


def _make_snapshots(n: int = 10) -> list:
    dates = pd.bdate_range("2024-01-02", periods=n)
    return [
        EquitySnapshot(
            timestamp=dates[i],
            capital=1_000_000 - i * 100,
            unrealized=i * 200,
            equity=1_000_000 + i * 100,
            positions=min(i, 3),
        )
        for i in range(n)
    ]


def _make_trades(n: int = 5) -> list:
    dates = pd.bdate_range("2024-01-02", periods=n * 2)
    trades = []
    for i in range(n):
        trades.append(TradeRecord(
            symbol="AAPL",
            direction=1,
            entry_price=150.0 + i,
            exit_price=155.0 + i,
            entry_time=dates[i * 2],
            exit_time=dates[i * 2 + 1],
            size=100,
            leverage=1.0,
            pnl=500.0 + i * 100,
            pnl_pct=3.3,
            exit_reason="signal",
            holding_bars=5,
            commission=10.0,
        ))
    return trades


class TestWriteEquityCurve:
    def test_creates_csv(self, tmp_path):
        snapshots = _make_snapshots()
        write_equity_curve(tmp_path, snapshots)
        assert (tmp_path / "equity_curve.csv").exists()

    def test_csv_content(self, tmp_path):
        snapshots = _make_snapshots(3)
        write_equity_curve(tmp_path, snapshots)
        df = pd.read_csv(tmp_path / "equity_curve.csv", index_col=0)
        assert len(df) == 3
        assert "equity" in df.columns

    def test_empty_snapshots(self, tmp_path):
        write_equity_curve(tmp_path, [])
        assert not (tmp_path / "equity_curve.csv").exists()


class TestWriteTradesCsv:
    def test_creates_csv(self, tmp_path):
        trades = _make_trades()
        write_trades_csv(tmp_path, trades)
        assert (tmp_path / "trades.csv").exists()

    def test_csv_content(self, tmp_path):
        trades = _make_trades(3)
        write_trades_csv(tmp_path, trades)
        df = pd.read_csv(tmp_path / "trades.csv")
        assert len(df) == 3
        assert "symbol" in df.columns
        assert "pnl" in df.columns

    def test_empty_trades(self, tmp_path):
        write_trades_csv(tmp_path, [])
        assert not (tmp_path / "trades.csv").exists()


class TestWriteOhlcvSnapshots:
    def test_creates_ohlcv_dir(self, tmp_path):
        data = {
            "AAPL": pd.DataFrame({
                "open": [150.0], "high": [151.0], "low": [149.0],
                "close": [150.5], "volume": [1000.0],
            }, index=pd.bdate_range("2024-01-02", periods=1)),
        }
        write_ohlcv_snapshots(tmp_path, data, ["AAPL"])
        assert (tmp_path / "ohlcv" / "AAPL.csv").exists()


class TestWriteMetricsJson:
    def test_creates_json(self, tmp_path):
        metrics = {"sharpe": 1.5, "max_drawdown": -0.1, "trade_count": 10}
        write_metrics_json(tmp_path, metrics)
        assert (tmp_path / "metrics.json").exists()

    def test_json_content(self, tmp_path):
        metrics = {"sharpe": 1.5, "nan_value": float("nan"), "inf_value": float("inf")}
        write_metrics_json(tmp_path, metrics)
        with open(tmp_path / "metrics.json") as f:
            data = json.load(f)
        assert data["sharpe"] == 1.5
        assert data["nan_value"] is None
        assert data["inf_value"] is None


class TestWriteAllArtifacts:
    def test_creates_all_files(self, tmp_path):
        class MockEngine:
            equity_snapshots = _make_snapshots(5)
            trades = _make_trades(3)

        data = {"AAPL": pd.DataFrame({
            "open": [150.0], "high": [151.0], "low": [149.0],
            "close": [150.5], "volume": [1000.0],
        }, index=pd.bdate_range("2024-01-02", periods=1))}

        run_dir = tmp_path / "run_0001"
        write_all_artifacts(run_dir, MockEngine(), data, ["AAPL"], {"sharpe": 1.0})

        assert (run_dir / "equity_curve.csv").exists()
        assert (run_dir / "trades.csv").exists()
        assert (run_dir / "metrics.json").exists()
        assert (run_dir / "ohlcv" / "AAPL.csv").exists()
