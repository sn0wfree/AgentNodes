"""react 节点真实 LLM 集成测试（阶段 1b）—— 真实工具调用循环。

背景：conftest 有两层 autouse 隔离；`real_llm_config`/`live_client` 夹具
（conftest 提供）显式绕过，恢复真实 llm.json + .env 中的 key。

运行（默认跳过；须显式开启）：
    $env:QNODES_LIVE_LLM="1"
    py -3.12 -m pytest tests/test_react_live.py -m llm -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.llm

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.core.agent.tools import BaseTool, ToolRegistry  # noqa: E402

requires_live = pytest.mark.skipif(
    os.environ.get("QNODES_LIVE_LLM") != "1",
    reason="live LLM tests are opt-in: set QNODES_LIVE_LLM=1 (and `-m llm`)",
)


class CalculatorTool(BaseTool):
    """安全四则运算计算器（与 stage1b_demo 共用语义）。"""

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
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    return registry


def _make_react(client, registry, sink):
    from strategy_research.kernel.runtimes.nodes.react import ReactConfig, make_react_node

    return make_react_node(
        client, registry, sink=sink,
        config=ReactConfig(
            system="你是计算助手。凡是数学计算必须调用 calculator 工具，不要心算。回答用中文，不超过 30 字。",
            max_iterations=5,
        ),
    )


# ── 真实工具循环 ─────────────────────────────────────────


@requires_live
def test_live_react_tool_loop_computes(live_client, calc_registry):
    """LLM 发起工具调用 → registry 执行 → 结果回填 → 正确答案。"""
    events: list[tuple[str, dict]] = []
    node = _make_react(live_client, calc_registry, sink=lambda k, d: events.append((k, d)))

    out = node({
        "messages": [{"role": "user", "content": "请计算 17*23+100 的结果"}],
        "run_id": "live:r0",
    })

    assert out["stop_reason"] == "completed"
    assert "491" in out["answer"], f"答案应含 491: {out['answer']}"
    # 工具真的被执行：事件成对（calculator）+ 消息协议含 role=tool
    starts = [d for k, d in events if k == "tool.started"]
    completes = [d for k, d in events if k == "tool.completed"]
    assert len(starts) >= 1 and len(starts) == len(completes)
    assert starts[0]["tool"] == "calculator"
    # 协议顺序：assistant(tool_calls) 后紧跟 role=tool
    msgs = out["messages"]
    tool_idx = [i for i, m in enumerate(msgs) if m["role"] == "tool"]
    assert tool_idx and tool_idx[0] > 0 and msgs[tool_idx[0] - 1]["role"] == "assistant"


@requires_live
def test_live_react_pure_chat_skips_tool(live_client, calc_registry):
    """纯聊天不应触发工具调用（工具使用有选择性）。"""
    events: list[tuple[str, dict]] = []
    node = _make_react(live_client, calc_registry, sink=lambda k, d: events.append((k, d)))

    out = node({
        "messages": [{"role": "user", "content": "用一句话说明什么是夏普比率"}],
        "run_id": "live:r1",
    })

    assert out["stop_reason"] == "completed"
    assert out["answer"].strip()
    # 无工具事件、无 role=tool 消息
    assert [k for k, _ in events if k.startswith("tool.")] == []
    assert all(m["role"] != "tool" for m in out["messages"])


# ── 八站全链路 × react（SessionController 驱动） ──────────


@requires_live
def test_live_react_slice(live_client, calc_registry):
    from strategy_research.core.gateway import SessionController
    from strategy_research.core.orchestration import (
        EntryPoint,
        SourceKind,
        WorkKind,
        WorkRequest,
    )
    from strategy_research.kernel.runtimes import GraphEngine

    events: list[str] = []
    sink = lambda kind, data: events.append(kind)  # noqa: E731
    ep = EntryPoint(sink=sink)
    react = _make_react(live_client, calc_registry, sink=sink)
    engine = GraphEngine(sink=sink)
    engine.add_node("react", react)
    controller = SessionController(ep, client=live_client, sink=sink)
    controller._build_engine = lambda: engine  # 1b 演示注入；正式接线在 A1

    receipt = ep.submit(
        WorkRequest(
            kind=WorkKind.CHAT,
            source=SourceKind.API_CHAT,
            session_key="chat:live-react",
            payload={"text": "请计算 (17*23)+100 等于多少"},
        )
    )
    assert receipt.accepted
    [run] = controller.run_pending()

    assert run.ok, run.error
    assert "491" in run.answer
    for expected in (
        "turn.admitted", "run.started",
        "tool.started", "tool.completed", "run.settled",
    ):
        assert expected in events, expected
