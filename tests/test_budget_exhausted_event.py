"""U2：budget_exhausted 事件在 skeleton（统一引擎）路径上的补发网。

**为什么存在**（inventory §6 差异 #2）：``budget_exhausted`` 事件只有
legacy 发（loop.py:1119，在预算停机 break 前发射）；skeleton 的预算
停机走 BudgetDecider → STOP → LoopFinalize，**没有任何发射点**——
「因为预算停止」这件事在默认引擎（API/web chat 主路径）上没有任何
可观测信号（V4-1 实测结论，.rules/lessons.md 存档）。

U2 修法：LoopFinalize 在 ``finished_reason == "budget"`` 时补发
``budget_exhausted``（载荷形状与 legacy 一致：reason / tokens_used /
cost_usd / elapsed）。发射点选 LoopFinalize 而非 BudgetDecider 的理由：
decider 拿到的 ctx 没有 result（载荷字段在 result 上），且 FINALIZE 是
统一引擎的单一收尾漏斗（U1 已建立）。

正向前提（R-009）：出口网 test_exit_net 的 budget 场景已证明预算维度
真触发（finished_reason == "budget"）。
"""
from __future__ import annotations

import asyncio
from collections import Counter
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


def _build(ws: Path, responses, extra_cfg):
    loop = AgentLoop(
        stream_mode=False,
        config=LLMConfig(api_key="sk-test"),
        registry=build_default_registry(),
        workspace=ws,
        trace_dir=ws / "traces",
        strategy={"name": "react", "config": dict(extra_cfg)},
    )
    mock = MockLLM(list(responses))
    loop.client.chat = mock.chat
    loop.client.achat = mock.achat
    return loop, mock


def _resp(**kw) -> LLMResponse:
    tc_objs = [ToolCall(id=t["id"], name=t["name"], arguments=t.get("arguments", {}))
               for t in (kw.pop("tool_calls", None) or [])]
    return LLMResponse(content=kw.get("content", ""), tool_calls=tc_objs,
                       finish_reason=kw.get("finish_reason", "stop"), usage=USAGE)


def _run(ws: Path, responses, extra_cfg) -> tuple[object, Counter]:
    events: list[str] = []
    payloads: dict[str, dict] = {}
    loop, _ = _build(ws, responses, extra_cfg or {})
    original = loop._on_event

    def _on_event(kind, payload=None, *a, **k):
        events.append(kind)
        payloads.setdefault(kind, payload or {})
        if original:
            return original(kind, payload)
        return None

    loop._on_event = _on_event
    r = asyncio.run(loop.arun("测试任务"))
    return r, Counter(events), payloads


# ── 正向前提：预算真触发（出口网已钉，这里再钉一次事件语境）──


def test_budget_really_triggers(tmp_path: Path):
    r, c, _ = _run(
        tmp_path,
        [_resp(content="预算内")],
        {"budget_max_tokens": 100},  # 每响应 150 token → 首轮即超
    )
    assert r.finished_reason == "budget", f"前提失败: {r.finished_reason}"


# ── U2 修复后全绿：budget_exhausted 恰发 1 次 ──


def test_budget_exhausted_emitted_once(tmp_path: Path):
    r, c, _ = _run(
        tmp_path,
        [_resp(content="预算内")],
        {"budget_max_tokens": 100},
    )
    assert c["budget_exhausted"] == 1, (
        f"budget_exhausted = {c['budget_exhausted']}（期望恰 1；"
        f"事件: {dict(c)}）"
    )


# ── 载荷形状（与 legacy loop.py:1119 一致）──


def test_budget_exhausted_payload_shape(tmp_path: Path):
    r, c, payloads = _run(
        tmp_path,
        [_resp(content="预算内")],
        {"budget_max_tokens": 100},
    )
    p = payloads.get("budget_exhausted")
    assert p is not None, "budget_exhausted 未发射"
    assert p.get("reason"), f"reason 缺失: {p}"
    assert p.get("tokens_used", 0) > 0, f"tokens_used 应 > 0: {p}"
    assert "cost_usd" in p, f"cost_usd 缺失: {p}"
    assert "elapsed" in p, f"elapsed 缺失: {p}"


# ── 对照：非预算出口不得误发（发射点必须在 budget 分支内）──


def test_budget_exhausted_not_on_other_exits(tmp_path: Path):
    r, c, _ = _run(
        tmp_path,
        [_resp(content="正常终答")],
        None,  # 无预算 → 正常终答
    )
    assert r.finished_reason == "stop"
    assert c["budget_exhausted"] == 0, (
        f"非预算出口误发 budget_exhausted: {c['budget_exhausted']}"
    )
