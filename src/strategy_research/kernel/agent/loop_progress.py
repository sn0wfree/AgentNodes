"""W-split: llm error + no-progress/approval (from AgentLoop)."""
from __future__ import annotations

from typing import Any

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from strategy_research.foundation.llm.errors import LLMError
    from strategy_research.foundation.llm.parser import LLMResponse, ToolCall
    from .loop import LoopResult

logger = logging.getLogger(__name__)

import hashlib


def _tool_call_hash(tc):
    payload = json.dumps({"name": tc.name, "arguments": tc.arguments}, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(payload.encode()).hexdigest()[:16]

class LoopProgressMixin:
# ── Host surface (provided by AgentLoop / AutoresearchRunner) ──
    _approval_event: Any
    _approval_on_timeout: Any
    _approval_response: Any
    _approval_timeout: Any
    _circuit_breaker: Any
    _detect_no_progress: Any
    _emit: Any
    _recent_hashes: Any
    _response_to_assistant_msg: Any
    _role: Any
    _trace: Any
    _trace_and_emit: Any
    no_progress_window: Any
    session_id: Any

    def _handle_llm_error(
        self, exc: LLMError, iteration: int, result: LoopResult,
    ) -> None:
        """Populate error result fields, emit error trace + event."""
        result.finished_reason = "error"
        result.error = f"{type(exc).__name__}: {exc}"
        self._trace({"type": "error", "iteration": iteration, "error": str(exc)})
        self._emit("error", {"message": str(exc), "fatal": True})

    @staticmethod
    def _is_stream_required_error(exc: Exception) -> bool:
        """Whether the error should NOT trigger a stream→achat fallback.

        Auth/rate-limit/config errors are equally fatal for both modes
        (return True → no fallback). Everything else (timeout, server,
        malformed SSE, JSON parse errors) gets one ``achat()`` retry.
        """
        from strategy_research.foundation.llm.errors import (
            LLMAuthError,
            LLMConfigError,
            LLMQuotaError,
            LLMRateLimitError,
        )
        return isinstance(exc, (LLMAuthError, LLMRateLimitError, LLMConfigError, LLMQuotaError))

    def _append_assistant_msg(
        self, response: LLMResponse, messages: list[dict[str, Any]],
        result: LoopResult, iteration: int,
    ) -> None:
        """Convert response to assistant msg, append to messages/result, trace."""
        assistant_msg = self._response_to_assistant_msg(response)
        messages.append(assistant_msg)
        result.messages.append(assistant_msg)
        self._trace_and_emit("llm_response", {
            "iteration": iteration,
            "finish_reason": response.finish_reason,
            "has_tool_calls": response.has_tool_calls(),
            "tool_call_count": len(response.tool_calls),
            "content": response.content or "",
            "content_preview": (response.content or "")[:200],
        })

    def _collect_tool_hashes(
        self, tool_calls: list[ToolCall], tool_result_msgs: list[dict[str, Any]],
    ) -> list[str]:
        """Compute tool_call hashes for no-progress detection."""
        return [_tool_call_hash(tc) for tc in tool_calls]

    def _append_tool_results(
        self,
        tool_calls: list[ToolCall],
        tool_result_msgs: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        result: LoopResult,
    ) -> None:
        """Append tool result messages to messages and result.messages."""
        for tool_result_msg in tool_result_msgs:
            messages.append(tool_result_msg)
            result.messages.append(tool_result_msg)
            # Phase 5-guardrails v1: observe injection/exfil signatures in
            # tool outputs (docs/design/phase5-guardrails-design.md §2.2).
            try:
                from .guardrails import scan_tool_result

                content = tool_result_msg.get("content") or ""
                tool_name = tool_result_msg.get("name") or "tool"
                for finding in scan_tool_result(tool_name, content):
                    self._emit("guardrail.finding", finding.to_dict())
            except Exception:  # noqa: BLE001 — guardrails never break the loop
                pass

    def _breaker_open_messages(
        self, tool_calls: list[ToolCall],
    ) -> list[dict[str, Any]]:
        """Return tool error messages when circuit breaker is OPEN.

        Tells the LLM to try a different approach instead of repeating
        the same failing tool calls.
        """
        state = self._circuit_breaker.to_dict() if self._circuit_breaker else {}
        return [
            {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps({
                    "status": "error",
                    "error": "circuit_breaker_open",
                    "message": (
                        f"Tool '{tc.name}' is temporarily unavailable "
                        "(circuit breaker open). Please try a different "
                        "approach or tool."
                    ),
                    "circuit_state": state,
                }, ensure_ascii=False),
            }
            for tc in tool_calls
        ]
    def _check_no_progress(
        self, tool_hashes: list[str], response: LLMResponse,
        result: LoopResult, iteration: int,
        *,
        hashes_pre_recorded: bool = False,
    ) -> bool:
        """Update _recent_hashes, detect no_progress. If triggered, ask
        the user for approval (opencode-style). Returns True if the
        loop should exit; False to continue.

        L7 v0.2: when ``hashes_pre_recorded=True`` (the strategy's
        ProgressStep already recorded the hashes into ``_recent_hashes``),
        skip the ``extend`` so the window isn't double-counted. The
        side-effects (record_event / circuit_breaker / emit) still run
        here.

        Approval gate semantics:
          * Emit ``agent_approval_requested`` and wait up to
            ``approval_timeout`` seconds for an external
            ``approve_loop(decision)`` call.
          * ``decision == "approved"``: clear the hashes, return False
            (keep looping).
          * ``decision == "reject"``: fill the result with
            ``finished_reason = "user_rejected"`` and return True.
          * timeout (no response): honour ``approval_on_timeout``
            ("continue" → keep looping, "reject" → exit).
        """
        if not hashes_pre_recorded:
            self._recent_hashes.extend(tool_hashes)
            if len(self._recent_hashes) > self.no_progress_window:
                self._recent_hashes = self._recent_hashes[-self.no_progress_window:]
        if not self._detect_no_progress():
            return False

        # 1. Log + side-effects (unchanged behaviour for tracing).
        from strategy_research.foundation.observability.hanging_events import record_event
        record_event(
            "no_progress",
            session_id=getattr(self, "session_id", None),
            detail=(
                f"iteration={iteration} "
                f"window={self.no_progress_window} "
                f"tool_calls={len(self._recent_hashes)}"
            ),
        )
        if self._circuit_breaker is not None:
            self._circuit_breaker.record_no_progress()

        # 2. Emit approval request and wait.
        tool_hash = self._recent_hashes[0] if self._recent_hashes else ""
        approval_payload = {
            "role": self._role,
            "tool_hash": tool_hash,
            "window": self.no_progress_window,
            "iteration": iteration,
            "timeout_s": self._approval_timeout,
            "on_timeout": self._approval_on_timeout,
            "message": (
                f"Agent {self._role or '(unknown)'} 已连续调用相同工具 "
                f"{self.no_progress_window} 次且输出相同,是否继续？"
            ),
        }
        self._trace_and_emit("agent_approval_requested", approval_payload)

        # 3. Wait for response (synchronous — caller's thread blocks here).
        #
        # 决策可能**先于** gate 到达：生产里用户在 agent 还没走到这里时
        # 就点了批准/拒绝。此前实现在等待前无条件下 `clear()` 并把
        # `_approval_response` 置 None——提前到达的决策被静默丢弃，然后
        # 一路阻塞到 `approval_timeout`（默认 1800s）。
        #
        # 顺序必须严格是 clear → 查 response → wait：这样任一时刻到达的
        # 决策都只可能落在「response 已置」或「event 已置」其一，两端都
        # 能接住，不会出现 event 被清而 response 仍在的空洞。
        self._approval_event.clear()
        if self._approval_response is not None:
            approved = True          # 提前到达（或与 clear 擦肩），直接消费
        else:
            approved = self._approval_event.wait(timeout=self._approval_timeout)
        decision = self._approval_response
        self._approval_response = None
        self._approval_event.clear()   # 消费完毕，复位供下一轮使用

        if decision == "reject":
            result.finished_reason = "user_rejected"
            result.answer = (
                response.content or
                f"User rejected agent loop at iteration {iteration}"
            )
            self._trace_and_emit("loop_end", {"reason": "user_rejected", "iteration": iteration})
            self._emit("assistant_message", {"content": result.answer})
            self._emit("iter_end", {
                "iteration": iteration,
                "finish_reason": "user_rejected",
                "tool_calls_made": result.tool_calls_made,
            })
            return True

        if not approved:
            # Timeout — fall back to configured policy.
            decision = self._approval_on_timeout

        if decision == "reject":
            result.finished_reason = "approval_timeout_rejected"
            result.answer = (
                f"Agent {self._role or '(unknown)'} no-progress approval timed out "
                f"({self._approval_timeout:.0f}s) and policy is 'reject'"
            )
            self._trace_and_emit("loop_end", {"reason": "approval_timeout", "iteration": iteration})
            self._emit("assistant_message", {"content": result.answer})
            self._emit("iter_end", {
                "iteration": iteration,
                "finish_reason": "approval_timeout_rejected",
                "tool_calls_made": result.tool_calls_made,
            })
            return True

        # Approved (or default-continue on timeout) — clear hashes, keep looping.
        logger.info(
            "[AGENT] no-progress approval %s for role=%s iteration=%d; "
            "continuing",
            decision or "approved", self._role, iteration,
        )
        self._emit("agent_approval_responded", {
            "role": self._role,
            "decision": decision or "approved",
            "iteration": iteration,
            "reason": "timeout" if not approved else "user",
        })
        self._recent_hashes.clear()
        return False

    def approve_loop(self, decision: str) -> None:
        """External hook to approve/reject a pending agent loop.

        Called by ``POST /api/study/{id}/agents/approve`` (forwarded by
        the scheduler). ``decision`` is ``"approved"`` or ``"reject"``.
        Unblocks ``_check_no_progress`` if it's currently waiting.
        """
        self._approval_response = decision
        self._approval_event.set()

    # ── SwarmWorker parity helpers (unified engine) ──────────────
