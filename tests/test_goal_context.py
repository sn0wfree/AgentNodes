"""Tests for core.goal.context — format_goal_context + continuation prompt.

The context module is what gets injected into the AgentLoop's user message.
It must render the snapshot as an XML-ish block the LLM can parse, and
the continuation prompt must enumerate open required items.
"""

from __future__ import annotations

from typing import Any

from agentnodes.apps.goal.context import (
    CONTINUABLE_GOAL_STATUSES,
    OPEN_CRITERION_STATUSES,
    criterion_is_covered,
    default_goal_criteria,
    format_goal_context,
    format_goal_continuation_prompt,
    get_current_goal_context,
    goal_needs_continuation,
    goal_progress_tuple,
)
from agentnodes.apps.goal.models import GoalStatus

# ─── Constants ────────────────────────────────────────────────────────────


class TestConstants:
    def test_open_criterion_statuses_includes_pending(self):
        assert "pending" in OPEN_CRITERION_STATUSES
        assert "open" in OPEN_CRITERION_STATUSES
        assert "unsatisfied" in OPEN_CRITERION_STATUSES

    def test_open_criterion_statuses_excludes_satisfied(self):
        """Satisfied/covered statuses are NOT in OPEN set."""
        assert "satisfied" not in OPEN_CRITERION_STATUSES
        assert "covered" not in OPEN_CRITERION_STATUSES

    def test_continuable_statuses(self):
        assert GoalStatus.ACTIVE.value in CONTINUABLE_GOAL_STATUSES
        assert GoalStatus.NEEDS_REFRESH.value in CONTINUABLE_GOAL_STATUSES
        assert GoalStatus.INSUFFICIENT_EVIDENCE.value in CONTINUABLE_GOAL_STATUSES
        # Non-continuable:
        assert GoalStatus.COMPLETE.value not in CONTINUABLE_GOAL_STATUSES
        assert GoalStatus.CANCELLED.value not in CONTINUABLE_GOAL_STATUSES


# ─── default_goal_criteria ───────────────────────────────────────────────


class TestDefaultCriteria:
    def test_returns_three_strings(self):
        criteria = default_goal_criteria()
        assert len(criteria) == 3
        for c in criteria:
            assert isinstance(c, str)
            assert len(c) > 5

    def test_returns_fresh_list_each_time(self):
        """Mutation must not leak between calls."""
        a = default_goal_criteria()
        a.append("mutated")
        b = default_goal_criteria()
        assert len(b) == 3


# ─── format_goal_context ─────────────────────────────────────────────────


def _make_snapshot(
    *,
    goal_id: str = "goal_abc",
    status: str = "active",
    objective: str = "Test",
    risk_tier: str = "research_general",
    criteria: list[dict] | None = None,
    evidence: list[dict] | None = None,
    evidence_count: int | None = None,
) -> dict[str, Any]:
    return {
        "goal": {
            "goal_id": goal_id,
            "session_id": "sess_1",
            "status": status,
            "objective": objective,
            "ui_summary": objective[:80],
            "source": "api",
            "protocol": "thesis_review",
            "risk_tier": risk_tier,
            "token_budget": None,
            "tokens_used": 0,
            "turn_budget": None,
            "turns_used": 0,
            "time_budget_seconds": None,
            "time_used_seconds": 0,
            "budget_wrapup_sent": False,
            "created_at": "2026-07-22T00:00:00Z",
            "updated_at": "2026-07-22T00:00:00Z",
            "completed_at": None,
            "recap": None,
        },
        "claims": [],
        "criteria": criteria or [],
        "evidence": evidence or [],
        "evidence_count": (
            evidence_count
            if evidence_count is not None
            else len(evidence or [])
        ),
    }


