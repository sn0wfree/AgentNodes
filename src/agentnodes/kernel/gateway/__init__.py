"""gateway —— Gateway 本体（阶段 1a 起步：最小控制器 + fencing + 不变量层）。

落位规则：本包零 FastAPI import、零 core/engine 之外的重依赖。
"""
from __future__ import annotations

from agentnodes.kernel.gateway.entry_adapters import (
    api_chat_request,
    cli_request,
    mcp_request,
    research_graph_enabled,
    scheduler_request,
    submit_via_entry,
)
from agentnodes.kernel.gateway.fencing import FenceToken, FenceVerdict, SessionFence
from agentnodes.kernel.gateway.gateway import Gateway
from agentnodes.kernel.gateway.invariants import Invariant, InvariantViolation, invariants, verify_all
from agentnodes.kernel.gateway.session_controller import RunResult, SessionController
from agentnodes.kernel.gateway.singleton import get_gateway, reset_gateway

__all__ = [
    "api_chat_request",
    "cli_request",
    "mcp_request",
    "research_graph_enabled",
    "scheduler_request",
    "submit_via_entry",
    "FenceToken",
    "FenceVerdict",
    "SessionFence",
    "Gateway",
    "Invariant",
    "InvariantViolation",
    "invariants",
    "verify_all",
    "get_gateway",
    "reset_gateway",
    "RunResult",
    "SessionController",
]
