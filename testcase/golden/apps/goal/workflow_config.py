"""YAML workflow config loader for Goal workflows.

Loads workflow definitions from YAML files, validates them, and
returns GoalWorkflowConfig instances.

Supported paths (in order of priority):
  1. Explicit path passed to load_goal_workflow()
  2. core/swarm/presets/goal_{name}.yaml
  3. ~/.quantnodes-research/workflows/{name}.yaml
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .workflow import (
    CompletionConfig,
    GoalAgentConfig,
    GoalWorkflowConfig,
    GoalWorkflowGoalConfig,
)

logger = logging.getLogger(__name__)

# 跟随 swarm 包实际位置解析（2026-11-03 W4d）：原先按本文件深度拼
# 'core/swarm/presets'，swarm 迁入 kernel 后该路径失效。
import strategy_research.kernel.swarm as _swarm_mod

_PRESETS_DIR = Path(_swarm_mod.__file__).parent / "presets"
_USER_WORKFLOWS_DIR = Path.home() / ".quantnodes-research" / "workflows"


def build_autoresearch_workflow_config(
    *,
    strategy_name: str,
    objective: str,
    metric_targets: list[dict[str, Any]] | None = None,
    monitor_interval_seconds: int | None = None,
    budget_turn: int | None = None,
    budget_time_seconds: int | None = None,
) -> GoalWorkflowConfig:
    """Build the 9-agent autoresearch ``GoalWorkflowConfig``.

    Single source of truth for the 9-agent preset (previously copy-pasted
    verbatim between ``api/routers/chat.py`` and ``api/routers/study.py``).
    """
    from .context import default_goal_criteria

    agent_configs = [
        GoalAgentConfig(id="researcher", prompt_file=".prompts/researcher.md",
                       tools=["read", "list_history", "factor_analysis", "websearch",
                              "webfetch", "get_market_data", "search_symbol"],
                       input_from=[], evidence_criterion=0, timeout=180, max_retries=3),
        GoalAgentConfig(id="data_quality", prompt_file=".prompts/data_quality.md",
                       tools=["read", "websearch", "webfetch", "get_market_data",
                              "list_data_sources"],
                       input_from=["researcher"], evidence_criterion=1, timeout=120, max_retries=2),
        GoalAgentConfig(id="factor_analyst", prompt_file=".prompts/factor_analyst.md",
                       tools=["read", "compute_factor", "factor_analysis", "get_market_data"],
                       input_from=["researcher", "data_quality"], evidence_criterion=1,
                       timeout=180, max_retries=3),
        GoalAgentConfig(id="strategist", prompt_file=".prompts/strategist.md",
                       tools=["read", "write", "run_backtest", "git_diff",
                              "websearch", "webfetch", "get_market_data"],
                       input_from=["researcher", "data_quality", "factor_analyst"],
                       evidence_criterion=2, timeout=240, max_retries=3),
        GoalAgentConfig(id="portfolio_construction", prompt_file=".prompts/portfolio_construction.md",
                       tools=["read", "get_market_data"],
                       input_from=["strategist"], evidence_criterion=2, timeout=120, max_retries=2),
        GoalAgentConfig(id="backtest", prompt_file=".prompts/backtest_diagnostics.md",
                       tools=[], input_from=["portfolio_construction"], evidence_criterion=2,
                       timeout=300, max_retries=1, executor_type="python_executor",
                       python_function="run_backtest_script"),
        GoalAgentConfig(id="risk_controller", prompt_file=".prompts/risk_controller.md",
                       tools=["read", "factor_analysis", "get_market_data"],
                       input_from=["backtest"], evidence_criterion=3, timeout=180, max_retries=2),
        GoalAgentConfig(id="attribution_analyst", prompt_file=".prompts/attribution_analyst.md",
                       tools=["read", "factor_analysis"],
                       input_from=["backtest", "risk_controller"], evidence_criterion=3,
                       timeout=180, max_retries=2),
        GoalAgentConfig(id="anti_overfit_analyst", prompt_file=".prompts/anti_overfit_analyst.md",
                       tools=["read", "list_history", "factor_analysis"],
                       input_from=["backtest", "risk_controller", "attribution_analyst"],
                       evidence_criterion=4, timeout=180, max_retries=2),
        GoalAgentConfig(id="backtest_diagnostics", prompt_file=".prompts/backtest_diagnostics.md",
                       tools=["read", "run_backtest", "git_diff"],
                       input_from=["anti_overfit_analyst"], evidence_criterion=4,
                       timeout=120, max_retries=2),
        GoalAgentConfig(id="decide", prompt_file=".prompts/backtest_diagnostics.md",
                       tools=[], input_from=["backtest", "anti_overfit_analyst", "backtest_diagnostics"],
                       evidence_criterion=4, timeout=60, max_retries=1,
                       executor_type="evaluator", python_function="decide"),
    ]

    return GoalWorkflowConfig(
        name=f"autoresearch_{strategy_name}",
        description=f"9-agent autoresearch: {objective}",
        goal=GoalWorkflowGoalConfig(
            default_criteria=default_goal_criteria(),
            risk_tier="research_general",
        ),
        agents=agent_configs,
        dag={
            "researcher": [],
            "data_quality": ["researcher"],
            "factor_analyst": ["researcher", "data_quality"],
            "strategist": ["researcher", "data_quality", "factor_analyst"],
            "portfolio_construction": ["strategist"],
            "backtest": ["portfolio_construction"],
            "risk_controller": ["backtest"],
            "attribution_analyst": ["backtest", "risk_controller"],
            "anti_overfit_analyst": ["backtest", "risk_controller", "attribution_analyst"],
            "backtest_diagnostics": ["anti_overfit_analyst"],
            "decide": ["backtest", "anti_overfit_analyst", "backtest_diagnostics"],
        },
        completion=CompletionConfig(
            mode="auto",
            metric_targets=metric_targets,
            monitor_interval_seconds=monitor_interval_seconds,
        ),
        budget_turn=budget_turn,
        budget_time_seconds=budget_time_seconds,
    )


def load_goal_workflow(
    name_or_path: str,
    *,
    base_dir: Path | None = None,
) -> "GoalWorkflowConfig":
    """Load a goal workflow config from YAML.

    Args:
        name_or_path: Either a workflow name (e.g. "factor_research")
                     or an explicit file path.
        base_dir: Optional base directory for resolving relative paths.

    Returns:
        Parsed GoalWorkflowConfig.

    Raises:
        FileNotFoundError: If the YAML file cannot be found.
        ValueError: If the YAML content is invalid.
    """
    from .workflow import (
        CompletionConfig,
        GoalAgentConfig,
        GoalWorkflowConfig,
        GoalWorkflowGoalConfig,
    )

    # Resolve the YAML file path
    yaml_path = _resolve_yaml_path(name_or_path)
    if yaml_path is None:
        raise FileNotFoundError(
            f"Workflow '{name_or_path}' not found. "
            f"Searched in: {_PRESETS_DIR}, {_USER_WORKFLOWS_DIR}"
        )

    # Load and parse YAML
    data = _load_yaml(yaml_path)

    # Parse goal config
    goal_data = data.get("goal", {})
    goal_config = GoalWorkflowGoalConfig(
        default_criteria=goal_data.get("default_criteria", []),
        risk_tier=goal_data.get("risk_tier", "research_general"),
    )

    # Parse agents
    agents = []
    for agent_data in data.get("agents", []):
        agent = GoalAgentConfig(
            id=agent_data["id"],
            prompt_file=agent_data.get("prompt_file", ""),
            tools=agent_data.get("tools", []),
            input_from=agent_data.get("input_from", []),
            evidence_criterion=agent_data.get("evidence_criterion", 0),
            timeout=agent_data.get("timeout", 120),
            max_retries=agent_data.get("max_retries", 3),
            condition=agent_data.get("condition"),
            executor_type=agent_data.get("executor_type", "llm"),
            python_function=agent_data.get("python_function"),
        )
        agents.append(agent)

    # Parse completion config
    completion_data = data.get("completion", {})
    completion = CompletionConfig(
        mode=completion_data.get("mode", "auto"),
        auto_audit=completion_data.get("auto_audit", True),
        require_all_evidence=completion_data.get("require_all_evidence", True),
    )

    # Parse DAG
    dag = data.get("dag", {})

    # Parse branches (optional)
    from .workflow import BranchConfig
    branches = []
    for branch_data in data.get("branches", []):
        branches.append(BranchConfig(
            condition=branch_data["condition"],
            action=branch_data["action"],
            target=branch_data["target"],
            reason=branch_data.get("reason", ""),
        ))

    config = GoalWorkflowConfig(
        name=data.get("name", yaml_path.stem),
        description=data.get("description", ""),
        version=data.get("version", "1.0"),
        goal=goal_config,
        agents=agents,
        dag=dag,
        completion=completion,
        branches=branches,
    )

    # Validate
    _validate_config(config)

    return config


def list_goal_workflows() -> list[dict[str, str]]:
    """List available goal workflow presets.

    Returns:
        List of dicts with 'name', 'description', and 'path' keys.
    """
    results = []

    # Search presets directory
    if _PRESETS_DIR.exists():
        for yaml_file in sorted(_PRESETS_DIR.glob("goal_*.yaml")):
            try:
                data = _load_yaml(yaml_file)
                results.append({
                    "name": data.get("name", yaml_file.stem),
                    "description": data.get("description", ""),
                    "path": str(yaml_file),
                })
            except Exception as exc:
                logger.warning("Failed to load %s: %s", yaml_file, exc)

    # Search user workflows directory
    if _USER_WORKFLOWS_DIR.exists():
        for yaml_file in sorted(_USER_WORKFLOWS_DIR.glob("*.yaml")):
            try:
                data = _load_yaml(yaml_file)
                results.append({
                    "name": data.get("name", yaml_file.stem),
                    "description": data.get("description", ""),
                    "path": str(yaml_file),
                })
            except Exception as exc:
                logger.warning("Failed to load %s: %s", yaml_file, exc)

    return results


# ── Internal Helpers ─────────────────────────────────────────


def _resolve_yaml_path(name_or_path: str) -> Path | None:
    """Resolve a workflow name or path to a YAML file."""
    path = Path(name_or_path)

    # If it's an explicit path that exists
    if path.exists() and path.suffix in (".yaml", ".yml"):
        return path

    # Search presets directory
    if _PRESETS_DIR.exists():
        preset = _PRESETS_DIR / f"goal_{name_or_path}.yaml"
        if preset.exists():
            return preset
        # Also try without goal_ prefix
        preset2 = _PRESETS_DIR / f"{name_or_path}.yaml"
        if preset2.exists():
            return preset2

    # Search user workflows directory
    if _USER_WORKFLOWS_DIR.exists():
        user_wf = _USER_WORKFLOWS_DIR / f"{name_or_path}.yaml"
        if user_wf.exists():
            return user_wf

    return None


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file and return the parsed data."""
    try:
        import yaml
    except ImportError:
        raise ImportError(
            "PyYAML is required for workflow config loading. "
            "Install with: pip install pyyaml"
        )

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Invalid YAML workflow file: {path}")

    return data


