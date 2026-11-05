"""Tests for CompactionMessage dataclass + DB I/O.

Covers:
- from_db_row: legacy content-prefix detection + new parts_json format
- to_db_kwargs: round-trip
- to_llm_message: <conversation-checkpoint> wrap with user role
- new_compaction_message: factory with fresh UUID
"""
from __future__ import annotations

import json

from agentnodes.kernel.agent.compaction_message import (
    MESSAGE_TYPE_ASSISTANT,
    MESSAGE_TYPE_COMPACTION,
    CompactionMessage,
    new_compaction_message,
)

# ── new_compaction_message factory ──────────────────────────


class TestNewCompactionMessage:
    def test_fresh_uuid(self):
        comp = new_compaction_message(
            session_id="ses_1", summary="sum", recent="rec",
        )
        assert comp.id.startswith("cmp_")
        assert comp.session_id == "ses_1"
        assert comp.summary == "sum"
        assert comp.recent == "rec"
        assert comp.reason == "auto"
        assert comp.metadata == {}

    def test_unique_ids(self):
        a = new_compaction_message("ses_1", "a")
        b = new_compaction_message("ses_1", "a")
        assert a.id != b.id


# ── to_llm_message (THE KEY FIX) ─────────────────────────────


class TestToLlmMessage:
    def test_role_is_user_not_assistant(self):
        """KEY: compaction must project as user role, not assistant.

        This is the fix for the 'spontaneous summary' bug. If the
        LLM sees the previous turn was an assistant 'summary', it
        continues the pattern. User role signals "this is context,
        not a previous turn".
        """
        comp = CompactionMessage(
            id="cmp_1", session_id="ses_1",
            summary="## Objective\n- work on X",
            recent="[User]: hi",
        )
        result = comp.to_llm_message()
        assert result["role"] == "user"

    def test_contains_conversation_checkpoint_wrap(self):
        comp = CompactionMessage(
            id="cmp_1", session_id="ses_1",
            summary="summary text",
            recent="recent text",
        )
        result = comp.to_llm_message()
        content = result["content"]
        assert "<conversation-checkpoint>" in content
        assert "</conversation-checkpoint>" in content

    def test_contains_summary_and_recent_sections(self):
        comp = CompactionMessage(
            id="cmp_1", session_id="ses_1",
            summary="my summary",
            recent="my recent",
        )
        result = comp.to_llm_message()
        content = result["content"]
        assert "<summary>" in content
        assert "my summary" in content
        assert "<recent-context>" in content
        assert "my recent" in content
        assert "</summary>" in content
        assert "</recent-context>" in content

    def test_contains_historical_context_disclaimer(self):
        """The disclaimer tells the LLM this is not new instructions."""
        comp = CompactionMessage(
            id="cmp_1", session_id="ses_1",
            summary="x", recent="y",
        )
        content = comp.to_llm_message()["content"]
        assert "historical context" in content.lower()
        assert "not as new instructions" in content.lower()


# ── to_db_kwargs ────────────────────────────────────────────


class TestToDbKwargs:
    def test_round_trip(self):
        comp = new_compaction_message(
            session_id="ses_1",
            summary="my summary",
            recent="my recent",
            reason="manual",
            metadata={"foo": "bar"},
        )
        kwargs = comp.to_db_kwargs()
        assert kwargs["id"] == comp.id
        assert kwargs["session_id"] == "ses_1"
        assert kwargs["role"] == MESSAGE_TYPE_ASSISTANT  # DB compat
        assert kwargs["message_type"] == MESSAGE_TYPE_COMPACTION
        assert kwargs["content"] == "my summary"
        parts = json.loads(kwargs["parts_json"])
        assert len(parts) == 1
        assert parts[0]["type"] == "compaction"
        assert parts[0]["summary"] == "my summary"
        assert parts[0]["recent"] == "my recent"
        assert parts[0]["reason"] == "manual"


# ── from_db_row (backward compat) ───────────────────────────


class TestFromDbRow:
    def test_new_format_from_parts_json(self):
        comp = new_compaction_message(
            session_id="ses_1", summary="s1", recent="r1", reason="auto",
        )
        row = comp.to_db_kwargs()
        row["tool_call_id"] = None
        row["created_at"] = 1000.0
        recovered = CompactionMessage.from_db_row(row)
        assert recovered.id == comp.id
        assert recovered.summary == "s1"
        assert recovered.recent == "r1"
        assert recovered.reason == "auto"

    def test_legacy_format_with_context_summary_prefix(self):
        """Old data stored as plain text with [context summary] prefix."""
        row = {
            "id": "msg_1",
            "session_id": "ses_1",
            "content": "[context summary]\n## Objective\n- work on X",
            "parts_json": None,
            "tool_call_id": None,
            "metadata_json": None,
        }
        comp = CompactionMessage.from_db_row(row)
        assert comp.summary.startswith("## Objective")
        assert "## Objective" in comp.summary
        assert "work on X" in comp.summary

    def test_legacy_format_anchored_summary(self):
        """Old Anchored Summary format - keeps the whole content."""
        row = {
            "id": "msg_1",
            "session_id": "ses_1",
            "content": "## Anchored Summary\n### Task\nBuild something",
            "parts_json": None,
            "tool_call_id": None,
            "metadata_json": None,
        }
        comp = CompactionMessage.from_db_row(row)
        assert "Anchored Summary" in comp.summary

    def test_empty_content_returns_empty_summary(self):
        row = {
            "id": "msg_1",
            "session_id": "ses_1",
            "content": "",
            "parts_json": None,
            "tool_call_id": None,
            "metadata_json": None,
        }
        comp = CompactionMessage.from_db_row(row)
        assert comp.summary == ""
        assert comp.recent == ""


# ── SQLite Row support ───────────────────────────────────────


class TestSqliteRowSupport:
    def test_from_db_row_works_with_dict_like(self):
        """from_db_row should work with both dict and sqlite3.Row."""
        from unittest.mock import MagicMock

        row = MagicMock()
        row.__getitem__ = lambda self, key: {
            "id": "msg_1",
            "session_id": "ses_1",
            "content": "hello",
            "parts_json": '[{"type": "compaction", "summary": "s", "recent": "r"}]',
            "tool_call_id": None,
            "metadata_json": "{}",
        }[key]
        row.keys = lambda: ["id", "session_id", "content", "parts_json", "tool_call_id", "metadata_json"]
        comp = CompactionMessage.from_db_row(row)
        assert comp.summary == "s"
        assert comp.recent == "r"
