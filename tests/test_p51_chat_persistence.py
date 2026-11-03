"""P5-1 验收：Gateway chat 补齐历史 / 持久化 / message_id。

依据 docs/p5-1-chat-gateway-parity-design-20261027.md §5（执行等价）。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


class FakeResp:
    def __init__(self, content: str, usage: dict | None = None) -> None:
        self.content = content
        usage = usage or {"total_tokens": 3, "prompt_tokens": 2, "completion_tokens": 1}
        self.usage = usage
        self.tool_calls: list = []

    def has_tool_calls(self) -> bool:
        return False


class FakeClient:
    """记录每次收到的 messages，用于断言历史注入。"""

    def __init__(self, reply: str = "ok-reply") -> None:
        self.reply = reply
        self.calls: list[list[dict]] = []

    def chat(self, messages, tools=None, **kwargs):
        self.calls.append([dict(m) for m in messages])
        return FakeResp(self.reply)


@pytest.fixture()
def env(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("SR_WORKSPACE_PATH", str(tmp_path))

    import strategy_research.api.routers.web_session as ws
    import strategy_research.api.session.web_session_db as wsdb

    monkeypatch.setattr(ws, "_get_db_path", lambda: db_path)
    monkeypatch.setattr(wsdb, "_get_db_path", lambda: db_path)

    from strategy_research.kernel.gateway import reset_gateway

    reset_gateway()

    client = FakeClient()
    from strategy_research.api.app import create_app

    app = create_app()

    # 让 Gateway 单例用 FakeClient（构造时注入）
    from strategy_research.kernel.gateway.singleton import get_gateway

    get_gateway(client=client)

    with TestClient(app) as tc:
        # P5-5①: /api/chat/ 不再公开——测试带认证头
        from strategy_research.api.auth_tokens import create_token

        tc.headers.update({"Authorization": f"Bearer {create_token('tester')}"})
        # 建会话
        tc.post("/api/chat/session", json={"id": "sess-1", "title": "t"})
        yield tc, db_path, client

    reset_gateway()


def _rows(db_path: Path, session_id: str = "sess-1") -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, role, content FROM messages WHERE session_id = ? ORDER BY created_at",
        (session_id,),
    ).fetchall()
    conn.close()
    return rows


def test_send_persists_two_messages(env):
    tc, db_path, client = env
    res = tc.post("/api/chat/send", json={"session_id": "sess-1", "content": "你好"})
    assert res.status_code == 200, res.text
    rows = _rows(db_path)
    assert len(rows) == 2, f"应写 user+assistant 两行，实际 {len(rows)}"
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert rows[0]["content"] == "你好"
    assert rows[1]["content"] == "ok-reply"


def test_response_carries_message_id(env):
    tc, db_path, _ = env
    res = tc.post("/api/chat/send", json={"session_id": "sess-1", "content": "hi"})
    body = res.json()
    assert body["message_id"], "message_id 不应为 None"
    assert body["reply"] == "ok-reply"
    ids = {r["id"] for r in _rows(db_path)}
    assert body["message_id"] in ids


def test_second_turn_injects_history(env):
    tc, db_path, client = env
    tc.post("/api/chat/send", json={"session_id": "sess-1", "content": "第一轮"})
    tc.post("/api/chat/send", json={"session_id": "sess-1", "content": "第二轮"})

    assert len(client.calls) >= 2
    second = client.calls[-1]
    contents = [m["content"] for m in second]
    # 第二轮应看到第一轮的用户输入（历史注入生效）
    assert "第一轮" in contents, f"历史缺失: {contents}"
    assert contents[-1] == "第二轮", "当前输入应在最后"


def test_history_does_not_duplicate_current_turn(env):
    """先读历史再 emit：当前轮不应出现两次。"""
    tc, db_path, client = env
    tc.post("/api/chat/send", json={"session_id": "sess-1", "content": "唯一一句"})
    contents = [m["content"] for m in client.calls[-1]]
    assert contents.count("唯一一句") == 1, f"当前轮重复: {contents}"


def test_multi_turn_history_accumulates(env):
    tc, db_path, client = env
    for text in ("A1", "A2", "A3"):
        tc.post("/api/chat/send", json={"session_id": "sess-1", "content": text})
    rows = _rows(db_path)
    assert len(rows) == 6, f"3 轮应 6 行，实际 {len(rows)}"
    last_call_contents = [m["content"] for m in client.calls[-1]]
    # 第三轮的 prompt 应包含前两轮
    assert "A1" in last_call_contents and "A2" in last_call_contents
    assert last_call_contents[-1] == "A3"


def test_events_written_to_event_log(env):
    """只发事件的路径：event_log 应有本轮事件（单一写入者 = projector）。"""
    tc, db_path, _ = env
    tc.post("/api/chat/send", json={"session_id": "sess-1", "content": "事件测试"})
    conn = sqlite3.connect(str(db_path))
    kinds = {
        r[0] for r in conn.execute(
            "SELECT type FROM event_log WHERE aggregate_id = 'sess-1'"
        )
    }
    conn.close()
    assert "message_received" in kinds
    assert "assistant_message" in kinds


def test_engine_failure_writes_nothing(env, monkeypatch):
    tc, db_path, client = env

    def boom(messages, tools=None, **kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr(client, "chat", boom)
    res = tc.post("/api/chat/send", json={"session_id": "sess-1", "content": "失败"})
    assert res.status_code == 502
    assert _rows(db_path) == [], "失败路径不应写消息"


def test_idempotent_no_duplicate_rows(env):
    """重复调用不会产生重复行（同 id UPSERT）。"""
    tc, db_path, _ = env
    tc.post("/api/chat/send", json={"session_id": "sess-1", "content": "x"})
    first = len(_rows(db_path))
    # 直接再发同样内容（新 id，应新增 2 行，不是 4 行）
    tc.post("/api/chat/send", json={"session_id": "sess-1", "content": "x"})
    assert len(_rows(db_path)) == first + 2


def test_controller_ignores_malformed_history():
    """非法 history 被过滤，不影响对话。"""
    from strategy_research.kernel.gateway.session_controller import SessionController
    from strategy_research.kernel.orchestration import EntryPoint, WorkRequest
    from strategy_research.kernel.orchestration.contracts import SourceKind, WorkKind

    ep = EntryPoint(sink=lambda k, d: None)
    ctrl = SessionController(ep, client=FakeClient("r"))
    req = WorkRequest(
        kind=WorkKind.CHAT, source=SourceKind.API_CHAT,
        session_key="chat:s",
        payload={"text": "q", "history": ["not-a-dict", {"role": "user"}, {"role": "user", "content": "ok"}]},
    )
    ep.submit(req)
    results = ctrl.run_pending()
    assert results and results[0].ok
