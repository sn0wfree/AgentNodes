"""SearchSymbolTool 测试（akshare 打桩，不依赖网络）。"""
from __future__ import annotations

import json
import sys
import types

import pandas as pd
import pytest

from strategy_research.kernel.agent.builtin_tools import build_default_registry
from strategy_research.kernel.agent.builtin_tools.data_tools import SearchSymbolTool


def _fake_akshare(df: pd.DataFrame) -> types.ModuleType:
    """构造带 stock_zh_a_spot_em 的 fake akshare 模块。"""
    mod = types.ModuleType("akshare")

    def stock_zh_a_spot_em():
        return df

    mod.stock_zh_a_spot_em = stock_zh_a_spot_em
    return mod


@pytest.fixture
def spot_df() -> pd.DataFrame:
    return pd.DataFrame({
        "代码": ["600519", "000858", "601318"],
        "名称": ["贵州茅台", "五粮液", "中国平安"],
        "最新价": [1500.0, 130.0, 45.0],
        "涨跌幅": [1.2, -0.5, 0.8],
    })


class TestSearchSymbolTool:

    def test_registered_and_readonly(self):
        registry = build_default_registry()
        tool = registry.get("search_symbol")
        assert isinstance(tool, SearchSymbolTool)
        assert tool.is_readonly is True

    def test_match_by_code(self, monkeypatch, spot_df):
        monkeypatch.setitem(sys.modules, "akshare", _fake_akshare(spot_df))
        result = json.loads(SearchSymbolTool().execute(ctx=None, query="600519"))
        assert result["status"] == "ok"
        assert result["n_results"] == 1
        assert result["results"][0]["code"] == "600519"
        assert result["results"][0]["name"] == "贵州茅台"

    def test_match_by_name(self, monkeypatch, spot_df):
        monkeypatch.setitem(sys.modules, "akshare", _fake_akshare(spot_df))
        result = json.loads(SearchSymbolTool().execute(ctx=None, query="茅台"))
        assert result["status"] == "ok"
        assert result["n_results"] == 1
        assert result["results"][0]["code"] == "600519"

    def test_match_multiple_and_limit(self, monkeypatch, spot_df):
        monkeypatch.setitem(sys.modules, "akshare", _fake_akshare(spot_df))
        result = json.loads(SearchSymbolTool().execute(ctx=None, query="60", limit=2))
        assert result["status"] == "ok"
        assert result["n_results"] == 2

    def test_empty_spot_returns_empty_results(self, monkeypatch):
        monkeypatch.setitem(
            sys.modules, "akshare",
            _fake_akshare(pd.DataFrame(columns=["代码", "名称"])),
        )
        result = json.loads(SearchSymbolTool().execute(ctx=None, query="xyz"))
        assert result["status"] == "ok"
        assert result["results"] == []

    def test_no_match_returns_empty(self, monkeypatch, spot_df):
        monkeypatch.setitem(sys.modules, "akshare", _fake_akshare(spot_df))
        result = json.loads(SearchSymbolTool().execute(ctx=None, query="zzz"))
        assert result["status"] == "ok"
        assert result["n_results"] == 0

    def test_akshare_not_installed(self, monkeypatch):
        real_import = __import__

        def fake_import(name, *args, **kwargs):
            if name == "akshare":
                raise ImportError("No module named 'akshare'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", fake_import)
        result = json.loads(SearchSymbolTool().execute(ctx=None, query="600519"))
        assert result["status"] == "error"
        assert "akshare" in result["error"].lower()

    def test_missing_query(self):
        """缺必填参数由框架拦截 (TypeError → loop 重试/兜底)。"""
        with pytest.raises(TypeError):
            SearchSymbolTool().execute(ctx=None)
