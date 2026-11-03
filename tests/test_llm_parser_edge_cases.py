"""Edge-case tests for parser.py — covers gaps not in test_llm_client.py."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.foundation.llm.errors import LLMMalformedResponseError
from strategy_research.foundation.llm.parser import (
    LLMResponse,
    ToolCall,
    _chunk_from_dict,
    parse_chat_response,
    parse_stream_chunk,
    parse_tool_arguments,
)
from strategy_research.foundation.llm.provider.deepseek import DeepSeekAdapter

# ── Helpers ──────────────────────────────────────────────────────────


def _make_choice(overrides: dict | None = None) -> dict:
    base = {"message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}
    if overrides:
        base.update(overrides)
    return {"choices": [base], "usage": {"total_tokens": 10}}


class TestParseChatResponseEdgeCases(unittest.TestCase):
    """Edge cases for parse_chat_response."""

    def test_choice_zero_not_dict(self) -> None:
        with self.assertRaises(LLMMalformedResponseError):
            parse_chat_response({"choices": ["not a dict"]})

    def test_message_not_dict(self) -> None:
        with self.assertRaises(LLMMalformedResponseError):
            parse_chat_response({"choices": [{"message": "string"}]})

    def test_content_none_becomes_empty(self) -> None:
        raw = {"choices": [{"message": {"role": "assistant", "content": None}, "finish_reason": "stop"}]}
        r = parse_chat_response(raw)
        self.assertEqual(r.content, "")

    def test_finish_reason_length(self) -> None:
        raw = {"choices": [{"message": {"role": "assistant", "content": "x"}, "finish_reason": "length"}]}
        r = parse_chat_response(raw)
        self.assertEqual(r.finish_reason, "length")

    def test_finish_reason_content_filter(self) -> None:
        raw = {"choices": [{"message": {"role": "assistant", "content": ""}, "finish_reason": "content_filter"}]}
        r = parse_chat_response(raw)
        self.assertEqual(r.finish_reason, "content_filter")

    def test_finish_reason_none_falls_to_stop(self) -> None:
        raw = {"choices": [{"message": {"role": "assistant", "content": "x"}}]}
        r = parse_chat_response(raw)
        self.assertEqual(r.finish_reason, "stop")

    def test_usage_non_int_values(self) -> None:
        raw = {"choices": [{"message": {"role": "assistant", "content": "x"}, "finish_reason": "stop"}], "usage": {"total_tokens": 10.5, "cost": "high"}}
        r = parse_chat_response(raw)
        self.assertEqual(r.usage["total_tokens"], 10)
        self.assertNotIn("cost", r.usage)

    def test_usage_not_dict(self) -> None:
        raw = {"choices": [{"message": {"role": "assistant", "content": "x"}, "finish_reason": "stop"}], "usage": "not a dict"}
        r = parse_chat_response(raw)
        self.assertEqual(r.usage, {})

    def test_raw_preserved(self) -> None:
        raw = {"choices": [{"message": {"role": "assistant", "content": "x"}, "finish_reason": "stop"}], "id": "chatcmpl-abc"}
        r = parse_chat_response(raw)
        self.assertEqual(r.raw["id"], "chatcmpl-abc")

    def test_tool_call_missing_id(self) -> None:
        raw = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{"function": {"name": "f", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}]}
        r = parse_chat_response(raw)
        self.assertEqual(len(r.tool_calls), 1)
        self.assertEqual(r.tool_calls[0].id, "")

    def test_tool_call_missing_function(self) -> None:
        raw = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]}, "finish_reason": "tool_calls"}]}
        r = parse_chat_response(raw)
        self.assertEqual(len(r.tool_calls), 1)
        self.assertEqual(r.tool_calls[0].name, "")
        self.assertEqual(r.tool_calls[0].arguments, {})

    def test_tool_call_function_not_dict(self) -> None:
        raw = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "function": "string"}]}, "finish_reason": "tool_calls"}]}
        r = parse_chat_response(raw)
        self.assertEqual(len(r.tool_calls), 0)

    def test_tool_call_tc_not_dict(self) -> None:
        raw = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": ["string"]}, "finish_reason": "tool_calls"}]}
        r = parse_chat_response(raw)
        self.assertEqual(len(r.tool_calls), 0)

    def test_reasoning_content_passthrough(self) -> None:
        raw = {"choices": [{"message": {"role": "assistant", "content": "answer", "reasoning_content": "thinking..."}, "finish_reason": "stop"}]}
        r = parse_chat_response(raw, adapter=DeepSeekAdapter())
        self.assertEqual(r.reasoning_content, "thinking...")


class TestToolCallDataClass(unittest.TestCase):
    """ToolCall dataclass to_dict."""

    def test_to_dict(self) -> None:
        tc = ToolCall(id="c1", name="read", arguments={"path": "/foo"})
        d = tc.to_dict()
        self.assertEqual(d["id"], "c1")
        self.assertEqual(d["name"], "read")
        self.assertEqual(d["arguments"], {"path": "/foo"})


class TestLLMResponseDataClass(unittest.TestCase):
    """LLMResponse dataclass methods."""

    def test_to_dict(self) -> None:
        r = LLMResponse(content="hi", reasoning_content="think", tool_calls=[ToolCall(id="c1", name="f", arguments={"x": 1})], finish_reason="tool_calls", usage={"total_tokens": 10})
        d = r.to_dict()
        self.assertEqual(d["content"], "hi")
        self.assertEqual(d["reasoning_content"], "think")
        self.assertEqual(d["finish_reason"], "tool_calls")
        self.assertEqual(d["usage"], {"total_tokens": 10})
        self.assertEqual(len(d["tool_calls"]), 1)

    def test_has_tool_calls_true(self) -> None:
        r = LLMResponse(tool_calls=[ToolCall(id="c1", name="f", arguments={})])
        self.assertTrue(r.has_tool_calls())

    def test_has_tool_calls_false(self) -> None:
        r = LLMResponse(content="hi")
        self.assertFalse(r.has_tool_calls())


class TestParseStreamChunkEdgeCases(unittest.TestCase):
    """Edge cases for parse_stream_chunk."""

    def test_data_empty_after_prefix(self) -> None:
        ch = parse_stream_chunk("data: ")
        self.assertIsNone(ch)

    def test_data_with_whitespace_only(self) -> None:
        ch = parse_stream_chunk("data:   ")
        self.assertIsNone(ch)

    def test_finish_reason_in_data(self) -> None:
        ch = parse_stream_chunk("data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}))
        self.assertIsNotNone(ch)
        self.assertEqual(ch.finish_reason, "stop")

    def test_finish_reason_length_in_data(self) -> None:
        ch = parse_stream_chunk("data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "length"}]}))
        self.assertEqual(ch.finish_reason, "length")


class TestChunkFromDict(unittest.TestCase):
    """Direct tests for _chunk_from_dict."""

    def test_not_dict_returns_none(self) -> None:
        self.assertIsNone(_chunk_from_dict("string"))

    def test_no_choices_returns_usage_only(self) -> None:
        ch = _chunk_from_dict({"usage": {"total_tokens": 5}})
        self.assertIsNotNone(ch)
        self.assertEqual(ch.usage, {"total_tokens": 5})
        self.assertEqual(ch.delta_content, "")

    def test_no_choices_no_usage_returns_empty(self) -> None:
        ch = _chunk_from_dict({"foo": "bar"})
        self.assertIsNotNone(ch)
        self.assertIsNone(ch.usage)

    def test_choices_first_not_dict_returns_none(self) -> None:
        ch = _chunk_from_dict({"choices": ["string"]})
        self.assertIsNone(ch)

    def test_delta_tool_calls_list(self) -> None:
        ch = _chunk_from_dict({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "f", "arguments": "{}"}}]}}]})
        self.assertIsNotNone(ch)
        self.assertEqual(len(ch.delta_tool_calls), 1)
        self.assertEqual(ch.delta_tool_calls[0]["id"], "c1")

    def test_delta_tool_calls_not_list(self) -> None:
        ch = _chunk_from_dict({"choices": [{"delta": {"tool_calls": "notlist"}}]})
        self.assertEqual(len(ch.delta_tool_calls), 0)

    def test_delta_tool_calls_none(self) -> None:
        ch = _chunk_from_dict({"choices": [{"delta": {"content": "hi"}}]})
        self.assertEqual(len(ch.delta_tool_calls), 0)

    def test_usage_non_int_values(self) -> None:
        ch = _chunk_from_dict({"usage": {"total_tokens": 10.5, "cost": "high"}, "choices": []})
        self.assertEqual(ch.usage, {"total_tokens": 10})

    def test_usage_not_dict(self) -> None:
        ch = _chunk_from_dict({"usage": "string", "choices": []})
        self.assertIsNone(ch.usage)

    def test_delta_content_none(self) -> None:
        ch = _chunk_from_dict({"choices": [{"delta": {"content": None}}]})
        self.assertEqual(ch.delta_content, "")

    def test_delta_thinking_via_provider(self) -> None:
        ch = _chunk_from_dict({"choices": [{"delta": {"content": "answer", "reasoning_content": "think"}}]}, adapter=DeepSeekAdapter())
        self.assertEqual(ch.delta_content, "answer")
        self.assertEqual(ch.delta_thinking, "think")


class TestParseToolArgumentsEdgeCases(unittest.TestCase):
    """Edge cases for parse_tool_arguments not covered in test_llm_client."""

    def test_nested_braces(self) -> None:
        result = parse_tool_arguments('{"a": {"b": 2}}')
        self.assertEqual(result, {"a": {"b": 2}})

    def test_fenced_with_extra_text_after(self) -> None:
        result = parse_tool_arguments('```json\n{"a": 1}\n``` and some text')
        self.assertEqual(result, {"a": 1})

    def test_stage3_only_braces(self) -> None:
        result = parse_tool_arguments('text before {"a": 1} text after')
        self.assertEqual(result, {"a": 1})

    def test_stage3_no_closing_brace(self) -> None:
        result = parse_tool_arguments('text before {"a": 1')
        self.assertEqual(result, {})

    def test_stage3_closing_before_opening(self) -> None:
        result = parse_tool_arguments("}text before{")
        self.assertEqual(result, {})

    def test_stage3_empty_braces(self) -> None:
        result = parse_tool_arguments("{")
        self.assertEqual(result, {})

    def test_all_stages_fail_logs_and_returns_empty(self) -> None:
        result = parse_tool_arguments('<not json at all>```\n{"bad": json}```')
        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()
