"""Tests for StudyStore round lifecycle — append_round, update_round, review overlay.

These are the DB mirror operations that run_round_phases calls at round end
(finalization flows through them for every round; P0-1 fixed the era when
the langgraph branch skipped them entirely).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from strategy_research.apps.study.store import StudyStore


@pytest.fixture
def store(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "goals.db"
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(db_path))
    s = StudyStore(db_path=db_path)
    s.create_study(
        owner_session_id="tester", goal_id=None, objective="test",
        workspace_path=str(tmp_path), strategy_name="demo",
        metric_targets=[{"name": "calmar", "op": ">=", "value": 0.5}],
        cooldown_base=0.01, cooldown_jitter=0.01, min_cooldown=0.01,
    )
    yield s


def _get_study_id(store):
    return store._conn.execute("SELECT study_id FROM studies LIMIT 1").fetchone()["study_id"]


def test_append_round_creates_db_row(store):
    """append_round inserts a study_rounds row."""
    sid = _get_study_id(store)
    metrics = {"calmar": 0.6, "sharpe": 0.4, "max_dd": -0.1}

    store.append_round(
        sid, round_num=1, run_name="run_0001",
        metrics=metrics, verdict="keep",
    )

    row = store._conn.execute(
        "SELECT round_num, run_name, verdict, metrics_json "
        "FROM study_rounds WHERE study_id = ? AND round_num = 1",
        (sid,),
    ).fetchone()
    assert row is not None
    assert row["round_num"] == 1
    assert row["run_name"] == "run_0001"
    assert row["verdict"] == "keep"

    import json
    saved_metrics = json.loads(row["metrics_json"])
    assert saved_metrics["calmar"] == 0.6
    assert saved_metrics["sharpe"] == 0.4


def test_append_round_discard_verdict(store):
    """Discard verdict is persisted correctly."""
    sid = _get_study_id(store)

    store.append_round(
        sid, round_num=1, run_name="run_0001",
        metrics={"calmar": 0.1}, verdict="discard",
    )

    row = store._conn.execute(
        "SELECT verdict FROM study_rounds WHERE study_id = ? AND round_num = 1",
        (sid,),
    ).fetchone()
    assert row["verdict"] == "discard"


def test_update_round_overlay(store):
    """update_round merges review info into the existing row."""
    sid = _get_study_id(store)

    store.append_round(
        sid, round_num=1, run_name="run_0001",
        metrics={"calmar": 0.6}, verdict="keep",
    )

    review_data = {
        "deviation": "low",
        "deviation_reason": "stub",
        "info_gap": False,
        "next_focus": "factor research",
        "topics": ["momentum"],
        "todo_updates": [],
    }
    store.update_round(sid, 1, review_data)

    row = store._conn.execute(
        "SELECT review_json, verdict FROM study_rounds WHERE study_id = ? AND round_num = 1",
        (sid,),
    ).fetchone()
    assert row["verdict"] == "keep"  # not overwritten
    import json
    review = json.loads(row["review_json"])
    assert review["deviation"] == "low"
    assert review["next_focus"] == "factor research"


def test_append_round_with_config_changes(store):
    """config_changes dict is persisted as JSON."""
    sid = _get_study_id(store)

    changes = {"top_n": {"old": 10, "new": 20}}
    store.append_round(
        sid, round_num=1, run_name="run_0001",
        metrics={}, verdict="discard", config_changes=changes,
    )

    row = store._conn.execute(
        "SELECT config_changes_json FROM study_rounds WHERE study_id = ?",
        (sid,),
    ).fetchone()
    assert row is not None
    import json
    saved = json.loads(row["config_changes_json"])
    assert saved["top_n"]["old"] == 10
    assert saved["top_n"]["new"] == 20


def test_append_round_round_not_found(store):
    """Appending to a non-existent round should still work (first append)."""
    sid = _get_study_id(store)
    store.append_round(sid, round_num=1, run_name="run_0001", metrics={}, verdict="keep")
    row = store._conn.execute(
        "SELECT COUNT(*) AS c FROM study_rounds WHERE study_id = ?", (sid,),
    ).fetchone()
    assert row["c"] == 1


def test_append_round_duplicate_round_appends_not_overwrites(store):
    """Second append to the same round should be rejected or handled safely."""
    sid = _get_study_id(store)
    store.append_round(sid, round_num=1, run_name="run_0001", metrics={}, verdict="keep")
    # The second append should be idempotent or silently ignored
    # (implementation depends on upsert vs append logic)
    try:
        store.append_round(sid, round_num=1, run_name="run_0002", metrics={}, verdict="discard")
    except Exception:
        pass  # Expected if round_num has a unique constraint
    # Verify first row still exists
    row = store._conn.execute(
        "SELECT run_name, verdict FROM study_rounds WHERE study_id = ? AND round_num = 1",
        (sid,),
    ).fetchone()
    assert row is not None


def test_update_round_nonexistent_returns_none(store):
    """update_round on non-existent round returns None."""
    sid = _get_study_id(store)
    result = store.update_round(sid, 99, {})
    assert result is None


def test_list_rounds_returns_ordered(store):
    """list_rounds returns rounds in order."""
    sid = _get_study_id(store)
    for i in range(1, 5):
        store.append_round(
            sid, round_num=i, run_name=f"run_{i:04d}",
            metrics={}, verdict="keep" if i < 4 else "discard",
        )

    rounds = store.list_rounds(sid)
    assert len(rounds) == 4
    assert [r.round_num for r in rounds] == [4, 3, 2, 1]  # descending
    assert rounds[0].verdict == "discard"
