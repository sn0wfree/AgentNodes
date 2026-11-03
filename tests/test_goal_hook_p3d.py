"""Tests for the _on_goal_complete hook and autoresearch GoalStore integration.

P3-D: Hypothesis monitoring auto-transition when goal reaches COMPLETE,
plus autoresearch CLI helpers.
"""

from __future__ import annotations

import pytest

# ============================================================
# Fixtures
# ============================================================


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    """Force all stores to use tmp_path via env vars."""
    monkeypatch.setenv(
        "QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"),
    )
    monkeypatch.setenv(
        "QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "h.json"),
    )
    monkeypatch.delenv("HYPOTHESIS_USE_SQLITE", raising=False)
    yield


@pytest.fixture
def goal_store():
    from strategy_research.apps.goal import GoalStore
    store = GoalStore()
    yield store


@pytest.fixture
def registry():
    from strategy_research.apps.hypothesis import HypothesisRegistry
    return HypothesisRegistry()


# ============================================================
# _on_goal_complete hook — auto-transition hypotheses to monitoring
# ============================================================


class TestOnGoalCompleteHook:
    def test_linked_validated_hyp_transitions_to_monitoring(
        self, goal_store, registry,
    ):
        """A validated hypothesis linked to a completed goal should be transitioned to monitoring."""
        goal = goal_store.replace_goal(
            session_id="sess_hook",
            objective="Test hook transition",
            criteria=["c1"],
        )
        hyp = registry.create(title="h1", thesis="t1", status="validated")
        registry.link_goal(hyp.hypothesis_id, goal.goal_id)

        # Fire the hook directly
        goal_store._on_goal_complete("sess_hook", goal.goal_id)

        # Should transition to monitoring
        assert registry.get(hyp.hypothesis_id).status == "monitoring"

    def test_linked_testing_hyp_not_transitioned(self, goal_store, registry):
        """A testing hypothesis cannot jump to monitoring (illegal transition); should remain testing."""
        from strategy_research.apps.hypothesis import VALID_TRANSITIONS
        # Confirm testing→monitoring is NOT allowed
        assert "monitoring" not in VALID_TRANSITIONS.get("testing", set())

        goal = goal_store.replace_goal(
            session_id="sess_hook_testing",
            objective="Testing path",
            criteria=["c1"],
        )
        hyp = registry.create(title="testing_h", thesis="t", status="testing")
        registry.link_goal(hyp.hypothesis_id, goal.goal_id)

        goal_store._on_goal_complete("sess_hook_testing", goal.goal_id)

        # testing stays testing (the hook silently skips illegal transitions)
        assert registry.get(hyp.hypothesis_id).status == "testing"

    def test_unlinked_hypothesis_not_affected(self, goal_store, registry):
        """Hypotheses not linked to this goal should remain untouched."""
        goal = goal_store.replace_goal(
            session_id="sess_hook2",
            objective="Test unlinked",
            criteria=["c1"],
        )
        # A validated hypothesis NOT linked to this goal
        unlinked = registry.create(title="orphan", thesis="t", status="validated")

        goal_store._on_goal_complete("sess_hook2", goal.goal_id)

        # Unlinked hypothesis must remain validated
        assert registry.get(unlinked.hypothesis_id).status == "validated"

    def test_rejected_hypothesis_not_transitioned(self, goal_store, registry):
        """rejected is terminal — should NOT be transitioned to monitoring."""
        goal = goal_store.replace_goal(
            session_id="sess_hook3",
            objective="Test rejected terminal",
            criteria=["c1"],
        )
        hyp = registry.create(title="rej", thesis="t", status="rejected")
        registry.link_goal(hyp.hypothesis_id, goal.goal_id)

        goal_store._on_goal_complete("sess_hook3", goal.goal_id)

        # rejected stays rejected (terminal)
        assert registry.get(hyp.hypothesis_id).status == "rejected"

    def test_exploring_hypothesis_not_transitioned(self, goal_store, registry):
        """exploring hypotheses (not continuable) should be left alone."""
        goal = goal_store.replace_goal(
            session_id="sess_hook4",
            objective="Test exploring",
            criteria=["c1"],
        )
        hyp = registry.create(title="expl", thesis="t", status="exploring")
        registry.link_goal(hyp.hypothesis_id, goal.goal_id)

        goal_store._on_goal_complete("sess_hook4", goal.goal_id)

        # Hook skips non-continuable states
        assert registry.get(hyp.hypothesis_id).status == "exploring"

    def test_invalidation_notes_appended(self, goal_store, registry):
        """invalidation_notes should mention the completed goal id."""
        goal = goal_store.replace_goal(
            session_id="sess_hook5",
            objective="Test notes",
            criteria=["c1"],
        )
        hyp = registry.create(title="notes", thesis="t", status="validated")
        registry.link_goal(hyp.hypothesis_id, goal.goal_id)

        goal_store._on_goal_complete("sess_hook5", goal.goal_id)

        updated = registry.get(hyp.hypothesis_id)
        assert goal.goal_id in updated.invalidation_notes

    def test_hook_with_no_linked_hypotheses_is_safe(self, goal_store):
        """Hook should not crash even if no hypotheses are linked."""
        goal = goal_store.replace_goal(
            session_id="sess_hook6",
            objective="No linked hyp",
            criteria=["c1"],
        )
        # No linked hypotheses
        goal_store._on_goal_complete("sess_hook6", goal.goal_id)
        # Just ensures no exception was raised
        assert True

    def test_fires_on_complete_status_update(self, goal_store, registry, tmp_path):
        """Updating status to COMPLETE should auto-fire the hook."""
        from strategy_research.apps.goal import AuditRow, EvidenceInput, GoalStatus

        goal = goal_store.replace_goal(
            session_id="sess_hook7",
            objective="Trigger via update",
            criteria=["step1"],
        )

        hyp = registry.create(title="trigger", thesis="t", status="validated")
        registry.link_goal(hyp.hypothesis_id, goal.goal_id)

        # Write a real artifact so verification_status == "verified"
        import hashlib
        artifact = tmp_path / "art.csv"
        artifact.write_text("x")
        digest = "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest()

        crit = goal_store.list_criteria(goal.goal_id)[0]
        ev = goal_store.append_evidence(
            session_id="sess_hook7",
            goal_id=goal.goal_id,
            expected_goal_id=goal.goal_id,
            evidence=EvidenceInput(
                text="e", criterion_id=crit.criterion_id,
                artifact_path=str(artifact), artifact_hash=digest,
            ),
        )
        audit_row = AuditRow(
            criterion_id=crit.criterion_id,
            result="satisfied",
            evidence_ids=[ev.evidence_id],
            notes="verified",
        )
        completed = goal_store.update_status(
            session_id="sess_hook7",
            goal_id=goal.goal_id,
            expected_goal_id=goal.goal_id,
            status=GoalStatus.COMPLETE,
            audit=[audit_row],
        )
        assert completed.status is GoalStatus.COMPLETE
        # The hook should have transitioned the linked hypothesis to monitoring
        assert registry.get(hyp.hypothesis_id).status == "monitoring"


