"""runtimes —— 执行面（GraphEngine 与节点）。

命名说明：原目录结构文档中的 `engine/` 与既有回测引擎包
`core/engine/`（BacktestEngine）撞名，落位时更名 `runtimes/`
——与 RuntimeHub「解析 runtime」的语义一致。

**V3-c**：react 节点导出已删——唯一执行引擎是 AgentLoop
（经 ``nodes.agent_loop_node.make_agent_loop_node`` 适配进图）。
"""
from __future__ import annotations

from .graph_engine import END, INTERRUPT_KEY, GraphEngine, GraphInterrupt
from .nodes.agent_loop_node import (
    make_agent_loop_node,
    make_loop_event_sink,
    wrap_async_capable_client,
)
from .nodes.llm import make_llm_node

__all__ = [
    "END",
    "GraphEngine",
    "GraphInterrupt",
    "INTERRUPT_KEY",
    "make_agent_loop_node",
    "make_llm_node",
    "make_loop_event_sink",
    "wrap_async_capable_client",
]
