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

from strategy_research.core.session.unified import list_sessions


def _make_db(path: Path, rows: list[tuple]) -> None:
    from strategy_research.api.session.web_session_db import _ensure_schema

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