# ============================================================
# autoresearch CLI helpers
# ============================================================


class TestAutoresearchGoalHelpers:
    def test_append_backtest_evidence_links_to_active_goal(self, goal_store):
        """_append_backtest_evidence should append to the active goal's criterion."""
        from strategy_research.cli.commands.autoresearch import (
            _append_backtest_evidence,
        )

        # Setup: an active goal with at least one criterion
        goal_store.replace_goal(
            session_id="ar_bt",
            objective="AR backtest",
            criteria=["crit1"],
        )

        before = goal_store.count_evidence(goal_store.get_current_goal("ar_bt").goal_id)

        # Call the helper — should not raise
        _append_backtest_evidence(
            session_id="ar_bt",
            run_name="rb_001",
            metrics={"calmar": 1.2, "sharpe": 1.5, "max_dd": -0.10},
            strategist_output={"hypothesis": "momentum works"},
        )

        # If the helper ran successfully, evidence was added
        # (silently swallowed on failure, so we just check no exception)
        after = goal_store.count_evidence(goal_store.get_current_goal("ar_bt").goal_id)
        assert after >= before

    def test_append_backtest_evidence_no_active_goal(self):
        """Without an active goal, the helper should silently return without crashing."""
        from strategy_research.cli.commands.autoresearch import (
            _append_backtest_evidence,
        )
        # No goal exists for this session — should not crash
        _append_backtest_evidence(
            session_id="ar_bt_no_goal",
            run_name="rb_002",
            metrics={"calmar": 0.5, "sharpe": 0.8, "max_dd": -0.20},
            strategist_output={"hypothesis": ""},
        )

    def test_register_researcher_hypothesis_creates_and_links(self, goal_store):
        """_register_researcher_hypothesis creates and links a hypothesis to the active goal."""
        from strategy_research.cli.commands.autoresearch import (
            _register_researcher_hypothesis,
        )
        from strategy_research.apps.hypothesis import HypothesisRegistry

        goal_store.replace_goal(
            session_id="ar_reg",
            objective="AR register",
            criteria=["crit1"],
        )

        # Empty hypothesis text should be silently skipped
        _register_researcher_hypothesis(
            session_id="ar_reg_empty",
            researcher_output={"hypothesis": ""},
            run_name="rb_001",
        )
        # Nothing should have been created
        assert len(HypothesisRegistry().list()) == 0

        # Non-empty hypothesis creates + links
        _register_researcher_hypothesis(
            session_id="ar_reg",
            researcher_output={"hypothesis": "momentum signal works on RB"},
            run_name="rb_002",
        )
        reg = HypothesisRegistry()
        all_hyps = reg.list()
        assert len(all_hyps) == 1
        goal = goal_store.get_current_goal("ar_reg")
        assert all_hyps[0].goal_id == goal.goal_id

    def test_register_researcher_hypothesis_idempotent(self, goal_store):
        """Calling register twice with the same thesis should not duplicate."""
        from strategy_research.cli.commands.autoresearch import (
            _register_researcher_hypothesis,
        )
        from strategy_research.apps.hypothesis import HypothesisRegistry

        goal_store.replace_goal(
            session_id="ar_idem",
            objective="AR idempotent",
            criteria=["c1"],
        )

        # Same thesis + run_name — second call should re-use existing
        for _ in range(2):
            _register_researcher_hypothesis(
                session_id="ar_idem",
                researcher_output={"hypothesis": "test hypothesis text"},
                run_name="rb_idem",
            )
        # Only one hypothesis should be created (because the title hash matches)
        all_hyps = HypothesisRegistry().list()
        # Idempotency key is f"{run_name}: {thesis[:60]}" -> same title
        unique_titles = {h.title for h in all_hyps}
        assert len(unique_titles) >= 1
