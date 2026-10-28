"""W-P: ``agent_node`` — the node-level adapter over AgentLoop.

Architecture (option B, 2026-10-28):
    GraphEngine orchestrates (nodes / edges / interrupts / budget tree).
    A single agent turn is executed by AgentLoop -- it is the execution
    primitive, not a second control flow.

So these tests assert the *adapter* contract, not a reimplementation:
the node returns an AgentResult-shaped dict, errors become results instead
of exceptions, and the full AgentLoop capability set is preserved (which is
the whole reason option B was chosen over a thin react loop).

Decision record: docs/stage-c1-wp-agent-node-design-20261028.md
"""
from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from strategy_research.core.agent import role_factory
from strategy_research.core.agent.loop import AgentLoop, LoopResult
from strategy_research.core.agent.role_factory import agent_node


def _ok(answer='{"ok": true}', iterations=1, tools=0):
    return LoopResult(
        answer=answer, iterations=iterations, tool_calls_made=tools,
        finished_reason="stop",
    )


def _err(msg, iterations=1):
    return LoopResult(
        answer="", iterations=iterations, tool_calls_made=0,
        finished_reason="error", error=msg,
    )


@pytest.fixture
def ws(tmp_path):
    return tmp_path


# ── the adapter keeps AgentLoop as the execution primitive ────


def test_agent_node_builds_an_agentloop(ws):
    """Option B is explicit: the node wraps AgentLoop, it does not replace it."""
    mock_loop = MagicMock()
    mock_loop.run.return_value = _ok()

    with patch.object(role_factory, "build_agent_loop", return_value=mock_loop):
        agent_node(role="researcher", workspace_path=ws,
                   strategy_name="s", task="do it")

    mock_loop.run.assert_called_once_with("do it")


def test_build_agent_loop_still_constructs_agentloop():
    """Guard against a future refactor swapping in a thinner runtime."""
    src = inspect.getsource(role_factory.build_agent_loop)
    assert "AgentLoop(" in src


# ── result envelope ───────────────────────────────────────────


def test_success_maps_to_agent_result_shape(ws):
    mock_loop = MagicMock()
    mock_loop.run.return_value = _ok('{"a": 1}', iterations=2, tools=1)

    with patch.object(role_factory, "build_agent_loop", return_value=mock_loop):
        out = agent_node(role="researcher", workspace_path=ws,
                         strategy_name="s", task="t")

    assert set(out) >= {
        "status", "output", "error", "elapsed_s", "summary",
        "metrics", "artifacts", "iterations", "tool_calls_made",
    }
    assert out["status"] == "success"
    assert out["output"] == '{"a": 1}'
    assert out["error"] is None
    assert out["iterations"] == 2
    assert out["tool_calls_made"] == 1


def test_error_maps_to_status_error_with_full_message(ws):
    mock_loop = MagicMock()
    mock_loop.run.return_value = _err("LLMRateLimitError: 429 boom", iterations=3)

    with patch.object(role_factory, "build_agent_loop", return_value=mock_loop):
        out = agent_node(role="researcher", workspace_path=ws,
                         strategy_name="s", task="t")

    assert out["status"] == "error"
    # full message (type + text), not just the type name
    assert out["error"] == "LLMRateLimitError: 429 boom"
    assert out["iterations"] == 3


def test_run_exception_becomes_error_result(ws):
    """A crashing loop must not take the orchestrating graph with it."""
    mock_loop = MagicMock()
    mock_loop.run.side_effect = RuntimeError("kaboom")

    with patch.object(role_factory, "build_agent_loop", return_value=mock_loop):
        out = agent_node(role="researcher", workspace_path=ws,
                         strategy_name="s", task="t")

    assert out["status"] == "error"
    assert "RuntimeError" in out["error"]
    assert "kaboom" in out["error"]


def test_metrics_carry_finished_reason(ws):
    mock_loop = MagicMock()
    mock_loop.run.return_value = LoopResult(
        answer="x", iterations=1, tool_calls_made=0,
        finished_reason="max_iter", metrics={"tokens": 42},
    )

    with patch.object(role_factory, "build_agent_loop", return_value=mock_loop):
        out = agent_node(role="researcher", workspace_path=ws,
                         strategy_name="s", task="t")

    assert out["metrics"]["finished_reason"] == "max_iter"
    assert out["metrics"]["tokens"] == 42


# ── construction failures stay failures ───────────────────────


def test_missing_prompt_raises(ws):
    """A missing role prompt is a broken install, not a runtime error."""
    with patch.object(role_factory, "build_agent_loop", return_value=None):
        with pytest.raises(RuntimeError, match="prompt"):
            agent_node(role="nope", workspace_path=ws,
                       strategy_name="s", task="t")


def test_unknown_role_raises_with_real_assembly(ws):
    """Same, without mocking: the real factory returns None for a bad role."""
    with pytest.raises(RuntimeError, match="prompt"):
        agent_node(role="nonexistent_role_xyz", workspace_path=ws,
                   strategy_name="s", task="t")


# ── capability preservation (the reason option B was chosen) ──


def test_node_knobs_are_forwarded(ws):
    """iteration_timeout_s / on_event must reach the AgentLoop, not be dropped.

    The swarm migration depends on the per-node timeout; silently dropping
    it would be a regression, not a simplification.
    """
    mock_loop = MagicMock()
    mock_loop.run.return_value = _ok()
    sink = MagicMock()

    with patch.object(role_factory, "build_agent_loop",
                      return_value=mock_loop) as build:
        agent_node(
            role="researcher", workspace_path=ws, strategy_name="s", task="t",
            iteration_timeout_s=120.0, sink=sink,
        )

    kwargs = build.call_args.kwargs
    assert kwargs["iteration_timeout_s"] == 120.0
    assert kwargs["on_event"] is sink


def test_agentloop_owns_the_four_capabilities_react_lacked():
    """Documents why a thin react loop was rejected.

    These four live in loop_tools.py and are unconditional for AgentLoop
    turns. Two are behavioural (truncation, retry); if any of them is later
    removed there is no reason left to keep AgentLoop.
    """
    src = Path(inspect.getfile(AgentLoop)).parent.joinpath(
        "loop_tools.py"
    ).read_text(encoding="utf-8")

    assert "_TOOL_MAX_RETRIES" in src, "transient tool retry"
    assert "_TOOL_RESULT_HISTORY_MAX" in src, "30k tool-result truncation"
    assert "check_guards" in src, "monotonic deny guards"
    assert "_execute_tool_batch" in src, "read-only parallel batch"
