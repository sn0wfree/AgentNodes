"""B3: workflow's declared-but-unread contracts now behave as documented.

Contract: docs/stage-c1-b3-workflow-assessment-20261028.md §3

Four things WorkflowDefinition promised and nothing honoured:
  1. exec.node_max_retries  -- now applied at the node boundary
  2. exec.node_timeout_seconds -- now forwarded as iteration_timeout_s
  3. NodeContext._failures -- read by the evaluator, never written; removed
  4. decorators.py -- 163 lines, zero importers; deleted
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from strategy_research.core.swarm.runtime import AgentResult, AgentStatus
from strategy_research.core.workflow import node_types
from strategy_research.core.workflow.definition import WorkflowNode
from strategy_research.core.workflow.node_types import NodeContext, _retryable_node_error

# ── §3.1 node_timeout_seconds ────────────────────────────────


def _ctx(tmp_path, params=None) -> NodeContext:
    return NodeContext(
        workspace=tmp_path,
        strategy_name="s",
        objective="o",
        session_id="sess",
        upstream={},
        params=params or {},
        llm_config=None,
        loop_factory=None,
        emit_event=None,
    )


def test_node_timeout_seconds_becomes_iteration_timeout(tmp_path):
    node = WorkflowNode(id="n", type="llm_agent")
    kwargs = node_types._loop_kwargs(
        node, _ctx(tmp_path, {"exec": {"node_timeout_seconds": 300}}),
    )
    assert kwargs["iteration_timeout_s"] == 300.0


def test_per_node_config_wins_over_exec_default(tmp_path):
    node = WorkflowNode(id="n", type="llm_agent", config={"timeout": 45})
    kwargs = node_types._loop_kwargs(
        node, _ctx(tmp_path, {"exec": {"node_timeout_seconds": 300}}),
    )
    assert kwargs["iteration_timeout_s"] == 45.0


def test_no_timeout_when_unset(tmp_path):
    node = WorkflowNode(id="n", type="llm_agent")
    kwargs = node_types._loop_kwargs(node, _ctx(tmp_path, {}))
    assert "iteration_timeout_s" not in kwargs


# ── §3.1 node_max_retries ────────────────────────────────────


def _err(err: str = "LLMServerError: 503") -> AgentResult:
    return AgentResult(agent_id="n", status=AgentStatus.ERROR, error=err)


def _wf_executor():
    """The module that owns node retry after the R-B migration."""
    from strategy_research.core.workflow import graph_compiler as wf_executor

    return wf_executor


def _ok() -> AgentResult:
    return AgentResult(agent_id="n", status=AgentStatus.SUCCESS, output="ok")


def _runner(tmp_path, params):
    """R-B3: node retry now lives in WorkflowGraphCompiler._dispatch_with_retry.

    The contract (exec.node_max_retries at the node boundary) is unchanged;
    only its host moved when the segment loop was deleted.
    """
    from strategy_research.core.workflow.definition import WorkflowDefinition
    from strategy_research.core.workflow.graph_compiler import WorkflowGraphCompiler
    definition = WorkflowDefinition(name="t", params=params)
    return WorkflowGraphCompiler(
        definition,
        workspace=tmp_path,
        objective="o",
        params=params,
        strategy_name="s",
    )


def test_node_retries_on_transient_error(tmp_path):
    runner = _runner(tmp_path, {"exec": {"node_max_retries": 2}})
    node = WorkflowNode(id="n", type="llm_agent")
    calls = {"n": 0}

    def fake_dispatch(node_, ctx_):
        calls["n"] += 1
        return _ok() if calls["n"] >= 2 else _err()

    with patch.object(
        _wf_executor(), "dispatch_node",
        side_effect=fake_dispatch,
    ):
        result = runner._dispatch_with_retry(node, _ctx(tmp_path))

    assert result.status == AgentStatus.SUCCESS
    assert calls["n"] == 2


def test_node_retries_stop_at_budget(tmp_path):
    runner = _runner(tmp_path, {"exec": {"node_max_retries": 2}})
    node = WorkflowNode(id="n", type="llm_agent")
    calls = {"n": 0}

    def fake_dispatch(node_, ctx_):
        calls["n"] += 1
        return _err()

    with patch.object(
        _wf_executor(), "dispatch_node",
        side_effect=fake_dispatch,
    ):
        result = runner._dispatch_with_retry(node, _ctx(tmp_path))

    assert result.status == AgentStatus.ERROR
    assert calls["n"] == 3  # 1 + 2 retries


def test_no_retry_when_config_absent(tmp_path):
    """Default WorkflowDefinition.params has node_max_retries: 2 (definition.py:51),
    but an explicit empty params must not retry."""
    runner = _runner(tmp_path, {})
    node = WorkflowNode(id="n", type="llm_agent")
    calls = {"n": 0}

    def fake_dispatch(node_, ctx_):
        calls["n"] += 1
        return _err()

    with patch.object(
        _wf_executor(), "dispatch_node",
        side_effect=fake_dispatch,
    ):
        runner._dispatch_with_retry(node, _ctx(tmp_path))

    assert calls["n"] == 1


def test_permanent_node_error_is_not_retried(tmp_path):
    runner = _runner(tmp_path, {"exec": {"node_max_retries": 3}})
    node = WorkflowNode(id="n", type="llm_agent")
    calls = {"n": 0}

    def fake_dispatch(node_, ctx_):
        calls["n"] += 1
        return _err("无法构造 AgentLoop for role='x' (prompt 不存在)")

    with patch.object(
        _wf_executor(), "dispatch_node",
        side_effect=fake_dispatch,
    ):
        runner._dispatch_with_retry(node, _ctx(tmp_path))

    assert calls["n"] == 1


@pytest.mark.parametrize("error,retryable", [
    ("LLMServerError: 503", True),
    ("timeout after 30s", True),
    ("prompt 不存在", False),
    ("empty system prompt for 'x'", False),
    ("unknown node type 'zzz'", False),
    (None, False),
])
def test_retryability_classification(error, retryable):
    assert _retryable_node_error(error) is retryable


# ── §3.3 _failures removed ───────────────────────────────────


def test_evaluator_no_longer_reads_phantom_failures(tmp_path):
    """NodeContext never had _failures set; the evaluator read it anyway."""
    ctx = _ctx(tmp_path)
    assert not hasattr(ctx, "_failures")


# ── §3.2 decorators.py deleted ───────────────────────────────


def test_decorators_module_is_gone():
    import importlib.util

    spec = importlib.util.find_spec(
        "strategy_research.core.workflow.decorators"
    )
    assert spec is None, "decorators.py has zero importers and must stay deleted"
