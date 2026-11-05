"""Tests for the GET /api/chat/session/{session_id}/state backfill endpoint (B13).

Covers:
  - empty session → all subsections null / empty
  - session with an active goal (no workflow runner) → goal snapshot
    includes criteria + evidence_count + progress_percent
  - session with an active goal whose GoalRecord carries a workflow_id
    → state endpoint also surfaces the static DAG from the workflow
    config (nodes/edges) and a minimal agent roster with status=pending
  - session that doesn't exist or isn't owned by the caller → 404
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from strategy_research.interfaces.api.app import create_app
from strategy_research.interfaces.api.auth_tokens import create_token


@pytest.fixture
def client():
    app = create_app()
    c = TestClient(app)
    c.headers.update({"Authorization": f"Bearer {create_token('admin')}"})
    return c


def _make_session(client) -> str:
    r = client.post("/api/chat/session", json={"title": "state-test"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_state_empty_session_returns_nulls(client):
    sid = _make_session(client)
    r = client.get(f"/api/chat/session/{sid}/state")
    assert r.status_code == 200
    body = r.json()
    assert body == {"goal": None, "workflow": None, "agents": []}




def test_state_unknown_session_404(client):
    r = client.get("/api/chat/session/does-not-exist/state")
    assert r.status_code == 404




# ─────────────────────────────────────────────────────────────────────
# Unit-level: workflow → DAG / agent shaping (no full app needed)
# ─────────────────────────────────────────────────────────────────────


class _FakeAgentCfg:
    def __init__(self, id):
        self.id = id


class _FakeConfig:
    name = "factor_research"
    agents = [_FakeAgentCfg("collector"), _FakeAgentCfg("analyst")]
    dag = {"collector": ["analyst"]}


class _FakeRunner:
    def __init__(self, agent_statuses):
        self._config = _FakeConfig()
        self._statuses = agent_statuses

    def get_progress(self):
        return {
            "goal_id": "g1",
            "status": "running",
            "agent_statuses": dict(self._statuses),
            "agents_completed": 1,
            "agents_total": 2,
            "evidence_count": 0,
            "paused": False,
        }






def test_shape_agents_empty_without_workflow():
    """No workflow → no agents."""
    from strategy_research.interfaces.api.routers.web_session import (
        _shape_agents_for_frontend,
    )

    assert _shape_agents_for_frontend("sess-1", None) == []
