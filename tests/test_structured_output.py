"""Tests for StructuredOutputParser (Phase 9: 4-layer degradation)."""
from __future__ import annotations

from strategy_research.foundation.structured_output import StructuredOutputParser
from strategy_research.foundation.llm.parser import parse_tool_arguments

# ── StructuredOutputParser.parse() ─────────────────────────────────


class TestStructuredOutputParser:
    """Test 4-layer degradation in StructuredOutputParser."""

    def setup_method(self):
        self.parser = StructuredOutputParser()

    # Layer 1: Strict JSON

    def test_strict_json(self):
        result = self.parser.parse('{"name": "test", "count": 5}')
        assert result.data == {"name": "test", "count": 5}
        assert result.source == "strict"
        assert result.errors == []

    def test_strict_json_non_dict(self):
        result = self.parser.parse('[1, 2, 3]')
        assert result.data == {"value": [1, 2, 3]}
        assert result.source == "strict"

    def test_strict_json_already_dict(self):
        result = self.parser.parse({"key": "value"})
        assert result.data == {"key": "value"}
        assert result.source == "strict"

    # Layer 2: Repair

    def test_repair_trailing_comma(self):
        result = self.parser.parse('{"name": "test", "count": 5,}')
        assert result.data == {"name": "test", "count": 5}
        assert result.source == "repaired"

    def test_repair_trailing_comma_in_list(self):
        result = self.parser.parse('{"items": [1, 2, 3,]}')
        assert result.data == {"items": [1, 2, 3]}
        assert result.source == "repaired"

    def test_repair_single_quotes(self):
        result = self.parser.parse("{'name': 'test', 'count': 5}")
        assert result.data == {"name": "test", "count": 5}
        assert result.source == "repaired"

    def test_repair_mixed_quotes(self):
        result = self.parser.parse("{'name': \"test\", 'count': 5}")
        assert result.data == {"name": "test", "count": 5}
        assert result.source == "repaired"

    def test_repair_markdown_fence(self):
        result = self.parser.parse('```json\n{"name": "test"}\n```')
        assert result.data == {"name": "test"}
        assert result.source == "strict"  # fence removed by Layer 1

    # Layer 3: Regex extraction

    def test_regex_string_field(self):
        schema = {"name": "string", "count": "number"}
        result = self.parser.parse(
            '{"name": "hello world", "count": 42}',
            schema=schema,
        )
        assert result.data == {"name": "hello world", "count": 42}
        assert result.source == "strict"  # Layer 1 succeeds first

    def test_regex_fallback(self):
        schema = {"name": "string"}
        # Invalid JSON but has extractable fields
        result = self.parser.parse(
            'name: "test_value"',
            schema=schema,
        )
        assert result.data is not None
        assert result.source == "regex"

    def test_regex_number_field(self):
        schema = {"count": "number"}
        result = self.parser.parse('count: 42', schema=schema)
        assert result.data == {"count": 42}
        assert result.source == "regex"

    def test_regex_boolean_field(self):
        schema = {"active": "boolean"}
        result = self.parser.parse('active: true', schema=schema)
        assert result.data == {"active": True}
        assert result.source == "regex"

    # Layer 4: Failure

    def test_empty_input(self):
        result = self.parser.parse("")
        assert result.data is None
        assert result.source == "failed"
        assert len(result.errors) > 0

    def test_non_string_input(self):
        result = self.parser.parse(123)
        assert result.data is None
        assert result.source == "failed"

    def test_none_input(self):
        result = self.parser.parse(None)
        assert result.data is None
        assert result.source == "failed"

    def test_garbage_input(self):
        result = self.parser.parse("not json at all {{{}}")
        assert result.data is None
        assert result.source == "failed"

    # Edge cases

    def test_whitespace_only(self):
        result = self.parser.parse("   ")
        assert result.data is None
        assert result.source == "failed"

    def test_nested_json(self):
        result = self.parser.parse('{"a": {"b": 1}, "c": [1, 2]}')
        assert result.data == {"a": {"b": 1}, "c": [1, 2]}
        assert result.source == "strict"


# ── parse_tool_arguments wrapper ────────────────────────────────────


class TestParseToolArguments:
    """Test parse_tool_arguments wrapper with 4-layer degradation."""

    def test_strict_json(self):
        result = parse_tool_arguments('{"name": "test"}')
        assert result == {"name": "test"}

    def test_empty_returns_dict(self):
        result = parse_tool_arguments("")
        assert result == {}

    def test_none_returns_dict(self):
        result = parse_tool_arguments(None)
        assert result == {}

    def test_with_schema(self):
        result = parse_tool_arguments(
            '{"name": "hello", "count": 42}',
            schema={"name": "string", "count": "number"},
        )
        assert result == {"name": "hello", "count": 42}

    def test_regression_single_quotes(self):
        """Regression: existing code passes single-quoted args."""
        result = parse_tool_arguments("{'key': 'value'}")
        assert result == {"key": "value"}

    def test_regression_markdown_fence(self):
        """Regression: existing code passes markdown-fenced JSON."""
        result = parse_tool_arguments('```json\n{"key": "value"}\n```')
        assert result == {"key": "value"}
