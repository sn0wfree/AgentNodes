# -*- coding: utf-8 -*-
"""候选 5：kernel.session 深模块的关键契约直测（不经过 api/session 垫片）。

现有 90+ 测试锚停在 interfaces/api/session 的 14 个转发垫片，深模块自身
无等价直测（deletion test：删垫片后复杂度归零，但测试锚全断）。本文件
对 kernel.session 的公开 interface 写行为级关键用例，证明深模块可独立
测试——为下版"测试锚迁移 + 删垫片"独立批铺路。

覆盖：Projector（事件→投影幂等）、SessionStore（attempt CRUD 往返）、
SessionService（会话创建/取用 + 生命周期）。part_accumulate 是 Projector
内部实现，经 Projector.apply 间接覆盖。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agentnodes.kernel.events.event_v2 import EventType, EventV2
from agentnodes.kernel.session.projector import (
    ProjectedSession,
    Projector,
)
from agentnodes.kernel.session.store import SessionStore


def _init_db(db_path: Path) -> None:
    """最小 schema（sessions / event_log / messages / message_parts）。"""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS sessions ("
            "id TEXT PRIMARY KEY, user_id TEXT, title TEXT, created_at REAL, "
            "updated_at REAL, starred INTEGER DEFAULT 0, "
            "tags_json TEXT DEFAULT '[]', message_count INTEGER DEFAULT 0, "
            "archived INTEGER DEFAULT 0)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS event_log ("
            "seq INTEGER PRIMARY KEY AUTOINCREMENT, aggregate_id TEXT, "
            "type TEXT, data TEXT, ts REAL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS messages ("
            "id TEXT PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, "
            "parts_json TEXT, created_at REAL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS message_parts ("
            "id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, "
            "type TEXT, data TEXT, seq INTEGER)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS attempts ("
            "attempt_id TEXT PRIMARY KEY, session_id TEXT, parent_attempt_id TEXT, "
            "status TEXT, prompt TEXT, run_dir TEXT, summary TEXT, react_trace_json TEXT, "
            "created_at TEXT, completed_at TEXT, message_id TEXT, error TEXT, "
            "metrics_json TEXT, persona TEXT, mode TEXT, model_override TEXT, thinking TEXT)"
        )
        conn.commit()
    finally:
        conn.close()


class TestProjectorDirect:
    """Projector.apply 幂等 + 文本事件投影（公开 interface，无垫片）。"""

    def test_apply_is_idempotent(self, tmp_path):
        db = tmp_path / "p.db"
        _init_db(db)
        projector = Projector(db)
        state = ProjectedSession(session_id="s1")
        event = EventV2.create(
            aggregate_id="s1", seq=1, type=EventType.TEXT_DELTA,
            data={"text": "hello"},
        )
        projector.apply(event, state)
        first = len(state.messages)
        projector.apply(event, state)  # 重复 → 幂等
        assert len(state.messages) == first
        projector.close()

    def test_interested_in_filters_event_types(self, tmp_path):
        db = tmp_path / "p.db"
        _init_db(db)
        projector = Projector(db)
        # FLUSH_ON 的真实成员（公开语义：关心哪些事件触发 flush）
        assert projector.interested_in("message_received") is True
        assert projector.interested_in("totally.unknown") is False
        projector.close()


class TestSessionStoreDirect:
    """SessionStore attempt CRUD 往返（公开 interface，无垫片）。"""

    def test_attempt_roundtrip(self, tmp_path):
        from agentnodes.kernel.session.models import Attempt, AttemptStatus

        db = tmp_path / "s.db"
        _init_db(db)
        store = SessionStore(db)
        attempt = Attempt(session_id="sess-1", status=AttemptStatus.RUNNING)
        aid = str(attempt.attempt_id)
        store.create_attempt(attempt)
        got = store.get_attempt("sess-1", aid)
        assert got is not None
        assert str(got.attempt_id) == aid
        store.update_attempt(got)
        store.delete_attempt(aid)
        assert store.get_attempt("sess-1", aid) is None
        store.close()

    def test_list_attempts_by_status(self, tmp_path):
        from agentnodes.kernel.session.models import Attempt, AttemptStatus

        db = tmp_path / "s2.db"
        _init_db(db)
        store = SessionStore(db)
        store.create_attempt(Attempt(session_id="s", status=AttemptStatus.RUNNING))
        store.create_attempt(Attempt(session_id="s", status=AttemptStatus.COMPLETED))
        running = store.list_attempts_by_status("s", [AttemptStatus.RUNNING.value])
        assert running
        assert all(a.status == AttemptStatus.RUNNING for a in running)
        store.close()


class TestSessionServiceDirect:
    """SessionService 生命周期查询（公开 interface，无垫片）。"""

    def test_new_session_not_processing(self, tmp_path):
        from agentnodes.kernel.session.service import SessionService

        db = tmp_path / "svc.db"
        _init_db(db)
        store = SessionStore(db)
        svc = SessionService(store=store, event_bus=None)
        assert svc.is_session_processing("nonexistent") is False
        # 空/不存在会话仍返回 send action（发送是新会话的合法起点）
        actions = svc.get_available_actions("nonexistent")
        assert any(a.get("name") == "send" for a in actions)
        store.close()

    def test_create_session_then_query(self, tmp_path):
        from unittest.mock import MagicMock

        from agentnodes.kernel.session.service import SessionService

        db = tmp_path / "svc2.db"
        _init_db(db)
        store = SessionStore(db)
        svc = SessionService(store=store, event_bus=MagicMock())
        result = svc.create_session("s1", title="t", user_id="u1")
        assert result is not None
        assert svc.is_session_processing("s1") is False
        store.close()
