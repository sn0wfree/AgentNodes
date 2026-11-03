"""Tests for the goal subsystem: models, context, policy."""

from __future__ import annotations

import pytest

from strategy_research.apps.goal.context import (
    default_goal_criteria,
)
from strategy_research.apps.goal.models import (
    AuditRow,
    EvidenceInput,
    EvidenceRecord,
    GoalClaim,
    GoalCriterion,
    GoalRecord,
    GoalStatus,
    RiskTier,
)
from strategy_research.apps.goal.policy import (
    normalize_required_text,
    reject_live_execution_objective,
)

# ── Model Tests ──────────────────────────────────────────────────────


class TestGoalModels:
    def test_goal_status_enum(self):
        assert GoalStatus.ACTIVE.value == "active"
        assert GoalStatus.COMPLETE.value == "complete"
        assert GoalStatus.CANCELLED.value == "cancelled"
        assert len(GoalStatus) == 12

    def test_risk_tier_enum(self):
        assert RiskTier.RESEARCH_GENERAL.value == "research_general"
        assert RiskTier.LIVE_TRADING_OR_EXECUTION.value == "live_trading_or_execution"
        assert len(RiskTier) == 4

    def test_goal_record_creation(self):
        goal = GoalRecord(
            goal_id="g1",
            session_id="s1",
            status=GoalStatus.ACTIVE,
            objective="Test objective",
            ui_summary="Test summary",
            source="test",
            protocol="default",
            risk_tier=RiskTier.RESEARCH_GENERAL,
        )
        assert goal.goal_id == "g1"
        assert goal.session_id == "s1"
        assert goal.objective == "Test objective"
        assert goal.status == GoalStatus.ACTIVE

    def test_goal_record_with_budgets(self):
        goal = GoalRecord(
            goal_id="g1",
            session_id="s1",
            status=GoalStatus.ACTIVE,
            objective="Test objective",
            ui_summary="Test",
            source="test",
            protocol="default",
            risk_tier=RiskTier.RESEARCH_GENERAL,
            token_budget=10000,
            turn_budget=5,
            time_budget_seconds=300,
        )
        assert goal.token_budget == 10000
        assert goal.turn_budget == 5
        assert goal.time_budget_seconds == 300

    def test_goal_claim_creation(self):
        claim = GoalClaim(
            claim_id="c1",
            goal_id="g1",
            session_id="s1",
            claim_type="hypothesis",
            text="Momentum factor has positive IC",
            status="active",
        )
        assert claim.claim_id == "c1"
        assert claim.goal_id == "g1"
        assert claim.text == "Momentum factor has positive IC"

    def test_goal_criterion_creation(self):
        criterion = GoalCriterion(
            criterion_id="cr1",
            goal_id="g1",
            session_id="s1",
            text="IC Analysis",
            required=True,
        )
        assert criterion.criterion_id == "cr1"
        assert criterion.goal_id == "g1"
        assert criterion.text == "IC Analysis"
        assert criterion.required is True

    def test_evidence_input_creation(self):
        evidence = EvidenceInput(
            text="IC mean = 0.05, IR = 0.3",
        )
        assert evidence.text == "IC mean = 0.05, IR = 0.3"

    def test_audit_row_creation(self):
        audit = AuditRow(
            criterion_id="cr1",
            result="pass",
            evidence_ids=["e1", "e2"],
            notes="All criteria met",
        )
        assert audit.criterion_id == "cr1"
        assert audit.result == "pass"
        assert len(audit.evidence_ids) == 2


# ── Context Tests ────────────────────────────────────────────────────


class TestGoalContext:
    def test_default_goal_criteria(self):
        criteria = default_goal_criteria()
        assert len(criteria) == 3

    def test_default_criteria_are_strings(self):
        criteria = default_goal_criteria()
        assert all(isinstance(c, str) for c in criteria)


# ── Policy Tests ─────────────────────────────────────────────────────


class TestGoalPolicy:
    def test_reject_live_execution_buy(self):
        with pytest.raises(ValueError):
            reject_live_execution_objective("Buy 100 shares of AAPL")

    def test_reject_live_execution_execute(self):
        with pytest.raises(ValueError):
            reject_live_execution_objective("Execute trade on Binance")

    def test_reject_live_execution_sell(self):
        with pytest.raises(ValueError):
            reject_live_execution_objective("Sell 50 contracts of SPY puts")

    def test_accept_research_objective(self):
        # Should not raise
        reject_live_execution_objective("Analyze momentum factor IC")

    def test_accept_chinese_objective(self):
        # Should not raise
        reject_live_execution_objective("分析动量因子IC")

    def test_normalize_required_text_valid(self):
        assert normalize_required_text("hello", "test") == "hello"

    def test_normalize_required_text_empty(self):
        with pytest.raises(ValueError):
            normalize_required_text("", "test")

    def test_normalize_required_text_whitespace(self):
        with pytest.raises(ValueError):
            normalize_required_text("  ", "test")

    def test_normalize_required_text_none(self):
        # None is not handled gracefully - it raises AttributeError
        with pytest.raises(AttributeError):
            normalize_required_text(None, "test")


