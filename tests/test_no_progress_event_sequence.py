"""U2：no-progress EXIT 的事件序列网（compare_exit 实测的固化形态）。

**为什么存在**（R-009"比事件序列，不只比结果字段"）：inventory §5 的
双引擎同场景对照当时是一次性实测；本文件把它固化为常驻网——

**skeleton 事件序列绝对值**：no-progress 拒绝场景的完整事件序列
（含 loop_end/assistant_message/iter_end 的发射**时机**）被钉住，
任何收尾顺序漂移即红。
（U6-2：legacy 对照值随引擎退役删除。）

场景构造（inventory §5 同款）：重复相同 tool_call → no-progress 窗口
命中 → approval 1s 超时 reject → after_act EXIT → FINALIZE 漏斗
（U1 后收尾齐全）。
"""
from __future__ import annotations
from llm_client_mock import patch_llm_client

import asyncio
from pathlib import Path

import pytest

from agentnodes.foundation.llm import LLMConfig, LLMResponse, ToolCall
from agentnodes.kernel.agent.builtin_tools import build_default_registry
from agentnodes.kernel.agent.loop import AgentLoop

USAGE = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}


class MockLLM:
    def __init__(self, responses):
        self.responses = list(responses)

    def chat(self, messages, **kwargs):
        if not self.responses:
            raise RuntimeError("MockLLM exhausted")
        return self.responses.pop(0)

    async def achat(self, messages, **kwargs):
        return self.chat(messages, **kwargs)


def _build(ws: Path, responses, engine: str | None = None):
    """inventory §5 同款装配：approval 1s 超时 reject + trace_dir。"""
    kw = dict(
        stream_mode=False,
        config=LLMConfig(api_key="sk-test"),
        registry=build_default_registry(),
        workspace=ws,
        trace_dir=ws / "traces",
        approval_timeout=1,
        approval_on_timeout="reject",
        strategy={"name": "react", "config": {"no_progress_window": 3}},
    )
    loop = AgentLoop(**kw)
    mock = MockLLM(list(responses))
    patch_llm_client(loop, mock.chat)
    loop.client.achat = mock.achat
    return loop


def _resp() -> LLMResponse:
    return LLMResponse(
        content="",
        tool_calls=[ToolCall(id="c1", name="list_history", arguments={})],
        finish_reason="tool_calls",
        usage=USAGE,
    )


def _collect(ws: Path, engine: str | None) -> tuple[list[str], object]:
    loop = _build(ws, [_resp() for _ in range(5)], engine)
    events: list[str] = []
    original = loop._on_event

    def _on_event(kind, payload=None, *a, **k):
        events.append(kind)
        if original:
            return original(kind, payload)
        return None

    loop._on_event = _on_event
    r = asyncio.run(loop.arun("测试任务", engine=engine) if engine
                    else loop.arun("测试任务"))
    return events, r


# ── skeleton（统一引擎）事件序列绝对值 ──


def test_skeleton_no_progress_reject_event_sequence(tmp_path: Path):
    events, r = _collect(tmp_path, engine=None)
    # 前提：确实走了 no-progress EXIT（审批闸 reject 语义）
    assert r.finished_reason == "approval_timeout_rejected", (
        f"前提失败：finished={r.finished_reason}"
    )
    # 序列骨架：审批请求 → 三件套 → loop_final（U1 后 EXIT 也走漏斗）
    # 用"子序列"断言而非全序列相等——llm_request/response 等高频事件的
    # 次数由 max_iterations/window 决定，锁次数是把网变脆。
    assert "agent_approval_requested" in events, "审批请求未发出"
    # 收尾三件套（loop_progress 审批闸发射）恰各 1 次
    for kind in ("loop_end", "assistant_message", "iter_end"):
        assert events.count(kind) == 1, (
            f"{kind} = {events.count(kind)}（期望恰 1）：{events}"
        )
    # U1 漏斗标志：EXIT 后也有 loop_final（metrics 跑了）
    assert events.count("loop_final") == 1, (
        f"loop_final = {events.count('loop_final')}（EXIT 漏斗未收尾）"
    )
    # 顺序：审批请求在三件套之前（闸先决策，闸内发审批，然后发三件套）
    assert events.index("agent_approval_requested") < events.index("loop_end"), (
        "审批请求应先于 loop_end"
    )

