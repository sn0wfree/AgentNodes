"""AgentDAGConfig — unified DAG configuration format.

The one DAG representation consumed by every scheduler (study round
loop, SwarmRuntime layers). Converts to/from:
- ``StudyGraph`` (study graph.json persistence)
- plain dict (API payloads / JSON)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .plugin import AgentPlugin
from .registry import AgentPluginRegistry, get_default_registry


@dataclass
class AgentNodeConfig:
    """Per-node overrides on top of the referenced AgentPlugin."""

    id: str                          # must exist in the plugin registry
    enabled: bool = True
    label: str = ""
    timeout: int | None = None
    max_iterations: int | None = None
    max_retries: int | None = None
    tools_override: list[str] | None = None
    toolset: str | None = None                 # A6: named toolset (see agent.toolsets)
    budget_max_tokens: int | None = None       # A6: inherit/limit run budget
    budget_max_seconds: float | None = None
    context: dict[str, Any] | None = None   # extra context injection

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "enabled": self.enabled}
        if self.label:
            out["label"] = self.label
        for k in ("timeout", "max_iterations", "max_retries"):
            v = getattr(self, k)
            if v is not None:
                out[k] = v
        if self.tools_override is not None:
            out["tools_override"] = list(self.tools_override)
        if self.toolset is not None:
            out["toolset"] = self.toolset
        if self.budget_max_tokens is not None:
            out["budget_max_tokens"] = self.budget_max_tokens
        if self.budget_max_seconds is not None:
            out["budget_max_seconds"] = self.budget_max_seconds
        if self.context is not None:
            out["context"] = dict(self.context)
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AgentNodeConfig":
        return cls(
            id=str(d["id"]),
            enabled=bool(d.get("enabled", True)),
            label=str(d.get("label", "")),
            timeout=d.get("timeout"),
            max_iterations=d.get("max_iterations"),
            max_retries=d.get("max_retries"),
            tools_override=(
                list(d["tools_override"])
                if d.get("tools_override") is not None else None
            ),
            toolset=d.get("toolset"),
            budget_max_tokens=d.get("budget_max_tokens"),
            budget_max_seconds=d.get("budget_max_seconds"),
            context=dict(d["context"]) if d.get("context") else None,
        )


@dataclass
class AgentDAGConfig:
    """A complete agent DAG: nodes + upstream adjacency."""

    name: str
    description: str = ""
    nodes: list[AgentNodeConfig] = field(default_factory=list)
    dag: dict[str, list[str]] = field(default_factory=dict)
    budget_turn: int | None = None
    budget_time_seconds: float | None = None
    version: str = "1.0"

    # ── accessors ──
    def node_ids(self) -> list[str]:
        return [n.id for n in self.nodes if n.enabled]

    def node_map(self) -> dict[str, AgentNodeConfig]:
        return {n.id: n for n in self.nodes}

    def enabled_adjacency(self) -> dict[str, list[str]]:
        """Adjacency restricted to enabled nodes (deps on disabled
        nodes are dropped)."""
        enabled = set(self.node_ids())
        return {
            nid: [d for d in deps if d in enabled]
            for nid, deps in self.dag.items() if nid in enabled
        }

    def effective_plugin(
        self,
        node: AgentNodeConfig,
        registry: AgentPluginRegistry | None = None,
    ) -> AgentPlugin | None:
        """Resolve the node's plugin (None when unknown id)."""
        reg = registry or get_default_registry()
        return reg.get(node.id)

    # ── validation ──
    def validate(self, registry: AgentPluginRegistry | None = None) -> list[str]:
        """Return a list of error strings (empty == OK)."""
        from strategy_research.kernel.workflow.dag import validate_dag

        reg = registry or get_default_registry()
        errors: list[str] = []
        seen: set[str] = set()
        for n in self.nodes:
            if not n.id:
                errors.append("node missing 'id'")
                continue
            if n.id in seen:
                errors.append(f"duplicate node id: {n.id!r}")
            seen.add(n.id)
            if reg is not None and not reg.has(n.id):
                errors.append(f"unknown plugin id: {n.id!r}")

        node_set = seen
        for nid, deps in self.dag.items():
            if nid not in node_set:
                errors.append(f"dag references unknown node: {nid!r}")
            for d in deps:
                if d not in node_set:
                    errors.append(f"dag references unknown node: {d!r}")
                if d == nid:
                    errors.append(f"self-loop at {nid!r}")

        adj = {k: list(v) for k, v in self.dag.items()}
        if adj:
            try:
                validate_dag(adj)
            except ValueError:
                errors.append("cycle detected in dag")
        return errors

    # ── serialization ──
    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "nodes": [n.to_dict() for n in self.nodes],
            "dag": {k: list(v) for k, v in self.dag.items()},
            "budget_turn": self.budget_turn,
            "budget_time_seconds": self.budget_time_seconds,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AgentDAGConfig":
        return cls(
            name=str(d.get("name", "dag")),
            description=str(d.get("description", "")),
            nodes=[AgentNodeConfig.from_dict(n) for n in d.get("nodes", [])],
            dag={
                str(k): [str(x) for x in v]
                for k, v in (d.get("dag") or {}).items()
            },
            budget_turn=d.get("budget_turn"),
            budget_time_seconds=d.get("budget_time_seconds"),
            version=str(d.get("version", "1.0")),
        )

    # ── StudyGraph interop ──────────────────────────────────────

    def to_study_graph(self, registry: AgentPluginRegistry | None = None):
        """Convert to ``core.study.graph.StudyGraph`` for graph.json."""
        from strategy_research.kernel.agent.capability import resolve

        _g = resolve("study.graph")
        GraphEdge = _g.GraphEdge
        GraphNode = _g.GraphNode
        StudyGraph = _g.StudyGraph

        reg = registry or get_default_registry()
        nodes = []
        for n in self.nodes:
            plugin = reg.get(n.id)
            if plugin is None:
                continue
            config: dict[str, Any] = {
                "timeout": n.timeout if n.timeout is not None
                else plugin.default_timeout,
                "max_retries": n.max_retries if n.max_retries is not None
                else plugin.default_max_retries,
            }
            if n.max_iterations is not None:
                config["max_iterations"] = n.max_iterations
            if plugin.executor_type != "llm":
                config["executor_type"] = plugin.executor_type
                if plugin.python_function:
                    config["python_function"] = plugin.python_function
            if plugin.tools:
                config["tools"] = list(plugin.tools)
            nodes.append(GraphNode(
                id=n.id,
                type=_plugin_node_type(plugin),
                label=n.label or plugin.name,
                config=config,
                enabled=n.enabled,
            ))
        edges = [
            GraphEdge(source=dep, target=nid)
            for nid, deps in self.dag.items()
            for dep in deps
        ]
        return StudyGraph(nodes=tuple(nodes), edges=tuple(edges))

    @classmethod
    def from_study_graph(
        cls,
        graph,
        name: str = "study_dag",
        description: str = "",
        registry: AgentPluginRegistry | None = None,
    ) -> "AgentDAGConfig":
        """Build from a ``StudyGraph`` (graph.json)."""
        nodes: list[AgentNodeConfig] = []
        for gn in graph.nodes:
            cfg = dict(gn.config or {})
            nodes.append(AgentNodeConfig(
                id=gn.id,
                enabled=gn.enabled,
                label=gn.label,
                timeout=cfg.get("timeout"),
                max_iterations=cfg.get("max_iterations"),
                max_retries=cfg.get("max_retries"),
                context=None,
            ))
        dag: dict[str, list[str]] = {n.id: [] for n in nodes}
        for e in graph.edges:
            dag.setdefault(e.target, [])
            if e.source not in dag[e.target]:
                dag[e.target].append(e.source)
        return cls(name=name, description=description, nodes=nodes, dag=dag)


def _plugin_node_type(plugin: AgentPlugin) -> str:
    """Map a plugin to a StudyGraph node type."""
    if plugin.executor_type == "python":
        return "tool"
    if plugin.executor_type == "evaluator":
        return "evaluator"
    return {
        "research": "llm_agent",
        "execution": "llm_agent",
        "evaluation": "evaluator",
        "tool": "tool",
    }.get(plugin.category, "llm_agent")


__all__ = ["AgentDAGConfig", "AgentNodeConfig"]
