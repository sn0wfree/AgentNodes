"""Tencent 数据源。

A 股永不封禁 HTTP 接口。
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
from .utils import is_a_share

logger = logging.getLogger(__name__)


@register
class TencentLoader:
    """Tencent 数据源"""

    name = "tencent"
    markets = {"a_share"}
    requires_auth = False

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

        result = {}
        for code in codes:
            if not is_a_share(code):
                continue
            try:
                df = self._fetch_one(code, start_date, end_date)
                if df is not None and not df.empty:
                    result[code] = df
            except Exception as exc:
                logger.warning("Tencent 获取失败 %s: %s", code, exc)

        return result

    def _fetch_one(self, code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """获取单个 A 股数据"""
        # 转换代码: 601595.SH -> sh601595
        suffix = "sh" if code.endswith(".SH") else "sz"
        tencent_code = suffix + code.split(".")[0]

        url = (
            f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
            f"?param={tencent_code},day,{start_date},{end_date},500,qfq"
        )

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://web.ifzq.gtimg.cn",
            },
        )

        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        # 解析响应
        stock_data = data.get("data", {}).get(tencent_code, {})
        klines = stock_data.get("qfqday") or stock_data.get("day")

        if not klines:
            return None

        # 列顺序: ["date", "open", "close", "high", "low", "volume"]
        records = []
        for row in klines:
            if len(row) >= 6:
                records.append({
                    "trade_date": row[0],
                    "open": float(row[1]),
                    "close": float(row[2]),
                    "high": float(row[3]),
                    "low": float(row[4]),
                    "volume": float(row[5]),
                })

        if not records:
            return None

        df = pd.DataFrame(records)
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.set_index("trade_date")
        df = df.sort_index()

        return df
