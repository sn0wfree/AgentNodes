"""Tests for data_source/utils.py:detect_market (data_source layer).

Covers the import-path fix in context-overflow Fix1: detect_market must
resolve core/utils/market_detection.py and map back to the data_source
layer labels (etf/index/us/hk/macro) used to pick a loader.

Verified manually before Fix1: the relative import
``from ...utils.market_detection`` pointed at the non-existent
``strategy_research.utils`` and raised ModuleNotFoundError, which made
get_market_data always fail.
"""
from __future__ import annotations

import pytest

from strategy_research.apps.data_source.utils import detect_market


class TestDataSourceDetectMarket:
    """data_source-layer mapping (uses core detection internally)."""

    @pytest.mark.parametrize("code,expected", [
        # A-share stocks
        ("600519.SH", "a_share"),
        ("000858.SZ", "a_share"),
        ("300059.SZ", "a_share"),
        # A-share index codes (mapped to index before generic a_share)
        ("000001.SH", "index"),
        ("000300.SH", "index"),
        # US equities
        ("AAPL", "us"),
        ("AAPL.US", "us"),
        # HK equities
        ("00700.HK", "hk"),
        # Macro / FRED series
        ("DGS10", "macro"),
        ("CPIAUCSL", "macro"),
        # Crypto / forex
        ("BTC-USDT", "crypto"),
        ("EUR/USD", "forex"),
    ])
    def test_market_mapping(self, code, expected):
        assert detect_market(code) == expected

    def test_does_not_raise_module_not_found(self):
        """Regression for Fix1: the broken relative import raised
        ModuleNotFoundError; now it must resolve cleanly."""
        # Exercise the import that used to fail
        from strategy_research.foundation.utils.market_detection import detect_market as _core
        assert callable(_core)
        # And the data_source wrapper must work end-to-end
        assert detect_market("600519.SH") == "a_share"
