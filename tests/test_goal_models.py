"""Tests for core.goal.models — dataclass invariants, enum values, freeze."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass

import pytest

from agentnodes.apps.goal.models import (
    AuditRow,
    EvidenceInput,
    EvidenceRecord,
    GoalClaim,
    GoalCriterion,
    GoalRecord,
    GoalStatus,
    RiskTier,
    StaleGoalError,
)

# ─── GoalStatus enum ─────────────────────────────────────────────────────


class TestGoalStatus:
    def test_all_lifecycle_values_present(self):
        """12 lifecycle states from active to superseded are all defined."""
        values = {s.value for s in GoalStatus}
        expected = {
            "active",
            "paused",
            "waiting_user",
            "needs_refresh",
            "insufficient_evidence",
            "compliance_blocked",
            "blocked",
            "budget_limited",
            "usage_limited",
            "complete",
            "cancelled",
            "superseded",
        }
        assert values == expected

    def test_status_is_str_enum(self):
        """GoalStatus members are also valid strings (str mixin)."""
        assert GoalStatus.ACTIVE == "active"
        assert isinstance(GoalStatus.ACTIVE, str)

    def test_completion_results_set(self):
        """These three values are valid completion audit results (matches store._COMPLETION_RESULTS)."""
        assert GoalStatus.COMPLETE.value == "complete"
        assert GoalStatus.CANCELLED.value == "cancelled"
        assert GoalStatus.SUPERSEDED.value == "superseded"


# ─── RiskTier enum ────────────────────────────────────────────────────────


class TestRiskTier:
    def test_all_four_tiers_present(self):
        """4 RiskTier values are all defined (per user decision)."""
        values = {t.value for t in RiskTier}
        expected = {
            "research_general",
            "market_specific_short_term",
            "personalized_advice_or_position_sizing",
            "live_trading_or_execution",
        }
        assert values == expected

    def test_live_tier_is_marked(self):
        """The LIVE_TRADING_OR_EXECUTION tier must exist (rejected at replace_goal)."""
        assert RiskTier.LIVE_TRADING_OR_EXECUTION.value == "live_trading_or_execution"


# ─── GoalRecord ───────────────────────────────────────────────────────────


class TestGoalRecord:
    def _make(self, **overrides):
        kwargs = dict(
            goal_id="goal_abc123",
            session_id="sess_xyz",
            status=GoalStatus.ACTIVE,
            objective="Test objective",
            ui_summary="Test summary",
            source="api",
            protocol="thesis_review",
            risk_tier=RiskTier.RESEARCH_GENERAL,
            token_budget=10000,
            tokens_used=42,
            turn_budget=20,
            turns_used=3,
            time_budget_seconds=600,
            time_used_seconds=12,
            budget_wrapup_sent=False,
            created_at="2026-07-22T00:00:00Z",
            updated_at="2026-07-22T00:01:00Z",
            completed_at=None,
            recap=None,
        )
        kwargs.update(overrides)
        return GoalRecord(**kwargs)

    def test_required_fields(self):
        """GoalRecord has the documented set of fields."""
        record = self._make()
        assert record.goal_id == "goal_abc123"
        assert record.session_id == "sess_xyz"
        assert record.status is GoalStatus.ACTIVE
        assert record.risk_tier is RiskTier.RESEARCH_GENERAL
        assert record.tokens_used == 42

    def test_defaults_for_optional_fields(self):
        """Budgets default to None, usage to 0, timestamps to empty."""
        record = GoalRecord(
            goal_id="g",
            session_id="s",
            status=GoalStatus.ACTIVE,
            objective="x",
            ui_summary="x",
            source="api",
            protocol="thesis_review",
            risk_tier=RiskTier.RESEARCH_GENERAL,
        )
        assert record.token_budget is None
        assert record.tokens_used == 0
        assert record.turn_budget is None
        assert record.turns_used == 0
        assert record.time_budget_seconds is None
        assert record.time_used_seconds == 0
        assert record.budget_wrapup_sent is False
        assert record.created_at == ""
        assert record.completed_at is None
        assert record.recap is None

    def test_frozen_dataclass(self):
        """GoalRecord is immutable (frozen=True)."""
        record = self._make()
        with pytest.raises(FrozenInstanceError):
            record.tokens_used = 99  # type: ignore[misc]

    def test_is_dataclass(self):
        record = self._make()
        assert is_dataclass(record)

    def test_field_count(self):
        """GoalRecord has 23 fields (22 + user_id from the D1 isolation migration)."""
        assert len(fields(GoalRecord)) == 23


# ─── GoalClaim ────────────────────────────────────────────────────────────


class TestGoalClaim:
    def test_basic_creation(self):
        c = GoalClaim(
            claim_id="clm_001",
            goal_id="goal_001",
            session_id="sess_001",
            claim_type="thesis",
            text="Momentum works in large caps",
            status="active",
        )
        assert c.claim_type == "thesis"
        assert c.text.startswith("Momentum")

    def test_defaults(self):
        c = GoalClaim(
            claim_id="c",
            goal_id="g",
            session_id="s",
            claim_type="thesis",
            text="x",
            status="active",
        )
        assert c.created_at == ""
        assert c.updated_at == ""

    def test_frozen(self):
        c = GoalClaim(
            claim_id="c",
            goal_id="g",
            session_id="s",
            claim_type="thesis",
            text="x",
            status="active",
        )
        with pytest.raises(FrozenInstanceError):
            c.text = "modified"  # type: ignore[misc]


# ─── GoalCriterion ────────────────────────────────────────────────────────


class TestGoalCriterion:
    def test_default_required_status(self):
        """Required=True and status=pending by default."""
        c = GoalCriterion(
            criterion_id="crit_001",
            goal_id="g",
            session_id="s",
            text="criterion text",
        )
        assert c.required is True
        assert c.status == "pending"
        assert c.freshness_requirement is None
        assert c.protocol_step is None

    def test_optional_fields(self):
        c = GoalCriterion(
            criterion_id="crit_001",
            goal_id="g",
            session_id="s",
            text="c",
            required=False,
            status="satisfied",
            freshness_requirement="daily",
            protocol_step="step_2",
        )
        assert c.required is False
        assert c.status == "satisfied"
        assert c.freshness_requirement == "daily"
        assert c.protocol_step == "step_2"

    def test_frozen(self):
        c = GoalCriterion(
            criterion_id="c",
            goal_id="g",
            session_id="s",
            text="x",
        )
        with pytest.raises(FrozenInstanceError):
            c.status = "covered"  # type: ignore[misc]


# ─── EvidenceInput + EvidenceRecord ───────────────────────────────────────


class TestEvidence:
    def test_evidence_input_defaults(self):
        e = EvidenceInput(text="some evidence")
        assert e.criterion_id is None
        assert e.claim_id is None
        assert e.evidence_type == "evidence"
        assert e.confidence is None
        assert e.caveat is None
        assert e.symbol_universe == []
        assert e.contradicts_claim_ids == []
        assert e.assumptions == {}

    def test_evidence_input_full(self):
        e = EvidenceInput(
            text="Sharpe = 0.85",
            criterion_id="crit_001",
            claim_id="clm_001",
            evidence_type="metric",
            tool_call_id="tc_123",
            run_id="run_0042",
            source_provider="tushare",
            symbol_universe=["000300.SH"],
            benchmark=["SPY"],
            timeframe="2020-2024",
            method="rolling_sharpe",
            assumptions={"vol_window": 60},
            artifact_path="/path/eq.csv",
            artifact_hash="sha256:abc",
            data_as_of="2024-12-31",
            confidence="high",
            caveat="limited sample",
            contradicts_claim_ids=["clm_002"],
        )
        assert e.symbol_universe == ["000300.SH"]
        assert e.assumptions["vol_window"] == 60
        assert e.artifact_hash == "sha256:abc"

    def test_evidence_record_persisted_fields(self):
        """EvidenceRecord has additional freshness/verification fields."""
        e = EvidenceRecord(
            evidence_id="ev_001",
            goal_id="g",
            session_id="s",
            text="x",
            freshness_status="fresh",
            verification_status="verified",
        )
        assert e.freshness_status == "fresh"
        assert e.verification_status == "verified"
        assert e.retrieved_at == ""
        assert e.contradicts_claim_ids == []


# ─── AuditRow ─────────────────────────────────────────────────────────────


class TestAuditRow:
    def test_basic(self):
        a = AuditRow(
            criterion_id="crit_001",
            result="satisfied",
            evidence_ids=["ev_001", "ev_002"],
            notes="verified",
        )
        assert a.result == "satisfied"
        assert len(a.evidence_ids) == 2
        assert a.notes == "verified"

    def test_default_notes(self):
        a = AuditRow(criterion_id="c", result="satisfied", evidence_ids=[])
        assert a.notes == ""


# ─── StaleGoalError ───────────────────────────────────────────────────────


class TestStaleGoalError:
    def test_is_value_error(self):
        """StaleGoalError is a ValueError subclass."""
        err = StaleGoalError("stale")
        assert isinstance(err, ValueError)
        assert str(err) == "stale"

    def test_raisable(self):
        with pytest.raises(StaleGoalError):
            raise StaleGoalError("expected_goal_id does not match")
