"""Goal event tests: payload builder, projector persistence, history.

Covers docs/goal-events-panel-link.md:
- build_goal_updated_payload: full snapshot, change_type, truncation
- projector._on_goal_updated: idempotent goal message (role=system,
  message_type='goal', metadata = structured snapshot)
- _convert_messages_to_history: goal messages enter the LLM context
  as system messages; compaction hides covered goal messages
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.kernel.events.event_v2 import EventType, EventV2
from strategy_research.api.session.projector import ProjectedSession, Projector
from strategy_research.core.goal.events import (
    CHANGE_TYPE_COMPLETE,
    CHANGE_TYPE_CREATE,
    CHANGE_TYPE_EVIDENCE,
    build_goal_updated_payload,
)


def _snapshot(goal_id="g1", objective="评估动量因子", progress=45, n_criteria=2,
              evidence_count=3, evidence_text="截面 IC = 0.045 (2023-01-01 至 2023-12-31)"):
    return {
        "goal": {
            "goal_id": goal_id,
            "session_id": "s1",
            "status": "active",
            "objective": objective,
            "progress_percent": progress,
            "recap": None,
        },
        "criteria": [
            {"criterion_id": "c1", "text": "完成截面 IC 分析", "status": "covered", "evidence_count": 2},
            {"criterion_id": "c2", "text": "完成分层回测", "status": "in_progress", "evidence_count": 1},
        ][:n_criteria],
        "evidence": [
            {"evidence_id": "ev1", "text": "旧证据"},
            {"evidence_id": "ev2", "text": evidence_text},
        ],
        "evidence_count": evidence_count,
    }


class _FakeStore:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    def get_current_snapshot(self, session_id):
        return self._snapshot

    def get_current_goal(self, session_id):
        g = (self._snapshot or {}).get("goal")
        return g


class TestBuildGoalPayload(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _FakeStore(_snapshot())

    def test_full_snapshot_fields(self) -> None:
        payload = build_goal_updated_payload("s1", self.store, CHANGE_TYPE_CREATE)
        self.assertEqual(payload["goal_id"], "g1")
        self.assertEqual(payload["objective"], "评估动量因子")
        self.assertEqual(payload["progress_percent"], 45)
        self.assertEqual(payload["evidence_count"], 3)
        self.assertEqual(payload["change_type"], CHANGE_TYPE_CREATE)
        self.assertEqual(len(payload["criteria"]), 2)
        self.assertEqual(payload["criteria"][0]["criterion_id"], "c1")
        self.assertEqual(payload["criteria"][0]["status"], "covered")
        self.assertEqual(payload["criteria"][0]["evidence_count"], 2)

    def test_change_types(self) -> None:
        self.assertEqual(
            build_goal_updated_payload("s1", self.store, CHANGE_TYPE_EVIDENCE)["change_type"],
            CHANGE_TYPE_EVIDENCE,
        )
        self.assertEqual(
            build_goal_updated_payload("s1", self.store, CHANGE_TYPE_COMPLETE)["change_type"],
            CHANGE_TYPE_COMPLETE,
        )

    def test_truncation_default_and_configurable(self) -> None:
        payload = build_goal_updated_payload("s1", self.store, CHANGE_TYPE_EVIDENCE)
        full = payload["evidence_text"]
        self.assertTrue(full.endswith("2023-12-31)"))
        # default truncate_chars=100: long text gets cut with an ellipsis
        self.assertEqual(payload["evidence_text_llm"], "截面 IC = 0.045 (2023-01-01 至 2023-12-31)")
        # short text stays intact
        payload2 = build_goal_updated_payload("s1", self.store, CHANGE_TYPE_EVIDENCE, truncate_chars=5)
        self.assertTrue(payload2["evidence_text_llm"].endswith("…"))
        self.assertLessEqual(len(payload2["evidence_text_llm"]), 6)

    def test_explicit_evidence_text_override(self) -> None:
        payload = build_goal_updated_payload(
            "s1", self.store, CHANGE_TYPE_EVIDENCE, evidence_text="自定义证据文本"
        )
        self.assertEqual(payload["evidence_text"], "自定义证据文本")
        self.assertEqual(payload["evidence_text_llm"], "自定义证据文本")

    def test_no_goal_returns_none(self) -> None:
        store = _FakeStore(None)
        self.assertIsNone(build_goal_updated_payload("s1", store, CHANGE_TYPE_CREATE))

    def test_whitespace_only_evidence_truncates_to_empty(self) -> None:
        store = _FakeStore(_snapshot(evidence_text="   "))
        payload = build_goal_updated_payload("s1", store, CHANGE_TYPE_EVIDENCE)
        self.assertEqual(payload["evidence_text"], "")
        self.assertEqual(payload["evidence_text_llm"], "")


def _goal_event(data, event_id="golevt", seq=1):
    return EventV2(
        id=event_id,
        aggregate_id="s1",
        seq=seq,
        type=EventType.GOAL_UPDATED,
        data=data,
        time_created=1000.0 + seq,
    )


class TestProjectorGoalMessage(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmpdir = tempfile.TemporaryDirectory()
        self.proj = Projector(Path(self._tmpdir.name) / "goal_test.db")
        self.state = ProjectedSession(session_id="s1")

    def tearDown(self) -> None:
        # Close the sqlite connection before deleting the temp dir —
        # otherwise Windows keeps goal_test.db locked (WinError 32)
        # and cleanup raises PermissionError.
        self.proj.close()
        self._tmpdir.cleanup()

    def _payload(self, **kw):
        store = _FakeStore(_snapshot(**kw))
        return build_goal_updated_payload("s1", store, CHANGE_TYPE_EVIDENCE)

    def test_creates_system_goal_message(self) -> None:
        self.proj._apply(_goal_event(self._payload()), self.state)
        msg = list(self.state.messages.values())[0]
        self.assertEqual(msg.message_type, "goal")
        self.assertEqual(msg.role, "system")
        self.assertEqual(msg.metadata["goal_id"], "g1")
        self.assertEqual(msg.metadata["change_type"], CHANGE_TYPE_EVIDENCE)
        self.assertIn("[目标状态]", msg.content)
        self.assertIn("进度: 45%", msg.content)
        self.assertIn("截面 IC = 0.045", msg.content)

    def test_idempotent_on_replay(self) -> None:
        # Replaying the SAME event (identical payload) must UPDATE in
        # place, not duplicate — the message_id is the idempotency key
        # shared with the frontend.
        payload = self._payload()
        ev1 = _goal_event(dict(payload), event_id="golevt", seq=1)
        self.proj._apply(ev1, self.state)
        ev2 = _goal_event(dict(payload), event_id="golevt", seq=2)
        self.proj._apply(ev2, self.state)
        self.assertEqual(len(self.state.messages), 1)
        msg = list(self.state.messages.values())[0]
        self.assertIn("截面 IC = 0.045", msg.content)

    def test_distinct_changes_create_distinct_messages(self) -> None:
        # Two different goal updates (different payloads) → two goal
        # messages: the chat stream keeps the full change history.
        ev1 = _goal_event(self._payload(objective="目标一"), event_id="e1", seq=1)
        self.proj._apply(ev1, self.state)
        ev2 = _goal_event(self._payload(objective="目标二"), event_id="e2", seq=2)
        self.proj._apply(ev2, self.state)
        self.assertEqual(len(self.state.messages), 2)

    def test_empty_payload_skipped(self) -> None:
        self.proj._apply(_goal_event({"goal_id": ""}), self.state)
        self.assertEqual(len(self.state.messages), 0)

    def test_flush_persists_metadata(self) -> None:
        import sqlite3
        import tempfile

        with tempfile.TemporaryDirectory():
            db = Path(self._tmpdir.name) / "goal_test.db"
            conn = sqlite3.connect(str(db))
            conn.execute(
                "CREATE TABLE sessions (id TEXT PRIMARY KEY, user_id TEXT, "
                "title TEXT, created_at REAL, updated_at REAL, starred INTEGER "
                "DEFAULT 0, tags_json TEXT DEFAULT '[]', message_count INTEGER "
                "DEFAULT 0, archived INTEGER DEFAULT 0)"
            )
            conn.execute(
                "CREATE TABLE messages (id TEXT PRIMARY KEY, session_id TEXT "
                "NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL DEFAULT '', "
                "created_at REAL NOT NULL, metadata_json TEXT, message_type TEXT, "
                "seq INTEGER NOT NULL DEFAULT 0)"
            )
            conn.execute(
                "CREATE TABLE message_parts (id TEXT PRIMARY KEY, message_id "
                "TEXT NOT NULL, session_id TEXT NOT NULL, type TEXT NOT NULL, "
                "data_json TEXT NOT NULL, seq INTEGER NOT NULL DEFAULT 0, "
                "time_created REAL NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE event_log (id TEXT PRIMARY KEY, aggregate_id TEXT "
                "NOT NULL, seq INTEGER NOT NULL, type TEXT NOT NULL, data_json "
                "TEXT NOT NULL, time_created REAL NOT NULL)"
            )
            conn.execute(
                "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
                "VALUES ('s1', 'admin', 't', 1.0, 1.0)"
            )
            conn.commit()
            conn.close()

            state = ProjectedSession(session_id="s1")
            self.proj._apply(_goal_event(self._payload()), state)
            self.proj.flush(state)
            conn = sqlite3.connect(str(db))
            row = conn.execute(
                "SELECT id, role, message_type, metadata_json FROM messages"
            ).fetchone()
            conn.close()
            self.assertIsNotNone(row)
            self.assertEqual(row[1], "system")
            self.assertEqual(row[2], "goal")
            import json as _json

            meta = _json.loads(row[3])
            self.assertEqual(meta["change_type"], CHANGE_TYPE_EVIDENCE)


class TestGoalInHistory(unittest.TestCase):
    """goal messages enter LLM context as system; compaction hides them."""

    def _convert(self, messages, keep_all=False):
        from strategy_research.api.session.service import SessionService

        # _convert_messages_to_history is an instance method that does
        # not touch self; bypass __init__ with __new__ (stateless).
        svc = SessionService.__new__(SessionService)
        return svc._convert_messages_to_history(
            messages, keep_all_compactions=keep_all
        )

    def _msg(self, mid, role, content, seq, mtype="assistant", metadata=None):
        class M:
            pass

        m = M()
        m.id = mid
        m.message_id = mid
        m.session_id = "s1"
        m.role = role
        m.content = content
        m.seq = seq
        m.message_type = mtype
        m.metadata = metadata or {}
        m.tool_call_id = None
        m.created_at = 1000.0 + seq
        return m

    def test_goal_message_becomes_system_entry(self) -> None:
        msgs = [
            self._msg("m1", "user", "你好", 1),
            self._msg("g1", "system", "[目标状态] 创建目标: ...", 2, mtype="goal"),
            self._msg("m2", "assistant", "好的", 3),
        ]
        history = self._convert(msgs)
        roles = [h["role"] for h in history]
        self.assertIn("system", roles)
        goal_entries = [h for h in history if h["role"] == "system"]
        self.assertEqual(len(goal_entries), 1)
        self.assertIn("[目标状态]", goal_entries[0]["content"])

    def test_compaction_hides_covered_goal_messages(self) -> None:
        msgs = [
            self._msg("m1", "user", "开始", 1),
            self._msg("g1", "system", "[目标状态] 旧目标", 2, mtype="goal"),
            self._msg("c1", "system", "摘要", 3, mtype="compaction",
                      metadata={"compacted_until_seq": 2}),
            self._msg("m2", "user", "继续", 4),
        ]
        history = self._convert(msgs)
        joined = "\n".join(h.get("content", "") for h in history)
        self.assertNotIn("旧目标", joined)
        self.assertIn("摘要", joined)


if __name__ == "__main__":
    unittest.main()
