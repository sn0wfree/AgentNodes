"""Tushare 数据源。

A 股/ETF/指数/港股 日线数据。
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from .base import validate_date_range
from .file_cache import file_cache
from .registry import register
from .utils import get_token, is_etf, is_hk, is_index, load_tokens

logger = logging.getLogger(__name__)


@register
class TushareLoader:
    """Tushare 数据源"""

    name = "tushare"
    markets = {"a_share", "etf", "index", "fund", "hk"}
    requires_auth = True

    def __init__(self, token: Optional[str] = None, workspace_path=None):
        if token:
            self.token = token
        else:
            tokens = load_tokens(workspace_path)
            self.token = get_token(tokens, "TUSHARE_TOKEN")
        self._api = None

    def is_available(self) -> bool:
        if not self.token:
            return False
        try:
            import tushare
            return True
        except ImportError:
            return False

    def _get_api(self):
        if self._api is None:
            import tushare as ts
            self._api = ts.pro_api(self.token)
        return self._api

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

        # 转换日期格式 (YYYY-MM-DD -> YYYYMMDD)
        ts_start = start_date.replace("-", "")
        ts_end = end_date.replace("-", "")

        result = {}
        api = self._get_api()

        for code in codes:
            try:
                df = self._fetch_one(api, code, ts_start, ts_end, interval)
                if df is not None and not df.empty:
                    result[code] = df
            except Exception as exc:
                logger.warning("Tushare 获取失败 %s: %s", code, exc)

        return result

    def _fetch_one(
        self, api, code: str, start: str, end: str, interval: str
    ) -> Optional[pd.DataFrame]:
        """获取单个代码的数据"""
        # 分钟线
        if interval != "1D":
            return self._fetch_minute(api, code, start, end, interval)

        # 日线: 根据代码类型选择 API。个股用 pro_bar 前复权(qfq):
        # api.daily 返回不复权价,除权除息日会出现 -10% 级假跳空,直接
        # 毒化动量/反转类因子。fund/index/hk 接口无复权参数(指数本身
        # 不复权),保持原样。pro_bar 失败(积分不足等)→ 回退不复权,
        # 与历史行为一致但打日志留痕。
        if is_etf(code):
            df = api.fund_daily(ts_code=code, start_date=start, end_date=end)
        elif is_index(code):
            df = api.index_daily(ts_code=code, start_date=start, end_date=end)
        elif is_hk(code):
            df = api.hk_daily(ts_code=code, start_date=start, end_date=end)
        else:
            df = None
            try:
                df = api.pro_bar(
                    ts_code=code, adj="qfq",
                    start_date=start, end_date=end,
                )
            except Exception as exc:
                logger.warning("tushare pro_bar(qfq) 失败 %s: %s; 回退不复权 daily", code, exc)
            if df is None or df.empty:
                df = api.daily(ts_code=code, start_date=start, end_date=end)

        if df is None or df.empty:
            return None

        # 归一化
        df = df.rename(columns={"vol": "volume", "trade_date": "trade_date"})
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.set_index("trade_date")

        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        std_cols = ["open", "high", "low", "close", "volume"]
        existing = [c for c in std_cols if c in df.columns]
        df = df[existing]
        df = df.dropna(subset=["open", "high", "low", "close"], how="all")
        df = df.sort_index()

        return df

    def _fetch_minute(
        self, api, code: str, start: str, end: str, interval: str
    ) -> Optional[pd.DataFrame]:
        """获取分钟线数据"""
        # Tushare 分钟线需要 stk_mins
        freq_map = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "1H": "60"}
        freq = freq_map.get(interval)
        if not freq:
            return None

        df = api.stk_mins(
            ts_code=code,
            freq=freq,
            start_date=start,
            end_date=end,
        )
        if df is None or df.empty:
            return None

        df = df.rename(columns={"vol": "volume"})
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.set_index("trade_date")

        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        std_cols = ["open", "high", "low", "close", "volume"]
        existing = [c for c in std_cols if c in df.columns]
        df = df[existing]
        df = df.sort_index()

        return df
