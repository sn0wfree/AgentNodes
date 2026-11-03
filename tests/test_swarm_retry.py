"""B3: swarm's declared-but-unhonoured contracts now behave as documented.

Contract: docs/stage-c1-b3-swarm-orchestration-design-20261028.md §3

Two classes of defect are covered:

1. ``AgentNodeConfig.max_retries`` was declared in dag_config.py but never
   read anywhere -- a promise the code did not keep.
2. Preset keys the runtime cannot honour (``branches[].action: redirect``,
   ``budget_token``) failed silently, which is worse than failing loudly.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from strategy_research.core.agent.dag_config import AgentNodeConfig
from strategy_research.core.agent.executor import (
    _NON_PROMPT_KEYS,
    AgentExecutionResult,
    AgentExecutor,
    _is_retryable_execution_error,
)
from strategy_research.core.agent.plugin import AgentPlugin

# ── §3.4: max_retries must not leak into the prompt ───────────


def test_max_retries_is_not_rendered_into_prompt():
    """It is an execution knob, not task context."""
    assert "max_retries" in _NON_PROMPT_KEYS


def test_max_retries_excluded_from_task_context_section():
    executor = AgentExecutor()
    plugin = AgentPlugin(
        id="x", name="x", category="execution",
        description="x", prompt_file=".prompts/x.md",
    )
    text = executor.build_task_text(
        plugin, "do it", {"max_retries": 2, "max_iterations": 5},
        None, None,
    )
    assert "max_retries" not in text
    assert "max_iterations" not in text


# ── §3.1: max_retries is honoured ────────────────────────────


def _plugin():
    return AgentPlugin(
        id="p", name="p", category="execution",
        description="p", prompt_file=".prompts/p.md",
    )


def test_retry_succeeds_after_transient_failure():
    calls = {"n": 0}

    def fake_exec_llm(*a, **kw):
        calls["n"] += 1
        if calls["n"] < 2:
            return AgentExecutionResult(
                agent_id="p", status="error",
                error="LLMServerError: 503 upstream",
            )
        return AgentExecutionResult(agent_id="p", status="success", output="ok")

    executor = AgentExecutor()
    node = AgentNodeConfig(id="p", max_retries=2)

    with patch.object(executor, "_exec_llm", side_effect=fake_exec_llm):
        result = executor._exec_llm_with_retry(
            _plugin(), "task", Path("."),
            context=None, upstream_outputs=None, previous_outputs=None,
            history=None, node=node, on_event=None, t0=0.0,
        )

    assert result.status == "success"
    assert calls["n"] == 2


def test_no_retry_when_max_retries_is_none():
    """Default is None -> existing behaviour unchanged."""
    calls = {"n": 0}

    def fake_exec_llm(*a, **kw):
        calls["n"] += 1
        return AgentExecutionResult(
            agent_id="p", status="error", error="LLMServerError: 503",
        )

    executor = AgentExecutor()
    node = AgentNodeConfig(id="p")  # max_retries defaults to None

    with patch.object(executor, "_exec_llm", side_effect=fake_exec_llm):
        result = executor._exec_llm_with_retry(
            _plugin(), "task", Path("."),
            context=None, upstream_outputs=None, previous_outputs=None,
            history=None, node=node, on_event=None, t0=0.0,
        )

    assert result.status == "error"
    assert calls["n"] == 1


def test_retry_stops_at_budget():
    calls = {"n": 0}

    def fake_exec_llm(*a, **kw):
        calls["n"] += 1
        return AgentExecutionResult(
            agent_id="p", status="error", error="LLMServerError: 503",
        )

    executor = AgentExecutor()
    node = AgentNodeConfig(id="p", max_retries=2)

    with patch.object(executor, "_exec_llm", side_effect=fake_exec_llm):
        result = executor._exec_llm_with_retry(
            _plugin(), "task", Path("."),
            context=None, upstream_outputs=None, previous_outputs=None,
            history=None, node=node, on_event=None, t0=0.0,
        )

    assert result.status == "error"
    assert calls["n"] == 3  # 1 initial + 2 retries


def test_permanent_errors_are_not_retried():
    """Repeating an auth/quota/config failure only burns time."""
    calls = {"n": 0}

    def fake_exec_llm(*a, **kw):
        calls["n"] += 1
        return AgentExecutionResult(
            agent_id="p", status="error", error="LLMAuthError: 401 bad key",
        )

    executor = AgentExecutor()
    node = AgentNodeConfig(id="p", max_retries=3)

    with patch.object(executor, "_exec_llm", side_effect=fake_exec_llm):
        executor._exec_llm_with_retry(
            _plugin(), "task", Path("."),
            context=None, upstream_outputs=None, previous_outputs=None,
            history=None, node=node, on_event=None, t0=0.0,
        )

    assert calls["n"] == 1


@pytest.mark.parametrize("error,retryable", [
    ("LLMServerError: 503", True),
    ("LLMTimeoutError: timed out", True),
    ("LLMMalformedResponseError: bad json", True),
    ("LLMAuthError: 401", False),
    ("LLMRateLimitError: 429", False),
    ("LLMQuotaError: quota", False),
    ("LLMConfigError: no key", False),
    ("empty system prompt for 'p'", False),
    (None, False),
])
def test_retryability_classification(error, retryable):
    assert _is_retryable_execution_error(error) is retryable


# ── §3.2 / §3.3: preset keys fail loud or warn ───────────────


def _write_preset(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "p.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_redirect_action_is_rejected(tmp_path):
    from strategy_research.kernel.swarm.preset_loader import load_preset

    path = _write_preset(tmp_path, """
name: t
agents:
  - id: a
    prompt_file: .prompts/a.md
dag:
  a: []
branches:
  - condition: "x > 1"
    action: redirect
    target: a
""")
    with pytest.raises(ValueError, match="redirect"):
        load_preset(path)


def test_skip_and_retry_are_allowed(tmp_path):
    from strategy_research.kernel.swarm.preset_loader import load_preset

    path = _write_preset(tmp_path, """
name: t
agents:
  - id: a
    prompt_file: .prompts/a.md
dag:
  a: []
branches:
  - condition: "x > 1"
    action: skip
    target: a
""")
    preset = load_preset(path)
    assert preset is not None


def test_budget_token_warns(tmp_path, caplog):
    from strategy_research.kernel.swarm.preset_loader import load_preset

    path = _write_preset(tmp_path, """
name: t
budget_token: 100000
agents:
  - id: a
    prompt_file: .prompts/a.md
dag:
  a: []
""")
    with caplog.at_level("WARNING"):
        preset = load_preset(path)
    # Still loads -- the action is a warning, not a hard failure.
    assert preset is not None
    assert any("budget_token" in r.message for r in caplog.records)
