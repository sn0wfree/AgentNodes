"""yfinance 数据源。

美股/港股/加密货币数据。
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from .base import validate_date_range
from .file_cache import file_cache
from .registry import register
from .utils import is_crypto, is_hk, is_us

logger = logging.getLogger(__name__)


@register
class YFinanceLoader:
    """yfinance 数据源"""

    name = "yfinance"
    markets = {"us", "hk", "crypto"}
    requires_auth = False

    def is_available(self) -> bool:
        try:
            import yfinance
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
                df = self._fetch_one(code, start_date, end_date, interval)
                if df is not None and not df.empty:
                    result[code] = df
            except Exception as exc:
                logger.warning("yfinance 获取失败 %s: %s", code, exc)

        return result

    def _fetch_one(
        self, code: str, start_date: str, end_date: str, interval: str
    ) -> Optional[pd.DataFrame]:
        """获取单个代码的数据"""
        import yfinance as yf

        # 转换代码格式
        if is_us(code):
            ticker = code.split(".")[0]  # AAPL.US -> AAPL
        elif is_hk(code):
            ticker = code.split(".")[0].zfill(5) + ".HK"  # 700.HK -> 00700.HK
        elif is_crypto(code):
            ticker = code.replace("-", "-")  # BTC-USDT -> BTC-USD
            if ticker.endswith("-USDT"):
                ticker = ticker.replace("-USDT", "-USD")
        else:
            ticker = code

        # 转换 interval
        interval_map = {"1D": "1d", "1H": "1h", "1W": "1wk", "1M": "1mo"}
        yf_interval = interval_map.get(interval, "1d")

        # 下载数据
        df = yf.download(
            ticker,
            start=start_date,
            end=end_date,
            interval=yf_interval,
            progress=False,
            auto_adjust=True,  # 显式前复权: 不同版本 yfinance 默认值相反
        )

        if df is None or df.empty:
            return None

        # 归一化列名
        df = df.rename(columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        })

        # 处理 MultiIndex (单 ticker 时可能有)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        std_cols = ["open", "high", "low", "close", "volume"]
        existing = [c for c in std_cols if c in df.columns]
        df = df[existing]
        df = df.sort_index()

        return df
