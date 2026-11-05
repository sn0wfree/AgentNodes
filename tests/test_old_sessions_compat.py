"""Tests for compatibility with sessions containing many compactions.

Verifies that the new keep-most-recent-only filter works correctly
across various session shapes (old, new, mixed).
"""

from __future__ import annotations

import logging

from agentnodes.interfaces.api.session.models import Message
from agentnodes.interfaces.api.session.service import SessionService


def _msg(role: str, content: str, message_id: str, message_type: str | None = None) -> Message:
    return Message(
        message_id=message_id,
        session_id="sess-1",
        role=role,
        content=content,
        message_type=message_type or role,
        metadata={},
    )


# ── Compatibility tests ─────────────────────────────────────────────


class TestOldSessionsCompat:
    def test_session_with_5_compactions(self):
        """Old session with 5 compactions → only 1 most recent in LLM context."""
        messages = [
            _msg("user", f"u{i}", f"u-{i}")
            for i in range(10)
        ] + [
            _msg("assistant", f"a{i}", f"a-{i}")
            for i in range(10)
        ] + [
            _msg("compaction", f"summary_{i}", f"c-{i}", "compaction")
            for i in range(5)
        ] + [_msg("user", "current", "current-1")]

        history = SessionService._convert_messages_to_history(messages)
        comp_count = sum(1 for h in history
                        if "<conversation-checkpoint>" in h.get("content", ""))
        assert comp_count == 1
        # Most recent (summary_4) should be the kept one
        assert any("summary_4" in h.get("content", "") for h in history)

    def test_session_with_no_compactions(self, caplog):
        """No compactions → no filtering overhead."""
        messages = [
            _msg("user", f"u{i}", f"u-{i}")
            for i in range(20)
        ] + [_msg("user", "current", "current-1")]

        history = SessionService._convert_messages_to_history(messages)
        # No compactions in history
        assert all("<conversation-checkpoint>" not in h.get("content", "") for h in history)
        # No "hiding" log (no compactions to hide)
        with caplog.at_level(logging.DEBUG, logger="agentnodes.interfaces.api.session.service"):
            SessionService._convert_messages_to_history(messages)
        hidden_logs = [r for r in caplog.records if "hiding" in r.message]
        assert len(hidden_logs) == 0

    def test_session_with_compaction_at_start(self):
        """Single compaction at the start → keep it."""
        messages = [
            _msg("compaction", "first_summary", "c-0", "compaction"),
        ] + [
            _msg("user", f"u{i}", f"u-{i}")
            for i in range(5)
        ] + [
            _msg("assistant", f"a{i}", f"a-{i}")
            for i in range(5)
        ] + [_msg("user", "current", "current-1")]

        history = SessionService._convert_messages_to_history(messages)
        comp_count = sum(1 for h in history
                        if "<conversation-checkpoint>" in h.get("content", ""))
        assert comp_count == 1
        assert any("first_summary" in h.get("content", "") for h in history)

    def test_session_with_all_compactions(self):
        """Edge case: 10 compactions + 1 current user."""
        messages = [
            _msg("compaction", f"s_{i}", f"c-{i}", "compaction")
            for i in range(10)
        ] + [_msg("user", "current", "current-1")]

        history = SessionService._convert_messages_to_history(messages)
        # Only the last compaction (s_9) should be kept
        comp_count = sum(1 for h in history
                        if "<conversation-checkpoint>" in h.get("content", ""))
        assert comp_count == 1
        assert any("s_9" in h.get("content", "") for h in history)

    def test_session_compaction_interleaved(self):
        """Compactions interleaved with user/assistant turns."""
        messages = [
            _msg("user", "u0", "u-0"),
            _msg("assistant", "a0", "a-0"),
            _msg("compaction", "c0", "c-0", "compaction"),
            _msg("user", "u1", "u-1"),
            _msg("assistant", "a1", "a-1"),
            _msg("user", "u2", "u-2"),
            _msg("compaction", "c1", "c-1", "compaction"),
            _msg("user", "u3", "u-3"),
            _msg("user", "current", "current-1"),
        ]
        history = SessionService._convert_messages_to_history(messages)
        # Both c0 and c1 should be filtered to only c1
        comp_count = sum(1 for h in history
                        if "<conversation-checkpoint>" in h.get("content", ""))
        assert comp_count == 1
        assert any("c1" in h.get("content", "") for h in history)
        assert not any("c0" in h.get("content", "") for h in history)

    def test_keep_all_compactions_legacy_compat(self):
        """With keep_all_compactions=True, all 5 are kept (backward compat)."""
        messages = [
            _msg("user", f"u{i}", f"u-{i}")
            for i in range(5)
        ] + [
            _msg("compaction", f"c{i}", f"c-{i}", "compaction")
            for i in range(5)
        ] + [_msg("user", "current", "current-1")]

        history = SessionService._convert_messages_to_history(
            messages, keep_all_compactions=True
        )
        comp_count = sum(1 for h in history
                        if "<conversation-checkpoint>" in h.get("content", ""))
        assert comp_count == 5

    def test_empty_message_list(self):
        """Empty list → empty history (no crash)."""
        history = SessionService._convert_messages_to_history([])
        assert history == []

    def test_only_current_message(self):
        """Only the current turn → empty history (excluded by spec)."""
        messages = [_msg("user", "current", "current-1")]
        history = SessionService._convert_messages_to_history(messages)
        assert history == []


# ── Performance smoke test ──────────────────────────────────────────


class TestCompactionFilterPerf:
    def test_filter_1000_messages_fast(self):
        """1000 messages + 50 compactions → should complete quickly."""
        import time

        messages = []
        for i in range(950):
            messages.append(_msg("user" if i % 2 == 0 else "assistant", f"m{i}", f"m-{i}"))
        for i in range(50):
            messages.insert(i * 20, _msg("compaction", f"s{i}", f"s-{i}", "compaction"))
        messages.append(_msg("user", "current", "current-1"))

        start = time.perf_counter()
        history = SessionService._convert_messages_to_history(messages)
        elapsed = time.perf_counter() - start

        # Should complete in < 100ms
        assert elapsed < 0.1, f"Filter took {elapsed:.3f}s, expected < 0.1s"
        # Only 1 compaction should be kept
        comp_count = sum(1 for h in history
                        if "<conversation-checkpoint>" in h.get("content", ""))
        assert comp_count == 1
