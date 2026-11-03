"""FuturesBaseEngine — 期货基类。

添加合约乘数到 PnL/margin 公式。子类需设置 contract_multiplier。
"""

from __future__ import annotations

from .base import BaseEngine


class FuturesBaseEngine(BaseEngine):
    """期货引擎基类。"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.contract_multiplier: float = config.get("contract_multiplier", 10.0)
        self.margin_rate: float = config.get("margin_rate", 0.10)
        self.commission_per_contract: float = config.get("commission_per_contract", 3.0)
        self.slippage_rate: float = config.get("slippage", 0.0005)

    def _calc_pnl(
        self, symbol: str, direction: int, size: float, entry_price: float, exit_price: float
    ) -> float:
        return direction * size * (exit_price - entry_price) * self.contract_multiplier

    def _calc_margin(self, symbol: str, size: float, price: float, leverage: float) -> float:
        return size * price * self.contract_multiplier * self.margin_rate

    def _calc_raw_size(self, symbol: str, target_notional: float, price: float) -> float:
        margin_per = price * self.contract_multiplier * self.margin_rate
        if margin_per <= 0:
            return 0.0
        return target_notional / margin_per

    def round_size(self, raw_size: float, price: float) -> float:
        return max(int(raw_size), 0)

    def calc_commission(self, size: float, price: float, direction: int, is_open: bool) -> float:
        return size * self.commission_per_contract


__all__ = ["FuturesBaseEngine"]
