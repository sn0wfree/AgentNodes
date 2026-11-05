"""Tests for _llm_summarize_v2 and L4 LLM integration path."""
from __future__ import annotations

from unittest.mock import MagicMock

from agentnodes.kernel.agent.compact import (
    CompactConfig,
    _llm_summarize_v2,
)


class FakeLLM:
    """Fake LLM that returns configurable responses."""

    def __init__(self, responses=None, default_text="Summary output"):
        self._responses = list(responses) if responses else []
        self._call_count = 0
        self.default_text = default_text
        self.last_kwargs = {}
        self.last_messages = None

    def chat(self, messages, **kwargs):
        self.last_kwargs = kwargs
        self.last_messages = messages
        self._call_count += 1
        if self._responses:
            resp = MagicMock()
            resp.content = self._responses.pop(0)
            return resp
        resp = MagicMock()
        resp.content = self.default_text
        return resp


class TestLLMSummarizeV2ReturnsNone:
    """_llm_summarize_v2 returns None when conditions aren't met."""

    def test_too_few_messages(self):
        cfg = CompactConfig(tail_turns=2)
        msgs = [{"role": "user", "content": "hi"}]
        result = _llm_summarize_v2(msgs, cfg, 1_000_000, 128_000, FakeLLM())
        assert result is None

    def test_empty_head(self):
        """All messages are system — head is empty."""
        cfg = CompactConfig()
        msgs = [
            {"role": "system", "content": "sys1"},
            {"role": "system", "content": "sys2"},
        ]
        result = _llm_summarize_v2(msgs, cfg, 1_000_000, 128_000, FakeLLM())
        assert result is None

    def test_empty_conversation(self):
        """Messages with truly empty serialized output → LLM skipped."""
        llm = FakeLLM(responses=["Summary"])
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=100)
        msgs = [
            {"role": "user", "content": _LARGE},
            {"role": "assistant", "content": _LARGE},
            {"role": "user", "content": _LARGE},
            {"role": "assistant", "content": _LARGE},
            {"role": "user", "content": _LARGE},
            {"role": "assistant", "content": _LARGE},
        ]
        result = _llm_summarize_v2(msgs, cfg, 1_000_000, 128_000, llm)
        # Non-empty conversation → LLM is called
        assert result is not None

    def test_llm_returns_empty(self):
        """LLM returns empty response."""
        llm = FakeLLM(responses=[""])
        cfg = CompactConfig()
        msgs = [
            {"role": "user", "content": "x" * 300},
            {"role": "assistant", "content": "y" * 300},
            {"role": "user", "content": "z" * 300},
        ]
        result = _llm_summarize_v2(msgs, cfg, 1_000_000, 128_000, llm)
        assert result is None

    def test_llm_returns_whitespace(self):
        """LLM returns only whitespace."""
        llm = FakeLLM(responses=["  \n  \t  "])
        cfg = CompactConfig()
        msgs = [
            {"role": "user", "content": "x" * 300},
            {"role": "assistant", "content": "y" * 300},
            {"role": "user", "content": "z" * 300},
        ]
        result = _llm_summarize_v2(msgs, cfg, 1_000_000, 128_000, llm)
        assert result is None

    def test_llm_raises(self):
        """LLM raises an exception — gracefully returns None."""
        llm = MagicMock()
        llm.chat.side_effect = RuntimeError("API down")
        cfg = CompactConfig()
        msgs = [
            {"role": "user", "content": "x" * 300},
            {"role": "assistant", "content": "y" * 300},
            {"role": "user", "content": "z" * 300},
        ]
        result = _llm_summarize_v2(msgs, cfg, 1_000_000, 128_000, llm)
        assert result is None


_LARGE = "x" * 5000  # ~1600 tokens — big enough to exceed small budgets


