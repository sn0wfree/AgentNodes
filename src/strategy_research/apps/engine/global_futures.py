"""GlobalFuturesEngine — 全球期货市场引擎 (CME/ICE/Eurex)。

规则：T+0, 50+ 品种, USD 佣金。
"""

from __future__ import annotations

import re

from .futures_base import FuturesBaseEngine

# 全球期货乘数表 (部分)
_GLOBAL_FUTURES_MULTIPLIERS = {
    "ES": 50.0, "NQ": 20.0, "YM": 5.0, "RTY": 50.0,  # CME 股指
    "CL": 1000.0, "GC": 100.0, "SI": 5000.0, "HG": 25000.0,  # CME 能源/金属
    "ZB": 1000.0, "ZN": 1000.0, "ZF": 2000.0, "ZT": 4000.0,  # CME 国债
    "NG": 10000.0, "HO": 42000.0, "RB": 42000.0,  # CME 能源
    "6E": 125000.0, "6J": 12500000.0, "6B": 62500.0,  # CME 外汇
    "ZC": 50.0, "ZS": 50.0, "ZW": 50.0, "KC": 37500.0,  # CBOT 农产品
    "CT": 50000.0, "SB": 112000.0, "CC": 10.0,  # ICE 软商品
    "FDAX": 25.0, "FESX": 10.0, "FGBL": 1000.0,  # Eurex
}


def _get_global_product_code(symbol: str) -> str:
    code = symbol.split(".")[0] if "." in symbol else symbol
    match = re.match(r"\d?[A-Za-z]+", code)
    return match.group(0).upper() if match else ""


class GlobalFuturesEngine(FuturesBaseEngine):
    """全球期货回测引擎。"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.slippage_rate: float = config.get("slippage", 0.0005)

    def run_backtest(self, data_map, signal_map, codes, **kwargs):
        for code in codes:
            product = _get_global_product_code(code)
            if product in _GLOBAL_FUTURES_MULTIPLIERS:
                self.contract_multiplier = _GLOBAL_FUTURES_MULTIPLIERS[product]
                break
        return super().run_backtest(data_map, signal_map, codes, **kwargs)


__all__ = ["GlobalFuturesEngine"]
