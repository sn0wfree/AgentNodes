"""GlobalEquityEngine — US/HK 股票市场引擎。

US: T+0, 零佣金, fractional shares
HK: T+0, 印花税+征费, 100股整手
"""

from __future__ import annotations

from .base import BaseEngine


class GlobalEquityEngine(BaseEngine):
    """全球股票回测引擎 (US/HK)。"""

    def __init__(self, config: dict, market: str = "us"):
        super().__init__(config)
        self.market = market
        self.slippage_us: float = config.get("slippage_us", 0.0005)
        self.slippage_hk: float = config.get("slippage_hk", 0.001)
        self.hk_stamp_tax: float = config.get("hk_stamp_tax", 0.001)
        self.hk_commission: float = config.get("hk_commission", 0.00015)
        self.hk_levy: float = config.get("hk_levy", 0.0000565)
        self.hk_settlement: float = config.get("hk_settlement", 0.00002)

    def apply_slippage(self, price: float, direction: int) -> float:
        """覆盖默认实现：US/HK 不同滑点率。"""
        rate = self.slippage_hk if self.market == "hk" else self.slippage_us
        return price * (1 + direction * rate)

    def round_size(self, raw_size: float, price: float) -> float:
        if self.market == "hk":
            return max(int(raw_size / 100) * 100, 0)
        return round(max(raw_size, 0.0), 2)  # fractional

    def calc_commission(self, size: float, price: float, direction: int, is_open: bool) -> float:
        if self.market == "hk":
            notional = size * price
            comm = notional * self.hk_commission
            comm += notional * self.hk_stamp_tax
            comm += notional * self.hk_levy
            comm += notional * self.hk_settlement
            return comm
        return 0.0  # US: zero commission


__all__ = ["GlobalEquityEngine"]
