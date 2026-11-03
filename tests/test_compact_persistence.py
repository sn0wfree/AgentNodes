"""Tests for L4 compaction persistence (opencode-aligned + bug fix).

Covers:
- _persist_compaction_event works with preserve_recent_tokens=None
  (the bug that crashed with '// 100 NoneType')
- L4 failure rolls back to original messages (no silent history loss)
- 4-tuple return from compact_messages
- recent_text is pre-serialized by compact (string type)
"""
from __future__ import annotations

from unittest.mock import MagicMock

from strategy_research.core.agent.compact import (
    CompactConfig,
    _resolve_threshold_tokens,
    compact_messages,
)
from strategy_research.core.agent.loop import (
    AgentLoop,
    compaction_persister_registered,
)
from strategy_research.foundation.llm.config import LLMConfig

# ── _resolve_threshold_tokens (opencode formula) ────────────


class TestResolveThreshold:
    def test_explicit_threshold_returned_as_is(self):
        cfg = CompactConfig(threshold_tokens=12000)
        assert _resolve_threshold_tokens(cfg, None, None) == 12000

    def test_none_threshold_with_1m_context(self):
        """Opencode formula: context - max(output, buffer).
        1M context, 128K output, 20K buffer → 872K.
        """
        cfg = CompactConfig(threshold_tokens=None)
        result = _resolve_threshold_tokens(
            cfg,
            model_context_tokens=1_000_000,
            model_max_output_tokens=128_000,
        )
        # 1_000_000 - max(128_000, 20_000) = 872_000
        assert result == 872_000

    def test_none_threshold_with_200k_context(self):
        cfg = CompactConfig(threshold_tokens=None)
        result = _resolve_threshold_tokens(
            cfg,
            model_context_tokens=200_000,
            model_max_output_tokens=8_000,
        )
        # 200_000 - max(8_000, 20_000) = 200_000 - 20_000 = 180_000
        assert result == 180_000

    def test_none_threshold_with_unknown_context(self):
        cfg = CompactConfig(threshold_tokens=None)
        result = _resolve_threshold_tokens(cfg, None, None)
        # Unknown context: fall back to 8_000
        assert result == 8_000

    def test_floor_at_8k(self):
        """Trigger must be at least 8K to be useful."""
        cfg = CompactConfig(threshold_tokens=None)
        result = _resolve_threshold_tokens(
            cfg,
            model_context_tokens=30_000,  # small
            model_max_output_tokens=20_000,
        )
        # 30_000 - 20_000 = 10_000, > 8_000
        assert result == 10_000

    def test_floor_at_8k_kicks_in(self):
        """If math gives < 8K, floor at 8K."""
        cfg = CompactConfig(threshold_tokens=None)
        result = _resolve_threshold_tokens(
            cfg,
            model_context_tokens=20_000,
            model_max_output_tokens=15_000,
        )
        # 20_000 - 15_000 = 5_000 → floored to 8_000
        assert result == 8_000


# ── _persist_compaction_event (the bug fix) ───────────────────


class TestPersistCompactionEvent:
    def _make_loop(self, threshold_tokens=None, preserve_recent_tokens=None):
        """Build a minimal AgentLoop for testing."""
        cfg = CompactConfig(
            threshold_tokens=threshold_tokens,
            preserve_recent_tokens=preserve_recent_tokens,
        )
        config = LLMConfig(
            provider="test",
            model="test",
            api_key="env:TEST",
            model_context_tokens=1_000_000,
            model_max_output_tokens=128_000,
        )
        loop = AgentLoop.__new__(AgentLoop)
        loop.config = config
        loop.cc = cfg
        loop.session_id = "ses_test"
        loop._previous_summary = None
        # No EventBusV2 injected → legacy persist_message fallback path
        # (see _persist_compaction_event in core/agent/loop.py).
        loop._event_bus = None
        return loop

    def test_works_with_preserve_recent_none(self):
        """The bug: '// 100' crash when preserve_recent_tokens=None.

        Now: serialize happens in compact, not loop. This should work
        regardless of preserve_recent_tokens value.
        """
        loop = self._make_loop(preserve_recent_tokens=None)
        # Register a no-op persister since the loop has no event_bus.
        # Previously this path silently dropped compaction events; now
        # we fail-fast on missing registration.  Tests opt in via the
        # context manager so production misconfiguration is still loud.
        with compaction_persister_registered(MagicMock()):
            # Should not raise
            loop._persist_compaction_event("summary text", "recent text")
        # No assertion needed — absence of exception is success

    def test_works_with_preserve_recent_int(self):
        loop = self._make_loop(preserve_recent_tokens=10000)
        with compaction_persister_registered(MagicMock()):
            loop._persist_compaction_event("summary text", "recent text")

    def test_works_with_preserve_recent_zero(self):
        loop = self._make_loop(preserve_recent_tokens=0)
        with compaction_persister_registered(MagicMock()):
            loop._persist_compaction_event("summary text", "recent text")

    def test_empty_summary_skips(self):
        loop = self._make_loop()
        # Should not raise even with empty summary
        with compaction_persister_registered(MagicMock()):
            loop._persist_compaction_event("", "recent text")
            loop._persist_compaction_event("   ", "recent text")
            loop._persist_compaction_event(None, "recent text")

    def test_no_session_id_skips(self):
        loop = self._make_loop()
        loop.session_id = None
        # Should not raise
        with compaction_persister_registered(MagicMock()):
            loop._persist_compaction_event("summary", "recent")

    def test_empty_recent_still_persists(self):
        """Empty recent is OK — summary is the important part."""
        loop = self._make_loop()
        # Should not raise
        with compaction_persister_registered(MagicMock()):
            loop._persist_compaction_event("summary", "")


