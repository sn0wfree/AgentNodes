"""Tests for market_detection.py — detect_market / detect_source / detect_submarket"""

from __future__ import annotations

import pytest

from agentnodes.foundation.utils.market_detection import (
    _MARKET_PATTERNS,
    _MARKET_TO_SOURCE,
    detect_market,
    detect_market_batch,
    detect_source,
    detect_submarket,
)

# ============================================================
# detect_market
# ============================================================


class TestDetectMarket:
    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            ("000001.SZ", "a_share"),
            ("600000.SH", "a_share"),
            ("300750.SZ", "a_share"),
            ("601398.SH", "a_share"),
            ("AAPL", "us_equity"),
            ("TSLA", "us_equity"),
            ("MSFT", "us_equity"),
            ("GOOG", "us_equity"),
            ("BRK.A", "us_equity"),
            ("00700.HK", "hk_equity"),
            ("09988.HK", "hk_equity"),
            ("2318.HK", "hk_equity"),
            ("BTC-USDT", "crypto"),
            ("ETH-USDT", "crypto"),
            ("SOL/USDT", "crypto"),
            ("DOGE/USDT", "crypto"),
            ("CU2501.SHFE", "futures"),
            ("M2501.DCE", "futures"),
            ("TA2501.ZCE", "futures"),
            ("SI2501.CZCE", "futures"),
            ("CU2501.GFEX", "futures"),
            ("EUR/USD", "forex"),
            ("GBP/USD", "forex"),
            ("EURUSD.FX", "forex"),
            ("USDCNY.FX", "forex"),
            # Bare 6-char FX pair (composite engine convention)
            ("EURUSD", "forex"),
            ("USDCNY", "forex"),
            ("RELIANCE.NS", "india_equity"),
            ("TCS.BO", "india_equity"),
            ("INFY.NS", "india_equity"),
            ("510300.OF", "fund"),
            ("110011.OF", "fund"),
        ],
    )
    def test_known_markets(self, code, expected):
        assert detect_market(code) == expected

    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            ("RANDOM_GARBAGE", "unknown"),
            ("123", "unknown"),
            ("a", "unknown"),
            ("", "unknown"),
            ("000001", "unknown"),
            ("00000.SZ", "unknown"),
        ],
    )
    def test_unknown_codes(self, code, expected):
        assert detect_market(code) == expected

    def test_case_sensitivity(self):
        # US equity should be uppercase
        assert detect_market("aapl") == "unknown"
        assert detect_market("AAPL") == "us_equity"

    def test_whitespace_stripped(self):
        assert detect_market("  AAPL  ") == "us_equity"

    def test_patterns_count(self):
        # Should have 8 market patterns
        assert len(_MARKET_PATTERNS) == 8


# ============================================================
# detect_source
# ============================================================


class TestDetectSource:
    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            ("000001.SZ", "tushare"),
            ("AAPL", "yfinance"),
            ("00700.HK", "yfinance"),
            ("BTC-USDT", "okx"),
            ("CU2501.SHFE", "tushare"),
            ("510300.OF", "tushare"),
            ("EUR/USD", "akshare"),
            ("RELIANCE.NS", "yahoo"),
        ],
    )
    def test_source_mapping(self, code, expected):
        assert detect_source(code) == expected

    def test_unknown_defaults_to_tushare(self):
        assert detect_source("RANDOM") == "tushare"

    def test_all_markets_have_source(self):
        for market in _MARKET_PATTERNS:
            if market not in ("macro",):  # macro has no pattern
                assert market in _MARKET_TO_SOURCE, f"{market} has no source mapping"


# ============================================================
# detect_submarket
# ============================================================


class TestDetectSubmarket:
    def test_us_only(self):
        assert detect_submarket(["AAPL", "TSLA"]) == "us"

    def test_hk_only(self):
        assert detect_submarket(["00700.HK", "09988.HK"]) == "hk"

    def test_mixed_us_hk(self):
        assert detect_submarket(["AAPL", "00700.HK"]) == "mixed"

    def test_other_when_no_us_hk(self):
        assert detect_submarket(["000001.SZ", "BTC-USDT"]) == "other"

    def test_empty_list(self):
        assert detect_submarket([]) == "other"


# ============================================================
# detect_market_batch
# ============================================================


class TestDetectMarketBatch:
    def test_batch(self):
        codes = ["AAPL", "000001.SZ", "BTC-USDT"]
        result = detect_market_batch(codes)
        assert result == {
            "AAPL": "us_equity",
            "000001.SZ": "a_share",
            "BTC-USDT": "crypto",
        }

    def test_empty(self):
        assert detect_market_batch({}) == {}

    def test_single(self):
        assert detect_market_batch(["TSLA"]) == {"TSLA": "us_equity"}


# ============================================================
# Cross-cutting
# ============================================================


class TestCrossCutting:
    def test_all_patterns_are_compiled_regex(self):
        for market, pattern in _MARKET_PATTERNS.items():
            assert hasattr(pattern, "match"), f"{market} pattern is not a compiled regex"

    def test_all_markets_matchable(self):
        """Every market in patterns should be detectable with a representative code."""
        representatives = {
            "a_share": "000001.SZ",
            "us_equity": "AAPL",
            "hk_equity": "00700.HK",
            "crypto": "BTC-USDT",
            "futures": "CU2501.SHFE",
            "forex": "EUR/USD",
            "india_equity": "RELIANCE.NS",
            "fund": "510300.OF",
        }
        for market, code in representatives.items():
            assert detect_market(code) == market, f"Failed for {market} with code {code}"
