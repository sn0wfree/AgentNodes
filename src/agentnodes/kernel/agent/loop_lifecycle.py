"""W-split 兼容层：run endings + claim validation（批次 13 S3-b1 起为薄转发）。

**实现已搬到** ``loop_endings.py``（模块级函数，显式传 loop）。
本文件只保留 mixin 形状供 ``AgentLoop`` 的 MRO 与既有导入点使用——
阶段 3 删 mixin 时整文件删除。
"""
from __future__ import annotations

from typing import Any

from . import loop_endings as _end

__all__ = ["LoopLifecycleMixin"]


class LoopLifecycleMixin:
    """薄转发壳——见模块 docstring。"""

    # ── Host surface (provided by AgentLoop / AutoresearchRunner) ──
    _budget_cost_usd: Any
    _budget_tokens: Any
    _emit: Any
    _enable_claim_validation: Any
    _iteration_timeout_s: Any
    _strict_claim_validation: Any
    _trace: Any
    _trace_and_emit: Any
    max_iterations: Any

    def _handle_iteration_timeout(self, result, iteration: int) -> None:
        """Finalize the result for a per-iteration timeout."""
        _end.handle_iteration_timeout(self, result, iteration)

    def _handle_max_iter(self, result) -> None:
        """Populate max_iter result fields, emit trace + event."""
        _end.handle_max_iter(self, result)

    def _handle_stop(self, response, result, iteration: int) -> None:
        """Populate stop result fields, emit trace + event."""
        _end.handle_stop(self, response, result, iteration)

    def _finalize_metrics(self, result, messages, t0: float) -> None:
        """Populate elapsed/tokens metrics, emit loop_final trace."""
        _end.finalize_metrics(self, result, messages, t0)

    def _run_claim_validation(self, result, messages) -> None:
        """Validate metric claims in the final answer against tool results."""
        _end.run_claim_validation(self, result, messages)
