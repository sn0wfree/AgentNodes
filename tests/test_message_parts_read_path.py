"""Tests for the message_parts read path (Level 2, Phase 2 commit 4).

Verifies that list_messages reads parts from the message_parts
table (not parts_json) and that _row_to_message accepts the
optional `parts` parameter.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
import uuid
from pathlib import Path

import pytest

SRC_PATH = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_PATH))


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("SR_WORKSPACE_PATH", str(tmp_path))
    # 路径解析发生在 web_session_db 模块内（router 的同名重导出只影响
    # router 自身的名字，不会改变 web_session_db 里的解析），两个模块都要 patch。
    import agentnodes.interfaces.api.routers.web_session as ws
    import agentnodes.interfaces.api.session.web_session_db as _wsdb
    monkeypatch.setattr(_wsdb, "_get_db_path", lambda: db_path)
    monkeypatch.setattr(ws, "_get_db_path", lambda: db_path)
    yield db_path


def _ensure_schema(db_path: Path) -> None:
    import agentnodes.interfaces.api.routers.web_session as ws
    with ws._get_db() as conn:
        ws._ensure_schema(conn)


def _create_session(db_path: Path, sid: str) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (sid, "anonymous", "test", time.time(), time.time()),
        )
        conn.commit()


class TestRowToMessageWithParts:
    def test_parts_param_used_when_provided(self, temp_db):
        """If `parts` is passed, _row_to_message uses it instead of falling back.

        Level 2 / Phase 2 commit 6 dropped parts_json. We test that
        the explicit `parts` parameter wins over any fallback path
        (which is now: synthesize from content for user/error).
        """
        db_path = temp_db
        _ensure_schema(db_path)
        _create_session(db_path, "sess-1")
        from agentnodes.interfaces.api.routers.web_session import _row_to_message, persist_message

        # Create a message with message_parts
        parts = [
            {"type": "text", "text": "from_message_parts"},
            {"type": "tool_call", "id": "tc1", "name": "x", "arguments": "{}"},
        ]
        msg_id = persist_message(
            session_id="sess-1", role="assistant", content="x", parts=parts,
        )

        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone()

        # parts=None → no fallback (parts_json dropped), role=assistant
        # with no user/content → empty parts list
        result_no_parts = _row_to_message(row, parts=None)
        assert result_no_parts["parts"] == []

        # When parts= is passed, use it
        explicit_parts = [
            {"type": "text", "text": "explicit"},
            {"type": "tool_call", "id": "tc1", "name": "x", "arguments": "{}"},
        ]
        result_explicit = _row_to_message(row, parts=explicit_parts)
        assert len(result_explicit["parts"]) == 2
        assert result_explicit["parts"][0]["text"] == "explicit"

    def test_none_parts_falls_back_to_parts_json(self, temp_db):
        """When parts=None (no message_parts rows fetched), fallback to parts_json.

        Level 2 / Phase 2 commit 6 dropped the parts_json column. To
        test the legacy fallback path, we add the column back,
        insert a row with parts_json, then read. All operations
        happen in the SAME connection so the schema cache is consistent.
        """
        db_path = temp_db
        _ensure_schema(db_path)
        _create_session(db_path, "sess-1")
        from agentnodes.interfaces.api.routers.web_session import _get_db, _row_to_message

        mid = str(uuid.uuid4())
        with _get_db() as conn:
            conn.execute("ALTER TABLE messages ADD COLUMN parts_json TEXT")
            conn.execute(
                "INSERT INTO messages (id, session_id, role, content, parts_json, created_at, metadata_json, message_type, seq) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (mid, "sess-1", "assistant", "x",
                 json.dumps([{"type": "text", "text": "from_json"}]),
                 100.0, "assistant", 1),
            )
            conn.commit()
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM messages WHERE id = ?", (mid,)).fetchone()
            # No message_parts row → parts=None → fall back to parts_json
            result = _row_to_message(row, parts=None)

        assert len(result["parts"]) == 1
        assert result["parts"][0]["text"] == "from_json"

    def test_missing_key_in_parts_map_falls_back(self, temp_db):
        """When parts dict doesn't have the message_id, fall back to parts_json.

        This is the actual list_messages case: parts_by_msg only has
        keys for messages with message_parts rows; missing keys
        signal fallback to parts_json.

        Level 2 / Phase 2 commit 6: parts_json was dropped. We add it
        back temporarily to test the fallback path.
        """
        db_path = temp_db
        _ensure_schema(db_path)
        _create_session(db_path, "sess-1")
        from agentnodes.interfaces.api.routers.web_session import _get_db, _row_to_message

        mid = str(uuid.uuid4())
        with _get_db() as conn:
            conn.execute("ALTER TABLE messages ADD COLUMN parts_json TEXT")
            conn.execute(
                "INSERT INTO messages (id, session_id, role, content, parts_json, created_at, metadata_json, message_type, seq) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (mid, "sess-1", "assistant", "x",
                 json.dumps([{"type": "text", "text": "from_json"}]),
                 100.0, "assistant", 1),
            )
            conn.commit()
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM messages WHERE id = ?", (mid,)).fetchone()
            # parts dict doesn't have this mid → fall back
            parts_map = {}  # empty
            result = _row_to_message(row, parts=parts_map.get(row["id"]))

        assert len(result["parts"]) == 1
        assert result["parts"][0]["text"] == "from_json"

    def test_user_message_without_parts_gets_synthesized(self, temp_db):
        """A user message with no parts (and no content) gets a synthesized text part."""
        db_path = temp_db
        _ensure_schema(db_path)
        _create_session(db_path, "sess-1")
        from agentnodes.interfaces.api.routers.web_session import _row_to_message, persist_message

        msg_id = persist_message(
            session_id="sess-1", role="user", content="hello", parts=None,
        )

        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone()

        # No message_parts rows; parts=None → falls back to parts_json (empty)
        # → role=user with content → synthesize text part from content
        result = _row_to_message(row, parts=None)
        assert len(result["parts"]) == 1
        assert result["parts"][0]["type"] == "text"
        assert result["parts"][0]["text"] == "hello"

    def test_error_message_synthesized_from_content(self, temp_db):
        """Error messages with no parts also get synthesized text parts from content."""
        db_path = temp_db
        _ensure_schema(db_path)
        _create_session(db_path, "sess-1")
        from agentnodes.interfaces.api.routers.web_session import _row_to_message, persist_message

        msg_id = persist_message(
            session_id="sess-1", role="assistant", content="⚠️ error msg",
            parts=None, message_type="error",
        )

        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone()

        result = _row_to_message(row, parts=None)
        assert len(result["parts"]) == 1
        assert result["parts"][0]["text"] == "⚠️ error msg"


class TestListMessagesReadsMessageParts:
    def test_list_messages_returns_message_parts_data(self, temp_db):
        """list_messages should return parts from message_parts (Level 2)."""
        db_path = temp_db
        _ensure_schema(db_path)
        _create_session(db_path, "sess-1")

        from agentnodes.interfaces.api.routers.web_session import persist_message

        # Create a message with message_parts (Level 2 normal path)
        parts = [
            {"type": "text", "text": "hello"},
            {"type": "tool_call", "id": "tc1", "name": "x", "arguments": "{}"},
        ]
        persist_message(
            session_id="sess-1", role="assistant", content="x", parts=parts,
            created_at=100.0,
        )

        # Manually call list_messages' underlying logic
        from agentnodes.interfaces.api.routers.web_session import _get_db
        with _get_db() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY seq ASC LIMIT 200",
                ("sess-1",),
            ).fetchall()
            # Replicate list_messages's batch fetch
            from agentnodes.interfaces.api.routers.web_session import _row_to_message
            message_ids = [r["id"] for r in rows]
            placeholders = ",".join("?" * len(message_ids))
            parts_rows = conn.execute(
                f"SELECT message_id, data_json FROM message_parts "
                f"WHERE message_id IN ({placeholders}) ORDER BY message_id, seq",
                message_ids,
            ).fetchall()
            parts_by_msg = {}
            for mid, data_json in parts_rows:
                part = json.loads(data_json)
                if isinstance(part, dict):
                    parts_by_msg.setdefault(mid, []).append(part)
            results = [_row_to_message(r, parts=parts_by_msg.get(r["id"])) for r in rows]

        # Verify message_parts data was used
        assert len(results) == 1
        parts_returned = results[0]["parts"]
        assert len(parts_returned) == 2
        assert parts_returned[0]["text"] == "hello"
        assert parts_returned[1]["id"] == "tc1"

    def test_list_messages_handles_messages_without_parts(self, temp_db):
        """Messages without message_parts rows AND no parts_json column
        get a synthesized text part from content (user/error) or empty parts.

        Level 2 / Phase 2 commit 6 dropped parts_json. Messages that
        were never migrated AND have no message_parts rows fall through
        to the user/error synthesis path.
        """
        db_path = temp_db
        _ensure_schema(db_path)
        _create_session(db_path, "sess-1")
        import uuid as _uuid

        from agentnodes.interfaces.api.routers.web_session import _get_db, _row_to_message

        # Insert a user message with no parts anywhere
        mid = str(_uuid.uuid4())
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "INSERT INTO messages (id, session_id, role, content, created_at, metadata_json, message_type, seq) "
                "VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
                (mid, "sess-1", "user", "hello world", 100.0, "user", 1),
            )
            conn.commit()

        with _get_db() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM messages WHERE id = ?", (mid,)).fetchone()
            # No message_parts row → parts_by_msg.get(mid) is None
            result = _row_to_message(row, parts=None)

        # No parts_json, no message_parts → role=user with content →
        # synthesize text part from content
        assert len(result["parts"]) == 1
        assert result["parts"][0]["type"] == "text"
        assert result["parts"][0]["text"] == "hello world"
