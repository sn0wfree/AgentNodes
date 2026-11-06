"""Tests for core.goal.store — SQLite-backed CRUD, stale-guard, completion audit.

The store is the heart of the goal ledger. We test:
  - replace_goal / get_current_goal / supersede semantics
  - update_goal with stale-write guard
  - append_evidence with criterion auto-cover
  - update_status + completion audit validation
  - account_usage + budget_limited promotion
  - delete_session_goals
  - snapshot + JSON serialization
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentnodes.apps.goal import (
    AuditRow,
    EvidenceInput,
    GoalStatus,
    GoalStore,
    RiskTier,
    StaleGoalError,
)

# ─── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path: Path) -> GoalStore:
    """Fresh GoalStore for each test."""
    db = tmp_path / "goals.db"
    return GoalStore(db_path=db)


@pytest.fixture
def sample_criteria() -> list[str]:
    return [
        "Define the research-only thesis and symbol universe",
        "Collect fresh market or benchmark evidence",
        "Record caveats, contradictions, and non-advice boundary",
    ]


# ─── Init / schema ───────────────────────────────────────────────────────


class TestInit:
    def test_creates_db_file(self, tmp_path: Path):
        db = tmp_path / "x.db"
        GoalStore(db_path=db)
        assert db.exists()

    def test_db_path_under_home_default(self):
        """Default db path lives under ~/.quantnodes-research/goals.db."""
        s = GoalStore(db_path=Path("/tmp/test_init_default.db"))
        assert s.db_path.name == "test_init_default.db"

    def test_idempotent_init(self, tmp_path: Path):
        """Opening an existing DB twice does not raise."""
        db = tmp_path / "y.db"
        GoalStore(db_path=db)
        GoalStore(db_path=db)  # must not raise

    def test_creates_all_tables(self, store: GoalStore):
        """Schema creates goals / claims / criteria / evidence / audits."""
        rows = store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = {row[0] for row in rows}
        assert "goals" in names
        assert "goal_claims" in names
        assert "goal_criteria" in names
        assert "goal_evidence" in names
        assert "goal_audits" in names


# ─── replace_goal / supersede ─────────────────────────────────────────────


class TestReplaceGoal:
    def test_creates_goal_with_criteria(self, store: GoalStore, sample_criteria):
        goal = store.replace_goal(
            session_id="sess_1",
            objective="Test objective",
            criteria=sample_criteria,
        )
        assert goal.session_id == "sess_1"
        assert goal.objective == "Test objective"
        assert goal.status is GoalStatus.ACTIVE
        assert goal.risk_tier is RiskTier.RESEARCH_GENERAL
        assert goal.goal_id.startswith("goal_")

    def test_supersedes_previous_current_goal(
        self, store: GoalStore, sample_criteria
    ):
        """A new replace_goal supersedes the previous active goal for the session."""
        g1 = store.replace_goal(session_id="sess", objective="first", criteria=sample_criteria)
        g2 = store.replace_goal(session_id="sess", objective="second", criteria=sample_criteria)
        assert g1.goal_id != g2.goal_id
        current = store.get_current_goal("sess")
        assert current is not None
        assert current.goal_id == g2.goal_id
        # The old one is now superseded
        old = store.get_goal(g1.goal_id)
        assert old is not None
        assert old.status is GoalStatus.SUPERSEDED

    def test_rejects_empty_objective(self, store: GoalStore, sample_criteria):
        with pytest.raises(ValueError, match="objective"):
            store.replace_goal(session_id="s", objective="", criteria=sample_criteria)

    def test_rejects_empty_criteria(self, store: GoalStore):
        with pytest.raises(ValueError, match="criterion is required"):
            store.replace_goal(session_id="s", objective="x", criteria=[])

    def test_rejects_live_trading_objective(self, store: GoalStore, sample_criteria):
        with pytest.raises(ValueError, match="live trading"):
            store.replace_goal(
                session_id="s", objective="buy AAPL now", criteria=sample_criteria
            )

    def test_rejects_live_risk_tier(self, store: GoalStore, sample_criteria):
        with pytest.raises(ValueError, match="live trading"):
            store.replace_goal(
                session_id="s",
                objective="Test",
                criteria=sample_criteria,
                risk_tier=RiskTier.LIVE_TRADING_OR_EXECUTION,
            )

    def test_rejects_negative_budget(self, store: GoalStore, sample_criteria):
        with pytest.raises(ValueError, match="must be positive"):
            store.replace_goal(
                session_id="s",
                objective="x",
                criteria=sample_criteria,
                token_budget=-10,
            )

    def test_creates_thesis_claim(
        self, store: GoalStore, sample_criteria
    ):
        goal = store.replace_goal(
            session_id="s", objective="Test", criteria=sample_criteria
        )
        claims = store.list_claims(goal.goal_id)
        assert len(claims) == 1
        assert claims[0].claim_type == "thesis"
        assert claims[0].status == "active"

    def test_criteria_have_step_index(
        self, store: GoalStore, sample_criteria
    ):
        goal = store.replace_goal(
            session_id="s", objective="x", criteria=sample_criteria
        )
        criteria = store.list_criteria(goal.goal_id)
        assert len(criteria) == 3
        assert [c.protocol_step for c in criteria] == ["step_1", "step_2", "step_3"]
        for c in criteria:
            assert c.required is True
            assert c.status == "pending"


# ─── get_current_goal / list_criteria / list_evidence ─────────────────────


class TestQueries:
    def test_get_current_goal_returns_latest(
        self, store: GoalStore, sample_criteria
    ):
        store.replace_goal(session_id="s", objective="v1", criteria=sample_criteria)
        g2 = store.replace_goal(session_id="s", objective="v2", criteria=sample_criteria)
        current = store.get_current_goal("s")
        assert current is not None
        assert current.goal_id == g2.goal_id

    def test_get_current_goal_no_session(self, store: GoalStore):
        assert store.get_current_goal("nonexistent") is None

    def test_list_criteria_ordered_by_step(
        self, store: GoalStore, sample_criteria
    ):
        goal = store.replace_goal(
            session_id="s", objective="x", criteria=sample_criteria
        )
        criteria = store.list_criteria(goal.goal_id)
        assert len(criteria) == 3

    def test_list_evidence_with_limit(
        self, store: GoalStore, sample_criteria
    ):
        goal = store.replace_goal(
            session_id="s", objective="x", criteria=sample_criteria
        )
        criteria = store.list_criteria(goal.goal_id)
        for i in range(5):
            store.append_evidence(
                session_id="s",
                goal_id=goal.goal_id,
                expected_goal_id=goal.goal_id,
                evidence=EvidenceInput(text=f"e{i}", criterion_id=criteria[0].criterion_id),
            )
        assert store.count_evidence(goal.goal_id) == 5
        assert len(store.list_evidence(goal.goal_id, limit=3)) == 3


# ─── update_goal / stale-guard ────────────────────────────────────────────


class TestUpdateGoal:
    def test_update_objective(self, store: GoalStore, sample_criteria):
        goal = store.replace_goal(
            session_id="s", objective="old", criteria=sample_criteria
        )
        updated = store.update_goal(
            session_id="s",
            goal_id=goal.goal_id,
            expected_goal_id=goal.goal_id,
            objective="new",
        )
        assert updated.objective == "new"

    def test_stale_guard_blocks_wrong_expected_id(
        self, store: GoalStore, sample_criteria
    ):
        goal = store.replace_goal(
            session_id="s", objective="x", criteria=sample_criteria
        )
        with pytest.raises(StaleGoalError):
            store.update_goal(
                session_id="s",
                goal_id=goal.goal_id,
                expected_goal_id="goal_wrong",
                objective="new",
            )

    def test_stale_guard_blocks_superseded_goal(
        self, store: GoalStore, sample_criteria
    ):
        g1 = store.replace_goal(
            session_id="s", objective="x", criteria=sample_criteria
        )
        store.replace_goal(
            session_id="s", objective="y", criteria=sample_criteria
        )
        with pytest.raises(StaleGoalError):
            store.update_goal(
                session_id="s",
                goal_id=g1.goal_id,
                expected_goal_id=g1.goal_id,
                objective="new",
            )


# ─── append_evidence ──────────────────────────────────────────────────────


class TestAppendEvidence:
    def test_appends_and_covers_criterion(
        self, store: GoalStore, sample_criteria
    ):
        goal = store.replace_goal(
            session_id="s", objective="x", criteria=sample_criteria
        )
        criteria = store.list_criteria(goal.goal_id)
        ev = store.append_evidence(
            session_id="s",
            goal_id=goal.goal_id,
            expected_goal_id=goal.goal_id,
            evidence=EvidenceInput(text="evidence 1", criterion_id=criteria[0].criterion_id),
        )
        assert ev.text == "evidence 1"
        # Criterion should now be "covered"
        updated_criteria = store.list_criteria(goal.goal_id)
        assert updated_criteria[0].status == "covered"

    def test_rejects_empty_text(
        self, store: GoalStore, sample_criteria
    ):
        goal = store.replace_goal(
            session_id="s", objective="x", criteria=sample_criteria
        )
        with pytest.raises(ValueError, match="text cannot be empty"):
            store.append_evidence(
                session_id="s",
                goal_id=goal.goal_id,
                expected_goal_id=goal.goal_id,
                evidence=EvidenceInput(text="   "),
            )

    def test_rejects_unknown_criterion_id(
        self, store: GoalStore, sample_criteria
    ):
        goal = store.replace_goal(
            session_id="s", objective="x", criteria=sample_criteria
        )
        with pytest.raises(ValueError, match="unknown criterion_id"):
            store.append_evidence(
                session_id="s",
                goal_id=goal.goal_id,
                expected_goal_id=goal.goal_id,
                evidence=EvidenceInput(text="x", criterion_id="crit_fake"),
            )


# ─── update_status + completion audit ─────────────────────────────────────


class TestUpdateStatus:
    def test_complete_requires_audit_for_required_criteria(
        self, store: GoalStore, sample_criteria
    ):
        goal = store.replace_goal(
            session_id="s", objective="x", criteria=sample_criteria
        )
        # No audit rows provided
        with pytest.raises(ValueError, match="missing audit row"):
            store.update_status(
                session_id="s",
                goal_id=goal.goal_id,
                expected_goal_id=goal.goal_id,
                status=GoalStatus.COMPLETE,
            )

    def test_complete_succeeds_with_full_audit(
        self, store: GoalStore, sample_criteria, tmp_path: Path
    ):
        goal = store.replace_goal(
            session_id="s", objective="x", criteria=sample_criteria
        )
        criteria = store.list_criteria(goal.goal_id)
        # Create verified evidence for each criterion
        audit_rows = []
        for crit in criteria:
            artifact = tmp_path / f"{crit.criterion_id}.txt"
            artifact.write_text("verified content")
            artifact_hash = "sha256:" + __import__("hashlib").sha256(
                artifact.read_bytes()
            ).hexdigest()
            ev = store.append_evidence(
                session_id="s",
                goal_id=goal.goal_id,
                expected_goal_id=goal.goal_id,
                evidence=EvidenceInput(
                    text=f"evidence for {crit.text}",
                    criterion_id=crit.criterion_id,
                    artifact_path=str(artifact),
                    artifact_hash=artifact_hash,
                ),
            )
            audit_rows.append(
                AuditRow(
                    criterion_id=crit.criterion_id,
                    result="satisfied",
                    evidence_ids=[ev.evidence_id],
                    notes="verified",
                )
            )
        updated = store.update_status(
            session_id="s",
            goal_id=goal.goal_id,
            expected_goal_id=goal.goal_id,
            status=GoalStatus.COMPLETE,
            audit=audit_rows,
            recap="all done",
        )
        assert updated.status is GoalStatus.COMPLETE
        assert updated.completed_at is not None
        assert updated.recap == "all done"

    def test_complete_rejects_unverified_evidence(
        self, store: GoalStore, sample_criteria
    ):
        goal = store.replace_goal(
            session_id="s", objective="x", criteria=sample_criteria
        )
        criteria = store.list_criteria(goal.goal_id)
        # Evidence without artifact/run_id → verification_status='unverified'
        unverified_rows = []
        for crit in criteria:
            ev = store.append_evidence(
                session_id="s",
                goal_id=goal.goal_id,
                expected_goal_id=goal.goal_id,
                evidence=EvidenceInput(text="x", criterion_id=crit.criterion_id),
            )
            unverified_rows.append(
                AuditRow(
                    criterion_id=crit.criterion_id,
                    result="satisfied",
                    evidence_ids=[ev.evidence_id],
                )
            )
        with pytest.raises(ValueError, match="verified evidence"):
            store.update_status(
                session_id="s",
                goal_id=goal.goal_id,
                expected_goal_id=goal.goal_id,
                status=GoalStatus.COMPLETE,
                audit=unverified_rows,
            )


# ─── account_usage / budget_limited ───────────────────────────────────────


class TestAccountUsage:
    def test_budget_limited_promotion(
        self, store: GoalStore, sample_criteria
    ):
        goal = store.replace_goal(
            session_id="s",
            objective="x",
            criteria=sample_criteria,
            token_budget=100,
        )
        updated = store.account_usage(
            session_id="s",
            goal_id=goal.goal_id,
            expected_goal_id=goal.goal_id,
            token_delta=50,
        )
        assert updated.tokens_used == 50
        assert updated.status is GoalStatus.ACTIVE
        # Cross the budget
        updated = store.account_usage(
            session_id="s",
            goal_id=goal.goal_id,
            expected_goal_id=goal.goal_id,
            token_delta=60,
        )
        assert updated.tokens_used == 110
        assert updated.status is GoalStatus.BUDGET_LIMITED

    def test_rejects_negative_deltas(
        self, store: GoalStore, sample_criteria
    ):
        goal = store.replace_goal(
            session_id="s", objective="x", criteria=sample_criteria
        )
        with pytest.raises(ValueError, match="non-negative"):
            store.account_usage(
                session_id="s",
                goal_id=goal.goal_id,
                expected_goal_id=goal.goal_id,
                token_delta=-1,
            )


# ─── snapshot / serialization ─────────────────────────────────────────────


class TestSnapshot:
    def test_get_goal_snapshot_json_safe(
        self, store: GoalStore, sample_criteria
    ):
        goal = store.replace_goal(
            session_id="s", objective="x", criteria=sample_criteria
        )
        snap = store.get_goal_snapshot(goal.goal_id)
        assert snap is not None
        assert snap["goal"]["goal_id"] == goal.goal_id
        # Round-trip through JSON
        encoded = json.dumps(snap, ensure_ascii=False)
        decoded = json.loads(encoded)
        assert decoded["goal"]["goal_id"] == goal.goal_id
        assert len(decoded["criteria"]) == 3

    def test_get_current_snapshot(
        self, store: GoalStore, sample_criteria
    ):
        store.replace_goal(session_id="s", objective="x", criteria=sample_criteria)
        snap = store.get_current_snapshot("s")
        assert snap is not None
        assert snap["goal"]["status"] == "active"


# ─── delete_session_goals ─────────────────────────────────────────────────


class TestDelete:
    def test_deletes_all_goal_rows(
        self, store: GoalStore, sample_criteria
    ):
        goal = store.replace_goal(
            session_id="s", objective="x", criteria=sample_criteria
        )
        criteria = store.list_criteria(goal.goal_id)
        store.append_evidence(
            session_id="s",
            goal_id=goal.goal_id,
            expected_goal_id=goal.goal_id,
            evidence=EvidenceInput(text="e", criterion_id=criteria[0].criterion_id),
        )
        deleted = store.delete_session_goals("s")
        assert deleted >= 1
        assert store.get_current_goal("s") is None
        assert store.count_evidence(goal.goal_id) == 0


# ─── env var override ─────────────────────────────────────────────────────


class TestEnvOverride:
    def test_env_var_path(self, tmp_path: Path, monkeypatch):
        custom = tmp_path / "custom_goals.db"
        monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(custom))
        s = GoalStore()
        assert s.db_path == custom.expanduser().resolve()


class TestListGoals:
    def test_list_goals_empty(self, tmp_path: Path):
        s = GoalStore(db_path=tmp_path / "test.db")
        goals = s.list_goals()
        assert goals == []

    def test_list_goals_returns_all(self, tmp_path: Path):
        s = GoalStore(db_path=tmp_path / "test.db")
        s.replace_goal(
            session_id="s1", objective="obj1", ui_summary="sum1",
            source="test", protocol="p", risk_tier=RiskTier.RESEARCH_GENERAL,
            criteria=["c1"],
        )
        s.replace_goal(
            session_id="s2", objective="obj2", ui_summary="sum2",
            source="test", protocol="p", risk_tier=RiskTier.MARKET_SPECIFIC_SHORT_TERM,
            criteria=["c2"],
        )
        goals = s.list_goals()
        assert len(goals) == 2

    def test_list_goals_filter_by_session(self, tmp_path: Path):
        s = GoalStore(db_path=tmp_path / "test.db")
        s.replace_goal(
            session_id="s1", objective="obj1", ui_summary="sum1",
            source="test", protocol="p", risk_tier=RiskTier.RESEARCH_GENERAL,
            criteria=["c1"],
        )
        s.replace_goal(
            session_id="s2", objective="obj2", ui_summary="sum2",
            source="test", protocol="p", risk_tier=RiskTier.MARKET_SPECIFIC_SHORT_TERM,
            criteria=["c2"],
        )
        goals = s.list_goals(session_id="s1")
        assert len(goals) == 1
        assert goals[0].session_id == "s1"

    def test_list_goals_filter_by_status(self, tmp_path: Path):
        s = GoalStore(db_path=tmp_path / "test.db")
        s.replace_goal(
            session_id="s1", objective="obj1", ui_summary="sum1",
            source="test", protocol="p", risk_tier=RiskTier.RESEARCH_GENERAL,
            criteria=["c1"],
        )
        goals_active = s.list_goals(status=GoalStatus.ACTIVE)
        assert len(goals_active) == 1
        goals_completed = s.list_goals(status=GoalStatus.COMPLETE)
        assert len(goals_completed) == 0

    def test_list_goals_limit(self, tmp_path: Path):
        s = GoalStore(db_path=tmp_path / "test.db")
        for i in range(5):
            s.replace_goal(
                session_id=f"s{i}", objective=f"obj{i}", ui_summary=f"sum{i}",
                source="test", protocol="p", risk_tier=RiskTier.RESEARCH_GENERAL,
                criteria=[f"c{i}"],
            )
        goals = s.list_goals(limit=3)
        assert len(goals) == 3
