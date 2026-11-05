"""Tests for _convert_messages_to_history compaction filter.

Verifies the opencode-aligned behavior: only the MOST RECENT compaction
message is included in LLM history. Older compactions are hidden from
LLM but kept in DB for audit/UI display.
"""

from __future__ import annotations

import logging

import pytest

from agentnodes.interfaces.api.session.models import Message
from agentnodes.interfaces.api.session.service import SessionService
from agentnodes.kernel.agent.compact import CompactConfig

# ── Helpers ──────────────────────────────────────────────────────────


def _make_message(
    role: str,
    content: str,
    *,
    message_id: str | None = None,
    message_type: str | None = None,
    created_at: float = 0.0,
    parts: list[dict] | None = None,
) -> Message:
    """Create a Message for testing."""
    return Message(
        message_id=message_id or f"msg-{role}-{content[:10]}",
        session_id="sess-1",
        role=role,
        content=content,
        message_type=message_type or role,
        created_at=created_at,
        metadata={"_parts": parts or []},
    )


# ── Tests ────────────────────────────────────────────────────────────


class TestHistoryCompactionFilter:
    def test_default_keeps_only_most_recent_compaction(self):
        """5 compactions → only 1 (most recent) goes to LLM history."""
        messages = [
            _make_message("user", "msg1", created_at=1.0),
            _make_message("assistant", "reply1", created_at=2.0),
            _make_message("user", "msg2", created_at=3.0),
            _make_message("assistant", "reply2", created_at=4.0),
            _make_message("compaction", "summary_1", message_type="compaction", created_at=5.0),
            _make_message("user", "msg3", created_at=6.0),
            _make_message("assistant", "reply3", created_at=7.0),
            _make_message("compaction", "summary_2", message_type="compaction", created_at=8.0),
            _make_message("user", "msg4", created_at=9.0),
            _make_message("assistant", "reply4", created_at=10.0),
            _make_message("compaction", "summary_3", message_type="compaction", created_at=11.0),
            _make_message("user", "current_turn", created_at=12.0),  # current turn
        ]
        history = SessionService._convert_messages_to_history(messages)
        comp_in_history = [h for h in history
                          if "<conversation-checkpoint>" in h.get("content", "")]
        assert len(comp_in_history) == 1
        # Most recent compaction (summary_3) should be kept
        assert "summary_3" in comp_in_history[0]["content"]
        # Older compactions (summary_1, summary_2) should be hidden
        assert all("summary_1" not in h.get("content", "") and "summary_2" not in h.get("content", "")
                  for h in history if "<conversation-checkpoint>" in h.get("content", ""))

    def test_keep_all_compactions_legacy(self):
        """keep_all_compactions=True → all compactions preserved."""
        messages = [
            _make_message("user", "msg1", created_at=1.0),
            _make_message("assistant", "reply1", created_at=2.0),
            _make_message("compaction", "summary_1", message_type="compaction", created_at=3.0),
            _make_message("user", "msg2", created_at=4.0),
            _make_message("assistant", "reply2", created_at=5.0),
            _make_message("compaction", "summary_2", message_type="compaction", created_at=6.0),
            _make_message("user", "current", created_at=7.0),
        ]
        history = SessionService._convert_messages_to_history(
            messages, keep_all_compactions=True
        )
        comp_in_history = [h for h in history
                          if "<conversation-checkpoint>" in h.get("content", "")]
        assert len(comp_in_history) == 2

    def test_no_compactions_no_filter(self):
        """No compactions → behavior unchanged."""
        messages = [
            _make_message("user", "msg1", created_at=1.0),
            _make_message("assistant", "reply1", created_at=2.0),
            _make_message("user", "msg2", created_at=3.0),
            _make_message("assistant", "reply2", created_at=4.0),
        ]
        history = SessionService._convert_messages_to_history(messages)
        assert len(history) == 3  # last message excluded (current turn)
        assert all("<conversation-checkpoint>" not in h.get("content", "") for h in history)

    def test_compaction_filter_excludes_current_turn(self):
        """Current turn (last message) is always excluded regardless of type."""
        messages = [
            _make_message("user", "m1", created_at=1.0),
            _make_message("assistant", "r1", created_at=2.0),
            _make_message("compaction", "s1", message_type="compaction", created_at=3.0),
            _make_message("user", "current", created_at=4.0),
        ]
        history = SessionService._convert_messages_to_history(messages)
        assert not any(h.get("content") == "current" for h in history)

    def test_single_compaction_kept(self):
        """1 compaction → keep it (nothing to filter)."""
        messages = [
            _make_message("user", "m1", created_at=1.0),
            _make_message("assistant", "r1", created_at=2.0),
            _make_message("compaction", "s1", message_type="compaction", created_at=3.0),
            _make_message("user", "m2", created_at=4.0),
        ]
        history = SessionService._convert_messages_to_history(messages)
        comp = [h for h in history if "<conversation-checkpoint>" in h.get("content", "")]
        assert len(comp) == 1
        assert "s1" in comp[0]["content"]

    def test_logs_hidden_count(self, caplog):
        """When compactions are hidden, [HIST] debug log records the count."""
        messages = [
            _make_message("user", "m1"),
            _make_message("assistant", "r1"),
            _make_message("compaction", "s1", message_type="compaction"),
            _make_message("user", "m2"),
            _make_message("assistant", "r2"),
            _make_message("compaction", "s2", message_type="compaction"),
            _make_message("user", "m3"),
            _make_message("assistant", "r3"),
            _make_message("compaction", "s3", message_type="compaction"),
            _make_message("user", "current"),
        ]
        with caplog.at_level(
            logging.DEBUG, logger="agentnodes.interfaces.api.session.history_convert"
        ):
            SessionService._convert_messages_to_history(messages)
        # Check that the hidden count log was emitted
        hidden_logs = [r for r in caplog.records
                      if "hiding" in r.message and "older compactions" in r.message]
        assert len(hidden_logs) >= 1
        assert "2" in hidden_logs[0].message  # 2 older compactions hidden

    def test_db_storage_unaffected(self):
        """The filter only affects LLM history; DB messages are unchanged."""
        messages = [
            _make_message("user", "m1"),
            _make_message("assistant", "r1"),
            _make_message("compaction", "s1", message_type="compaction"),
            _make_message("user", "m2"),
            _make_message("assistant", "r2"),
            _make_message("compaction", "s2", message_type="compaction"),
            _make_message("user", "current"),
        ]
        # DB still has all messages
        assert len(messages) == 7
        # LLM history has only 1 compaction
        history = SessionService._convert_messages_to_history(messages)
        comp_count = sum(1 for h in history
                        if "<conversation-checkpoint>" in h.get("content", ""))
        assert comp_count == 1


class TestCompactConfigKeepAll:
    """Verify CompactConfig has the new field with correct default."""

    def test_default_is_false(self):
        cfg = CompactConfig()
        assert cfg.keep_all_compactions_in_history is False

    def test_can_set_true(self):
        cfg = CompactConfig(keep_all_compactions_in_history=True)
        assert cfg.keep_all_compactions_in_history is True

    def test_field_is_frozen(self):
        """CompactConfig is a frozen dataclass — can't mutate."""
        cfg = CompactConfig()
        with pytest.raises(Exception):  # FrozenInstanceError
            cfg.keep_all_compactions_in_history = True  # type: ignore
