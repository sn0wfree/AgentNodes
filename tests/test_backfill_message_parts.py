"""Tests for the message_parts backfill script (Level 2, commit 3)."""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest
import agentnodes.interfaces.api.session.web_session_db as _wsdb

ROOT_PATH = Path(__file__).resolve().parents[1]
SRC_PATH = ROOT_PATH / "src"
sys.path.insert(0, str(SRC_PATH))
sys.path.insert(0, str(ROOT_PATH))


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("AN_WORKSPACE_PATH", str(tmp_path))
    import agentnodes.interfaces.api.routers.web_session as ws
    monkeypatch.setattr(_wsdb, "_get_db_path", lambda: db_path)
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


def _insert_message_with_parts(db_path: Path, sid: str, parts: list, ts: float = 100.0) -> str:
    """Insert a message with parts_json (legacy path).

    Level 2 / Phase 2 commit 6 dropped parts_json. For testing the
    backfill (which migrates FROM parts_json TO message_parts), we
    add the column back, insert the row, then call backfill (which
    copies parts to message_parts and would normally drop parts_json
    in commit 6). The test only verifies the message_parts migration,
    not the column drop.
    """
    import uuid
    mid = str(uuid.uuid4())
    parts_json = json.dumps(parts, ensure_ascii=False)
    import agentnodes.interfaces.api.routers.web_session as ws
    with ws._get_db() as conn:
        # Ensure column exists for the test (commit 6 dropped it).
        # Use a fresh table-create for each new test to avoid schema
        # cache staleness across connections.
        if not ws._has_column(conn, "messages", "parts_json"):
            conn.execute("ALTER TABLE messages ADD COLUMN parts_json TEXT")
        # Verify the column is now visible
        cols = [r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()]
        assert "parts_json" in cols, f"parts_json not in {cols}"
        # Build INSERT dynamically so the test works on both pre-
        # and post-migration DBs
        col_list = ["id", "session_id", "role", "content", "created_at", "message_type", "seq"]
        val_list = [mid, sid, "assistant", "x", ts, "assistant", 1]
        if "parts_json" in cols:
            col_list.insert(4, "parts_json")
            val_list.insert(4, parts_json)
        if "metadata_json" in cols:
            col_list.insert(5, "metadata_json")
            val_list.insert(5, None)
        placeholders = ",".join("?" * len(col_list))
        cols_csv = ",".join(col_list)
        conn.execute(
            f"INSERT INTO messages ({cols_csv}) VALUES ({placeholders})",
            val_list,
        )
        conn.commit()
    return mid


