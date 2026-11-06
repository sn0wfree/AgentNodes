"""Tests for GoalStore.account_usage() — token/time/turn budget tracking."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentnodes.apps.goal import GoalStatus, GoalStore


@pytest.fixture
def store(tmp_path: Path) -> GoalStore:
    return GoalStore(db_path=tmp_path / "goals_account.db")


@pytest.fixture
def goal_with_budgets(store: GoalStore):
    """A goal with all three budgets set."""
    return store.replace_goal(
        session_id="sess_budget",
        objective="Budget test",
        criteria=["c1"],
        token_budget=1000,
        turn_budget=5,
        time_budget_seconds=600,
    )


# ─── 基本累加 ────────────────────────────────────────────────────────


class TestUsageAccumulation:
    def test_token_usage_increments(self, store, goal_with_budgets):
        updated = store.account_usage(
            session_id="sess_budget",
            goal_id=goal_with_budgets.goal_id,
            expected_goal_id=goal_with_budgets.goal_id,
            token_delta=100,
        )
        assert updated.tokens_used == 100

    def test_multiple_calls_accumulate(self, store, goal_with_budgets):
        gid = goal_with_budgets.goal_id
        store.account_usage(
            session_id="sess_budget", goal_id=gid,
            expected_goal_id=gid, token_delta=100,
        )
        updated = store.account_usage(
            session_id="sess_budget", goal_id=gid,
            expected_goal_id=gid, token_delta=200,
        )
        assert updated.tokens_used == 300

    def test_turn_usage_increments(self, store, goal_with_budgets):
        updated = store.account_usage(
            session_id="sess_budget",
            goal_id=goal_with_budgets.goal_id,
            expected_goal_id=goal_with_budgets.goal_id,
            turn_delta=2,
        )
        assert updated.turns_used == 2

    def test_time_usage_increments(self, store, goal_with_budgets):
        updated = store.account_usage(
            session_id="sess_budget",
            goal_id=goal_with_budgets.goal_id,
            expected_goal_id=goal_with_budgets.goal_id,
            time_delta_seconds=30,
        )
        assert updated.time_used_seconds == 30

    def test_all_deltas_together(self, store, goal_with_budgets):
        updated = store.account_usage(
            session_id="sess_budget",
            goal_id=goal_with_budgets.goal_id,
            expected_goal_id=goal_with_budgets.goal_id,
            token_delta=50,
            turn_delta=1,
            time_delta_seconds=10,
        )
        assert updated.tokens_used == 50
        assert updated.turns_used == 1
        assert updated.time_used_seconds == 10


# ─── 预算超限 → BUDGET_LIMITED ───────────────────────────────────────


class TestBudgetExceeded:
    def test_token_budget_exceeded(self, store, goal_with_budgets):
        gid = goal_with_budgets.goal_id
        updated = store.account_usage(
            session_id="sess_budget", goal_id=gid,
            expected_goal_id=gid, token_delta=1000,
        )
        assert updated.status == GoalStatus.BUDGET_LIMITED

    def test_turn_budget_exceeded(self, store, goal_with_budgets):
        gid = goal_with_budgets.goal_id
        updated = store.account_usage(
            session_id="sess_budget", goal_id=gid,
            expected_goal_id=gid, turn_delta=5,
        )
        assert updated.status == GoalStatus.BUDGET_LIMITED

    def test_time_budget_exceeded(self, store, goal_with_budgets):
        gid = goal_with_budgets.goal_id
        updated = store.account_usage(
            session_id="sess_budget", goal_id=gid,
            expected_goal_id=gid, time_delta_seconds=600,
        )
        assert updated.status == GoalStatus.BUDGET_LIMITED

    def test_exact_budget_is_exceeded(self, store, goal_with_budgets):
        """Exactly hitting the budget should trigger BUDGET_LIMITED (>= check)."""
        gid = goal_with_budgets.goal_id
        updated = store.account_usage(
            session_id="sess_budget", goal_id=gid,
            expected_goal_id=gid, token_delta=1000,
        )
        assert updated.status is GoalStatus.BUDGET_LIMITED

    def test_under_budget_stays_active(self, store, goal_with_budgets):
        gid = goal_with_budgets.goal_id
        updated = store.account_usage(
            session_id="sess_budget", goal_id=gid,
            expected_goal_id=gid, token_delta=999,
        )
        assert updated.status == GoalStatus.ACTIVE

    def test_no_budget_never_limits(self, store):
        """Goal without budgets never triggers BUDGET_LIMITED."""
        goal = store.replace_goal(
            session_id="sess_nobudget",
            objective="No budgets",
            criteria=["c1"],
        )
        updated = store.account_usage(
            session_id="sess_nobudget", goal_id=goal.goal_id,
            expected_goal_id=goal.goal_id, token_delta=999999,
        )
        assert updated.status != GoalStatus.BUDGET_LIMITED


# ─── 负数 delta 报错 ────────────────────────────────────────────────


class TestNegativeDelta:
    def test_negative_token_raises(self, store, goal_with_budgets):
        with pytest.raises(ValueError, match="non-negative"):
            store.account_usage(
                session_id="sess_budget",
                goal_id=goal_with_budgets.goal_id,
                expected_goal_id=goal_with_budgets.goal_id,
                token_delta=-1,
            )

    def test_negative_turn_raises(self, store, goal_with_budgets):
        with pytest.raises(ValueError, match="non-negative"):
            store.account_usage(
                session_id="sess_budget",
                goal_id=goal_with_budgets.goal_id,
                expected_goal_id=goal_with_budgets.goal_id,
                turn_delta=-1,
            )

    def test_negative_time_raises(self, store, goal_with_budgets):
        with pytest.raises(ValueError, match="non-negative"):
            store.account_usage(
                session_id="sess_budget",
                goal_id=goal_with_budgets.goal_id,
                expected_goal_id=goal_with_budgets.goal_id,
                time_delta_seconds=-1,
            )

    def test_zero_deltas_allowed(self, store, goal_with_budgets):
        updated = store.account_usage(
            session_id="sess_budget",
            goal_id=goal_with_budgets.goal_id,
            expected_goal_id=goal_with_budgets.goal_id,
        )
        assert updated.tokens_used == 0
        assert updated.status == GoalStatus.ACTIVE


# ─── stale goal ───────────────────────────────────────────────────────


class TestStaleGoal:
    def test_wrong_expected_goal_id_raises(self, store, goal_with_budgets):
        from agentnodes.apps.goal.models import StaleGoalError

        with pytest.raises(StaleGoalError):
            store.account_usage(
                session_id="sess_budget",
                goal_id=goal_with_budgets.goal_id,
                expected_goal_id="stale_wrong_id",
                token_delta=10,
            )

    def test_wrong_session_id_allowed(self, store, goal_with_budgets):
        """v2 decision D: session is no longer part of the write guard —
        any executor identity (e.g. a study micro session) may write when
        goal_id + expected_goal_id match."""
        updated = store.account_usage(
            session_id="wrong_session",
            goal_id=goal_with_budgets.goal_id,
            expected_goal_id=goal_with_budgets.goal_id,
            token_delta=10,
        )
        assert updated.tokens_used == 10


# ─── persistence ──────────────────────────────────────────────────────


class TestPersistence:
    def test_usage_persists_across_reloads(self, store, goal_with_budgets):
        gid = goal_with_budgets.goal_id
        store.account_usage(
            session_id="sess_budget", goal_id=gid,
            expected_goal_id=gid, token_delta=250, turn_delta=2,
        )
        reloaded = store.get_goal(gid)
        assert reloaded.tokens_used == 250
        assert reloaded.turns_used == 2
