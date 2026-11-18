"""kernel/session 跨模块组合测试 —— R-014 收官后的领域闭环。

覆盖一条「创建 → 投影写入 → 列表可见 → 软删 → 各视角一致」的完整链路，
串起 B3 下沉的四个模块：``db``（连接/DDL）· ``service``（SessionService /
auto_title）· ``store``（SessionStore 直读）· ``reader``（CLI/MCP/TUI 门面）。

**路径解析用生产路径**：fixture 只设 ``AGENTNODES_SESSION_DB`` env——
service（经 db._get_db_path）、reader（_resolve(None)）、SessionStore
（构造参数）三条解析链全部收敛到同一个真实 resolver，
不做任何模块级 patch。这是 E1 语义统一的端到端钉子：web 与 CLI 共享
同一份存储后，任何一侧的写入对另一侧的读取必须立刻可见，删除必须
三视角一致（列表消失 / 搜索消失 / GET 仍可读）。

Windows 注意：线程缓存里的连接持有文件句柄，conftest 的 autouse
``_close_session_services`` 统一回收；``SessionStore`` 每次操作自带
连接开/关（``_conn`` finally close），无需手动 close。
"""
from __future__ import annotations

import time
import uuid
from unittest.mock import MagicMock

import pytest

from agentnodes.kernel.session import db as kdb
from agentnodes.kernel.session.reader import (
    delete_session,
    get_messages,
    get_session,
    list_sessions,
    search_messages,
)
from agentnodes.kernel.session.service import SessionService, auto_title_session
from agentnodes.kernel.session.store import SessionStore


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """经生产 resolver（AGENTNODES_SESSION_DB 绝对路径覆盖）指向 tmp 库。"""
    p = tmp_path / "loop.db"
    monkeypatch.setenv("AGENTNODES_SESSION_DB", str(p))
    return p


def _insert_message_like_projector(db_path, session_id: str, content: str, seq: int) -> str:
    """按投影器的写入形状插一条消息（B4 Step 4：直接写由投影器负责）。"""
    conn = kdb._get_db_at(db_path)
    msg_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO messages (id, session_id, role, content, created_at, message_type, seq) "
        "VALUES (?, ?, 'assistant', ?, ?, 'assistant', ?)",
        (msg_id, session_id, content, time.time(), seq),
    )
    conn.commit()
    return msg_id


class TestSessionDomainLoop:
    def test_create_list_title_read_archive_loop(self, db_path):
        """创建 → 列表可见 → 首条消息自动起标题 → 投影写入 → 三视角读 → 软删 → 三视角一致。"""
        # 1) SessionService 建会话（经 db 层写 sessions 表）
        svc = SessionService(store=MagicMock(), event_bus=MagicMock())
        meta = svc.create_session("sess-loop", user_id="alice")
        assert meta["id"] == "sess-loop"

        # 2) reader（CLI 视角）立刻可见 —— 同一份存储
        listed = list_sessions()
        assert any(r["id"] == "sess-loop" for r in listed)

        # 3) 首条用户消息自动起标题（send_message 内部的同款调用）
        assert auto_title_session("sess-loop", "帮我回测动量策略") == "帮我回测动量策略"

        # 4) 消息按投影器形状写入；SessionStore 直读（独立连接，同库）
        # C4（2026-11-18）：get_messages 恒走 event_log/projector，
        # messages 读模型直读显式走 `_get_messages_from_db`。
        _insert_message_like_projector(db_path, "sess-loop", "回测结果: IC=0.05", 1)
        _insert_message_like_projector(db_path, "sess-loop", "回测结果: 换手=0.3", 2)
        store = SessionStore(str(db_path))
        msgs = store._get_messages_from_db("sess-loop")
        assert [m.content for m in msgs] == ["回测结果: IC=0.05", "回测结果: 换手=0.3"]

        # 5) reader 搜索命中
        assert len(search_messages("回测结果")) == 2

        # 6) 软删（E1）→ 三视角一致
        assert delete_session("sess-loop") is True
        #   6a. CLI 列表消失
        assert all(r["id"] != "sess-loop" for r in list_sessions())
        #   6b. CLI 搜索消失
        assert search_messages("回测结果") == []
        #   6c. GET 仍可读 + 消息保留（web 语义：投影/SSE 依赖）
        row = get_session("sess-loop")
        assert row is not None and row["archived"] is True
        assert len(get_messages("sess-loop")) == 2
        #   6d. SessionStore 直读同样保留
        assert len(store._get_messages_from_db("sess-loop")) == 2

    def test_two_sessions_independent_visibility(self, db_path):
        """多会话：删除其一不影响另一（列表/搜索按会话隔离）。"""
        svc = SessionService(store=MagicMock(), event_bus=MagicMock())
        svc.create_session("keep-me", user_id="alice")
        svc.create_session("drop-me", user_id="alice")
        _insert_message_like_projector(db_path, "keep-me", "共同关键词 xyz", 1)
        _insert_message_like_projector(db_path, "drop-me", "共同关键词 xyz", 1)

        assert delete_session("drop-me") is True

        ids = {r["id"] for r in list_sessions()}
        assert "keep-me" in ids and "drop-me" not in ids
        hits = search_messages("共同关键词")
        assert len(hits) == 1 and hits[0]["session_id"] == "keep-me"

    def test_reader_delete_is_idempotent_across_channels(self, db_path):
        """重复删除幂等（E1 契约）；不存在返回 False。"""
        svc = SessionService(store=MagicMock(), event_bus=MagicMock())
        svc.create_session("idem", user_id="alice")
        assert delete_session("idem") is True
        assert delete_session("idem") is True, "重复删除幂等 True（行存在）"
        assert delete_session("never-existed") is False


class TestSchemaSharedByAllModules:
    def test_store_and_db_share_one_schema(self, db_path):
        """SessionStore 与 db 层各自连接同一文件 → 同一份 schema（双主修复语义）。"""
        # db 层先建库
        kdb._get_db_at(db_path)
        # store 后建（其 _init_schema 委托 schema_port）
        store = SessionStore(str(db_path))
        with store._conn() as conn:
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert {"sessions", "messages", "message_parts"} <= tables

    def test_cross_connection_visibility(self, db_path):
        """db 缓存连接写入 → store 的独立连接立即可见（WAL 多连接语义）。"""
        store = SessionStore(str(db_path))
        conn = kdb._get_db_at(db_path)
        conn.execute(
            "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
            "VALUES ('cross', 'u', 't', 1.0, 1.0)"
        )
        conn.commit()
        # 经 store 的独立连接读回
        with store._conn() as c:
            n = c.execute("SELECT COUNT(*) FROM sessions WHERE id='cross'").fetchone()[0]
        assert n == 1