def _validate_config(config: "GoalWorkflowConfig") -> None:
    """Validate a GoalWorkflowConfig for consistency."""
    if not config.name:
        raise ValueError("Workflow config must have a name")

    if not config.agents:
        raise ValueError("Workflow config must define at least one agent")

    if not config.dag:
        raise ValueError("Workflow config must define a DAG")

    # Validate DAG references
    agent_ids = {a.id for a in config.agents}
    dag_nodes = set(config.dag.keys())
    for targets in config.dag.values():
        dag_nodes.update(targets)

    missing = dag_nodes - agent_ids
    if missing:
        raise ValueError(
            f"DAG references unknown agents: {missing}. "
            f"Known agents: {agent_ids}"
        )

    # Validate DAG is acyclic
    from strategy_research.kernel.workflow.dag import validate_dag
    validate_dag(config.dag)

    # Validate evidence_criterion indices
    num_criteria = len(config.goal.default_criteria)
    for agent in config.agents:
        if agent.evidence_criterion < 0:
            raise ValueError(
                f"Agent {agent.id} has invalid evidence_criterion: "
                f"{agent.evidence_criterion}"
            )
        if num_criteria > 0 and agent.evidence_criterion >= num_criteria:
            logger.warning(
                "Agent %s evidence_criterion %d exceeds criteria count %d",
                agent.id, agent.evidence_criterion, num_criteria,
            )


