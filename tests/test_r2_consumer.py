"""R2.1 测试——run.answer 事件、常驻消费者、receipt/wait 契约。

设计：docs/stage-r2-consumer-steer-aging-design-20261030.md（E1=C）
  A. run.answer：成功轮发出、fenced 不发
  B. 常驻消费者：submit 后自驱、wait_turn、优雅停止、幂等启动
  C. persist 桥：run.answer → assistant_message + agent_done；失败路径
  D. API：receipt 契约（消费者在跑）+ wait=true 同步语义 + 无消费者回落
"""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from strategy_research.kernel.gateway import (
    Gateway,
    SessionController,
    SessionFence,
)
from strategy_research.kernel.orchestration import (
    EntryPoint,
    SourceKind,
    WorkKind,
    WorkRequest,
)


# ── 假件 ─────────────────────────────────────────────────


@dataclass
class Resp:
    content: str = "回答"
    usage: dict = field(default_factory=lambda: {"total_tokens": 5})


class FakeClient:
    def __init__(self, answer: str = "回答") -> None:
        self.answer = answer

    def chat(self, messages, **kwargs):
        return Resp(content=self.answer)


class Probe:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def __call__(self, kind, data):
        self.events.append((kind, data))

    def of(self, kind):
        return [d for k, d in self.events if k == kind]


def chat(text: str = "q", session_key: str = "chat:s") -> WorkRequest:
    return WorkRequest(
        kind=WorkKind.CHAT, source=SourceKind.CLI,
        session_key=session_key, payload={"text": text},
    )


# ── A. run.answer 事件 ───────────────────────────────────


def _make(fence=None, engine=None, client=None):
    probe = Probe()
    ep = EntryPoint(sink=probe)
    kwargs: dict = dict(client=client or FakeClient(), sink=probe, fence=fence)
    if engine is not None:
        kwargs["engine_factory"] = lambda: engine
    return SessionController(ep, **kwargs), ep, probe


def test_run_answer_emitted_on_success():
    controller, ep, probe = _make()
    ep.submit(chat())
    runs = controller.run_pending()
    assert runs[0].ok
    answers = probe.of("run.answer")
    assert len(answers) == 1
    assert answers[0]["content"] == "回答"
    assert answers[0]["run_id"] == runs[0].run_id
    # 顺序：answer 在 settled 之前
    kinds = [k for k, _ in probe.events]
    assert kinds.index("run.answer") < kinds.index("run.settled")


def test_run_answer_absent_when_fenced():
    fence = SessionFence()

    class SupersedingEngine:
        def run(self, state):
            fence.claim(
                str(state.get("fence_domain") or ""),
                f"{state.get('run_id')}:next",
            )
            return {"answer": "stale"}

    controller, ep, probe = _make(fence=fence, engine=SupersedingEngine())
    ep.submit(chat())
    runs = controller.run_pending()
    assert runs[0].ok is False
    assert probe.of("run.answer") == []


# ── B. 常驻消费者 ────────────────────────────────────────


def test_consumer_drives_submitted_turn():
    from strategy_research.kernel.gateway import Gateway

    gw = Gateway(client=FakeClient())
    assert not gw.consumer_running()
    gw.start_consumer(kinds={WorkKind.CHAT})
    try:
        assert gw.consumer_running()
        gw.entry.submit(chat("自驱", "chat:auto"))
        result = gw.wait_turn(gw.entry.session_id_for("chat:auto"), timeout=5.0)
        assert result is not None and result.ok
        assert result.answer == "回答"
    finally:
        gw.stop_consumer(grace=2.0)
    assert not gw.consumer_running()


def test_start_consumer_is_idempotent():
    from strategy_research.kernel.gateway import Gateway

    gw = Gateway(client=FakeClient())
    gw.start_consumer()
    try:
        first = gw._consumer_thread
        gw.start_consumer()
        assert gw._consumer_thread is first
    finally:
        gw.stop_consumer(grace=1.0)


def test_wait_turn_unknown_session_times_out():
    from strategy_research.kernel.gateway import Gateway

    gw = Gateway(client=FakeClient())
    assert gw.wait_turn("chat:never", timeout=0.05) is None


# ── C. persist 桥 ────────────────────────────────────────


def test_bridge_persist_mode_emits_assistant_message():
    from strategy_research.api.routers.chat import _install_chat_event_bridge

    class FakeEventBus:
        def __init__(self) -> None:
            self.emitted: list[tuple[str, str, dict]] = []

        def emit(self, sid, kind, data):
            self.emitted.append((sid, kind, data))

    class FakeService:
        def __init__(self) -> None:
            self.event_bus = FakeEventBus()

    class FakeGW:
        def __init__(self) -> None:
            self.sinks: list = []

        def install_sink(self, sink):
            self.sinks.append(sink)

    service = FakeService()
    gw = FakeGW()
    holder: dict[str, str] = {}
    _install_chat_event_bridge(
        gw, "sess-9", holder, service=service, persist=True,
    )
    bridge = gw.sinks[-1]
    bridge("text_delta", {"text": "你"})
    bridge("run.answer", {"run_id": "t1:r0", "content": "你好"})
    bridge("run.settled", {"run_id": "t1:r0", "ok": True})

    kinds = [(k, d) for sid, k, d in service.event_bus.emitted]
    assistant = [d for k, d in kinds if k == "assistant_message"]
    assert len(assistant) == 1
    assert assistant[0]["content"] == "你好"
    assert assistant[0]["message_id"] == "t1:r0"
    assert holder["id"] == "t1:r0"


