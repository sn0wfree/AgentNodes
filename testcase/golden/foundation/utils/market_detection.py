"""市场检测 — 借鉴自 vibe-trading backtest/engines/_market_hooks.py

按代码格式正则分到 8 种市场，每种映射回 legacy data source 名。
"""

from __future__ import annotations

import re

# ─── 市场正则 ───

_MARKET_PATTERNS: dict[str, re.Pattern] = {
    "a_share": re.compile(r"^\d{6}\.(SZ|SH|BJ)$"),
    "hk_equity": re.compile(r"^\d{4,5}\.HK$"),
    "crypto": re.compile(r"^[A-Z]+-USDT$|^[A-Z]+/USDT$|^[A-Z]+-[A-Z]{3}$"),
    "futures": re.compile(r"^[A-Z]+\d{4}\.(SHFE|DCE|ZCE|CZCE|GFEX)$"),
    "forex": re.compile(
        r"^[A-Z]{3}/[A-Z]{3}$|^[A-Z]{6}\.FX$|"
        r"^(EURUSD|GBPUSD|USDJPY|USDCNY|USDCNH|AUDUSD|USDCAD|NZDUSD|"
        r"USDCHF|EURGBP|EURJPY|GBPJPY|EURCNY|XAUUSD|XAGUSD)$"
    ),
    "india_equity": re.compile(r"^[A-Z]{1,12}\.(NS|BO)$"),
    "fund": re.compile(r"^\d{6}\.(OF|SZ_OF|SH_OF)$"),
    "us_equity": re.compile(r"^[A-Z]{1,5}$|^[A-Z]{1,4}\.[A-Z]{1,2}$"),
}

# ─── market → data source 映射 ───

_MARKET_TO_SOURCE: dict[str, str] = {
    "a_share": "tushare",
    "us_equity": "yfinance",
    "hk_equity": "yfinance",
    "crypto": "okx",
    "futures": "tushare",
    "fund": "tushare",
    "macro": "akshare",
    "forex": "akshare",
    "india_equity": "yahoo",
}


def detect_market(code: str) -> str:
    """根据 code 返回 market 类型，未匹配返回 'unknown'。

    Examples:
        "000001.SZ" → "a_share"
        "AAPL"      → "us_equity"
        "BTC-USDT"  → "crypto"
        "CU2501.SHFE" → "futures"
        "EUR/USD"   → "forex"
        "RANDOM"    → "unknown"
    """
    code = code.strip()
    for market, pattern in _MARKET_PATTERNS.items():
        if pattern.match(code):
            return market
    return "unknown"


def detect_source(code: str) -> str:
    """market → legacy data source name (tushare/yfinance/okx/akshare).

    Examples:
        "000001.SZ" → "tushare"
        "AAPL"      → "yfinance"
        "BTC-USDT"  → "okx"
    """
    market = detect_market(code)
    return _MARKET_TO_SOURCE.get(market, "tushare")


def detect_submarket(codes: list[str]) -> str:
    """US/HK 细分，返回 'us' / 'hk' / 'mixed'。"""
    us_found = any(detect_market(c) == "us_equity" for c in codes)
    hk_found = any(detect_market(c) == "hk_equity" for c in codes)
    if us_found and hk_found:
        return "mixed"
    if us_found:
        return "us"
    if hk_found:
        return "hk"
    return "other"


def detect_market_batch(codes: list[str]) -> dict[str, str]:
    """批量检测: {code: market}。"""
    return {code: detect_market(code) for code in codes}


__all__ = [
    "detect_market",
    "detect_source",
    "detect_submarket",
    "detect_market_batch",
    "_MARKET_PATTERNS",
    "_MARKET_TO_SOURCE",
]
