"""IndiaEquityEngine — 印度股票市场引擎。

规则：T+1 delivery, 默认禁止做空, circuit bands, STT/GST/stamp duty。
"""

from __future__ import annotations

from .base import BaseEngine


class IndiaEquityEngine(BaseEngine):
    """印度股票回测引擎。"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.brokerage: float = config.get("brokerage", 0.0003)
        self.stt: float = config.get("stt", 0.001)
        self.exchange_txn: float = config.get("exchange_txn", 0.0000345)
        self.sebi_charge: float = config.get("sebi_charge", 0.000001)
        self.stamp_duty: float = config.get("stamp_duty", 0.00015)
        self.gst: float = config.get("gst", 0.18)
        self.slippage_rate: float = config.get("slippage", 0.001)

    def round_size(self, raw_size: float, price: float) -> float:
        return max(int(raw_size), 0)

    def calc_commission(self, size: float, price: float, direction: int, is_open: bool) -> float:
        notional = size * price
        comm = notional * self.brokerage
        comm += notional * self.exchange_txn
        comm += notional * self.sebi_charge
        if not is_open:
            comm += notional * self.stt
            comm += notional * self.stamp_duty
        comm *= (1 + self.gst)
        return comm


__all__ = ["IndiaEquityEngine"]
