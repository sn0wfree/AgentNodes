"""Unit tests for A3 structured tool results + LLM views."""
from __future__ import annotations

import json

from agentnodes.kernel.agent.tool_result import (
    ToolResult,
    error,
    from_any,
    ok,
    render_for_llm,
    table_result,
    to_json,
    to_wire,
)


class TestFromAny:
    def test_toolresult_passthrough(self):
        r = ok(text="hi")
        assert from_any(r) is r

    def test_none_is_ok_empty(self):
        r = from_any(None)
        assert r.status == "ok" and r.text == ""

    def test_plain_string(self):
        r = from_any("hello")
        assert r.status == "ok" and r.text == "hello"

    def test_json_string_becomes_dict(self):
        r = from_any(json.dumps({"status": "ok", "result": {"a": 1}}))
        assert r.status == "ok"
        assert r.data == {"a": 1}

    def test_invalid_json_string_kept_as_text(self):
        r = from_any("{not json")
        assert r.text == "{not json"

    def test_dict_status_success_maps_ok(self):
        r = from_any({"status": "success", "result": 1})
        assert r.status == "ok"

    def test_dict_error(self):
        r = from_any({"status": "error", "error": "boom"})
        assert r.status == "error"
        assert "boom" in r.text

    def test_number(self):
        r = from_any(42)
        assert r.data == 42


class TestBuilders:
    def test_error(self):
        r = error("bad")
        assert r.status == "error" and "bad" in r.text

    def test_table_result_from_dicts(self):
        r = table_result([{"a": 1, "b": 2}, {"a": 3, "b": 4}], title="t")
        assert r.table is not None
        assert r.table["columns"] == ["a", "b"]
        assert len(r.table["rows"]) == 2
        assert r.meta["row_count"] == 2

    def test_table_result_from_lists(self):
        r = table_result([[1, 2], [3, 4]], columns=["x", "y"])
        assert r.table["columns"] == ["x", "y"]
        assert r.table["rows"] == [[1, 2], [3, 4]]

    def test_empty_table(self):
        r = table_result([], columns=["a"])
        assert r.table["rows"] == []


class TestToWire:
    def test_contains_data_and_views(self):
        r = ok(data={"n": 1}, text="t")
        w = to_wire(r)
        assert w["status"] == "ok"
        assert w["data"] == {"n": 1}
        assert w["views"]["text"] == "t"

    def test_to_json_roundtrip(self):
        r = ok(text="hello", tool="x")
        parsed = json.loads(to_json(r))
        assert parsed["views"]["text"] == "hello"
        assert parsed["meta"]["tool"] == "x"


class TestRenderForLlm:
    def test_short_error(self):
        s = render_for_llm(error("denied"), max_chars=1000)
        assert "error" in s.lower()
        assert "denied" in s

    def test_short_table_includes_header_and_rows(self):
        r = table_result([{"id": i, "v": i * 2} for i in range(3)], columns=["id", "v"])
        s = render_for_llm(r, max_chars=5000)
        assert "| id |" in s
        assert "| 0 |" in s

    def test_long_table_keeps_header_and_counts_rows(self):
        rows = [{"id": i, "payload": "x" * 20} for i in range(500)]
        r = table_result(rows, columns=["id", "payload"], text="big table")
        s = render_for_llm(r, max_chars=800)
        assert len(s) <= 900  # budget + small slack for footer
        assert "| id |" in s  # header survives
        assert "500 rows" in s or "total 500" in s

    def test_long_text_head_tail(self):
        body = "HEAD " + ("mid" * 5000) + " TAIL"
        s = render_for_llm(ok(text=body), max_chars=200)
        assert "HEAD" in s
        assert "TAIL" in s
        assert "truncated" in s
        assert len(s) <= 250

    def test_max_chars_zero_is_untruncated(self):
        body = "z" * 50_000
        s = render_for_llm(ok(text=body), max_chars=0)
        assert len(s) == 50_000

    def test_json_preview_when_no_text_table(self):
        s = render_for_llm(ok(data={"k": list(range(100))}), max_chars=5000)
        assert "k" in s


class TestToolResultDefaults:
    def test_ok_flag(self):
        assert ok().ok is True
        assert error("x").ok is False

    def test_status_default_ok(self):
        assert ToolResult().status == "ok"
