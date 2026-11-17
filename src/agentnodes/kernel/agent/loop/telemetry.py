"""W-split 兼容层：hooks + emit/trace（批次 13 S3-b1 起为薄转发）。

**实现已搬到** ``telemetry/agent_telemetry.py``（模块级函数，显式传 loop）。
本文件只保留 mixin 形状供 ``AgentLoop`` 的 MRO 与既有导入点使用——
阶段 3 删 mixin 时整文件删除。

保留理由：``tests/test_loop_compaction_cycle.py`` 等按名字 import mixin；
``AgentLoop`` 的继承列表也引用它。方法体已无逻辑（1 行转发）。
"""
from __future__ import annotations

import asyncio
from typing import Any

from agentnodes.kernel.agent.telemetry import agent_telemetry as _tel

__all__ = ["LoopTelemetryMixin"]


class LoopTelemetryMixin:
    """薄转发壳——见模块 docstring。"""

    # ── Host surface (provided by AgentLoop / AutoresearchRunner) ──
    _hooks: Any
    _on_event: Any
    _trace: Any

    def _fire_hooks(self, method_name: str, *args: Any, **kwargs: Any) -> None:
        """Sync adapter for async CompositeHook methods."""
        _tel.fire_hooks(self, method_name, *args, **kwargs)

    def _emit(self, event_type: str, data: dict | None = None) -> None:
        """Emit an event to the on_event callback (if set)."""
        _tel.emit(self, event_type, data)

    def _trace_and_emit(self, event_type: str, data: dict | None = None) -> None:
        """Write a trace entry AND emit it onto the event bus."""
        _tel.trace_and_emit(self, event_type, data)

    def emit_tool_progress(
        self,
        *,
        tool: str,
        call_id: str,
        stage: str,
        current: int | None = None,
        total: int | None = None,
        message: str = "",
    ) -> None:
        """Emit a tool_progress event from inside a tool execution."""
        _tel.emit_tool_progress(
            self, tool=tool, call_id=call_id, stage=stage,
            current=current, total=total, message=message,
        )

    async def _afire_hooks(self, method_name: str, *args: Any, **kwargs: Any) -> None:
        """Async version of _fire_hooks - awaits coroutine hooks directly."""
        await _tel.afire_hooks(self, method_name, *args, **kwargs)
