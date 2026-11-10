"""runtimes.nodes —— 图节点集(llm / agent_loop)。

**V3-c**:react 节点(``make_react_node``)已删除——双执行路径收敛完成,
唯一执行引擎是 AgentLoop(经 ``agent_loop_node.make_agent_loop_node``
适配成同步 NodeFn)。删除前历史见 V3 决策文档
(``docs/design/v3-path-convergence-decision-20261109.md``)。
"""
from __future__ import annotations

from .agent_loop_node import (
    make_agent_loop_node,
    make_loop_event_sink,
    wrap_async_capable_client,
)
from .llm import make_llm_node

__all__ = [
    "make_agent_loop_node",
    "make_llm_node",
    "make_loop_event_sink",
    "wrap_async_capable_client",
]
