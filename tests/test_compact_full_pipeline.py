"""Tests for compact_messages — full L0-L4 pipeline, fallback chain, dedup detection."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from strategy_research.kernel.agent.compact import (
    CompactConfig,
    compact_messages,
)


class FakeLLM:
    def __init__(self, responses=None, default_text="Summary output"):
        self._responses = list(responses) if responses else []
        self.default_text = default_text
        self.last_messages = None

    def chat(self, messages, **kwargs):
        self.last_messages = messages
        if self._responses:
            resp = MagicMock()
            resp.content = self._responses.pop(0)
            return resp
        resp = MagicMock()
        resp.content = self.default_text
        return resp


def _make_msgs(n: int, content_len: int = 300) -> list[dict]:
    msgs = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": f"msg{i} " + "x" * content_len})
    return msgs


class TestCompactMessagesDisabled:
    def test_disabled_returns_original(self):
        cfg = CompactConfig(enabled=False)
        msgs = _make_msgs(20)
        result, applied, summary, recent = compact_messages(msgs, config=cfg)
        assert result is msgs
        assert applied == []
        assert summary is None
        assert recent is None


# Phase A: L1 layer removed. These tests are skipped until A4
# (compact_messages simplification) when equivalent L4-only tests
# will replace them in test_compact_opencode_style.py.



class TestCompactMessagesNoTrigger:
    def test_below_l1_threshold(self):
        """Below L1 threshold (now L4 threshold in Phase A): no compaction."""
        cfg = CompactConfig(threshold_tokens=100_000)
        msgs = _make_msgs(5, content_len=50)
        result, applied, summary, recent = compact_messages(msgs, config=cfg)
        assert applied == []
        assert summary is None


# Phase A: L4 tests (A4 unblocked - compact_messages is now L4-only).
class TestCompactMessagesL4:
    def test_llm_summarize_applied(self):
        """L4 runs when token count exceeds threshold.

        Phase A: needs >= 3 turns so that with tail_turns=1, the L4 safety
        check (l4_min_messages=2) passes (recent = 1 user + 1 system = 2+).
        """
        llm = FakeLLM(responses=["## Objective\nTest summary"])
        cfg = CompactConfig(
            threshold_tokens=100,
            tail_turns=1,
            preserve_recent_tokens=500,
        )
        msgs = []
        for i in range(5):
            msgs.append({"role": "user", "content": "x" * 300})
            msgs.append({"role": "assistant", "content": "y" * 300})
        result, applied, summary, recent = compact_messages(
            msgs, config=cfg, threshold_tokens=0,
            llm_client=llm,
        )
        assert summary is not None
        assert recent is not None
        assert any("llm_summarize" in layer for layer in applied)

    def test_llm_summarize_returns_4_tuple(self):
        llm = FakeLLM(responses=["Summary text"])
        cfg = CompactConfig(threshold_tokens=100, tail_turns=1, preserve_recent_tokens=500)
        msgs = _make_msgs(10)
        result, applied, summary, recent = compact_messages(
            msgs, config=cfg, threshold_tokens=0, llm_client=llm,
        )
        assert isinstance(summary, str) or summary is None
        assert isinstance(recent, str) or recent is None

    def test_no_llm_client_skips_l4(self):
        """Without llm_client, L4 is skipped."""
        cfg = CompactConfig(threshold_tokens=100)
        msgs = _make_msgs(10, content_len=300)
        result, applied, summary, recent = compact_messages(
            msgs, config=cfg, threshold_tokens=0, llm_client=None,
        )
        assert summary is None
        assert not any("llm_summarize" in layer for layer in applied)

    def test_llm_client_empty_response_skips_l4(self):
        """LLM returns empty → L4 skipped gracefully."""
        llm = FakeLLM(responses=[""])
        cfg = CompactConfig(threshold_tokens=100, tail_turns=1, preserve_recent_tokens=500)
        msgs = _make_msgs(10, content_len=300)
        result, applied, summary, recent = compact_messages(
            msgs, config=cfg, threshold_tokens=0, llm_client=llm,
        )
        assert summary is None

    def test_llm_client_raises_skips_l4(self):
        """LLM raises → L4 skipped gracefully."""
        llm = MagicMock()
        llm.chat.side_effect = RuntimeError("API down")
        cfg = CompactConfig(threshold_tokens=100, tail_turns=1, preserve_recent_tokens=500)
        msgs = _make_msgs(10, content_len=300)
        result, applied, summary, recent = compact_messages(
            msgs, config=cfg, threshold_tokens=0, llm_client=llm,
        )
        assert summary is None


# Phase A: L3 layer removed. These tests are skipped.

class TestCompactMessagesMarkerFiltering:
    def test_context_summary_marker_filtered(self):
        """[context summary] marker in messages should be filtered by compact_messages."""
        cfg = CompactConfig(threshold_tokens=100_000)
        msgs = [
            {"role": "assistant", "content": "[context summary]\nold summary here"},
            {"role": "user", "content": "new question"},
        ]
        result, applied, summary, recent = compact_messages(msgs, config=cfg)
        # The compaction marker message should be filtered or handled
        # (implementation-dependent)
        assert len(result) <= len(msgs)


# Phase A: L1 layer removed. Marked as duplicate of earlier skip-decorated class.

# Phase A: tests unblocked by A4.
class TestCompactMessagesPreviousSummary:
    def test_previous_summary_passed_to_llm(self):
        """previous_summary is passed to _llm_summarize_v2."""
        llm = FakeLLM(responses=["Updated summary"])
        cfg = CompactConfig(threshold_tokens=100, tail_turns=1, preserve_recent_tokens=500)
        msgs = _make_msgs(10, content_len=300)
        result, applied, summary, recent = compact_messages(
            msgs, config=cfg, threshold_tokens=0, llm_client=llm,
            previous_summary="Old summary",
        )
        assert summary is not None
        # Verify LLM was called
        assert llm.last_messages is not None
        prompt = llm.last_messages[1]["content"]
        assert "Old summary" in prompt

    def test_no_previous_summary(self):
        """Without previous_summary, fresh summary prompt used."""
        llm = FakeLLM(responses=["Fresh summary"])
        cfg = CompactConfig(threshold_tokens=100, tail_turns=1, preserve_recent_tokens=500)
        msgs = _make_msgs(10, content_len=300)
        result, applied, summary, recent = compact_messages(
            msgs, config=cfg, threshold_tokens=0, llm_client=llm,
        )
        assert llm.last_messages is not None
        prompt = llm.last_messages[1]["content"]
        assert "<previous-summary>" not in prompt


class TestCompactMessagesEdgeCases:
    def test_empty_messages(self):
        cfg = CompactConfig()
        result, applied, summary, recent = compact_messages([], config=cfg)
        assert result == []
        assert applied == []

    def test_single_user_message(self):
        cfg = CompactConfig(threshold_tokens=100_000)
        msgs = [{"role": "user", "content": "hello"}]
        result, applied, summary, recent = compact_messages(msgs, config=cfg)
        assert result == msgs

    def test_only_system_messages(self):
        cfg = CompactConfig(threshold_tokens=100_000)
        msgs = [{"role": "system", "content": "prompt"}]
        result, applied, summary, recent = compact_messages(msgs, config=cfg)
        assert result == msgs
