"""WorkflowGraphCompiler — compile WorkflowDefinition into a GraphEngine.

R-B1（docs/plan-retry-fix-and-workflow-migration-20261031.md §2.2）：
把 `WorkflowDefinition` 编译成 GraphEngine 图，`dispatch_node` 作为节点
执行体——与 B1 的 SwarmRuntime 编译器同模式，消灭最后一个自研调度器。

本刀为**影子模式**：新模块不改 `WorkflowRunner`，由测试并行跑两边对比。

编译形状（逐条对齐 executor.py 现语义）：

    segment_0 … segment_n   按 segment_cut() 切分（approval 节点是切割点）
    每个段内：
      节点按 `ordered_ids()` 的拓扑序 **串行** 成链（现状层内也是 for 循环，
      不并行——不要在这里引入并行，那会改变行为）
      planner 之后插入合成节点 `__plan_<id>`：展开 plan_* 步骤（内部拓扑序）
      段的末尾是一个 `__failcheck_<i>` 节点：段内 ≥2 次失败 → 停（规则层）
    approval 门：独立节点抛 GraphInterrupt("approval_required")
      → run 返回带 __interrupt__ 的 state；恢复 = pop 标记 + run(start=段+1)

已知不支持（显式抛 Unsupported，留给 R-B2/R-B3）：
    - evaluator 判 replan（图内环）——B3
    - 影子阶段不写 store / 不发 SSE（B2 接线）

约定：
    - 节点 fn 返回**完整 state**（GraphEngine 契约）。
    - upstream = state["results"] 全量快照（与 executor 的 `dict(results)` 一致，
      不是仅直接依赖——这是现状语义，必须保真）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..runtimes import END, INTERRUPT_KEY, GraphEngine, GraphInterrupt
from ..swarm.runtime import AgentResult, AgentStatus
from .definition import WorkflowDefinition, WorkflowNode, WorkflowSegment
from .node_types import NodeContext, NodeDispatchError, _retryable_node_error, dispatch_node

logger = logging.getLogger(__name__)

APPROVAL_REASON = "approval_required"
_MAX_SEGMENT_FAILURES = 2


class WorkflowCompilerUnsupported(RuntimeError):
    """The definition uses a feature this compiler slice does not cover yet."""


@dataclass
class CompiledRunState:
    """Mutable run state carried through the graph.

    Kept as a dataclass inside ``state["run"]`` so node fns never rebuild
    the dict (GraphEngine passes the same object around).
    """

    results: dict[str, AgentResult] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    plan: list[dict[str, Any]] = field(default_factory=list)
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    stopped_reason: str = ""


class WorkflowGraphCompiler:
    """Definition → GraphEngine compiler (shadow slice, R-B1)."""

    def __init__(
        self,
        definition: WorkflowDefinition,
        *,
        workspace: Path,
        objective: str,
        params: dict[str, Any] | None = None,
        strategy_name: str = "default",
        session_id: str = "",
        llm_config: Any | None = None,
        loop_factory: Callable[..., str] | None = None,
        emit_event: Callable[[str, dict[str, Any]], None] | None = None,
        on_node_result: Callable[[str, int, AgentResult], None] | None = None,
        on_segment_start: Callable[[int, list[str]], None] | None = None,
        on_segment_done: Callable[[int, list[str]], None] | None = None,
    ) -> None:
        self.definition = definition
        self.workspace = Path(workspace)
        self.objective = objective
        self.params = params or dict(definition.params or {})
        self.strategy_name = strategy_name
        self.session_id = session_id
        self.llm_config = llm_config
        self.loop_factory = loop_factory
        self.emit_event = emit_event or (lambda _t, _d: None)
        # R-B2: persistence hook — (node_id, segment_idx, result).
        self.on_node_result = on_node_result or (lambda _n, _s, _r: None)
        # R-B2: segment lifecycle hooks (run_segments table writes).
        self.on_segment_start = on_segment_start or (lambda _i, _n: None)
        self.on_segment_done = on_segment_done or (lambda _i, _n: None)
        self.segments: list[WorkflowSegment] = definition.segment_cut()
        self._node_by_id = {n.id: n for n in definition.nodes}
        self._order_index = {
            nid: i for i, nid in enumerate(definition.ordered_ids())
        }
        # segment index → name of the node that ends that segment
        # (gate when an approval follows, else the segment-done marker).
        self._terminal_of_segment: dict[int, str] = {}
        # node id → segment index (for the persistence hook)
        self._segment_of_node: dict[str, int] = {
            nid: seg.index for seg in self.segments for nid in seg.node_ids
        }

    # ── Public ────────────────────────────────────────────────

    def compile(self, pre_completed: dict[str, AgentResult] | None = None) -> GraphEngine:
        """Build the engine. ``pre_completed`` seeds the run state (resume)."""
        self._run_state = CompiledRunState(results=dict(pre_completed or {}))
        self._terminal_of_segment = {}
        engine = GraphEngine(
            sink=self._sink,
            max_hops=2 * (len(self._node_by_id) + len(self.segments) + 4) * 4,
        )

        for seg in self.segments:
            self._compile_segment(engine, seg)

        # 段终端 → 下一段（或 END）。审批门属于"被它门控的那一段"之前：
        # 旧 runner 在 _run_loop 顶部查 segments[idx].approval_after，即在
        # 跑该段之前暂停。因此门节点挂在上一段终端与该段入口之间。
        for i in range(len(self.segments)):
            terminal = self._terminal_of_segment[i]
            if i + 1 < len(self.segments):
                nxt_seg = self.segments[i + 1]
                if nxt_seg.approval_after:
                    nxt = self._gate_name(nxt_seg)
                else:
                    nxt = self._segment_entry(nxt_seg)
            else:
                nxt = END
            engine.add_conditional_edge(terminal, self._make_router(nxt), None)

        # 门 → 它门控的那一段入口（获批后 run(start=...) 也走同一条路径）。
        for seg in self.segments:
            if seg.approval_after:
                engine.add_edge(self._gate_name(seg), self._segment_entry(seg))

        return engine

    def run(self, pre_completed: dict[str, AgentResult] | None = None,
            *, start: str | None = None) -> CompiledRunState:
        """Compile + execute; returns the final state object."""
        engine = self.compile(pre_completed)
        state = {
            "run": self._run_state,
            "pre_completed": dict(pre_completed or {}),
            "start_segment": 0,
        }
        if start is not None:
            state["pre_completed"] = dict(pre_completed or {})
        final = engine.run(state, start=start)
        interrupt = final.get(INTERRUPT_KEY)
        if isinstance(interrupt, dict):
            self._run_state.stopped_reason = str(interrupt.get("reason") or "")
        elif not self._run_state.stopped_reason:
            # 正常跑完：旧 _finish() 发 run_completed("全部步骤完成")。
            self._run_state.stopped_reason = "completed"
            self._emit(self._run_state, "run_completed", {
                "reason": "全部步骤完成",
                "completed_nodes": sorted(self._run_state.results),
            })
        return self._run_state

    @property
    def state(self) -> CompiledRunState:
        return self._run_state

    # ── Compilation helpers ───────────────────────────────────

    def _compile_segment(self, engine: GraphEngine, seg: WorkflowSegment) -> None:
        ordered = sorted(seg.node_ids, key=lambda nid: self._order_index.get(nid, 1 << 30))
        start_name = self._segment_start_name(seg)
        engine.add_node(start_name, self._make_segment_start_fn(seg))
        prev: str | None = start_name
        for node_id in ordered:
            node = self._node_by_id[node_id]
            if node.type == "approval":
                # Approval nodes never sit inside a segment (segment_cut
                # removes them); guard anyway so a malformed definition
                # fails loud rather than silently running it.
                raise WorkflowCompilerUnsupported(
                    f"approval node {node.id!r} found inside a segment"
                )
            engine.add_node(self._node_name(seg, node_id), self._make_node_fn(node))
            engine.add_edge(prev, self._node_name(seg, node_id))
            prev = self._node_name(seg, node_id)

            if node.type == "planner":
                plan_name = self._plan_name(seg, node_id)
                engine.add_node(plan_name, self._make_plan_fn(node))
                engine.add_edge(prev, plan_name)
                prev = plan_name

        # Approval gate BEFORE this segment (if any) becomes a node that
        # interrupts. `approval_after` names the gate gating *this*
        # segment (the old runner checked it at the top of _run_loop
        # before running the segment), so the node sits between the
        # previous segment's terminal and this segment's start.
        check = self._failcheck_name(seg)
        engine.add_node(check, self._make_failcheck_fn(seg))
        engine.add_edge(prev, check)

        done_name = self._segment_done_name(seg)
        engine.add_node(done_name, self._make_segment_done_fn(seg))
        engine.add_edge(check, done_name)
        self._terminal_of_segment[seg.index] = done_name

        if seg.approval_after:
            gate_name = self._gate_name(seg)
            engine.add_node(gate_name, self._make_gate_fn(seg))
            # The gate is entered from the previous segment (see compile);
            # after approval the caller resumes with run(start=<this
            # segment's entry>), so the gate's own edge targets that entry.
            # No edge is needed from this segment to its own gate.

    def _segment_entry(self, seg: WorkflowSegment) -> str:
        ordered = sorted(seg.node_ids, key=lambda nid: self._order_index.get(nid, 1 << 30))
        if not ordered:
            return END
        return self._node_name(seg, ordered[0])

    # ── Node factories ────────────────────────────────────────

    def _make_node_fn(self, node: WorkflowNode) -> Callable[[dict], dict]:
        def fn(state: dict) -> dict:
            run: CompiledRunState = state["run"]
            if node.id in run.results:
                return state  # already done (resume / retry skip)
            ctx = NodeContext(
                workspace=self.workspace,
                strategy_name=self.strategy_name,
                objective=self.objective,
                session_id=self.session_id,
                upstream=dict(run.results),
                params=self.params,
                llm_config=self.llm_config,
                loop_factory=self.loop_factory,
                emit_event=self.emit_event,
            )
            try:
                result = self._dispatch_with_retry(node, ctx)
            except NodeDispatchError as exc:
                # Config-level error: the old runner turned this into a
                # run-level failure. Mirror that.
                raise GraphInterrupt("node_dispatch_error", {"node": node.id,
                                                              "error": str(exc)}) from exc
            except Exception as exc:  # noqa: BLE001
                result = AgentResult(agent_id=node.id, status=AgentStatus.ERROR,
                                     error=str(exc))
            run.results[node.id] = result
            self.on_node_result(node.id, self._segment_of_node.get(node.id, 0), result)
            self._emit(run, "node_completed", {
                "node_id": node.id, "type": node.type,
                "status": result.status.value,
                "summary": (result.summary or "")[:120],
            })
            if result.status == AgentStatus.ERROR:
                run.failures.append(f"{node.id}: {result.error or 'unknown'}")
            return state

        return fn

    def _make_plan_fn(self, planner: WorkflowNode) -> Callable[[dict], dict]:
        """planner 之后立即展开 plan_* 步骤（内部拓扑序，串行）。

        与 executor._run_plan_steps 同构：plan 步骤 id 前缀 plan_、
        depends_on 决定层序、pre_completed 跳过、失败计数。
        """

        def fn(state: dict) -> dict:
            run: CompiledRunState = state["run"]
            planner_result = run.results.get(planner.id)
            plan = (planner_result.artifacts or {}).get("plan") if planner_result else None
            if not plan:
                return state
            run.plan = list(plan)
            self._emit(run, "plan_created", {
                "steps": [s.get("id") for s in run.plan],
            })
            self._run_plan_steps(run)
            return state

        return fn

    def _make_segment_start_fn(self, seg: WorkflowSegment) -> Callable[[dict], dict]:
        def fn(state: dict) -> dict:
            run: CompiledRunState = state["run"]
            self.on_segment_start(seg.index, seg.node_ids)
            self._emit(run, "segment_started", {
                "segment_idx": seg.index, "nodes": seg.node_ids,
            })
            return state

        return fn

    def _make_segment_done_fn(self, seg: WorkflowSegment) -> Callable[[dict], dict]:
        def fn(state: dict) -> dict:
            run: CompiledRunState = state["run"]
            self.on_segment_done(seg.index, seg.node_ids)
            self._emit(run, "segment_completed", {
                "segment_idx": seg.index,
                "results": {
                    k: (v.summary or "") for k, v in run.results.items()
                    if k in seg.node_ids
                },
            })
            return state

        return fn

    def _make_failcheck_fn(self, seg: WorkflowSegment) -> Callable[[dict], dict]:
        """段末规则层：本段（含 planner 展开的 plan_* 步骤）失败 ≥2 → 停。

        旧实现 `_execute_segment_nodes` 用 `failures_this_segment` 计数，
        plan 步骤也 append 进去（同段）。这里按同口径统计：段内节点 id
        与 plan_* 前缀都算本段。
        """

        def fn(state: dict) -> dict:
            run: CompiledRunState = state["run"]
            seg_ids = set(seg.node_ids)
            seg_failures = [
                f for f in run.failures
                if f.split(":", 1)[0] in seg_ids
                or f.split(":", 1)[0].startswith("plan_")
            ]
            if len(seg_failures) >= _MAX_SEGMENT_FAILURES:
                run.stopped_reason = "连续 2 步失败，规则层判定停止"
                run.failures.append(run.stopped_reason)
                self._emit(run, "run_completed", {
                    "reason": run.stopped_reason,
                    "completed_nodes": sorted(run.results),
                })
                raise GraphInterrupt("segment_failures", {
                    "segment_idx": seg.index, "reason": run.stopped_reason,
                })
            return state

        return fn

    def _make_gate_fn(self, seg: WorkflowSegment) -> Callable[[dict], dict]:
        def fn(state: dict) -> dict:
            run: CompiledRunState = state["run"]
            self._emit(run, "awaiting_approval", {
                "node_id": seg.approval_after,
                "segment_idx": seg.index,
                "preview": "执行暂停，等待人工确认",
            })
            raise GraphInterrupt(APPROVAL_REASON, {
                "node_id": seg.approval_after,
                "segment_idx": seg.index,
            })

        return fn

    def _make_router(self, next_node: str) -> Callable[[dict], str]:
        def route(state: dict) -> str:
            if INTERRUPT_KEY in state:
                return END
            return next_node

        return route

    # ── Plan steps (mirror of executor._run_plan_steps) ───────

    def _run_plan_steps(self, run: CompiledRunState) -> None:
        from .dag import topological_layers

        nodes: list[WorkflowNode] = []
        for step in run.plan:
            sid = str(step.get("id") or "")
            if not sid:
                continue
            if not sid.startswith("plan_"):
                sid = f"plan_{sid}"
                step["id"] = sid
            cfg = {
                "role": "researcher",
                "tools": step.get("tools") or [],
                "prompt_text": step.get("description", ""),
            }
            nodes.append(WorkflowNode(
                id=sid, type=step.get("type", "llm_agent"),
                label=step.get("title", sid), config=cfg,
            ))
        if not nodes:
            run.plan = []
            return

        adj: dict[str, list[str]] = {n.id: [] for n in nodes}
        for step in run.plan:
            for dep in step.get("depends_on") or []:
                dep_id = dep if str(dep).startswith("plan_") else f"plan_{dep}"
                if dep_id in adj and dep_id != step["id"]:
                    adj[step["id"]].append(dep_id)
        node_by_id = {n.id: n for n in nodes}

        for layer in topological_layers(adj):
            for node_id in layer:
                if node_id in run.results:
                    continue
                node = node_by_id[node_id]
                ctx = NodeContext(
                    workspace=self.workspace,
                    strategy_name=self.strategy_name,
                    objective=self.objective,
                    session_id=self.session_id,
                    upstream=dict(run.results),
                    params=self.params,
                    llm_config=self.llm_config,
                    loop_factory=self.loop_factory,
                    emit_event=self.emit_event,
                )
                try:
                    result = self._dispatch_with_retry(node, ctx)
                except NodeDispatchError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    result = AgentResult(agent_id=node_id, status=AgentStatus.ERROR,
                                         error=str(exc))
                run.results[node_id] = result
                self.on_node_result(node_id, self._segment_of_node.get(node_id, 0), result)
                self._emit(run, "node_completed", {
                    "node_id": node_id, "type": node.type,
                    "status": result.status.value,
                    "summary": (result.summary or "")[:120],
                })
                if result.status == AgentStatus.ERROR:
                    run.failures.append(f"{node_id}: {result.error or 'unknown'}")

    # ── Misc ──────────────────────────────────────────────────

    def _dispatch_with_retry(self, node: WorkflowNode, ctx: NodeContext) -> AgentResult:
        """Same contract as executor._execute_node_with_retry."""
        retries = int((self.params.get("exec", {}) or {}).get("node_max_retries", 0) or 0)
        attempt = 0
        result = dispatch_node(node, ctx)
        while (
            result.status == AgentStatus.ERROR
            and attempt < retries
            and _retryable_node_error(result.error)
        ):
            attempt += 1
            logger.warning("node %s failed (attempt %d/%d), retrying: %s",
                           node.id, attempt, retries, result.error)
            result = dispatch_node(node, ctx)
        return result

    def _emit(self, run: CompiledRunState, event: str, data: dict[str, Any]) -> None:
        run.events.append((event, data))
        self.emit_event(event, data)

    def _sink(self, kind: str, data: dict[str, Any]) -> None:
        # Engine-level events are not part of the workflow event contract;
        # the shadow comparison ignores them.
        pass

    def _node_name(self, seg: WorkflowSegment, node_id: str) -> str:
        return f"s{seg.index}_{node_id}"

    def _plan_name(self, seg: WorkflowSegment, node_id: str) -> str:
        return f"s{seg.index}_plan_{node_id}"

    def _failcheck_name(self, seg: WorkflowSegment) -> str:
        return f"s{seg.index}_failcheck"

    def _segment_start_name(self, seg: WorkflowSegment) -> str:
        return f"s{seg.index}_start"

    def _segment_done_name(self, seg: WorkflowSegment) -> str:
        return f"s{seg.index}_done"

    def _gate_name(self, seg: WorkflowSegment) -> str:
        return f"s{seg.index}_gate"


__all__ = [
    "APPROVAL_REASON",
    "CompiledRunState",
    "WorkflowCompilerUnsupported",
    "WorkflowGraphCompiler",
]
