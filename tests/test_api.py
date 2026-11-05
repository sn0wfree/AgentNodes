"""Tests for API — FastAPI app + routers + CLI"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from strategy_research.interfaces.api.app import create_app
from strategy_research.interfaces.api.auth_tokens import create_token

# ============================================================
# fixtures
# ============================================================


@pytest.fixture
def auth_headers():
    """Bearer token for protected routers (goal/hypothesis/etc.)."""
    return {"Authorization": f"Bearer {create_token('admin')}"}


@pytest.fixture
def client(auth_headers):
    """创建测试客户端（带 auth 头；公开端点不受影响）。"""
    app = create_app()
    client = TestClient(app)
    client.headers.update(auth_headers)
    return client


@pytest.fixture
def client_with_goal(tmp_path, auth_headers):
    """带 goal DB 的测试客户端。"""
    db_path = str(tmp_path / "goals.db")
    app = create_app(goal_db_path=db_path)
    client = TestClient(app)
    client.headers.update(auth_headers)
    return client


@pytest.fixture
def client_with_hypothesis(tmp_path, auth_headers):
    """带 hypothesis 文件的测试客户端。"""
    hyp_path = str(tmp_path / "hypotheses.json")
    app = create_app(hypotheses_path=hyp_path)
    client = TestClient(app)
    client.headers.update(auth_headers)
    return client


# ============================================================
# app root + health
# ============================================================


class TestAppRoot:
    def test_root_serves_spa(self, client):
        """`/` serves the SPA index.html (static dir present in dev repo)."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")

    def test_health(self, client):
        """`/health` must return JSON — it must not be shadowed by the
        SPA catch-all route (regression for the routing-order bug)."""
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")
        assert resp.json()["status"] == "ok"


# ============================================================
# hypothesis router
# ============================================================




# ============================================================
# validation router
# ============================================================





# ============================================================
# memory router

# ============================================================
# memory router
# ============================================================


# ============================================================
# run router
# ============================================================


class TestRunRouter:
    def test_run_list_no_workspace(self, client):
        resp = client.get("/api/run/list?workspace_path=/nonexistent&strategy_name=test")
        assert resp.status_code == 200
        data = resp.json()
        assert data["runs"] == []

    def test_run_status_not_found(self, client):
        resp = client.get("/api/run/status?workspace_path=/tmp&strategy_name=test&run_name=run_0001")
        assert resp.status_code == 404


# ============================================================
# CLI: api serve
# ============================================================


class TestAPIServeCLI:
    def test_api_serve_help(self):
        import subprocess
        import sys
        result = subprocess.run(
            [sys.executable, "-m", "strategy_research.interfaces.cli", "api", "serve", "--help"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        assert result.returncode == 0
        assert "--host" in result.stdout
        assert "--port" in result.stdout