# ── Save (Phase 4 v0.5.5) ──────────────────────────────────


def save_goal_workflow(
    path: Path,
    config: "GoalWorkflowConfig",
    *,
    backup: bool = True,
    validate: bool = True,
) -> None:
    """Atomically write a GoalWorkflowConfig to YAML.

    Steps:
      1. Serialize config to YAML.
      2. If ``backup=True`` and ``path`` exists, rename to ``<path>.bak``.
      3. Write to ``<path>.tmp``.
      4. If ``validate=True``, run ``validate_dag()`` — on failure, restore.
      5. ``os.replace(tmp, path)``.

    Args:
        path: Destination YAML file path.
        config: GoalWorkflowConfig to serialize.
        backup: Whether to create a ``.bak`` file before overwriting.
        validate: Whether to validate the DAG before writing.

    Raises:
        ValueError: If the DAG is invalid (cycle detected).
        OSError: If the write fails.
    """
    import os

    try:
        import yaml
    except ImportError:
        raise ImportError(
            "PyYAML is required for saving workflow configs. "
            "Install with: pip install pyyaml"
        )

    # Validate DAG before writing
    if validate:
        from strategy_research.kernel.workflow.dag import validate_dag
        validate_dag(config.dag)

    # Serialize to dict
    data = {
        "name": config.name,
        "description": config.description,
        "version": config.version,
        "goal": {
            "default_criteria": config.goal.default_criteria,
            "risk_tier": config.goal.risk_tier,
        },
        "agents": [
            {
                "id": a.id,
                "prompt_file": a.prompt_file,
                "tools": a.tools,
                "input_from": a.input_from,
                "evidence_criterion": a.evidence_criterion,
                "timeout": a.timeout,
                "max_retries": a.max_retries,
                **({"condition": a.condition} if a.condition else {}),
            }
            for a in config.agents
        ],
        "dag": config.dag,
        "completion": {
            "mode": config.completion.mode,
            "auto_audit": config.completion.auto_audit,
            "require_all_evidence": config.completion.require_all_evidence,
        },
    }

    if config.branches:
        data["branches"] = [
            {
                "condition": b.condition,
                "action": b.action,
                "target": b.target,
                "reason": b.reason,
            }
            for b in config.branches
        ]

    yaml_str = yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)

    # Atomic write (shared impl)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Backup
    if backup and path.exists():
        bak_path = path.with_suffix(path.suffix + ".bak")
        bak_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

    from strategy_research.foundation.utils.io_utils import atomic_write_text
    try:
        # mkstemp-created files are 0600; preserve that (workflow YAMLs
        # can embed nothing secret today, but keep least privilege)
        atomic_write_text(path, yaml_str, mode=0o600)
    except Exception:
        # Restore from backup on failure
        if backup and path.exists():
            bak_path = path.with_suffix(path.suffix + ".bak")
            if bak_path.exists():
                os.replace(str(bak_path), str(path))
        raise


__all__ = [
    "load_goal_workflow",
    "list_goal_workflows",
    "save_goal_workflow",
]
