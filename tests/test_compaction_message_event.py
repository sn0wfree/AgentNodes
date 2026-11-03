"""Tests for CompactionMessage event-sourcing serialization.

Verifies to_message_list() output format used in compact.ended events.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.kernel.agent.compaction_message import CompactionMessage, new_compaction_message


class TestCompactionMessageToMessageList(unittest.TestCase):
    """Verify to_message_list() output format."""

    def test_to_message_list_basic(self) -> None:
        cm = CompactionMessage(id="cmp_abc123", session_id="s1", summary="test summary", reason="auto")
        result = cm.to_message_list()
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        msg = result[0]
        self.assertEqual(msg["role"], "system")
        self.assertEqual(msg["content"], "test summary")
        self.assertIn("id", msg)
        self.assertTrue(msg["id"].startswith("compact-"))

    def test_to_message_list_id_format(self) -> None:
        cm = CompactionMessage(id="cmp_abc123def456", session_id="s1", summary="summary")
        result = cm.to_message_list()
        self.assertEqual(result[0]["id"], "compact-cmp_abc123de")

    def test_to_message_list_with_recent(self) -> None:
        cm = CompactionMessage(id="cmp_abc", session_id="s1", summary="summary", recent="recent msgs")
        result = cm.to_message_list()
        self.assertEqual(result[0]["content"], "summary")

    def test_to_message_list_empty_summary(self) -> None:
        cm = CompactionMessage(id="cmp_abc", session_id="s1", summary="")
        result = cm.to_message_list()
        self.assertEqual(result[0]["content"], "")

    def test_to_message_list_includes_reason_in_metadata(self) -> None:
        cm = CompactionMessage(id="cmp_abc", session_id="s1", summary="summary", reason="manual")
        result = cm.to_message_list()
        self.assertEqual(result[0]["content"], "summary")


class TestNewCompactionMessage(unittest.TestCase):
    """Verify new_compaction_message factory."""

    def test_creates_with_uuid_id(self) -> None:
        cm = new_compaction_message(session_id="s1", summary="test")
        self.assertTrue(cm.id.startswith("cmp_"))
        self.assertEqual(cm.session_id, "s1")
        self.assertEqual(cm.summary, "test")
        self.assertEqual(cm.reason, "auto")

    def test_creates_with_reason(self) -> None:
        cm = new_compaction_message(session_id="s1", summary="test", reason="overflow")
        self.assertEqual(cm.reason, "overflow")

    def test_creates_with_recent(self) -> None:
        cm = new_compaction_message(session_id="s1", summary="test", recent="recent")
        self.assertEqual(cm.recent, "recent")

    def test_creates_with_metadata(self) -> None:
        cm = new_compaction_message(session_id="s1", summary="test", metadata={"count": 5})
        self.assertEqual(cm.metadata, {"count": 5})


class TestCompactionMessageToParts(unittest.TestCase):
    """Verify to_parts() output format."""

    def test_to_parts_format(self) -> None:
        cm = CompactionMessage(id="cmp_abc", session_id="s1", summary="test", reason="auto")
        parts = cm.to_parts()
        self.assertEqual(len(parts), 1)
        p = parts[0]
        self.assertEqual(p["type"], "compaction")
        self.assertEqual(p["summary"], "test")
        self.assertEqual(p["reason"], "auto")

    def test_to_parts_includes_recent(self) -> None:
        cm = CompactionMessage(id="cmp_abc", session_id="s1", summary="test", recent="context")
        p = cm.to_parts()[0]
        self.assertEqual(p["recent"], "context")


class TestCompactionMessageToDbKwargs(unittest.TestCase):
    """Verify to_db_kwargs() round-trip."""

    def test_to_db_kwargs_basic(self) -> None:
        cm = CompactionMessage(id="cmp_abc", session_id="s1", summary="test")
        kwargs = cm.to_db_kwargs()
        self.assertEqual(kwargs["id"], "cmp_abc")
        self.assertEqual(kwargs["session_id"], "s1")
        self.assertEqual(kwargs["role"], "assistant")
        self.assertEqual(kwargs["message_type"], "compaction")
        self.assertEqual(kwargs["content"], "test")

    def test_to_db_kwargs_round_trip(self) -> None:
        cm = new_compaction_message(session_id="s1", summary="test", recent="ctx", reason="manual")
        kwargs = cm.to_db_kwargs()
        reconstructed = CompactionMessage.from_db_row(kwargs)
        self.assertEqual(reconstructed.id, cm.id)
        self.assertEqual(reconstructed.summary, cm.summary)
        self.assertEqual(reconstructed.recent, cm.recent)
        self.assertEqual(reconstructed.reason, cm.reason)


class TestCompactionMessageToLlmMessage(unittest.TestCase):
    """Verify to_llm_message() projection format."""

    def test_to_llm_message_returns_user_role(self) -> None:
        cm = CompactionMessage(id="cmp_abc", session_id="s1", summary="test")
        msg = cm.to_llm_message()
        self.assertEqual(msg["role"], "user")
        self.assertIn("<conversation-checkpoint>", msg["content"])
        self.assertIn("test", msg["content"])

    def test_to_llm_message_includes_recent(self) -> None:
        cm = CompactionMessage(id="cmp_abc", session_id="s1", summary="test", recent="recent msgs")
        msg = cm.to_llm_message()
        self.assertIn("recent msgs", msg["content"])


if __name__ == "__main__":
    unittest.main()
