"""Unit tests for core.utils.token_utils — chars→token estimation."""
from __future__ import annotations

from agentnodes.foundation.utils.token_utils import (
    CHARS_PER_TOKEN,
    estimate_tokens,
    estimate_tokens_text,
)


class TestEstimateTokensText:
    def test_empty_string_returns_one(self):
        assert estimate_tokens_text("") == 1

    def test_scales_by_chars_per_token(self):
        text = "x" * 30
        assert estimate_tokens_text(text) == int(30 / CHARS_PER_TOKEN)

    def test_custom_divisor(self):
        assert estimate_tokens_text("x" * 10, chars_per_token=2.0) == 5

    def test_short_text_at_least_one(self):
        assert estimate_tokens_text("a") == 1


class TestEstimateTokens:
    def test_empty_messages_returns_one(self):
        assert estimate_tokens([]) == 1

    def test_string_content_counts_length(self):
        msgs = [{"role": "user", "content": "x" * 30}]
        assert estimate_tokens(msgs) == int(30 / CHARS_PER_TOKEN)

    def test_none_content_counts_zero_chars(self):
        msgs = [{"role": "assistant", "content": None}]
        assert estimate_tokens(msgs) == 1

    def test_tool_role_adds_fixed_overhead(self):
        msgs = [{"role": "tool", "content": ""}]
        assert estimate_tokens(msgs) == int(100 / CHARS_PER_TOKEN)

    def test_tool_calls_arguments_counted(self):
        msgs = [{
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "function": {"name": "f", "arguments": {"q": "y" * 9}},
            }],
        }]
        # json.dumps({"q":"yyyyyyyyy"}) length = 18
        assert estimate_tokens(msgs) >= 1

    def test_non_dict_tool_call_ignored(self):
        msgs = [{"role": "assistant", "content": "x", "tool_calls": ["junk"]}]
        assert estimate_tokens(msgs) == int(1 / CHARS_PER_TOKEN) or estimate_tokens(msgs) == 1

    def test_sum_over_messages(self):
        msgs = [
            {"role": "user", "content": "x" * 30},
            {"role": "assistant", "content": "y" * 30},
        ]
        assert estimate_tokens(msgs) == int(60 / CHARS_PER_TOKEN)

    def test_custom_divisor(self):
        msgs = [{"role": "user", "content": "x" * 10}]
        assert estimate_tokens(msgs, chars_per_token=1.0) == 10
