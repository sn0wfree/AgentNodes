"""Tests for the message_parts dual-write (Level 2, Phase 2 commit 2).

After this commit, persist_message writes BOTH:
1. The legacy parts_json column (source of truth until commit 5)
2. Individual rows in message_parts (new structure)

The read path is unchanged in this commit. Commit 5 will switch it.
"""
from __future__ import annotations

import agentnodes.interfaces.api.session.web_session_db as _wsdb

import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

# Add the src directory to the path so we can import agentnodes
SRC_PATH = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_PATH))

# Use a temporary DB for these tests so we don't touch the real one
@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("AN_WORKSPACE_PATH", str(tmp_path))
    # Patch _get_db_path to return our temp db
    import agentnodes.interfaces.api.routers.web_session as ws
    monkeypatch.setattr(_wsdb, "_get_db_path", lambda: db_path)
    monkeypatch.setattr(ws, "_get_db_path", lambda: db_path)
    # Re-import the module so it picks up the patch
    yield db_path
    # Cleanup: close any open connection
    try:
        from agentnodes.interfaces.api.routers.web_session import _get_db
        with _get_db():
            pass
    except Exception:
        pass


def _ensure_schema(db_path: Path) -> None:
    """Run _ensure_schema on the test DB; fixture owns the path patch."""
    import agentnodes.interfaces.api.routers.web_session as ws
    with ws._get_db() as conn:
        ws._ensure_schema(conn)


def _create_session(db_path: Path, sid: str) -> None:
    """Create a session row for FK reference."""
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (sid, "anonymous", "test", time.time(), time.time()),
        )
        conn.commit()


