"""Unit tests for the DAG engine helper methods on AutoresearchRunner.

Covers:
- _try_parse_json: JSON parsing + fallback for non-string / non-JSON
- _build_round_task_text: section composition

(_rebuild_phase_outputs was retired with the langgraph branch in P5-2;
its suite was removed alongside the method.)

These are pure functions / pure data transforms — exercised via
``AutoresearchRunner.__new__`` to bypass the heavyweight __init__.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from strategy_research.apps.study.runner import AutoresearchRunner


def _bare_runner() -> AutoresearchRunner:
    """Build a runner without running __init__ (no store, no scheduler)."""
    return AutoresearchRunner.__new__(AutoresearchRunner)


# ── _try_parse_json ───────────────────────────────────────────────────


class TestTryParseJson:
    def test_parses_valid_json_object(self):
        runner = _bare_runner()
        assert runner._try_parse_json('{"a": 1}') == {"a": 1}

    def test_parses_valid_json_array(self):
        runner = _bare_runner()
        assert runner._try_parse_json("[1, 2, 3]") == [1, 2, 3]

    def test_parses_json_string(self):
        runner = _bare_runner()
        assert runner._try_parse_json('"hello"') == "hello"

    def test_parses_json_number(self):
        runner = _bare_runner()
        assert runner._try_parse_json("42") == 42

    def test_invalid_json_returns_raw(self):
        runner = _bare_runner()
        assert runner._try_parse_json("not json") == "not json"

    def test_empty_string_returns_empty(self):
        runner = _bare_runner()
        assert runner._try_parse_json("") == ""

    def test_non_string_returns_unchanged(self):
        runner = _bare_runner()
        # Non-string input (e.g. dict already parsed) returns as-is.
        assert runner._try_parse_json({"a": 1}) == {"a": 1}
        assert runner._try_parse_json(None) is None
        assert runner._try_parse_json(42) == 42


# ── _build_round_task_text ────────────────────────────────────────────


class TestBuildRoundTaskText:
    def test_no_state_no_directive_only_intro(self):
        runner = _bare_runner()
        out = runner._build_round_task_text({}, None)
        assert out == "根据研究目标与历史结果完成当前轮次的工作。"

    def test_with_state_includes_current_state_section(self):
        runner = _bare_runner()
        state = {"strategy_name": "foo", "round": 3}
        out = runner._build_round_task_text(state, None)
        assert "## 当前状态" in out
        assert '"strategy_name": "foo"' in out

    def test_with_directive_includes_directive_section(self):
        runner = _bare_runner()
        out = runner._build_round_task_text({}, "请优化因子权重")
        assert "## 用户指令" in out
        assert "请优化因子权重" in out

    def test_full_sections_appear_in_order(self):
        runner = _bare_runner()
        out = runner._build_round_task_text(
            {"k": "v"},
            "请继续",
        )
        intro = out.index("根据研究目标")
        state = out.index("## 当前状态")
        directive = out.index("## 用户指令")
        assert intro < state < directive

    def test_non_serializable_state_uses_default_str(self):
        runner = _bare_runner()
        state = {"data": object()}
        # Should not raise — json.dumps with default=str.
        out = runner._build_round_task_text(state, None)
        assert "## 当前状态" in out
