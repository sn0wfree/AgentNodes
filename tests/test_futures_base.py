"""Tests for engine/futures_base.py — FuturesBaseEngine contract multiplier + margin.

Note: FuturesBaseEngine is abstract (can_execute is undefined). Tests use
ChinaFuturesEngine, a concrete subclass, to exercise the inherited logic.
"""

from __future__ import annotations

import pytest

from strategy_research.apps.engine.china_futures import ChinaFuturesEngine
from strategy_research.apps.engine.futures_base import FuturesBaseEngine


def _make_eng(config: dict) -> ChinaFuturesEngine:
    """Create a concrete FuturesBaseEngine subclass for testing."""
    return ChinaFuturesEngine(config)


# ============================================================
# Configuration / initialization
# ============================================================


class TestFuturesBaseInit:
    def test_default_config(self):
        eng = _make_eng({})
        assert eng.contract_multiplier == 10.0
        assert eng.margin_rate == 0.10
        assert eng.commission_per_contract == 3.0
        assert eng.slippage_rate == 0.0005

    def test_custom_multiplier(self):
        eng = _make_eng({"contract_multiplier": 50.0})
        assert eng.contract_multiplier == 50.0

    def test_custom_margin_rate(self):
        eng = _make_eng({"margin_rate": 0.15})
        assert eng.margin_rate == 0.15

    def test_custom_commission(self):
        eng = _make_eng({"commission_per_contract": 5.0})
        assert eng.commission_per_contract == 5.0

    def test_custom_slippage(self):
        eng = _make_eng({"slippage": 0.001})
        assert eng.slippage_rate == 0.001

    def test_initial_capital_from_base(self):
        eng = _make_eng({"initial_cash": 500_000})
        assert eng.initial_capital == 500_000
        assert eng.capital == 500_000


# ============================================================
# _calc_pnl — contract multiplier
# ============================================================


class TestCalcPnL:
    def test_long_profit(self):
        eng = _make_eng({"contract_multiplier": 10.0})
        pnl = eng._calc_pnl("IF2501", direction=1, size=1, entry_price=4000.0, exit_price=4100.0)
        # 1 * 1 * (4100 - 4000) * 10 = 1000
        assert pnl == 1000.0

    def test_short_profit(self):
        eng = _make_eng({"contract_multiplier": 10.0})
        pnl = eng._calc_pnl("IF2501", direction=-1, size=1, entry_price=4000.0, exit_price=3900.0)
        # -1 * 1 * (3900 - 4000) * 10 = 1000
        assert pnl == 1000.0

    def test_long_loss(self):
        eng = _make_eng({"contract_multiplier": 10.0})
        pnl = eng._calc_pnl("IF2501", direction=1, size=1, entry_price=4000.0, exit_price=3900.0)
        # 1 * 1 * (3900 - 4000) * 10 = -1000
        assert pnl == -1000.0

    def test_multi_contract_size(self):
        eng = _make_eng({"contract_multiplier": 50.0})
        pnl = eng._calc_pnl("ES", direction=1, size=5, entry_price=4500.0, exit_price=4600.0)
        # 1 * 5 * 100 * 50 = 25000
        assert pnl == 25000.0

    def test_zero_size_no_pnl(self):
        eng = _make_eng({})
        pnl = eng._calc_pnl("X", direction=1, size=0, entry_price=100.0, exit_price=200.0)
        assert pnl == 0.0

    def test_no_price_change_no_pnl(self):
        eng = _make_eng({"contract_multiplier": 100.0})
        pnl = eng._calc_pnl("X", direction=1, size=1, entry_price=100.0, exit_price=100.0)
        assert pnl == 0.0


# ============================================================
# _calc_margin — contract multiplier × margin_rate
# ============================================================


class TestCalcMargin:
    def test_basic_margin(self):
        eng = _make_eng({"contract_multiplier": 10.0, "margin_rate": 0.10})
        margin = eng._calc_margin("IF2501", size=1, price=4000.0, leverage=1.0)
        # 1 * 4000 * 10 * 0.10 = 4000
        assert margin == 4000.0

    def test_multi_contract_margin(self):
        eng = _make_eng({"contract_multiplier": 50.0, "margin_rate": 0.05})
        margin = eng._calc_margin("ES", size=2, price=4500.0, leverage=1.0)
        # 2 * 4500 * 50 * 0.05 = 22500
        assert margin == 22500.0

    def test_margin_independent_of_leverage_arg(self):
        eng1 = _make_eng({"margin_rate": 0.10})
        eng2 = _make_eng({"margin_rate": 0.10})
        m1 = eng1._calc_margin("X", size=1, price=100.0, leverage=10.0)
        m2 = eng2._calc_margin("X", size=1, price=100.0, leverage=1.0)
        assert m1 == m2


