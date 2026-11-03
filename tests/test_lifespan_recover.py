"""Tests for FastAPI lifespan wiring — recover_on_startup must run.

The scheduler's ``recover_on_startup`` method itself is covered in
``tests/test_study_scheduler.py``. Here we verify the lifespan hook
actually invokes it during ``create_app`` so that uvicorn reload / process
restart does not leave RUNNING/QUEUED studies stranded.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from strategy_research.api.routers import study_helpers


@pytest.fixture
def _env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SR_SESSIONS_DB", str(tmp_path / "sessions.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))
    # Reset scheduler cache between tests so each gets a fresh instance.
    # (3c 迁移后缓存搬到 study_helpers，按 db_path 键控)
    study_helpers._scheduler_cache.clear()
    yield
    study_helpers._scheduler_cache.clear()


def test_lifespan_invokes_recover_on_startup(_env, monkeypatch):
    """``recover_on_startup`` must be awaited during lifespan startup."""
    from strategy_research.api.app import create_app

    calls: list[bool] = []

    async def fake_recover(self):
        calls.append(True)
        return []

    from strategy_research.apps.study import StudyScheduler
    monkeypatch.setattr(StudyScheduler, "recover_on_startup", fake_recover)

    app = create_app()
    with TestClient(app):
        pass  # enter/exit lifespan
    assert calls == [True], f"expected recover_on_startup to be called once, got {calls}"


def test_lifespan_recover_handles_running_study(_env, monkeypatch):
    """End-to-end: pre-seed a RUNNING study; after lifespan it becomes
    INTERRUPTED (per recover policy: running → interrupted, manual resume).
    """
    from strategy_research.api.app import create_app
    from strategy_research.apps.study import StudyStatus, StudyStore

    # Pre-seed a "left over from prior process" RUNNING study.
    store = StudyStore()
    rec = store.create_study(
        owner_session_id="sess-ghost",
        goal_id="g-ghost",
        objective="ghost",
        workspace_path="/tmp",
        strategy_name="s",
    )
    # Force into RUNNING to simulate "process died mid-run" state.
    store.update_execution_status(rec.study_id, StudyStatus.RUNNING)
    ghost_id = rec.study_id

    app = create_app()
    with TestClient(app):
        pass

    s = store.get_study(ghost_id)
    assert s is not None
    assert s.execution_status == StudyStatus.INTERRUPTED, (
        f"expected RUNNING → INTERRUPTED after recover, got {s.execution_status}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
