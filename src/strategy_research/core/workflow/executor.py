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

Design: docs/workflow-module-design.md
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from ..swarm.runtime import AgentResult, AgentStatus
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
            # Rejected → replan from the planner (new plan with reason)
            self.status = "running"
            self.store.update_run(self.run_id, status="running")
            self._plan_reason = f"用户拒绝执行，请调整计划。编辑意见：{json.dumps(edits or {}, ensure_ascii=False)}"
            if not self._do_replan():
                return True
            if self.status == "running":
                self._run_loop()
        return True

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
        max_iterations = (len(self.segments) * 2 + 8) * (self.params.get("exec", {}).get("max_segments", 3) + 1)
        iterations = 0
        while self.status not in _TERMINAL_STATUSES and iterations < max_iterations:
            iterations += 1
            if self._budget_exceeded():
                self._finish_stop("时间预算超限")
                return

            gate = self._current_gate()
            if gate is not None and self._approval_status(gate.id) == "awaiting":
                self.status = "awaiting"
                self.store.update_run(self.run_id, status="awaiting")
                self._emit("awaiting_approval", {
                    "run_id": self.run_id, "node_id": gate.id,
                    "segment_idx": self.segment_idx,
                    "preview": "执行暂停，等待人工确认",
                })
                return  # wait for approve()

            if self.segment_idx < len(self.segments):
                segment = self.segments[self.segment_idx]
                self._run_segment(segment)
                continue

            # All static segments done → evaluate the final decision
            if self._last_decision_verdict() == "replan":
                if not self._do_replan():
                    return
                continue
            self._finish()
            return

        if self.status not in _TERMINAL_STATUSES:
            self._fail("执行循环异常终止（超过最大迭代）")

    def _run_segment(self, segment: WorkflowSegment) -> None:
        self.status = "running"
        self.store.upsert_segment(self.run_id, segment.index, segment.node_ids, status="running")
        self._emit("segment_started", {"run_id": self.run_id, "segment_idx": segment.index,
                                       "nodes": segment.node_ids})
        t0 = time.perf_counter()
        try:
            results = self._execute_segment_nodes(segment)
            self._emit("segment_completed", {"run_id": self.run_id, "segment_idx": segment.index,
                                             "results": {k: v.summary for k, v in results.items()}})
        except WorkflowRunError as exc:
            self.store.upsert_segment(self.run_id, segment.index, segment.node_ids,
                                      status="failed", error=str(exc))
            self._fail(str(exc))
            return
        self.store.upsert_segment(self.run_id, segment.index, segment.node_ids,
                                  status="completed",
                                  elapsed_s=round(time.perf_counter() - t0, 2))
        self.segment_idx += 1

    def _execute_segment_nodes(self, segment: WorkflowSegment) -> dict[str, AgentResult]:
        """Execute a segment's nodes in topological order, skipping completed."""
        from ..workflow.dag import topological_layers

        node_by_id = {n.id: n for n in segment.nodes}
        adj: dict[str, list[str]] = {n.id: [] for n in segment.nodes}
        for edge in segment.edges:
            adj[edge.target].append(edge.source)
        results: dict[str, AgentResult] = {}
        failures_this_segment: list[str] = []

        for layer in topological_layers(adj):
            for node_id in layer:
                if node_id in self.pre_completed:
                    results[node_id] = self.pre_completed[node_id]
                    continue
                node = node_by_id[node_id]
                result = self._execute_node(node, results)
                results[node_id] = result
                self.pre_completed[node_id] = result
                self.store.save_node_output(self.run_id, segment.index, result)
                self._emit("node_completed", {
                    "run_id": self.run_id, "node_id": node.id, "type": node.type,
                    "status": result.status.value if hasattr(result.status, "value") else str(result.status),
                    "summary": result.summary[:120],
                })
                if result.status == AgentStatus.ERROR:
                    failures_this_segment.append(node.id)
                    self.failures.append(f"{node.id}: {result.error or 'unknown'}")
                    self.store.update_run(self.run_id, failures=self.failures)

                # Planner expansion: run the produced plan steps right after
                # the planner, before any downstream node (e.g. evaluator).
                if node.type == "planner" and result.artifacts.get("plan"):
                    self._plan = list(result.artifacts["plan"])
                    self._emit("plan_created", {
                        "run_id": self.run_id, "steps": [s["id"] for s in self._plan],
                    })
                    self._run_plan_steps(results, failures_this_segment)
                    if self.status in _TERMINAL_STATUSES:
                        return results

        if len(failures_this_segment) >= 2:
            # Rule layer: 2 consecutive failures → stop
            self.failures.append("连续 2 步失败，规则层判定停止")
            self.store.update_run(self.run_id, failures=self.failures)
            self._finish_stop("连续 2 步失败，规则层判定停止")
        return results

    def _run_plan_steps(
        self,
        results: dict[str, AgentResult],
        failures_this_segment: list[str],
    ) -> None:
        """Execute planner-produced steps (plan_*) with internal dependencies."""
        from ..workflow.dag import topological_layers

        nodes: list[WorkflowNode] = []
        for step in self._plan:
            sid = step["id"]
            if not sid.startswith("plan_"):
                sid = f"plan_{sid}"
                step["id"] = sid
            cfg: dict[str, Any] = {"role": "researcher", "tools": step.get("tools") or [],
                                   "prompt_text": step.get("description", "")}
            nodes.append(WorkflowNode(id=sid, type=step.get("type", "llm_agent"),
                                      label=step.get("title", sid), config=cfg))
        if not nodes:
            self._plan = []
            return

        adj: dict[str, list[str]] = {n.id: [] for n in nodes}
        for step in self._plan:
            for dep in step.get("depends_on") or []:
                dep_id = dep if dep.startswith("plan_") else f"plan_{dep}"
                if dep_id in adj and dep_id != step["id"]:
                    adj[step["id"]].append(dep_id)
        node_by_id = {n.id: n for n in nodes}
        dynamic_segment_idx = -1

        for layer in topological_layers(adj):
            for node_id in layer:
                if node_id in self.pre_completed:
                    results[node_id] = self.pre_completed[node_id]
                    continue
                node = node_by_id[node_id]
                result = self._execute_node(node, results)
                results[node_id] = result
                self.pre_completed[node_id] = result
                self.store.save_node_output(self.run_id, dynamic_segment_idx, result)
                self._emit("node_completed", {
                    "run_id": self.run_id, "node_id": node.id, "type": node.type,
                    "status": result.status.value if hasattr(result.status, "value") else str(result.status),
                    "summary": result.summary[:120],
                })
                if result.status == AgentStatus.ERROR:
                    failures_this_segment.append(node.id)
                    self.failures.append(f"{node.id}: {result.error or 'unknown'}")
                    self.store.update_run(self.run_id, failures=self.failures)

    def _execute_node(self, node: WorkflowNode, results: dict[str, AgentResult]) -> AgentResult:
        ctx = NodeContext(
            workspace=self.workspace,
            strategy_name=self.strategy_name,
            objective=self.objective,
            session_id=self.session_id,
            upstream=dict(results),
            params=self.params,
            llm_config=self.llm_config,
            loop_factory=self.loop_factory,
            emit_event=self.emit_event,
        )
        try:
            return self._execute_node_with_retry(node, ctx)
        except NodeDispatchError as exc:
            logger.warning("node %s dispatch failed: %s", node.id, exc)
            raise
        except Exception as exc:  # noqa: BLE001
            return AgentResult(
                agent_id=node.id, status=AgentStatus.ERROR, error=str(exc),
            )

    def _execute_node_with_retry(
        self, node: WorkflowNode, ctx: "NodeContext",
    ) -> AgentResult:
        """``dispatch_node`` plus the ``exec.node_max_retries`` contract (B3).

        ``WorkflowDefinition``'s defaults declare ``node_max_retries: 2``
        (definition.py:51) but nothing read it. Retries are applied here, at
        the node boundary, and only for ERROR results -- a SUCCESS with a
        bad verdict is a decision, not a failure to retry.
        """
        retries = int((self.params.get("exec", {}) or {}).get("node_max_retries", 0) or 0)
        attempt = 0
        result = dispatch_node(node, ctx)
        while (
            result.status == AgentStatus.ERROR
            and attempt < retries
            and _retryable_node_error(result.error)
        ):
            attempt += 1
            logger.warning(
                "node %s failed (attempt %d/%d), retrying: %s",
                node.id, attempt, retries, result.error,
            )
            result = dispatch_node(node, ctx)
        return result

    # ── Planner dynamic segment ───────────────────────────────

    def _run_dynamic_segment(self) -> None:
        """Execute planner-produced steps (plan_*) — used by replan."""
        results = dict(self.pre_completed)
        failures: list[str] = []
        self._run_plan_steps(results, failures)

    def _finish(self) -> None:
        self._finish_stop("全部步骤完成")

    def _last_decision(self) -> dict[str, Any] | None:
        for result in reversed(list(self.pre_completed.values())):
            decision = result.artifacts.get("decision")
            if decision and isinstance(decision, dict):
                return decision
        return None

    def _last_decision_verdict(self) -> str:
        decision = self._last_decision()
        if decision:
            return str(decision.get("verdict", "continue"))
        return "continue"

    def _do_replan(self) -> bool:
        """Run planner again with the previous plan + reason; execute new steps."""
        max_segments = self.params.get("exec", {}).get("max_segments", 3)
        if self.replan_count >= max_segments:
            self._finish_stop(f"达到最大重规划次数（{max_segments}）")
            return False
        self.replan_count += 1

        # Drop stale dynamic step results (plan_* ids) from pre_completed
        stale = [k for k in self.pre_completed if k.startswith("plan_")]
        for key in stale:
            del self.pre_completed[key]

        planner_nodes = [n for n in self.definition.nodes if n.type == "planner"]
        if not planner_nodes:
            self._finish_stop("评估要求重规划但没有 planner 节点")
            return False

        reason = self._plan_reason or self._last_decision_reason()
        self._emit("plan_replan", {"run_id": self.run_id, "reason": reason})
        self._plan_reason = ""

        # Re-run the planner node
        planner = planner_nodes[0]
        ctx = NodeContext(
            workspace=self.workspace, strategy_name=self.strategy_name,
            objective=self.objective, session_id=self.session_id,
            upstream=self._evaluator_upstream(), params=self.params,
            llm_config=self.llm_config, loop_factory=self.loop_factory,
            emit_event=self.emit_event,
        )
        result = dispatch_node(planner, ctx)
        self.pre_completed[planner.id] = result
        self.store.save_node_output(self.run_id, 0, result)
        if result.status != AgentStatus.SUCCESS:
            self._fail(f"重规划失败：{result.error}")
            return False
        self._plan = result.artifacts.get("plan", [])
        self._emit("plan_created", {"run_id": self.run_id,
                                    "steps": [s["id"] for s in self._plan]})
        self._run_dynamic_segment()
        return True

    def _last_decision_reason(self) -> str:
        decision = self._last_decision()
        if decision:
            return str(decision.get("reason", ""))
        return "评估要求重新规划"

    def _evaluator_upstream(self) -> dict[str, AgentResult]:
        return self.pre_completed

    # ── Approval gates ────────────────────────────────────────

    def _current_gate(self) -> WorkflowNode | None:
        if self.segment_idx < len(self.segments):
            gate_id = self.segments[self.segment_idx].approval_after
            if gate_id:
                for node in self.definition.nodes:
                    if node.id == gate_id:
                        return node
        return None

    def _approval_status(self, node_id: str) -> str:
        record = self.store.get_approval(self.run_id, node_id)
        if record is None:
            self.store.create_approval(self.run_id, node_id)
            return "awaiting"
        return record["status"]

    # ── Budgets ───────────────────────────────────────────────

    def _budget_exceeded(self) -> bool:
        budget = self.definition.budget or {}
        time_limit = budget.get("time_seconds")
        if time_limit and (time.perf_counter() - self._t0) > float(time_limit):
            return True
        return False

    # ── Terminal ──────────────────────────────────────────────

    def _finish_stop(self, reason: str) -> None:
        self.status = "completed"
        self.store.update_run(self.run_id, status="completed", findings=self.findings)
        self._emit("run_completed", {"run_id": self.run_id, "reason": reason,
                                     "completed_nodes": sorted(self.pre_completed.keys())})

    def _fail(self, reason: str) -> None:
        self.status = "failed"
        self.store.update_run(self.run_id, status="failed", failures=self.failures)
        self._emit("run_failed", {"run_id": self.run_id, "error": reason})

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
