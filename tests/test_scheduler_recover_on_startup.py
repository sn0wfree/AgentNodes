"""Tests for StudyScheduler.recover_on_startup (startup recovery).

When the server restarts:
- RUNNING → INTERRUPTED (prevents auto-restart loops)
- PAUSED → stays PAUSED (respect user pause)
- QUEUED → re-enqueue
- MONITORING → re-submit
- workspace not found → ERROR

These tests verify the status transitions WITHOUT actually running
studies (submit is stubbed).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from strategy_research.core.goal import GoalStore
from strategy_research.core.goal.context import default_goal_criteria
from strategy_research.apps.study.store import StudyStore
from strategy_research.apps.study.models import StudyStatus


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "g.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "h.json"))
    ws = tmp_path / "ws"
    (ws / "strategies" / "demo").mkdir(parents=True)
    (ws / "strategies" / "demo" / "strategy.py").write_text("PARAMS = {}\n")
    return ws


_goal_counter = 0

def _create_study_with_status(store, ws, status):
    """Create a study and set its status (returns the study_id)."""
    global _goal_counter
    _goal_counter += 1
    gs = GoalStore()
    goal = gs.replace_goal(
        session_id=f"owner-s-{_goal_counter}", objective="x",
        criteria=default_goal_criteria(), supersede=False,
    )
    study = store.create_study(
        owner_session_id=f"owner-s-{_goal_counter}", goal_id=goal.goal_id,
        objective=f"test-{status.value}",
        workspace_path=str(ws), strategy_name="demo",
        metric_targets=[{"name": "calmar", "op": ">=", "value": 0.5}],
        cooldown_base=0.01, cooldown_jitter=0.01, min_cooldown=0.01,
    )
    store.update_execution_status(study.study_id, status)
    return study.study_id


@pytest.mark.asyncio
async def test_running_becomes_interrupted(env):
    """RUNNING studies get flipped to INTERRUPTED on startup."""
    store = StudyStore()
    sid = _create_study_with_status(store, env, StudyStatus.RUNNING)
    assert store.get_study(sid).execution_status == StudyStatus.RUNNING

    from strategy_research.apps.study.scheduler import StudyScheduler
    sched = StudyScheduler(store=store)
    sched.submit = lambda study: asyncio.sleep(0)

    await sched.recover_on_startup()

    updated = store.get_study(sid)
    assert updated.execution_status == StudyStatus.INTERRUPTED


@pytest.mark.asyncio
async def test_paused_stays_paused(env):
    """PAUSED studies are not recovered (not resubmitted)."""
    store = StudyStore()
    sid = _create_study_with_status(store, env, StudyStatus.PAUSED)

    from strategy_research.apps.study.scheduler import StudyScheduler
    sched = StudyScheduler(store=store)
    sched.submit = lambda study: asyncio.sleep(0)

    recovered = await sched.recover_on_startup()

    recovered_ids = [s.study_id for s in recovered]
    assert sid not in recovered_ids
    assert store.get_study(sid).execution_status == StudyStatus.PAUSED


@pytest.mark.asyncio
async def test_queued_is_recovered(env):
    """QUEUED studies are recovered (resubmitted)."""
    store = StudyStore()
    sid = _create_study_with_status(store, env, StudyStatus.QUEUED)

    from strategy_research.apps.study.scheduler import StudyScheduler
    sched = StudyScheduler(store=store)
    sched.submit = lambda study: asyncio.sleep(0)

    recovered = await sched.recover_on_startup()

    recovered_ids = [s.study_id for s in recovered]
    assert sid in recovered_ids


@pytest.mark.asyncio
async def test_stale_workspace_becomes_error(env):
    """Studies with non-existent workspace get marked as ERROR."""
    store = StudyStore()
    gs = GoalStore()
    goal = gs.replace_goal(
        session_id="owner-x", objective="x",
        criteria=default_goal_criteria(), supersede=False,
    )
    study = store.create_study(
        owner_session_id="owner-x", goal_id=goal.goal_id,
        objective="stale",
        workspace_path="/tmp/nonexistent-workspace-xyz",
        strategy_name="demo",
        metric_targets=[],
        cooldown_base=0.01, cooldown_jitter=0.01, min_cooldown=0.01,
    )
    store.update_execution_status(study.study_id, StudyStatus.RUNNING)

    from strategy_research.apps.study.scheduler import StudyScheduler
    sched = StudyScheduler(store=store)
    sched.submit = lambda study: asyncio.sleep(0)

    recovered = await sched.recover_on_startup()

    stale_ids = [s.study_id for s in recovered]
    assert study.study_id not in stale_ids

    updated = store.get_study(study.study_id)
    assert updated.execution_status == StudyStatus.ERROR


@pytest.mark.asyncio
async def test_mixed_statuses_correctly_transition(env):
    """All three non-terminal statuses handled independently in a single run."""
    store = StudyStore()
    ids = {
        StudyStatus.RUNNING: _create_study_with_status(store, env, StudyStatus.RUNNING),
        StudyStatus.PAUSED: _create_study_with_status(store, env, StudyStatus.PAUSED),
        StudyStatus.QUEUED: _create_study_with_status(store, env, StudyStatus.QUEUED),
    }

    from strategy_research.apps.study.scheduler import StudyScheduler
    sched = StudyScheduler(store=store)
    sched.submit = lambda study: asyncio.sleep(0)

    recovered = await sched.recover_on_startup()
    recovered_ids = {s.study_id for s in recovered}

    # RUNNING → INTERRUPTED
    assert ids[StudyStatus.RUNNING] in recovered_ids
    assert store.get_study(ids[StudyStatus.RUNNING]).execution_status == StudyStatus.INTERRUPTED

    # PAUSED → stays PAUSED, not recovered
    assert ids[StudyStatus.PAUSED] not in recovered_ids
    assert store.get_study(ids[StudyStatus.PAUSED]).execution_status == StudyStatus.PAUSED

    # QUEUED → recovered
    assert ids[StudyStatus.QUEUED] in recovered_ids
