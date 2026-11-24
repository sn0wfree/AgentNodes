# -*- coding: utf-8 -*-
"""H0 · C-1 契约：token 用量事件经生产 chat bridge 到达 SSE。

实测总量到不了前端，需先确认「事件是否真的过了 bridge」这一层。
本测试直接驱动 `apps/chat/send_turn.py::_event_bridge`（生产路径的非
turned-off seam），断言 `llm_usage` / `session_total_tokens` 被 publish。
"""
from __future__ import annotations

from typing import Any

from agentnodes.apps.chat.send_turn import _event_bridge


class _FakeGateway:
    def __init__(self):
        self.sinks: list[Any] = []

    def add_sink(self, sink):
        self.sinks.append(sink)
        return lambda: self.sinks.remove(sink)


class _Deps:
    def __init__(self, pushed: list):
        self.pushed = pushed

    def publish_sse(self, kind: str, data: dict, session_id: str) -> None:
        self.pushed.append((session_id, kind, data))


def _make_bridge(pushed):
    gw = _FakeGateway()
    deps = _Deps(pushed)
    close = _event_bridge(
        gw,
        deps=deps,  # type: ignore[arg-type]
        session_id="sess-1",
        assistant_mid_holder={},
        event_correlation_id="corr-1",
        terminal_observer=None,
        seed_user=None,
        persist=False,
    )
    return gw, close


def test_llm_usage_reaches_publish_sse():
    """llm_usage 必须在生产 bridge 的直通集合内（C-1 修复目标）。"""
    pushed: list = []
    gw, close = _make_bridge(pushed)
    assert len(gw.sinks) == 1

    gw.sinks[0]("llm_usage", {
        "event_correlation_id": "corr-1",
        "input_tokens": 100,
        "output_tokens": 50,
        "total_tokens": 150,
    })

    kinds = [k for _, k, _ in pushed]
    assert "llm_usage" in kinds, f"llm_usage 未过 bridge；实际推送: {kinds}"
    payload = next(d for _, k, d in pushed if k == "llm_usage")
    assert payload["input_tokens"] == 100
    assert "event_correlation_id" not in payload
    close()


def test_session_total_tokens_reaches_publish_sse():
    """session_total_tokens（权威累计 + context_used）同样必须过 bridge。"""
    pushed: list = []
    gw, close = _make_bridge(pushed)

    gw.sinks[0]("session_total_tokens", {
        "event_correlation_id": "corr-1",
        "total_tokens": 900,
        "context_used": 450,
    })

    kinds = [k for _, k, _ in pushed]
    assert "session_total_tokens" in kinds, f"未过 bridge；实际: {kinds}"
    payload = next(d for _, k, d in pushed if k == "session_total_tokens")
    assert payload["context_used"] == 450
    close()
