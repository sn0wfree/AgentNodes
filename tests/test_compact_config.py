"""Tests for CompactConfig and compact_messages core functions."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from strategy_research.kernel.agent.compact import (
    CompactConfig,
    _build_summary_prompt,
    _estimate_tokens,
    _fix_tool_pairs,
    _select_by_token_budget,
    _serialize_message,
    _split_into_turns,
    compact_messages,
)

# ── CompactConfig defaults ────────────────────────────────────────


class TestCompactConfig:
    def test_defaults(self):
        cfg = CompactConfig()
        assert cfg.enabled is True
        # opencode-aligned defaults (user-specified)
        assert cfg.microcompact_ratio == 0.9
        assert cfg.llm_summarize_ratio == 0.80
        assert cfg.hard_truncate_ratio == 0.99
        assert cfg.overflow_ratio == 0.99
        # opencode-aligned: chars not tokens
        assert cfg.microcompact_tool_result_chars == 2000
        assert cfg.collapse_keep_recent == 4
        assert cfg.tail_turns == 2
        assert cfg.preserve_recent_tokens is None
        assert cfg.enable_incremental_summary is True
        # opencode DEFAULT_BUFFER
        assert cfg.compaction_buffer_tokens == 20_000
        # opencode-aligned: None means derive from model context
        assert cfg.threshold_tokens is None

    def test_custom_values(self):
        cfg = CompactConfig(
            microcompact_ratio=0.6,
            llm_summarize_ratio=0.7,
            microcompact_tool_result_chars=3000,
            collapse_keep_recent=6,
            tail_turns=3,
            preserve_recent_tokens=10000,
        )
        assert cfg.microcompact_ratio == 0.6
        assert cfg.llm_summarize_ratio == 0.7
        assert cfg.microcompact_tool_result_chars == 3000
        assert cfg.collapse_keep_recent == 6
        assert cfg.tail_turns == 3
        assert cfg.preserve_recent_tokens == 10000

    def test_frozen(self):
        cfg = CompactConfig()
        with pytest.raises(AttributeError):
            cfg.microcompact_ratio = 0.9

    def test_tool_truncate_chars_default(self):
        """DEPRECATED field: defaults to empty dict in Phase A (L4-only flow)."""
        cfg = CompactConfig()
        assert cfg.tool_truncate_chars == {}

    def test_tool_truncate_chars_custom(self):
        """DEPRECATED field: still loadable for backward compat, ignored at runtime."""
        limits = {"read": 5000, "custom_tool": 1000}
        cfg = CompactConfig(tool_truncate_chars=limits)
        assert cfg.tool_truncate_chars["read"] == 5000
        assert cfg.tool_truncate_chars["custom_tool"] == 1000


# ── Token estimation ──────────────────────────────────────────────


class TestEstimateTokens:
    def test_empty_messages(self):
        assert _estimate_tokens([]) >= 1

    def test_simple_text(self):
        msgs = [{"role": "user", "content": "hello world"}]
        tokens = _estimate_tokens(msgs)
        assert tokens > 0

    def test_with_tool_calls(self):
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "test", "arguments": '{"arg": "val"}'}}
            ]}
        ]
        tokens = _estimate_tokens(msgs)
        assert tokens > 0


# ── L1: Smart Microcompact (DEPRECATED, removed in Phase A) ───────
# These tests are skipped because L1 (_smart_microcompact, _get_tool_name)
# was removed in commit A2. The L4-only flow in opencode-style doesn't
# pre-truncate tool outputs; L4 handles summarization directly.
# See docs/compaction-phase-a-simplification.md for details.








class TestFixToolPairs:
    def test_no_orphans(self):
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "function": {"name": "test", "arguments": "{}"}}
            ]},
            {"role": "tool", "content": "result", "tool_call_id": "c1"},
        ]
        result = _fix_tool_pairs(msgs)
        assert len(result) == 2

    def test_remove_orphan_result(self):
        msgs = [
            {"role": "tool", "content": "orphan", "tool_call_id": "c1"},
        ]
        result = _fix_tool_pairs(msgs)
        assert len(result) == 0

    def test_remove_orphan_call(self):
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "function": {"name": "test", "arguments": "{}"}}
            ]},
            {"role": "assistant", "content": "no tools"},
        ]
        result = _fix_tool_pairs(msgs)
        # Assistant with only orphaned tool_calls and no content is dropped
        assert len(result) == 1
        assert result[0]["content"] == "no tools"


# ── Token budget selection ────────────────────────────────────────


class TestSelectByTokenBudget:
    def test_returns_head_and_recent(self):
        msgs = [
            {"role": "user", "content": "u1 " * 100},
            {"role": "assistant", "content": "a1 " * 100},
            {"role": "user", "content": "u2 " * 100},
            {"role": "assistant", "content": "a2 " * 100},
            {"role": "user", "content": "u3 " * 100},
            {"role": "assistant", "content": "a3 " * 100},
        ]
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=500)
        head, recent = _select_by_token_budget(msgs, cfg, None)
        assert len(head) > 0
        assert len(recent) > 0
        assert len(head) + len(recent) == len(msgs)

    def test_empty_messages(self):
        cfg = CompactConfig()
        head, recent = _select_by_token_budget([], cfg, None)
        assert head == []
        assert recent == []


# ── Split into turns ──────────────────────────────────────────────


class TestSplitIntoTurns:
    def test_basic(self):
        msgs = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
        ]
        turns = _split_into_turns(msgs)
        assert len(turns) == 2
        assert len(turns[0]) == 2
        assert len(turns[1]) == 2

    def test_trailing_user(self):
        msgs = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
        ]
        turns = _split_into_turns(msgs)
        assert len(turns) == 2
        assert len(turns[1]) == 1


# ── Serialization ─────────────────────────────────────────────────


class TestSerializeMessage:
    def test_user_message(self):
        msg = {"role": "user", "content": "hello"}
        assert _serialize_message(msg) == "[User]: hello"

    def test_assistant_message(self):
        msg = {"role": "assistant", "content": "response"}
        assert _serialize_message(msg) == "[Assistant]: response"

    def test_tool_call(self):
        msg = {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "test", "arguments": '{"k":"v"}'}}],
        }
        result = _serialize_message(msg)
        assert "[Assistant tool call]: test" in result

    def test_tool_result(self):
        msg = {"role": "tool", "content": "output", "tool_call_id": "c1"}
        result = _serialize_message(msg)
        assert "[Tool result]:" in result
        assert "output" in result

    def test_tool_result_truncation(self):
        msg = {"role": "tool", "content": "x" * 3000, "tool_call_id": "c1"}
        result = _serialize_message(msg)
        assert "truncated" in result
        assert len(result) < 3000

    def test_think_block_split(self):
        msg = {"role": "assistant", "content": "<think>thinking about it</think>final answer"}
        result = _serialize_message(msg)
        assert "[Assistant reasoning]: thinking about it" in result
        assert "[Assistant]: final answer" in result
        assert "<think>" not in result

    def test_think_block_only(self):
        msg = {"role": "assistant", "content": "<think>just thinking</think>"}
        result = _serialize_message(msg)
        assert "[Assistant reasoning]: just thinking" in result
        assert "[Assistant]:" not in result

    def test_no_think_block(self):
        msg = {"role": "assistant", "content": "just text"}
        result = _serialize_message(msg)
        assert "[Assistant]: just text" in result
        assert "[Assistant reasoning]:" not in result

    def test_system_message(self):
        msg = {"role": "system", "content": "you are helpful"}
        result = _serialize_message(msg)
        assert result == "[System update]: you are helpful"

    def test_tool_error_detected(self):
        error_content = json.dumps({"status": "error", "message": "file not found"})
        msg = {"role": "tool", "content": error_content, "tool_call_id": "c1"}
        result = _serialize_message(msg)
        assert "[Tool error]:" in result
        assert "file not found" in result
        assert "[Tool result]:" not in result


# ── Summary prompt ────────────────────────────────────────────────


class TestBuildSummaryPrompt:
    def test_first_summary(self):
        prompt = _build_summary_prompt("conversation", None, "template")
        assert "Create a new anchored summary" in prompt
        assert "conversation" in prompt
        assert "template" in prompt

    def test_incremental_summary(self):
        prompt = _build_summary_prompt("conversation", "previous", "template")
        assert "Update the anchored summary" in prompt
        assert "<previous-summary>" in prompt
        assert "previous" in prompt


# ── compact_messages integration ──────────────────────────────────


class TestCompactMessages:
    def test_no_compaction_below_threshold(self):
        msgs = [{"role": "user", "content": "hello"}]
        cfg = CompactConfig()
        result, layers, _, _ = compact_messages(msgs, cfg, threshold_tokens=8000)
        assert layers == []
        assert len(result) == 1

    def test_disabled(self):
        msgs = [{"role": "user", "content": "x" * 10000}]
        cfg = CompactConfig(enabled=False)
        result, layers, _, _ = compact_messages(msgs, cfg, threshold_tokens=100)
        assert layers == []

    def test_microcompact_only(self):
        """DEPRECATED: L1 layer removed in Phase A. Test skipped.
        Replaced by test_compact_opencode_style.py::test_l4_only_no_l1.
        """
        pytest.skip("L1 layer removed in Phase A; see test_compact_opencode_style.py")

    def test_hard_truncate(self):
        """DEPRECATED: L3 layer removed in Phase A. Test skipped."""
        pytest.skip("L3 layer removed in Phase A; see test_compact_opencode_style.py")

    def test_force_all_threshold_zero_runs_all_layers(self):
        """DEPRECATED: L1/L3 removed in Phase A. force_all now only forces L4.
        See test_compact_opencode_style.py::test_force_all_runs_l4_only.
        """
        pytest.skip("L1/L3 removed in Phase A; see test_compact_opencode_style.py")

    def test_force_all_threshold_zero_runs_llm_summarize(self):
        """opencode-aligned: the summary is NOT injected as an inline
        assistant turn (that caused the "spontaneous summary" bug).
        Instead, the summary text is returned via the 4-tuple for
        the caller to persist as a CompactionMessage.

        Phase A: msgs must be user/assistant alternation so turn-split
        produces > tail_turns turns. With tail_turns=2 and 5 turns,
        the L4 safety check (l4_min_messages) passes.
        """
        msgs = []
        for i in range(5):
            msgs.append({"role": "user", "content": f"msg {i} " * 500})
            msgs.append({"role": "assistant", "content": f"reply {i} " * 500})

        mock_client = MagicMock()
        mock_client.chat.return_value = MagicMock(content="- bullet 1\n- bullet 2")

        cfg = CompactConfig(tail_turns=2)
        result, layers, l4_summary, l4_recent = compact_messages(
            msgs, cfg, threshold_tokens=0, llm_client=mock_client,
        )
        assert any("llm_summarize" in l for l in layers)
        # opencode-aligned: summary is NOT inline in messages
        assert not any(
            m.get("content", "").startswith("[context summary]")
            for m in result
        )
        # Instead, summary text is returned in the 4-tuple
        assert l4_summary == "- bullet 1\n- bullet 2"
        # recent is pre-serialized by compact (string)
        assert isinstance(l4_recent, str)


# ── LLMConfig integration ────────────────────────────────────────


class TestLLMConfigCompactConfig:
    def test_compact_config_in_llm_config(self):
        from strategy_research.foundation.llm.config import LLMConfig
        cfg = LLMConfig(compact_config=CompactConfig(microcompact_ratio=0.6))
        assert cfg.compact_config is not None
        assert cfg.compact_config.microcompact_ratio == 0.6

    def test_default_none(self):
        from strategy_research.foundation.llm.config import LLMConfig
        cfg = LLMConfig()
        assert cfg.compact_config is None
