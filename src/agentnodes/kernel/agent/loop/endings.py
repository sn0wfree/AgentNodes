"""AgentLoop 的收尾帮手（批次 13 · S3-b1）。

**背景**：这些方法原先在 ``loop_lifecycle.py`` 的 ``LoopLifecycleMixin`` 上。
它们已是纯函数形态（只读 loop 的预算字段、写 result、发事件），唯一
依赖 ``self`` 的原因是它们在 mixin 里。

**形态**：全部显式传 ``loop`` 的模块级函数。``loop_lifecycle.py`` 保留为
薄转发壳（阶段 3 删）。

**为什么值得搬**：``_run_claim_validation`` 与 ``_finalize_metrics`` 是
``DefaultFinalizationStep`` 的降级路径（step 未绑定时会自己实现一遍），
把它们抽成纯函数后，步骤侧可以直接引用同一个实现，不必经 loop。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from agentnodes.foundation.utils.token_utils import estimate_tokens

logger = logging.getLogger(__name__)

__all__ = [
    "finalize_metrics",
    "handle_attempt_timeout",
    "handle_iteration_timeout",
    "handle_max_iter",
    "handle_stop",
    "run_claim_validation",
]


def handle_attempt_timeout(
    loop: Any, result: Any, iteration: int, elapsed: float
) -> None:
    """Finalize the result for an **attempt-level** (整轮) timeout.

    与 :func:`handle_iteration_timeout` 的区别：迭代级是单次 LLM 调用超时
    （循环正常退出，终态 ``finished_reason="timeout"``）；attempt 级是**整轮
    预算耗尽**，必须**中止执行**——本函数只负责补齐终态字段与事件，
    抛异常由调用方（``_run_loop_core``）完成。

    事件面与迭代级保持同构（``loop_end`` + ``iter_end``），让下游按
    ``finish_reason`` 归一处理时不必区分两级超时。

    批 4a（路线步骤 4 · 裁决 §10）：``loop_end`` 附带
    ``timeout_tool_outcomes``——attempt 超时时逐调用的**实际**结果
    （state ∈ stopped/still_running/returned/unknown），与声明的
    能力级别并列透出。声明 ≠ 事实：未知显式记 unknown。
    """
    timeout_s = getattr(loop, "_attempt_timeout_s", None)
    result.finished_reason = "timeout"
    result.error = (
        f"attempt timed out after {timeout_s}s "
        f"(elapsed {elapsed:.1f}s, iteration {iteration})"
    )
    loop._trace_and_emit("loop_end", {
        "reason": "attempt_timeout",
        "iteration": iteration,
        "elapsed": round(elapsed, 3),
        "timeout_tool_outcomes": list(getattr(loop, "_timeout_tool_outcomes", []) or []),
    })
    loop._emit("iter_end", {
        "iteration": iteration,
        "finish_reason": "timeout",
        "tool_calls_made": result.tool_calls_made,
    })


def handle_iteration_timeout(
    loop: Any, result: Any, iteration: int
) -> None:
    """Finalize the result for a per-iteration timeout."""
    result.finished_reason = "timeout"
    result.error = f"iteration {iteration} exceeded {loop._iteration_timeout_s}s"
    loop._trace_and_emit("loop_end", {"reason": "timeout", "iteration": iteration})
    loop._emit("iter_end", {
        "iteration": iteration,
        "finish_reason": "timeout",
        "tool_calls_made": result.tool_calls_made,
    })


def handle_max_iter(loop: Any, result: Any) -> None:
    """Populate max_iter result fields, emit trace + event."""
    result.finished_reason = "max_iter"
    if not result.answer:
        result.answer = (
            f"Reached max_iterations={loop.max_iterations} without a final answer."
        )
    loop._trace_and_emit("loop_end", {"reason": "max_iter", "iteration": result.iterations})
    loop._emit("assistant_message", {"content": result.answer})
    loop._emit("iter_end", {
        "iteration": result.iterations,
        "finish_reason": "max_iter",
        "tool_calls_made": result.tool_calls_made,
    })


def handle_stop(loop: Any, response: Any, result: Any, iteration: int) -> None:
    """Populate stop result fields, emit trace + event."""
    result.answer = response.content
    result.finished_reason = "stop"
    loop._trace_and_emit("loop_end", {"reason": "stop", "iteration": iteration})
    loop._emit("assistant_message", {"content": response.content or ""})
    loop._emit("iter_end", {
        "iteration": iteration,
        "finish_reason": "stop",
        "tool_calls_made": result.tool_calls_made,
    })


def finalize_metrics(
    loop: Any, result: Any, messages: list[dict[str, Any]], t0: float
) -> None:
    """Populate elapsed/tokens metrics, emit loop_final trace."""
    elapsed = time.perf_counter() - t0
    result.metrics["elapsed_s"] = round(elapsed, 2)
    result.metrics["tokens"] = estimate_tokens(messages)
    # A1: real billed tokens (from LLM usage), not estimate.
    result.metrics["usage_tokens"] = int(getattr(loop, "_budget_tokens", 0) or 0)
    result.metrics["usage_cost_usd"] = float(getattr(loop, "_budget_cost_usd", 0.0) or 0.0)
    loop._trace_and_emit("loop_final", {
        "reason": result.finished_reason,
        "iterations": result.iterations,
        "tool_calls_made": result.tool_calls_made,
        "elapsed_s": round(elapsed, 2),
        "usage_tokens": result.metrics["usage_tokens"],
        "compression": result.compression_applied,
    })


def run_claim_validation(
    loop: Any, result: Any, messages: list[dict[str, Any]]
) -> None:
    """Validate metric claims in the final answer against tool results.

    Attaches ``ClaimValidationResult.__dict__`` to
    ``result.metrics["claim_validation"]`` so the API layer can surface it
    in assistant-message metadata (→ 🟡/🔴 badge).

    When ``strict_claim_validation`` is enabled and unverified claims
    exist, a soft warning is appended to the answer (never rewrites or
    deletes the model's text).
    """
    if not getattr(loop, "_enable_claim_validation", False):
        return
    from agentnodes.kernel.agent.validators import validate_claims

    tool_texts = [
        m.get("content", "") for m in messages if m.get("role") == "tool"
    ]
    cv = validate_claims(result.answer or "", tool_texts)
    result.metrics["claim_validation"] = cv.__dict__
    if getattr(loop, "_strict_claim_validation", False) and not cv.ok:
        warning = (
            "\n\n> ⚠️ 数据真实性警告：以下数字未在工具返回值中找到，"
            f"可能为模型推测：{', '.join(cv.unverified)}"
        )
        result.answer = (result.answer or "") + warning
        loop._trace({
            "type": "claim_validation_warning",
            "unverified": cv.unverified,
        })