class TestMessagePartsDualWrite:
    def test_persist_writes_parts_json_and_message_parts(self, temp_db):
        """persist_message should write BOTH legacy and new.

        Level 2 / Phase 2 commit 6 dropped parts_json. After commit 6,
        persist_message writes ONLY to message_parts (parts_json is
        gone). This test verifies the post-migration behavior: the
        message row has the basic columns and message_parts has all
        3 parts with correct seq, type, and data.
        """
        db_path = temp_db
        _ensure_schema(db_path)
        sid = "test-sess-1"
        _create_session(db_path, sid)

        from agentnodes.interfaces.api.routers.web_session import persist_message

        parts = [
            {"type": "text", "id": "t1", "text": "hello"},
            {"type": "tool_call", "id": "tc1", "name": "x", "arguments": "{}"},
            {"type": "tool_call", "id": "tc2", "name": "y", "arguments": "{}"},
        ]
        msg_id = persist_message(
            session_id=sid,
            role="assistant",
            content="hi",
            parts=parts,
            created_at=100.0,
        )

        with sqlite3.connect(str(db_path)) as conn:
            # Verify the message row (no parts_json column post-migration)
            row = conn.execute(
                "SELECT content FROM messages WHERE id = ?", (msg_id,)
            ).fetchone()
            assert row is not None
            assert row[0] == "hi"

            # Verify the message_parts rows
            part_rows = conn.execute(
                "SELECT id, message_id, type, data_json, seq, time_created "
                "FROM message_parts WHERE message_id = ? ORDER BY seq",
                (msg_id,),
            ).fetchall()
            assert len(part_rows) == 3
            assert [r[2] for r in part_rows] == ["text", "tool_call", "tool_call"]
            assert [r[4] for r in part_rows] == [0, 1, 2]
            for r in part_rows:
                assert r[1] == msg_id
                assert r[5] == 100.0
            # data_json roundtrips
            assert json.loads(part_rows[0][3])["text"] == "hello"

    def test_persist_without_parts_does_not_write_message_parts(self, temp_db):
        """A message with no parts should not create any message_parts rows."""
        db_path = temp_db
        _ensure_schema(db_path)
        sid = "test-sess-2"
        _create_session(db_path, sid)

        from agentnodes.interfaces.api.routers.web_session import persist_message
        msg_id = persist_message(
            session_id=sid, role="user", content="hi", parts=None,
        )

        with sqlite3.connect(str(db_path)) as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM message_parts WHERE message_id = ?", (msg_id,)
            ).fetchone()[0]
            assert n == 0

    def test_persist_part_sequences_match_input_order(self, temp_db):
        """Part seq values match the input list order."""
        db_path = temp_db
        _ensure_schema(db_path)
        sid = "test-sess-3"
        _create_session(db_path, sid)

        from agentnodes.interfaces.api.routers.web_session import persist_message
        parts = [{"type": "text", "text": str(i)} for i in range(10)]
        msg_id = persist_message(
            session_id=sid, role="assistant", content="x", parts=parts,
        )

        with sqlite3.connect(str(db_path)) as conn:
            seqs = [r[0] for r in conn.execute(
                "SELECT seq FROM message_parts WHERE message_id = ? ORDER BY seq",
                (msg_id,),
            ).fetchall()]
            assert seqs == list(range(10))

    def test_legacy_read_still_works(self, temp_db):
        """The OLD read path (parts_json) must still work after dual-write.
        This guarantees commit 5 can be a safe switchover.

        Level 2 / Phase 2 commit 6 dropped parts_json. After commit 6,
        parts come from message_parts (batch-fetched by callers).
        _row_to_message(row) without the `parts` parameter returns
        empty parts (the legacy fallback path is no longer reachable
        in production but kept for backward compat with test fixtures
        that don't use the new read path).
        """
        db_path = temp_db
        _ensure_schema(db_path)
        sid = "test-sess-4"
        _create_session(db_path, sid)

        from agentnodes.interfaces.api.routers.web_session import _get_db, _row_to_message, persist_message

        parts = [
            {"type": "text", "id": "t1", "text": "hi"},
            {"type": "tool_call", "id": "tc1", "name": "x", "arguments": "{}"},
        ]
        msg_id = persist_message(
            session_id=sid, role="assistant", content="hi", parts=parts,
        )

        # _row_to_message with parts=None: returns empty (post-migration)
        with _get_db() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone()
            result_no_parts = _row_to_message(row)

        # Post-migration: parts_json column is gone, so parts=[]
        assert result_no_parts["parts"] == []

        # _row_to_message WITH explicit parts: returns them
        with _get_db() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone()
            result_explicit = _row_to_message(row, parts=parts)

        assert len(result_explicit["parts"]) == 2
        assert result_explicit["parts"][0]["type"] == "text"
        assert result_explicit["parts"][1]["type"] == "tool_call"

    def test_message_parts_session_id_matches(self, temp_db):
        """message_parts.session_id must equal the parent message's session_id."""
        db_path = temp_db
        _ensure_schema(db_path)
        sid = "test-sess-5"
        _create_session(db_path, sid)

        from agentnodes.interfaces.api.routers.web_session import persist_message

        persist_message(
            session_id=sid, role="assistant", content="x",
            parts=[{"type": "text", "text": "hi"}],
        )

        with sqlite3.connect(str(db_path)) as conn:
            for r in conn.execute("SELECT session_id FROM message_parts"):
                assert r[0] == sid

    def test_cascade_delete_removes_parts(self, temp_db):
        """Deleting a session should cascade-delete its message_parts."""
        db_path = temp_db
        _ensure_schema(db_path)
        sid = "test-sess-6"
        _create_session(db_path, sid)

        from agentnodes.interfaces.api.routers.web_session import persist_message
        persist_message(
            session_id=sid, role="assistant", content="x",
            parts=[{"type": "text", "text": "a"}, {"type": "text", "text": "b"}],
        )

        # Use a single connection with foreign_keys=ON so the FK
        # constraint actually fires. sqlite3's PRAGMA foreign_keys is
        # per-connection; a fresh sqlite3.connect() defaults to OFF.
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            before = conn.execute("SELECT COUNT(*) FROM message_parts").fetchone()[0]
            assert before == 2
            conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
            conn.commit()
            after = conn.execute("SELECT COUNT(*) FROM message_parts").fetchone()[0]
            assert after == 0

    def test_persist_skips_non_dict_parts(self, temp_db):
        """A part that's not a dict should be silently skipped (defensive)."""
        db_path = temp_db
        _ensure_schema(db_path)
        sid = "test-sess-7"
        _create_session(db_path, sid)

        from agentnodes.interfaces.api.routers.web_session import persist_message
        # Mix of valid and invalid parts
        parts = [
            {"type": "text", "text": "valid"},
            "invalid string",
            None,
            {"type": "tool_call", "name": "x"},
        ]
        msg_id = persist_message(
            session_id=sid, role="assistant", content="x", parts=parts,
        )

        with sqlite3.connect(str(db_path)) as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM message_parts WHERE message_id = ?", (msg_id,)
            ).fetchone()[0]
            # Only the 2 valid dicts should be written
            assert n == 2
