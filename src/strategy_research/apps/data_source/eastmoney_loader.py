"""Eastmoney (东方财富) 数据源。

支持 A 股日线/分钟线。A 股 + 港股 EOD。
"""
from __future__ import annotations

import json
import logging
import urllib.request
from typing import Optional

import pandas as pd

from .base import validate_date_range
from .file_cache import file_cache
from .registry import register
from .utils import is_a_share, is_hk

logger = logging.getLogger(__name__)


# 东财 secid 映射
# A 股: 1.600519 (沪) / 0.000001 (深)
# 港股: 116.00700 (港股通)
def _to_secid(code: str) -> Optional[str]:
    """资产代码 → 东财 secid 格式。"""
    if is_a_share(code):
        num = code.split(".")[0]
        if code.endswith(".SH"):
            return f"1.{num}"
        if code.endswith(".SZ"):
            return f"0.{num}"
        if code.endswith(".BJ"):
            return f"0.{num}"  # 北交所也用 0
    if is_hk(code):
        # 5 位港股代码 (如 00700.HK → 00700)
        num = code.split(".")[0].lstrip("0") or "0"
        return f"116.{num.zfill(5)}"
    return None


@register
class EastmoneyLoader:
    """东方财富数据源"""

    name = "eastmoney"
    markets = {"a_share", "hk_equity"}
    requires_auth = False

    BASE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

    def is_available(self) -> bool:
        return True

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

        # 东财 klt 参数: 101=日K, 102=周K, 103=月K, 5=5分, 15=15分, 30=30分, 60=60分
        klt_map = {"1D": 101, "1W": 102, "1M": 103, "5m": 5, "15m": 15, "30m": 30, "60m": 60}
        klt = klt_map.get(interval, 101)

        result = {}
        for code in codes:
            secid = _to_secid(code)
            if secid is None:
                continue
            try:
                df = self._fetch_one(secid, code, start_date, end_date, klt)
                if df is not None and not df.empty:
                    result[code] = df
            except Exception as exc:
                logger.warning("Eastmoney 获取失败 %s: %s", code, exc)

        return result

    def _fetch_one(
        self, secid: str, code: str, start_date: str, end_date: str, klt: int
    ) -> Optional[pd.DataFrame]:
        """获取单个标的数据。

        返回字段: open / high / low / close / volume
        """
        # 转换日期为东财格式 (YYYYMMDD)
        s = start_date.replace("-", "")
        e = end_date.replace("-", "")

        url = (
            f"{self.BASE_URL}"
            f"?secid={secid}"
            f"&fields1=f1,f2,f3,f4,f5,f6"
            f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
            f"&klt={klt}"
            f"&fqt=1"  # 前复权
            f"&beg={s}"
            f"&end={e}"
        )

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                "Referer": "https://quote.eastmoney.com/",
            },
        )

        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

        klines = payload.get("data", {}).get("klines", [])
        if not klines:
            return None

        # 每行: "2024-01-02,open,close,high,low,volume,amount,amplitude,change_pct,change,turnover"
        records = []
        for line in klines:
            parts = line.split(",")
            if len(parts) < 6:
                continue
            try:
                records.append({
                    "trade_date": parts[0],
                    "open": float(parts[1]),
                    "close": float(parts[2]),
                    "high": float(parts[3]),
                    "low": float(parts[4]),
                    "volume": float(parts[5]),
                })
            except (ValueError, IndexError):
                continue

        if not records:
            return None

        df = pd.DataFrame(records)
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.set_index("trade_date")
        df = df.sort_index()

        # 过滤日期范围
        df = df.loc[start_date:end_date]

        return df
