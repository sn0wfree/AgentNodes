"""S3 idempotency guard: persist_message must not double-write a message
the projector already projected (same message_id).

Without the guard the second INSERT violates UNIQUE(id) and — worse —
double-counts sessions.message_count (the exchange would appear twice in
history and summary counters would drift).
"""
from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Point the web_session DB helpers at a temp file and bootstrap schema."""
    import agentnodes.interfaces.api.routers.web_session as ws
    import agentnodes.interfaces.api.session.web_session_db as wsdb

    db_path = tmp_path / "s3.db"
    monkeypatch.setattr(ws, "_get_db_path", lambda: db_path)
    monkeypatch.setattr(wsdb, "_get_db_path", lambda: db_path)
    conn = sqlite3.connect(str(db_path))
    wsdb._ensure_schema(conn)
    conn.execute(
        "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
        "VALUES ('s1', 'tester', 't', 0, 0)",
    )
    conn.commit()
    conn.close()
    return db_path


def _count(db_path, session_id: str) -> tuple[int, int]:
    conn = sqlite3.connect(str(db_path))
    n = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,),
    ).fetchone()[0]
    mc = conn.execute(
        "SELECT message_count FROM sessions WHERE id = ?", (session_id,),
    ).fetchone()[0]
    conn.close()
    return n, mc


def test_second_write_with_same_id_is_skipped(db):
    """同一 message_id 二次写入：不抛错、不产生第二行、不重复计数。"""
    from agentnodes.interfaces.api.routers.web_session import persist_message

    first = persist_message(
        session_id="s1", role="assistant", content="hello",
        message_id="msg_fixed", message_type="assistant",
    )
    assert first == "msg_fixed"
    assert _count(db, "s1") == (1, 1)

    second = persist_message(
        session_id="s1", role="assistant", content="hello",
        message_id="msg_fixed", message_type="assistant",
    )
    assert second == "msg_fixed"
    # 仍然一行、计数不涨（幂等）
    assert _count(db, "s1") == (1, 1)


def test_distinct_ids_still_insert(db):
    """不同 message_id 正常写入（防护不能误伤正常路径）。"""
    from agentnodes.interfaces.api.routers.web_session import persist_message

    persist_message(session_id="s1", role="user", content="a",
                    message_id="m1", message_type="user")
    persist_message(session_id="s1", role="assistant", content="b",
                    message_id="m2", message_type="assistant")
    assert _count(db, "s1") == (2, 2)


def test_projector_then_persist_same_id_keeps_one_row(db):
    """模拟 projector 先写、persist_message 后到的次序（真实竞态）。"""
    from agentnodes.interfaces.api.routers.web_session import persist_message

    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO messages (id, session_id, role, content, created_at) "
        "VALUES ('proj_1', 's1', 'assistant', 'from projector', 1.0)",
    )
    conn.commit()
    conn.close()

    persist_message(session_id="s1", role="assistant", content="from projector",
                    message_id="proj_1", message_type="assistant")

    n, mc = _count(db, "s1")
    # 只有 projector 那一行；persist 被跳过 → 计数保持 0（未重复计数）
    assert n == 1
    assert mc == 0
