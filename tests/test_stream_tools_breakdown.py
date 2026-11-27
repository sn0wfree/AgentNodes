# -*- coding: utf-8 -*-
"""流式路径 tools 传参回归（§9 复核修复 1）。

bug：core.py 的 `_get_response` 流式分支曾硬编码 `tools=None` 调
`_trace_llm_request`，导致 `context.breakdown` 工具三段恒为 0
——与"模型确实能调用工具"自相矛盾。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_stream_loop():
    from agentnodes.kernel.agent.loop.core import AgentLoop

    loop = object.__new__(AgentLoop)
    loop._stream_mode = True
    loop._emit = MagicMock()
    loop.session_id = "s-test"

    tools = [{
        "name": "search",
        "description": "搜索网页" * 20,
        "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
    }]
    loop._effective_tools = MagicMock(return_value=tools)
    loop._trace_llm_request = MagicMock()
    loop._astream_chat = AsyncMock(return_value="ok")
    loop._with_iteration_timeout = AsyncMock(return_value="ok")
    loop.client = MagicMock()
    return loop, tools


@pytest.mark.asyncio
async def test_stream_branch_passes_real_tools_to_trace():
    loop, tools = _make_stream_loop()

    await loop._get_response(
        messages=[{"role": "user", "content": "hi"}],
        iteration=1,
        async_mode=True,
        hook_ctx=SimpleNamespace(),
        result=MagicMock(),
    )

    args = loop._trace_llm_request.call_args
    assert args is not None, "stream 分支没有调用 _trace_llm_request"
    # 源码用关键字参数 tools=tools
    assert args.kwargs.get("tools") == tools, "stream 分支传了 tools=None（bug 回归）"
    loop._effective_tools.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_stream_branch_breakdown_has_tool_segments():
    """端到端契约：stream 分支触发的 breakdown 工具段 > 0。"""
    from agentnodes.kernel.agent.telemetry.agent_telemetry import trace_llm_request

    loop, tools = _make_stream_loop()
    loop._trace_llm_request = lambda messages, iteration, tools: trace_llm_request(
        loop, messages, iteration, tools
    )

    await loop._get_response(
        messages=[{"role": "user", "content": "hi"}],
        iteration=1,
        async_mode=True,
        hook_ctx=SimpleNamespace(),
        result=MagicMock(),
    )

    kinds = [c[0][0] for c in loop._emit.call_args_list]
    assert "context.breakdown" in kinds
    breakdown = next(
        c[0][1] for c in loop._emit.call_args_list
        if c[0][0] == "context.breakdown"
    )
    segs = breakdown["segments"]
    assert segs["tool_prompt"] > 0
    assert segs["system_tool_schemas"] > 0
    assert segs["mcp_tool_schemas"] == 0
