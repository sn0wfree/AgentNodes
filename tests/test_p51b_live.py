"""P5-1b 真实验证：新 chat 路径的流式 / 工具 / 历史 / 持久化（真实 MiniMax）。

运行：
    $env:QNODES_LIVE_LLM="1";
    py -3.12 -m pytest tests/test_p51b_live.py -m llm -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.llm

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentnodes.kernel.agent.tools import BaseTool, ToolRegistry  # noqa: E402

requires_live = pytest.mark.skipif(
    os.environ.get("QNODES_LIVE_LLM") != "1",
    reason="live LLM tests are opt-in: set QNODES_LIVE_LLM=1",
)


class CalculatorTool(BaseTool):
    name = "calculator"
    description = "计算四则运算表达式。参数 expr：如 '17*23'，只支持数字与 + - * / ( )"
    parameters = {
        "type": "object",
        "properties": {"expr": {"type": "string", "description": "四则运算表达式"}},
        "required": ["expr"],
    }
    _ALLOWED = set("0123456789+-*/(). ")

    def execute(self, *, expr: str = "", **_: Any) -> str:
        cleaned = expr.replace("×", "*").replace("÷", "/").strip()
        if not cleaned or set(cleaned) - self._ALLOWED:
            return '{"error": "表达式包含非法字符"}'
        try:
            result = eval(cleaned, {"__builtins__": {}}, {})  # noqa: S307
        except Exception as exc:  # noqa: BLE001
            return f'{{"error": "{exc}"}}'
        return f'{{"result": {result}}}'


@pytest.fixture
def calc_registry():
    reg = ToolRegistry()
    reg.register(CalculatorTool())
    return reg


@requires_live
def test_live_streaming_emits_deltas(live_client):
    """真流式：至少收到多个 text_delta，聚合答案非空。"""
    from agentnodes.kernel.gateway import Gateway, api_chat_request

    deltas: list[str] = []
    other: list[str] = []
    gw = Gateway(
        client=live_client,
        stream=True,
        sink=lambda k, d: (
            deltas.append(d.get("delta", "")) if k == "text_delta" else other.append(k)
        ),
    )
    gw.verify_invariants()
    gw.entry.submit(api_chat_request("live-stream", "用一句话说明什么是动量因子，20 字以内"))
    runs = gw.run_once()
    assert runs and runs[0].ok, runs[0].error if runs else "no run"
    # 注意：MiniMax 会把推理包在 <think> 里，适配器现在**缓冲整个推理块**后
    # 一次性交出（否则跨 chunk 的标签无法被下游正则匹配）。因此正文 delta
    # 数量可能只有 1-2 个——断言改为「至少 1 个且聚合非空」，不再要求 >= 2。
    assert len(deltas) >= 1, "未收到任何 text_delta"
    joined = "".join(deltas)
    assert joined.strip()
    assert "<think>" not in joined, f"推理标记泄漏到正文: {joined[:80]!r}"
    assert runs[0].answer.strip()


@requires_live
def test_live_tool_call_executes(live_client, calc_registry):
    """真工具循环：react 节点流式下能发起工具调用并得到正确答案。"""
    from agentnodes.kernel.gateway import Gateway, api_chat_request

    events: list[tuple[str, dict]] = []
    gw = Gateway(
        client=live_client,
        registry=calc_registry,
        stream=True,
        sink=lambda k, d: events.append((k, d)),
    )
    gw.verify_invariants()
    gw.entry.submit(api_chat_request(
        "live-tool", "请用 calculator 工具计算 17*23+100，只回答数字",
    ))
    runs = gw.run_once()
    assert runs and runs[0].ok, runs[0].error if runs else "no run"

    kinds = [k for k, _ in events]
    assert "tool.started" in kinds, f"未触发工具调用: {set(kinds)}"
    tools = [d.get("tool") for k, d in events if k == "tool.started"]
    assert "calculator" in tools
    assert "491" in runs[0].answer, f"答案应含 491: {runs[0].answer}"


@requires_live
def test_live_history_injected_second_turn(live_client):
    """真历史注入：第二轮能记住第一轮的信息。"""
    from agentnodes.kernel.gateway import Gateway, api_chat_request

    gw = Gateway(client=live_client, stream=False)
    gw.verify_invariants()

    gw.entry.submit(api_chat_request(
        "live-mem", "记住这个数字：742。只回复「记住了」",
    ))
    r1 = gw.run_once()
    assert r1 and r1[0].ok

    # 第二轮手工构造 history（模拟 chat 层读库后的形态）
    gw.entry.submit(api_chat_request(
        "live-mem", "我刚才让你记住的数字是多少？只回答数字",
        history=[
            {"role": "user", "content": "记住这个数字：742。只回复「记住了」"},
            {"role": "assistant", "content": r1[0].answer},
        ],
    ))
    r2 = gw.run_once()
    assert r2 and r2[0].ok
    assert "742" in r2[0].answer, f"未记住历史: {r2[0].answer}"
