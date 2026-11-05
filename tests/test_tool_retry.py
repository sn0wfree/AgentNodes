"""工具级自动重试：同步与异步路径行为一致。

loop.py 对 transient 错误（ValueError/TypeError/KeyError/ConnectionError/
TimeoutError/OSError/IOError）最多重试 _TOOL_MAX_RETRIES 次；其他异常
立即失败；重试耗尽返回带 hint 的错误 JSON。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentnodes.kernel.agent import loop as loop_mod
from agentnodes.kernel.agent.loop import AgentLoop, LoopResult
from agentnodes.kernel.agent.tools import BaseTool, ToolRegistry
from agentnodes.foundation.llm import LLMConfig, ToolCall


class FlakyTool(BaseTool):
    """前 fail_times 次抛 exc_cls，之后返回 ok。"""

    name = "flaky"

    def __init__(self, fail_times: int = 1, exc_cls: type[Exception] = ValueError):
        self.fail_times = fail_times
        self.exc_cls = exc_cls
        self.calls = 0

    def execute(self, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc_cls("transient failure")
        return json.dumps({"status": "ok", "value": 42})


@pytest.fixture
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(FlakyTool())
    return reg


@pytest.fixture
def loop(registry: ToolRegistry, tmp_path: Path) -> AgentLoop:
    return AgentLoop(
        stream_mode=False,
        config=LLMConfig(api_key="sk-test"),
        registry=registry,
        workspace=tmp_path,
    )


@pytest.fixture
def no_delay(monkeypatch):
    monkeypatch.setattr(loop_mod, "_TOOL_RETRY_DELAY", 0.0)


def _tc() -> ToolCall:
    return ToolCall(id="c1", name="flaky", arguments={})


# ── Sync path (_execute_tool_call) ───────────────────────────────────


class TestSyncRetry:

    def test_retries_transient_then_succeeds(self, loop, no_delay):
        tool = loop.registry.get("flaky")
        tool.fail_times = 1
        out = loop._execute_tool_call(_tc(), LoopResult())
        assert tool.calls == 2
        assert json.loads(out["content"])["status"] == "ok"

    def test_no_retry_on_non_transient(self, loop):
        tool = loop.registry.get("flaky")
        tool.exc_cls = RuntimeError
        out = loop._execute_tool_call(_tc(), LoopResult())
        assert tool.calls == 1
        err = json.loads(out["content"])
        assert err["status"] == "error"
        assert "RuntimeError" in err["error"]

    def test_retries_exhausted_returns_hint(self, loop, no_delay):
        tool = loop.registry.get("flaky")
        tool.fail_times = 10
        out = loop._execute_tool_call(_tc(), LoopResult())
        assert tool.calls == loop_mod._TOOL_MAX_RETRIES
        err = json.loads(out["content"])
        assert err["status"] == "error"
        assert "hint" in err

    def test_no_retry_when_tool_returns_error_json(self, loop, no_delay):
        """err_actionable 返回 JSON 而非抛异常 → 不重试。"""
        tool = loop.registry.get("flaky")
        tool.fail_times = 0
        tool.execute = lambda **kw: json.dumps({"status": "error", "error": "x"})
        out = loop._execute_tool_call(_tc(), LoopResult())
        assert tool.calls == 0  # execute 被替换，直接返回 error JSON
        assert json.loads(out["content"])["status"] == "error"


# ── Async path (_aexecute_tool_call) ─────────────────────────────────


class TestAsyncRetry:

    async def test_retries_transient_then_succeeds(self, loop, no_delay):
        tool = loop.registry.get("flaky")
        tool.fail_times = 1
        out = await loop._aexecute_tool_call(_tc(), LoopResult())
        assert tool.calls == 2
        assert json.loads(out["content"])["status"] == "ok"

    async def test_no_retry_on_non_transient(self, loop):
        tool = loop.registry.get("flaky")
        tool.exc_cls = RuntimeError
        out = await loop._aexecute_tool_call(_tc(), LoopResult())
        assert tool.calls == 1
        err = json.loads(out["content"])
        assert err["status"] == "error"
        assert "RuntimeError" in err["error"]

    async def test_retries_exhausted_returns_hint(self, loop, no_delay):
        tool = loop.registry.get("flaky")
        tool.fail_times = 10
        out = await loop._aexecute_tool_call(_tc(), LoopResult())
        assert tool.calls == loop_mod._TOOL_MAX_RETRIES
        err = json.loads(out["content"])
        assert err["status"] == "error"
        assert "hint" in err
