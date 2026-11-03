"""W-split: loop event forwarding helpers (from SessionService)."""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .models import Attempt
    from .service import SessionService

from strategy_research.foundation.llm.config import LLMConfig
from .part_accumulate import _accumulate_part

logger = logging.getLogger(__name__)

def _build_attempt_metrics(usage_state: dict[str, int], loop_result: Any) -> dict:
    """Build the attempt metrics payload from usage counters and loop result."""
    metrics = {
        "input_tokens": usage_state["input"],
        "output_tokens": usage_state["output"],
        "total_tokens": usage_state["input"] + usage_state["output"],
    }
    if loop_result.metrics.get("claim_validation"):
        metrics["claim_validation"] = loop_result.metrics["claim_validation"]
    return metrics


def _emit_goal_event(
    event_bus: Any, session_id: str, data: dict[str, Any], cfg: Optional[LLMConfig]
) -> None:
    """Detect goal tool results and emit the full-snapshot goal event.

    When create_goal / add_evidence / complete_goal tools execute, emit ONE
    full-snapshot ``goal_updated`` SSE event (built by
    core/goal/events.build_goal_updated_payload). The event drives the
    frontend panel AND is persisted by the projector as a
    message_type='goal' message that enters the LLM context (see
    docs/goal-events-panel-link.md).
    """
    import json as _json

    tool_name = data.get("name", "")
    change_type_map = {
        "create_goal": "create",
        "add_evidence": "evidence",
        "complete_goal": "complete",
    }
    change_type = change_type_map.get(tool_name)
    if change_type is None:
        return

    result_raw = data.get("result", "")
    try:
        result = _json.loads(result_raw) if isinstance(result_raw, str) else result_raw
    except (_json.JSONDecodeError, TypeError, ValueError):
        return

    if not isinstance(result, dict) or result.get("status") != "ok":
        return

    from ...core.goal import GoalStore
    from ...core.goal.events import build_goal_updated_payload

    truncate = 100
    if cfg is not None and cfg.compact_config is not None:
        truncate = cfg.compact_config.goal_evidence_truncate_chars

    payload = None
    try:
        with GoalStore() as store:
            payload = build_goal_updated_payload(
                session_id,
                store,
                change_type,
                truncate_chars=truncate,
                evidence_text=result.get("text") if change_type == "evidence" else None,
            )
    except Exception:  # noqa: BLE001
        logger.warning("goal event build failed", exc_info=True)

    if payload is not None:
        event_bus.emit(session_id, "goal_updated", payload)


