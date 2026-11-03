"""AKShare 数据源。

免费全市场数据 (A 股/美股/港股/期货/宏观/外汇)。
"""

from __future__ import annotations

import logging
from typing import Optional, cast

import pandas as pd

from .base import validate_date_range
from .file_cache import file_cache
from .registry import register
from .utils import is_a_share, is_crypto, is_etf, is_forex, is_hk, is_index, is_us

logger = logging.getLogger(__name__)


@register
class AKShareLoader:
    """AKShare 数据源"""

    name = "akshare"
    markets = {"a_share", "etf", "index", "us", "hk", "futures", "macro", "forex", "crypto"}
    requires_auth = False

    def is_available(self) -> bool:
        try:
            import akshare
            return True
        except ImportError:
            return False

    @file_cache(enable_cache=True, granularity='d')
    def fetch(
        self,
        codes: list[str],
        start_date: str,
        end_date: str,
        *,
        interval: str = "1D",
        fields: Optional[list[str]] = None,
    ) -> dict[str, pd.DataFrame]:
        validate_date_range(start_date, end_date)

        result = {}
        for code in codes:
            try:
                df = self._fetch_one(code, start_date, end_date)
                if df is not None and not df.empty:
                    result[code] = df
            except Exception as exc:
                logger.warning("AKShare 获取失败 %s: %s", code, exc)

        return result

    def _fetch_one(
        self, code: str, start_date: str, end_date: str
    ) -> Optional[pd.DataFrame]:
        """获取单个代码的数据"""

        # 去掉横线
        sd = start_date.replace("-", "")
        ed = end_date.replace("-", "")

        if is_etf(code):
            return self._fetch_etf(code, sd, ed)
        elif is_a_share(code) or is_index(code):
            return self._fetch_a_share(code, sd, ed)
        elif is_hk(code):
            return self._fetch_hk(code, sd, ed)
        elif is_us(code):
            return self._fetch_us(code, sd, ed)
        elif is_forex(code):
            return self._fetch_forex(code, sd, ed)
        elif is_crypto(code):
            return None  # AKShare 不支持加密货币
        else:
            return self._fetch_a_share(code, sd, ed)

    def _fetch_etf(self, code: str, start: str, end: str) -> Optional[pd.DataFrame]:
        """获取 ETF 数据"""
        import akshare as ak

        # 转换为 Sina 格式: 518880.SH -> sh518880
        suffix = "sh" if code.endswith(".SH") else "sz"
        symbol = suffix + code.split(".")[0]

        df = ak.fund_etf_hist_sina(symbol=symbol)
        if df is None or df.empty:
            return None

        col_map = {"日期": "trade_date", "开盘": "open", "最高": "high",
                    "最低": "low", "收盘": "close", "成交量": "volume"}
        df = df.rename(columns=col_map)

        if "trade_date" in df.columns:
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            df = df.set_index("trade_date")

        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        std_cols = ["open", "high", "low", "close", "volume"]
        existing = [c for c in std_cols if c in df.columns]
        df = df[existing]
        df = df.sort_index()

        return cast(pd.DataFrame, df)

    def _fetch_a_share(self, code: str, start: str, end: str) -> Optional[pd.DataFrame]:
        """获取 A 股数据"""
        import akshare as ak

        symbol = code.split(".")[0]
        df = ak.stock_zh_a_hist(
            symbol=symbol, period="daily", start_date=start, end_date=end, adjust="qfq"
        )
        if df is None or df.empty:
            return None

        col_map = {"日期": "trade_date", "开盘": "open", "最高": "high",
                    "最低": "low", "收盘": "close", "成交量": "volume"}
        df = df.rename(columns=col_map)

        if "trade_date" in df.columns:
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            df = df.set_index("trade_date")

        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        std_cols = ["open", "high", "low", "close", "volume"]
        existing = [c for c in std_cols if c in df.columns]
        df = df[existing]
        df = df.sort_index()

        return cast(pd.DataFrame, df)

    def _fetch_hk(self, code: str, start: str, end: str) -> Optional[pd.DataFrame]:
        """获取港股数据"""
        import akshare as ak

        symbol = code.split(".")[0].zfill(5)
        df = ak.stock_hk_hist(symbol=symbol, period="daily", start_date=start, end_date=end, adjust="qfq")
        if df is None or df.empty:
            return None

        col_map = {"日期": "trade_date", "开盘": "open", "最高": "high",
                    "最低": "low", "收盘": "close", "成交量": "volume"}
        df = df.rename(columns=col_map)

        if "trade_date" in df.columns:
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            df = df.set_index("trade_date")

        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        std_cols = ["open", "high", "low", "close", "volume"]
        existing = [c for c in std_cols if c in df.columns]
        df = df[existing]
        df = df.sort_index()

        return cast(pd.DataFrame, df)

    def _fetch_us(self, code: str, start: str, end: str) -> Optional[pd.DataFrame]:
        """获取美股数据"""
        import akshare as ak

        symbol = code.split(".")[0]
        # 尝试不同前缀
        for prefix in ["105.", "106.", ""]:
            try:
                df = ak.stock_us_hist(symbol=prefix + symbol, period="daily", start_date=start, end_date=end, adjust="qfq")
                if df is not None and not df.empty:
                    break
            except Exception:
                continue
        else:
            return None

        col_map = {"日期": "trade_date", "开盘": "open", "最高": "high",
                    "最低": "low", "收盘": "close", "成交量": "volume"}
        df = df.rename(columns=col_map)

        if "trade_date" in df.columns:
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            df = df.set_index("trade_date")

        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        std_cols = ["open", "high", "low", "close", "volume"]
        existing = [c for c in std_cols if c in df.columns]
        df = df[existing]
        df = df.sort_index()

        return cast(pd.DataFrame, df)

    def _fetch_forex(self, code: str, start: str, end: str) -> Optional[pd.DataFrame]:
        """获取外汇数据"""
        import akshare as ak

        symbol = code.replace("/", "")
        df = ak.forex_hist_em(symbol=symbol)
        if df is None or df.empty:
            return None

        col_map = {"日期": "trade_date", "开盘": "open", "最高": "high",
                    "最低": "low", "收盘": "close"}
        df = df.rename(columns=col_map)

        if "trade_date" in df.columns:
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            df = df.set_index("trade_date")

        for col in ["open", "high", "low", "close"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df["volume"] = 0.0
        std_cols = ["open", "high", "low", "close", "volume"]
        existing = [c for c in std_cols if c in df.columns]
        df = df[existing]
        df = df.sort_index()

        return cast(pd.DataFrame, df)
