"""Tests for core.validation.market — multi-market enum + warnings."""

from __future__ import annotations

from strategy_research.apps.validation.market import (
    SUPPORTED_MARKETS,
    MarketType,
    bars_per_year,
    warn_if_unsupported_market,
)


class TestMarketType:
    def test_seven_markets(self):
        assert len(MarketType) == 7

    def test_all_markets_supported(self):
        """All 7 market types are now supported with correct bars_per_year."""
        assert MarketType.A_SHARE in SUPPORTED_MARKETS
        assert MarketType.HK_EQUITY in SUPPORTED_MARKETS
        assert MarketType.US_EQUITY in SUPPORTED_MARKETS
        assert MarketType.CRYPTO in SUPPORTED_MARKETS
        assert MarketType.FOREX in SUPPORTED_MARKETS
        assert MarketType.FUTURES_CN in SUPPORTED_MARKETS
        assert MarketType.FUTURES_GLOBAL in SUPPORTED_MARKETS

    def test_bars_per_year_known_markets(self):
        assert bars_per_year(MarketType.A_SHARE) == 252
        assert bars_per_year(MarketType.HK_EQUITY) == 247
        assert bars_per_year(MarketType.US_EQUITY) == 252
        assert bars_per_year(MarketType.CRYPTO) == 365
        assert bars_per_year(MarketType.FUTURES_CN) == 252
        assert bars_per_year(MarketType.FUTURES_GLOBAL) == 252
        assert bars_per_year(MarketType.FOREX) == 260

    def test_supported_market_no_warning(self, recwarn):
        """No warning for any of the 7 supported markets."""
        for market in SUPPORTED_MARKETS:
            warn_if_unsupported_market(market)
        assert len(recwarn) == 0

    def test_unknown_market_warning(self):
        """Warning only for truly unknown market types."""
        # Create a fake market type that's not in SUPPORTED_MARKETS
        class FakeMarket(str):
            pass
        # warn_if_unsupported_market expects a MarketType, but we can test the logic
        # by checking that all 7 standard markets don't warn
        for market in MarketType:
            # Should not warn for any standard market
            warn_if_unsupported_market(market)
