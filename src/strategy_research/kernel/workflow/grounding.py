from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class MarketData:
    symbol: str
    data: dict[str, object] = field(default_factory=dict)


@runtime_checkable
class GroundingProvider(Protocol):
    def fetch_market_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> MarketData: ...

    def is_available(self) -> bool: ...


__all__ = ["MarketData", "GroundingProvider"]
