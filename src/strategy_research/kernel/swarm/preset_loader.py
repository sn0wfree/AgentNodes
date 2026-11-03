"""Preset loader — load swarm presets from YAML files."""

from __future__ import annotations

import logging
from pathlib import Path

from .runtime import SwarmPreset
from .types import AgentCall

logger = logging.getLogger(__name__)

# Actions the branch machinery understands. ``redirect`` is documented as
# future work in runtime.py (_apply_branch_action) and only logs -- a preset
# that asks for it must fail loudly instead of silently doing nothing.
_SUPPORTED_BRANCH_ACTIONS = frozenset({"skip", "retry"})


def _reject_unsupported(data: dict, path: Path) -> None:
    """Fail loud on preset keys the runtime cannot honour (B3).

    Two silent-failure classes are closed here:

    - ``branches[].action: redirect`` -- runtime.py logs "not implemented"
      and carries on. A user who wrote it believed it took effect.
    - ``budget_token`` -- declared on SwarmPreset but never read
      (``_budget_exceeded`` checks turn/time only), so a token budget would
      silently never trigger.
    """
    for branch in data.get("branches") or []:
        if not isinstance(branch, dict):
            continue
        action = branch.get("action")
        if action and action not in _SUPPORTED_BRANCH_ACTIONS:
            raise ValueError(
                f"{path.name}: branch action {action!r} is not implemented "
                f"(supported: {sorted(_SUPPORTED_BRANCH_ACTIONS)})"
            )

    if data.get("budget_token") is not None:
        logger.warning(
            "%s: 'budget_token' is not enforced by SwarmRuntime "
            "(only budget_turn / budget_time_seconds are); ignoring it. "
            "Remove the key or use one of the supported budgets.",
            path.name,
        )


def load_preset(path: Path) -> SwarmPreset | None:
    """Load a swarm preset from a YAML file.

    Format:
        name: quant_research_team
        description: 量化研究团队
        agents:
          - id: researcher
            prompt_file: .prompts/researcher.md
            tools: [read_file, compute_factor]
          - id: factor_analyst
            prompt_file: .prompts/factor_analyst.md
            input_from: [researcher]
            tools: [compute_factor, run_backtest]
        dag:
          researcher: []
          factor_analyst: [researcher]
    """
    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML not installed, cannot load presets")
        return None

    try:
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load preset %s: %s", path, exc)
        return None

    if not isinstance(data, dict):
        logger.warning("Invalid preset format in %s", path)
        return None

    _reject_unsupported(data, path)

    name = data.get("name", path.stem)
    description = data.get("description", "")

    # Parse agents
    agents: list[AgentCall] = []
    for agent_data in data.get("agents", []):
        agent_id = agent_data.get("id", "")
        prompt_file = agent_data.get("prompt_file", "")
        tools = agent_data.get("tools", [])
        input_from = agent_data.get("input_from", [])

        agents.append(AgentCall(
            agent_name=agent_id,
            prompt=prompt_file,
            context={"tools": tools, "input_from": input_from},
        ))

    # Parse DAG
    dag: dict[str, list[str]] = {}
    for agent_id, deps in data.get("dag", {}).items():
        dag[agent_id] = list(deps) if deps else []

    return SwarmPreset(
        name=name,
        description=description,
        agents=agents,
        dag=dag,
    )


def list_presets(directory: Path) -> list[SwarmPreset]:
    """List all presets in a directory."""
    presets: list[SwarmPreset] = []
    if not directory.is_dir():
        return presets

    for yml_file in sorted(directory.glob("*.yaml")):
        preset = load_preset(yml_file)
        if preset is not None:
            presets.append(preset)

    for yml_file in sorted(directory.glob("*.yml")):
        preset = load_preset(yml_file)
        if preset is not None:
            presets.append(preset)

    return presets
