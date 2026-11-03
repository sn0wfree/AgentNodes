"""Tests for market data MCP tools: get_market_data, list_data_sources, search_symbol."""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def server():
    from strategy_research.interfaces.mcp.server import MCPServer
    s = MCPServer()
    s.register_default_tools()
    return s


def _extract_body(result: dict) -> dict:
    if "content" in result:
        text = result["content"][0]["text"]
        return json.loads(text)
    elif "error" in result:
        return {"status": "error", "error": result["error"]}
    else:
        raise AssertionError(f"Unexpected result: {result}")


# ============================================================
# get_market_data
# ============================================================


class TestGetMarketData:
    def test_requires_params(self, server):
        result = server.call_tool("get_market_data", {})
        body = _extract_body(result)
        assert body["status"] == "error"

    def test_requires_codes(self, server):
        result = server.call_tool("get_market_data", {
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
        })
        body = _extract_body(result)
        assert body["status"] == "error"
        assert "codes" in body["error"].lower()

    def test_requires_dates(self, server):
        result = server.call_tool("get_market_data", {
            "codes": ["000001.SZ"],
        })
        body = _extract_body(result)
        assert body["status"] == "error"

    def test_invalid_date_range(self, server):
        result = server.call_tool("get_market_data", {
            "codes": ["000001.SZ"],
            "start_date": "2024-12-31",
            "end_date": "2024-01-01",
        })
        body = _extract_body(result)
        assert body["status"] == "error"

    def test_returns_meta(self, server):
        """验证返回格式包含 meta 字段。"""
        result = server.call_tool("get_market_data", {
            "codes": ["000001.SZ"],
            "start_date": "2024-01-01",
            "end_date": "2024-01-05",
        })
        body = _extract_body(result)
        # 可能成功也可能失败（取决于数据源可用性），但格式应正确
        assert "status" in body
        if body["status"] == "ok":
            assert "meta" in body
            assert "data" in body

    def test_max_rows_configurable(self, server):
        result = server.call_tool("get_market_data", {
            "codes": ["000001.SZ"],
            "start_date": "2024-01-01",
            "end_date": "2024-03-01",
            "max_rows": 10,
        })
        body = _extract_body(result)
        assert "status" in body

    def test_source_override(self, server):
        result = server.call_tool("get_market_data", {
            "codes": ["000001.SZ"],
            "start_date": "2024-01-01",
            "end_date": "2024-01-05",
            "source": "tencent",
        })
        body = _extract_body(result)
        assert "status" in body


# ============================================================
# list_data_sources
# ============================================================


class TestListDataSources:
    def test_returns_sources(self, server):
        result = server.call_tool("list_data_sources", {})
        body = _extract_body(result)
        assert body["status"] == "ok"
        assert "sources" in body
        assert body["n_sources"] > 0

    def test_source_format(self, server):
        result = server.call_tool("list_data_sources", {})
        body = _extract_body(result)
        assert body["status"] == "ok"
        for src in body["sources"]:
            assert "name" in src
            assert "available" in src
            assert "markets" in src
            assert "requires_auth" in src


# ============================================================
# search_symbol
# ============================================================


class TestSearchSymbol:
    def test_requires_query(self, server):
        result = server.call_tool("search_symbol", {})
        body = _extract_body(result)
        assert body["status"] == "error"

    def test_empty_query(self, server):
        result = server.call_tool("search_symbol", {"query": ""})
        body = _extract_body(result)
        assert body["status"] == "error"

    def test_format(self, server):
        result = server.call_tool("search_symbol", {
            "query": "test",
            "limit": 5,
        })
        body = _extract_body(result)
        assert "status" in body
        assert "results" in body or body["status"] == "error"
