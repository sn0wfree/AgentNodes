"""Goal v2 write-path session decoupling tests (decision D).

Covers: cross-session writes are allowed (goal_id + expected_goal_id are
the guards); stale/status guards are preserved; evidence/journal always
persist under the goal's own session_id.
"""

from __future__ import annotations

import pytest

from agentnodes.apps.goal import GoalStore
from agentnodes.apps.goal.context import default_goal_criteria
from agentnodes.apps.goal.models import EvidenceInput, StaleGoalError


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db")
    )
    monkeypatch.setenv(
        "QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json")
    )
    return GoalStore()


@pytest.fixture
def goal(store):
    return store.replace_goal(
        session_id="chat_abc",
        objective="研究动量因子",
        criteria=default_goal_criteria(),
    )


def test_cross_session_append_evidence_allowed(store, goal):
    """A micro-session executor identity may write evidence to the goal."""
    ev = store.append_evidence(
        session_id="study:stu_123",
        goal_id=goal.goal_id,
        expected_goal_id=goal.goal_id,
        evidence=EvidenceInput(
            text="跨 session 证据", evidence_type="acceptance",
            run_id="run_0001", source_provider="study",
            source_type="metric_targets_met",
        ),
    )
    # persisted under the goal's own session, not the writer's
    assert ev.session_id == "chat_abc"
    listed = store.list_evidence(goal.goal_id)
    assert len(listed) == 1


def test_cross_session_update_goal_allowed(store, goal):
    updated = store.update_goal(
        session_id="study:stu_123",
        goal_id=goal.goal_id,
        expected_goal_id=goal.goal_id,
        objective="研究动量与波动率",
    )
    assert updated.objective == "研究动量与波动率"


def test_cross_session_account_usage_allowed(store, goal):
    updated = store.account_usage(
        session_id="study:stu_123",
        goal_id=goal.goal_id,
        expected_goal_id=goal.goal_id,
        token_delta=5,
    )
    assert updated.tokens_used == 5


def test_cross_session_complete_lite_allowed(store, goal):
    for criterion in store.list_criteria(goal.goal_id):
        if not criterion.required:
            continue
        store.append_evidence(
            session_id="study:stu_123",
            goal_id=goal.goal_id,
            expected_goal_id=goal.goal_id,
            evidence=EvidenceInput(
                text="evidence", criterion_id=criterion.criterion_id,
                evidence_type="acceptance", run_id="run_0001",
                source_provider="study", source_type="metric_targets_met",
            ),
        )
    updated = store.complete_lite(
        session_id="study:stu_123",
        goal_id=goal.goal_id,
        expected_goal_id=goal.goal_id,
        recap="达标",
    )
    assert updated.status.value == "complete"


def test_journal_session_field_records_writer(store, goal):
    """Journal rows keep the writer's session as metadata; lookups are by goal_id."""
    entry = store.append_journal_entry(
        goal_id=goal.goal_id,
        session_id="study:stu_123",
        round_num=1,
        hypothesis_id="hyp_1",
        label="动量因子",
    )
    assert entry.session_id == "study:stu_123"
    entries = store.list_journal_entries(goal.goal_id)
    assert len(entries) == 1


# ── preserved guards ───────────────────────────────────────────────────


def test_wrong_expected_goal_id_still_rejected(store, goal):
    with pytest.raises(StaleGoalError):
        store.update_goal(
            session_id="study:stu_123",
            goal_id=goal.goal_id,
            expected_goal_id="goal_wrong",
            objective="x",
        )


def test_superseded_goal_still_rejected(store, goal):
    store.replace_goal(
        session_id="chat_abc", objective="新目标", criteria=default_goal_criteria(),
    )
    with pytest.raises(StaleGoalError):
        store.update_goal(
            session_id="study:stu_123",
            goal_id=goal.goal_id,
            expected_goal_id=goal.goal_id,
            objective="x",
        )


def test_completed_goal_still_rejected(store):
    g = store.replace_goal(
        session_id="chat_abc", objective="目标", criteria=default_goal_criteria(),
    )
    for criterion in store.list_criteria(g.goal_id):
        if not criterion.required:
            continue
        store.append_evidence(
            session_id="chat_abc", goal_id=g.goal_id,
            expected_goal_id=g.goal_id,
            evidence=EvidenceInput(
                text="evidence", criterion_id=criterion.criterion_id,
                evidence_type="acceptance", run_id="run_0001",
                source_provider="study", source_type="metric_targets_met",
            ),
        )
    store.complete_lite(
        session_id="chat_abc", goal_id=g.goal_id, expected_goal_id=g.goal_id,
        recap="done",
    )
    with pytest.raises(StaleGoalError):
        store.update_goal(
            session_id="chat_abc", goal_id=g.goal_id,
            expected_goal_id=g.goal_id, objective="x",
        )
