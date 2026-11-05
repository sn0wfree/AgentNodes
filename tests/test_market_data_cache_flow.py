"""Tests for the merged get_market_data flow (fetch + persist in one step).

Covers the context-overflow fix (docs/design/context-overflow-fix.md):
- get_market_data returns a compact summary + preview (NOT full OHLCV).
- persist=True (default) writes the fetched OHLCV into the workspace
  DuckDB via save_ohlcv_to_db — no separate commit_market_data step.
- commit_market_data tool is retired (get_market_data persists directly).
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pandas as pd
import pytest

from strategy_research.kernel.agent.tools import ToolContext

os.environ.setdefault("STRATEGY_RESEARCH_CACHE_DIR", "/tmp/sr_test_cache")


@pytest.fixture(autouse=True)
def _clean_cache():
    shutil.rmtree("/tmp/sr_test_cache", ignore_errors=True)
    yield
    shutil.rmtree("/tmp/sr_test_cache", ignore_errors=True)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "strategies").mkdir()
    (tmp_path / "data").mkdir()
    return tmp_path


class _FakeLoader:
    name = "fake"

    def is_available(self):
        return True

    def fetch(self, codes, start_date, end_date, *, interval="1D", fields=None,
              force_refresh=False):
        out = {}
        for c in codes:
            dates = pd.date_range(start_date, periods=10, freq="D")
            out[c] = pd.DataFrame({
                "date": dates,
                "open": 10.0, "high": 11.0, "low": 9.5,
                "close": 10.5, "volume": 1000,
            })
        return out


@pytest.fixture
def gmd():
    # Stub the loader registry + market detection so the tool finds the
    # fake loader without network.
    import strategy_research.apps.data_source.registry as dsr
    from strategy_research.kernel.agent.builtin_tools.data_tools import (
        GetMarketDataTool,
    )
    dsr.LOADER_REGISTRY["fake"] = _FakeLoader
    dsr.detect_market = lambda code: "a_share"
    return GetMarketDataTool()


def _run_get(tool, codes, start="2023-01-01", end="2023-01-10", source="fake",
             **extra):
    kwargs = dict(codes=codes, start_date=start, end_date=end, source=source)
    kwargs.update(extra)
    ws = kwargs.pop("workspace", None)
    kwargs["ctx"] = ToolContext(workspace=ws)
    return json.loads(tool.execute(**kwargs))


class TestGetMarketDataSummary:
    def test_returns_summary_not_full_rows(self, gmd, workspace):
        result = _run_get(gmd, ["600519.SH", "000858.SZ"], workspace=str(workspace))
        assert result["status"] == "ok"
        # Full data must NOT be present (context-overflow fix)
        assert "data" not in result
        # No legacy cache_key / cached plumbing in the agent interface
        assert "cached" not in result
        assert "cache_key" not in result.get("summary", {}).get("600519.SH", {})
        assert "next_step" not in result
        # summary has stats, not rows
        s = result["summary"]["600519.SH"]
        assert s["rows"] == 10
        assert s["status"] == "ok"
        assert "first_close" in s and "last_close" in s
        assert "avg_volume" in s
        # preview limited to 5 rows
        assert len(result["preview"]["600519.SH"]) == 5

    def test_empty_code_marks_summary_empty(self, gmd, workspace):
        result = _run_get(gmd, ["600519.SH"], workspace=str(workspace))
        assert result["status"] == "ok"

    def test_default_persist_true(self, gmd, workspace):
        result = _run_get(gmd, ["600519.SH"], workspace=str(workspace))
        assert result["persisted"] is True
        assert result["persisted_rows"] == 10


class TestPersistToDuckDB:
    def test_persist_writes_into_duckdb(self, gmd, workspace):
        result = _run_get(gmd, ["600519.SH", "000858.SZ"], workspace=str(workspace))
        assert result["status"] == "ok"
        assert result["persisted"] is True
        assert result["persisted_rows"] == 20

        from strategy_research.apps.db import get_connection, init_db
        init_db(workspace)
        conn = get_connection(workspace)
        rows = conn.execute(
            "SELECT asset_code, COUNT(*) FROM price_data GROUP BY 1"
        ).fetchall()
        assert dict(rows) == {"600519.SH": 10, "000858.SZ": 10}

    def test_persist_strategy_partitioning(self, gmd, workspace):
        _run_get(gmd, ["600519.SH"], workspace=str(workspace), strategy_name="blue_chip")
        from strategy_research.apps.db import get_connection, init_db
        init_db(workspace)
        conn = get_connection(workspace)
        rows = conn.execute(
            "SELECT DISTINCT strategy_name FROM price_data"
        ).fetchall()
        assert rows == [("blue_chip",)]

    def test_persist_false_does_not_write(self, gmd, workspace):
        result = _run_get(gmd, ["600519.SH"], workspace=str(workspace), persist=False)
        assert result["persisted"] is False
        assert result["persisted_rows"] == 0
        assert result["status"] == "ok"

    def test_persist_true_requires_workspace(self, gmd):
        result = _run_get(gmd, ["600519.SH"])  # no workspace
        assert result["status"] == "error"
        assert "workspace" in result["error"]
        assert "persist" in result["fix"].lower() or "ctx" in result["fix"]

    def test_repeated_persist_is_idempotent(self, gmd, workspace):
        """INSERT OR REPLACE → same rows, no duplication."""
        _run_get(gmd, ["600519.SH"], workspace=str(workspace))
        _run_get(gmd, ["600519.SH"], workspace=str(workspace))
        from strategy_research.apps.db import get_connection, init_db
        init_db(workspace)
        conn = get_connection(workspace)
        n = conn.execute(
            "SELECT COUNT(*) FROM price_data WHERE asset_code='600519.SH'"
        ).fetchone()[0]
        assert n == 10


class TestGetMarketDataEdgeCases:
    def test_summary_fields_complete(self, gmd, workspace):
        result = _run_get(gmd, ["600519.SH"], workspace=str(workspace))
        s = result["summary"]["600519.SH"]
        for field in ("rows", "status", "first_close", "last_close",
                      "close_min", "close_max", "avg_volume"):
            assert field in s, f"summary missing {field}"

    def test_preview_bounded_to_5_rows(self, gmd, workspace):
        result = _run_get(gmd, ["600519.SH"], workspace=str(workspace))
        assert len(result["preview"]["600519.SH"]) <= 5

    def test_unavailable_source_errors(self, gmd, workspace):
        """Explicit source that exists but is unavailable → actionable error."""
        import strategy_research.apps.data_source.registry as dsr

        class _Unavailable:
            name = "down"
            def is_available(self):
                return False
        dsr.LOADER_REGISTRY["down"] = _Unavailable
        raw = gmd.execute(
            ctx=ToolContext(workspace=workspace), codes=["600519.SH"],
            start_date="2023-01-01", end_date="2023-01-10", source="down",
        )
        result = json.loads(raw)
        assert result["status"] == "error"
        assert "not available" in result["error"]


class TestCommitMarketDataRetired:
    def test_commit_market_data_not_in_registry(self):
        from strategy_research.kernel.agent.builtin_tools import build_default_registry
        reg = build_default_registry()
        assert reg.get("commit_market_data") is None
        assert reg.get("get_market_data") is not None