# ── Integration: Model Lifecycle ─────────────────────────────────────


class TestGoalLifecycle:
    def test_goal_status_transitions(self):
        """Test valid status transitions."""
        goal = GoalRecord(
            goal_id="g1",
            session_id="s1",
            status=GoalStatus.ACTIVE,
            objective="Test",
            ui_summary="Test",
            source="test",
            protocol="default",
            risk_tier=RiskTier.RESEARCH_GENERAL,
        )
        # Simulate status transitions
        assert goal.status == GoalStatus.ACTIVE

        # Active -> Complete
        completed = GoalRecord(
            **{**goal.__dict__, "status": GoalStatus.COMPLETE}
        )
        assert completed.status == GoalStatus.COMPLETE

    def test_goal_budget_tracking(self):
        """Test budget fields are properly tracked."""
        goal = GoalRecord(
            goal_id="g1",
            session_id="s1",
            status=GoalStatus.ACTIVE,
            objective="Test",
            ui_summary="Test",
            source="test",
            protocol="default",
            risk_tier=RiskTier.RESEARCH_GENERAL,
            token_budget=10000,
            tokens_used=5000,
            turn_budget=5,
            turns_used=2,
            time_budget_seconds=300,
            time_used_seconds=60,
        )
        assert goal.tokens_used == 5000
        assert goal.turns_used == 2
        assert goal.time_used_seconds == 60

    def test_goal_hierarchy_fields(self):
        """Test parent-child goal relationships."""
        GoalRecord(goal_id='p1', session_id='s1', status=GoalStatus.ACTIVE, objective='Parent goal', ui_summary='Parent', source='test', protocol='default', risk_tier=RiskTier.RESEARCH_GENERAL)
        child = GoalRecord(
            goal_id="c1",
            session_id="s1",
            status=GoalStatus.ACTIVE,
            objective="Child goal",
            ui_summary="Child",
            source="test",
            protocol="default",
            risk_tier=RiskTier.RESEARCH_GENERAL,
            parent_goal_id="p1",
        )
        assert child.parent_goal_id == "p1"

    def test_goal_workflow_binding(self):
        """Test workflow_id binding."""
        goal = GoalRecord(
            goal_id="g1",
            session_id="s1",
            status=GoalStatus.ACTIVE,
            objective="Test",
            ui_summary="Test",
            source="test",
            protocol="default",
            risk_tier=RiskTier.RESEARCH_GENERAL,
            workflow_id="factor_research",
        )
        assert goal.workflow_id == "factor_research"


# ── Integration: Evidence Model ──────────────────────────────────────


class TestEvidenceModel:
    def test_evidence_with_criterion(self):
        evidence = EvidenceInput(
            text="IC mean = 0.05",
            criterion_id="cr1",
        )
        assert evidence.criterion_id == "cr1"

    def test_evidence_with_source(self):
        evidence = EvidenceInput(
            text="Fetched 500 stocks",
            source_provider="tencent",
            source_type="market_data",
        )
        assert evidence.source_provider == "tencent"
        assert evidence.source_type == "market_data"

    def test_evidence_with_symbols(self):
        evidence = EvidenceInput(
            text="Analyzed 100 stocks",
            symbol_universe=["000001.SZ", "600519.SH"],
        )
        assert len(evidence.symbol_universe) == 2

    def test_evidence_record_fields(self):
        record = EvidenceRecord(
            evidence_id="ev1",
            goal_id="g1",
            session_id="s1",
            text="IC mean = 0.05",
            criterion_id="cr1",
            verification_status="verified",
            confidence="high",
        )
        assert record.verification_status == "verified"
        assert record.confidence == "high"


# ── Integration: Claim Model ─────────────────────────────────────────


class TestClaimModel:
    def test_claim_with_hypothesis(self):
        claim = GoalClaim(
            claim_id="c1",
            goal_id="g1",
            session_id="s1",
            claim_type="hypothesis",
            text="Momentum factor has positive IC on A-shares",
            status="active",
        )
        assert claim.claim_type == "hypothesis"
        assert claim.status == "active"

    def test_claim_status_transitions(self):
        claim = GoalClaim(
            claim_id="c1",
            goal_id="g1",
            session_id="s1",
            claim_type="hypothesis",
            text="Test claim",
            status="active",
        )
        # Simulate status change
        verified = GoalClaim(
            **{**claim.__dict__, "status": "verified"}
        )
        assert verified.status == "verified"
