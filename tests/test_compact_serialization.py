"""Tests for _serialize_message — edge cases and all message types."""
from __future__ import annotations

from strategy_research.kernel.agent.compact import (
    TOOL_OUTPUT_MAX_CHARS,
    _serialize_message,
)


class TestSerializeUserMessage:
    def test_basic(self):
        assert _serialize_message({"role": "user", "content": "hello"}) == "[User]: hello"

    def test_empty_content(self):
        assert _serialize_message({"role": "user", "content": ""}) == "[User]: "

    def test_missing_content(self):
        assert _serialize_message({"role": "user"}) == "[User]: "

    def test_multiline(self):
        msg = {"role": "user", "content": "line1\nline2\nline3"}
        result = _serialize_message(msg)
        assert result == "[User]: line1\nline2\nline3"

    def test_special_chars(self):
        msg = {"role": "user", "content": '<tag> & "quotes"'}
        result = _serialize_message(msg)
        assert '<tag> & "quotes"' in result


class TestSerializeAssistantMessage:
    def test_basic_text(self):
        msg = {"role": "assistant", "content": "I can help"}
        result = _serialize_message(msg)
        assert "[Assistant]: I can help" in result

    def test_empty_content_no_tool_calls(self):
        msg = {"role": "assistant", "content": ""}
        result = _serialize_message(msg)
        assert result == ""

    def test_missing_content(self):
        msg = {"role": "assistant"}
        result = _serialize_message(msg)
        assert result == ""


class TestSerializeThinkBlocks:
    def test_single_think_block(self):
        msg = {"role": "assistant", "content": "<think>thinking</think>actual response"}
        result = _serialize_message(msg)
        assert "[Assistant reasoning]: thinking" in result
        assert "[Assistant]: actual response" in result

    def test_multiple_think_blocks(self):
        msg = {"role": "assistant", "content": "<think>thought1</think>mid</think>thought2</think>final"}
        result = _serialize_message(msg)
        assert "[Assistant reasoning]: thought1" in result
        # The regex finds first think block non-greedily; remaining text
        # after removal includes the second think block verbatim
        assert "thought2" in result
        assert "[Assistant]: mid" in result

    def test_think_only(self):
        """Only think blocks, no visible content."""
        msg = {"role": "assistant", "content": "<think>internal reasoning</think>"}
        result = _serialize_message(msg)
        assert "[Assistant reasoning]: internal reasoning" in result
        # No [Assistant] line since clean_content is empty
        assert "[Assistant]:" not in result

    def test_think_with_empty_body(self):
        msg = {"role": "assistant", "content": "<think></think>actual"}
        result = _serialize_message(msg)
        # Empty think body should not produce a reasoning line
        assert "[Assistant reasoning]" not in result
        assert "[Assistant]: actual" in result

    def test_think_block_case_insensitive(self):
        msg = {"role": "assistant", "content": "<think>THINKING</think>response"}
        result = _serialize_message(msg)
        assert "[Assistant reasoning]: THINKING" in result

    def test_mixed_think_and_no_think(self):
        """Mix of messages: some with think, some without."""
        msg1 = {"role": "assistant", "content": "<think>plan</think>"}
        msg2 = {"role": "assistant", "content": "no think block"}
        r1 = _serialize_message(msg1)
        r2 = _serialize_message(msg2)
        assert "[Assistant reasoning]: plan" in r1
        assert "[Assistant]: no think block" in r2


class TestSerializeToolCalls:
    def test_single_tool_call(self):
        msg = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "read", "arguments": '"/tmp/test.py"'}},
            ],
        }
        result = _serialize_message(msg)
        assert "[Assistant tool call]: read(" in result
        assert '"/tmp/test.py"' in result

    def test_multiple_tool_calls(self):
        msg = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "read", "arguments": '"/a.py"'}},
                {"id": "c2", "type": "function", "function": {"name": "search_code", "arguments": '"query"'}},
            ],
        }
        result = _serialize_message(msg)
        assert "[Assistant tool call]: read(" in result
        assert "[Assistant tool call]: search_code(" in result

    def test_tool_call_long_args_truncated(self):
        long_args = "x" * 200
        msg = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "shell", "arguments": long_args}},
            ],
        }
        result = _serialize_message(msg)
        assert "..." in result

    def test_tool_call_non_string_args(self):
        msg = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "read", "arguments": 42}},
            ],
        }
        result = _serialize_message(msg)
        assert "[Assistant tool call]: read(" in result

    def test_tool_call_with_content(self):
        """Tool calls AND text content."""
        msg = {
            "role": "assistant",
            "content": "Let me read the file",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "read", "arguments": '"/a.py"'}},
            ],
        }
        result = _serialize_message(msg)
        assert "[Assistant]: Let me read the file" in result
        assert "[Assistant tool call]: read(" in result

    def test_empty_tool_calls(self):
        msg = {"role": "assistant", "content": "text", "tool_calls": []}
        result = _serialize_message(msg)
        assert "[Assistant]: text" in result

    def test_tool_call_no_id(self):
        """Tool call without id — should still serialize."""
        msg = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"type": "function", "function": {"name": "read", "arguments": '"/a.py"'}},
            ],
        }
        result = _serialize_message(msg)
        assert "[Assistant tool call]: read(" in result


class TestSerializeToolResult:
    def test_basic(self):
        msg = {"role": "tool", "content": "file contents here"}
        result = _serialize_message(msg)
        assert result == "[Tool result]: file contents here"

    def test_empty(self):
        msg = {"role": "tool", "content": ""}
        result = _serialize_message(msg)
        assert result == "[Tool result]: "

    def test_long_truncated(self):
        long_content = "x" * (TOOL_OUTPUT_MAX_CHARS + 1000)
        msg = {"role": "tool", "content": long_content}
        result = _serialize_message(msg)
        assert "[truncated]" in result
        assert len(result) < len(long_content)

    def test_error_detected(self):
        import json
        error_content = json.dumps({"status": "error", "message": "file not found"})
        msg = {"role": "tool", "content": error_content}
        result = _serialize_message(msg)
        assert "[Tool error]:" in result
        assert "file not found" in result

    def test_error_with_error_key(self):
        import json
        error_content = json.dumps({"status": "error", "error": "timeout"})
        msg = {"role": "tool", "content": error_content}
        result = _serialize_message(msg)
        assert "[Tool error]:" in result
        assert "timeout" in result

    def test_json_not_error(self):
        """JSON response without status:error is treated as normal tool result."""
        import json
        content = json.dumps({"status": "ok", "data": [1, 2, 3]})
        msg = {"role": "tool", "content": content}
        result = _serialize_message(msg)
        assert "[Tool result]:" in result

    def test_non_json_with_error_word(self):
        """Non-JSON content containing 'error' — not treated as error."""
        msg = {"role": "tool", "content": "Error in the output above"}
        result = _serialize_message(msg)
        assert "[Tool result]:" in result


class TestSerializeSystemMessage:
    def test_basic(self):
        msg = {"role": "system", "content": "system prompt"}
        result = _serialize_message(msg)
        assert result == "[System update]: system prompt"

    def test_empty(self):
        msg = {"role": "system", "content": ""}
        result = _serialize_message(msg)
        assert result == "[System update]: "


class TestSerializeUnknownRole:
    def test_unknown_role(self):
        msg = {"role": "custom", "content": "data"}
        result = _serialize_message(msg)
        assert result == ""

    def test_missing_role(self):
        msg = {"content": "data"}
        result = _serialize_message(msg)
        assert result == ""
