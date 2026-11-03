"""ChinaAEngine — A 股市场引擎。

规则：T+1, 禁止做空, 100 股整手, 涨跌停, 佣金+印花税+过户费。
"""

from __future__ import annotations

import pandas as pd

from .base import BaseEngine


def _price_limit(symbol: str) -> float:
    """涨跌停幅度。"""
    code = symbol.split(".")[0] if "." in symbol else symbol
    if code.startswith("300") or code.startswith("688"):
        return 0.20  # 创业板/科创板
    if code.startswith("8") and len(code) == 6:
        return 0.30  # 北交所
    return 0.10  # 主板


def _calc_pct_change(bar: pd.Series) -> float | None:
    """从 bar 计算涨跌幅（需要前收盘）。暂用 close/open 近似。"""
    open_p = float(bar.get("open", 0))
    close_p = float(bar.get("close", 0))
    if open_p > 0:
        return (close_p - open_p) / open_p
    return None


class ChinaAEngine(BaseEngine):
    """A 股回测引擎。"""

    def __init__(self, config: dict):
        config = {**config, "leverage": 1.0}  # A 股无杠杆
        super().__init__(config)
        self.commission_rate: float = config.get("commission_rate", 0.00025)  # 万2.5
        self.commission_min: float = config.get("commission_min", 5.0)  # ¥5
        self.stamp_tax: float = config.get("stamp_tax", 0.0005)  # 万5 仅卖出
        self.transfer_fee: float = config.get("transfer_fee", 0.00001)  # 万0.1
        self.slippage_rate: float = config.get("slippage", 0.001)

    def can_execute(self, symbol: str, direction: int, bar: pd.Series) -> bool:
        """覆盖默认实现：添加 T+1 和涨跌停限制。"""
        # 1. 禁止做空
        if direction == -1:
            return False

        # 2. T+1: 当日买入不可当日卖出
        if direction == 0:
            pos = self.positions.get(symbol)
            if pos is not None:
                bar_date = bar.name.date() if hasattr(bar, "name") else None
                entry_date = pos.entry_time.date() if hasattr(pos.entry_time, "date") else None
                if bar_date and entry_date and bar_date == entry_date:
                    return False

        # 3. 涨跌停
        pct_chg = _calc_pct_change(bar)
        if pct_chg is not None:
            limit = _price_limit(symbol)
            if direction == 1 and pct_chg >= limit - 0.001:
                return False  # 涨停不能买
            if direction == 0 and pct_chg <= -limit + 0.001:
                return False  # 跌停不能卖

        return True

    def round_size(self, raw_size: float, price: float) -> float:
        return max(int(raw_size / 100) * 100, 0)

    def calc_commission(self, size: float, price: float, direction: int, is_open: bool) -> float:
        notional = size * price
        comm = max(notional * self.commission_rate, self.commission_min)
        comm += notional * self.transfer_fee
        if not is_open:
            comm += notional * self.stamp_tax
        return comm


__all__ = ["ChinaAEngine"]