def test_bridge_persist_mode_reports_failure():
    from strategy_research.api.routers import chat as chat_mod

    class FakeGW:
        def install_sink(self, sink):
            self.sink = sink

    pushed: list[tuple[str, dict]] = []
    gw = FakeGW()
    holder: dict[str, str] = {}
    import unittest.mock as mock

    # 拦截 _push_sse 观察（不走真实 SSE 缓冲）
    with mock.patch.object(chat_mod, "_push_sse",
                           lambda kind, data, sid: pushed.append((kind, data))):
        chat_mod._install_chat_event_bridge(
            gw, "sess-x", holder, service=None, persist=True,
        )
        gw.sink("run.failed", {"run_id": "t2:r0", "error": "boom"})

    kinds = [k for k, _ in pushed]
    assert "error" in kinds and "agent_done" in kinds
    done = [d for k, d in pushed if k == "agent_done"][-1]
    assert done["status"] == "error"


# ── D. API receipt / wait 契约 ───────────────────────────


class ApiResp:
    def __init__(self, content: str) -> None:
        self.content = content
        self.usage = {"total_tokens": 3}
        self.tool_calls: list = []

    def has_tool_calls(self) -> bool:
        return False


class ApiClient:
    def __init__(self, reply: str = "ok-reply") -> None:
        self.reply = reply

    def chat(self, messages, tools=None, **kwargs):
        return ApiResp(self.reply)


def _rows(db_path: Path, session_id: str = "sess-r2") -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, role, content FROM messages WHERE session_id = ? ORDER BY created_at",
        (session_id,),
    ).fetchall()
    conn.close()
    return rows


def _wait_for_rows(db_path: Path, want: int, timeout: float = 8.0) -> list[sqlite3.Row]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = _rows(db_path)
        if len(rows) >= want:
            return rows
        time.sleep(0.1)
    return _rows(db_path)


def test_receipt_contract_and_async_persistence(tmp_path, monkeypatch):
    db_path = tmp_path / "r2.db"
    monkeypatch.setenv("SR_WORKSPACE_PATH", str(tmp_path))
    
    import strategy_research.api.routers.web_session as ws
    import strategy_research.api.session.web_session_db as wsdb

    monkeypatch.setattr(ws, "_get_db_path", lambda: db_path)
    monkeypatch.setattr(wsdb, "_get_db_path", lambda: db_path)

    from strategy_research.kernel.gateway import reset_gateway

    reset_gateway()
    from strategy_research.api.app import create_app
    from strategy_research.kernel.gateway.singleton import get_gateway

    get_gateway(client=ApiClient())
    app = create_app()
    with __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(app) as tc:
        from strategy_research.api.auth_tokens import create_token

        tc.headers.update({"Authorization": f"Bearer {create_token('tester')}"})
        tc.post("/api/chat/session", json={"id": "sess-r2", "title": "t"})

        gw = get_gateway()
        gw.start_consumer(kinds={WorkKind.CHAT})
        try:
            # receipt：立即返回，无 reply；答案异步落库
            res = tc.post(
                "/api/chat/send",
                json={"session_id": "sess-r2", "content": "异步"},
            )
            assert res.status_code == 200, res.text
            body = res.json()
            assert body.get("accepted") is True
            assert "reply" not in body
            rows = _wait_for_rows(db_path, want=2)
            assert [r["role"] for r in rows] == ["user", "assistant"]
            assert rows[1]["content"] == "ok-reply"

            # wait=true：同步语义保留（等消费者，不自己驱动）
            res2 = tc.post(
                "/api/chat/send?wait=true",
                json={"session_id": "sess-r2", "content": "同步"},
            )
            assert res2.status_code == 200, res2.text
            body2 = res2.json()
            assert body2.get("reply") == "ok-reply"
            assert body2.get("status") == "completed"
        finally:
            gw.stop_consumer(grace=2.0)
    reset_gateway()


def test_sync_fallback_when_no_consumer(tmp_path, monkeypatch):
    """消费者未跑（现有测试/部署形态）→ 同步契约逐字节保留（等价门）。"""
    db_path = tmp_path / "r2b.db"
    monkeypatch.setenv("SR_WORKSPACE_PATH", str(tmp_path))
    
    import strategy_research.api.routers.web_session as ws
    import strategy_research.api.session.web_session_db as wsdb

    monkeypatch.setattr(ws, "_get_db_path", lambda: db_path)
    monkeypatch.setattr(wsdb, "_get_db_path", lambda: db_path)

    from strategy_research.kernel.gateway import reset_gateway

    reset_gateway()
    from strategy_research.api.app import create_app
    from strategy_research.kernel.gateway.singleton import get_gateway

    get_gateway(client=ApiClient())
    app = create_app()
    from fastapi.testclient import TestClient

    with TestClient(app) as tc:
        from strategy_research.api.auth_tokens import create_token

        tc.headers.update({"Authorization": f"Bearer {create_token('tester')}"})
        tc.post("/api/chat/session", json={"id": "sess-r2", "title": "t"})
        res = tc.post(
            "/api/chat/send",
            json={"session_id": "sess-r2", "content": "你好"},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body.get("reply") == "ok-reply"  # 现契约不变
        assert [r["role"] for r in _rows(db_path)] == ["user", "assistant"]
    reset_gateway()
