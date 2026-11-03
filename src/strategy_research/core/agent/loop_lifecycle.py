"""W-split: run endings + claim validation (from AgentLoop)."""
from __future__ import annotations

from typing import Any

import logging
import time
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

from strategy_research.foundation.utils.token_utils import estimate_tokens

if TYPE_CHECKING:
    from ..llm.parser import LLMResponse
    from .loop import LoopResult

class LoopLifecycleMixin:
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

    def _handle_iteration_timeout(self, result: LoopResult, iteration: int) -> None:
        """Finalize the result for a per-iteration timeout."""
        result.finished_reason = "timeout"
        result.error = f"iteration {iteration} exceeded {self._iteration_timeout_s}s"
        self._trace_and_emit("loop_end", {"reason": "timeout", "iteration": iteration})
        self._emit("iter_end", {
            "iteration": iteration,
            "finish_reason": "timeout",
            "tool_calls_made": result.tool_calls_made,
        })

    def _handle_max_iter(self, result: LoopResult) -> None:
        """Populate max_iter result fields, emit trace + event."""
        result.finished_reason = "max_iter"
        if not result.answer:
            result.answer = (
                f"Reached max_iterations={self.max_iterations} without a final answer."
            )
        self._trace_and_emit("loop_end", {"reason": "max_iter", "iteration": result.iterations})
        self._emit("assistant_message", {"content": result.answer})
        self._emit("iter_end", {
            "iteration": result.iterations,
            "finish_reason": "max_iter",
            "tool_calls_made": result.tool_calls_made,
        })

    def _handle_stop(
        self, response: LLMResponse, result: LoopResult, iteration: int,
    ) -> None:
        """Populate stop result fields, emit trace + event."""
        result.answer = response.content
        result.finished_reason = "stop"
        self._trace_and_emit("loop_end", {"reason": "stop", "iteration": iteration})
        self._emit("assistant_message", {"content": response.content or ""})
        self._emit("iter_end", {
            "iteration": iteration,
            "finish_reason": "stop",
            "tool_calls_made": result.tool_calls_made,
        })

    def _finalize_metrics(
        self, result: LoopResult, messages: list[dict[str, Any]], t0: float,
    ) -> None:
        """Populate elapsed/tokens metrics, emit loop_final trace."""
        elapsed = time.perf_counter() - t0
        result.metrics["elapsed_s"] = round(elapsed, 2)
        result.metrics["tokens"] = estimate_tokens(messages)
        # A1: real billed tokens (from LLM usage), not estimate.
        result.metrics["usage_tokens"] = int(getattr(self, "_budget_tokens", 0) or 0)
        result.metrics["usage_cost_usd"] = float(getattr(self, "_budget_cost_usd", 0.0) or 0.0)
        self._trace_and_emit("loop_final", {
            "reason": result.finished_reason,
            "iterations": result.iterations,
            "tool_calls_made": result.tool_calls_made,
            "elapsed_s": round(elapsed, 2),
            "usage_tokens": result.metrics["usage_tokens"],
            "compression": result.compression_applied,
        })

    def _run_claim_validation(
        self, result: LoopResult, messages: list[dict[str, Any]],
    ) -> None:
        """Validate metric claims in the final answer against tool results.

        Attaches ``ClaimValidationResult.__dict__`` to
        ``result.metrics["claim_validation"]`` so the API layer can
        surface it in assistant-message metadata (→ 🟡/🔴 badge).

        When ``strict_claim_validation`` is enabled and unverified
        claims exist, a soft warning is appended to the answer (never
        rewrites or deletes the model's text).
        """
        if not self._enable_claim_validation:
            return
        from .validators import validate_claims

        tool_texts = [
            m.get("content", "")
            for m in messages if m.get("role") == "tool"
        ]
        cv = validate_claims(result.answer or "", tool_texts)
        result.metrics["claim_validation"] = cv.__dict__
        if self._strict_claim_validation and not cv.ok:
            warning = (
                "\n\n> ⚠️ 数据真实性警告：以下数字未在工具返回值中找到，"
                f"可能为模型推测：{', '.join(cv.unverified)}"
            )
            result.answer = (result.answer or "") + warning
            self._trace({
                "type": "claim_validation_warning",
                "unverified": cv.unverified,
            })

    # ── Public API ───────────────────────────────
