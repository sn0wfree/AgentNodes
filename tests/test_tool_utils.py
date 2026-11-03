"""Tests for the shared tool utilities (utils.py).

Covers:
- try_unwrap_list / try_unwrap_dict: explicit unwrap
- err_actionable: structured error payloads
- truncate: long values

Note: the legacy ``safe_get_param`` name-driven coercion was removed in
P1 (annotation-driven ``BaseTool._coerce_params`` is the single coercion
path now; todo_tools migrated to inline json.loads + try_unwrap_list).
"""
from __future__ import annotations

import json

from strategy_research.kernel.agent.builtin_tools.utils import (
    err_actionable,
    truncate,
    try_unwrap_dict,
    try_unwrap_list,
)

# ── try_unwrap_list / try_unwrap_dict ────────────────────────


class TestTryUnwrap:
    def test_unwrap_list_finds_item(self):
        assert try_unwrap_list({"item": [1, 2]}) == [1, 2]

    def test_unwrap_list_no_match_returns_none(self):
        assert try_unwrap_list({"foo": 1}) is None

    def test_unwrap_list_not_dict_returns_none(self):
        assert try_unwrap_list([1, 2]) is None
        assert try_unwrap_list("string") is None
        assert try_unwrap_list(None) is None

    def test_unwrap_dict_finds_item(self):
        assert try_unwrap_dict({"item": {"k": "v"}}) == {"k": "v"}

    def test_unwrap_dict_no_match_returns_none(self):
        assert try_unwrap_dict({"foo": 1}) is None

    def test_unwrap_dict_not_dict_returns_none(self):
        assert try_unwrap_dict([1, 2]) is None
        assert try_unwrap_dict("string") is None


# ── err_actionable ────────────────────────────────────────────


class TestErrActionable:
    def test_minimal(self):
        out = json.loads(err_actionable("oops"))
        assert out == {"status": "error", "error": "oops"}

    def test_with_received(self):
        out = json.loads(err_actionable("bad", received=[1, 2, 3]))
        assert out["received"] == [1, 2, 3]
        assert "status" not in out["received"]  # plain value, not a payload

    def test_with_expected(self):
        out = json.loads(err_actionable("bad", expected="list[str]"))
        assert out["expected"] == "list[str]"

    def test_with_fix(self):
        out = json.loads(err_actionable("bad", fix="call get_market_data first"))
        assert out["fix"] == "call get_market_data first"

    def test_with_tool(self):
        out = json.loads(err_actionable("bad", tool="import_data"))
        assert out["tool"] == "import_data"

    def test_with_extra(self):
        out = json.loads(err_actionable("bad", extra={"code": "600519.SH"}))
        assert out["code"] == "600519.SH"

    def test_full(self):
        out = json.loads(err_actionable(
            "bad shape",
            received={"item": [1, 2]},
            expected="list[record]",
            fix="call get_market_data then call import_data(data=<result>)",
            tool="import_data",
            extra={"code": "600519.SH"},
        ))
        assert out["status"] == "error"
        assert out["error"] == "bad shape"
        assert out["received"] == {"item": [1, 2]}
        assert out["expected"] == "list[record]"
        assert "get_market_data" in out["fix"]
        assert out["tool"] == "import_data"
        assert out["code"] == "600519.SH"


# ── truncate ─────────────────────────────────────────────────


class TestTruncate:
    def test_short_string_passthrough(self):
        assert truncate("hello") == "hello"

    def test_long_string_truncated(self):
        long = "x" * 300
        out = truncate(long, max_len=50)
        assert out.startswith("x" * 50)
        assert "truncated" in out
        assert "300" in out

    def test_dict_truncated_recursively(self):
        d = {"short": "a", "long": "x" * 300}
        out = truncate(d, max_len=50)
        assert out["short"] == "a"
        assert out["long"].startswith("x" * 50)

    def test_list_truncated(self):
        long_list = list(range(20))
        out = truncate(long_list, max_len=50)
        assert len(out) == 6  # 5 items + 1 "... (total 20 items)"

    def test_int_passthrough(self):
        assert truncate(42) == 42

    def test_none_passthrough(self):
        assert truncate(None) is None
