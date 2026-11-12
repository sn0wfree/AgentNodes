"""U3：run_stream 双通道流式契约（design doc §5 U3 批）。

**为什么存在**：``run_stream`` 是统一引擎的流式接口（业界形状：token +
事件同一契约，survey §2 OpenAI run_streamed / Vercel full-stream 行）。
现状它只 yield 4 处：error ×3 + done ×1——``text_delta``/``thinking_delta``
虽然在 ``_astream_chat`` 里经 ``_emit`` → ``on_event`` 回调发射（chat SSE
在消费），但 **run_stream 生成器本身不产出它们**。骨架被选为唯一引擎后，
``run_stream`` 就是唯一的流式面，必须双通道齐全。

前提事实（U3-a 实测）：
- delta 事件生产已在 skeleton 路径上通（``_get_response`` →
  ``_astream_chat``，与 legacy 同一层）；既有测试
  ``test_arun_stream_mode_emits_text_delta_events`` 钉的就是 on_event 通道；
- 本批把同一事件流**转发**进 run_stream（零新词表，design doc §3.3）。

实现方式：骨架持有 ``pending_events`` 队列，``_emit`` 转发适配器把
delta/生命周期事件压队；run_stream 每个步骤间隙 drain 并 yield。
``stream_mode=False`` 时 ``_astream_chat`` 不会被调用，delta 自然缺失
（与 legacy 一致）——两条负向断言钉住。
"""
from __future__ import annotations
from llm_client_mock import patch_llm_client

import asyncio
from collections import Counter
from pathlib import Path

import pytest

from agentnodes.foundation.llm import LLMConfig, LLMResponse, ToolCall
from agentnodes.kernel.agent.builtin_tools import build_default_registry
from agentnodes.kernel.agent.loop import AgentLoop

USAGE = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}


