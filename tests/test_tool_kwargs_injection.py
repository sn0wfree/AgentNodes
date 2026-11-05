"""Verify AgentLoop injects workspace + session_id into tool kwargs."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from agentnodes.kernel.agent.loop import AgentLoop
from agentnodes.kernel.agent.tools import BaseTool, ToolRegistry


class SpyTool(BaseTool):
    name = "spy_probe"
    description = "probe"
    parameters = {"type": "object", "properties": {}, "required": []}
    captured: dict = {}

    def execute(self, **kwargs):
        SpyTool.captured = dict(kwargs)
        return '{"status": "ok"}'


def test_session_and_workspace_injected():
    cfg = MagicMock(api_key="k", model="m", temperature=0, max_tokens=100)
    reg = ToolRegistry()
    reg.register(SpyTool())

    loop = AgentLoop(
        config=cfg, registry=reg,
        workspace=Path("/tmp/ws_probe"), session_id="sess-probe-1",
        system_prompt="x", max_iterations=2,
    )
    # Bypass compaction for the unit test
    loop._maybe_compact = lambda messages: (messages, False)

    tc = MagicMock()
    tc.name = "spy_probe"
    tc.id = "c1"
    tc.arguments = {}

    loop._execute_tool_call(tc, MagicMock())
    # 481 用 resolve() 之后的路径比较；Windows 上 Path("/tmp/x") 会变成
    # 盘符相对路径（D:/tmp/x），直接比字面量会失败。
    ws = SpyTool.captured.get("workspace")
    assert Path(ws).name == "ws_probe" or Path(ws) == Path("/tmp/ws_probe").resolve()
    assert SpyTool.captured.get("session_id") == "sess-probe-1"


def test_explicit_session_wins():
    cfg = MagicMock(api_key="k", model="m", temperature=0, max_tokens=100)
    reg = ToolRegistry()
    reg.register(SpyTool())

    loop = AgentLoop(
        config=cfg, registry=reg,
        workspace=Path("/tmp/ws_probe"), session_id="sess-probe-1",
        system_prompt="x", max_iterations=2,
    )
    loop._maybe_compact = lambda messages: (messages, False)

    tc = MagicMock()
    tc.name = "spy_probe"
    tc.id = "c1"
    tc.arguments = {"session_id": "explicit-sess", "workspace": "/tmp/other"}

    loop._execute_tool_call(tc, MagicMock())
    # LLM-supplied values must NOT be overridden
    assert SpyTool.captured.get("session_id") == "explicit-sess"
    assert SpyTool.captured.get("workspace") == "/tmp/other"
