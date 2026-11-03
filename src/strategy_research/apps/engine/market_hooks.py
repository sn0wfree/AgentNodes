"""Market hooks — per-bar 费用和风控检查。

crypto: funding fee + liquidation
forex: swap
"""

from __future__ import annotations

from typing import Dict, Set

import pandas as pd

from .models import Position

# ── Crypto ────────────────────────────────────────────

FUNDING_HOURS = {0, 8, 16}


def calc_crypto_funding_fee(
    symbol: str,
    bar: pd.Series,
    timestamp: pd.Timestamp,
    positions: Dict[str, Position],
    funding_rate: float,
    applied_set: Set,
    daily_done_set: Set,
) -> float:
    """计算加密 funding fee。"""
    current_date = timestamp.date()
    hour = timestamp.hour if hasattr(timestamp, "hour") else 0

    if hour in FUNDING_HOURS:
        key = (symbol, current_date, hour)
        if key in applied_set:
            return 0.0
        applied_set.add(key)
    else:
        day_key = (symbol, current_date)
        if day_key in daily_done_set:
            return 0.0
        daily_done_set.add(day_key)

    pos = positions.get(symbol)
    if pos is None:
        return 0.0

    mark_price = float(bar.get("close", pos.entry_price))
    notional = pos.size * mark_price
    return notional * funding_rate * pos.direction


def check_crypto_liquidation(
    symbol: str,
    bar: pd.Series,
    positions: Dict[str, Position],
) -> bool:
    """检查是否触发强平。"""
    pos = positions.get(symbol)
    if pos is None or pos.leverage <= 1.0:
        return False

    mark_price = float(bar.get("close", pos.entry_price))
    margin = pos.size * pos.entry_price / pos.leverage
    unrealized = pos.direction * pos.size * (mark_price - pos.entry_price)
    notional = pos.size * mark_price

    maint_rate = _maintenance_rate(notional)
    maint_margin = notional * maint_rate

    return (margin + unrealized) <= maint_margin


_TIER_TABLE = [
    (100_000, 0.004),
    (500_000, 0.006),
    (1_000_000, 0.01),
    (5_000_000, 0.02),
    (10_000_000, 0.05),
    (float("inf"), 0.10),
]


def _maintenance_rate(notional: float) -> float:
    for threshold, rate in _TIER_TABLE:
        if notional <= threshold:
            return rate
    return 0.10


__all__ = [
    "calc_crypto_funding_fee",
    "check_crypto_liquidation",
]
