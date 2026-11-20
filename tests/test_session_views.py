# -*- coding: utf-8 -*-
"""M3：apps/chat/session_views 单元测试（此前零直测，只有 CLI 间接测试）。

会话视图是 L2 领域数据面（CLI/REPL 取数走它，渲染留前端）。本文件钉住：
五个薄用例与 kernel.session.reader 行为一致（参数透传、行数据原样）、
删除路径的软删语义、模块归属（L2 而非前端渲染逻辑）。
"""
from __future__ import annotations

import pytest

from agentnodes.apps.chat.session_views import (
    delete_session_by_id,
    list_recent_sessions,
    search_session_messages,
    session_detail,
    session_messages,
)
from agentnodes.kernel.session import reader


class TestSessionViewDelegation:
    """五个用例与 kernel.reader 的委托关系（monkeypatch 断言参数透传）。"""

    def test_list_recent_sessions_forwards_limit(self, monkeypatch):
        seen = {}

        def fake_list(limit=50):
            seen["limit"] = limit
            return [{"id": "s1", "title": "t"}]

        monkeypatch.setattr(reader, "list_sessions", fake_list)
        rows = list_recent_sessions(limit=7)
        assert seen["limit"] == 7
        assert rows == [{"id": "s1", "title": "t"}]  # 领域行数据原样

    def test_session_detail_forwards_id(self, monkeypatch):
        seen = {}

        def fake_get(sid, db_path=None):
            seen["sid"] = sid
            return {"id": sid, "title": "t"}

        monkeypatch.setattr(reader, "get_session", fake_get)
        assert session_detail("s1")["id"] == "s1"
        assert seen["sid"] == "s1"

    def test_session_messages_forwards_id_and_limit(self, monkeypatch):
        seen = {}

        def fake_msgs(sid, limit=50):
            seen.update(sid=sid, limit=limit)
            return [{"role": "user", "content": "hi"}]

        monkeypatch.setattr(reader, "get_messages", fake_msgs)
        rows = session_messages("s1", limit=12)
        assert seen == {"sid": "s1", "limit": 12}
        assert rows[0]["content"] == "hi"

    def test_search_forwards_query_and_limit(self, monkeypatch):
        seen = {}

        def fake_search(query, limit=50):
            seen.update(query=query, limit=limit)
            return [{"session_id": "s1", "content": "hit"}]

        monkeypatch.setattr(reader, "search_messages", fake_search)
        rows = search_session_messages("kw", limit=9)
        assert seen == {"query": "kw", "limit": 9}
        assert rows[0]["content"] == "hit"

    def test_delete_forwards_id(self, monkeypatch):
        seen = {}

        def fake_delete(sid):
            seen["sid"] = sid
            return True

        monkeypatch.setattr(reader, "delete_session", fake_delete)
        assert delete_session_by_id("s1") is True
        assert seen["sid"] == "s1"


class TestNoRenderingLeak:
    """L2 只返回领域行数据——不做截断/时间格式化（渲染留前端）。"""

    def test_no_rendering_params_in_signatures(self):
        import importlib
        import inspect

        # 零 re-export 纪律：apps/__init__ 不导出子模块，用 import_module
        session_views = importlib.import_module("agentnodes.apps.chat.session_views")

        for name in (
            "list_recent_sessions", "session_detail", "session_messages",
            "search_session_messages", "delete_session_by_id",
        ):
            sig = inspect.signature(getattr(session_views, name))
            params = set(sig.parameters)
            assert not (params & {"width", "truncate", "time_format", "console"}), name
