"""FRED 数据源。

美国宏观经济数据 (56 个预置系列)。
"""

from __future__ import annotations

import logging
import time
from typing import Optional, cast

import pandas as pd
import requests

from .base import validate_date_range
from .file_cache import file_cache
from .registry import register
from .utils import get_token, load_tokens

logger = logging.getLogger(__name__)


# FRED 预置系列
FRED_SERIES = {
    # 政策利率 / 国债收益率
    "DFF": "联邦基金有效利率",
    "DGS3MO": "3 个月国债",
    "DGS6MO": "6 个月国债",
    "DGS1": "1 年国债",
    "DGS2": "2 年国债",
    "DGS5": "5 年国债",
    "DGS10": "10 年国债",
    "DGS20": "20 年国债",
    "DGS30": "30 年国债",
    # 收益率曲线 / 通胀预期
    "T10Y2Y": "10Y-2Y 利差",
    "T10Y3M": "10Y-3M 利差",
    "T5YIE": "5 年通胀预期",
    "T10YIE": "10 年通胀预期",
    "DFII10": "10 年实际利率",
    # 通胀
    "CPIAUCSL": "CPI (headline)",
    "CPILFESL": "CPI (core)",
    "PCEPI": "PCE (headline)",
    "PCEPILFE": "PCE (core)",
    # 劳动力市场
    "UNRATE": "失业率",
    "PAYEMS": "非农就业",
    "ICSA": "初请失业金",
    "JTSJOL": "职位空缺",
    # 增长 / 情绪
    "GDPC1": "实际 GDP",
    "INDPRO": "工业产出",
    "RSAFS": "零售销售",
    "UMCSENT": "密歇根消费者信心",
    "USSLIND": "领先指数",
    # 房地产
    "HOUST": "新屋开工",
    "CSUSHPISA": "Case-Shiller 房价",
    "MORTGAGE30US": "30 年房贷利率",
    # 流动性 / 货币供应
    "M2SL": "M2 货币供应",
    "WRESBAL": "准备金余额",
    # 信用利差
    "BAMLC0A0CM": "投资级信用利差",
    "BAMLH0A0HYM2": "高收益信用利差",
    # 波动率
    "VIXCLS": "VIX",
    "VXNCLS": "VXN",
    # 大宗商品
    "DCOILWTICO": "WTI 原油",
    "DCOILBRENTEU": "布伦特原油",
    # 外汇
    "DEXUSEU": "EUR/USD",
    "DEXJPUS": "USD/JPY",
    "DEXCHUS": "USD/CNY",
    "DTWEXBGS": "美元指数",
}

# 核心系列 (高频同步)
CORE_SERIES = [
    "DFF", "DGS2", "DGS5", "DGS10", "DGS30",
    "T10Y2Y", "T10Y3M", "T5YIE", "T10YIE",
    "VIXCLS", "DCOILWTICO", "DCOILBRENTEU",
    "DEXUSEU", "DEXJPUS", "DEXCHUS",
    "M2SL", "MORTGAGE30US",
]


@register
class FredLoader:
    """FRED 数据源"""

    name = "fred"
    markets = {"macro"}
    requires_auth = True

    API_URL = "https://api.stlouisfed.org/fred/series/observations"

    def __init__(self, api_key: Optional[str] = None, workspace_path=None):
        if api_key:
            self.api_key = api_key
        else:
            tokens = load_tokens(workspace_path)
            self.api_key = get_token(tokens, "FRED_API_KEY")
        self._last_request = 0.0
        self._min_interval = 0.6

    def is_available(self) -> bool:
        if not self.api_key:
            return False
        try:
            resp = requests.get(
                self.API_URL,
                params={"series_id": "DFF", "api_key": self.api_key, "file_type": "json", "limit": 1},
                timeout=10,
            )
            return resp.status_code == 200
        except Exception:
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
        for series_id in codes:
            try:
                df = self._fetch_series(series_id, start_date, end_date)
                if df is not None and not df.empty:
                    result[series_id] = df
            except Exception as exc:
                logger.warning("FRED 获取失败 %s: %s", series_id, exc)

        return result

    def _fetch_series(
        self, series_id: str, start_date: str, end_date: str
    ) -> Optional[pd.DataFrame]:
        """获取单个 FRED 系列"""
        # 节流
        now = time.monotonic()
        elapsed = now - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "observation_start": start_date,
            "observation_end": end_date,
            "limit": 5000,
        }

        resp = requests.get(self.API_URL, params=params, timeout=15)
        self._last_request = time.monotonic()

        if resp.status_code != 200:
            logger.warning("FRED API 返回 %d: %s", resp.status_code, series_id)
            return None

        data = resp.json()
        observations = data.get("observations", [])

        if not observations:
            return None

        df = pd.DataFrame(observations)
        df["date"] = pd.to_datetime(df["date"])
        # FRED 用 "." 表示缺失值
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna(subset=["value"])

        if df.empty:
            return None

        df = df.set_index("date")[["value"]]
        df = df.rename(columns={"value": "close"})
        df["open"] = df["close"]
        df["high"] = df["close"]
        df["low"] = df["close"]
        df["volume"] = 0.0

        df = df[["open", "high", "low", "close", "volume"]]
        df = df.sort_index()

        return cast(pd.DataFrame, df)

    def list_series(self) -> dict[str, str]:
        """列出所有预置系列"""
        return FRED_SERIES.copy()

    def list_core_series(self) -> list[str]:
        """列出核心系列"""
        return CORE_SERIES.copy()