class TestFormatGoalContext:
    def test_contains_xml_open_close(self):
        snap = _make_snapshot()
        out = format_goal_context(snap)
        assert "<current-research-goal>" in out
        assert "</current-research-goal>" in out

    def test_contains_goal_id_and_objective(self):
        snap = _make_snapshot(goal_id="goal_xyz", objective="Investigate momentum")
        out = format_goal_context(snap)
        assert "goal_id: goal_xyz" in out
        assert "Investigate momentum" in out
        assert "expected_goal_id: goal_xyz" in out

    def test_contains_status_and_risk_tier(self):
        snap = _make_snapshot(status="active", risk_tier="market_specific_short_term")
        out = format_goal_context(snap)
        assert "status: active" in out
        assert "risk_tier: market_specific_short_term" in out

    def test_renders_criteria_with_index_and_evidence_count(self):
        snap = _make_snapshot(
            criteria=[
                {"criterion_id": "crit_a", "text": "A criterion", "status": "pending"},
                {"criterion_id": "crit_b", "text": "B criterion", "status": "covered"},
            ],
            evidence=[
                {"criterion_id": "crit_b", "text": "covered by this"},
            ],
        )
        out = format_goal_context(snap)
        assert "1. [pending] crit_a: A criterion (evidence=0)" in out
        assert "2. [covered] crit_b: B criterion (evidence=1)" in out

    def test_includes_instructions_block(self):
        snap = _make_snapshot()
        out = format_goal_context(snap)
        assert "instructions:" in out
        assert "audit" in out.lower()
        assert "add_goal_evidence" in out

    def test_uses_evidence_count_from_field(self):
        snap = _make_snapshot(evidence=[], evidence_count=42)
        out = format_goal_context(snap)
        assert "evidence_count: 42" in out

    def test_empty_criteria_does_not_crash(self):
        snap = _make_snapshot(criteria=[])
        out = format_goal_context(snap)
        assert "<current-research-goal>" in out
        assert "criteria:" in out


# ─── criterion_is_covered ────────────────────────────────────────────────


class TestCriterionIsCovered:
    def test_pending_is_not_covered(self):
        snap = _make_snapshot()
        crit = {"criterion_id": "c1", "status": "pending"}
        assert criterion_is_covered(snap, crit) is False

    def test_satisfied_is_covered(self):
        snap = _make_snapshot()
        crit = {"criterion_id": "c1", "status": "satisfied"}
        assert criterion_is_covered(snap, crit) is True

    def test_covered_status_means_covered(self):
        snap = _make_snapshot()
        crit = {"criterion_id": "c1", "status": "covered"}
        assert criterion_is_covered(snap, crit) is True

    def test_pending_with_evidence_is_covered(self):
        """Pending criterion with linked evidence counts as covered."""
        snap = _make_snapshot(
            evidence=[{"criterion_id": "c1", "text": "x"}],
        )
        crit = {"criterion_id": "c1", "status": "pending"}
        assert criterion_is_covered(snap, crit) is True


# ─── goal_progress_tuple ─────────────────────────────────────────────────


class TestGoalProgress:
    def test_empty(self):
        snap = _make_snapshot()
        covered, total = goal_progress_tuple(snap)
        assert covered == 0
        assert total == 0

    def test_all_covered(self):
        snap = _make_snapshot(
            criteria=[
                {"criterion_id": "c1", "status": "satisfied"},
                {"criterion_id": "c2", "status": "satisfied"},
            ],
        )
        covered, _ = goal_progress_tuple(snap)
        assert covered == 2

    def test_partial(self):
        snap = _make_snapshot(
            criteria=[
                {"criterion_id": "c1", "status": "satisfied"},
                {"criterion_id": "c2", "status": "pending"},
            ],
        )
        covered, _ = goal_progress_tuple(snap)
        assert covered == 1


# ─── goal_needs_continuation ─────────────────────────────────────────────


class TestGoalNeedsContinuation:
    def test_active_with_open_criterion_needs_continuation(self):
        snap = _make_snapshot(
            status="active",
            criteria=[
                {"criterion_id": "c1", "text": "open", "status": "pending", "required": True},
            ],
        )
        assert goal_needs_continuation(snap) is True

    def test_active_with_empty_criteria_does_not_continue(self):
        """Empty criteria → nothing to drive → False (regression fix)."""
        snap = _make_snapshot(status="active")
        assert goal_needs_continuation(snap) is False

    def test_active_with_all_covered_does_not_continue(self):
        snap = _make_snapshot(
            status="active",
            criteria=[
                {"criterion_id": "c1", "text": "done", "status": "covered", "required": True},
            ],
        )
        assert goal_needs_continuation(snap) is False

    def test_complete_does_not_need(self):
        snap = _make_snapshot(
            status="complete",
            criteria=[
                {"criterion_id": "c1", "text": "t", "status": "pending", "required": True},
            ],
        )
        assert goal_needs_continuation(snap) is False

    def test_cancelled_does_not_need(self):
        snap = _make_snapshot(
            status="cancelled",
            criteria=[
                {"criterion_id": "c1", "text": "t", "status": "pending", "required": True},
            ],
        )
        assert goal_needs_continuation(snap) is False

    def test_paused_does_not_need(self):
        snap = _make_snapshot(
            status="paused",
            criteria=[
                {"criterion_id": "c1", "text": "t", "status": "pending", "required": True},
            ],
        )
        assert goal_needs_continuation(snap) is False


