"""WorkflowDefinition — Dify-style modular workflow definition.

A definition is a typed node DAG persisted as JSON in
``workspace/workflows/`` (user, writable) or ``templates/workflows/``
(builtin, read-only).

Node types (see workflow/node_types.py for dispatch):
    llm_agent | planner | evaluator | approval | python | tool

Approval nodes never enter execution segments: they are graph cut
points.  Execution pauses between segments until the user approves
(see workflow/executor.py for the segment loop).

Design: docs/workflow-module-design.md
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Node type registry ─────────────────────────────────────────


NODE_TYPES: tuple[str, ...] = (
    "llm_agent", "planner", "evaluator", "approval", "python", "tool",
)

# Types that may appear at most once per definition
SINGLETON_TYPES: tuple[str, ...] = ("planner", "evaluator", "approval")

# Per-type required config keys (validated on load)
REQUIRED_CONFIG: dict[str, tuple[str, ...]] = {
    "llm_agent": ("role",),
    "planner": (),
    "evaluator": (),
    "approval": (),
    "python": ("function",),
    "tool": ("tool",),
}

ID_RE = re.compile(r"^[a-zA-Z_][\w-]*$")

DEFAULT_PARAMS: dict[str, Any] = {
    "llm": {"temperature": None, "max_tokens": None, "timeout_s": None},
    "loop": {"max_iterations": 8},
    "planner": {"max_steps": 6},
    "exec": {"max_segments": 3, "node_timeout_seconds": 300, "node_max_retries": 2},
    "approval": {"timeout": None},
    "summary": {"max_chars": 300},
}


class WorkflowDefinitionError(ValueError):
    """Invalid workflow definition (validation failures listed)."""


# ── Data models ────────────────────────────────────────────────


@dataclass
class WorkflowNode:
    """A single typed node in the workflow graph."""

    id: str
    type: str
    label: str = ""
    config: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowNode":
        return cls(
            id=str(data.get("id", "")),
            type=str(data.get("type", "")),
            label=str(data.get("label", "")),
            config=dict(data.get("config") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "label": self.label,
            "config": dict(self.config),
        }


@dataclass
class WorkflowEdge:
    """Directed dependency: target depends on source."""

    source: str
    target: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowEdge":
        return cls(source=str(data.get("source", "")), target=str(data.get("target", "")))

    def to_dict(self) -> dict[str, str]:
        return {"source": self.source, "target": self.target}


@dataclass
class WorkflowSegment:
    """A runnable slice of the definition graph.

    Segments are produced by cutting the graph at approval nodes.
    Approval nodes themselves are never inside a segment — they gate
    execution between segments (``approval_after``).
    """

    index: int
    nodes: list[WorkflowNode]
    edges: list[WorkflowEdge]
    inputs: list[str] = field(default_factory=list)  # upstream node ids feeding this segment
    approval_after: str | None = None  # approval node id gating this segment

    @property
    def node_ids(self) -> list[str]:
        return [n.id for n in self.nodes]


# ── Definition ─────────────────────────────────────────────────


@dataclass
class WorkflowDefinition:
    """A validated workflow definition (nodes + edges + params)."""

    name: str
    nodes: list[WorkflowNode] = field(default_factory=list)
    edges: list[WorkflowEdge] = field(default_factory=list)
    description: str = ""
    version: str = "1.0"
    budget: dict[str, Any] = field(default_factory=dict)
    llm: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    source: str = "user"  # builtin | user

    # ── Construction ──────────────────────────────────────────

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, source: str = "user") -> "WorkflowDefinition":
        nodes = [WorkflowNode.from_dict(n) for n in data.get("nodes", [])]
        edges = [WorkflowEdge.from_dict(e) for e in data.get("edges", [])]
        params = dict(data.get("params") or {})
        merged_params = _deep_merge(DEFAULT_PARAMS, params)
        return cls(
            name=str(data.get("name", "")),
            nodes=nodes,
            edges=edges,
            description=str(data.get("description", "")),
            version=str(data.get("version", "1.0")),
            budget=dict(data.get("budget") or {}),
            llm=dict(data.get("llm") or {}),
            params=merged_params,
            source=source,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "budget": dict(self.budget),
            "llm": dict(self.llm),
            "params": dict(self.params),
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "source": self.source,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    # ── File I/O ──────────────────────────────────────────────

    @classmethod
    def load(cls, path: Path, *, source: str = "user") -> "WorkflowDefinition":
        """Load from JSON file and validate. Raises WorkflowDefinitionError."""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise WorkflowDefinitionError(f"invalid JSON in {path}: {exc}") from exc
        definition = cls.from_dict(raw, source=source)
        errors = definition.validate()
        if errors:
            raise WorkflowDefinitionError(f"definition '{definition.name}' invalid: " + "; ".join(errors))
        return definition

    def save(self, path: Path) -> None:
        """Write JSON to path (validating first)."""
        errors = self.validate()
        if errors:
            raise WorkflowDefinitionError(
                f"definition '{self.name}' invalid: " + "; ".join(errors)
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")

    # ── Validation ────────────────────────────────────────────

    def validate(self) -> list[str]:
        """Return a list of validation errors (empty = valid)."""
        errors: list[str] = []

        if not self.name or not ID_RE.match(self.name):
            errors.append("name must match ^[a-zA-Z_][\\w-]*$")
        if not self.nodes:
            errors.append("nodes must not be empty")

        seen_ids = self._validate_nodes(errors)

        for stype in SINGLETON_TYPES:
            count = sum(1 for n in self.nodes if n.type == stype)
            if count > 1:
                errors.append(f"type '{stype}' may appear at most once (found {count})")

        node_ids = set(seen_ids)
        self._validate_edges(errors, node_ids)

        # Cycle detection via topological sort
        try:
            self.topological_layers()
        except WorkflowDefinitionError as exc:
            errors.append(str(exc))

        # Orphan nodes (no edges at all) — allowed only if the graph is a
        # single node; otherwise require connectivity for segment purposes.
        if len(self.nodes) > 1:
            self._validate_orphans(errors)

        # Value-domain checks
        self._validate_params(errors)

        return errors

    def _validate_nodes(self, errors: list[str]) -> dict[str, str]:
        """Validate node ids/types/config; returns id→type map."""
        seen_ids: dict[str, str] = {}
        for node in self.nodes:
            if not node.id or not ID_RE.match(node.id):
                errors.append(f"node id '{node.id}' must match ^[a-zA-Z_][\\w-]*$")
                continue
            if node.id in seen_ids:
                errors.append(f"duplicate node id '{node.id}'")
                continue
            seen_ids[node.id] = node.type
            if node.type not in NODE_TYPES:
                errors.append(f"node '{node.id}': unknown type '{node.type}'")
                continue
            for key in REQUIRED_CONFIG.get(node.type, ()):
                if not node.config.get(key):
                    errors.append(f"node '{node.id}': missing required config '{key}'")
        return seen_ids

    def _validate_edges(self, errors: list[str], node_ids: set[str]) -> None:
        """Validate edge endpoints and self-loops."""
        for edge in self.edges:
            if edge.source not in node_ids:
                errors.append(f"edge source '{edge.source}' not found")
            if edge.target not in node_ids:
                errors.append(f"edge target '{edge.target}' not found")
            if edge.source == edge.target:
                errors.append(f"self-loop on '{edge.source}'")

    def _validate_orphans(self, errors: list[str]) -> None:
        """Flag nodes with no incident edges (require connectivity)."""
        connected = set()
        for edge in self.edges:
            connected.add(edge.source)
            connected.add(edge.target)
        for node in self.nodes:
            if node.id not in connected:
                errors.append(f"node '{node.id}' is orphaned (no edges)")

    def _validate_params(self, errors: list[str]) -> None:
        """Validate params value domains (max_steps / temperature)."""
        max_steps = self.params.get("planner", {}).get("max_steps", 6)
        if not isinstance(max_steps, int) or not (3 <= max_steps <= 8):
            errors.append(f"params.planner.max_steps must be int in [3, 8], got {max_steps!r}")
        temp = self.params.get("llm", {}).get("temperature")
        if temp is not None and not (0 <= float(temp) <= 2):
            errors.append(f"params.llm.temperature must be in [0, 2], got {temp!r}")

    # ── Topology ──────────────────────────────────────────────

    def adjacency(self) -> dict[str, list[str]]:
        adj: dict[str, list[str]] = {n.id: [] for n in self.nodes}
        for edge in self.edges:
            adj[edge.target].append(edge.source)
        return adj

    def topological_layers(self) -> list[list[str]]:
        """Return node ids in topological layers (raises on cycle)."""
        from ..workflow.dag import topological_layers as _tl
        try:
            return _tl(self.adjacency())
        except Exception as exc:  # dag module raises ValueError on cycles
            raise WorkflowDefinitionError(f"cycle detected: {exc}") from exc

    def ordered_ids(self) -> list[str]:
        """All node ids in a deterministic topological order."""
        ordered: list[str] = []
        for layer in self.topological_layers():
            ordered.extend(sorted(layer))
        return ordered

    # ── Graph cutting (approval nodes are cut points) ─────────

    def segment_cut(self) -> list[WorkflowSegment]:
        """Cut the graph into segments at approval nodes.

        Approval nodes never enter segments; a segment's
        ``approval_after`` names the approval gate that precedes it.
        A leading approval (no upstream work) is skipped entirely.
        """
        approval_ids = [n.id for n in self.nodes if n.type == "approval"]
        if not approval_ids:
            return [self._make_segment(0, [n for n in self.nodes], None)]

        ordered = self.ordered_ids()
        # Split topological order at each approval node
        buckets: list[list[str]] = [[]]
        for nid in ordered:
            if nid in approval_ids:
                if buckets[-1]:
                    buckets.append([])
            else:
                buckets[-1].append(nid)
        buckets = [b for b in buckets if b]

        segments: list[WorkflowSegment] = []
        # Map each bucket to the approval that gates it: the approval
        # whose position in the topological order immediately precedes
        # the bucket.  Reconstructed via the original order.
        approval_positions = [i for i, nid in enumerate(ordered) if nid in approval_ids]

        cursor = 0
        for bucket in buckets:
            first_pos = ordered.index(bucket[0])
            gate: str | None = None
            for pos in approval_positions:
                if pos < first_pos:
                    candidate = ordered[pos]
                    # An approval with no upstream work is a no-op gate
                    if self._has_upstream(candidate):
                        gate = candidate
            segments.append(self._make_segment(cursor, [n for n in self.nodes if n.id in bucket], gate))
            cursor += 1
        return segments

    def _has_upstream(self, node_id: str) -> bool:
        """True if the node has any upstream node (through non-self edges)."""
        return any(e.target == node_id for e in self.edges)

    def _make_segment(
        self, index: int, nodes: list[WorkflowNode], gate: str | None,
    ) -> WorkflowSegment:
        node_ids = {n.id for n in nodes}
        edges = [e for e in self.edges if e.source in node_ids and e.target in node_ids]
        return WorkflowSegment(
            index=index, nodes=nodes, edges=edges,
            inputs=self._segment_inputs(node_ids),
            approval_after=gate,
        )

    def _segment_inputs(self, node_ids: set[str]) -> list[str]:
        """Executable upstream node ids feeding this segment.

        Cross-segment edges are walked back through approval chains
        (approval nodes produce no output — they only gate).  A
        segment depends on the *executable* nodes upstream of it.
        """
        all_ids = {n.id for n in self.nodes}
        by_id = {n.id: n for n in self.nodes}
        inputs: list[str] = []

        def collect(node_id: str) -> list[str]:
            node = by_id.get(node_id)
            if node is None:
                return []
            if node.type != "approval":
                return [node_id]
            result: list[str] = []
            for edge in self.edges:
                if edge.target == node_id:
                    result.extend(collect(edge.source))
            return result

        for edge in self.edges:
            if edge.target in node_ids and edge.source not in node_ids:
                for src in collect(edge.source):
                    if src in all_ids and src not in node_ids and src not in inputs:
                        inputs.append(src)
        return inputs

    # ── SwarmPreset conversion (graph API / auditing) ─────────

    def to_swarm_preset(self, segment: WorkflowSegment, objective: str) -> Any:
        """Convert a segment into a SwarmPreset for the execution layer.

        Segment nodes map to AgentCalls: python/tool nodes set
        executor_type=python_executor; LLM nodes keep the default
        (dispatch in workflow/node_types.py handles execution).
        """
        from strategy_research.kernel.swarm.types import SwarmPreset
        from ..workflow.types import AgentCall

        agents: list[AgentCall] = []
        dag: dict[str, list[str]] = {}
        segment_ids = set(segment.node_ids)

        for node in segment.nodes:
            ctx: dict[str, Any] = dict(node.config)
            ctx["node_type"] = node.type
            ctx["node_label"] = node.label
            if node.type in ("python", "tool"):
                ctx["executor_type"] = "python_executor"
                ctx["python_function"] = node.config.get("function") or node.config.get("tool")
            agents.append(AgentCall(
                agent_name=node.id,
                prompt=objective,
                context=ctx,
                metadata={"label": node.label, "node_type": node.type},
            ))
            dag[node.id] = [
                e.source for e in self.edges
                if e.target == node.id and e.source in segment_ids
            ]

        budget = self.budget or {}
        return SwarmPreset(
            name=f"{self.name}#seg{segment.index}",
            description=self.description,
            agents=agents,
            dag=dag,
            version=self.version,
            budget_token=budget.get("token"),
            budget_turn=budget.get("turn"),
            budget_time_seconds=budget.get("time_seconds"),
        )


# ── Helpers ────────────────────────────────────────────────────


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into a copy of base."""
    merged = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


__all__ = [
    "NODE_TYPES", "SINGLETON_TYPES", "REQUIRED_CONFIG", "DEFAULT_PARAMS",
    "WorkflowDefinitionError", "WorkflowNode", "WorkflowEdge",
    "WorkflowSegment", "WorkflowDefinition",
]