class _LoopEventForwarder:
    """AgentLoop on_event adapter for the B4 event-sourced path.

    Two jobs per event:
    1. Accumulate the part protocol (text/tool/thinking) into
       ``accumulated_parts`` for the assistant message body.
    2. Forward the event onto EventBusV2 (event_log + SSE); tool/usage
       data ride along in ``event.data`` so the projector can merge them
       into the message parts.

    Also accumulates llm_usage tokens into attempt-local metrics so the
    frontend can show context usage progress.
    """

    def __init__(
        self,
        service: "SessionService",
        *,
        attempt: Attempt,
        accumulated_parts: list[dict[str, Any]],
        event_bus: Any,
        cfg: Optional[LLMConfig],
    ) -> None:
        self.service = service
        self.attempt = attempt
        self.accumulated_parts = accumulated_parts
        self.event_bus = event_bus
        self.cfg = cfg
        self._usage_lock = threading.Lock()
        self._usage_state: dict[str, int] = {"input": 0, "output": 0, "context_used": 0}

    @property
    def usage_state(self) -> dict[str, int]:
        return self._usage_state

    def __call__(self, event_type: str, data: dict[str, Any]) -> None:
        # Offload large fields (system_prompt, tools_schema, llm response
        # content) to sidecar blobs so the event_log stays lean; the small
        # metadata + sidecar refs go into event_log. This lets a later
        # projection reconstruct the full envelope from the event log
        # alone (DSH request/response-envelope pattern).
        if isinstance(data, dict):
            if event_type == "llm_request":
                data = self._offload_large_fields(data)
            elif event_type == "llm_response":
                data = self._offload_large_fields(data, fields=("content",))

        # Accumulate parts for persistence
        _accumulate_part(self.accumulated_parts, event_type, data)

        # B4: tool result persistence is handled by EventBusV2 →
        # projector.flush(). The tool_result event lands in event_log,
        # the projector merges it into the assistant message's tool_call
        # part, and flush writes the updated message to messages table.
        # No direct DB write needed here.

        # Track token usage so the frontend can show a context usage bar.
        # llm_usage is emitted by AgentLoop after each LLM call (see
        # core/agent/loop.py).
        if event_type == "llm_usage" and isinstance(data, dict):
            with self._usage_lock:
                # OpenAI-compatible providers use input_tokens /
                # output_tokens; some send prompt_tokens /
                # completion_tokens. Accept both.
                inc_in = int(data.get("input_tokens") or data.get("prompt_tokens") or 0)
                inc_out = int(data.get("output_tokens") or data.get("completion_tokens") or 0)
                self._usage_state["input"] += inc_in
                self._usage_state["output"] += inc_out
                # context_used = the size of the prompt actually sent to the
                # model on the most recent LLM call. Overwrite (not
                # accumulate) — a fresh call re-sends the whole context.
                prompt_used = int(data.get("prompt_tokens") or data.get("input_tokens") or 0)
                if prompt_used > 0:
                    self._usage_state["context_used"] = prompt_used
            total = self._usage_state["input"] + self._usage_state["output"]
            # Emit a session_total_tokens event so the frontend has an
            # authoritative figure (not the per-call delta).
            self.event_bus.emit(
                self.attempt.session_id,
                "session_total_tokens",
                {
                    "input_tokens": self._usage_state["input"],
                    "output_tokens": self._usage_state["output"],
                    "total_tokens": total,
                    "context_used": self._usage_state["context_used"],
                    "message_id": self.attempt.message_id,
                    "attempt_id": self.attempt.attempt_id,
                },
            )

        # Add attempt/message context
        data = dict(data)
        data.setdefault("attempt_id", self.attempt.attempt_id)
        data.setdefault("message_id", self.attempt.message_id)

        # Detect goal tool results → emit goal SSE events for frontend
        if event_type == "tool_result":
            _emit_goal_event(self.event_bus, self.attempt.session_id, data, self.cfg)

        self.event_bus.emit(self.attempt.session_id, event_type, data)

    def _offload_large_fields(
        self, data: dict[str, Any], fields: tuple[str, ...] = ("system_prompt", "tools_schema"),
    ) -> dict[str, Any]:
        """Offload large llm fields to sidecar blobs.

        Returns a copy of ``data`` with any of ``fields`` that exceed the
        inline threshold replaced by ``{field}_path`` / ``{field}_preview`` /
        ``{field}_size`` references. The blob dir is derived from the event
        DB path so the projection can resolve refs without a separate trace
        file.
        """
        import hashlib as _hashlib

        out = dict(data)
        threshold = int(os.environ.get("SR_LLM_REQUEST_OFFLOAD_THRESHOLD", "4096"))

        # Locate the sidecar dir next to the event DB: <dir>/trace-blobs/.
        blob_root = None
        try:
            db_path = getattr(self.event_bus, "_db_path", None)
            if db_path is not None:
                blob_root = Path(db_path).parent / "trace-blobs"
        except Exception:
            blob_root = None

        for field in fields:
            value = out.get(field)
            if not isinstance(value, str) or len(value) <= threshold:
                continue
            if blob_root is None:
                # No blob root → keep inline (better full data than loss).
                continue
            blob_root.mkdir(parents=True, exist_ok=True)
            digest = _hashlib.sha256(
                f"{self.attempt.session_id}\0{field}\0{value}".encode("utf-8")
            ).hexdigest()
            path = blob_root / f"{digest[:24]}.txt"
            try:
                path.write_text(value, encoding="utf-8")
            except OSError:
                continue
            out[field + "_path"] = f"trace-blobs/{path.name}"
            out[field + "_preview"] = value[:512]
            out[field + "_size"] = len(value)
            out.pop(field, None)
            # P0-1 C3: track this offload in blob_refs so the TTL-based
            # cleanup has a single source of truth. Done outside the
            # file write above so a write failure doesn't leave a
            # dangling ref_count=1.
            try:
                from strategy_research.foundation.storage.blob_schema import (
                    ensure_blob_refs_schema,
                    record_blob_offload,
                )
                blob_conn = getattr(
                    self.event_bus, "_backend", None
                )
                if blob_conn is not None and hasattr(
                    blob_conn, "_ensure_conn"
                ):
                    conn_refs = blob_conn._ensure_conn()  # type: ignore[attr-defined]
                    ensure_blob_refs_schema(conn_refs)
                    record_blob_offload(conn_refs, out[field + "_path"])
                    conn_refs.commit()
            except Exception:
                logger.debug(
                    "blob_refs record failed for %s; cleanup will not "
                    "track this offload until the next one",
                    out[field + "_path"],
                )
        return out


