"""iFinD 数据源 (简化版)。

A 股/宏观/港美股 数据，基于 MCP 协议。
"""

from __future__ import annotations

import json
import logging
from typing import Optional

import pandas as pd
import requests

from .base import validate_date_range
from .file_cache import file_cache
from .registry import register
from .utils import get_token, load_tokens

logger = logging.getLogger(__name__)


@register
class IFindLoader:
    """iFinD 数据源 (简化版 MCP 客户端)"""

    name = "ifind"
    markets = {"a_share", "macro", "hk", "us", "bond", "commodity"}
    requires_auth = True

    SERVERS = {
        "stock": "https://api-mcp.51ifind.com:8643/ds-mcp-servers/hexin-ifind-ds-stock-mcp",
        "edb": "https://api-mcp.51ifind.com:8643/ds-mcp-servers/hexin-ifind-ds-edb-mcp",
        "global_stock": "https://api-mcp.51ifind.com:8643/ds-mcp-servers/hexin-ifind-ds-global-stock-mcp",
    }

    def __init__(self, token: Optional[str] = None, workspace_path=None):
        if token:
            self.token = token
        else:
            tokens = load_tokens(workspace_path)
            self.token = get_token(tokens, "IFIND_MCP_TOKEN")
        self._sessions: dict[str, str] = {}
        self._req_ids: dict[str, int] = {}

    def is_available(self) -> bool:
        return bool(self.token)

    def _next_id(self, server: str) -> int:
        self._req_ids[server] = self._req_ids.get(server, 0) + 1
        return self._req_ids[server]

    def _headers(self, server: str) -> dict:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": self.token,
        }
        if server in self._sessions:
            h["Mcp-Session-Id"] = self._sessions[server]
        return h

    def _init_session(self, server: str) -> None:
        """初始化 MCP session"""
        if server in self._sessions:
            return

        url = self.SERVERS[server]
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(server),
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "strategy-research", "version": "0.1.0"},
            },
        }

        resp = requests.post(url, json=payload, headers=self._headers(server), timeout=30)
        resp.raise_for_status()

        session_id = resp.headers.get("Mcp-Session-Id")
        if not session_id:
            raise RuntimeError("初始化失败: 未返回 session ID")

        self._sessions[server] = session_id

        # 发送 initialized 通知
        notify = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        requests.post(url, json=notify, headers=self._headers(server), timeout=10)

    def call(self, server: str, tool: str, params: dict) -> dict:
        """调用 MCP 工具"""
        self._init_session(server)

        url = self.SERVERS[server]
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(server),
            "method": "tools/call",
            "params": {
                "name": tool,
                "arguments": params,
            },
        }

        resp = requests.post(url, json=payload, headers=self._headers(server), timeout=60)
        data = resp.json()

        if isinstance(data, dict) and "error" in data:
            return {"ok": False, "error": data["error"]}

        resp.raise_for_status()
        return {"ok": True, "data": data}

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
                logger.warning("iFinD 获取失败 %s: %s", code, exc)

        return result

    def _fetch_one(
        self, code: str, start_date: str, end_date: str
    ) -> Optional[pd.DataFrame]:
        """获取单个代码的数据"""
        # 判断是 A 股还是宏观
        if self._is_macro_query(code):
            return self._fetch_edb(code, start_date, end_date)
        elif self._is_hk_us(code):
            return self._fetch_global(code, start_date, end_date)
        else:
            return self._fetch_stock(code, start_date, end_date)

    def _is_macro_query(self, code: str) -> bool:
        """判断是否为宏观数据查询"""
        macro_keywords = ["GDP", "CPI", "PPI", "PMI", "M2", "利率", "汇率"]
        return any(kw in code.upper() for kw in macro_keywords) or len(code) > 10

    def _is_hk_us(self, code: str) -> bool:
        """判断是否为港美股"""
        return code.endswith(".HK") or code.endswith(".US")

    def _fetch_stock(
        self, code: str, start_date: str, end_date: str
    ) -> Optional[pd.DataFrame]:
        """获取 A 股数据"""
        query = f"{code} {start_date} 至 {end_date} 收盘价"
        result = self.call("stock", "get_stock_performance", {"query": query})
        return self._parse_result(result, code)

    def _fetch_edb(
        self, code: str, start_date: str, end_date: str
    ) -> Optional[pd.DataFrame]:
        """获取宏观数据"""
        query = f"{code} {start_date}-{end_date}"
        result = self.call("edb", "get_edb_data", {"query": query})
        return self._parse_result(result, code)

    def _fetch_global(
        self, code: str, start_date: str, end_date: str
    ) -> Optional[pd.DataFrame]:
        """获取港美股数据"""
        query = f"{code} {start_date} 至 {end_date} 收盘价"
        result = self.call("global_stock", "global_stock_quotes", {"query": query})
        return self._parse_result(result, code)

    def _parse_result(self, result: dict, code: str) -> Optional[pd.DataFrame]:
        """解析 MCP 返回结果"""
        if not result.get("ok"):
            return None

        data = result.get("data", {})
        content = data.get("result", {}).get("content", [])

        if not content:
            return None

        text = content[0].get("text", "") if isinstance(content[0], dict) else str(content[0])

        # 尝试解析为 JSON
        try:
            json_data = json.loads(text)
            if isinstance(json_data, list):
                df = pd.DataFrame(json_data)
            elif isinstance(json_data, dict):
                df = pd.DataFrame(json_data)
            else:
                return None
        except (json.JSONDecodeError, TypeError):
            # 尝试解析为 CSV 或表格
            try:
                from io import StringIO
                df = pd.read_csv(StringIO(text))
            except Exception:
                return None

        # 归一化
        df = self._normalize_df(df, code)
        return df

    def _normalize_df(self, df: pd.DataFrame, code: str) -> Optional[pd.DataFrame]:
        """归一化 DataFrame"""
        # 尝试找到日期列
        date_col = None
        for col in ["日期", "date", "Date", "trade_date"]:
            if col in df.columns:
                date_col = col
                break
        if date_col is None and len(df.columns) > 0:
            date_col = df.columns[0]

        # 尝试找到价格列
        price_col = None
        for col in ["收盘价", "close", "Close", "close_price"]:
            if col in df.columns:
                price_col = col
                break
        if price_col is None and len(df.columns) > 1:
            price_col = df.columns[-1]

        if date_col is None or price_col is None:
            return None

        df["trade_date"] = pd.to_datetime(df[date_col], errors="coerce")
        df["close"] = pd.to_numeric(df[price_col], errors="coerce")
        df = df.dropna(subset=["trade_date", "close"])

        if df.empty:
            return None

        df = df.set_index("trade_date")
        df["open"] = df["close"]
        df["high"] = df["close"]
        df["low"] = df["close"]
        df["volume"] = 0.0

        df = df[["open", "high", "low", "close", "volume"]]
        df = df.sort_index()

        return df
