"""Segment-loop executor for modular DAG workflows.

Runs a WorkflowDefinition segment by segment:
  1. Graph is pre-cut at approval nodes (definition.segment_cut()).
  2. A planner node expands into a dynamic segment of llm_agent steps
     (plan_* prefix) executed right after its segment.
  3. An evaluator node returns continue / replan / stop; replan loops
     back to the planner with the previous plan + reason, migrating
     completed nodes via pre_completed.
  4. Approval gates pause between segments (status=awaiting) until
     the user responds via approve(); timeout keeps waiting.

Execution is synchronous per segment; the controller stores active
runs (mirrors _active_runners in api/routers/workflow.py).

Design: docs/design/workflow-module-design.md
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from strategy_research.kernel.swarm.types import AgentResult, AgentStatus
from .definition import WorkflowDefinition, WorkflowNode, WorkflowSegment
from .node_types import (
    NodeContext,
    NodeDispatchError,
    _retryable_node_error,
    dispatch_node,
    register_builtin_tool_executors,
)
from .store import WorkflowStore

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = ("completed", "failed", "cancelled")


class WorkflowRunError(RuntimeError):
    """Unrecoverable workflow execution error."""


class WorkflowRunner:
    """Executes a workflow definition; one instance per run."""

    def __init__(
        self,
        definition: WorkflowDefinition,
        workspace: Path,
        objective: str,
        *,
        store: WorkflowStore | None = None,
        strategy_name: str = "",
        session_id: str = "",
        llm_config: Any | None = None,
        loop_factory: Callable[..., str] | None = None,
        emit_event: Callable[[str, dict], None] | None = None,
        params_override: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> None:
        self.definition = definition
        self.workspace = Path(workspace)
        self.objective = objective
        self.store = store or WorkflowStore()
        self.strategy_name = strategy_name or "default"
        self.session_id = session_id
        self.llm_config = llm_config
        self.loop_factory = loop_factory
        self.emit_event = emit_event or (lambda _e, _d: None)
        self.params_override = params_override or {}
        self.run_id = run_id or f"wf_{uuid.uuid4().hex[:8]}"
        self.segments = definition.segment_cut()
        self.params = self._merge_params(definition.params, params_override)

        self.status = "pending"
        self.segment_idx = 0
        self.pre_completed: dict[str, AgentResult] = {}
        self.findings: list[str] = []
        self.failures: list[str] = []
        self.replan_count = 0
        self._plan: list[dict[str, Any]] = []
        self._plan_reason = ""
        self._t0 = time.perf_counter()
        # 用户拒绝审批 → 下一轮强制 replan
        self._forced_replan = False

    # ── Public API ────────────────────────────────────────────

    def start(self) -> str:
        """Begin execution. Runs until the first waiting point (or completion)."""
        self._ensure_executors()
        self._persist_run()
        self._run_loop()
        return self.run_id

    def approve(self, approved: bool, edits: dict[str, Any] | None = None) -> bool:
        """Respond to the pending approval gate. True if handled."""
        if self.status != "awaiting":
            return False
        gate_node = self._current_gate()
        if gate_node is None:
            return False
        self.store.respond_approval(self.run_id, gate_node.id, approved, edits)
        self._emit("approval_responded", {"run_id": self.run_id, "node_id": gate_node.id,
                                          "approved": approved})
        if approved:
            self.status = "running"
            self.store.update_run(self.run_id, status="running")
            self._run_loop()
        else:
            # Rejected → replan from the planner (new plan with reason).
            # Stashed as a forced replan; the graph is re-entered at the
            # replan node so the planner runs again with the reason.
            self.status = "running"
            self.store.update_run(self.run_id, status="running")
            self._plan_reason = f"用户拒绝执行，请调整计划。编辑意见：{json.dumps(edits or {}, ensure_ascii=False)}"
            self._forced_replan = True
            self._run_loop()
        return True

    @classmethod
    def resume_from_store(
        cls,
        run_id: str,
        definition: WorkflowDefinition,
        workspace: Path,
        objective: str,
        *,
        store: WorkflowStore | None = None,
        **kwargs: Any,
    ) -> "WorkflowRunner | None":
        """Rebuild a runner from persisted state (R-B2).

        Solves the "restart = stuck forever" gap: ``/approve`` used to
        require the live runner instance (registry.get → 404 after a
        restart). ``pre_completed`` is rebuilt from ``node_outputs``
        (R-B0 added the ``output`` column) and execution resumes at the
        first segment with pending nodes.

        Returns None when the run does not exist or is already terminal.
        """
        store = store or WorkflowStore()
        run = store.get_run(run_id)
        if run is None or run.get("status") in _TERMINAL_STATUSES:
            return None
        runner = cls(
            definition=definition,
            workspace=workspace,
            objective=objective or run.get("objective", ""),
            store=store,
            run_id=run_id,
            **kwargs,
        )
        runner.pre_completed = store.rebuild_agent_results(run_id)
        runner.segment_idx = int(run.get("segment_idx") or 0)
        try:
            runner.findings = list(json.loads(run.get("findings") or "[]"))
            runner.failures = list(json.loads(run.get("failures") or "[]"))
        except (json.JSONDecodeError, TypeError):
            pass
        runner.status = "running"
        store.update_run(run_id, status="running")
        runner._emit("run_resumed", {
            "run_id": run_id,
            "completed_nodes": sorted(runner.pre_completed),
            "segment_idx": runner.segment_idx,
        })
        return runner

    def status_snapshot(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "definition": self.definition.name,
            "status": self.status,
            "segment_idx": self.segment_idx,
            "segments_total": len(self.segments),
            "replan_count": self.replan_count,
            "replan_max": self.params.get("exec", {}).get("max_segments", 3),
            "completed_nodes": sorted(self.pre_completed.keys()),
            "findings": self.findings,
            "failures": self.failures,
            "elapsed_s": round(time.perf_counter() - self._t0, 2),
        }

    # ── Main loop ─────────────────────────────────────────────

    def _run_loop(self) -> None:
        """Drive execution via the graph compiler.

        R-B2 switched the main path; R-B3 deleted the legacy segment loop.
        The public contract (status / approve / pre_completed / snapshot)
        is unchanged: the compiler's run state is mirrored back after
        every run.
        """
        from .graph_compiler import APPROVAL_REASON, WorkflowGraphCompiler

        compiler = WorkflowGraphCompiler(
            self.definition,
            workspace=self.workspace,
            objective=self.objective,
            params=self.params,
            strategy_name=self.strategy_name,
            session_id=self.session_id,
            llm_config=self.llm_config,
            loop_factory=self.loop_factory,
            emit_event=self._emit,
            on_node_result=self._persist_node_result,
            on_segment_start=self._persist_segment_start,
            on_segment_done=self._persist_segment_done,
        )

        # Resume point: forced replan (approval rejected) wins — the user
        # asked for a new plan, so re-enter the replan loop rather than
        # pressing on with the old one.
        forced = bool(self._forced_replan)
        start = "__replan" if forced else self._resume_start(compiler)
        run_state = compiler.run(
            pre_completed=self.pre_completed,
            start=start,
            forced_replan=forced,
        )
        self._forced_replan = False
        self._sync_from_compiler(run_state, compiler)

    def _resume_start(self, compiler) -> str | None:
        """Which node to start the graph at (None = default entry).

        A segment counts as done only when all its nodes are in
        pre_completed. For the first pending segment, the approval gate
        that guards it is decisive:

        - approval still ``awaiting`` → start at the gate so it
          re-interrupts (the run stays paused until the user responds);
        - approval already ``approved`` → start at the segment entry,
          stepping past the gate;
        - no gate → start at the segment entry.
        """
        if not self.pre_completed:
            return None
        for seg in compiler.segments:
            if not any(nid not in self.pre_completed for nid in seg.node_ids):
                continue
            if seg.approval_after:
                record = self.store.get_approval(self.run_id, seg.approval_after)
                if record is None or record.get("status") != "approved":
                    return compiler._gate_name(seg)
            return compiler._segment_entry(seg)
        return None

    def _persist_node_result(self, node_id: str, segment_idx: int,
                             result: AgentResult) -> None:
        self.store.save_node_output(self.run_id, segment_idx, result)
        self._emit("node_completed", {
            "run_id": self.run_id, "node_id": node_id,
            "status": result.status.value if hasattr(result.status, "value")
            else str(result.status),
            "summary": (result.summary or "")[:120],
        })

    def _persist_segment_start(self, segment_idx: int, nodes: list[str]) -> None:
        self.store.upsert_segment(self.run_id, segment_idx, nodes,
                                  status="running")

    def _persist_segment_done(self, segment_idx: int, nodes: list[str]) -> None:
        self.store.upsert_segment(self.run_id, segment_idx, nodes,
                                  status="completed")

    def _sync_from_compiler(self, run_state, compiler) -> None:
        """Mirror compiled run state back onto the runner's public fields."""
        from .graph_compiler import APPROVAL_REASON

        self.pre_completed = dict(run_state.results)
        self.replan_count = run_state.replan_count
        for reason in run_state.failures:
            if reason not in self.failures:
                self.failures.append(reason)

        # segment_idx: first segment with pending nodes (UI progress).
        advanced = 0
        for seg in compiler.segments:
            if all(nid in self.pre_completed for nid in seg.node_ids):
                advanced = seg.index + 1
            else:
                break
        self.segment_idx = advanced
        self.store.update_run(self.run_id, segment_idx=self.segment_idx,
                              failures=self.failures)

        reason = run_state.stopped_reason
        if reason == APPROVAL_REASON:
            # 旧路径在 _approval_status() 里惰性建审批记录；编译器路径
            # 在 gate 中断后补建，保证 approve()/UI 能查到 awaiting 记录。
            gate = self._current_gate()
            if gate is not None and self.store.get_approval(
                self.run_id, gate.id) is None:
                self.store.create_approval(self.run_id, gate.id)
            self.status = "awaiting"
            self.store.update_run(self.run_id, status="awaiting")
            return
        if reason in ("segment_failures", "node_dispatch_error"):
            self.status = "completed" if reason == "segment_failures" else "failed"
            self.store.update_run(self.run_id, status=self.status)
            if reason == "node_dispatch_error":
                self._emit("run_failed", {
                    "run_id": self.run_id,
                    "error": "节点配置错误（NodeDispatchError）",
                })
            return
        self.status = "completed"
        self.store.update_run(self.run_id, status="completed",
                              findings=self.findings)

    # ── Approval gates ────────────────────────────────────────

    def _current_gate(self) -> WorkflowNode | None:
        if self.segment_idx < len(self.segments):
            gate_id = self.segments[self.segment_idx].approval_after
            if gate_id:
                for node in self.definition.nodes:
                    if node.id == gate_id:
                        return node
        return None

    # ── Persistence / events ──────────────────────────────────

    def _persist_run(self) -> None:
        self.store.create_run(
            self.run_id, self.definition.name, self.session_id, self.objective,
            params_snapshot={"params": self.params, "budget": self.definition.budget,
                             "llm": self.definition.llm},
        )

    def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        self.store.append_event(self.run_id, event_type, data)
        self.emit_event(event_type, data)

    def _ensure_executors(self) -> None:
        register_builtin_tool_executors()

    @staticmethod
    def _merge_params(default: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
        merged = json.loads(json.dumps(default))
        if not override:
            return merged
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value
        return merged


# ── Active-run registry (mirrors _active_runners in the API) ───


class WorkflowRunRegistry:
    """Tracks active WorkflowRunner instances by run_id."""

    def __init__(self) -> None:
        self._runs: dict[str, WorkflowRunner] = {}

    def put(self, runner: WorkflowRunner) -> None:
        self._runs[runner.run_id] = runner

    def get(self, run_id: str) -> WorkflowRunner | None:
        return self._runs.get(run_id)

    def pop(self, run_id: str) -> WorkflowRunner | None:
        return self._runs.pop(run_id, None)

    def prune(self) -> None:
        """Drop terminal runs."""
        for run_id in [rid for rid, r in self._runs.items()
                       if r.status in _TERMINAL_STATUSES]:
            self._runs.pop(run_id, None)

    def active(self) -> list[dict[str, Any]]:
        return [r.status_snapshot() for r in self._runs.values()]


__all__ = ["WorkflowRunner", "WorkflowRunRegistry", "WorkflowRunError"]
