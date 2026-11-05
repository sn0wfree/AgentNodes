"""Backtest models 详细单元测试。

覆盖:
- Position dataclass (frozen, hash, defaults)
- TradeRecord dataclass (frozen, required fields)
- EquitySnapshot dataclass (frozen, defaults)
"""
from __future__ import annotations

import pandas as pd
import pytest

from strategy_research.apps.engine.models import EquitySnapshot, Position, TradeRecord


# ─────────────────────────────────────────────
# Position
# ─────────────────────────────────────────────
class TestPosition:
    def test_instantiation(self):
        p = Position(
            symbol="600000.SH", direction=1, entry_price=10.0,
            entry_time=pd.Timestamp("2024-01-02"), size=1000,
        )
        assert p.symbol == "600000.SH"
        assert p.direction == 1
        assert p.entry_price == 10.0
        assert p.size == 1000

    def test_defaults(self):
        p = Position(
            symbol="AAPL", direction=1, entry_price=150.0,
            entry_time=pd.Timestamp("2024-01-02"), size=100,
        )
        assert p.leverage == 1.0
        assert p.entry_bar_idx == 0
        assert p.entry_commission == 0.0

    def test_custom_fields(self):
        p = Position(
            symbol="BTC-USDT", direction=-1, entry_price=40000.0,
            entry_time=pd.Timestamp("2024-01-02"), size=0.5,
            leverage=10.0, entry_bar_idx=5, entry_commission=20.0,
        )
        assert p.leverage == 10.0
        assert p.entry_bar_idx == 5
        assert p.entry_commission == 20.0

    def test_frozen(self):
        p = Position(
            symbol="X", direction=1, entry_price=10.0,
            entry_time=pd.Timestamp("2024-01-02"), size=100,
        )
        with pytest.raises(AttributeError):
            p.symbol = "Y"

    def test_hashable(self):
        p1 = Position(
            symbol="X", direction=1, entry_price=10.0,
            entry_time=pd.Timestamp("2024-01-02"), size=100,
        )
        p2 = Position(
            symbol="X", direction=1, entry_price=10.0,
            entry_time=pd.Timestamp("2024-01-02"), size=100,
        )
        assert p1 == p2
        assert hash(p1) == hash(p2)

    def test_not_equal_different_symbol(self):
        p1 = Position(
            symbol="X", direction=1, entry_price=10.0,
            entry_time=pd.Timestamp("2024-01-02"), size=100,
        )
        p2 = Position(
            symbol="Y", direction=1, entry_price=10.0,
            entry_time=pd.Timestamp("2024-01-02"), size=100,
        )
        assert p1 != p2

    def test_repr(self):
        p = Position(
            symbol="X", direction=1, entry_price=10.0,
            entry_time=pd.Timestamp("2024-01-02"), size=100,
        )
        assert "Position" in repr(p)
        assert "X" in repr(p)


