"""fanout preset 真 LLM 集成测试（F3）—— 多 subagent 并行研究。

运行（默认跳过；须显式开启）：
    $env:QNODES_LIVE_LLM="1"
    py -3.12 -m pytest tests/test_fanout_live.py -m llm -v
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


@requires_live
def test_live_three_subagents_parallel_answers(live_client, calc_registry):
    """三个子代理并行回答不同问题，结果按序合并。"""
    from strategy_research.kernel.runtimes.presets.fanout import build_fanout_preset

    events: list[tuple[str, dict]] = []
    engine = build_fanout_preset(
        live_client, calc_registry,
        split=lambda s: ["1+1 等于几？", "2+2 等于几？", "3+3 等于几？"],
        system="你是计算助手。必须调用 calculator 工具计算，回答只给数字，中文不超过 10 字。",
        max_iterations=5,
        max_parallel=3,
        sink=lambda k, d: events.append((k, d)),
    )

    out = engine.run({"run_id": "live-fanout:r0"})

    assert out["fanout_ok"] is True
    assert len(out["fanout_answers"]) == 3
    pairs = [("2", out["fanout_answers"][0]),
             ("4", out["fanout_answers"][1]),
             ("6", out["fanout_answers"][2])]
    for expected, answer in pairs:
        assert expected in answer, f"期望含 {expected}: {answer!r}"
    # 三个分支各自真实调用过工具
    tool_starts = [d for k, d in events if k == "tool.started"]
    assert len(tool_starts) >= 3


@requires_live
def test_live_branch_budget_slice_enforced(live_client, calc_registry):
    """父预算切 3 片，每片够一次调用：三支都完成，父账收到真实消耗。"""
    from strategy_research.core.agent.strategy.budget import BudgetLimits
    from strategy_research.kernel.runtimes.presets.fanout import build_fanout_preset
    from strategy_research.kernel.runtimes.run_budget import RunBudget

    parent = RunBudget(limits=BudgetLimits(max_tokens=6000))
    engine = build_fanout_preset(
        live_client, calc_registry,
        split=lambda s: ["回复：A", "回复：B", "回复：C"],
        system="直接回复用户内容，不要调用工具，不超过 5 字。",
        max_iterations=2,
        max_parallel=3,
    )
    out = engine.run({"run_id": "live-budget:r0", "run_budget": parent})

    assert out["fanout_ok"] is True
    assert parent.usage.tokens > 0  # 切片消耗真的回到父账
    assert len(out["fanout_answers"]) == 3
