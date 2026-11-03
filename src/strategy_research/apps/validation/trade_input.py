"""Lightweight trade record for validation (P3-c).

Adapted from vibe-trading-ai 0.1.11 (MIT License, HKUDS).

The validation tools (Monte Carlo / Bootstrap / Walk-Forward) work on a
list of completed round-trip trades plus an equity curve. We do not depend
on the project's full ``TradeRecord`` (which lives in
``backtest/engines/base.py`` of vibe-trading). Instead this is a minimal,
self-contained dataclass for the validator.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TradeInput:
    """A completed round-trip trade for validation.

    Attributes:
        symbol: Instrument identifier (free-form).
        direction: 1 for long, -1 for short.
        entry_price: Entry execution price.
        exit_price: Exit execution price.
        entry_time: Entry timestamp.
        exit_time: Exit timestamp.
        size: Number of shares / coins traded.
        pnl: Realized profit and loss in account currency.
        pnl_pct: Realized return as a fraction (e.g. 0.05 for 5%).
        holding_bars: Number of bars the position was held.
        exit_reason: Why the trade closed (signal / stop / end_of_backtest / ...).
    """

    symbol: str
    direction: int
    entry_price: float
    exit_price: float
    entry_time: datetime
    exit_time: datetime
    size: float
    pnl: float
    pnl_pct: float
    holding_bars: int = 0
    exit_reason: str = "signal"


__all__ = ["TradeInput"]