# ─────────────────────────────────────────────
# TradeRecord
# ─────────────────────────────────────────────
class TestTradeRecord:
    def test_instantiation(self):
        t = TradeRecord(
            symbol="600000.SH", direction=1, entry_price=10.0, exit_price=11.0,
            entry_time=pd.Timestamp("2024-01-02"), exit_time=pd.Timestamp("2024-01-03"),
            size=1000, leverage=1.0, pnl=1000.0, pnl_pct=10.0,
            exit_reason="signal", holding_bars=1,
        )
        assert t.symbol == "600000.SH"
        assert t.pnl == 1000.0
        assert t.exit_reason == "signal"

    def test_defaults(self):
        t = TradeRecord(
            symbol="X", direction=1, entry_price=10.0, exit_price=11.0,
            entry_time=pd.Timestamp("2024-01-02"), exit_time=pd.Timestamp("2024-01-03"),
            size=100, leverage=1.0, pnl=100.0, pnl_pct=10.0,
            exit_reason="signal", holding_bars=1,
        )
        assert t.commission == 0.0

    def test_frozen(self):
        t = TradeRecord(
            symbol="X", direction=1, entry_price=10.0, exit_price=11.0,
            entry_time=pd.Timestamp("2024-01-02"), exit_time=pd.Timestamp("2024-01-03"),
            size=100, leverage=1.0, pnl=100.0, pnl_pct=10.0,
            exit_reason="signal", holding_bars=1,
        )
        with pytest.raises(AttributeError):
            t.pnl = 0.0

    def test_all_exit_reasons(self):
        reasons = ["signal", "liquidation", "end_of_backtest"]
        for reason in reasons:
            t = TradeRecord(
                symbol="X", direction=1, entry_price=10.0, exit_price=11.0,
                entry_time=pd.Timestamp("2024-01-02"), exit_time=pd.Timestamp("2024-01-03"),
                size=100, leverage=1.0, pnl=100.0, pnl_pct=10.0,
                exit_reason=reason, holding_bars=1,
            )
            assert t.exit_reason == reason

    def test_short_direction(self):
        t = TradeRecord(
            symbol="BTC-USDT", direction=-1, entry_price=40000.0, exit_price=39000.0,
            entry_time=pd.Timestamp("2024-01-02"), exit_time=pd.Timestamp("2024-01-03"),
            size=0.1, leverage=10.0, pnl=100.0, pnl_pct=2.5,
            exit_reason="signal", holding_bars=1,
        )
        assert t.direction == -1


# ─────────────────────────────────────────────
# EquitySnapshot
# ─────────────────────────────────────────────
class TestEquitySnapshot:
    def test_instantiation(self):
        s = EquitySnapshot(
            timestamp=pd.Timestamp("2024-01-02"),
            capital=1000000.0, unrealized=0.0, equity=1000000.0,
        )
        assert s.timestamp == pd.Timestamp("2024-01-02")
        assert s.capital == 1000000.0
        assert s.equity == 1000000.0

    def test_defaults(self):
        s = EquitySnapshot(
            timestamp=pd.Timestamp("2024-01-02"),
            capital=1000000.0, unrealized=0.0, equity=1000000.0,
        )
        assert s.positions == 0

    def test_custom_positions(self):
        s = EquitySnapshot(
            timestamp=pd.Timestamp("2024-01-02"),
            capital=950000.0, unrealized=50000.0, equity=1000000.0,
            positions=3,
        )
        assert s.positions == 3

    def test_frozen(self):
        s = EquitySnapshot(
            timestamp=pd.Timestamp("2024-01-02"),
            capital=1000000.0, unrealized=0.0, equity=1000000.0,
        )
        with pytest.raises(AttributeError):
            s.capital = 0.0

    def test_hashable(self):
        s1 = EquitySnapshot(
            timestamp=pd.Timestamp("2024-01-02"),
            capital=1000000.0, unrealized=0.0, equity=1000000.0,
        )
        s2 = EquitySnapshot(
            timestamp=pd.Timestamp("2024-01-02"),
            capital=1000000.0, unrealized=0.0, equity=1000000.0,
        )
        assert s1 == s2


# ─────────────────────────────────────────────
# Dataclass interop
# ─────────────────────────────────────────────
class TestDataclassInterop:
    def test_position_as_dict_key(self):
        p = Position(
            symbol="X", direction=1, entry_price=10.0,
            entry_time=pd.Timestamp("2024-01-02"), size=100,
        )
        d = {p: "value"}
        assert d[p] == "value"

    def test_equity_snapshot_as_list_item(self):
        s1 = EquitySnapshot(
            timestamp=pd.Timestamp("2024-01-02"),
            capital=1000000.0, unrealized=0.0, equity=1000000.0,
        )
        s2 = EquitySnapshot(
            timestamp=pd.Timestamp("2024-01-03"),
            capital=950000.0, unrealized=50000.0, equity=1000000.0,
        )
        snaps = sorted([s2, s1], key=lambda s: s.timestamp)
        assert snaps[0].timestamp == pd.Timestamp("2024-01-02")
