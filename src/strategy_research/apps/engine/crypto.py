"""CryptoEngine — 加密货币市场引擎。

规则：24/7, maker/taker 佣金, funding 每 8h, tiered 强平。
"""

from __future__ import annotations

import pandas as pd

from .base import BaseEngine
from .market_hooks import calc_crypto_funding_fee, check_crypto_liquidation


class CryptoEngine(BaseEngine):
    """加密货币回测引擎。"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.maker_rate: float = config.get("maker_rate", 0.0002)
        self.taker_rate: float = config.get("taker_rate", 0.0005)
        self.slippage_rate: float = config.get("slippage", 0.0005)
        self.funding_rate: float = config.get("funding_rate", 0.0001)
        self._funding_applied: set = set()
        self._funding_daily_done: set = set()

    def round_size(self, raw_size: float, price: float) -> float:
        return round(max(raw_size, 0.0), 6)

    def calc_commission(self, size: float, price: float, direction: int, is_open: bool) -> float:
        rate = self.taker_rate if is_open else self.maker_rate
        return size * price * rate

    def on_bar(self, symbol: str, bar: pd.Series, timestamp: pd.Timestamp) -> None:
        # Funding fee
        fee = calc_crypto_funding_fee(
            symbol, bar, timestamp, self.positions,
            self.funding_rate, self._funding_applied, self._funding_daily_done,
        )
        self.capital -= fee

        # Liquidation check
        if check_crypto_liquidation(symbol, bar, self.positions):
            pos = self.positions.get(symbol)
            if pos is not None:
                mark_price = float(bar.get("close", pos.entry_price))
                liq_price = self.apply_slippage(mark_price, -pos.direction)
                self._close_position(symbol, liq_price, timestamp, "liquidation")


__all__ = ["CryptoEngine"]
