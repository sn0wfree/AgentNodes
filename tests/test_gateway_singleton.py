"""阶段 3a 轻量复查：跨入口共享唯一 Gateway（session 串行域）。

背景（复查发现的问题）：三个阶段 3a 入口最初各持一个 Gateway 实例，
同一 session 从不同入口提交会落进不同 mailbox，串行域被打破——
两路 turn 可并发。修正为统一 `get_gateway()` 单例。
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clean():
    from strategy_research.kernel.gateway import reset_gateway

    reset_gateway()
    yield
    reset_gateway()


def test_all_entries_share_one_gateway_instance(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEGY_RESEARCH_LLM_CONFIG", str(tmp_path / "c.json"))
    (tmp_path / "c.json").write_text('{"llm": {"enabled": false}}', encoding="utf-8")
    monkeypatch.setenv("LLM_API_KEY", "dummy")

    from strategy_research.interfaces.api.routers.chat import _get_gateway as router_gw
    from strategy_research.cli.tui.session import _get_cli_gateway as cli_gw
    from strategy_research.apps.study.scheduler import _get_gateway_singleton

    a = router_gw()
    b = cli_gw()
    c = _get_gateway_singleton()
    assert a is b is c
    assert a.entry is b.entry  # 同一 EntryPoint → 同一 mailbox 目录


def test_same_session_key_shares_mailbox_across_entries(tmp_path, monkeypatch):
    """核心断言：同一 session_key 从不同入口提交 → 进同一 mailbox。"""
    monkeypatch.setenv("STRATEGY_RESEARCH_LLM_CONFIG", str(tmp_path / "c.json"))
    (tmp_path / "c.json").write_text('{"llm": {"enabled": false}}', encoding="utf-8")
    monkeypatch.setenv("LLM_API_KEY", "dummy")

    from strategy_research.kernel.gateway import api_chat_request, get_gateway, scheduler_request

    gw = get_gateway()
    # 两个入口都用同一个逻辑 session_key
    gw.entry.submit(api_chat_request("shared", "chat 消息"))
    gw.entry.submit(scheduler_request("shared", {"task": "x"}))

    # api_chat 用 "chat:shared"、scheduler 用 "automation:shared" —— 不同域，
    # 各自一个 mailbox（这是正确语义：不同 domain 不互相阻塞）
    assert gw.entry.mailbox_for("chat:shared") is not None
    assert gw.entry.mailbox_for("automation:shared") is not None
    assert gw.entry.mailbox_for("chat:shared") is not gw.entry.mailbox_for("automation:shared")


def test_same_entry_same_key_is_one_mailbox(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEGY_RESEARCH_LLM_CONFIG", str(tmp_path / "c.json"))
    (tmp_path / "c.json").write_text('{"llm": {"enabled": false}}', encoding="utf-8")
    monkeypatch.setenv("LLM_API_KEY", "dummy")

    from strategy_research.kernel.gateway import api_chat_request, get_gateway

    gw = get_gateway()
    gw.entry.submit(api_chat_request("s", "m1"))
    gw.entry.submit(api_chat_request("s", "m2"))
    mailbox = gw.entry.mailbox_for("chat:s")
    assert mailbox is not None
    assert mailbox.peek_depth() == 2  # 同一队列串行


def test_reset_gateway_creates_fresh_instance(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEGY_RESEARCH_LLM_CONFIG", str(tmp_path / "c.json"))
    (tmp_path / "c.json").write_text('{"llm": {"enabled": false}}', encoding="utf-8")
    monkeypatch.setenv("LLM_API_KEY", "dummy")

    from strategy_research.kernel.gateway import get_gateway, reset_gateway

    first = get_gateway()
    reset_gateway()
    second = get_gateway()
    assert first is not second
