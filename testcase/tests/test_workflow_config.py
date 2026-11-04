"""Tests for core/goal/workflow_config.py — YAML workflow loader."""

from __future__ import annotations

import pytest

from strategy_research.apps.goal.workflow_config import (
    list_goal_workflows,
    load_goal_workflow,
    save_goal_workflow,
)

# ────────────────────────── list_goal_workflows ──────────────────────────


def test_list_returns_builtin_presets():
    workflows = list_goal_workflows()
    names = {w["name"] for w in workflows}
    # Built-in presets are seeded from src/.../swarm/presets/goal_*.yaml.
    assert "goal_autoresearch_9agent" in names
    assert "goal_factor_research" in names
    # Each entry includes description + path.
    for w in workflows:
        assert "name" in w
        assert "description" in w
        assert "path" in w


def test_list_ignores_unparseable_yaml(tmp_path, monkeypatch):
    """A broken YAML in the user dir must not break the whole list."""
    monkeypatch.setattr(
        "strategy_research.apps.goal.workflow_config._USER_WORKFLOWS_DIR",
        tmp_path,
    )
    (tmp_path / "broken.yaml").write_text("not: a, valid: yaml: :", encoding="utf-8")
    (tmp_path / "ok.yaml").write_text(
        "name: ok\ndescription: good\n", encoding="utf-8"
    )
    workflows = list_goal_workflows()
    names = {w["name"] for w in workflows}
    assert "ok" in names
    # The broken file is silently skipped (with a warning logged).
    assert "broken" not in names


# ────────────────────────── load_goal_workflow ──────────────────────────


def test_load_by_name_returns_builtin_preset():
    cfg = load_goal_workflow("factor_research")
    assert cfg.name in ("factor_research", "goal_factor_research")
    assert len(cfg.agents) > 0
    assert cfg.dag  # at least one entry


def test_load_by_explicit_path(tmp_path):
    yaml_file = tmp_path / "my_wf.yaml"
    yaml_file.write_text(
        """
name: custom
description: a tiny workflow
goal:
  default_criteria: ["x"]
  risk_tier: research_general
agents:
  - id: only
    tools: []
    input_from: []
    evidence_criterion: 0
    timeout: 60
    max_retries: 2
dag:
  only: []
completion:
  mode: auto
  auto_audit: true
  require_all_evidence: false
""",
        encoding="utf-8",
    )
    cfg = load_goal_workflow(str(yaml_file))
    assert cfg.name == "custom"
    assert len(cfg.agents) == 1
    assert cfg.agents[0].id == "only"


def test_load_unknown_name_raises_file_not_found():
    with pytest.raises(FileNotFoundError):
        load_goal_workflow("nonexistent-workflow-xyz")


def test_load_invalid_yaml_raises_value_error(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("- this\n- is\n- a\n- list\n- not\n- a\n- dict\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid YAML"):
        load_goal_workflow(str(bad))


def test_load_applies_default_values():
    cfg = load_goal_workflow("factor_research")
    # Defaults from the loader: timeout=120, max_retries=3, evidence_criterion=0
    for a in cfg.agents:
        assert a.timeout > 0
        assert a.max_retries > 0
        assert a.evidence_criterion >= 0


# ────────────────────────── save_goal_workflow ──────────────────────────


def test_save_round_trip(tmp_path):
    from strategy_research.apps.goal.workflow import (
        CompletionConfig,
        GoalAgentConfig,
        GoalWorkflowConfig,
        GoalWorkflowGoalConfig,
    )

    cfg = GoalWorkflowConfig(
        name="round-trip",
        description="test",
        version="1.0",
        goal=GoalWorkflowGoalConfig(
            default_criteria=["Sharpe > 1"], risk_tier="research_general",
        ),
        agents=[GoalAgentConfig(id="a", prompt_file=".prompts/a.md", tools=[], input_from=[], evidence_criterion=0, timeout=60, max_retries=2)],
        dag={"a": []},
        completion=CompletionConfig(
            mode="auto", auto_audit=True, require_all_evidence=False,
        ),
    )
    target = tmp_path / "rt.yaml"
    save_goal_workflow(target, cfg)
    assert target.exists()
    # Backup should NOT be created when target doesn't exist yet.
    assert not target.with_suffix(".yaml.bak").exists()

    # Reload and verify key fields round-trip.
    cfg2 = load_goal_workflow(str(target))
    assert cfg2.name == "round-trip"
    assert len(cfg2.agents) == 1
    assert cfg2.agents[0].id == "a"


def test_save_creates_backup_when_overwriting(tmp_path):
    from strategy_research.apps.goal.workflow import (
        CompletionConfig,
        GoalAgentConfig,
        GoalWorkflowConfig,
        GoalWorkflowGoalConfig,
    )

    target = tmp_path / "wf.yaml"
    target.write_text("name: placeholder\n", encoding="utf-8")

    cfg = GoalWorkflowConfig(
        name="new",
        description="",
        version="1.0",
        goal=GoalWorkflowGoalConfig(
            default_criteria=[], risk_tier="research_general",
        ),
        agents=[GoalAgentConfig(id="a", prompt_file=".prompts/a.md", tools=[], input_from=[])],
        dag={"a": []},
        completion=CompletionConfig(),
    )
    save_goal_workflow(target, cfg, backup=True)
    # The previous content was moved aside.
    assert target.with_suffix(".yaml.bak").exists()
    # New content is in place.
    assert "new" in target.read_text(encoding="utf-8")


def test_save_rejects_cycle_in_dag(tmp_path):
    from strategy_research.apps.goal.workflow import (
        CompletionConfig,
        GoalAgentConfig,
        GoalWorkflowConfig,
        GoalWorkflowGoalConfig,
    )

    cyclic = GoalWorkflowConfig(
        name="cycle",
        description="",
        version="1.0",
        goal=GoalWorkflowGoalConfig(
            default_criteria=[], risk_tier="research_general",
        ),
        agents=[
            GoalAgentConfig(id="a", prompt_file=".prompts/a.md", tools=[], input_from=["b"]),
            GoalAgentConfig(id="b", prompt_file=".prompts/b.md", tools=[], input_from=["a"]),
        ],
        dag={"a": ["b"], "b": ["a"]},
        completion=CompletionConfig(),
    )
    with pytest.raises(ValueError):
        save_goal_workflow(tmp_path / "cycle.yaml", cyclic)


def test_save_no_backup_when_disabled(tmp_path):
    from strategy_research.apps.goal.workflow import (
        CompletionConfig,
        GoalAgentConfig,
        GoalWorkflowConfig,
        GoalWorkflowGoalConfig,
    )

    target = tmp_path / "wf.yaml"
    target.write_text("name: placeholder\n", encoding="utf-8")
    cfg = GoalWorkflowConfig(
        name="new",
        description="",
        version="1.0",
        goal=GoalWorkflowGoalConfig(
            default_criteria=[], risk_tier="research_general",
        ),
        agents=[GoalAgentConfig(id="a", prompt_file=".prompts/a.md", tools=[], input_from=[])],
        dag={"a": []},
        completion=CompletionConfig(),
    )
    save_goal_workflow(target, cfg, backup=False)
    assert not target.with_suffix(".yaml.bak").exists()