class StreamingMockLLM:
    """流式替身：yield chunk dict 形态与 openai_client.astream 一致。

    chunk 字段：delta_content / delta_thinking / delta_tool_calls /
    finish_reason / usage / raw_content / raw_thinking。
    """

    def __init__(self, text: str, thinking: str = "", tool_calls: list | None = None):
        self.text = text
        self.thinking = thinking
        self.tool_calls = tool_calls or []

    def chat(self, messages, **kwargs):
        return LLMResponse(
            content=self.text,
            tool_calls=[ToolCall(**tc) for tc in self.tool_calls],
            finish_reason="tool_calls" if self.tool_calls else "stop",
            usage=USAGE,
        )

    async def achat(self, messages, **kwargs):
        return self.chat(messages, **kwargs)

    async def astream(self, messages, tools=None, **kwargs):
        # 分 3 个 chunk：thinking → text1 → text2(+finish)
        if self.thinking:
            yield self._chunk(delta_thinking=self.thinking,
                              finish_reason=None)
        n = len(self.text) or 1
        third = max(1, n // 3)
        parts = [self.text[i:i + third] for i in range(0, n, third)] or [""]
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            yield self._chunk(
                delta_content=part,
                delta_tool_calls=self.tool_calls if (last and self.tool_calls) else None,
                finish_reason=("tool_calls" if (last and self.tool_calls)
                               else ("stop" if last else None)),
            )

    def stream(self, messages, tools=None, **kwargs):
        async def _gen():
            async for c in self.astream(messages, tools=tools, **kwargs):
                yield c
        return _gen()

    @staticmethod
    def _chunk(*, delta_content="", delta_thinking="", delta_tool_calls=None,
               finish_reason=None, raw_content=None, raw_thinking=None):
        return type("Chunk", (), {
            "delta_content": delta_content,
            "delta_thinking": delta_thinking,
            "delta_tool_calls": delta_tool_calls or [],
            "finish_reason": finish_reason,
            "usage": USAGE if finish_reason else None,
            "raw_content": raw_content,
            "raw_thinking": raw_thinking,
        })()


def _build(ws: Path, mock: StreamingMockLLM, *, stream_mode: bool):
    loop = AgentLoop(
        stream_mode=stream_mode,
        config=LLMConfig(api_key="sk-test"),
        registry=build_default_registry(),
        workspace=ws,
        trace_dir=ws / "traces",
        strategy={"name": "react", "config": {"max_iterations": 3}},
    )
    patch_llm_client(loop, mock.chat)
    loop.client.achat = mock.achat
    loop.client.stream = mock.stream
    loop.client.astream = mock.astream
    return loop


def _collect_run_stream(loop: AgentLoop) -> list[dict]:
    """直接 drain 骨架 run_stream（经 AgentLoop._run_via_skeleton 的装配）。

    AgentLoop 没有公开 run_stream 入口（生产走 run_to_completion），
    这里复用 ``_run_via_skeleton`` 的装配逻辑但改走生成器——通过
    monkeypatch ``run_to_completion`` 不可行，直接构造骨架装配。
    """
    from agentnodes.kernel.agent.modes import deciders_for
    from agentnodes.kernel.agent.skeleton import ReActSkeleton, SkeletonRunSpec
    from agentnodes.kernel.agent.skeleton_adapters import (
        LoopActor, LoopFinalize, LoopHooks, LoopIterationStart,
        LoopPrepare, LoopReasoner,
    )

    strategy = loop._strategy
    max_iter = loop._effective_max_iterations()
    deciders = deciders_for(strategy, loop, max_iterations=max_iter)
    skeleton = ReActSkeleton(
        reasoner=LoopReasoner(loop),
        actor=LoopActor(loop),
        deciders=deciders,
        hooks=LoopHooks(loop),
        finalize=LoopFinalize(loop),
        prepare=LoopPrepare(loop),
        on_iteration_start=LoopIterationStart(loop),
        # 与生产装配（loop.py _run_via_skeleton）一致：双通道流式
        event_source=lambda: loop,
    )
    spec = SkeletonRunSpec(task="测试任务", context=None, history=None,
                           max_iterations=max_iter, session_id="")
    return asyncio.run(_drain(skeleton.run_stream(spec)))


async def _drain(gen) -> list[dict]:
    out = []
    async for ev in gen:
        out.append(ev)
    return out


# ── 正向：stream_mode=True → done 之前有 delta ──


def test_run_stream_yields_text_delta_before_done(tmp_path: Path):
    mock = StreamingMockLLM(text="你好世界这是一段流式回答")
    loop = _build(tmp_path, mock, stream_mode=True)
    evs = _collect_run_stream(loop)
    kinds = Counter(e.get("type") for e in evs)
    assert kinds["text_delta"] >= 2, (
        f"stream_mode=True 下 text_delta 应 >= 2（3 chunk 分段），实得 "
        f"{kinds['text_delta']}；全部事件: {[e.get('type') for e in evs]}"
    )
    # 顺序：最后一个 text_delta 在 done 之前
    types = [e.get("type") for e in evs]
    assert types.index("text_delta") < types.index("done"), (
        f"text_delta 必须在 done 之前: {types}"
    )
    assert kinds["done"] == 1
    # thinking 通道：本 mock 无 thinking → 不出现（不算失败）
    assert kinds.get("thinking_start", 0) >= 0


# ── thinking 通道 ──


def test_run_stream_yields_thinking_delta(tmp_path: Path):
    mock = StreamingMockLLM(text="思考后的答案", thinking="让我想想…")
    loop = _build(tmp_path, mock, stream_mode=True)
    evs = _collect_run_stream(loop)
    kinds = Counter(e.get("type") for e in evs)
    assert kinds["thinking_delta"] >= 1, (
        f"thinking_delta 缺失: {[e.get('type') for e in evs]}"
    )
    types = [e.get("type") for e in evs]
    assert types.index("thinking_delta") < types.index("done")


# ── 负向：stream_mode=False → 无 delta ──


def test_run_stream_no_delta_when_not_streaming(tmp_path: Path):
    mock = StreamingMockLLM(text="非流式回答")
    loop = _build(tmp_path, mock, stream_mode=False)
    evs = _collect_run_stream(loop)
    kinds = Counter(e.get("type") for e in evs)
    assert kinds.get("text_delta", 0) == 0, (
        f"stream_mode=False 不应出现 text_delta: {[e.get('type') for e in evs]}"
    )
    assert kinds["done"] == 1


# ── tool_call 场景：delta + tool 流共存（chat 真实形态）──


def test_run_stream_tool_call_with_text(tmp_path: Path):
    mock = StreamingMockLLM(
        text="",
        tool_calls=[{"id": "c1", "name": "list_history", "arguments": {}}],
    )
    loop = _build(tmp_path, mock, stream_mode=True)
    evs = _collect_run_stream(loop)
    types = [e.get("type") for e in evs]
    assert "done" in types, f"必须收尾: {types}"
    # 工具调用不该崩：chunk 带 delta_tool_calls 也能正常 drain 完
    assert types[-1] == "done" or types[-1] == "error", types
