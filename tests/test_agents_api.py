"""Integration tests for GET /api/agents/schemas.

Verifies the endpoint shape agreed in the design review:
  - dict form: ``{ "<role>": { role, fields, field_hints, ... } }``
  - startup warm-up populates the cache (no lazy-parse on first hit)
  - prompt file mtime change triggers automatic cache refresh
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture
def client():
    from agentnodes.interfaces.api.app import create_app

    app = create_app(workspace_path=None)
    return TestClient(app)


class TestAgentSchemasAPI:
    def test_returns_200_and_dict(self, client):
        resp = client.get("/api/agents/schemas")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, dict)

    def test_contains_main_agents(self, client):
        body = client.get("/api/agents/schemas").json()
        for role in ("researcher", "risk_controller", "portfolio_construction"):
            assert role in body, f"missing schema for {role}"

    def test_schema_shape(self, client):
        body = client.get("/api/agents/schemas").json()
        schema = body["researcher"]
        assert schema["role"] == "researcher"
        assert isinstance(schema["fields"], list)
        assert len(schema["fields"]) > 0
        assert isinstance(schema["field_hints"], dict)
        # Every field has a hints entry with the agreed keys
        for key in schema["fields"]:
            hints = schema["field_hints"][key]
            for prop in ("label", "type", "core", "enum_values", "format", "description"):
                assert prop in hints, f"field {key} missing hint property {prop}"

    def test_researcher_action_field(self, client):
        body = client.get("/api/agents/schemas").json()
        schema = body["researcher"]
        assert schema["action_field"] == "action"
        assert "optimize_param" in (schema["action_enum"] or [])

    def test_risk_controller_enum_auto_derived(self, client):
        body = client.get("/api/agents/schemas").json()
        hints = body["risk_controller"]["field_hints"]["risk_rating"]
        assert hints["type"] == "enum"
        # Auto-derived from the "Green | Yellow | Red" sample string
        assert hints["enum_values"] is not None
        assert "Green" in hints["enum_values"]
        assert "Red" in hints["enum_values"]

    def test_startup_warmup_populates_cache(self, client):
        """The lifespan startup hook pre-parses schemas (no lazy first hit)."""
        from agentnodes.interfaces.api.routers.agents import _schema_cache
        assert len(_schema_cache) > 0, "startup warm-up should have filled the cache"

    def test_mtime_change_refreshes_cache(self, client, tmp_path):
        """Editing a prompt file (mtime change) invalidates the cache.

        2026-11-05：不再依赖「上一个测试已填充缓存」——pytest-xdist 下
        测试顺序不受控。这里先显式打一次接口确保缓存已暖。
        """
        from agentnodes.interfaces.api.routers import agents as agents_router

        warm = client.get("/api/agents/schemas")
        assert warm.status_code == 200

        # Snapshot current cache state
        before = dict(agents_router._schema_cache)
        assert len(before) > 0

        # Simulate an mtime change: bump one tracked mtime so the check
        # sees a difference and re-parses (files themselves unchanged).
        some_role = next(iter(agents_router._schema_mtimes))
        agents_router._schema_mtimes[some_role] = -1.0

        resp = client.get("/api/agents/schemas")
        assert resp.status_code == 200
        # Cache was re-populated (fresh dict, same roles)
        after = agents_router._schema_cache
        assert set(after.keys()) == set(before.keys())
        # mtimes restored from the real files
        assert agents_router._schema_mtimes[some_role] != -1.0

    def test_no_auth_required_for_get(self, client):
        """GET /agents/schemas is read-only metadata — no bearer token."""
        resp = client.get("/api/agents/schemas")  # no Authorization header
        assert resp.status_code == 200
