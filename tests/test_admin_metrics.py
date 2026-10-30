"""Tests for the ``/api/admin/metrics`` HTTP endpoint.

B4 D3: the endpoint no longer surfaces ``core.session.db.MetricsLogger``
(that legacy stack is retired). It now reports ``EventStore``'s own health —
backend kind, degraded flag, cache hit/miss/evictions — which is the honest
replacement because EventStore is the single remaining write path.

The auth surface is unchanged (``X-Admin-Token``), and the old
``?recent=`` parameter is still accepted (and still bounded) so existing
operator URLs do not start failing with 422.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from strategy_research.api.app import create_app

_TOKEN = "test-admin-secret-metrics"


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SR_ADMIN_TOKEN", _TOKEN)
    monkeypatch.setenv("SR_SESSIONS_DB", str(tmp_path / "sessions.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))
    return TestClient(create_app())


class TestAdminMetricsAuth:
    def test_requires_token(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SR_ADMIN_TOKEN", "secret")
        monkeypatch.setenv("SR_SESSIONS_DB", str(tmp_path / "sessions.db"))
        monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
        monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))
        c = TestClient(create_app())
        r = c.get("/api/admin/metrics")
        assert r.status_code == 401

    def test_disabled_returns_503(self, monkeypatch):
        monkeypatch.delenv("SR_ADMIN_TOKEN", raising=False)
        c = TestClient(create_app())
        r = c.get("/api/admin/metrics")
        assert r.status_code == 503


class TestAdminMetricsPayload:
    def test_reports_event_store_health(self, admin_client):
        r = admin_client.get("/api/admin/metrics", headers={"X-Admin-Token": _TOKEN})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        health = body["health"]["event_store"]
        # The fields EventStore.health_report() actually produces.
        assert "degraded" in health
        assert "backend" in health
        assert "cache_hit_rate" in health
        assert "cache_evictions" in health
        # The retired metrics surface is gone.
        assert "stats" not in body

    def test_legacy_recent_param_still_accepted(self, admin_client):
        """Operators may have ?recent= bookmarked; keep it working."""
        r = admin_client.get(
            "/api/admin/metrics?recent=5", headers={"X-Admin-Token": _TOKEN},
        )
        assert r.status_code == 200
        assert r.json()["recent"] == []

    def test_recent_param_upper_bound(self, admin_client):
        # recent > 200 must still be rejected (Query ge=1, le=200)
        r = admin_client.get(
            "/api/admin/metrics?recent=10000", headers={"X-Admin-Token": _TOKEN},
        )
        assert r.status_code == 422