# ─── format_goal_continuation_prompt ─────────────────────────────────────


class TestContinuationPrompt:
    def test_enumerates_open_criteria(self):
        snap = _make_snapshot(
            criteria=[
                {"criterion_id": "c1", "text": "Open item", "status": "pending", "required": True},
                {"criterion_id": "c2", "text": "Done item", "status": "satisfied", "required": True},
            ],
        )
        out = format_goal_continuation_prompt(snap)
        assert "<goal-continuation>" in out
        assert "c1: Open item" in out
        # c2 is satisfied, must NOT appear in open_required_items
        assert "Done item" not in out.split("open_required_items:")[1].split("Rules:")[0]

    def test_includes_progress_line(self):
        snap = _make_snapshot(
            criteria=[
                {"criterion_id": "c1", "text": "first", "status": "satisfied"},
                {"criterion_id": "c2", "text": "second", "status": "pending"},
            ],
        )
        out = format_goal_continuation_prompt(snap)
        assert "covered_criteria=1/2" in out
        assert "evidence_count=0" in out

    def test_all_covered_shows_hint(self):
        snap = _make_snapshot(
            criteria=[
                {"criterion_id": "c1", "text": "first", "status": "satisfied"},
            ],
        )
        out = format_goal_continuation_prompt(snap)
        # When all covered, prompt hints to audit and complete
        assert "All criteria appear covered" in out

    def test_includes_previous_answer_block(self):
        snap = _make_snapshot()
        out = format_goal_continuation_prompt(snap, previous_answer="my prior reply")
        assert "Previous assistant text:" in out
        assert "my prior reply" in out

    def test_no_previous_answer_skips_block(self):
        snap = _make_snapshot()
        out = format_goal_continuation_prompt(snap)
        assert "Previous assistant text:" not in out


# ─── get_current_goal_context ─────────────────────────────────────────────


class TestGetCurrentGoalContext:
    def test_empty_session_returns_empty(self):
        assert get_current_goal_context("") == ("", None)

    def test_whitespace_session_returns_empty(self):
        assert get_current_goal_context("   ") == ("", None)

    def test_no_goal_returns_empty(self, tmp_path):
        """No current goal in DB → empty tuple."""
        from agentnodes.apps.goal import GoalStore
        GoalStore(db_path=tmp_path / "g.db")
        # Empty session has no goal
        # get_current_goal_context uses default DB path, so we can't fully test in isolation.
        # Instead test the format path:
        assert get_current_goal_context("nonexistent_session")[0] == ""


# ─── Integration: format from real store snapshot ────────────────────────


class TestIntegrationWithStore:
    def test_full_flow_renders(self, tmp_path):
        """End-to-end: create goal via store, format via context."""
        from agentnodes.apps.goal import (
            EvidenceInput,
            GoalStore,
        )

        store = GoalStore(db_path=tmp_path / "g.db")
        goal = store.replace_goal(
            session_id="s",
            objective="Test objective",
            criteria=default_goal_criteria(),
        )
        criteria = store.list_criteria(goal.goal_id)
        store.append_evidence(
            session_id="s",
            goal_id=goal.goal_id,
            expected_goal_id=goal.goal_id,
            evidence=EvidenceInput(text="e", criterion_id=criteria[0].criterion_id),
        )
        snap = store.get_goal_snapshot(goal.goal_id)
        out = format_goal_context(snap)
        assert "<current-research-goal>" in out
        assert "Test objective" in out
        assert "evidence_count: 1" in out
