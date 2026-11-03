"""Local 数据源。

本地 CSV/Parquet/DuckDB 文件。
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from .base import normalize_ohlcv, validate_date_range, validate_ohlc
from .registry import register

logger = logging.getLogger(__name__)


@register
class LocalLoader:
    """Local 数据源"""

    name = "local"
    markets = {"a_share", "etf", "index", "us", "hk", "macro", "fund", "futures", "forex", "crypto"}
    requires_auth = False

    def is_available(self) -> bool:
        return True

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
                logger.warning("Local 获取失败 %s: %s", code, exc)

        return result

    def _fetch_one(self, code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """获取单个代码的数据 (需要外部提供文件路径映射)"""
        # 这是一个占位实现
        # 实际使用时需要通过 config 或参数指定文件路径
        return None

    def fetch_from_file(
        self,
        file_path: str,
        code: str,
        start_date: str,
        end_date: str,
        file_type: str = "auto",
        col_map: Optional[dict[str, str]] = None,
        date_format: str = "%Y-%m-%d",
    ) -> Optional[pd.DataFrame]:
        """从本地文件获取数据"""
        from pathlib import Path

        path = Path(file_path)
        if not path.exists():
            logger.warning("文件不存在: %s", file_path)
            return None

        # 自动检测文件类型
        if file_type == "auto":
            suffix = path.suffix.lower()
            if suffix == ".csv":
                file_type = "csv"
            elif suffix == ".parquet":
                file_type = "parquet"
            elif suffix == ".duckdb":
                file_type = "duckdb"
            else:
                file_type = "csv"

        # 读取文件
        if file_type == "csv":
            df = pd.read_csv(path)
        elif file_type == "parquet":
            df = pd.read_parquet(path)
        elif file_type == "duckdb":
            import duckdb
            conn = duckdb.connect(str(path), read_only=True)
            df = conn.execute(
                "SELECT * FROM prices WHERE asset_code = ?", [code]
            ).df()
            conn.close()
        else:
            return None

        # 归一化
        df = normalize_ohlcv(df, col_map=col_map)

        # 日期过滤
        if df.index.name == "trade_date" or isinstance(df.index, pd.DatetimeIndex):
            start = pd.to_datetime(start_date)
            end = pd.to_datetime(end_date)
            df = df[(df.index >= start) & (df.index <= end)]

        # OHLC 验证
        df = validate_ohlc(df)

        return df
