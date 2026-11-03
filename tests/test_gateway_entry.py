"""阶段 3a adapter + Gateway 组装测试（不触碰现有入口，纯新增层）。"""
from __future__ import annotations

import os
from dataclasses import dataclass

import pytest

from strategy_research.core.gateway import (
    Gateway,
    api_chat_request,
    cli_request,
    mcp_request,
    scheduler_request,
)
from strategy_research.core.orchestration import QueueMode, SourceKind, WorkKind


# ── adapter 翻译 ─────────────────────────────────────────


def test_api_chat_request_shape():
    req = api_chat_request("sess-1", "你好")
    assert req.kind is WorkKind.CHAT
    assert req.source is SourceKind.API_CHAT
    assert req.session_key == "chat:sess-1"
    assert req.payload == {"text": "你好"}
    assert req.queue_mode is None  # 用五行默认（api_chat → steer）


def test_cli_request_defaults_to_followup_source():
    req = cli_request("main", "hi")
    assert req.source is SourceKind.CLI
    assert req.session_key == "chat:main"  # 自动补 domain 前缀
    assert req.payload["text"] == "hi"


def test_cli_request_keeps_existing_domain_prefix():
    req = cli_request("study:momentum", "hi")
    assert req.session_key == "study:momentum"


def test_mcp_request_uses_mcp_domain():
    req = mcp_request("client-a", {"q": 1})
    assert req.source is SourceKind.MCP
    assert req.session_key == "mcp:client-a"
    assert req.payload == {"q": 1}


def test_scheduler_request_uses_automation_kind():
    req = scheduler_request("nightly", {"task": "run"})
    assert req.kind is WorkKind.AUTOMATION
    assert req.source is SourceKind.SCHEDULER
    assert req.session_key == "automation:nightly"


def test_adapters_strip_protocol_fields():
    """payload 白名单：adapter 只传业务字段。"""
    req = api_chat_request("s", "x")
    assert set(req.payload) == {"text"}


# ── Gateway 组装 ─────────────────────────────────────────


@dataclass
class FakeResp:
    content: str = "ok"
    usage: dict = None  # type: ignore[assignment]

    def __post_init__(self):
        self.usage = self.usage or {"total_tokens": 1}


class FakeClient:
    def __init__(self, answer: str = "回答了") -> None:
        self.answer = answer
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        return FakeResp(content=self.answer)


def test_gateway_end_to_end():
    gw = Gateway(client=FakeClient("网关回答"))
    receipt = gw.entry.submit(api_chat_request("s1", "问"))
    assert receipt.accepted

    [run] = gw.run_once()
    assert run.ok and run.answer == "网关回答"
    assert run.turn_id.endswith("#1")

    kinds = [k for k, _ in gw.drain_events()]
    assert "turn.admitted" in kinds and "run.settled" in kinds
    # 排空后再次 drain 为空
    assert gw.drain_events() == []


def test_gateway_verify_invariants_passes():
    gw = Gateway(client=FakeClient())
    gw.verify_invariants()  # 不抛即通过


def test_gateway_multiple_sessions_isolated():
    client = FakeClient()
    gw = Gateway(client=client)
    gw.entry.submit(api_chat_request("a", "q1"))
    gw.entry.submit(api_chat_request("b", "q2"))
    runs = gw.run_once()
    assert len(runs) == 2
    session_ids = {r.turn_id.split("#")[0] for r in runs}
    assert len(session_ids) == 2


def test_gateway_engine_factory_injection():
    """A1 前的注入点：自定义引擎（如 react 图）。"""
    from strategy_research.kernel.runtimes import GraphEngine, make_llm_node

    client = FakeClient("注入引擎回答")
    gw = Gateway(client=client, engine_factory=lambda: _build(client))
    gw.entry.submit(api_chat_request("s", "q"))
    [run] = gw.run_once()
    assert run.answer == "注入引擎回答"


def _build(client):
    from strategy_research.kernel.runtimes import GraphEngine, make_llm_node

    engine = GraphEngine()
    engine.add_node("llm", make_llm_node(client))
    return engine