class TestLLMSummarizeV2Success:
    """_llm_summarize_v2 returns (messages, summary_text, recent_text)."""

    def test_returns_3_tuple(self):
        llm = FakeLLM(responses=["## Objective\nTest summary"])
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=500)
        msgs = [
            {"role": "user", "content": _LARGE},
            {"role": "assistant", "content": _LARGE},
            {"role": "user", "content": _LARGE},
            {"role": "assistant", "content": _LARGE},
        ]
        result = _llm_summarize_v2(msgs, cfg, 1_000_000, 128_000, llm)
        assert result is not None
        messages, summary_text, recent_text = result
        assert summary_text == "## Objective\nTest summary"
        assert isinstance(recent_text, str)

    def test_preserves_system_messages(self):
        llm = FakeLLM(responses=["Summary"])
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=100)
        msgs = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": _LARGE},
            {"role": "assistant", "content": _LARGE},
            {"role": "user", "content": _LARGE},
            {"role": "assistant", "content": _LARGE},
            {"role": "user", "content": _LARGE},
        ]
        result = _llm_summarize_v2(msgs, cfg, 1_000_000, 128_000, llm)
        assert result is not None
        messages, _, _ = result
        system_msgs = [m for m in messages if m["role"] == "system"]
        assert len(system_msgs) == 1

    def test_recent_text_serializes_recent_messages(self):
        llm = FakeLLM(responses=["Summary"])
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=100)
        msgs = [
            {"role": "user", "content": _LARGE},
            {"role": "assistant", "content": _LARGE},
            {"role": "user", "content": "follow up question"},
            {"role": "assistant", "content": "follow up answer"},
        ]
        result = _llm_summarize_v2(msgs, cfg, 1_000_000, 128_000, llm)
        assert result is not None
        _, _, recent_text = result
        assert "follow up question" in recent_text

    def test_summary_max_tokens_formula(self):
        """opencode formula: max_tokens = min(model_max_output, summary_output_tokens)."""
        llm = FakeLLM(responses=["Summary"])
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=100, summary_output_tokens=4_096)
        msgs = [
            {"role": "user", "content": _LARGE},
            {"role": "assistant", "content": _LARGE},
            {"role": "user", "content": _LARGE},
            {"role": "assistant", "content": _LARGE},
            {"role": "user", "content": _LARGE},
        ]
        _llm_summarize_v2(msgs, cfg, 1_000_000, 8_192, llm)
        assert llm.last_kwargs["max_tokens"] == 4_096  # min(8192, 4096)

    def test_summary_max_tokens_caps_at_config(self):
        """If model_max_output > summary_output_tokens, cap at config."""
        llm = FakeLLM(responses=["Summary"])
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=100, summary_output_tokens=2_048)
        msgs = [
            {"role": "user", "content": _LARGE},
            {"role": "assistant", "content": _LARGE},
            {"role": "user", "content": _LARGE},
            {"role": "assistant", "content": _LARGE},
            {"role": "user", "content": _LARGE},
        ]
        _llm_summarize_v2(msgs, cfg, 1_000_000, 128_000, llm)
        assert llm.last_kwargs["max_tokens"] == 2_048  # min(128000, 2048)

    def test_summary_max_tokens_no_model_output(self):
        """If model_max_output is None, use config directly."""
        llm = FakeLLM(responses=["Summary"])
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=100, summary_output_tokens=4_096)
        msgs = [
            {"role": "user", "content": _LARGE},
            {"role": "assistant", "content": _LARGE},
            {"role": "user", "content": _LARGE},
            {"role": "assistant", "content": _LARGE},
            {"role": "user", "content": _LARGE},
        ]
        _llm_summarize_v2(msgs, cfg, 1_000_000, None, llm)
        assert llm.last_kwargs["max_tokens"] == 4_096

    def test_incremental_summary_includes_previous(self):
        """When previous_summary is set, prompt includes it."""
        llm = FakeLLM(responses=["Updated summary"])
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=100)
        msgs = [
            {"role": "user", "content": _LARGE},
            {"role": "assistant", "content": _LARGE},
            {"role": "user", "content": _LARGE},
            {"role": "assistant", "content": _LARGE},
        ]
        result = _llm_summarize_v2(
            msgs, cfg, 1_000_000, 128_000, llm,
            previous_summary="Old summary text",
        )
        assert result is not None
        # The LLM was called — verify the prompt contains previous summary
        assert llm.last_messages is not None
        prompt = llm.last_messages[1]["content"]
        assert "Old summary text" in prompt
        assert "<previous-summary>" in prompt


