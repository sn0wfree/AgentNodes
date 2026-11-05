"""Tests for import_data defensive unwrap (Fix 1).

Covers:
- Backward compat: standard {code: [records]} shape still works
- Auto-unwrap: {"item": [...]}, {"data": [...]}, {"records": [...]}
- Actionable error when wrapper key is unknown
- Empty data still works
- Schema declares data[code] is an array
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from strategy_research.kernel.agent.builtin_tools.data_tools import ImportDataTool
from strategy_research.kernel.agent.tools import ToolContext
from strategy_research.apps.db import init_db

# ── Shared fixture ──────────────────────────────────────────────


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a fresh workspace with initialized DuckDB."""
    init_db(tmp_path)
    return tmp_path


def _records(code: str = "600519.SH", n: int = 2) -> list[dict]:
    return [
        {
            "trade_date": f"2023-12-{11 + i:02d}",
            "open": 100.0 + i,
            "close": 101.0 + i,
            "high": 102.0 + i,
            "low": 99.0 + i,
            "volume": 1000.0 + i,
        }
        for i in range(n)
    ]


def _execute(workspace: Path, data) -> dict:
    """Run import_data and return parsed result."""
    tool = ImportDataTool()
    return json.loads(tool.execute(ctx=ToolContext(workspace=workspace), data=data))


# ── 1. Backward compat — standard shape still works ─────────────


class TestImportDataStandardShape:
    def test_standard_list_shape(self, workspace: Path):
        """Standard shape: data[code] = [records] — ok."""
        result = _execute(workspace, {"600519.SH": _records()})
        assert result["status"] == "ok"
        assert result["imported"] == 2

    def test_backward_compat_empty_data(self, workspace: Path):
        """Empty list per code still returns ok (no rows imported)."""
        result = _execute(workspace, {"A": []})
        assert result["status"] == "ok"
        assert result["imported"] == 0

    def test_multiple_codes(self, workspace: Path):
        """Multiple codes, each with records."""
        data = {
            "600519.SH": _records("600519.SH", 3),
            "000001.SZ": _records("000001.SZ", 2),
        }
        result = _execute(workspace, data)
        assert result["status"] == "ok"
        assert result["imported"] == 5
        assert result["n_codes"] == 2


# ── 2. Defensive unwrap — LLM wraps list in single-key object ────


class TestImportDataDefensiveUnwrap:
    def test_unwrap_item_wrapper(self, workspace: Path):
        """LLM wraps in {"item": [...]} — should auto-unwrap."""
        data = {"600519.SH": {"item": _records()}}
        result = _execute(workspace, data)
        assert result["status"] == "ok", result
        assert result["imported"] == 2

    def test_unwrap_data_wrapper(self, workspace: Path):
        """LLM wraps in {"data": [...]} — should auto-unwrap."""
        data = {"600519.SH": {"data": _records()}}
        result = _execute(workspace, data)
        assert result["status"] == "ok", result
        assert result["imported"] == 2

    def test_unwrap_records_wrapper(self, workspace: Path):
        """LLM wraps in {"records": [...]} — should auto-unwrap."""
        data = {"600519.SH": {"records": _records()}}
        result = _execute(workspace, data)
        assert result["status"] == "ok", result
        assert result["imported"] == 2

    def test_unwrap_bars_wrapper(self, workspace: Path):
        """LLM wraps in {"bars": [...]} — should auto-unwrap."""
        data = {"600519.SH": {"bars": _records()}}
        result = _execute(workspace, data)
        assert result["status"] == "ok", result
        assert result["imported"] == 2

    def test_unwrap_rows_wrapper(self, workspace: Path):
        """LLM wraps in {"rows": [...]} — should auto-unwrap."""
        data = {"600519.SH": {"rows": _records()}}
        result = _execute(workspace, data)
        assert result["status"] == "ok", result
        assert result["imported"] == 2

    def test_unwrap_ohlcv_wrapper(self, workspace: Path):
        """LLM wraps in {"ohlcv": [...]} — should auto-unwrap."""
        data = {"600519.SH": {"ohlcv": _records()}}
        result = _execute(workspace, data)
        assert result["status"] == "ok", result
        assert result["imported"] == 2

    def test_unwrap_values_wrapper(self, workspace: Path):
        """LLM wraps in {"values": [...]} — should auto-unwrap."""
        data = {"600519.SH": {"values": _records()}}
        result = _execute(workspace, data)
        assert result["status"] == "ok", result
        assert result["imported"] == 2

    def test_unwrap_mixed_codes(self, workspace: Path):
        """Some codes wrapped, some not — all should work."""
        data = {
            "600519.SH": {"item": _records("600519.SH", 2)},
            "000001.SZ": _records("000001.SZ", 3),
        }
        result = _execute(workspace, data)
        assert result["status"] == "ok", result
        assert result["imported"] == 5


# ── 3. Actionable error — wrapper key unknown ───────────────────


class TestImportDataActionableError:
    def test_dict_no_known_key_clear_error(self, workspace: Path):
        """LLM wraps with wrong key — clear actionable error message."""
        data = {"600519.SH": {"foo": "...", "bar": "..."}}
        result = _execute(workspace, data)
        assert result["status"] == "error"
        # The error message itself mentions 600519.SH
        assert "600519.SH" in result["error"]
        # Structured fields guide recovery
        assert "expected" in result
        assert "fix" in result
        assert "received" in result
        # Should mention get_market_data as the fix
        assert "get_market_data" in result["fix"]

    def test_dict_no_known_key_lists_actual_keys(self, workspace: Path):
        """Error message's `received` field includes the actual keys received."""
        data = {"600519.SH": {"foo": "x", "bar": "y", "baz": "z"}}
        result = _execute(workspace, data)
        assert result["status"] == "error"
        # The 'received' field has the actual dict (with our keys)
        for k in ("foo", "bar", "baz"):
            assert k in str(result["received"]), f"missing key {k} in received"

    def test_real_session_failure_shape_caught(self, workspace: Path):
        """Reproduce the actual session 700dc7f7 failure shape.

        The real failure was:
            data[code] = {"item": [records]}

        This should now succeed (was failing before Fix 1).
        """
        data = {
            "600519.SH": {
                "item": [
                    {
                        "trade_date": "2023-12-11T00:00:00",
                        "open": 1536.555,
                        "close": 1544.555,
                        "high": 1550.555,
                        "low": 1503.555,
                        "volume": 36831.0,
                    }
                ]
            }
        }
        result = _execute(workspace, data)
        # Before fix: "no date column in data for 600519.SH"
        # After fix: status=ok
        assert result["status"] == "ok", result
        assert result["imported"] == 1


# ── 4. Schema enrichment ────────────────────────────────────────


class TestImportDataSchema:
    def test_schema_derived_from_signature(self):
        """v2: schema derives from the execute signature."""
        schema = ImportDataTool().to_openai_schema()["function"]["parameters"]
        props = schema["properties"]
        assert props["data"]["type"] == "object"
        assert props["strategy_name"]["type"] == "string"
        # 注入参数 (workspace) 已从 schema 剥离 (ToolContext)
        assert "workspace" not in props

    def test_required_from_signature(self):
        """必填参数由签名无默认值参数派生。"""
        required = ImportDataTool().to_openai_schema()["function"]["parameters"]["required"]
        assert "data" in required
        assert "strategy_name" not in required