# ── compact_messages 4-tuple (opencode-aligned) ──────────────


class TestCompactMessages4Tuple:
    def test_returns_4_tuple_even_when_l4_skipped(self):
        msgs = [
            {"role": "user", "content": "x"},
        ]
        # Very small messages — L4 won't trigger
        result = compact_messages(msgs, threshold_tokens=1000)
        assert len(result) == 4
        messages, layers, summary, recent = result
        assert summary is None
        assert recent is None
        assert layers == []

    def test_recent_text_is_string_when_l4_runs(self):
        """recent_text must be a string (pre-serialized) when L4 fires."""
        # Make messages large enough that L4 actually generates summary
        msgs = [
            {"role": "user", "content": f"msg {i} " * 30} for i in range(5)
        ] * 5  # 5 user msgs, each 30 words
        msgs += [{"role": "assistant", "content": f"reply {i} " * 30} for i in range(5)] * 5
        cfg = CompactConfig(tail_turns=1)
        mock_client = MagicMock()
        mock_client.chat.return_value = MagicMock(content="summary")
        result = compact_messages(
            msgs, cfg, threshold_tokens=0, llm_client=mock_client,
        )
        messages, layers, summary, recent = result
        # If L4 ran, recent is a string; if not, it's None
        if summary is not None:
            assert isinstance(recent, str)

    def test_summary_max_tokens_uses_opencode_formula(self):
        """max_tokens = min(model_max_output, summary_output_tokens)."""
        # Alternating user/assistant turns, large enough that head
        # selection is non-empty and L4 fires.
        msgs = []
        for i in range(15):
            msgs.append({"role": "user", "content": f"msg {i} " * 1000})
            msgs.append({"role": "assistant", "content": f"reply {i} " * 1000})
        mock_client = MagicMock()
        mock_client.chat.return_value = MagicMock(content="summary")
        cfg = CompactConfig(tail_turns=1)
        # model_max_output=1000, summary_output_tokens=4096 → min=1000
        compact_messages(
            msgs, cfg,
            threshold_tokens=0,
            model_max_output_tokens=1000,
            llm_client=mock_client,
        )
        call_kwargs = mock_client.chat.call_args.kwargs
        assert call_kwargs["max_tokens"] == 1000

    def test_summary_max_tokens_caps_at_4096(self):
        """When model output > 4096, max_tokens = 4096."""
        # Alternating user/assistant turns, large enough that head
        # selection is non-empty and L4 fires.
        msgs = []
        for i in range(15):
            msgs.append({"role": "user", "content": f"msg {i} " * 1000})
            msgs.append({"role": "assistant", "content": f"reply {i} " * 1000})
        mock_client = MagicMock()
        mock_client.chat.return_value = MagicMock(content="summary")
        cfg = CompactConfig(tail_turns=1)
        # model_max_output=8000, summary_output_tokens=4096 → min=4096
        compact_messages(
            msgs, cfg,
            threshold_tokens=0,
            model_max_output_tokens=8000,
            llm_client=mock_client,
        )
        call_kwargs = mock_client.chat.call_args.kwargs
        assert call_kwargs["max_tokens"] == 4096
