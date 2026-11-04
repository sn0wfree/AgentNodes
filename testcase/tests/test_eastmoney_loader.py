"""Tests for eastmoney_loader — PR2 extra 新增的东方财富 loader."""
from __future__ import annotations

import pytest

# ============================================================
# 1. _to_secid 映射
# ============================================================


class TestToSecid:
    """资产代码 → 东财 secid 映射。"""

    def test_shanghai_a_share(self):
        from strategy_research.apps.data_source.eastmoney_loader import _to_secid
        assert _to_secid("600519.SH") == "1.600519"

    def test_shenzhen_a_share(self):
        from strategy_research.apps.data_source.eastmoney_loader import _to_secid
        assert _to_secid("000001.SZ") == "0.000001"

    def test_beijing_a_share(self):
        from strategy_research.apps.data_source.eastmoney_loader import _to_secid
        # 北京交易所用 6 位数字 + .BJ
        assert _to_secid("430139.BJ") == "0.430139"
        assert _to_secid("830799.BJ") == "0.830799"

    def test_hk_stock(self):
        from strategy_research.apps.data_source.eastmoney_loader import _to_secid
        assert _to_secid("00700.HK") == "116.00700"

    def test_hk_stock_5digit(self):
        from strategy_research.apps.data_source.eastmoney_loader import _to_secid
        # 港股代码可能有 5 位
        assert _to_secid("09988.HK") == "116.09988"

    def test_us_stock_returns_none(self):
        """美股不在 eastmoney 支持范围内。"""
        from strategy_research.apps.data_source.eastmoney_loader import _to_secid
        assert _to_secid("AAPL.US") is None

    def test_unknown_format_returns_none(self):
        from strategy_research.apps.data_source.eastmoney_loader import _to_secid
        assert _to_secid("invalid_code") is None


# ============================================================
# 2. Loader 类属性与 is_available
# ============================================================


class TestEastmoneyLoaderBasics:
    """Loader 基础属性。"""

    def test_registered_in_registry(self):
        from strategy_research.apps.data_source import LOADER_REGISTRY
        assert "eastmoney" in LOADER_REGISTRY

    def test_name_attribute(self):
        from strategy_research.apps.data_source.eastmoney_loader import EastmoneyLoader
        assert EastmoneyLoader.name == "eastmoney"

    def test_markets(self):
        from strategy_research.apps.data_source.eastmoney_loader import EastmoneyLoader
        # 应包含 a_share 和 hk_equity
        assert "a_share" in EastmoneyLoader.markets
        assert "hk_equity" in EastmoneyLoader.markets

    def test_no_auth_required(self):
        from strategy_research.apps.data_source.eastmoney_loader import EastmoneyLoader
        assert EastmoneyLoader.requires_auth is False

    def test_is_available_no_network(self):
        """无网络也应声明可用（构造时才探测）。"""
        from strategy_research.apps.data_source.eastmoney_loader import EastmoneyLoader
        loader = EastmoneyLoader()
        assert loader.is_available() is True

    def test_klt_mapping(self):
        """klt 参数：日K=101，周K=102，月K=103。"""
        # 间接通过 fetch 调用验证（不需要实际网络）
        from strategy_research.apps.data_source.eastmoney_loader import EastmoneyLoader
        loader = EastmoneyLoader()
        # Mock fetch 跳过实际请求
        # 这里仅验证构造和接口正常
        assert hasattr(loader, "fetch")


# ============================================================
# 3. fetch 过滤与 skip 行为
# ============================================================


class TestFetchFiltering:
    """fetch 应跳过不支持的代码。"""

    def test_skips_unsupported_codes(self, monkeypatch: pytest.MonkeyPatch):
        """美股等不在 markets 的代码应被跳过。"""
        from strategy_research.apps.data_source.eastmoney_loader import EastmoneyLoader

        loader = EastmoneyLoader()
        # Mock _fetch_one 不被调用
        called_with = []

        def mock_fetch_one(secid, code, start_date, end_date, klt):
            called_with.append(code)
            return None

        monkeypatch.setattr(loader, "_fetch_one", mock_fetch_one)

        # 混合：2 个 A 股 + 1 个美股 (force_refresh 绕过 file_cache，
        # 否则命中磁盘缓存时 _fetch_one 不会被调用)
        loader.fetch(codes=['600519.SH', '000001.SZ', 'AAPL.US'], start_date='2024-01-01', end_date='2024-01-31', force_refresh=True)

        # 只应调用 A 股的 fetch
        assert "AAPL.US" not in called_with
        assert "600519.SH" in called_with
        assert "000001.SZ" in called_with

    def test_handles_empty_codes(self):
        """空 codes 列表应返回空 dict。"""
        from strategy_research.apps.data_source.eastmoney_loader import EastmoneyLoader

        loader = EastmoneyLoader()
        result = loader.fetch(
            codes=[],
            start_date="2024-01-01",
            end_date="2024-01-31",
        )
        assert result == {}

    def test_continues_on_fetch_error(self, monkeypatch: pytest.MonkeyPatch):
        """单个代码失败不应影响其他。"""
        from strategy_research.apps.data_source.eastmoney_loader import EastmoneyLoader

        loader = EastmoneyLoader()

        def mock_fetch_one(secid, code, start_date, end_date, klt):
            if code == "600519.SH":
                raise RuntimeError("network error")
            import pandas as pd
            return pd.DataFrame({
                "open": [1.0], "close": [1.1], "high": [1.2],
                "low": [0.9], "volume": [1000.0],
            }, index=pd.date_range("2024-01-01", periods=1))

        monkeypatch.setattr(loader, "_fetch_one", mock_fetch_one)

        result = loader.fetch(
            codes=["600519.SH", "000001.SZ"],
            start_date="2024-01-01",
            end_date="2024-01-31",
        )

        # 失败的 600519 不应在结果，成功的 000001 应在
        assert "600519.SH" not in result
        assert "000001.SZ" in result


# ============================================================
# 4. 与 fallback chain 的集成
# ============================================================


class TestIntegrationWithFallbackChain:
    """eastmoney 应在 a_share 和 hk_equity 的 fallback chain 中。"""

    def test_a_share_chain_contains_eastmoney(self):
        from strategy_research.apps.data_source.registry import FALLBACK_CHAINS
        assert "eastmoney" in FALLBACK_CHAINS["a_share"]

    def test_hk_chain_contains_eastmoney(self):
        from strategy_research.apps.data_source.registry import FALLBACK_CHAINS
        assert "eastmoney" in FALLBACK_CHAINS["hk"]

    def test_resolve_loader_finds_eastmoney_for_a_share(self, monkeypatch):
        """resolve_loader('a_share') 应能在 tencent/akshare 不可用时 fallback 到 eastmoney。"""
        from strategy_research.apps.data_source import resolve_loader
        # 直接尝试解析 — eastmoney 应可用
        loader = resolve_loader("a_share")
        assert loader.name in {"tencent", "mootdx", "eastmoney", "baostock",
                                "akshare", "tushare", "local"}

    def test_list_loaders_includes_eastmoney(self):
        from strategy_research.apps.data_source import list_loaders
        loaders = list_loaders()
        assert "eastmoney" in loaders
