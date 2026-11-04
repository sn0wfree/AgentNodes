"""Regression test for the agent-loop approval endpoint (G4).

The ``POST /api/study/{id}/agents/approve`` endpoint calls
``scheduler.approve_agent_loop(study_id, decision)``. This method
looks for ``runner.agent_loop``, but ``AutoresearchRunner`` never sets
that attribute — it uses ``_loop_strategy`` instead. The result is
that ``approve_agent_loop`` always returns ``False``, making the
approval endpoint a no-op.

This test documents the known issue. When the bug is fixed (by
exposing ``agent_loop`` on ``AutoresearchRunner`` or by rewiring the
approval path), update the test to assert ``returns True``.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "g.db"))
    from strategy_research.apps.study.store import StudyStore
    return StudyStore(db_path=tmp_path / "g.db")


def test_approve_agent_loop_returns_false_when_no_runner(store):
    """With no active executor, approve_agent_loop returns False."""
    from strategy_research.apps.study.scheduler import StudyScheduler

    sched = StudyScheduler(store=store)
    # No active runners — should return False (no-op)
    result = sched.approve_agent_loop("nonexistent-study", "approved")
    assert result is False


def test_approve_agent_loop_returns_false_when_runner_has_no_agent_loop(store):
    """Even with an active runner, if it lacks agent_loop, returns False.

    This documents the known G4 bug: AutoresearchRunner doesn't expose
    ``agent_loop``. When this bug is fixed, this test should be updated
    to assert ``True`` and verify the approval is forwarded.
    """
    from unittest.mock import MagicMock
    from strategy_research.apps.study.scheduler import StudyScheduler

    sched = StudyScheduler(store=store)

    # Inject a mock runner that lacks agent_loop (simulates AutoresearchRunner)
    mock_runner = MagicMock(spec=[])  # empty spec = no attributes
    sched._active_executors["test-sid"] = mock_runner

    # approve_agent_loop should return False (no-op) because runner has no agent_loop
    result = sched.approve_agent_loop("test-sid", "approved")
    assert result is False, (
        "approve_agent_loop should return False when runner lacks agent_loop. "
        "When fixed, update this test to assert True."
    )

    # Cleanup
    sched._active_executors.pop("test-sid", None)


def test_approve_agent_loop_calls_approve_when_runner_has_loop(store):
    """When runner DOES have agent_loop, the method calls approve_loop."""
    from unittest.mock import MagicMock
    from strategy_research.apps.study.scheduler import StudyScheduler

    sched = StudyScheduler(store=store)

    # Mock runner with agent_loop attribute
    mock_loop = MagicMock()
    mock_runner = MagicMock()
    mock_runner.agent_loop = mock_loop
    sched._active_executors["test-sid"] = mock_runner

    result = sched.approve_agent_loop("test-sid", "approved")
    assert result is True
    mock_loop.approve_loop.assert_called_once_with("approved")

    sched._active_executors.pop("test-sid", None)