class TestBackfillMessageParts:
    def test_dry_run_no_changes(self, temp_db):
        from scripts import backfill_message_parts as bk
        db_path = temp_db
        _ensure_schema(db_path)
        _create_session(db_path, "sess-1")
        _insert_message_with_parts(
            db_path, "sess-1",
            [{"type": "text", "text": "hi"}, {"type": "tool_call", "name": "x"}],
        )

        before = sqlite3.connect(str(db_path)).execute(
            "SELECT COUNT(*) FROM message_parts"
        ).fetchone()[0]
        assert before == 0

        bk.backfill(db_path, apply=False)

        after = sqlite3.connect(str(db_path)).execute(
            "SELECT COUNT(*) FROM message_parts"
        ).fetchone()[0]
        # dry-run didn't write
        assert after == 0

    def test_apply_migrates_parts_json(self, temp_db):
        from scripts import backfill_message_parts as bk
        db_path = temp_db
        _ensure_schema(db_path)
        _create_session(db_path, "sess-1")
        _insert_message_with_parts(
            db_path, "sess-1",
            [
                {"type": "text", "text": "hello"},
                {"type": "tool_call", "id": "tc1", "name": "x", "arguments": "{}"},
                {"type": "tool_call", "id": "tc2", "name": "y", "arguments": "{}"},
            ],
        )

        bk.backfill(db_path, apply=True)

        with sqlite3.connect(str(db_path)) as conn:
            rows = conn.execute(
                "SELECT type, seq, data_json FROM message_parts ORDER BY seq"
            ).fetchall()
        assert len(rows) == 3
        assert [r[0] for r in rows] == ["text", "tool_call", "tool_call"]
        assert [r[1] for r in rows] == [0, 1, 2]
        # data_json round-trips
        assert json.loads(rows[0][2])["text"] == "hello"

    def test_idempotent_rerun(self, temp_db):
        """Running twice doesn't duplicate or error."""
        from scripts import backfill_message_parts as bk
        db_path = temp_db
        _ensure_schema(db_path)
        _create_session(db_path, "sess-1")
        _insert_message_with_parts(
            db_path, "sess-1",
            [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}],
        )

        bk.backfill(db_path, apply=True)
        n1 = sqlite3.connect(str(db_path)).execute(
            "SELECT COUNT(*) FROM message_parts"
        ).fetchone()[0]
        assert n1 == 2

        bk.backfill(db_path, apply=True)  # rerun
        n2 = sqlite3.connect(str(db_path)).execute(
            "SELECT COUNT(*) FROM message_parts"
        ).fetchone()[0]
        # No duplicates: (message_id, seq) is unique
        assert n2 == 2

    def test_skips_messages_with_null_parts(self, temp_db):
        """Messages with parts_json=NULL or '[]' are skipped."""
        from scripts import backfill_message_parts as bk
        db_path = temp_db
        _ensure_schema(db_path)
        _create_session(db_path, "sess-1")
        # Insert with parts_json=null (need to re-add the column)
        import uuid

        import agentnodes.interfaces.api.routers.web_session as ws
        mid = str(uuid.uuid4())
        with ws._get_db() as conn:
            if not ws._has_column(conn, "messages", "parts_json"):
                conn.execute("ALTER TABLE messages ADD COLUMN parts_json TEXT")
                conn.commit()
            conn.execute(
                "INSERT INTO messages (id, session_id, role, content, parts_json, created_at, metadata_json, message_type, seq) "
                "VALUES (?, ?, ?, ?, NULL, ?, NULL, ?, ?)",
                (mid, "sess-1", "user", "x", 100.0, "user", 1),
            )
            conn.commit()

        bk.backfill(db_path, apply=True)
        with ws._get_db() as conn:
            n = conn.execute("SELECT COUNT(*) FROM message_parts").fetchone()[0]
        assert n == 0

    def test_skips_invalid_json(self, temp_db):
        """Messages with malformed parts_json are logged + skipped, not aborted."""
        from scripts import backfill_message_parts as bk
        db_path = temp_db
        _ensure_schema(db_path)
        # Use a single connection to ensure schema cache consistency
        import agentnodes.interfaces.api.routers.web_session as ws
        with ws._get_db() as conn:
            conn.execute(
                "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
                "VALUES ('sess-1', 'a', 't', 1.0, 1.0)"
            )
            if not ws._has_column(conn, "messages", "parts_json"):
                conn.execute("ALTER TABLE messages ADD COLUMN parts_json TEXT")
            conn.commit()

            import uuid
            # Insert with invalid parts_json + one valid one
            conn.execute(
                "INSERT INTO messages (id, session_id, role, content, parts_json, created_at, metadata_json, message_type, seq) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (str(uuid.uuid4()), "sess-1", "assistant", "x",
                 "not valid json{{", 100.0, "assistant", 1),
            )
            conn.execute(
                "INSERT INTO messages (id, session_id, role, content, parts_json, created_at, metadata_json, message_type, seq) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (str(uuid.uuid4()), "sess-1", "assistant", "y",
                 '[{"type": "text", "text": "ok"}]', 100.0, "assistant", 2),
            )
            conn.commit()

        bk.backfill(db_path, apply=True)
        # The valid one is migrated. Use a raw sqlite3 connection
        # (not _get_db) to avoid schema cache staleness.
        with sqlite3.connect(str(db_path)) as conn:
            n = conn.execute("SELECT COUNT(*) FROM message_parts").fetchone()[0]
        assert n == 1
