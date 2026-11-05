"""Tests for CompactionMessage and async _amaybe_compact."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentnodes.kernel.agent.compact import CompactConfig, compact_messages


def _register_mock_persister(mock):
    """Register a mock compaction persister and return cleanup."""
    from agentnodes.kernel.agent.loop import register_compaction_persister
    register_compaction_persister(mock)
    return mock


def _reset_persister():
    from agentnodes.kernel.agent.loop import register_compaction_persister
    register_compaction_persister(None)



class TestCompactionMessageFormat:
    """Tests for compaction message persistence format.

    CompactionMessage is not a standalone class — the format is
    constructed inline in _persist_compaction_event. Test the
    persistence dict format directly.
    """

    def test_persist_compaction_event_creates_correct_format(self):
        """_persist_compaction_event creates a user-role compaction message."""
        from agentnodes.kernel.agent.loop import AgentLoop
        cfg = CompactConfig()
        config = MagicMock()
        config.model_context_tokens = 1_000_000
        config.model_max_output_tokens = 128_000
        config.compact_config = cfg
        loop = AgentLoop.__new__(AgentLoop)
        loop.config = config
        loop.cc = cfg
        loop.session_id = "ses_test"
        loop._previous_summary = None
        loop._event_bus = None
        loop.client = MagicMock()

        mock_persist = MagicMock()
        _register_mock_persister(mock_persist)
        try:
            loop._persist_compaction_event("Summary text", "Recent text")
        finally:
            _reset_persister()

        mock_persist.assert_called_once()
        call_kwargs = mock_persist.call_args[1]
        assert call_kwargs["role"] == "assistant"
        assert call_kwargs["content"] == "Summary text"
        assert call_kwargs["message_type"] == "compaction"

    def test_persist_compaction_event_empty_summary_skips(self):
        from agentnodes.kernel.agent.loop import AgentLoop
        cfg = CompactConfig()
        config = MagicMock()
        config.model_context_tokens = 1_000_000
        config.model_max_output_tokens = 128_000
        config.compact_config = cfg
        loop = AgentLoop.__new__(AgentLoop)
        loop.config = config
        loop.cc = cfg
        loop.session_id = "ses_test"
        loop._previous_summary = None
        loop._event_bus = None
        loop.client = MagicMock()

        mock_persist = MagicMock()
        _register_mock_persister(mock_persist)
        try:
            loop._persist_compaction_event("", "Recent")
        finally:
            _reset_persister()

        mock_persist.assert_not_called()

    def test_persist_compaction_event_no_session_skips(self):
        from agentnodes.kernel.agent.loop import AgentLoop
        cfg = CompactConfig()
        config = MagicMock()
        config.model_context_tokens = 1_000_000
        config.model_max_output_tokens = 128_000
        config.compact_config = cfg
        loop = AgentLoop.__new__(AgentLoop)
        loop.config = config
        loop.cc = cfg
        loop.session_id = None
        loop._previous_summary = None
        loop._event_bus = None
        loop.client = MagicMock()

        mock_persist = MagicMock()
        _register_mock_persister(mock_persist)
        try:
            loop._persist_compaction_event("Summary", "Recent")
        finally:
            _reset_persister()

        mock_persist.assert_not_called()


class TestAsyncMaybeCompact:
    """Tests for _amaybe_compact async path."""

    def _make_loop(self, messages=None):
        from agentnodes.kernel.agent.loop import AgentLoop
        cfg = CompactConfig()
        config = MagicMock()
        config.model_context_tokens = 1_000_000
        config.model_max_output_tokens = 128_000
        config.compact_config = cfg
        loop = AgentLoop.__new__(AgentLoop)
        loop.config = config
        loop.cc = cfg
        loop.session_id = "ses_test"
        loop._previous_summary = None
        loop._event_bus = None
        loop.threshold_tokens = None
        loop.client = MagicMock()
        return loop

    @pytest.mark.asyncio
    async def test_async_returns_2_tuple(self):
        """_amaybe_compact returns (messages, applied)."""
        loop = self._make_loop()
        msgs = [{"role": "user", "content": "hello"}]
        result = await loop._amaybe_compact(msgs)
        assert isinstance(result, tuple)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_async_below_threshold_no_compaction(self):
        loop = self._make_loop()
        msgs = [{"role": "user", "content": "hello"}]
        result, applied = await loop._amaybe_compact(msgs)
        assert applied == []

    @pytest.mark.asyncio
    async def test_async_rollback_on_compact_failure(self):
        """If compact_messages raises, _amaybe_compact returns original."""
        loop = self._make_loop()
        msgs = [
            {"role": "user", "content": "x" * 300},
            {"role": "assistant", "content": "y" * 300},
        ]
        with patch(
            "agentnodes.kernel.agent.compact.compact_messages",
            side_effect=RuntimeError("compact error"),
        ):
            result, applied = await loop._amaybe_compact(msgs)
        assert result == msgs
        assert applied == []

    @pytest.mark.asyncio
    async def test_async_rollback_on_persist_failure(self):
        """If _persist_compaction_event raises, return original messages."""
        loop = self._make_loop()
        msgs = [
            {"role": "user", "content": "x" * 300},
            {"role": "assistant", "content": "y" * 300},
            {"role": "user", "content": "z" * 300},
        ]
        # Mock compact_messages to return L4 result
        mock_result = (
            [{"role": "system", "content": "sys"}, msgs[-1]],
            ["llm_summarize(3->2)"],
            "summary text",
            "recent text",
        )
        with patch(
            "agentnodes.kernel.agent.compact.compact_messages",
            return_value=mock_result,
        ), patch.object(
            loop, "_persist_compaction_event",
            side_effect=RuntimeError("DB error"),
        ):
            result, applied = await loop._amaybe_compact(list(msgs))
        # Rollback to original messages
        assert result == msgs
        assert applied == []

    @pytest.mark.asyncio
    async def test_async_successful_l4_returns_compressed(self):
        """When L4 succeeds, compressed messages returned."""
        loop = self._make_loop()
        msgs = [
            {"role": "user", "content": "x" * 300},
            {"role": "assistant", "content": "y" * 300},
            {"role": "user", "content": "z" * 300},
        ]
        mock_result = (
            [{"role": "system", "content": "sys"}, msgs[-1]],
            ["llm_summarize(3->2)"],
            "summary text",
            "recent text",
        )
        with patch(
            "agentnodes.kernel.agent.compact.compact_messages",
            return_value=mock_result,
        ), patch.object(
            loop, "_persist_compaction_event",
        ) as mock_persist:
            result, applied = await loop._amaybe_compact(list(msgs))
        assert len(result) < len(msgs)
        assert "llm_summarize(3->2)" in applied
        mock_persist.assert_called_once()


class TestSyncMaybeCompact:
    """Tests for _maybe_compact sync path."""

    def _make_loop(self):
        from agentnodes.kernel.agent.loop import AgentLoop
        cfg = CompactConfig()
        config = MagicMock()
        config.model_context_tokens = 1_000_000
        config.model_max_output_tokens = 128_000
        config.compact_config = cfg
        loop = AgentLoop.__new__(AgentLoop)
        loop.config = config
        loop.cc = cfg
        loop.session_id = "ses_test"
        loop._previous_summary = None
        loop._event_bus = None
        loop.threshold_tokens = None
        loop.client = MagicMock()
        return loop

    def test_sync_returns_2_tuple(self):
        loop = self._make_loop()
        msgs = [{"role": "user", "content": "hello"}]
        result = loop._maybe_compact(msgs)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_sync_below_threshold(self):
        loop = self._make_loop()
        msgs = [{"role": "user", "content": "hello"}]
        result, applied = loop._maybe_compact(msgs)
        assert applied == []

    def test_sync_rollback_on_compact_failure(self):
        loop = self._make_loop()
        msgs = [{"role": "user", "content": "hello"}]
        with patch(
            "agentnodes.kernel.agent.compact.compact_messages",
            side_effect=RuntimeError("error"),
        ):
            result, applied = loop._maybe_compact(msgs)
        assert result == msgs
        assert applied == []

    def test_sync_rollback_on_persist_failure(self):
        loop = self._make_loop()
        msgs = [
            {"role": "user", "content": "x" * 300},
            {"role": "assistant", "content": "y" * 300},
            {"role": "user", "content": "z" * 300},
        ]
        mock_result = (
            [msgs[-1]],
            ["llm_summarize(3->1)"],
            "summary",
            "recent",
        )
        with patch(
            "agentnodes.kernel.agent.compact.compact_messages",
            return_value=mock_result,
        ), patch.object(
            loop, "_persist_compaction_event",
            side_effect=RuntimeError("DB error"),
        ):
            result, applied = loop._maybe_compact(list(msgs))
        assert result == msgs
        assert applied == []


class TestCompactMessagesThresholdTokens:
    """Tests for threshold_tokens handling."""

    def test_threshold_tokens_none_derives_from_context(self):
        """threshold_tokens=None → derive from model context."""
        llm = MagicMock()
        llm.chat.return_value = MagicMock(content="Summary")
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=500)
        msgs = [
            {"role": "user", "content": "x" * 300},
            {"role": "assistant", "content": "y" * 300},
            {"role": "user", "content": "z" * 300},
        ]
        # With 1M context, threshold is ~872K. Messages are tiny → no compaction
        result, applied, summary, recent = compact_messages(
            msgs, config=cfg,
            model_context_tokens=1_000_000,
            model_max_output_tokens=128_000,
            llm_client=llm,
        )
        assert applied == []

    def test_threshold_tokens_zero_force_all(self):
        """threshold_tokens=0 → force L4 (Phase A: only L4 layer exists).

        Phase A: needs >= 5 turns so with tail_turns=1, L4 safety check
        (l4_min_messages=2) passes (recent = 1 user + 0 system = at least 1).
        With 5 turns and tail_turns=1, recent = last 1 turn = 2 messages
        (user + assistant) which is > l4_min_messages=2 in safety.
        """
        llm = MagicMock()
        llm.chat.return_value = MagicMock(content="Summary")
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=500)
        msgs = []
        for i in range(5):
            msgs.append({"role": "user", "content": "x" * 300})
            msgs.append({"role": "assistant", "content": "y" * 300})
        result, applied, summary, recent = compact_messages(
            msgs, config=cfg, threshold_tokens=0, llm_client=llm,
        )
        # L4 runs in force mode
        assert any("llm_summarize" in layer for layer in applied)
