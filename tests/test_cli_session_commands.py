"""CLI session 命令（cmd_session_list/show/search/delete）的输出面测试。

此前 ``interfaces/cli/commands/session.py`` 覆盖率 27.8%——命令体几乎无测试。
E1 改了删除语义（软删 + 文案），这些命令的输出路径值得钉住。
路径解析走生产链：fixture 设 ``AGENTNODES_SESSION_DB`` env。
"""
from __future__ import annotations

import argparse
import time

import pytest

from agentnodes.kernel.session import db as kdb
from agentnodes.interfaces.cli.commands.session import (
    cmd_session_delete,
    cmd_session_list,
    cmd_session_search,
    cmd_session_show,
)


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    p = tmp_path / "cli.db"
    monkeypatch.setenv("AGENTNODES_SESSION_DB", str(p))
    return p


def _seed(db_path, sid: str, title: str, content: str):
    conn = kdb._get_db_at(db_path)
    now = time.time()
    conn.execute(
        "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
        "VALUES (?, 'u', ?, ?, ?)",
        (sid, title, now, now),
    )
    conn.execute(
        "INSERT INTO messages (id, session_id, role, content, created_at, message_type, seq) "
        "VALUES (?, ?, 'user', ?, ?, 'user', 1)",
        (f"{sid}-m1", sid, content, now),
    )
    conn.commit()


def _args(**kw) -> argparse.Namespace:
    return argparse.Namespace(**kw)


class TestSessionList:
    def test_empty_db_prints_hint(self, db_path, capsys):
        assert cmd_session_list(_args(limit=10)) == 0
        assert "暂无会话" in capsys.readouterr().out

    def test_lists_sessions_with_title(self, db_path, capsys):
        _seed(db_path, "abc12345-6789", "我的策略研究", "内容")
        assert cmd_session_list(_args(limit=10)) == 0
        out = capsys.readouterr().out
        assert "abc12345-6789"[:16] in out
        assert "我的策略研究" in out

    def test_limit_respected(self, db_path, capsys):
        for i in range(5):
            _seed(db_path, f"sess-{i}", f"t{i}", "c")
        assert cmd_session_list(_args(limit=2)) == 0
        out = capsys.readouterr().out
        assert "共 2 个" in out


class TestSessionShow:
    def test_missing_session_returns_1(self, db_path, capsys):
        assert cmd_session_show(_args(session_id="nope")) == 1
        assert "不存在" in capsys.readouterr().out

    def test_shows_session_and_messages(self, db_path, capsys):
        _seed(db_path, "sess-x", "详情会话", "第一条消息")
        assert cmd_session_show(_args(session_id="sess-x")) == 0
        out = capsys.readouterr().out
        assert "sess-x" in out
        assert "详情会话" in out
        assert "第一条消息" in out


class TestSessionSearch:
    def test_no_hits(self, db_path, capsys):
        _seed(db_path, "s1", "t", "普通内容")
        assert cmd_session_search(_args(query="不存在的词xyz", limit=20)) == 0
        assert "未找到匹配" in capsys.readouterr().out

    def test_hits_printed(self, db_path, capsys):
        _seed(db_path, "s1", "t", "动量因子分析结论")
        assert cmd_session_search(_args(query="动量因子", limit=20)) == 0
        out = capsys.readouterr().out
        assert "动量因子分析结论" in out


class TestSessionDelete:
    def test_delete_prints_soft_delete_note(self, db_path, capsys):
        _seed(db_path, "del-1", "t", "c")
        assert cmd_session_delete(_args(session_id="del-1")) == 0
        out = capsys.readouterr().out
        assert "已删除会话" in out
        assert "软删" in out, "E1 后文案必须如实告知是软删"

    def test_missing_session_returns_1(self, db_path, capsys):
        assert cmd_session_delete(_args(session_id="nope")) == 1
        assert "不存在" in capsys.readouterr().out

    def test_delete_hides_from_list_but_keeps_get(self, db_path):
        """E1 端到端：CLI 删完 → 列表不见 → get_session 仍可读。"""
        _seed(db_path, "del-2", "t", "c")
        assert cmd_session_delete(_args(session_id="del-2")) == 0
        assert cmd_session_list(_args(limit=10)) == 0
        from agentnodes.kernel.session.reader import get_session
        row = get_session("del-2")
        assert row is not None and row["archived"] is True
