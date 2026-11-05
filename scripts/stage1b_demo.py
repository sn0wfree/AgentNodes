"""阶段 1b 演示：真实工具调用循环（calculator 工具 + 真实 MiniMax）。

用法：
    $env:QNODES_LIVE_LLM="1"   # 可选，仅控制 live 测试
    py -3.12 -X utf8 scripts/stage1b_demo.py "帮我算 (17*23)+100 等于多少"
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentnodes.core.agent.tools import BaseTool
from agentnodes.core.gateway import SessionController
from agentnodes.core.llm import LLMConfig, OpenAICompatClient
from agentnodes.core.orchestration import EntryPoint, SourceKind, WorkKind, WorkRequest
from agentnodes.core.runtimes.nodes.react import ReactConfig, make_react_node
from agentnodes.core.agent.tools import ToolRegistry


class CalculatorTool(BaseTool):
    """安全的四则运算计算器（eval 限定数字与运算符）。"""

    name = "calculator"
    description = "计算四则运算表达式。参数 expr：如 '17*23+100'，只支持数字与 + - * / ( )"
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
            result = eval(cleaned, {"__builtins__": {}}, {})  # noqa: S307 —— 字符集已白名单
        except Exception as exc:  # noqa: BLE001
            return f'{{"error": "{exc}"}}'
        return f'{{"result": {result}}}'


def main() -> None:
    text = " ".join(a for a in sys.argv[1:] if a.strip()) or "帮我算 (17*23)+100 等于多少"

    registry = ToolRegistry()
    registry.register(CalculatorTool())

    client = OpenAICompatClient(LLMConfig.load())
    sink = lambda kind, data: print(f"  [event] {kind}: {data}")  # noqa: E731
    ep = EntryPoint(sink=sink)

    react = make_react_node(
        client, registry,
        sink=sink,
        config=ReactConfig(system="你是量化研究助手。需要计算时调用 calculator 工具，不要心算。回答用中文。"),
    )
    engine = __import__("agentnodes.core.runtimes.graph_engine", fromlist=["GraphEngine"]).GraphEngine(sink=sink)
    engine.add_node("react", react)

    # 复用控制器的准入/身份签发，引擎换成 react 图
    controller = SessionController(ep, client=client, sink=sink)
    controller._build_engine = lambda: engine  # 1b 演示：注入 react 图（正式接线在 A1）

    request = WorkRequest(
        kind=WorkKind.CHAT,
        source=SourceKind.API_CHAT,
        session_key="chat:demo",
        payload={"text": text},
    )
    receipt = ep.submit(request)
    if not receipt.accepted:
        print(f"  拒绝: {receipt.reason}")
        return

    [run] = controller.run_pending()
    if run.ok:
        print(f"\n助手: {run.answer}")
        print(f"  (turn={run.turn_id} run={run.run_id})")
    else:
        print(f"\n失败: {run.error}")


if __name__ == "__main__":
    main()
