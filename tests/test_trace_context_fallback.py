"""统一引擎路径的 trace_id / session_id 兜底（U5.5 之后）。

**背景**：兜底原在 ``_run_loop_core``（legacy 引擎）内部——统一引擎
（``LoopEngine``）走不到，于是**生产主路径的日志与事件没有 request 级
标识**（``event_store.py:167`` 注入时拿到 None）。走 API 的路径由
``session/service.py:601`` 先设了 ``attempt_id``，所以此前没被发现；
直接用 ``AgentLoop`` 的调用则缺失。

**修法**：兜底移到 ``AgentLoop.arun`` 入口（U5.5 统一为唯一入口，
``run()`` 经 ``arun`` 走同一路径）。

**语义**（与 legacy 旧行为一致）：**只在未设置时补**——不覆盖调用方
已设的值（API 层的 attempt_id 优先）。

**断言视角（重要）**：ContextVar 的 ``set`` 在协程内只改**子 context**，
调用方（主上下文）读到的仍是原值。所以断言必须从**循环内部**读——
事件注入点（``event_store.py:167``）与日志格式化器（``logging_setup.py:107``）
读的正是循环内部那个 context，这正是兜底的目的。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentnodes.foundation.llm import LLMConfig, LLMResponse
from agentnodes.foundation.observability.trace import _session_id, _trace_id
from agentnodes.kernel.agent.builtin_tools import build_default_registry
from agentnodes.kernel.agent.loop.core import AgentLoop


class _Mock:
    def chat(self, messages, **kw):
        return LLMResponse(content="x", tool_calls=[], finish_reason="stop")

    async def achat(self, messages, **kw):
        return self.chat(messages, **kw)

    async def astream(self, messages, tools=None, **kw):
        from agentnodes.foundation.llm.parser import StreamChunk
        yield StreamChunk(delta_content="x", finish_reason="stop")


def _make_loop(tmp_path: Path, *, session_id: str = "") -> AgentLoop:
    loop = AgentLoop(
        stream_mode=False,
        config=LLMConfig(api_key="sk-test"),
        registry=build_default_registry(),
        workspace=tmp_path,
        session_id=session_id,
        strategy={"name": "react", "config": {"max_iterations": 2}},
    )
    m = _Mock()
    loop.client.chat = m.chat
    loop.client.achat = m.achat
    loop.client.astream = m.astream
    return loop


@pytest.fixture(autouse=True)
def _clean_trace_vars():
    """每个测试前后清 ContextVar（否则跨测试泄漏）。"""
    t = _trace_id.set(None)
    s = _session_id.set(None)
    yield
    _trace_id.reset(t)
    _session_id.reset(s)


def _run_and_capture(loop: AgentLoop, **kw):
    """跑一次，并**在循环内部**捕获 ContextVar 的值。

    事件注入点与日志格式化器读的就是这个 context——所以这是
    「生产路径有没有 trace_id」的真实判据。
    """
    seen: dict[str, str | None] = {}

    def _spy(kind, payload=None, *a, **k):
        seen.setdefault("trace_id", _trace_id.get())
        seen.setdefault("session_id", _session_id.get())

    loop._on_event = _spy
    asyncio.run(loop.arun("测试", **kw))
    return seen


def test_unified_engine_sets_trace_id(tmp_path: Path):
    """统一引擎路径上 trace_id 不再是 None（U5.5 之后的默认路径）。"""
    loop = _make_loop(tmp_path)
    seen = _run_and_capture(loop)
    tid = seen.get("trace_id")
    assert tid is not None, "统一引擎路径未设置 trace_id"
    assert len(tid) == 12, f"trace_id 应为 12 字符：{tid!r}"
    int(tid, 16)  # 必须是 hex


def test_unified_engine_sets_session_id(tmp_path: Path):
    """session_id 也一并兜底（原先同样只在 legacy 体内）。"""
    loop = _make_loop(tmp_path, session_id="sess-xyz")
    seen = _run_and_capture(loop)
    assert seen.get("session_id") == "sess-xyz"


def test_preset_trace_id_not_overwritten(tmp_path: Path):
    """**不覆盖**调用方已设的值——API 层的 attempt_id 优先。"""
    _trace_id.set("PRESET123456")
    loop = _make_loop(tmp_path)
    seen = _run_and_capture(loop)
    assert seen.get("trace_id") == "PRESET123456", "兜底覆盖了调用方已设的 trace_id"


def test_run_sync_path_also_sets_trace_id(tmp_path: Path):
    """sync 的 run() 也覆盖（经 arun 同一入口）。"""
    loop = _make_loop(tmp_path)
    seen: dict[str, str | None] = {}

    def _spy(kind, payload=None, *a, **k):
        seen.setdefault("trace_id", _trace_id.get())

    loop._on_event = _spy
    loop.run("测试")
    assert seen.get("trace_id") is not None, "sync 路径未设置 trace_id"


def test_legacy_path_still_gets_trace_id(tmp_path: Path):
    """legacy 路径（arun(engine='legacy')）也覆盖——兜底在入口，两条路径共用。"""
    loop = _make_loop(tmp_path)
    seen = _run_and_capture(loop, engine="legacy")
    assert seen.get("trace_id") is not None, "legacy 路径未设置 trace_id"