# ============================================================
# _calc_raw_size — required contracts for target notional
# ============================================================


class TestCalcRawSize:
    def test_basic_size(self):
        eng = _make_eng({"contract_multiplier": 10.0, "margin_rate": 0.10})
        size = eng._calc_raw_size("IF2501", target_notional=100_000, price=4000.0)
        # margin_per = 4000 * 10 * 0.10 = 4000
        # size = 100000 / 4000 = 25
        assert size == pytest.approx(25.0)

    def test_zero_margin_returns_zero(self):
        eng = _make_eng({"margin_rate": 0.0})
        size = eng._calc_raw_size("X", target_notional=100_000, price=4000.0)
        assert size == 0.0

    def test_negative_margin_returns_zero(self):
        eng = _make_eng({"margin_rate": -0.1})
        size = eng._calc_raw_size("X", target_notional=100_000, price=4000.0)
        assert size == 0.0

    def test_zero_price_returns_zero(self):
        eng = _make_eng({})
        size = eng._calc_raw_size("X", target_notional=100_000, price=0.0)
        assert size == 0.0


# ============================================================
# round_size — integer contracts
# ============================================================


class TestRoundSize:
    def test_rounds_down(self):
        eng = _make_eng({})
        assert eng.round_size(2.7, 4000.0) == 2

    def test_integer_unchanged(self):
        eng = _make_eng({})
        assert eng.round_size(5.0, 4000.0) == 5

    def test_negative_clamped_to_zero(self):
        eng = _make_eng({})
        assert eng.round_size(-3.0, 4000.0) == 0

    def test_zero_size(self):
        eng = _make_eng({})
        assert eng.round_size(0.0, 4000.0) == 0


# ============================================================
# calc_commission — per-contract
# ============================================================


class TestCalcCommission:
    def test_basic_commission(self):
        eng = _make_eng({"commission_per_contract": 3.0})
        comm = eng.calc_commission(size=1, price=4000.0, direction=1, is_open=True)
        assert comm == 3.0

    def test_multi_contract_commission(self):
        eng = _make_eng({"commission_per_contract": 2.5})
        comm = eng.calc_commission(size=5, price=4000.0, direction=-1, is_open=False)
        assert comm == 12.5

    def test_open_and_close_same(self):
        eng = _make_eng({"commission_per_contract": 3.0})
        c_open = eng.calc_commission(1, 4000.0, 1, is_open=True)
        c_close = eng.calc_commission(1, 4000.0, -1, is_open=False)
        assert c_open == c_close


# ============================================================
# apply_slippage — uses config (CRITICAL-3 fix)
# ============================================================


class TestApplySlippage:
    def test_buy_slippage_raises_price(self):
        eng = _make_eng({"slippage": 0.0005})
        result = eng.apply_slippage(price=4000.0, direction=1)
        # 4000 * (1 + 0.0005) = 4002.0
        assert result == pytest.approx(4002.0)

    def test_sell_slippage_lowers_price(self):
        eng = _make_eng({"slippage": 0.0005})
        result = eng.apply_slippage(price=4000.0, direction=-1)
        # 4000 * (1 - 0.0005) = 3998.0
        assert result == pytest.approx(3998.0)

    def test_custom_slippage(self):
        eng = _make_eng({"slippage": 0.001})
        result = eng.apply_slippage(price=100.0, direction=1)
        assert result == pytest.approx(100.1)

    def test_zero_slippage(self):
        eng = _make_eng({"slippage": 0.0})
        result = eng.apply_slippage(price=100.0, direction=1)
        assert result == 100.0

    def test_default_slippage_no_config(self):
        eng = _make_eng({})
        result = eng.apply_slippage(price=10000.0, direction=1)
        assert result == pytest.approx(10005.0)

    def test_slippage_reads_from_config_not_hardcoded(self):
        # Regression test for CRITICAL-3
        eng = _make_eng({"slippage": 0.05})
        result = eng.apply_slippage(price=100.0, direction=1)
        assert result == pytest.approx(105.0)


# ============================================================
# Inherits from BaseEngine
# ============================================================


class TestFuturesBaseInheritance:
    def test_is_base_engine(self):
        from strategy_research.apps.engine.base import BaseEngine
        eng = _make_eng({})
        assert isinstance(eng, BaseEngine)
        assert isinstance(eng, FuturesBaseEngine)

    def test_can_execute_works(self):
        eng = _make_eng({})
        result = eng.can_execute("CU2501.SHFE", 1, None)
        # ChinaFuturesEngine.can_execute returns True (T+0)
        assert result is True
