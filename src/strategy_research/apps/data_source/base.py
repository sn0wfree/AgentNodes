"""数据源基础模块。

共享工具函数、DataLoader 协议、OHLC 验证、归一化。
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Optional, Protocol, runtime_checkable

import pandas as pd

logger = logging.getLogger(__name__)


# ============================================================
# DataLoader 协议
# ============================================================

@runtime_checkable
class DataLoader(Protocol):
    """数据源协议。所有 loader 必须实现此接口。"""

    name: str
    markets: set[str]
    requires_auth: bool

    def is_available(self) -> bool:
        """检查此数据源是否可用 (token 存在、网络正常等)"""
        ...

    def fetch(
        self,
        codes: list[str],
        start_date: str,
        end_date: str,
        *,
        interval: str = "1D",
        fields: Optional[list[str]] = None,
    ) -> dict[str, pd.DataFrame]:
        """获取数据。

        Args:
            codes: 资产代码列表
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)
            interval: K 线周期 ("1D", "1H", etc.)
            fields: 额外字段 (如 pe, pb, roe)

        Returns:
            dict: {code: DataFrame}，DataFrame 包含 open/high/low/close/volume
        """
        ...


# ============================================================
# 日期验证
# ============================================================

def validate_date_range(start_date: str, end_date: str) -> None:
    """验证日期范围"""
    if start_date > end_date:
        raise ValueError(f"start_date ({start_date}) > end_date ({end_date})")


# ============================================================
# OHLC 验证 (复用自 vibe-trading)
# ============================================================

def validate_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """验证并清理 OHLC 数据，删除无效 bar。

    无效条件:
    - high < low
    - high < open 或 high < close
    - low > open 或 low > close
    - 任何价格 <= 0
    """
    if df.empty:
        return df

    required = ["open", "high", "low", "close"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        logger.warning("Missing OHLC columns: %s", missing)
        return df

    invalid = (
        (df["high"] < df["low"]) |
        (df["high"] < df["open"]) |
        (df["high"] < df["close"]) |
        (df["low"] > df["open"]) |
        (df["low"] > df["close"]) |
        (df["open"] <= 0) |
        (df["high"] <= 0) |
        (df["low"] <= 0) |
        (df["close"] <= 0)
    )

    if invalid.any():
        n_dropped = invalid.sum()
        logger.warning("Dropped %d invalid OHLC bars", n_dropped)
        df = df[~invalid]

    return df


# ============================================================
# OHLCV 归一化
# ============================================================

def normalize_ohlcv(
    df: pd.DataFrame,
    date_col: str = "trade_date",
    col_map: Optional[dict[str, str]] = None,
) -> pd.DataFrame:
    """将 DataFrame 归一化为标准 OHLCV 格式。

    标准格式:
    - index: DatetimeIndex (name="trade_date")
    - columns: open, high, low, close, volume

    Args:
        df: 原始 DataFrame
        date_col: 日期列名
        col_map: 列名映射 (源列名 -> 标准列名)
    """
    if df.empty:
        return df

    df = df.copy()

    # 应用列名映射
    if col_map:
        df = df.rename(columns=col_map)

    # 尝试常见中文列名
    cn_map = {
        "日期": "trade_date",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "收盘": "close",
        "成交量": "volume",
        "成交额": "amount",
    }
    df = df.rename(columns={k: v for k, v in cn_map.items() if k in df.columns})

    # 尝试常见英文列名
    en_map = {
        "date": "trade_date",
        "Date": "trade_date",
        "vol": "volume",
        "Vol": "volume",
    }
    df = df.rename(columns={k: v for k, v in en_map.items() if k in df.columns})

    # 确保有 trade_date 列
    if date_col in df.columns:
        df = df.rename(columns={date_col: "trade_date"})
    elif "trade_date" not in df.columns:
        # 尝试用 index
        if df.index.name is not None:
            df = df.reset_index()
            df = df.rename(columns={df.columns[0]: "trade_date"})

    # 解析日期
    if "trade_date" in df.columns:
        df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
        df = df.dropna(subset=["trade_date"])
        df = df.set_index("trade_date")

    # 数值类型转换
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # 确保有 volume 列
    if "volume" not in df.columns:
        df["volume"] = 0.0

    # 选择标准列
    std_cols = ["open", "high", "low", "close", "volume"]
    existing = [c for c in std_cols if c in df.columns]
    df = df[existing]

    # 删除 OHLC 全为 NaN 的行
    df = df.dropna(subset=["open", "high", "low", "close"], how="all")

    # 排序
    df = df.sort_index()

    return df


# ============================================================
# 缓存工具
# ============================================================

def make_cache_key(source: str, symbol: str, timeframe: str,
                   start_date: str, end_date: str) -> str:
    """生成缓存键 (SHA-256)"""
    content = f"{source}:{symbol}:{timeframe}:{start_date}:{end_date}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


# ============================================================
# HTTP 节流
# ============================================================

class HostThrottle:
    """主机级 HTTP 节流器"""

    def __init__(self, min_interval: float = 0.5):
        self._min_interval = min_interval
        self._last_request: dict[str, float] = {}

    def wait(self, host: str) -> None:
        """等待直到可以发送请求"""
        now = time.monotonic()
        last = self._last_request.get(host, 0)
        elapsed = now - last
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request[host] = time.monotonic()
