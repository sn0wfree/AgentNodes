"""Tests for study/store.py — study_rounds CRUD."""
import os

import pytest

from strategy_research.apps.study.store import StudyStore


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path):
    os.environ["QUANTNODES_RESEARCH_GOAL_DB_PATH"] = str(tmp_path / "goals.db")
    yield
    os.environ.pop("QUANTNODES_RESEARCH_GOAL_DB_PATH", None)


@pytest.fixture
def store():
    return StudyStore()


@pytest.fixture
def study(store):
    return store.create_study(
        owner_session_id="test-sess",
        goal_id="goal_123",
        objective="test",
        workspace_path="/tmp/ws",
        strategy_name="demo",
    )


class TestStudyRoundsCRUD:
    def test_append_round(self, store, study):
        record = store.append_round(
            study.study_id, 1, "run_0001",
            metrics={"calmar": 0.5, "sharpe": 0.3},
            verdict="keep",
        )
        assert record.round_id.startswith("round_")
        assert record.round_num == 1
        assert record.metrics == {"calmar": 0.5, "sharpe": 0.3}
        assert record.verdict == "keep"

    def test_list_rounds(self, store, study):
        store.append_round(study.study_id, 1, "run_0001", metrics={"calmar": 0.5})
        store.append_round(study.study_id, 2, "run_0002", metrics={"calmar": 0.6})
        rounds = store.list_rounds(study.study_id)
        assert len(rounds) == 2
        assert rounds[0].round_num == 2  # newest first
        assert rounds[1].round_num == 1

    def test_list_rounds_limit(self, store, study):
        for i in range(5):
            store.append_round(study.study_id, i, f"run_{i:04d}")
        rounds = store.list_rounds(study.study_id, limit=3)
        assert len(rounds) == 3

    def test_get_round(self, store, study):
        store.append_round(study.study_id, 3, "run_0003", verdict="discard")
        record = store.get_round(study.study_id, 3)
        assert record is not None
        assert record.run_name == "run_0003"
        assert record.verdict == "discard"

    def test_get_round_not_found(self, store, study):
        record = store.get_round(study.study_id, 999)
        assert record is None

    def test_round_goal_id_populated(self, store, study):
        record = store.append_round(study.study_id, 1, "run_0001")
        assert record.goal_id == "goal_123"
        # v2 single identity: session_id column == study_id
        assert record.session_id == study.study_id

    def test_round_with_evidence_ids(self, store, study):
        record = store.append_round(
            study.study_id, 1, "run_0001",
            evidence_ids=["ev_1", "ev_2"],
        )
        assert record.evidence_ids == ["ev_1", "ev_2"]

    def test_round_with_config_changes(self, store, study):
        record = store.append_round(
            study.study_id, 1, "run_0001",
            config_changes={"param": "top_n", "old": 10, "new": 20},
        )
        assert record.config_changes == {"param": "top_n", "old": 10, "new": 20}
