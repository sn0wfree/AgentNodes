"""Tests for backtest_models.py — Position / TradeRecord / EquitySnapshot"""

from __future__ import annotations

import dataclasses
import pickle
from copy import deepcopy

import pandas as pd
import pytest

from strategy_research.foundation.utils.backtest_models import (
    EquitySnapshot,
    Position,
    TradeRecord,
)

# ============================================================
# Position
# ============================================================


class TestPosition:
    def _make(self, **kwargs):
        defaults = dict(
            symbol="AAPL",
            direction=1,
            entry_price=150.0,
            entry_time=pd.Timestamp("2024-01-02"),
            size=100.0,
        )
        defaults.update(kwargs)
        return Position(**defaults)

    def test_construct_basic(self):
        p = self._make()
        assert p.symbol == "AAPL"
        assert p.direction == 1
        assert p.entry_price == 150.0
        assert p.entry_time == pd.Timestamp("2024-01-02")
        assert p.size == 100.0

    def test_default_leverage(self):
        assert self._make().leverage == 1.0

    def test_default_entry_commission(self):
        assert self._make().entry_commission == 0.0

    def test_default_entry_bar_idx(self):
        assert self._make().entry_bar_idx == 0

    def test_short_direction(self):
        p = self._make(direction=-1, entry_price=200.0)
        assert p.direction == -1
        assert p.entry_price == 200.0

    def test_frozen_prevents_mutation(self):
        p = self._make()
        with pytest.raises(dataclasses.FrozenInstanceError):
            p.symbol = "MSFT"  # type: ignore

    def test_equality(self):
        p1 = self._make()
        p2 = self._make()
        assert p1 == p2

    def test_hashable(self):
        p1 = self._make()
        p2 = self._make()
        assert hash(p1) == hash(p2)
        assert {p1, p2} == {p1}

    def test_repr_contains_class_name(self):
        p = self._make()
        assert "Position" in repr(p)
        assert "AAPL" in repr(p)

    def test_asdict_roundtrip(self):
        p = self._make()
        d = dataclasses.asdict(p)
        assert d["symbol"] == "AAPL"
        assert d["direction"] == 1
        assert d["entry_time"] == pd.Timestamp("2024-01-02")


# ============================================================
# TradeRecord
# ============================================================


class TestTradeRecord:
    def _make(self, **kwargs):
        defaults = dict(
            symbol="TSLA",
            direction=1,
            entry_price=200.0,
            exit_price=220.0,
            entry_time=pd.Timestamp("2024-01-02"),
            exit_time=pd.Timestamp("2024-01-10"),
            size=50.0,
            leverage=1.0,
            pnl=1000.0,
            pnl_pct=0.10,
            exit_reason="signal",
            holding_bars=8,
        )
        defaults.update(kwargs)
        return TradeRecord(**defaults)

    def test_construct_basic(self):
        t = self._make()
        assert t.symbol == "TSLA"
        assert t.pnl == 1000.0
        assert t.exit_reason == "signal"

    def test_field_count_13(self):
        # Verify all expected fields exist
        t = self._make()
        names = [f.name for f in dataclasses.fields(t)]
        assert len(names) == 13
        assert "symbol" in names
        assert "direction" in names
        assert "entry_price" in names
        assert "exit_price" in names
        assert "entry_time" in names
        assert "exit_time" in names
        assert "size" in names
        assert "leverage" in names
        assert "pnl" in names
        assert "pnl_pct" in names
        assert "exit_reason" in names
        assert "holding_bars" in names
        assert "commission" in names

    def test_default_commission_zero(self):
        assert self._make().commission == 0.0

    def test_exit_reason_values(self):
        for reason in ("signal", "liquidation", "end_of_backtest"):
            t = self._make(exit_reason=reason)
            assert t.exit_reason == reason

    def test_frozen_prevents_mutation(self):
        t = self._make()
        with pytest.raises(dataclasses.FrozenInstanceError):
            t.pnl = 0.0  # type: ignore

    def test_negative_pnl_loss(self):
        t = self._make(pnl=-500.0, pnl_pct=-0.05)
        assert t.pnl == -500.0
        assert t.pnl_pct == -0.05

    def test_equality_and_hash(self):
        t1 = self._make()
        t2 = self._make()
        assert t1 == t2
        assert hash(t1) == hash(t2)

    def test_asdict_roundtrip(self):
        t = self._make()
        d = dataclasses.asdict(t)
        assert d["pnl"] == 1000.0
        assert d["exit_reason"] == "signal"

    def test_deepcopy_independence(self):
        t = self._make()
        t2 = deepcopy(t)
        assert t == t2
        assert t is not t2

    def test_repr_contains_class_name(self):
        t = self._make()
        assert "TradeRecord" in repr(t)


# ============================================================
# EquitySnapshot
# ============================================================


class TestEquitySnapshot:
    def _make(self, **kwargs):
        defaults = dict(
            timestamp=pd.Timestamp("2024-01-02"),
            capital=50000.0,
            unrealized=2000.0,
            equity=52000.0,
        )
        defaults.update(kwargs)
        return EquitySnapshot(**defaults)

    def test_construct_basic(self):
        s = self._make()
        assert s.capital == 50000.0
        assert s.unrealized == 2000.0
        assert s.equity == 52000.0

    def test_default_positions_zero(self):
        assert self._make().positions == 0

    def test_with_positions(self):
        s = self._make(positions=3)
        assert s.positions == 3

    def test_frozen_prevents_mutation(self):
        s = self._make()
        with pytest.raises(dataclasses.FrozenInstanceError):
            s.equity = 0.0  # type: ignore

    def test_field_count_5(self):
        s = self._make()
        names = [f.name for f in dataclasses.fields(s)]
        assert len(names) == 5
        assert set(names) == {"timestamp", "capital", "unrealized", "equity", "positions"}

    def test_equality_and_hash(self):
        s1 = self._make()
        s2 = self._make()
        assert s1 == s2
        assert hash(s1) == hash(s2)

    def test_asdict_roundtrip(self):
        s = self._make()
        d = dataclasses.asdict(s)
        assert d["equity"] == 52000.0
        assert d["positions"] == 0

    def test_repr_contains_class_name(self):
        s = self._make()
        assert "EquitySnapshot" in repr(s)


# ============================================================
# Cross-cutting
# ============================================================


class TestCrossCutting:
    def test_pickle_roundtrip_position(self):
        p = Position("AAPL", 1, 150.0, pd.Timestamp("2024-01-02"), 100.0)
        restored = pickle.loads(pickle.dumps(p))
        assert restored == p

    def test_pickle_roundtrip_trade(self):
        t = TradeRecord(
            "TSLA", 1, 200.0, 220.0,
            pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-10"),
            50.0, 1.0, 1000.0, 0.10, "signal", 8,
        )
        restored = pickle.loads(pickle.dumps(t))
        assert restored == t

    def test_pickle_roundtrip_snapshot(self):
        s = EquitySnapshot(pd.Timestamp("2024-01-02"), 50000.0, 2000.0, 52000.0, 0)
        restored = pickle.loads(pickle.dumps(s))
        assert restored == s

    def test_all_three_in_set(self):
        p = Position("A", 1, 1.0, pd.Timestamp("2024-01-01"), 1.0)
        t = TradeRecord("A", 1, 1.0, 2.0, pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-02"), 1.0, 1.0, 1.0, 1.0, "signal", 1)
        s = EquitySnapshot(pd.Timestamp("2024-01-01"), 1.0, 0.0, 1.0, 1)
        combined = {p, t, s}
        assert len(combined) == 3
