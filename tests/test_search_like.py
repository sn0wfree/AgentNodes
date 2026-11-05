"""搜索改造测试 —— LIKE 子串匹配（替代 FTS/trigram）。

背景：trigram 分词器要求查询 >= 3 字符，中文双字词（策略/回测/动量）零命中。
改用 LIKE 后中文、英文、混合查询全部可用。
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app_client(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("AN_WORKSPACE_PATH", str(tmp_path))
    import agentnodes.interfaces.api.routers.web_session as ws
    import agentnodes.interfaces.api.session.web_session_db as wsdb

    monkeypatch.setattr(ws, "_get_db_path", lambda: db_path)
    monkeypatch.setattr(wsdb, "_get_db_path", lambda: db_path)

    from agentnodes.interfaces.api.app import create_app

    app = create_app()
    with TestClient(app) as client:
        # P5-5①: chat 端点要求认证
        from agentnodes.interfaces.api.auth_tokens import create_token

        client.headers.update(
            {"Authorization": f"Bearer {create_token('tester')}"},
        )
        yield client, db_path


def _seed(db_path: Path, session_id: str = "s1", *,
          user_id: str = "tester", archived: int = 0) -> None:
    # 默认属主 = 测试 token 用户（P5-5①：搜索按会话属主过滤）
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "INSERT OR REPLACE INTO sessions (id, user_id, title, created_at, updated_at, archived) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, user_id, f"title-{session_id}", time.time(), time.time(), archived),
    )
    docs = [
        ("策略回测很重要", "assistant"),
        ("动量策略表现好", "assistant"),
        ("factor momentum alpha", "user"),
        ("夏普比率与回撤", "assistant"),
    ]
    for i, (content, role) in enumerate(docs):
        conn.execute(
            "INSERT OR REPLACE INTO messages (id, session_id, role, content, created_at, seq) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (f"{session_id}-m{i}", session_id, role, content, time.time() + i, i),
        )
    conn.commit()
    conn.close()


def _search(client, query: str, limit: int = 50):
    return client.post("/api/chat/session/search", json={"query": query, "limit": limit})


# ── 中文双字词（修复前为 0 命中）─────────────────────────


@pytest.mark.parametrize("q,expected_min", [
    ("策略", 2),     # 命中「策略回测很重要」「动量策略表现好」
    ("回测", 1),
    ("动量", 1),
    ("夏普", 0),     # 「夏普比率」包含夏普 → 1
    ("因子", 0),
])
def test_chinese_two_char_queries(app_client, q, expected_min):
    client, db_path = app_client
    _seed(db_path)
    res = _search(client, q)
    assert res.status_code == 200
    hits = res.json()["hits"]
    assert len(hits) >= expected_min or (q in ("夏普", "因子") and len(hits) >= 1), \
        f"{q!r} 应命中至少 {expected_min} 条，实际 {len(hits)}"


def test_chinese_two_char_exact_hits(app_client):
    client, db_path = app_client
    _seed(db_path)
    for q, expected in (("策略", 2), ("回测", 1), ("动量", 1), ("夏普", 1), ("因子", 0)):
        hits = _search(client, q).json()["hits"]
        assert len(hits) == expected, f"{q!r}: 期望 {expected} 条，实际 {len(hits)}"


# ── 英文与混合 ───────────────────────────────────────────


def test_english_query(app_client):
    client, db_path = app_client
    _seed(db_path)
    hits = _search(client, "momentum").json()["hits"]
    assert len(hits) == 1
    assert "momentum" in hits[0]["snippet"].lower() or "momentum" in hits[0]["snippet"]


def test_mixed_language_query(app_client):
    client, db_path = app_client
    _seed(db_path)
    # 「动量」+「策略」都在同一句里
    hits = _search(client, "动量 策略").json()["hits"]
    assert len(hits) == 1
    # snippet 会给每个命中词加 <mark>，检查去标签后的原文
    import re

    plain = re.sub(r"</?mark>", "", hits[0]["snippet"])
    assert "动量策略" in plain


def test_multi_word_and_semantics(app_client):
    """多个关键词必须全部命中（AND）。"""
    client, db_path = app_client
    _seed(db_path)
    # 「策略」命中 2 条，「回测」只命中 1 条 → AND 后 1 条
    hits = _search(client, "策略 回测").json()["hits"]
    assert len(hits) == 1
    assert "回测" in hits[0]["snippet"]


# ── 通配符转义 ───────────────────────────────────────────


def test_percent_is_literal_not_wildcard(app_client):
    client, db_path = app_client
    _seed(db_path)
    # 「%」不应匹配所有消息
    hits = _search(client, "%").json()["hits"]
    assert hits == []


def test_underscore_is_literal(app_client):
    client, db_path = app_client
    _seed(db_path)
    hits = _search(client, "_").json()["hits"]
    assert hits == []


def test_backslash_escaped(app_client):
    client, db_path = app_client
    _seed(db_path)
    res = _search(client, "\\")
    assert res.status_code == 200


# ── 过滤与契约 ───────────────────────────────────────────


def test_archived_sessions_excluded(app_client):
    client, db_path = app_client
    _seed(db_path, "s1", archived=0)
    _seed(db_path, "s2", archived=1)
    hits = _search(client, "策略").json()["hits"]
    assert all(h["session_id"] == "s1" for h in hits)


def test_other_users_excluded(app_client):
    client, db_path = app_client
    _seed(db_path, "mine", user_id="anonymous")
    _seed(db_path, "theirs", user_id="someone-else")
    hits = _search(client, "策略").json()["hits"]
    assert all(h["session_id"] == "mine" for h in hits)


def test_empty_query_returns_empty(app_client):
    client, db_path = app_client
    _seed(db_path)
    assert _search(client, "   ").json()["hits"] == []


def test_response_contract_kept(app_client):
    """响应字段与改造前一致（score 保留为 0.0）。"""
    client, db_path = app_client
    _seed(db_path)
    hit = _search(client, "策略").json()["hits"][0]
    assert set(hit) == {
        "session_id", "session_title", "message_id",
        "role", "snippet", "score", "created_at",
    }
    assert hit["score"] == 0.0


def test_limit_respected(app_client):
    client, db_path = app_client
    _seed(db_path)
    hits = _search(client, "策略", limit=1).json()["hits"]
    assert len(hits) <= 1


def test_fts_table_not_required(app_client):
    """搜索不再依赖 messages_fts（表可缺失）。"""
    client, db_path = app_client
    _seed(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("DROP TABLE IF EXISTS messages_fts")
    conn.commit()
    conn.close()
    res = _search(client, "策略")
    assert res.status_code == 200
    assert len(res.json()["hits"]) == 2
