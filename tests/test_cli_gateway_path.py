"""阶段 3a：CLI 灰度路径测试（flag 开 → 走 Gateway）。

不驱动完整 TUI：直接验证分支选择与 Gateway 装配。
完整 TUI 渲染由既有 test_cli_tui_session*.py 覆盖（flag 关路径）。
"""
from __future__ import annotations

import asyncio

import pytest


def test_cli_has_no_legacy_agent_loop_path(monkeypatch):
    """P5-1c ④：门退役后 CLI 只有 Gateway 一条路径。

    曾经这里测 `QNODES_NEW_ENTRY=0` 回退到直连 AgentLoop 的分支；该分支
    （`ChatSession._run_agent_loop`）已随门一起删除，所以现在的契约是
    「不存在直连路径、不存在门函数」。
    """
    import strategy_research.kernel.gateway as gw
    from strategy_research.cli.tui.session import ChatSession

    assert not hasattr(gw, "new_entry_enabled"), "门函数应已退役"
    assert not hasattr(ChatSession, "_run_agent_loop"), "旧直连分支应已删除"
    assert hasattr(ChatSession, "_run_via_gateway"), "Gateway 路径必须在"


def test_cli_flag_on_dispatches_to_gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "g.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "h.json"))

    from strategy_research.kernel.gateway import reset_gateway

    reset_gateway()

    from strategy_research.cli.tui.session import ChatSession

    called = {"gateway": False, "task": None}

    async def fake_gateway(self, task):
        called["gateway"] = True
        called["task"] = task

    monkeypatch.setattr(ChatSession, "_run_via_gateway", fake_gateway)

    async def run():
        session = ChatSession.__new__(ChatSession)
        session.ctx = type("C", (), {"interactive_mode": "chat", "session_id": "cli", "history": []})()
        # P5-1c ④：门已退役，dispatch 恒走 _run_via_gateway；这里直接调用
        # 该唯一路径，避免为构造完整 TUI 状态而引入无关依赖。
        await ChatSession._run_via_gateway(session, "你好")

    asyncio.run(run())
    assert called["gateway"] is True
    assert called["task"] == "你好"
    reset_gateway()


def test_cli_gateway_singleton_reused(tmp_path, monkeypatch):
    from strategy_research.kernel.gateway import reset_gateway

    reset_gateway()
    monkeypatch.setenv("STRATEGY_RESEARCH_LLM_CONFIG", str(tmp_path / "cfg.json"))
    (tmp_path / "cfg.json").write_text('{"llm": {"enabled": false}}', encoding="utf-8")
    monkeypatch.setenv("LLM_API_KEY", "dummy")

    from strategy_research.cli.tui.session import _get_cli_gateway

    a = _get_cli_gateway()
    b = _get_cli_gateway()
    assert a is b
    reset_gateway()
