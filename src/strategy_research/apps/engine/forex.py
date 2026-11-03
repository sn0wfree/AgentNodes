"""ForexEngine — 外汇市场引擎。

规则：24x5, spread-as-cost, 默认 100:1 杠杆, 每日 swap (周三三倍)。
"""

from __future__ import annotations

import pandas as pd

from .base import BaseEngine


class ForexEngine(BaseEngine):
    """外汇回测引擎。"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.spread_pips: float = config.get("spread_pips", 1.5)
        self.pip_value: float = config.get("pip_value", 0.0001)
        self.slippage_rate: float = config.get("slippage", 0.0001)
        self.swap_long: float = config.get("swap_long", -0.5)
        self.swap_short: float = config.get("swap_short", 0.3)
        self.swap_enabled: bool = config.get("swap_enabled", True)
        self._last_swap_dates: dict = {}

    def round_size(self, raw_size: float, price: float) -> float:
        return round(max(raw_size, 0.0), 2)

    def calc_commission(self, size: float, price: float, direction: int, is_open: bool) -> float:
        spread_cost = size * self.spread_pips * self.pip_value
        return spread_cost / 2  # half spread per side

    def on_bar(self, symbol: str, bar: pd.Series, timestamp: pd.Timestamp) -> None:
        if not self.swap_enabled:
            return

        pos = self.positions.get(symbol)
        if pos is None:
            return

        current_date = timestamp.date()
        last_swap = self._last_swap_dates.get(symbol)
        if last_swap == current_date:
            return

        # Wednesday triple swap
        is_wednesday = timestamp.weekday() == 2
        multiplier = 3 if is_wednesday else 1

        swap_rate = self.swap_long if pos.direction == 1 else self.swap_short
        notional = pos.size * float(bar.get("close", pos.entry_price))
        swap = notional * swap_rate * multiplier * 0.0001
        self.capital += swap
        self._last_swap_dates[symbol] = current_date


__all__ = ["ForexEngine"]