def _bootstrap_workspace(workspace: Path) -> None:
    """Ensure workspace has the required directory structure.

    Only creates directories — no root-level template files to avoid
    confusing the LLM about where actual strategies live.
    """
    # Create strategies directory
    strategies_dir = workspace / "strategies"
    strategies_dir.mkdir(parents=True, exist_ok=True)

    # Ensure data.duckdb exists (init_db handles table creation)
    db_path = workspace / "data.duckdb"
    if not db_path.exists():
        try:
            from strategy_research.apps.db import init_db
            init_db(workspace)
        except Exception as exc:
            logger.warning("Failed to init DuckDB: %s", exc)


# Read-only tools allowed in plan mode (agent may observe but not modify).
_PLAN_READONLY_TOOLS = frozenset({
    "read", "list", "search_code", "search_file",
    "get_file_info", "websearch", "web_fetch", "webfetch",
    "read_document", "think", "tool_help",
    "list_goals", "get_goal_status",
    "list_history", "git_diff", "factor_analysis",
    "pattern_recognition", "list_skills", "skill",
    "factor_cross_sectional_analysis", "factor_quintile_returns",
    "factor_ic_decay", "factor_turnover",
    "strategy_compare", "drawdown_analysis", "benchmark_comparison",
})


def _plan_mode_allowed_tools(mode: str) -> Optional[list[str]]:
    """Restrict to read-only tools in plan mode; otherwise no restriction."""
    if mode == "plan":
        return list(_PLAN_READONLY_TOOLS)
    return None


def _thinking_instructions(thinking: str) -> str:
    """Build system-prompt instructions for the thinking mode.

    "auto" = no injection, let the provider decide.
    """
    if thinking == "off":
        return (
            "\n\nIMPORTANT: Do NOT use thinking/reasoning blocks. "
            "Respond directly with your analysis."
        )
    if thinking == "on":
        return (
            "\n\nIMPORTANT: Use extended thinking for complex analysis. "
            "Show your reasoning process in <think> blocks."
        )
    return ""


def _resolve_loop_prompt(system_prompt: Optional[str], persona: Optional[str]):
    """Resolve the final system prompt and loop role.

    Unknown persona → fall back to the caller-provided prompt (empty string
    is treated as "no override").
    """
    final_prompt = system_prompt
    loop_role = "chat"
    if system_prompt is None and persona:
        from strategy_research.core.agent.prompt_builder import PromptBuilderFactory

        if persona in PromptBuilderFactory.list_roles():
            persona_prompt = PromptBuilderFactory.get(persona).build_system_prompt(persona, {})
            if persona_prompt:
                final_prompt = persona_prompt
                loop_role = persona
    return final_prompt, loop_role


