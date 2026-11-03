"""ChinaFuturesEngine — 中国期货市场引擎。

规则：T+0, 65+ 品种合约乘数, 品种级保证金/佣金。
"""

from __future__ import annotations

from .futures_base import FuturesBaseEngine

# 品种乘数表 (部分)
_CN_FUTURES_MULTIPLIERS = {
    "IF": 300.0, "IC": 200.0, "IM": 200.0, "IH": 300.0,  # 股指
    "CU": 5.0, "AL": 5.0, "ZN": 5.0, "PB": 5.0, "NI": 1.0, "SN": 1.0,  # 有色
    "AU": 1000.0, "AG": 15.0,  # 贵金属
    "RB": 10.0, "HC": 10.0, "SS": 5.0, "BU": 10.0, "RU": 10.0, "SP": 10.0,  # 黑色/化工
    "I": 100.0, "J": 100.0, "JM": 60.0, "A": 10.0, "B": 10.0, "M": 10.0, "Y": 10.0, "P": 10.0,  # 农产品
    "C": 10.0, "CS": 10.0, "JD": 10.0, "LH": 16.0,  # 农产品
    "SC": 1000.0, "NR": 10.0, "LU": 10.0,  # 能源
    "TA": 5.0, "MA": 10.0, "FG": 20.0, "SA": 20.0, "UR": 20.0,  # 化工
    "CF": 5.0, "SR": 10.0, "RM": 10.0, "AP": 10.0, "CJ": 5.0,  # 农产品
    "T": 10000.0, "TF": 10000.0, "TS": 20000.0,  # 国债
}


def _get_product_code(symbol: str) -> str:
    """从合约代码提取品种代码。"""
    code = symbol.split(".")[0] if "." in symbol else symbol
    # 去除数字部分 (如 CU2501 -> CU)
    product = ""
    for ch in code:
        if ch.isalpha():
            product += ch
        else:
            break
    return product.upper()


class ChinaFuturesEngine(FuturesBaseEngine):
    """中国期货回测引擎。"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.slippage_rate: float = config.get("slippage", 0.0005)

    def run_backtest(self, data_map, signal_map, codes, **kwargs):
        # 动态设置合约乘数
        for code in codes:
            product = _get_product_code(code)
            if product in _CN_FUTURES_MULTIPLIERS:
                self.contract_multiplier = _CN_FUTURES_MULTIPLIERS[product]
                break
        return super().run_backtest(data_map, signal_map, codes, **kwargs)


__all__ = ["ChinaFuturesEngine"]
