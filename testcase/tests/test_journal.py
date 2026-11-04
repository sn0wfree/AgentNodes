"""Tests for goal/store.py — journal CRUD + novelty/regression gates."""
import os

import pytest

from strategy_research.apps.goal.store import GoalStore


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path):
    os.environ["QUANTNODES_RESEARCH_GOAL_DB_PATH"] = str(tmp_path / "goals.db")
    yield
    os.environ.pop("QUANTNODES_RESEARCH_GOAL_DB_PATH", None)


@pytest.fixture
def store():
    return GoalStore()


@pytest.fixture
def goal(store):
    return store.replace_goal(
        session_id="test-sess",
        objective="test objective",
        criteria=["calmar >= 0.5", "sharpe >= 0.3"],
    )


class TestJournalCRUD:
    def test_append_and_list(self, store, goal):
        entry = store.append_journal_entry(
            goal.goal_id, "test-sess", 1, "hyp-1", "test hypothesis",
            levers=["integrate"], predicted_affected=["calmar"],
        )
        assert entry.entry_id.startswith("journal_")
        assert entry.round_num == 1

        entries = store.list_journal_entries(goal.goal_id)
        assert len(entries) == 1
        assert entries[0].hypothesis_id == "hyp-1"

    def test_fill_attribution(self, store, goal):
        store.append_journal_entry(
            goal.goal_id, "test-sess", 1, "hyp-1", "test",
            levers=["integrate"], predicted_affected=["calmar"],
        )
        ok = store.fill_journal_attribution(
            goal.goal_id, "test-sess", 1, "accepted",
            {"calmar": "flipped", "sharpe": "still_F"},
        )
        assert ok

        entries = store.list_journal_entries(goal.goal_id)
        assert entries[0].gating_outcome == "accepted"
        assert entries[0].gating_attribution == {"calmar": "flipped", "sharpe": "still_F"}

    def test_get_latest(self, store, goal):
        store.append_journal_entry(goal.goal_id, "s", 1, "h1", "label1")
        store.append_journal_entry(goal.goal_id, "s", 2, "h2", "label2")
        latest = store.get_latest_journal_entry(goal.goal_id)
        assert latest.round_num == 2

    def test_list_limit(self, store, goal):
        for i in range(5):
            store.append_journal_entry(goal.goal_id, "s", i, f"h{i}", f"l{i}")
        entries = store.list_journal_entries(goal.goal_id, limit=3)
        assert len(entries) == 3


class TestNoveltyGate:
    def test_novel_first_time(self, store, goal):
        is_novel, reason = store.check_novelty(
            goal.goal_id, "new-hyp", ["integrate"], ["calmar"],
        )
        assert is_novel is True

    def test_duplicate_id(self, store, goal):
        store.append_journal_entry(
            goal.goal_id, "s", 1, "dup-id", "test",
            levers=["integrate"], predicted_affected=["calmar"],
        )
        is_novel, reason = store.check_novelty(
            goal.goal_id, "dup-id", ["integrate"], ["calmar"],
        )
        assert is_novel is False
        assert "duplicate hypothesis_id" in reason

    def test_signature_duplicate_reverted(self, store, goal):
        store.append_journal_entry(
            goal.goal_id, "s", 1, "h1", "test",
            levers=["integrate"], predicted_affected=["calmar"],
        )
        store.fill_journal_attribution(goal.goal_id, "s", 1, "reverted", {})
        is_novel, reason = store.check_novelty(
            goal.goal_id, "h2", ["integrate"], ["calmar"],
        )
        assert is_novel is False
        assert "signature duplicate" in reason

    def test_signature_duplicate_not_reverted(self, store, goal):
        store.append_journal_entry(
            goal.goal_id, "s", 1, "h1", "test",
            levers=["integrate"], predicted_affected=["calmar"],
        )
        store.fill_journal_attribution(goal.goal_id, "s", 1, "accepted", {})
        is_novel, reason = store.check_novelty(
            goal.goal_id, "h2", ["integrate"], ["calmar"],
        )
        assert is_novel is True

    def test_label_similarity(self, store, goal):
        store.append_journal_entry(
            goal.goal_id, "s", 1, "h1", "动量因子提升收益假设",
        )
        is_novel, reason = store.check_novelty(
            goal.goal_id, "h2", ["integrate"], ["calmar"],
        )
        # Different hypothesis_id and no lever/signature match → novel
        assert is_novel is True


class TestRegressionGate:
    def test_no_regression(self, store, goal):
        passes, regressed = store.check_regression(
            goal.goal_id, {"calmar": "flipped", "sharpe": "still_F"},
        )
        assert passes is True
        assert regressed == []

    def test_regression_detected(self, store, goal):
        passes, regressed = store.check_regression(
            goal.goal_id, {"calmar": "regressed", "sharpe": "flipped"},
        )
        assert passes is False
        assert "calmar" in regressed

    def test_archive_rejected(self, store, goal):
        store.append_journal_entry(
            goal.goal_id, "s", 1, "h1", "test",
            levers=["integrate"], predicted_affected=["calmar"],
        )
        ok = store.archive_rejected_edit(
            goal.goal_id, 1, "h1", "regression", "calmar regressed",
        )
        assert ok

        entries = store.list_journal_entries(goal.goal_id)
        assert "regression" in entries[0].archived_reason


class TestJournalContext:
    def test_empty_context(self, store, goal):
        ctx = store.build_journal_context(goal.goal_id, 1)
        assert ctx == ""

    def test_context_with_entries(self, store, goal):
        store.append_journal_entry(
            goal.goal_id, "s", 1, "h1", "动量因子假设",
            levers=["integrate"], predicted_affected=["calmar"],
        )
        store.fill_journal_attribution(
            goal.goal_id, "s", 1, "accepted", {"calmar": "flipped"},
        )
        ctx = store.build_journal_context(goal.goal_id, 2)
        assert "<journal-history>" in ctx
        assert "R1" in ctx
        assert "动量因子假设" in ctx

    def test_context_with_regression(self, store, goal):
        store.append_journal_entry(
            goal.goal_id, "s", 1, "h1", "test",
            levers=["integrate"], predicted_affected=["calmar"],
        )
        store.fill_journal_attribution(
            goal.goal_id, "s", 1, "reverted", {"calmar": "regressed"},
        )
        ctx = store.build_journal_context(goal.goal_id, 2)
        assert "回归" in ctx
