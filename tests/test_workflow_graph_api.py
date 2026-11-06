"""Tests for the workflow graph endpoint (GET /api/goal/workflow/{name}/graph).

The graph endpoint powers the standalone workflow page (React route
/workflow): it exposes the DAG structure (nodes + edges) derived from
the workflow YAML so the frontend can render it without running the
workflow first.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentnodes.interfaces.api.auth_tokens import create_token
from agentnodes.interfaces.api.routers.workflow import router


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/goal/workflow")
    return TestClient(app)


def auth_header(user_id: str = "tester") -> dict[str, str]:
    return {"Authorization": f"Bearer {create_token(user_id)}"}


class TestWorkflowGraph:
    def test_graph_returns_nodes_and_edges(self, client):
        res = client.get(
            "/api/goal/workflow/goal_autoresearch_9agent/graph",
            headers=auth_header(),
        )
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["name"] == "goal_autoresearch_9agent"

        nodes = data["nodes"]
        assert len(nodes) >= 9
        ids = {n["id"] for n in nodes}
        assert "researcher" in ids
        assert "backtest" in ids
        for n in nodes:
            assert n["label"]

        edges = data["edges"]
        assert len(edges) > 0
        for e in edges:
            assert e["source"] in ids
            assert e["target"] in ids

    def test_graph_edges_follow_input_dependencies(self, client):
        """dag[agent]=[deps] → edge source=dep, target=agent."""
        res = client.get(
            "/api/goal/workflow/goal_autoresearch_9agent/graph",
            headers=auth_header(),
        )
        edges = res.json()["edges"]
        targets = {e["target"] for e in edges}
        # Every non-root agent must be reachable as an edge target.
        assert "data_quality" in targets
        assert "factor_analyst" in targets
        # Researcher is a root (no incoming edges).
        researcher_edges = [e for e in edges if e["target"] == "researcher"]
        assert researcher_edges == []

    def test_graph_unknown_workflow_404(self, client):
        res = client.get(
            "/api/goal/workflow/does_not_exist/graph",
            headers=auth_header(),
        )
        assert res.status_code == 404
        assert "not found" in res.json()["detail"]

    def test_graph_is_consistent_with_workflow_list(self, client):
        """Every preset from /list must load a graph."""
        res = client.get("/api/goal/workflow/list", headers=auth_header())
        workflows = res.json()["workflows"]
        assert len(workflows) > 0
        for w in workflows:
            g = client.get(
                f"/api/goal/workflow/{w['name']}/graph",
                headers=auth_header(),
            )
            assert g.status_code == 200, f"graph failed for {w['name']}"
