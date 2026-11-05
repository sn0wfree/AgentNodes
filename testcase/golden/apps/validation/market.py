"""Market type registry for validation (P3-c).

This module defines the multi-market interface that validation tools can
target. All 7 market types are supported with correct bars_per_year values.
"""

from __future__ import annotations

import warnings
from enum import Enum


class MarketType(str, Enum):
    """Multi-market enum for validation tools."""

    A_SHARE = "a_share"
    HK_EQUITY = "hk_equity"
    US_EQUITY = "us_equity"
    CRYPTO = "crypto"
    FUTURES_CN = "futures_cn"
    FUTURES_GLOBAL = "futures_global"
    FOREX = "forex"


_MARKET_BARS_PER_YEAR: dict[MarketType, int] = {
    MarketType.A_SHARE: 252,
    MarketType.HK_EQUITY: 247,
    MarketType.US_EQUITY: 252,
    MarketType.CRYPTO: 365,
    MarketType.FUTURES_CN: 252,
    MarketType.FUTURES_GLOBAL: 252,
    MarketType.FOREX: 260,
}

SUPPORTED_MARKETS: frozenset[MarketType] = frozenset({
    MarketType.A_SHARE,
    MarketType.HK_EQUITY,
    MarketType.US_EQUITY,
    MarketType.CRYPTO,
    MarketType.FUTURES_CN,
    MarketType.FUTURES_GLOBAL,
    MarketType.FOREX,
})


def bars_per_year(market: MarketType) -> int:
    """Return the default bars_per_year for a market.

    Returns the correct trading days per year for each market type.
    """
    return _MARKET_BARS_PER_YEAR.get(market, _MARKET_BARS_PER_YEAR[MarketType.A_SHARE])


def warn_if_unsupported_market(market: MarketType) -> None:
    """Emit a UserWarning when a market type is not recognized.

    All 7 standard market types are supported. This only warns for
    completely unknown market values.
    """
    if market not in SUPPORTED_MARKETS:
        warnings.warn(
            f"MarketType.{market.value} is not a recognized market type. "
            f"Falling back to A_SHARE bars_per_year={bars_per_year(MarketType.A_SHARE)}.",
            UserWarning,
            stacklevel=2,
        )


__all__ = [
    "MarketType",
    "SUPPORTED_MARKETS",
    "bars_per_year",
    "warn_if_unsupported_market",
]
