"""Comprehensive tool tests with real market data integration."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from strategy_research.kernel.agent.builtin_tools import (
    FactorAnalysisTool,
    FactorCrossSectionalAnalysis,
    FactorICDecay,
    FactorQuintileReturns,
    FactorTurnover,
    PatternRecognitionTool,
    build_default_registry,
)
from strategy_research.kernel.agent.builtin_tools.data_tools import (
    GetMarketDataTool,
    ImportDataTool,
    ListDataSourcesTool,
)
from strategy_research.kernel.agent.tools import ToolContext
from strategy_research.apps.db import get_connection, init_db

# ── Shared fixtures ───────────────────────────────────────────────────


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create workspace with initialized DuckDB."""
    init_db(tmp_path)
    return tmp_path


@pytest.fixture
def real_market_data(workspace: Path) -> dict:
    """Generate realistic market data for multiple A-share stocks."""
    np.random.seed(42)
    dates = pd.date_range("2023-01-01", periods=252, freq="B")  # 1 year trading days

    # Simulate realistic A-share price data
    stocks = {
        "000001.SZ": {"name": "平安银行", "base": 12.0, "vol": 0.02},
        "600519.SH": {"name": "贵州茅台", "base": 1800.0, "vol": 0.015},
        "000858.SZ": {"name": "五粮液", "base": 150.0, "vol": 0.025},
        "601318.SH": {"name": "中国平安", "base": 45.0, "vol": 0.018},
        "000333.SZ": {"name": "美的集团", "base": 55.0, "vol": 0.022},
        "600036.SH": {"name": "招商银行", "base": 35.0, "vol": 0.02},
        "000651.SZ": {"name": "格力电器", "base": 38.0, "vol": 0.023},
        "601012.SH": {"name": "隆基绿能", "base": 25.0, "vol": 0.03},
        "300750.SZ": {"name": "宁德时代", "base": 200.0, "vol": 0.028},
        "002594.SZ": {"name": "比亚迪", "base": 250.0, "vol": 0.026},
    }

    data = {}
    for code, info in stocks.items():
        returns = np.random.randn(len(dates)) * info["vol"]
        prices = info["base"] * np.exp(np.cumsum(returns))

        stock_data = []
        for i, (d, close) in enumerate(zip(dates, prices)):
            high = close * (1 + abs(np.random.randn()) * 0.01)
            low = close * (1 - abs(np.random.randn()) * 0.01)
            open_price = close * (1 + np.random.randn() * 0.005)
            volume = int(np.random.lognormal(15, 0.5))

            stock_data.append({
                "date": d.strftime("%Y-%m-%d"),
                "open": round(open_price, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "close": round(close, 2),
                "volume": volume,
            })
        data[code] = stock_data

    return data


@pytest.fixture
def populated_workspace(workspace: Path, real_market_data: dict) -> Path:
    """Workspace with real market data imported."""
    import_tool = ImportDataTool()
    result = json.loads(import_tool.execute(
        ctx=ToolContext(workspace=workspace),
        data=real_market_data,
    ))
    assert result["status"] == "ok"
    return workspace


# ── Integration: Import + Analysis Workflow ───────────────────────────


class TestImportAndAnalysis:
    def test_import_multiple_stocks(self, workspace: Path, real_market_data: dict):
        """Test importing data for 10 A-share stocks."""
        import_tool = ImportDataTool()
        result = json.loads(import_tool.execute(
            ctx=ToolContext(workspace=workspace),
            data=real_market_data,
        ))
        assert result["status"] == "ok"
        assert result["imported"] == 2520  # 10 stocks * 252 days
        assert result["n_codes"] == 10

    def test_import_then_query(self, populated_workspace: Path):
        """Test importing data then querying it."""
        conn = get_connection(populated_workspace, read_only=True)

        # Query ohlcv view
        df = conn.execute("SELECT * FROM ohlcv LIMIT 10").fetchdf()
        assert len(df) == 10
        assert "date" in df.columns
        assert "asset" in df.columns
        assert "close" in df.columns

        # Count assets
        assets = conn.execute("SELECT DISTINCT asset FROM ohlcv").fetchdf()
        assert len(assets) == 10

        conn.close()

    def test_factor_analysis_real_data(self, populated_workspace: Path):
        """Test factor analysis with real market data."""
        tool = FactorAnalysisTool()
        result = json.loads(tool.execute(
            ctx=ToolContext(workspace=populated_workspace),
            factor_code="ts_return(close, 20)",
        ))
        assert result["status"] == "ok"
        assert "ic_mean" in result
        assert "spearman_ic" in result
        assert "n_observations" in result

    def test_cross_sectional_analysis_real_data(self, populated_workspace: Path):
        """Test cross-sectional IC analysis with real data."""
        tool = FactorCrossSectionalAnalysis()
        result = json.loads(tool.execute(
            ctx=ToolContext(workspace=populated_workspace),
            factor_code="ts_return(close, 20)",
        ))
        assert result["status"] == "ok"
        assert "ic_pearson_mean" in result
        assert "ic_spearman_mean" in result
        assert result["n_assets"] == 10
        assert result["n_dates"] > 200

    def test_ic_decay_real_data(self, populated_workspace: Path):
        """Test IC decay analysis with real data."""
        tool = FactorICDecay()
        result = json.loads(tool.execute(
            ctx=ToolContext(workspace=populated_workspace),
            factor_code="ts_return(close, 20)",
        ))
        assert result["status"] == "ok"
        assert "ic_decay" in result
        assert isinstance(result["ic_decay"], list)

    def test_quintile_returns_real_data(self, populated_workspace: Path):
        """Test quintile returns analysis with real data."""
        tool = FactorQuintileReturns()
        result = json.loads(tool.execute(
            ctx=ToolContext(workspace=populated_workspace),
            factor_code="ts_return(close, 20)",
        ))
        assert result["status"] == "ok"
        assert "n_groups" in result
        assert result["n_groups"] == 5
        assert "long_short_spread" in result

    def test_turnover_real_data(self, populated_workspace: Path):
        """Test factor turnover analysis with real data."""
        tool = FactorTurnover()
        result = json.loads(tool.execute(
            ctx=ToolContext(workspace=populated_workspace),
            factor_code="ts_return(close, 20)",
        ))
        assert result["status"] == "ok"
        assert "avg_turnover" in result
        assert "median_turnover" in result

    def test_pattern_recognition_real_data(self, populated_workspace: Path):
        """Test pattern recognition with real data."""
        tool = PatternRecognitionTool()
        result = json.loads(tool.execute(
            ctx=ToolContext(workspace=populated_workspace),
            asset="000001.SZ",
        ))
        assert result["status"] == "ok"
        assert "patterns" in result


# ── Integration: Factor Expression Variants ──────────────────────────


class TestFactorExpressions:
    """Test various factor expressions with real data."""

    def test_momentum_factor(self, populated_workspace: Path):
        """Test momentum factor (20-day return)."""
        tool = FactorCrossSectionalAnalysis()
        result = json.loads(tool.execute(
            ctx=ToolContext(workspace=populated_workspace),
            factor_code="ts_return(close, 20)",
        ))
        assert result["status"] == "ok"
        assert "ic_pearson_mean" in result

    def test_volatility_factor(self, populated_workspace: Path):
        """Test volatility factor."""
        tool = FactorCrossSectionalAnalysis()
        result = json.loads(tool.execute(
            ctx=ToolContext(workspace=populated_workspace),
            factor_code="ts_std(ts_return(close, 1), 20)",
        ))
        assert result["status"] == "ok"

    def test_mean_reversion_factor(self, populated_workspace: Path):
        """Test mean reversion factor."""
        tool = FactorCrossSectionalAnalysis()
        result = json.loads(tool.execute(
            ctx=ToolContext(workspace=populated_workspace),
            factor_code="close / ts_mean(close, 20) - 1",
        ))
        assert result["status"] == "ok"

    def test_volume_price_factor(self, populated_workspace: Path):
        """Test volume-price correlation factor."""
        tool = FactorCrossSectionalAnalysis()
        result = json.loads(tool.execute(
            ctx=ToolContext(workspace=populated_workspace),
            factor_code="ts_corr(close, volume, 20)",
        ))
        assert result["status"] == "ok"

    def test_rank_factor(self, populated_workspace: Path):
        """Test rank-based factor."""
        tool = FactorCrossSectionalAnalysis()
        result = json.loads(tool.execute(
            ctx=ToolContext(workspace=populated_workspace),
            factor_code="ts_rank(close, 20)",
        ))
        assert result["status"] == "ok"


# ── Integration: Data Source Tools ────────────────────────────────────


class TestDataSourceTools:
    def test_list_data_sources(self):
        """Test listing available data sources."""
        tool = ListDataSourcesTool()
        result = json.loads(tool.execute(ctx=ToolContext(workspace=None)))
        assert result["status"] == "ok"
        assert "sources" in result
        assert len(result["sources"]) > 0

        # Check that at least one source is available
        available = [s for s in result["sources"] if s.get("available")]
        assert len(available) > 0

    def test_get_market_data_validation(self):
        """Test input validation for get_market_data (v2 signatures)."""
        tool = GetMarketDataTool()
        ctx = ToolContext(workspace=None)

        # Missing required args → v2 必选参数缺失由框架层抛 TypeError
        with pytest.raises(TypeError):
            tool.execute(ctx=ctx, start_date="2023-01-01", end_date="2023-01-31")
        with pytest.raises(TypeError):
            tool.execute(ctx=ctx, codes=["000001.SZ"])

        # Empty codes → value validation error
        result = json.loads(tool.execute(
            ctx=ctx,
            codes=[],
            start_date="2023-01-01",
            end_date="2023-01-31",
        ))
        assert result["status"] == "error"


# ── Integration: Registry Tests ──────────────────────────────────────


class TestRegistryIntegration:
    def test_all_tools_registered(self):
        """Test that all expected tools are registered."""
        r = build_default_registry()
        expected_tools = [
            "read", "write", "compute_factor", "factor_analysis",
            "run_backtest", "git_diff", "list_history", "pattern_recognition",
            "list_skills", "skill", "options_pricing",
            "factor_cross_sectional_analysis", "factor_quintile_returns",
            "factor_ic_decay", "factor_turnover",
            "strategy_compare", "drawdown_analysis", "benchmark_comparison",
            "get_market_data", "list_data_sources", "search_symbol", "import_data",
        ]
        for name in expected_tools:
            assert name in r.tool_names, f"Missing tool: {name}"

    def test_tool_descriptions_complete(self):
        """Test that all tools have complete descriptions.

        v2: description is a short one-liner (detailed docs live in the
        execute docstring / tool card), so only non-empty is enforced.
        """
        r = build_default_registry()
        for name in r.tool_names:
            tool = r.get(name)
            assert tool.description, f"{name} has empty description"
            assert len(tool.description) > 10, f"{name} description too short"

    def test_tool_parameters_valid(self):
        """Test that all tools have valid parameter schemas.

        v2: schemas derive from the execute() signature via
        to_openai_schema(); the legacy `parameters` dict is not populated
        for migrated tools.
        """
        r = build_default_registry()
        for name in r.tool_names:
            tool = r.get(name)
            schema = tool.to_openai_schema()["function"]["parameters"]
            assert schema.get("type") == "object", f"{name} missing object type"
            assert "properties" in schema, f"{name} missing properties"


# ── Performance: Large Dataset Tests ─────────────────────────────────


class TestPerformance:
    def test_large_dataset_import(self, workspace: Path):
        """Test importing a large dataset."""
        np.random.seed(42)
        dates = pd.date_range("2020-01-01", periods=1000, freq="B")  # 4 years

        # Generate data for 50 stocks
        data = {}
        for i in range(50):
            code = f"STOCK{i:03d}.SZ"
            returns = np.random.randn(len(dates)) * 0.02
            prices = 100 * np.exp(np.cumsum(returns))

            stock_data = []
            for d, close in zip(dates, prices):
                stock_data.append({
                    "date": d.strftime("%Y-%m-%d"),
                    "open": round(close * (1 + np.random.randn() * 0.005), 2),
                    "high": round(close * 1.01, 2),
                    "low": round(close * 0.99, 2),
                    "close": round(close, 2),
                    "volume": int(np.random.lognormal(15, 0.5)),
                })
            data[code] = stock_data

        import_tool = ImportDataTool()
        result = json.loads(import_tool.execute(ctx=ToolContext(workspace=workspace), data=data))
        assert result["status"] == "ok"
        assert result["imported"] == 50000  # 50 stocks * 1000 days

    def test_cross_sectional_analysis_performance(self, workspace: Path):
        """Test cross-sectional analysis with large dataset."""
        # Import large dataset first
        np.random.seed(42)
        dates = pd.date_range("2020-01-01", periods=500, freq="B")

        data = {}
        for i in range(30):
            code = f"STOCK{i:03d}.SZ"
            returns = np.random.randn(len(dates)) * 0.02
            prices = 100 * np.exp(np.cumsum(returns))

            stock_data = []
            for d, close in zip(dates, prices):
                stock_data.append({
                    "date": d.strftime("%Y-%m-%d"),
                    "open": round(close * (1 + np.random.randn() * 0.005), 2),
                    "high": round(close * 1.01, 2),
                    "low": round(close * 0.99, 2),
                    "close": round(close, 2),
                    "volume": int(np.random.lognormal(15, 0.5)),
                })
            data[code] = stock_data

        import_tool = ImportDataTool()
        import_tool.execute(ctx=ToolContext(workspace=workspace), data=data)

        # Run analysis
        tool = FactorCrossSectionalAnalysis()
        result = json.loads(tool.execute(
            ctx=ToolContext(workspace=workspace),
            factor_code="ts_return(close, 20)",
        ))
        assert result["status"] == "ok"
        assert result["n_assets"] == 30
