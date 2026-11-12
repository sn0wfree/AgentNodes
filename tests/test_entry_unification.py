"""U5.5：入口契约网（AgentLoop.run_stream 是唯一流式入口）。

**验三件事**：

1. ``AgentLoop.run_stream`` 存在且可迭代，产出 delta + done（恰一次）；
2. ``arun`` 与 ``run_stream`` **同源**（一次运行两种消费方式，不是两套逻辑）；
3. **有意非对称**：``run_stream`` 不接受 ``engine`` 参数（legacy 无流式入口）。

**为什么需要**：U5.5 之前，想直接流消费必须绕过 AgentLoop 手工搭 6 个
adapter（test_run_stream_dual_channel 原写法），与生产装配重复——那份
重复正是「装配逻辑散落在测试里」的证据（R-018 的推广：测试基础设施
必须从生产装配导出）。
"""
from __future__ import annotations

import asyncio
import inspect
from collections import Counter
from pathlib import Path

import pytest

from agentnodes.foundation.llm import LLMConfig, LLMResponse, ToolCall
from agentnodes.kernel.agent.builtin_tools import build_default_registry
from agentnodes.kernel.agent.loop import AgentLoop

USAGE = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}


class MockLLM:
    """四入口替身（R-008）。"""

    def __init__(self, text: str = "答案", *, tool_calls=None):
        self.text = text
        self.tool_calls = tool_calls or []
        self.chat_calls = 0

    def chat(self, messages, **kwargs):
        self.chat_calls += 1
        tcs = [ToolCall(**tc) for tc in self.tool_calls]
        return LLMResponse(
            content=self.text, tool_calls=tcs,
            finish_reason="tool_calls" if tcs else "stop", usage=USAGE,
        )

    async def achat(self, messages, **kwargs):
        return self.chat(messages, **kwargs)

    def stream(self, messages, tools=None, **kwargs):
        resp = self.chat(messages, **kwargs)
        from agentnodes.foundation.llm.parser import StreamChunk
        yield StreamChunk(delta_content=resp.content or "", finish_reason="stop")

    async def astream(self, messages, tools=None, **kwargs):
        for chunk in self.stream(messages, tools=tools, **kwargs):
            yield chunk


def _build(tmp_path: Path, mock: MockLLM, *, stream_mode: bool = True):
    loop = AgentLoop(
        stream_mode=stream_mode,
        config=LLMConfig(api_key="sk-test"),
        registry=build_default_registry(),
        workspace=tmp_path,
        trace_dir=tmp_path / "traces",
        strategy={"name": "react", "config": {"max_iterations": 3}},
    )
    loop.client.chat = mock.chat
    loop.client.achat = mock.achat
    loop.client.stream = mock.stream
    loop.client.astream = mock.astream
    return loop


# ── 1. run_stream 存在且可迭代 ──


def test_agent_loop_has_run_stream():
    """AgentLoop 暴露 run_stream（U5.5 新增的公开流式入口）。"""
    assert hasattr(AgentLoop, "run_stream")
    assert inspect.isasyncgenfunction(AgentLoop.run_stream)


def test_run_stream_yields_done_exactly_once(tmp_path: Path):
    """drain 到底：done 恰一次，且有内容事件。"""
    loop = _build(tmp_path, MockLLM("流式答案"))

    async def _drain():
        return [ev async for ev in loop.run_stream("测试任务")]

    evs = asyncio.run(_drain())
    kinds = Counter(e.get("type") for e in evs)
    assert kinds["done"] == 1, f"done 应恰一次：{dict(kinds)}"
    assert kinds.get("text_delta", 0) >= 1, f"应有 delta：{dict(kinds)}"
    assert evs[-1]["type"] == "done", "done 必须是最后一个事件"


def test_run_stream_signature_has_no_engine():
    """**有意非对称**：run_stream 不接受 engine 参数。

    legacy 引擎没有流式入口（事件走 _emit 副作用），暴露 engine 只会
    拿到残缺流——是 API 陷阱。
    """
    sig = inspect.signature(AgentLoop.run_stream)
    assert "engine" not in sig.parameters, (
        "run_stream 不该有 engine 参数（legacy 无流式入口）"
    )
    # 但 arun 必须有（U6 删除前 legacy 仍需可达）
    assert "engine" in inspect.signature(AgentLoop.arun).parameters


# ── 2. arun 与 run_stream 同源 ──


def test_arun_and_run_stream_share_path(tmp_path: Path):
    """arun 内部走 run_stream 的同一实现（spy 验证）。"""
    loop = _build(tmp_path, MockLLM("同源答案"))
    seen: list[str] = []
    original = loop._run_stream_skeleton

    async def _spy(task, context, history):
        seen.append(task)
        async for ev in original(task, context, history):
            yield ev

    loop._run_stream_skeleton = _spy
    r = asyncio.run(loop.arun("测试任务"))
    assert seen == ["测试任务"], f"arun 未走 run_stream 路径：{seen}"
    assert r.answer == "同源答案"


def test_run_and_arun_agree(tmp_path: Path):
    """run() / arun() 走同一引擎（U5 已做，此处回归钉住）。"""
    loop1 = _build(tmp_path / "a", MockLLM("答案A"))
    r1 = loop1.run("任务")
    loop2 = _build(tmp_path / "b", MockLLM("答案A"))
    r2 = asyncio.run(loop2.arun("任务"))
    for key in ("answer", "finished_reason", "iterations", "success"):
        assert getattr(r1, key) == getattr(r2, key), (
            f"{key}: run={getattr(r1, key)!r} arun={getattr(r2, key)!r}"
        )


# ── 3. 内部装配方法被提取（可单独复用/测试）──


def test_skeleton_builders_are_separate():
    """装配逻辑已从 _run_via_skeleton 提取为可复用方法（U5.5 纯重构）。"""
    assert hasattr(AgentLoop, "_build_skeleton")
    assert hasattr(AgentLoop, "_build_skeleton_spec")
    # _run_via_skeleton 仍是薄包装（不再重复装配）
    src = inspect.getsource(AgentLoop._run_via_skeleton)
    assert "_stream_skeleton" in src or "run_stream" in src, (
        "_run_via_skeleton 应是薄包装，不再重复装配逻辑"
    )
    assert "ReActSkeleton(" not in src, (
        "_run_via_skeleton 不应再直接构造 ReActSkeleton（已提取到 _build_skeleton）"
    )