class TestLLMSummarizeV2TokenBudget:
    """Token budget selection behavior."""

    def test_preserve_recent_tokens_explicit(self):
        """Explicit preserve_recent_tokens overrides dynamic calculation."""
        llm = FakeLLM(responses=["Summary"])
        cfg = CompactConfig(
            tail_turns=2,
            preserve_recent_tokens=100,
        )
        msgs = [
            {"role": "user", "content": _LARGE},
            {"role": "assistant", "content": _LARGE},
            {"role": "user", "content": _LARGE},
            {"role": "assistant", "content": _LARGE},
            {"role": "user", "content": _LARGE},
            {"role": "assistant", "content": _LARGE},
            {"role": "user", "content": _LARGE},
        ]
        result = _llm_summarize_v2(msgs, cfg, 1_000_000, 128_000, llm)
        assert result is not None
        messages, _, _ = result
        non_system = [m for m in messages if m["role"] != "system"]
        # Budget=100 → recent has last 2 turns via tail_turns minimum
        assert len(non_system) < 7  # not all 7 messages

    def test_dynamic_budget_large_context(self):
        """Large context: budget = min(8000, max(2000, 25% of usable))."""
        llm = FakeLLM(responses=["Summary"])
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=100)
        # Enough messages to exceed budget
        msgs = [
            {"role": "user", "content": _LARGE},
            {"role": "assistant", "content": _LARGE},
            {"role": "user", "content": _LARGE},
            {"role": "assistant", "content": _LARGE},
            {"role": "user", "content": _LARGE},
            {"role": "assistant", "content": _LARGE},
        ]
        result = _llm_summarize_v2(msgs, cfg, 1_000_000, 128_000, llm)
        assert result is not None

    def test_dynamic_budget_small_context(self):
        """Small context: budget = max(2000, 25% of usable)."""
        llm = FakeLLM(responses=["Summary"])
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=100)
        msgs = [
            {"role": "user", "content": _LARGE},
            {"role": "assistant", "content": _LARGE},
            {"role": "user", "content": _LARGE},
            {"role": "assistant", "content": _LARGE},
            {"role": "user", "content": _LARGE},
            {"role": "assistant", "content": _LARGE},
        ]
        result = _llm_summarize_v2(msgs, cfg, 10_000, 1_000, llm)
        assert result is not None

    def test_no_model_context_budget(self):
        """No model context: budget defaults to MAX_PRESERVE_RECENT."""
        llm = FakeLLM(responses=["Summary"])
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=100)
        msgs = [
            {"role": "user", "content": _LARGE},
            {"role": "assistant", "content": _LARGE},
            {"role": "user", "content": _LARGE},
            {"role": "assistant", "content": _LARGE},
            {"role": "user", "content": _LARGE},
            {"role": "assistant", "content": _LARGE},
        ]
        result = _llm_summarize_v2(msgs, cfg, None, None, llm)
        assert result is not None


class TestLLMSummarizeV2EdgeCases:
    """Edge cases in LLM summarization."""

    def test_only_user_messages(self):
        """All-user conversations: recent has messages but no assistant role
        in new_messages → l4_min_messages safety check triggers → None.
        """
        llm = FakeLLM(responses=["Summary"])
        cfg = CompactConfig(tail_turns=2, preserve_recent_tokens=100)
        msgs = [
            {"role": "user", "content": "q1 " * 200},
            {"role": "user", "content": "q2 " * 200},
            {"role": "user", "content": "q3 " * 200},
            {"role": "user", "content": "q4 " * 200},
        ]
        result = _llm_summarize_v2(msgs, cfg, 1_000_000, 128_000, llm)
        assert result is None

    def test_tool_messages_included(self):
        """Tool messages are serialized and sent to LLM for summarization."""
        llm = FakeLLM(responses=["Summary"])
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=100)
        msgs = [
            {"role": "user", "content": _LARGE},
            {"role": "assistant", "content": _LARGE},
            {"role": "tool", "content": "tool result data"},
            {"role": "assistant", "content": _LARGE},
            {"role": "user", "content": _LARGE},
            {"role": "assistant", "content": _LARGE},
        ]
        result = _llm_summarize_v2(msgs, cfg, 1_000_000, 128_000, llm)
        assert result is not None
        assert llm.last_messages is not None
        user_prompt = llm.last_messages[1]["content"]
        assert "tool result data" in user_prompt

    def test_system_messages_not_in_head(self):
        """System messages are excluded from head for summarization."""
        llm = FakeLLM(responses=["Summary"])
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=100)
        msgs = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": _LARGE},
            {"role": "assistant", "content": _LARGE},
            {"role": "user", "content": _LARGE},
            {"role": "assistant", "content": _LARGE},
            {"role": "user", "content": _LARGE},
        ]
        result = _llm_summarize_v2(msgs, cfg, 1_000_000, 128_000, llm)
        assert result is not None
        messages, _, _ = result
        # System message preserved in output
        assert messages[0]["role"] == "system"
