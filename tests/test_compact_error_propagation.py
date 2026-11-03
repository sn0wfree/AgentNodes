"""Tests for L4 compaction error propagation + rollback.

The bug in session 700dc7f7-95d was a NoneType crash that was silently
swallowed. After Commit 1, errors are now:
1. Propagated up (no silent fail)
2. Roll back to original messages (LLM keeps full history)

These tests verify the rollback path.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from strategy_research.core.agent.compact import CompactConfig
from strategy_research.core.agent.loop import AgentLoop
from strategy_research.foundation.llm.config import LLMConfig

# ── _persist_compaction_event propagates errors ──────────────


class TestPersistPropagatesErrors:
    def _make_loop(self):
        cfg = CompactConfig()
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

    def test_persist_message_failure_raises(self):
        """If persist_message fails, the error propagates (not silent)."""
        loop = self._make_loop()

        # Register a persister that raises, so the legacy path actually
        # invokes persist_message and we can verify error propagation.
        from strategy_research.core.agent.loop import (
            compaction_persister_registered,
        )

        with patch(
            "strategy_research.api.routers.web_session.persist_message",
            side_effect=RuntimeError("DB connection lost"),
        ) as mock_persist:
            with compaction_persister_registered(mock_persist):
                with pytest.raises(RuntimeError, match="DB connection lost"):
                    loop._persist_compaction_event("summary", "recent")

    def test_persist_message_failure_logs_traceback(self):
        """logger.exception is called (includes traceback)."""
        loop = self._make_loop()

        from strategy_research.core.agent.loop import (
            compaction_persister_registered,
        )

        with patch(
            "strategy_research.core.agent.loop.logger"
        ) as mock_logger:
            with patch(
                "strategy_research.api.routers.web_session.persist_message",
                side_effect=RuntimeError("DB error"),
            ) as mock_persist:
                with compaction_persister_registered(mock_persist):
                    with pytest.raises(RuntimeError):
                        loop._persist_compaction_event("summary", "recent")
            # logger.exception was called (not just logger.warning)
            mock_logger.exception.assert_called()


# ── _maybe_compact rolls back on persistence failure ──────────


class TestMaybeCompactRollsBack:
    """If persistence fails, _maybe_compact returns the original
    messages (not the L4-compressed ones)."""

    def _make_loop(self, messages: list[dict]) -> AgentLoop:
        cfg = CompactConfig()
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
        loop._event_bus = None
        # Skip __init__
        loop.client = MagicMock()
        # Use original messages as fallback
        loop._original_messages = messages
        return loop

    def test_rolls_back_when_persist_raises(self):
        """If _persist_compaction_event raises, _maybe_compact
        catches it and returns the original messages (no compaction)."""
        # Create enough messages that L4 actually triggers. Use properly
        # alternated user/assistant turns so _select_by_token_budget keeps
        # a user message in `recent` (L4 safety check requires it).
        msgs = []
        for i in range(25):
            msgs.append({"role": "user", "content": f"msg {i} " * 30})
            msgs.append({"role": "assistant", "content": f"reply {i} " * 30})

        loop = self._make_loop(msgs)
        mock_llm = MagicMock()
        mock_llm.chat.return_value = MagicMock(content="summary text")
        loop.client = mock_llm

        # Force L4 to run by setting threshold_tokens=0 (sentinel for force all)
        loop.threshold_tokens = 0

        # Mock _persist_compaction_event to raise (simulates DB error)
        with patch.object(
            loop, "_persist_compaction_event",
            side_effect=RuntimeError("DB failure"),
        ):
            new_messages, applied = loop._maybe_compact(list(msgs))

        # The L4-compressed messages would have fewer entries.
        # Rollback returns the ORIGINAL list unchanged.
        assert len(new_messages) == len(msgs)
        assert applied == []
        # Content unchanged
        assert new_messages[0]["content"] == msgs[0]["content"]

    def test_rolls_back_when_compact_raises(self):
        """If compact_messages itself raises, _maybe_compact returns
        the original messages (LLM is more useful with full history)."""
        msgs = [{"role": "user", "content": "x"}]
        loop = self._make_loop(msgs)

        with patch(
            "strategy_research.core.agent.loop.compact_messages",
            side_effect=RuntimeError("compact internal error"),
        ):
            new_messages, applied = loop._maybe_compact(list(msgs))

        # Original messages returned (no compaction applied)
        assert new_messages == msgs
        assert applied == []

    def test_successful_l4_returns_compressed_messages(self):
        """When L4 succeeds and persistence succeeds, compacted
        messages are returned (not original)."""
        # Properly alternated user/assistant turns so L4 produces a
        # smaller message set (the safety check requires a user role).
        # Messages are large enough to overflow the recent-preserve
        # budget (8k tokens) so head selection is non-empty and L4 fires.
        msgs = []
        for i in range(25):
            msgs.append({"role": "user", "content": f"msg {i} " * 500})
            msgs.append({"role": "assistant", "content": f"reply {i} " * 500})

        loop = self._make_loop(msgs)
        mock_llm = MagicMock()
        mock_llm.chat.return_value = MagicMock(content="summary")
        loop.client = mock_llm
        loop.threshold_tokens = 0  # force L4

        with patch.object(
            loop, "_persist_compaction_event",
        ) as mock_persist:
            new_messages, applied = loop._maybe_compact(list(msgs))

        # L4 ran successfully → fewer messages (compressed)
        assert len(new_messages) < len(msgs)
        # Persist was called once
        mock_persist.assert_called_once()
        # 'llm_summarize' is in applied
        assert any("llm_summarize" in layer for layer in applied)
