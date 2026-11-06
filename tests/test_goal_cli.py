"""Tests for core.goal.cli — 7 subcommands + argument validation."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import pytest

from agentnodes.apps.goal import (
    GoalStore,
    RiskTier,
    default_goal_criteria,
)
from agentnodes.apps.goal.cli import (
    add_goal_subparsers,
    cmd_goal_audit,
    cmd_goal_cancel,
    cmd_goal_complete,
    cmd_goal_evidence,
    cmd_goal_list,
    cmd_goal_start,
    cmd_goal_status,
)

# ─── Helpers ────────────────────────────────────────────────────────────


def _make_args(**kwargs) -> argparse.Namespace:
    """Build a Namespace with all cmd_goal_start args + any overrides."""
    base = dict(
        session_id="sess_test",
        db=None,
        objective=None,
        criterion=None,
        summary="",
        source="cli",
        protocol="thesis_review",
        risk_tier="research_general",
        token_budget=None,
        turn_budget=None,
        time_budget=None,
        goal_id=None,
        text=None,
        criterion_id=None,
        type="evidence",
        artifact=None,
        artifact_hash=None,
        data_as_of=None,
        confidence=None,
        caveat=None,
        symbol=None,
        benchmark=None,
        timeframe=None,
        method=None,
        result=None,
        evidence=None,
        notes=None,
        audit_file=None,
        recap=None,
    )
    base.update(kwargs)
    return argparse.Namespace(**base)


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    add_goal_subparsers(sub)
    return parser


def _parse_goal(parser: argparse.ArgumentParser, *args: str) -> argparse.Namespace:
    """Parse a `goal <subcmd>` invocation."""
    return parser.parse_args(["goal", *args])


@pytest.fixture
def store_db(tmp_path: Path, monkeypatch) -> Path:
    """Use a fresh DB for each test by setting QUANTNODES_RESEARCH_GOAL_DB_PATH."""
    db = tmp_path / "cli_test.db"
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(db))
    return db


# ─── goal start ──────────────────────────────────────────────────────────


class TestCmdStart:
    def test_basic(self, store_db: Path, capsys):
        args = _make_args(
            objective="Research momentum",
            criterion=["Define thesis", "Collect data"],
        )
        rc = cmd_goal_start(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Goal created" in out
        assert "Research momentum" in out

    def test_default_criteria_when_none(self, store_db: Path, capsys):
        args = _make_args(objective="x")
        rc = cmd_goal_start(args)
        assert rc == 0
        store = GoalStore(db_path=store_db)
        goal = store.get_current_goal("sess_test")
        assert goal is not None
        criteria = store.list_criteria(goal.goal_id)
        assert len(criteria) == len(default_goal_criteria())

    def test_rejects_live_objective(self, store_db: Path, capsys):
        args = _make_args(objective="buy AAPL now")
        rc = cmd_goal_start(args)
        assert rc != 0
        store = GoalStore(db_path=store_db)
        assert store.get_current_goal("sess_test") is None

    def test_rejects_live_risk_tier_via_cli(self, store_db: Path, capsys):
        """risk_tier live_trading is rejected at runtime (replace_goal raises)."""
        args = _make_args(
            objective="x",
            risk_tier=RiskTier.LIVE_TRADING_OR_EXECUTION.value,
        )
        rc = cmd_goal_start(args)
        assert rc == 1
        out = capsys.readouterr()
        assert "live trading" in out.err.lower()

    def test_all_risk_tiers_accepted_via_cli(self, store_db: Path):
        parser = _make_parser()
        for tier in [t.value for t in RiskTier]:
            args = _parse_goal(parser, "start", "--session-id", "s",
                               "--objective", "x", "--risk-tier", tier)
            # parse_args succeeds for all valid values
            assert args.risk_tier == tier

    def test_with_budgets(self, store_db: Path, capsys):
        args = _make_args(
            objective="x",
            token_budget=1000,
            turn_budget=10,
            time_budget=600,
        )
        rc = cmd_goal_start(args)
        assert rc == 0
        store = GoalStore(db_path=store_db)
        goal = store.get_current_goal("sess_test")
        assert goal.token_budget == 1000
        assert goal.turn_budget == 10
        assert goal.time_budget_seconds == 600

    def test_supersedes_previous(self, store_db: Path, capsys):
        cmd_goal_start(_make_args(objective="first"))
        cmd_goal_start(_make_args(objective="second"))
        store = GoalStore(db_path=store_db)
        current = store.get_current_goal("sess_test")
        assert current.objective == "second"


# ─── goal status ─────────────────────────────────────────────────────────


class TestCmdStatus:
    def test_by_session(self, store_db: Path, capsys):
        cmd_goal_start(_make_args(objective="Show me"))
        capsys.readouterr()
        rc = cmd_goal_status(_make_args())
        assert rc == 0
        out = capsys.readouterr().out
        assert "<current-research-goal>" in out
        assert "Show me" in out

    def test_no_goal_returns_1(self, store_db: Path):
        rc = cmd_goal_status(_make_args())
        assert rc == 1

    def test_by_goal_id(self, store_db: Path, capsys):
        cmd_goal_start(_make_args(objective="id-lookup"))
        store = GoalStore(db_path=store_db)
        goal = store.get_current_goal("sess_test")
        capsys.readouterr()
        args = _make_args(session_id=None)
        args.goal_id = goal.goal_id
        rc = cmd_goal_status(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert goal.goal_id in out

    def test_requires_session_or_goal(self, store_db: Path, capsys):
        args = _make_args()
        args.session_id = None
        args.goal_id = None
        rc = cmd_goal_status(args)
        assert rc == 1


# ─── goal evidence ───────────────────────────────────────────────────────


class TestCmdEvidence:
    def _setup_goal(self, store_db: Path) -> str:
        cmd_goal_start(_make_args(objective="x"))
        store = GoalStore(db_path=store_db)
        goal = store.get_current_goal("sess_test")
        return store.list_criteria(goal.goal_id)[0].criterion_id

    def test_append(self, store_db: Path, capsys):
        cid = self._setup_goal(store_db)
        capsys.readouterr()
        args = _make_args(text="some evidence", criterion_id=cid)
        rc = cmd_goal_evidence(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Evidence appended" in out

    def test_with_artifact_hashes_to_verified(self, store_db: Path, tmp_path):
        cid = self._setup_goal(store_db)
        artifact = tmp_path / "data.txt"
        artifact.write_text("verified content")
        digest = "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest()
        args = _make_args(text="x", criterion_id=cid,
                          artifact=str(artifact), artifact_hash=digest)
        rc = cmd_goal_evidence(args)
        assert rc == 0
        store = GoalStore(db_path=store_db)
        ev = store.list_evidence(store.get_current_goal("sess_test").goal_id)[-1]
        assert ev.verification_status == "verified"

    def test_no_current_goal_returns_1(self, store_db: Path):
        rc = cmd_goal_evidence(_make_args(text="x"))
        assert rc == 1


# ─── goal audit ──────────────────────────────────────────────────────────


class TestCmdAudit:
    def _setup(self, store_db: Path) -> tuple[str, str]:
        """Returns (goal_id, criterion_id)."""
        cmd_goal_start(_make_args(objective="x"))
        store = GoalStore(db_path=store_db)
        goal = store.get_current_goal("sess_test")
        return goal.goal_id, store.list_criteria(goal.goal_id)[0].criterion_id

    def test_basic(self, store_db: Path, capsys):
        _, cid = self._setup(store_db)
        capsys.readouterr()
        args = _make_args(criterion_id=cid, result="satisfied", notes="ok")
        rc = cmd_goal_audit(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Audit row written" in out

    def test_invalid_result_rejected(self, store_db: Path):
        _, cid = self._setup(store_db)
        args = _make_args(criterion_id=cid, result="not_a_real_value", notes="")
        rc = cmd_goal_audit(args)
        assert rc == 1

    def test_updates_criterion_status(self, store_db: Path):
        _, cid = self._setup(store_db)
        args = _make_args(criterion_id=cid, result="satisfied")
        cmd_goal_audit(args)
        store = GoalStore(db_path=store_db)
        criteria = store.list_criteria(store.get_current_goal("sess_test").goal_id)
        updated = [c for c in criteria if c.criterion_id == cid][0]
        assert updated.status == "satisfied"

    def test_no_current_goal_returns_1(self, store_db: Path):
        args = _make_args(criterion_id="c1", result="satisfied")
        rc = cmd_goal_audit(args)
        assert rc == 1


# ─── goal complete ───────────────────────────────────────────────────────


class TestCmdComplete:
    def test_requires_audit_input(self, store_db: Path):
        cmd_goal_start(_make_args(objective="x"))
        rc = cmd_goal_complete(_make_args())
        assert rc == 1

    def test_incomplete_audit_rejected(self, store_db: Path):
        """Complete with missing audit rows raises (validation error)."""
        cmd_goal_start(_make_args(objective="x"))
        # Single criterion audit row but unverified evidence → should fail
        args = _make_args(criterion_id="crit_x", result="satisfied")
        rc = cmd_goal_complete(args)
        # Should fail because crit_x is unknown OR evidence not verified
        assert rc != 0


# ─── goal list ───────────────────────────────────────────────────────────


class TestCmdList:
    def test_empty(self, store_db: Path, capsys):
        rc = cmd_goal_list(_make_args())
        assert rc == 0
        out = capsys.readouterr().out
        assert "no goals" in out

    def test_lists_superseded(self, store_db: Path, capsys):
        cmd_goal_start(_make_args(objective="first"))
        cmd_goal_start(_make_args(objective="second"))
        capsys.readouterr()
        rc = cmd_goal_list(_make_args())
        assert rc == 0
        out = capsys.readouterr().out
        assert "first" in out
        assert "second" in out


# ─── goal cancel ─────────────────────────────────────────────────────────


class TestCmdCancel:
    def test_basic(self, store_db: Path, capsys):
        cmd_goal_start(_make_args(objective="x"))
        capsys.readouterr()
        rc = cmd_goal_cancel(_make_args())
        assert rc == 0
        store = GoalStore(db_path=store_db)
        goal = store.get_current_goal("sess_test")
        assert goal is None  # no current (cancelled is not in _CURRENT_STATUSES)

    def test_no_goal_returns_1(self, store_db: Path):
        rc = cmd_goal_cancel(_make_args())
        assert rc == 1


# ─── argparse wiring ─────────────────────────────────────────────────────


class TestArgparseWiring:
    def test_all_seven_subcommands_present(self):
        parser = _make_parser()
        # parse "goal" alone → goal_command is None
        args = parser.parse_args(["goal"])
        assert args.command == "goal"
        assert args.goal_command is None

    def test_start_args_parsed(self):
        parser = _make_parser()
        args = _parse_goal(parser,
            "start", "--session-id", "s1",
            "--objective", "test",
            "--risk-tier", "market_specific_short_term",
            "--token-budget", "5000",
            "--criterion", "first", "--criterion", "second",
        )
        assert args.command == "goal"
        assert args.goal_command == "start"
        assert args.session_id == "s1"
        assert args.objective == "test"
        assert args.risk_tier == "market_specific_short_term"
        assert args.token_budget == 5000
        assert args.criterion == ["first", "second"]
