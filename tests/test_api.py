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


class TestHypothesisRouter:
    def test_hypothesis_create(self, client_with_hypothesis):
        resp = client_with_hypothesis.post("/api/hypothesis/create", json={
            "title": "Momentum thesis",
            "thesis": "20-day winners continue",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "hypothesis_id" in data

    def test_hypothesis_list_empty(self, client_with_hypothesis):
        resp = client_with_hypothesis.get("/api/hypothesis/list")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert isinstance(data["hypotheses"], list)

    def test_hypothesis_search(self, client_with_hypothesis):
        resp = client_with_hypothesis.get("/api/hypothesis/search?q=momentum")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["query"] == "momentum"

    def test_hypothesis_get_not_found(self, client_with_hypothesis):
        resp = client_with_hypothesis.get("/api/hypothesis/nonexistent")
        assert resp.status_code == 404

    # ─── new behavior assertions ────────────────────────────────────────

    def test_hypothesis_create_accepts_universe_and_signal(self, client_with_hypothesis):
        """universe/signal_definition should be stored on the new hypothesis."""
        r = client_with_hypothesis.post("/api/hypothesis/create", json={
            "title": "carry_rb",
            "thesis": "TS momentum carry",
            "universe": "rb_futures",
            "signal_definition": "20d return > 5%",
        })
        assert r.status_code == 200
        hyp_id = r.json()["hypothesis_id"]
        # Verify via GET that the fields round-trip
        get_r = client_with_hypothesis.get(f"/api/hypothesis/{hyp_id}")
        body = get_r.json()["hypothesis"]
        assert body["universe"] == "rb_futures"
        assert body["signal_definition"] == "20d return > 5%"

    def test_hypothesis_list_filters_by_status(self, client_with_hypothesis):
        client_with_hypothesis.post("/api/hypothesis/create", json={
            "title": "h1", "thesis": "t", "status": "exploring",
        })
        client_with_hypothesis.post("/api/hypothesis/create", json={
            "title": "h2", "thesis": "t", "status": "testing",
        })
        resp = client_with_hypothesis.get("/api/hypothesis/list?status=testing")
        items = resp.json()["hypotheses"]
        assert all(h["status"] == "testing" for h in items)
        # And exploring filter excludes them
        resp2 = client_with_hypothesis.get("/api/hypothesis/list?status=exploring")
        items2 = resp2.json()["hypotheses"]
        assert all(h["status"] == "exploring" for h in items2)

    def test_hypothesis_list_returns_to_dict_payload(self, client_with_hypothesis):
        """Items must NOT include non-serializable internals (e.g. raw dataclass refs)."""
        client_with_hypothesis.post("/api/hypothesis/create", json={
            "title": "h_ser", "thesis": "x",
        })
        r = client_with_hypothesis.get("/api/hypothesis/list")
        items = r.json()["hypotheses"]
        assert len(items) >= 1
        item = items[0]
        assert "hypothesis_id" in item
        assert "title" in item
        assert "status" in item
        # datetime fields should be JSON-serializable (str or None)
        assert isinstance(item.get("created_at"), (str, type(None)))
        # No Python-specific leaks
        assert "_sa_instance_state" not in item

    def test_hypothesis_get_returns_to_dict_payload(self, client_with_hypothesis):
        r = client_with_hypothesis.post("/api/hypothesis/create", json={
            "title": "h_get", "thesis": "t",
        })
        hyp_id = r.json()["hypothesis_id"]
        get_r = client_with_hypothesis.get(f"/api/hypothesis/{hyp_id}")
        body = get_r.json()
        assert body["status"] == "ok"
        hyp = body["hypothesis"]
        assert hyp["hypothesis_id"] == hyp_id
        # to_dict includes run_cards etc.
        assert "run_cards" in hyp
        assert "related_ids" in hyp

    def test_hypothesis_update_returns_404_for_missing_id(self, client_with_hypothesis):
        """Updating a nonexistent id should return 404, not 500."""
        r = client_with_hypothesis.put("/api/hypothesis/update", json={
            "hypothesis_id": "hyp_does_not_exist",
            "status": "testing",
        })
        assert r.status_code == 404
        detail = r.json().get("detail", "")
        assert "hyp_does_not_exist" in detail or "not found" in detail.lower()

    def test_hypothesis_search_with_query_returns_results(self, client_with_hypothesis):
        client_with_hypothesis.post("/api/hypothesis/create", json={
            "title": "alpha_decay",
            "thesis": "TS alpha decays rapidly",
        })
        r = client_with_hypothesis.get("/api/hypothesis/search?q=alpha")
        body = r.json()
        assert body["status"] == "ok"
        # Should find the one we just created (FTS5)
        titles = [h["title"] for h in body["results"]]
        assert "alpha_decay" in titles

    def test_hypothesis_update_changes_status(self, client_with_hypothesis):
        r1 = client_with_hypothesis.post("/api/hypothesis/create", json={
            "title": "to_update", "thesis": "t", "status": "exploring",
        })
        hyp_id = r1.json()["hypothesis_id"]
        r2 = client_with_hypothesis.put("/api/hypothesis/update", json={
            "hypothesis_id": hyp_id,
            "status": "testing",
        })
        assert r2.status_code == 200
        assert r2.json()["hypothesis"]["status"] == "testing"


# ============================================================
# validation router
# ============================================================


class TestValidationRouter:
    def test_validate_run_not_found(self, client, tmp_path, monkeypatch):
        # A3: containment check fires first if path is outside the
        # configured root. Use the home dir as root and an in-root
        # missing dir → still 404.
        monkeypatch.setenv("STRATEGY_RESEARCH_VALIDATE_ROOT", str(tmp_path))
        missing = tmp_path / "no-such-run"
        resp = client.post("/api/validate/run", json={"run_dir": str(missing)})
        assert resp.status_code == 404

    def test_validate_run_rejects_path_outside_root(self, client, tmp_path, monkeypatch):
        """A3: paths outside STRATEGY_RESEARCH_VALIDATE_ROOT → 400."""
        monkeypatch.setenv("STRATEGY_RESEARCH_VALIDATE_ROOT", str(tmp_path))
        resp = client.post("/api/validate/run", json={"run_dir": "/etc"})
        assert resp.status_code == 400



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
            [sys.executable, "-m", "strategy_research.cli", "api", "serve", "--help"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        assert result.returncode == 0
        assert "--host" in result.stdout
        assert "--port" in result.stdout
