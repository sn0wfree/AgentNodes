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

    # ── StudyGraph interop 已随 apps/study 移除（2026-11-05 批次 3）──
    #
    # ``to_study_graph`` / ``from_study_graph`` 的唯一用途是与
    # ``apps/study/graph.py::StudyGraph``（graph.json 持久化）互转。
    # 该包已归档到 ``testcase/golden/apps/study/``，``study.graph``
    # capability kind 也一并删除 —— 两个方法失去目标类型，故移除。
    #
    # 需要恢复时：业务侧重新提供 StudyGraph（或任何等价的 DAG 表示），
    # 在 ``apps/capabilities.py`` 注册 ``study.graph``，再从 golden
    # 归档里取回这两个方法的实现（``git show <batch3-commit>^:...``）。
    # 连带移除只服务 ``to_study_graph`` 的 ``_plugin_node_type``。


__all__ = ["AgentDAGConfig", "AgentNodeConfig"]
