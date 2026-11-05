"""Tests for the text-part-routing DB migration.

The migration (_migrate_text_part_ids) runs idempotently on every
_get_db() call. It scans messages.parts_json and assigns a deterministic
id (f"legacy-{msg_id}-{idx}") to any text part missing one.

Reference: docs/archive/undated/text-part-routing.md
"""
from __future__ import annotations

import json
import sqlite3

import pytest


@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    """Point the DB helpers at a tmp SQLite file."""
    workspace = tmp_path
    workspace.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SR_WORKSPACE_PATH", str(workspace))
    yield workspace / "quantnodes_agentnodes_user.db"


def _insert_session(conn: sqlite3.Connection, session_id: str) -> None:
    conn.execute(
        "INSERT INTO sessions (id, user_id, title, created_at, updated_at, "
        "starred, tags_json, message_count, archived) "
        "VALUES (?, ?, ?, ?, ?, 0, '[]', 0, 0)",
        (session_id, "anonymous", "test", 1000.0, 1000.0),
    )
    conn.commit()


def _insert_message(conn: sqlite3.Connection, message_id: str, session_id: str, parts_json: str) -> None:
    conn.execute(
        "INSERT INTO messages (id, session_id, role, content, parts_json, created_at, metadata_json) "
        "VALUES (?, ?, 'assistant', '', ?, 1000.0, NULL)",
        (message_id, session_id, parts_json),
    )
    conn.commit()


def _get_parts(temp_db, message_id: str) -> list[dict]:
    conn = sqlite3.connect(str(temp_db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT parts_json FROM messages WHERE id = ?", (message_id,)
    ).fetchone()
    conn.close()
    return json.loads(row["parts_json"])


def _create_schema(temp_db) -> None:
    """Create the messages + sessions tables (migration only handles data)."""
    conn = sqlite3.connect(str(temp_db))
    conn.executescript("""
        CREATE TABLE messages (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            parts_json TEXT,
            tool_call_id TEXT,
            created_at REAL NOT NULL,
            metadata_json TEXT
        );
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '新会话',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            starred INTEGER NOT NULL DEFAULT 0,
            tags_json TEXT NOT NULL DEFAULT '[]',
            message_count INTEGER NOT NULL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0
        );
    """)
    conn.commit()
    conn.close()


def _run_migration(temp_db) -> None:
    """Run the migration directly on the temp DB."""
    from agentnodes.interfaces.api.routers.web_session import _migrate_text_part_ids
    conn = sqlite3.connect(str(temp_db))
    conn.row_factory = sqlite3.Row
    _migrate_text_part_ids(conn)
    conn.commit()
    conn.close()


def test_migration_assigns_id_to_text_part_without_id(temp_db):
    """Legacy text parts get f"legacy-{msg_id}-{idx}" ids."""
    _create_schema(temp_db)
    conn = sqlite3.connect(str(temp_db))
    _insert_session(conn, "sess-1")
    _insert_message(conn, "msg-1", "sess-1", json.dumps([
        {"type": "text", "text": "Hello"},
        {"type": "tool_call", "id": "tc1", "name": "foo"},
        {"type": "text", "text": "World"},
    ]))
    conn.close()

    _run_migration(temp_db)

    parts = _get_parts(temp_db, "msg-1")

    # First text part → legacy-msg-1-0
    assert parts[0]["id"] == "legacy-msg-1-0"
    assert parts[0]["text"] == "Hello"
    # Tool call unchanged
    assert parts[1]["id"] == "tc1"
    # Second text part → legacy-msg-1-2 (idx reflects position in parts array)
    assert parts[2]["id"] == "legacy-msg-1-2"
    assert parts[2]["text"] == "World"


def test_migration_is_idempotent(temp_db):
    """Running the migration twice assigns no new ids."""
    _create_schema(temp_db)
    conn = sqlite3.connect(str(temp_db))
    _insert_session(conn, "sess-1")
    _insert_message(conn, "msg-1", "sess-1", json.dumps([
        {"type": "text", "text": "Hello"},
    ]))
    conn.close()

    _run_migration(temp_db)
    _run_migration(temp_db)  # second run, should be a no-op

    parts = _get_parts(temp_db, "msg-1")

    assert parts[0]["id"] == "legacy-msg-1-0"
    assert parts[0]["text"] == "Hello"


def test_migration_does_not_touch_parts_with_id(temp_db):
    """Already-id'd text parts are left alone (no double-assignment)."""
    _create_schema(temp_db)
    conn = sqlite3.connect(str(temp_db))
    _insert_session(conn, "sess-1")
    _insert_message(conn, "msg-1", "sess-1", json.dumps([
        {"type": "text", "id": "explicit-id", "text": "Hello"},
        {"type": "tool_call", "id": "tc1", "name": "foo"},
    ]))
    conn.close()

    _run_migration(temp_db)

    parts = _get_parts(temp_db, "msg-1")

    assert parts[0]["id"] == "explicit-id"
    assert parts[0]["text"] == "Hello"
    assert parts[1]["id"] == "tc1"


def test_migration_skips_non_text_parts(temp_db):
    """Only text parts get ids; tool_call/thinking/etc are untouched."""
    _create_schema(temp_db)
    conn = sqlite3.connect(str(temp_db))
    _insert_session(conn, "sess-1")
    _insert_message(conn, "msg-1", "sess-1", json.dumps([
        {"type": "tool_call", "id": "tc1", "name": "foo"},
        {"type": "thinking", "text": "hmm"},
        {"type": "file_edit", "file_path": "/a", "old_content": "x", "new_content": "y"},
    ]))
    conn.close()

    _run_migration(temp_db)

    parts = _get_parts(temp_db, "msg-1")

    # No parts have the "legacy-" prefix
    for p in parts:
        assert not p.get("id", "").startswith("legacy-")
    # Original ids preserved (or absent for parts that don't need one)
    assert parts[0]["id"] == "tc1"
    assert "id" not in parts[1]
    assert "id" not in parts[2]


def test_migration_handles_mixed_messages(temp_db):
    """Some messages have id'd parts, others don't — both should be normalized."""
    _create_schema(temp_db)
    conn = sqlite3.connect(str(temp_db))
    _insert_session(conn, "sess-1")
    _insert_message(conn, "msg-1", "sess-1", json.dumps([
        {"type": "text", "id": "u1", "text": "A"},
        {"type": "text", "text": "B"},  # missing id
    ]))
    _insert_message(conn, "msg-2", "sess-1", json.dumps([
        {"type": "text", "text": "C"},  # missing id
    ]))
    conn.close()

    _run_migration(temp_db)

    parts1 = _get_parts(temp_db, "msg-1")
    parts2 = _get_parts(temp_db, "msg-2")

    assert parts1[0]["id"] == "u1"                 # unchanged
    assert parts1[1]["id"] == "legacy-msg-1-1"     # assigned
    assert parts2[0]["id"] == "legacy-msg-2-0"     # assigned
