"""B4 D5: the one shared session-listing helper.

Contract: docs/stage-c1-b4-dataplane-design-20261028.md §4.2 (item #3)

Five consumers need "list sessions" and none could reuse an existing
function: the FastAPI endpoint is Request-bound, PlatformStore's variant
raises in single-DB mode, and the legacy SessionDB reads the retired stack.
This is the one function the less-is-more rule permits -- so it gets tests.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from strategy_research.kernel.session.unified import list_sessions


def _make_db(path: Path, rows: list[tuple]) -> None:
    from strategy_research.interfaces.api.session.web_session_db import _ensure_schema

    conn = sqlite3.connect(str(path))
    _ensure_schema(conn)
    for sid, title, created, updated in rows:
        conn.execute(
            "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
            "VALUES (?, 'u', ?, ?, ?)",
            (sid, title, created, updated),
        )
    conn.commit()
    conn.close()


def test_returns_newest_first(tmp_path: Path):
    db = tmp_path / "s.db"
    _make_db(db, [
        ("s1", "old", 1.0, 10.0),
        ("s2", "new", 2.0, 20.0),
        ("s3", "mid", 3.0, 15.0),
    ])
    out = list_sessions(db)
    assert [r["id"] for r in out] == ["s2", "s3", "s1"]


def test_limit_is_applied(tmp_path: Path):
    db = tmp_path / "s.db"
    _make_db(db, [(f"s{i}", f"t{i}", float(i), float(i)) for i in range(10)])
    assert len(list_sessions(db, limit=3)) == 3


def test_shape_is_plain_dicts(tmp_path: Path):
    db = tmp_path / "s.db"
    _make_db(db, [("s1", "t", 1.0, 2.0)])
    row = list_sessions(db)[0]
    assert set(row) == {"id", "title", "created_at", "updated_at"}


def test_missing_db_returns_empty(tmp_path: Path):
    """A fresh install has no DB yet -- callers must not crash."""
    assert list_sessions(tmp_path / "nope.db") == []


def test_limit_is_floored_at_one(tmp_path: Path):
    db = tmp_path / "s.db"
    _make_db(db, [("s1", "t", 1.0, 2.0)])
    assert len(list_sessions(db, limit=0)) == 1


# ── B4 Step 3: the other readers migrated off the legacy stack ──


def _seed_with_messages(path: Path) -> None:
    import sqlite3

    from strategy_research.interfaces.api.session.web_session_db import _ensure_schema

    conn = sqlite3.connect(str(path))
    _ensure_schema(conn)
    conn.execute(
        "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
        "VALUES ('s1', 'u', 'Research', 1.0, 2.0)",
    )
    for i, (role, content) in enumerate([
        ("user", "hello momentum"), ("assistant", "reply about momentum"),
        ("user", "other"),
    ]):
        conn.execute(
            "INSERT INTO messages (id, session_id, role, content, created_at, seq, message_type) "
            "VALUES (?, 's1', ?, ?, ?, ?, ?)",
            (f"m{i}", role, content, 10.0 + i, i + 1, "user" if role == "user" else "assistant"),
        )
    conn.commit()
    conn.close()


def test_get_session_returns_real_columns(tmp_path: Path):
    """The legacy dataclass had no session_id/title/workspace; this does."""
    from strategy_research.kernel.session.unified import get_session

    db = tmp_path / "s.db"
    _seed_with_messages(db)
    row = get_session("s1", db)
    assert row["id"] == "s1"
    assert row["title"] == "Research"
    assert "updated_at" in row


def test_get_session_missing_returns_none(tmp_path: Path):
    from strategy_research.kernel.session.unified import get_session

    db = tmp_path / "s.db"
    _seed_with_messages(db)
    assert get_session("nope", db) is None


def test_get_messages_uses_created_at(tmp_path: Path):
    """The legacy field was `timestamp`; the unified column is created_at."""
    from strategy_research.kernel.session.unified import get_messages

    db = tmp_path / "s.db"
    _seed_with_messages(db)
    msgs = get_messages("s1", db_path=db)
    assert len(msgs) == 3
    assert msgs[0]["role"] == "user"
    assert "created_at" in msgs[0]
    assert "timestamp" not in msgs[0]


def test_get_messages_ordered_by_seq(tmp_path: Path):
    from strategy_research.kernel.session.unified import get_messages

    db = tmp_path / "s.db"
    _seed_with_messages(db)
    contents = [m["content"] for m in get_messages("s1", db_path=db)]
    assert contents == ["hello momentum", "reply about momentum", "other"]


def test_search_messages_like(tmp_path: Path):
    from strategy_research.kernel.session.unified import search_messages

    db = tmp_path / "s.db"
    _seed_with_messages(db)
    hits = search_messages("momentum", db_path=db)
    assert len(hits) == 2
    assert all("momentum" in h["content"] for h in hits)


def test_search_messages_blank_query_returns_empty(tmp_path: Path):
    from strategy_research.kernel.session.unified import search_messages

    db = tmp_path / "s.db"
    _seed_with_messages(db)
    assert search_messages("   ", db_path=db) == []


def test_delete_session_removes_row_and_messages(tmp_path: Path):
    from strategy_research.kernel.session.unified import (
        delete_session,
        get_messages,
        get_session,
    )

    db = tmp_path / "s.db"
    _seed_with_messages(db)
    assert delete_session("s1", db) is True
    assert get_session("s1", db) is None
    assert get_messages("s1", db_path=db) == []


def test_delete_missing_session_returns_false(tmp_path: Path):
    from strategy_research.kernel.session.unified import delete_session

    db = tmp_path / "s.db"
    _seed_with_messages(db)
    assert delete_session("nope", db) is False
