"""``kernel/session/service.auto_title_session`` 的直达单元测试（批次 B3 下沉后）。

背景：该函数原住 ``routers/web_session.py``，唯一生产调用方是
``SessionService.send_message``；B3 随簇下沉到 kernel，router 留 re-export。
此前它只被 ``test_webui_api`` 经由 router 间接覆盖，本文件补**实现直达**的
行为矩阵（B3 的垫片不透明性不该让实现细节无测试）。
"""
from __future__ import annotations

import pytest

from agentnodes.kernel.session import db as kdb
from agentnodes.kernel.session.service import auto_title_session


def _seed_session(db_path, session_id: str, title: str) -> None:
    conn = kdb._get_db_at(db_path)
    conn.execute(
        "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
        "VALUES (?, 'u', ?, 1.0, 1.0)",
        (session_id, title),
    )
    conn.commit()


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """tmp 数据库 + patch 重定向（auto_title 内部走同包 db._get_db）。"""
    p = tmp_path / "title.db"
    monkeypatch.setattr(kdb, "_get_db_path", lambda: p)
    return p


class TestAutoTitleSession:
    def test_replaces_default_title(self, db_path):
        _seed_session(db_path, "s1", "新会话")
        title = auto_title_session("s1", "帮我分析动量因子")
        assert title == "帮我分析动量因子"

    def test_keeps_custom_title(self, db_path):
        _seed_session(db_path, "s1", "我的研究")
        assert auto_title_session("s1", "任何内容") is None
        conn = kdb._get_db_at(db_path)
        assert conn.execute(
            "SELECT title FROM sessions WHERE id='s1'"
        ).fetchone()[0] == "我的研究"

    def test_truncates_long_content(self, db_path):
        _seed_session(db_path, "s1", "新会话")
        title = auto_title_session("s1", "x" * 100)
        # 原实现：title[:max_len].rstrip() + "…" → max_len+1 字符
        assert title == "x" * 30 + "…"
        assert len(title) == 31

    def test_empty_content_returns_none(self, db_path):
        _seed_session(db_path, "s1", "新会话")
        assert auto_title_session("s1", "") is None
        assert auto_title_session("s1", "   ") is None

    def test_missing_session_returns_none(self, db_path):
        assert auto_title_session("nope", "hello") is None

    def test_newline_collapsed_to_space(self, db_path):
        _seed_session(db_path, "s1", "新会话")
        title = auto_title_session("s1", "第一行\n第二行")
        assert title == "第一行 第二行"
        assert "\n" not in title

    def test_missing_session_robust_to_missing_row(self, db_path):
        """会话行不存在 → None（不抛）；DB 文件由连接层按需建（schema ensure）。"""
        assert auto_title_session("nope", "hello") is None

    def test_default_max_len_parameterizable(self, db_path):
        _seed_session(db_path, "s1", "新会话")
        title = auto_title_session("s1", "abcdefghij", max_len=5)
        assert title == "abcde…"
