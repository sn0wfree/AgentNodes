"""MarketMixin — 通用市场规则 Mixin。

提供 apply_slippage 和 can_execute 的默认实现。
子类可覆盖以添加特殊逻辑（如 T+1、涨跌停等）。
"""

from __future__ import annotations

import pandas as pd


class MarketMixin:
    """通用市场规则 Mixin。

    提供:
    - apply_slippage(): 通用滑点模型
    - can_execute(): 默认允许所有交易

    子类应设置 slippage_rate 属性，并在需要时覆盖方法。
    """

    # 子类应在 __init__ 中设置此属性
    slippage_rate: float = 0.0005

    def apply_slippage(self, price: float, direction: int) -> float:
        """通用滑点模型。

        Args:
            price: 原始价格
            direction: 1=买入(向上滑), -1=卖出(向下滑)

        Returns:
            滑点后的价格
        """
        return price * (1 + direction * self.slippage_rate)

    def can_execute(self, symbol: str, direction: int, bar: pd.Series) -> bool:
        """市场规则是否允许此交易。

        默认实现：允许所有交易。子类可覆盖以添加限制（如 T+1、涨跌停等）。

        Args:
            symbol: 交易标的
            direction: 1=买, -1=卖, 0=平仓
            bar: 当前 K 线数据

        Returns:
            True 表示允许交易
        """
        return True


__all__ = ["MarketMixin"]
