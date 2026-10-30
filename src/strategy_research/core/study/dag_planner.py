"""DAGPlanner — LLM-driven DAG generation from research objectives.

Analyses a free-form objective and selects a suitable subset of
``AgentPlugin`` entries from the registry to compose the study graph.
Built-in YAML presets are summarized in the planner prompt as
few-shot candidates; the LLM decides which agent pipeline best fits
the research goal.

Usage::

    planner = DAGPlanner()
    result = planner.plan("研究 A 股动量因子，目标 Calmar >= 0.5")
    # result.config  GoalWorkflowConfig
    # result.selected_agents  list[str]
    # result.graph  StudyGraph (ready for graph.json persistence)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..agent.dag_config import AgentDAGConfig
from ..agent.plugin import AgentPlugin
from ..agent.registry import AgentPluginRegistry, get_default_registry

logger = logging.getLogger(__name__)


PLANNER_PROMPT_FILE = ".prompts/planner.md"


@dataclass
class PlannerConstraints:
    """Constraints applied to the planner output."""

    max_agents: int = 12
    exclude_agents: list[str] = field(default_factory=list)
    force_agents: list[str] = field(default_factory=list)


@dataclass
class PlanResult:
    """Composed plan returned by :meth:`DAGPlanner.plan`."""

    config: AgentDAGConfig
    selected_agents: list[str]
    reasoning: str = ""
    presets_referenced: list[str] = field(default_factory=list)
    graph: Any = None  # StudyGraph ready for graph.json persistence


class DAGPlanner:
    """LLM-driven DAG planner.

    When no real LLM is available (``should_use_real_llm()`` is False)
    :meth:`plan` falls back to deterministic selection based on the
    objective's keyword overlap with plugin ``keywords``.
    """

    def __init__(
        self,
        registry: AgentPluginRegistry | None = None,
        *,
        llm_config: Any | None = None,
        preset_yaml_dir: Path | None = None,
    ):
        self._registry = registry or get_default_registry()
        self._llm_config = llm_config
        self._preset_yaml_dir = (
            preset_yaml_dir
            or Path(__file__).parent.parent.parent
            / "core" / "swarm" / "presets"
        )

    def plan(
        self,
        objective: str,
        constraints: PlannerConstraints | None = None,
    ) -> PlanResult:
        constraints = constraints or PlannerConstraints()
        # Auto-completion of hard dependencies + forced + exclusion.
        forced = set(constraints.force_agents)
        excluded = set(constraints.exclude_agents)
        try:
            llm_selected = self._plan_via_llm(objective, constraints)
        except Exception as exc:  # noqa: BLE001
            logger.info("DAGPlanner LLM path unavailable: %s; using fallback", exc)
            llm_selected = None

        if llm_selected is None:
            llm_selected = self._plan_via_keywords(objective, constraints)

        # Apply constraints
        selected = forced | set(llm_selected)
        selected -= excluded
        selected = self._registry.complete_dependencies(selected)

        # Trim to max_agents
        if len(selected) > constraints.max_agents:
            selected = set(list(selected)[: constraints.max_agents])
            selected = self._registry.complete_dependencies(selected)

        config = self._build_config(sorted(selected), objective)
        graph = config.to_study_graph(self._registry)
        return PlanResult(
            config=config,
            selected_agents=sorted(selected),
            reasoning=f"selected {len(selected)} plugins",
            presets_referenced=[],
            graph=graph,
        )

    # ── LLM path ──────────────────────────────────────────────

    def _plan_via_llm(
        self,
        objective: str,
        constraints: PlannerConstraints,
    ) -> list[str] | None:
        if not self._llm_is_available():
            return None
        # P0 修复（2026-10-31）：层级错误的一级相对导入（`.agent` 解析为
        # study.agent，不存在）让 LLM 规划路径永远 ImportError，静默回退
        # 关键字匹配——「LLM 驱动 DAG 生成」实际从未生效。与模块头部
        # `from ..agent.dag_config import ...` 同级。
        from ..agent.prompt_builder import PromptBuilderFactory

        system_prompt = PromptBuilderFactory.get("planner").build_system_prompt(
            "planner", {},
        )
        if not system_prompt:
            return None
        catalog_text = self._format_catalog()
        presets_text = self._format_presets()
        user_prompt = (
            f"## 研究目标\n{objective}\n\n"
            f"## 约束\n"
            f"- 最多选择 {constraints.max_agents} 个 agent\n"
            f"- 必选: {', '.join(constraints.force_agents) or '无'}\n"
            f"- 排除: {', '.join(constraints.exclude_agents) or '无'}\n\n"
            f"## Agent Catalog\n{catalog_text}\n\n"
            f"## 参考预设\n{presets_text}\n\n"
            "请输出 JSON: {\"selected\": [\"agent_id_1\", \"agent_id_2\", ...], "
            "\"reasoning\": \"选择理由...\"}"
        )
        try:
            raw = self._call_llm(system_prompt, user_prompt)
        except Exception as exc:  # noqa: BLE001
            logger.debug("DAGPlanner LLM call failed: %s", exc)
            return None
        parsed = self._try_parse_json(raw)
        if not isinstance(parsed, dict):
            return None
        selected = parsed.get("selected") or []
        if not isinstance(selected, list):
            return None
        # Drop unknowns
        selected = [s for s in selected if isinstance(s, str) and self._registry.has(s)]
        return selected

    def _llm_is_available(self) -> bool:
        try:
            from .role_factory import should_use_real_llm
            return should_use_real_llm()
        except Exception:  # noqa: BLE001
            return False

    def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        from .role_factory import run_agent_via_llm

        # run_agent_via_llm needs workspace + strategy_name; pass empty
        # workspace (no tools needed for planner).
        return run_agent_via_llm(
            role="planner",
            workspace_path=Path("/tmp/dag-planner"),
            strategy_name="dag-planner",
            task=user_prompt,
            max_iterations=1,
        )

    def _try_parse_json(self, raw: str) -> Any:
        if not isinstance(raw, str):
            return raw
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass
        # Tolerate ```json ... ``` fences.
        for fence in ("```json", "```"):
            if fence in raw:
                try:
                    body = raw.split(fence, 1)[1].rsplit("```", 1)[0]
                    return json.loads(body)
                except (IndexError, json.JSONDecodeError):
                    continue
        return None

    # ── Keyword fallback ───────────────────────────────────────

    def _plan_via_keywords(
        self,
        objective: str,
        constraints: PlannerConstraints,
    ) -> list[str]:
        """Score plugins by keyword overlap with the objective.

        Always includes ``researcher`` + ``strategist`` + ``backtest`` +
        ``risk_controller`` (the minimum viable pipeline).
        """
        text = objective.lower()
        scored: list[tuple[int, AgentPlugin]] = []
        for plugin in self._registry.list_plugins():
            score = sum(1 for kw in plugin.keywords if kw.lower() in text)
            if score > 0:
                scored.append((score, plugin))
        scored.sort(key=lambda t: (-t[0], t[1].id))
        selected: list[str] = []
        for _, plugin in scored:
            if len(selected) >= constraints.max_agents - 4:
                break
            if plugin.id not in constraints.exclude_agents:
                selected.append(plugin.id)
        # Always include core required plugins
        for core in ("researcher", "strategist", "backtest", "risk_controller"):
            if core not in selected and core not in constraints.exclude_agents:
                selected.append(core)
        return selected

    # ── Formatting helpers ─────────────────────────────────────

    def _format_catalog(self) -> str:
        lines: list[str] = []
        for p in self._registry.list_plugins():
            kw = ", ".join(p.keywords) or "-"
            line = (
                f"- **{p.id}** [{p.category}/{p.executor_type}]: "
                f"{p.description} (keywords: {kw})"
            )
            if p.optional:
                line += " [optional]"
            else:
                line += " [required]"
            lines.append(line)
        return "\n".join(lines)

    def _format_presets(self) -> str:
        """Summarize available YAML presets as few-shot candidates."""
        d = self._preset_yaml_dir
        if not d.is_dir():
            return "(no presets available)"
        names: list[str] = []
        for f in sorted(d.glob("goal_*.yaml")):
            names.append(f.stem.replace("goal_", ""))
        return ", ".join(names)

    # ── Config builder ─────────────────────────────────────────

    def _build_config(
        self, selected: list[str], objective: str,
    ) -> AgentDAGConfig:
        nodes: list = []
        dag: dict[str, list[str]] = {}
        # Use the standard pipeline adjacency when all core nodes are
        # present; fall back to per-plugin requires for custom picks.
        from ..agent.builtin_plugins import standard_pipeline_adjacency

        adjacency = standard_pipeline_adjacency()
        for pid in selected:
            from ..agent.dag_config import AgentNodeConfig

            nodes.append(AgentNodeConfig(id=pid))
            if pid in adjacency:
                deps = [d for d in adjacency[pid] if d in selected]
            else:
                plugin = self._registry.get(pid)
                deps = [
                    d for d in (plugin.requires if plugin else ())
                    if d in selected
                ]
            dag[pid] = deps

        return AgentDAGConfig(
            name="planner_dag",
            description=f"LLM-planned DAG for: {objective[:80]}",
            nodes=nodes,
            dag=dag,
        )


__all__ = [
    "DAGPlanner",
    "PlannerConstraints",
    "PlanResult",
    "PLANNER_PROMPT_FILE",
]
