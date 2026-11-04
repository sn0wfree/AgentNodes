"""MarketMixin 单元测试。"""

from __future__ import annotations

import pandas as pd
import pytest

from strategy_research.apps.engine.market_mixin import MarketMixin


class TestMarketMixinApplySlippage:
    """测试 apply_slippage 方法。"""

    def test_buy_slippage(self):
        """买入时价格上滑。"""
        mixin = MarketMixin()
        mixin.slippage_rate = 0.001
        result = mixin.apply_slippage(100.0, 1)
        assert result == pytest.approx(100.1)

    def test_sell_slippage(self):
        """卖出时价格下滑。"""
        mixin = MarketMixin()
        mixin.slippage_rate = 0.001
        result = mixin.apply_slippage(100.0, -1)
        assert result == pytest.approx(99.9)

    def test_zero_slippage_rate(self):
        """滑点率为 0 时价格不变。"""
        mixin = MarketMixin()
        mixin.slippage_rate = 0.0
        result = mixin.apply_slippage(100.0, 1)
        assert result == 100.0

    def test_default_slippage_rate(self):
        """默认滑点率为 0.0005。"""
        mixin = MarketMixin()
        assert mixin.slippage_rate == 0.0005
        result = mixin.apply_slippage(100.0, 1)
        assert result == pytest.approx(100.05)

    def test_high_slippage_rate(self):
        """高滑点率测试。"""
        mixin = MarketMixin()
        mixin.slippage_rate = 0.01
        result = mixin.apply_slippage(1000.0, 1)
        assert result == pytest.approx(1010.0)

    def test_close_direction(self):
        """平仓时 direction=0，价格不变。"""
        mixin = MarketMixin()
        mixin.slippage_rate = 0.001
        result = mixin.apply_slippage(100.0, 0)
        assert result == 100.0


class TestMarketMixinCanExecute:
    """测试 can_execute 方法。"""

    def test_default_returns_true(self):
        """默认实现返回 True。"""
        mixin = MarketMixin()
        bar = pd.Series({"open": 100, "close": 100, "volume": 1000})
        assert mixin.can_execute("AAPL", 1, bar) is True

    def test_any_symbol(self):
        """对任何标的返回 True。"""
        mixin = MarketMixin()
        bar = pd.Series({"open": 100, "close": 100})
        assert mixin.can_execute("BTC-USD", 1, bar) is True
        assert mixin.can_execute("EURUSD", -1, bar) is True
        assert mixin.can_execute("000001.SZ", 0, bar) is True

    def test_any_direction(self):
        """对任何方向返回 True。"""
        mixin = MarketMixin()
        bar = pd.Series({"open": 100, "close": 100})
        assert mixin.can_execute("AAPL", 1, bar) is True
        assert mixin.can_execute("AAPL", -1, bar) is True
        assert mixin.can_execute("AAPL", 0, bar) is True


class TestMarketMixinInheritance:
    """测试 MarketMixin 的继承行为。"""

    def test_subclass_can_override_apply_slippage(self):
        """子类可以覆盖 apply_slippage。"""

        class CustomEngine(MarketMixin):
            def apply_slippage(self, price: float, direction: int) -> float:
                # 固定 0.1% 滑点
                return price * 1.001 if direction == 1 else price * 0.999

        engine = CustomEngine()
        assert engine.apply_slippage(100.0, 1) == pytest.approx(100.1)
        assert engine.apply_slippage(100.0, -1) == pytest.approx(99.9)

    def test_subclass_can_override_can_execute(self):
        """子类可以覆盖 can_execute。"""

        class RestrictedEngine(MarketMixin):
            def can_execute(self, symbol: str, direction: int, bar: pd.Series) -> bool:
                # 只允许买入
                return direction == 1

        engine = RestrictedEngine()
        bar = pd.Series({"open": 100, "close": 100})
        assert engine.can_execute("AAPL", 1, bar) is True
        assert engine.can_execute("AAPL", -1, bar) is False
        assert engine.can_execute("AAPL", 0, bar) is False

    def test_subclass_inherits_default(self):
        """子类默认继承 MarketMixin 的方法。"""

        class SimpleEngine(MarketMixin):
            pass

        engine = SimpleEngine()
        engine.slippage_rate = 0.001
        bar = pd.Series({"open": 100, "close": 100})
        assert engine.apply_slippage(100.0, 1) == pytest.approx(100.1)
        assert engine.can_execute("AAPL", 1, bar) is True
