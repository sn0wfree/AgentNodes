"""Study execution graph: nodes + edges with topological layering.

The graph defines which agents run during a study round and how they
depend on each other. Multi-entry / multi-exit shapes are first-class
(topological BFS grouping parallel branches into the same layer).

Persistence:
    ``{workspace}/study/{study_id}/graph.json`` — written at study
    creation time by ``init_study_dir`` and read on every round by
    ``AutoresearchRunner``.

Validation:
    ``StudyGraph.validate()`` returns a list of error strings (empty on
    success). Errors include duplicate node ids, edges pointing at unknown
    nodes, and cycles.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── Node / Edge primitives ──────────────────────────────────────


@dataclass(frozen=True)
class GraphNode:
    """A single agent in the study execution graph."""

    id: str
    type: str  # "llm_agent" | "evaluator" | "planner" | "tool" | ...
    label: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True  # when False the runner skips this node but keeps it in the graph

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "label": self.label,
            "config": dict(self.config),
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GraphNode":
        return cls(
            id=str(data.get("id", "")),
            type=str(data.get("type", "")),
            label=str(data.get("label", "")),
            config=dict(data.get("config") or {}),
            enabled=bool(data.get("enabled", True)),
        )


@dataclass(frozen=True)
class GraphEdge:
    """Directed dependency: ``target`` runs after ``source`` completes."""

    source: str
    target: str
    condition: str | None = None  # reserved for v2 (e.g. "skip_if_source_failed")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"source": self.source, "target": self.target}
        if self.condition is not None:
            out["condition"] = self.condition
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GraphEdge":
        return cls(
            source=str(data.get("source", "")),
            target=str(data.get("target", "")),
            condition=data.get("condition"),
        )


# ── Graph container ─────────────────────────────────────────────


@dataclass(frozen=True)
class StudyGraph:
    """A validated study execution graph (nodes + edges)."""

    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]

    # ── accessors ──
    @property
    def node_ids(self) -> list[str]:
        return [n.id for n in self.nodes]

    @property
    def node_map(self) -> dict[str, GraphNode]:
        return {n.id: n for n in self.nodes}

    @property
    def enabled_node_ids(self) -> list[str]:
        return [n.id for n in self.nodes if n.enabled]

    # ── topological analysis ──
    def topological_layers(self) -> list[list[str]]:
        """BFS layering: returns ``[[layer1_ids], [layer2_ids], ...]``.

        A node enters the earliest layer where **all** of its upstream
        nodes have already been placed. Nodes with no upstreams
        (entries) are in layer 0; nodes with no downstreams (exits)
        are in the last layer.

        Multi-entry / multi-exit shapes work out naturally: two upstream
        nodes sharing a downstream end up in different layers (the
        later of the two), and the downstream waits for both.
        """
        node_set = set(self.node_ids)
        in_degree: dict[str, int] = {nid: 0 for nid in node_set}
        adjacency: dict[str, list[str]] = defaultdict(list)
        for edge in self.edges:
            if edge.source not in node_set or edge.target not in node_set:
                continue
            adjacency[edge.source].append(edge.target)
            in_degree[edge.target] += 1

        # BFS by layer — each iteration flushes one layer of nodes whose
        # upstreams are all satisfied; the layer list accumulates until
        # no more in-edges remain (cycles caught by validate()).
        layers: list[list[str]] = []
        current = sorted([nid for nid, d in in_degree.items() if d == 0])
        while current:
            layers.append(list(current))
            next_layer: list[str] = []
            for nid in current:
                for tgt in adjacency.get(nid, []):
                    in_degree[tgt] -= 1
                    if in_degree[tgt] == 0:
                        next_layer.append(tgt)
            current = sorted(next_layer)
        return layers

    def entry_ids(self) -> list[str]:
        """Nodes with no upstream — start of the graph (multi-entry)."""
        node_set = set(self.node_ids)
        has_upstream = {e.target for e in self.edges}
        return [nid for nid in node_set if nid not in has_upstream]

    def exit_ids(self) -> list[str]:
        """Nodes with no downstream — end of the graph (multi-exit)."""
        node_set = set(self.node_ids)
        has_downstream = {e.source for e in self.edges}
        return [nid for nid in node_set if nid not in has_downstream]

    def entry_count(self) -> int:
        return len(self.entry_ids())

    def exit_count(self) -> int:
        return len(self.exit_ids())

    # ── validation ──
    def validate(self) -> list[str]:
        """Return a list of error strings (empty == OK)."""
        errors: list[str] = []
        node_set = set(self.node_ids)
        seen: set[str] = set()
        for n in self.nodes:
            if not n.id:
                errors.append("node missing 'id'")
                continue
            if n.id in seen:
                errors.append(f"duplicate node id: {n.id!r}")
            seen.add(n.id)
        for e in self.edges:
            if e.source not in node_set:
                errors.append(
                    f"edge {e.source!r} -> {e.target!r}: source not in nodes"
                )
            if e.target not in node_set:
                errors.append(
                    f"edge {e.source!r} -> {e.target!r}: target not in nodes"
                )
        # Cycle detection via Kahn's algorithm on enabled nodes.
        enabled = [n.id for n in self.nodes if n.enabled]
        enabled_set = set(enabled)
        in_deg: dict[str, int] = {nid: 0 for nid in enabled_set}
        adj: dict[str, list[str]] = defaultdict(list)
        for e in self.edges:
            if e.source in enabled_set and e.target in enabled_set:
                adj[e.source].append(e.target)
                in_deg[e.target] += 1
        seen2: set[str] = set()
        queue: deque[str] = deque(sorted(nid for nid, d in in_deg.items() if d == 0))
        while queue:
            nid = queue.popleft()
            seen2.add(nid)
            for tgt in adj.get(nid, []):
                in_deg[tgt] -= 1
                if in_deg[tgt] == 0:
                    queue.append(tgt)
        if seen2 != enabled_set:
            missing = enabled_set - seen2
            errors.append(f"cycle detected: nodes {sorted(missing)!r} unreachable")
        return errors

    # ── serialization ──
    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StudyGraph":
        nodes = tuple(GraphNode.from_dict(n) for n in (data.get("nodes") or []))
        edges = tuple(GraphEdge.from_dict(e) for e in (data.get("edges") or []))
        return cls(nodes=nodes, edges=edges)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, raw: str) -> "StudyGraph":
        return cls.from_dict(json.loads(raw))

    # ── persistence ──
    def save(self, ws: Path, study_id: str) -> Path:
        """Write graph.json into ``{ws}/study/{study_id}/graph.json``."""
        root = ws / "study" / study_id
        root.mkdir(parents=True, exist_ok=True)
        p = root / "graph.json"
        p.write_text(self.to_json(), encoding="utf-8")
        return p

    @classmethod
    def load(cls, ws: Path, study_id: str) -> "StudyGraph | None":
        """Read graph.json if it exists. Returns None for missing/unreadable."""
        p = ws / "study" / study_id / "graph.json"
        if not p.is_file():
            return None
        try:
            return cls.from_json(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            logger.warning(
                "StudyGraph.load: failed to parse %s: %s", p, exc,
            )
            return None


__all__ = ["GraphNode", "GraphEdge", "StudyGraph"]
