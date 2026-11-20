# -*- coding: utf-8 -*-
"""第三轮复审候选 S2：会话删除生命周期（归档 + 投影失效）的行为单测。

``composition.delete_session_lifecycle`` 是删除会话的唯一拥有者——Web
``DELETE /session/{id}`` 与 CLI ``session delete`` 都走它。此前只有 Web 侧
失效投影缓存，CLI 侧只归档不失效，两前端语义分叉。

本文件钉住两件事：

1. **归档 + 列表口径**：删后 ``list_sessions`` 不含该行（``archived=1`` 软删，
   数据保留）；
2. **投影失效**：删后 projector 的内存投影缓存里不再有该会话——这是删除
   生命周期相对「只归档」的增量部分。

Web 与 CLI 两个入口各覆盖一遍（同一断言，两条路径）。

**观察手段**：projector 的缓存是私有属性（``Projector._cache``），本仓库既
有测试（``tests/test_projector_incremental.py``）同样直读该属性——这里沿用
同一手法，无其他公开观察面。
"""
from __future__ import annotations

import argparse
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """把会话库重定向到临时目录（走生产路径解析链：env 覆盖）。"""
    p = tmp_path / "lifecycle.db"
    monkeypatch.setenv("AGENTNODES_SESSION_DB", str(p))
    return p


@pytest.fixture
def app(db_path):
    """真实 app（DI container 装配在同一 DB 上——生产同构）。"""
    from agentnodes.interfaces.api.app import create_app

    return create_app(workspace_path=None)


@pytest.fixture
def container(app):
    """app 挂着的 DI container（projector 与 session_service 的持有者）。"""
    c = getattr(app.state, "_container", None)
    assert c is not None, "app 未 attach DI container"
    return c


@pytest.fixture
def client(app):
    """带鉴权头的测试客户端（token 的 user 与建会话者一致）。"""
    from agentnodes.interfaces.api.auth_tokens import create_token

    c = TestClient(app)
    c.headers.update({"Authorization": f"Bearer {create_token('tester')}"})
    return c


def _seed(db_path, sid: str, title: str = "t", content: str = "c") -> None:
    """插一行会话 + 一条消息（与 CLI 测试同款种子）。"""
    from agentnodes.kernel.session import db as kdb

    conn = kdb._get_db_at(db_path)
    now = time.time()
    conn.execute(
        "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
        "VALUES (?, 'u', ?, ?, ?)",
        (sid, title, now, now),
    )
    conn.execute(
        "INSERT INTO messages (id, session_id, role, content, created_at, "
        "message_type, seq) VALUES (?, ?, 'user', ?, ?, 'user', 1)",
        (f"{sid}-m1", sid, content, now),
    )
    conn.commit()


def _prime_cache(projector, sid: str) -> None:
    """把会话放进 projector 缓存（模拟「该会话已被投影」）。"""
    projector._cache[sid] = object()


class TestDeleteSessionLifecycle:
    """删除生命周期的公共部分（归档 + 投影失效）。"""

    def test_archive_and_drop_projection(self, db_path, container):
        from agentnodes.interfaces.api.composition import delete_session_lifecycle
        from agentnodes.kernel.session.reader import get_session, list_sessions

        _seed(db_path, "del-1")
        _prime_cache(container.projector, "del-1")
        assert "del-1" in container.projector._cache

        assert delete_session_lifecycle("del-1") is True

        row = get_session("del-1")
        assert row is not None and row["archived"] is True
        assert "del-1" not in {s["id"] for s in list_sessions()}
        assert "del-1" not in container.projector._cache

    def test_repeat_delete_is_idempotent(self, db_path, container):
        """重复删除幂等（行仍在、仍算存在——与既有 reader 口径一致）。"""
        from agentnodes.interfaces.api.composition import delete_session_lifecycle

        _seed(db_path, "del-2")
        assert delete_session_lifecycle("del-2") is True
        assert delete_session_lifecycle("del-2") is True

    def test_missing_session_returns_false(self, db_path, container):
        """删不存在的会话返回 False（不抛异常——CLI 据此打印「失败」）。"""
        from agentnodes.interfaces.api.composition import delete_session_lifecycle

        assert delete_session_lifecycle("nope") is False


class TestWebDeleteUsesLifecycle:
    """Web DELETE 走同一操作（不是自己拼 SQL 的第二条路径）。"""

    def test_web_delete_archives_and_invalidates(self, db_path, client, container):
        from agentnodes.kernel.session.reader import list_sessions

        resp = client.post("/api/chat/session", json={"title": "WebDel"})
        sid = resp.json()["id"]
        _prime_cache(container.projector, sid)

        resp = client.delete(f"/api/chat/session/{sid}")
        assert resp.status_code == 200
        assert resp.json()["deleted_id"] == sid

        assert sid not in {s["id"] for s in list_sessions()}
        assert sid not in container.projector._cache


class TestCliDeleteUsesLifecycle:
    """CLI delete 走同一操作——并因此获得投影失效（此前 CLI 只归档）。"""

    def test_cli_delete_archives_and_invalidates(self, db_path, app, container, capsys):
        from agentnodes.interfaces.cli.commands.session import cmd_session_delete

        _seed(db_path, "cli-1")
        _prime_cache(container.projector, "cli-1")

        rc = cmd_session_delete(argparse.Namespace(session_id="cli-1"))
        assert rc == 0
        assert "已删除会话" in capsys.readouterr().out

        assert "cli-1" not in container.projector._cache
