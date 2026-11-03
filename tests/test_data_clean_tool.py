"""DataCleanTool (LLM 包装层) 全链路测试。

core/tools/data_clean.py 的 clean_data 纯函数已在 test_data_clean.py
覆盖；本文件验证 BaseTool 包装层：查询 price_data、dry_run 语义、
写库（DELETE + 重写）、错误路径与注册元数据。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from strategy_research.kernel.agent.builtin_tools import (
    DataCleanTool,
    build_default_registry,
)
from strategy_research.kernel.agent.tools import ToolContext
from strategy_research.apps.db import get_connection, save_ohlcv_to_db


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "strategies").mkdir()
    (tmp_path / "data").mkdir()
    return tmp_path


@pytest.fixture
def db_with_nan(workspace: Path) -> Path:
    """两资产 × 5 天，600519 的 close 含 1 个 NaN。"""
    dates = pd.bdate_range("2024-01-01", periods=5)
    rng = np.random.default_rng(1)
    data_map: dict[str, pd.DataFrame] = {}
    for code in ["600519.SH", "000858.SZ"]:
        close = 100 * np.cumprod(1 + rng.normal(0.001, 0.01, len(dates)))
        df = pd.DataFrame(
            {
                "open": close * 0.99,
                "high": close * 1.01,
                "low": close * 0.98,
                "close": close,
                "volume": 1e6,
            },
            index=pd.DatetimeIndex(dates, name="trade_date"),
        )
        if code == "600519.SH":
            df.iloc[2, df.columns.get_loc("close")] = np.nan
        data_map[code] = df
    save_ohlcv_to_db(workspace, data_map, "clean_strat")
    return workspace


def _count_nan(workspace: Path) -> int:
    conn = get_connection(workspace)
    try:
        return int(conn.execute(
            "SELECT COUNT(*) FROM price_data WHERE close IS NULL"
        ).fetchone()[0])
    finally:
        conn.close()


def _count_rows(workspace: Path) -> int:
    conn = get_connection(workspace)
    try:
        return int(conn.execute(
            "SELECT COUNT(*) FROM price_data"
        ).fetchone()[0])
    finally:
        conn.close()


class TestDataCleanTool:

    def test_registered_in_default_registry(self):
        """clean_data 在默认注册表中且标记为写工具。"""
        registry = build_default_registry()
        tool = registry.get("clean_data")
        assert isinstance(tool, DataCleanTool)
        assert tool.is_readonly is False
        assert registry.get("clean_data") is not None

    def test_dry_run_returns_report_and_does_not_write(self, db_with_nan):
        """dry_run=True 只返回报告，DB 不变。"""
        tool = build_default_registry().get("clean_data")
        result = json.loads(tool.execute(
            ctx=ToolContext(workspace=db_with_nan), strategy_name="clean_strat"
        ))
        assert result["status"] == "ok"
        assert result["dry_run"] is True
        report = result["report"]
        assert "dedup" in report["steps_applied"]
        assert "impute" in report["steps_applied"]
        assert report["initial_rows"] == 10
        assert report["missing_filled"] == 1
        # DB 未被修改
        assert _count_nan(db_with_nan) == 1
        assert _count_rows(db_with_nan) == 10

    def test_dry_run_false_persists_cleaned_data(self, db_with_nan):
        """dry_run=False 清空并重写 price_data，NaN 被填充。"""
        tool = build_default_registry().get("clean_data")
        result = json.loads(tool.execute(
            ctx=ToolContext(workspace=db_with_nan),
            strategy_name="clean_strat",
            dry_run=False,
        ))
        assert result["status"] == "ok"
        assert result["dry_run"] is False
        assert result["report"]["missing_filled"] == 1
        assert _count_nan(db_with_nan) == 0
        assert _count_rows(db_with_nan) == 10

    def test_custom_steps_and_params(self, db_with_nan):
        """preset=custom + steps/params 被透传。"""
        tool = build_default_registry().get("clean_data")
        result = json.loads(tool.execute(
            ctx=ToolContext(workspace=db_with_nan),
            strategy_name="clean_strat",
            preset="custom",
            steps=["impute"],
            params={"impute_method": "zero", "impute_columns": ["close"]},
            dry_run=True,
        ))
        assert result["status"] == "ok"
        assert result["report"]["steps_applied"] == ["impute"]
        assert result["report"]["params_applied"]["impute_method"] == "zero"

    def test_invalid_preset(self, db_with_nan):
        """无效 preset → 可操作错误。"""
        tool = build_default_registry().get("clean_data")
        result = json.loads(tool.execute(
            ctx=ToolContext(workspace=db_with_nan),
            strategy_name="clean_strat",
            preset="bogus",
        ))
        assert result["status"] == "error"
        assert "bogus" in result["error"]
        assert "quick" in result["expected"]

    def test_empty_table(self, workspace):
        """无 DB/无数据 → 提示先取数。"""
        tool = build_default_registry().get("clean_data")
        result = json.loads(tool.execute(
            ctx=ToolContext(workspace=workspace), strategy_name="clean_strat"
        ))
        assert result["status"] == "error"
        assert "duckdb" in result["error"].lower() or "empty" in result["error"].lower()
        assert "get_market_data" in result["fix"]

    def test_missing_workspace(self, db_with_nan):
        """缺 workspace → 结构化错误。"""
        tool = build_default_registry().get("clean_data")
        result = json.loads(tool.execute(
            ctx=ToolContext(), strategy_name="clean_strat",
        ))
        assert result["status"] == "error"
        assert "workspace" in result["error"]
