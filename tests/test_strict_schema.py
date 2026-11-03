"""Tests for OpenAI strict-mode schema generation.

Covers:
- make_strict_schema: recursive additionalProperties: false + required
- BaseTool.to_openai_schema: emits strict flag when strict=True
- Tool selection: only simple-shape tools opt in
"""
from __future__ import annotations

from strategy_research.kernel.agent.builtin_tools import build_default_registry
from strategy_research.kernel.agent.tools import BaseTool, make_strict_schema

# ── make_strict_schema: pure schema rewriting ────────────────


class TestMakeStrictSchema:
    def test_adds_additional_properties_false(self):
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        out = make_strict_schema(schema)
        assert out["additionalProperties"] is False

    def test_adds_required_with_all_props(self):
        schema = {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "integer"}}}
        out = make_strict_schema(schema)
        assert set(out["required"]) == {"a", "b"}

    def test_preserves_existing_required(self):
        schema = {
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
            "required": ["a"],
        }
        out = make_strict_schema(schema)
        assert "a" in out["required"]
        # Will add b since strict mode requires all props
        assert "b" in out["required"]

    def test_walks_nested_objects(self):
        schema = {
            "type": "object",
            "properties": {
                "outer": {
                    "type": "object",
                    "properties": {"inner": {"type": "string"}},
                },
            },
        }
        out = make_strict_schema(schema)
        assert out["additionalProperties"] is False
        assert out["properties"]["outer"]["additionalProperties"] is False
        assert "inner" in out["properties"]["outer"]["required"]

    def test_walks_array_items(self):
        schema = {
            "type": "object",
            "properties": {
                "list": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"k": {"type": "string"}},
                    },
                },
            },
        }
        out = make_strict_schema(schema)
        assert out["properties"]["list"]["items"]["additionalProperties"] is False
        assert "k" in out["properties"]["list"]["items"]["required"]

    def test_dict_shape_becomes_strict_no_extras(self):
        """Dict-shape parameters become strict-no-extras in strict mode.

        The original schema allows arbitrary keys with array values.
        Strict mode can't express that — it forces no-extras. The tool
        author must use an explicit schema or list shape if strict mode
        is desired.
        """
        schema = {
            "type": "object",
            "properties": {
                "data": {
                    "type": "object",
                    "additionalProperties": {"type": "array", "items": {"type": "object"}},
                },
            },
        }
        out = make_strict_schema(schema)
        # In strict mode, dict-shape gets converted to no-extras
        assert out["properties"]["data"]["additionalProperties"] is False

    def test_does_not_mutate_input(self):
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        original = {**schema, "properties": {**schema["properties"]}}
        make_strict_schema(schema)
        # Original unchanged
        assert schema == original
        assert "additionalProperties" not in schema

    def test_handles_empty_properties(self):
        schema = {"type": "object", "properties": {}}
        out = make_strict_schema(schema)
        assert out["additionalProperties"] is False
        # No required to add
        assert out.get("required", []) == []

    def test_non_object_nodes_unchanged(self):
        schema = {"type": "string"}
        out = make_strict_schema(schema)
        assert out == {"type": "string"}


# ── to_openai_schema: emits strict flag ──────────────────────


class StrictTool(BaseTool):
    name = "strict_test"
    description = "Tool for testing strict schema."
    parameters = {
        "type": "object",
        "properties": {
            "a": {"type": "string"},
            "b": {"type": "integer"},
        },
    }
    strict = True

    def execute(self, **kwargs):
        return "{}"


class NonStrictTool(BaseTool):
    name = "non_strict_test"
    description = "Tool without strict."
    parameters = {
        "type": "object",
        "properties": {
            "a": {"type": "string"},
        },
    }

    def execute(self, **kwargs):
        return "{}"


class TestToOpenAISchema:
    def test_strict_tool_has_strict_flag(self):
        tool = StrictTool()
        schema = tool.to_openai_schema()
        assert schema["type"] == "function"
        assert schema["function"]["strict"] is True
        # Properties were rewritten
        params = schema["function"]["parameters"]
        assert params["additionalProperties"] is False
        assert set(params["required"]) == {"a", "b"}

    def test_non_strict_tool_no_strict_flag(self):
        tool = NonStrictTool()
        schema = tool.to_openai_schema()
        assert "strict" not in schema["function"]
        # additionalProperties NOT added (only on strict)
        params = schema["function"]["parameters"]
        assert "additionalProperties" not in params


# ── Real tools: candidates for strict ────────────────────────


class TestRealToolsStrictCompat:
    """Verify which real tools can safely opt into strict mode."""

    def test_options_pricing_is_strict(self):
        """OptionsPricingTool has 6 numeric/string required params → strict OK."""
        registry = build_default_registry()
        tool = registry.get("options_pricing")
        assert tool is not None
        assert tool.strict is True
        # And the schema validates
        schema = tool.to_openai_schema()
        assert schema["function"]["strict"] is True
        params = schema["function"]["parameters"]
        assert params["additionalProperties"] is False
        assert set(params["required"]) == {
            "spot", "strike", "rate", "volatility", "time_to_expiry", "option_type",
        }

    def test_import_data_not_strict_due_to_dict_shape(self):
        """import_data has dict-shape `data` field → cannot be strict."""
        registry = build_default_registry()
        tool = registry.get("import_data")
        assert tool is not None
        assert tool.strict is False
        # Even if forced to strict, the dict-shape would be dropped
        # (we don't enable strict; this documents why)

    def test_tools_with_optional_fields_not_strict(self):
        """Tools with optional params aren't strict (would force all to required)."""
        registry = build_default_registry()
        # read_file has optional limit/offset
        tool = registry.get("read")
        assert tool is not None
        assert tool.strict is False
