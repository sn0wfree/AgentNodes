"""Tests for admin API: kill switch, metrics, audit log, auth."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agentnodes.interfaces.api.app import create_app
from agentnodes.kernel.agent.compact import (
    reset_compaction_metrics,
)


@pytest.fixture
def client_with_admin_token(monkeypatch):
    """Create test client with admin token enabled."""
    monkeypatch.setenv("SR_ADMIN_TOKEN", "test-admin-secret-123")
    reset_compaction_metrics()
    app = create_app()
    return TestClient(app)


@pytest.fixture
def client_no_admin_token(monkeypatch):
    """Create test client with admin token DISABLED."""
    monkeypatch.delenv("SR_ADMIN_TOKEN", raising=False)
    app = create_app()
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset metrics and audit log before each test (autouse).

    Uses fresh module references to handle cases where other test files
    reloaded the module via importlib.reload().
    """
    # Re-import to get current module reference (handles reload from
    # other test files like test_compact_safety.py)
    from agentnodes.kernel.agent import compact as compact_mod
    compact_mod.reset_compaction_metrics()
    # Clear audit log
    from agentnodes.interfaces.api.routers import admin as admin_mod
    admin_mod._audit_log.clear()
    # Reset keep_all override
    compact_mod.set_keep_all_override(False)
    yield


# ── Auth tests ───────────────────────────────────────────────────────


class TestAdminAuth:
    def test_health_check_no_auth_required(self, client_no_admin_token):
        """Health endpoint accessible without token."""
        r = client_no_admin_token.get("/api/admin/health")
        assert r.status_code == 200
        assert r.json() == {"admin_enabled": False}

    def test_health_check_shows_enabled(self, client_with_admin_token):
        """Health endpoint shows admin is enabled."""
        r = client_with_admin_token.get("/api/admin/health")
        assert r.status_code == 200
        assert r.json() == {"admin_enabled": True}

    def test_protected_endpoint_without_token_401(self, client_with_admin_token):
        r = client_with_admin_token.get("/api/admin/compaction/metrics")
        assert r.status_code == 401

    def test_protected_endpoint_wrong_token_401(self, client_with_admin_token):
        r = client_with_admin_token.get(
            "/api/admin/compaction/metrics",
            headers={"X-Admin-Token": "wrong"},
        )
        assert r.status_code == 401

    def test_admin_disabled_503(self, client_no_admin_token):
        """When SR_ADMIN_TOKEN unset, admin endpoints return 503."""
        r = client_no_admin_token.get("/api/admin/compaction/metrics")
        assert r.status_code == 503


# ── Keep-all toggle tests ────────────────────────────────────────────


class TestKeepAllToggle:
    def test_toggle_to_true(self, client_with_admin_token):
        r = client_with_admin_token.post(
            "/api/admin/compaction/keep-all/true?confirm=yes",
            headers={"X-Admin-Token": "test-admin-secret-123"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["keep_all_compactions"] is True
        from agentnodes.kernel.agent import compact
        assert compact._KEEP_ALL_COMPACTIONS_OVERRIDE is True

    def test_toggle_to_false(self, client_with_admin_token):
        # Set true first
        client_with_admin_token.post(
            "/api/admin/compaction/keep-all/true?confirm=yes",
            headers={"X-Admin-Token": "test-admin-secret-123"},
        )
        # Then false
        r = client_with_admin_token.post(
            "/api/admin/compaction/keep-all/false?confirm=yes",
            headers={"X-Admin-Token": "test-admin-secret-123"},
        )
        assert r.status_code == 200
        assert r.json()["keep_all_compactions"] is False

    def test_toggle_requires_confirm(self, client_with_admin_token):
        r = client_with_admin_token.post(
            "/api/admin/compaction/keep-all/true",  # no confirm
            headers={"X-Admin-Token": "test-admin-secret-123"},
        )
        assert r.status_code == 400

    def test_toggle_wrong_confirm_value(self, client_with_admin_token):
        r = client_with_admin_token.post(
            "/api/admin/compaction/keep-all/true?confirm=no",
            headers={"X-Admin-Token": "test-admin-secret-123"},
        )
        assert r.status_code == 400


# ── Metrics tests ────────────────────────────────────────────────────


class TestCompactionMetrics:
    def test_get_initial_metrics(self, client_with_admin_token):
        r = client_with_admin_token.get(
            "/api/admin/compaction/metrics",
            headers={"X-Admin-Token": "test-admin-secret-123"},
        )
        assert r.status_code == 200
        body = r.json()
        assert "metrics" in body
        assert body["metrics"]["total_hidden"] == 0
        assert body["metrics"]["total_kept"] == 0
        assert body["metrics"]["l4_aborts"] == 0
        assert body["metrics"]["filter_calls"] == 0

    def test_metrics_reflect_changes(self, client_with_admin_token):
        # Directly bump metrics (use fresh module reference)
        from agentnodes.kernel.agent import compact as compact_mod
        compact_mod._compaction_metrics["total_hidden"] = 5
        compact_mod._compaction_metrics["total_kept"] = 1
        compact_mod._compaction_metrics["l4_aborts"] = 2
        r = client_with_admin_token.get(
            "/api/admin/compaction/metrics",
            headers={"X-Admin-Token": "test-admin-secret-123"},
        )
        body = r.json()
        assert body["metrics"]["total_hidden"] == 5
        assert body["metrics"]["total_kept"] == 1
        assert body["metrics"]["l4_aborts"] == 2

    def test_reset_metrics(self, client_with_admin_token):
        from agentnodes.kernel.agent import compact as compact_mod
        compact_mod._compaction_metrics["total_hidden"] = 99
        r = client_with_admin_token.post(
            "/api/admin/compaction/metrics/reset?confirm=yes",
            headers={"X-Admin-Token": "test-admin-secret-123"},
        )
        assert r.status_code == 200
        assert compact_mod._compaction_metrics["total_hidden"] == 0

    def test_reset_requires_confirm(self, client_with_admin_token):
        r = client_with_admin_token.post(
            "/api/admin/compaction/metrics/reset",
            headers={"X-Admin-Token": "test-admin-secret-123"},
        )
        assert r.status_code == 400


# ── Audit log tests ──────────────────────────────────────────────────


class TestAuditLog:
    def test_audit_log_records_toggle(self, client_with_admin_token):
        client_with_admin_token.post(
            "/api/admin/compaction/keep-all/true?confirm=yes",
            headers={"X-Admin-Token": "test-admin-secret-123"},
        )
        r = client_with_admin_token.get(
            "/api/admin/audit-log",
            headers={"X-Admin-Token": "test-admin-secret-123"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["count"] >= 1
        actions = [e["action"] for e in body["entries"]]
        assert "compaction.keep_all.toggle" in actions

    def test_audit_log_records_reset(self, client_with_admin_token):
        client_with_admin_token.post(
            "/api/admin/compaction/metrics/reset?confirm=yes",
            headers={"X-Admin-Token": "test-admin-secret-123"},
        )
        r = client_with_admin_token.get(
            "/api/admin/audit-log",
            headers={"X-Admin-Token": "test-admin-secret-123"},
        )
        body = r.json()
        actions = [e["action"] for e in body["entries"]]
        assert "compaction.metrics.reset" in actions

    def test_audit_log_limit(self, client_with_admin_token):
        r = client_with_admin_token.get(
            "/api/admin/audit-log?limit=5",
            headers={"X-Admin-Token": "test-admin-secret-123"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["count"] <= 5


# ── Module-level functions ──────────────────────────────────────────


class TestModuleFunctions:
    def test_get_compaction_metrics_returns_copy(self):
        from agentnodes.kernel.agent import compact as compact_mod
        m = compact_mod.get_compaction_metrics()
        assert isinstance(m, dict)
        m["total_hidden"] = 999
        # Original should not be modified
        assert compact_mod._compaction_metrics["total_hidden"] != 999

    def test_set_keep_all_override(self):
        from agentnodes.kernel.agent import compact as compact_mod
        compact_mod.set_keep_all_override(True)
        assert compact_mod._KEEP_ALL_COMPACTIONS_OVERRIDE is True
        compact_mod.set_keep_all_override(False)
        assert compact_mod._KEEP_ALL_COMPACTIONS_OVERRIDE is False

    def test_reset_compaction_metrics(self):
        from agentnodes.kernel.agent import compact as compact_mod
        compact_mod._compaction_metrics["total_hidden"] = 100
        compact_mod._compaction_metrics["total_kept"] = 10
        compact_mod._compaction_metrics["l4_aborts"] = 5
        compact_mod._compaction_metrics["filter_calls"] = 20
        compact_mod.reset_compaction_metrics()
        assert compact_mod._compaction_metrics["total_hidden"] == 0
        assert compact_mod._compaction_metrics["total_kept"] == 0
        assert compact_mod._compaction_metrics["l4_aborts"] == 0
        assert compact_mod._compaction_metrics["filter_calls"] == 0


# ── Integration: filter updates metrics ──────────────────────────────


class TestMetricsIntegration:
    def test_filter_increments_metrics(self):
        """_convert_messages_to_history should increment filter_calls."""
        from agentnodes.interfaces.api.session.models import Message
        from agentnodes.interfaces.api.session.service import SessionService
        from agentnodes.kernel.agent import compact as compact_mod

        compact_mod.reset_compaction_metrics()
        messages = [
            Message(message_id=f"m{i}", session_id="s1", role="user",
                   content=f"m{i}", message_type="user")
            for i in range(5)
        ]
        # Add 3 compactions
        for i in range(3):
            messages.insert(
                i * 2 + 1,
                Message(message_id=f"c{i}", session_id="s1", role="user",
                       content=f"s{i}", message_type="compaction"),
            )
        # Don't include the last message (current turn)
        SessionService._convert_messages_to_history(messages[:-1])
        # Verify metrics incremented
        assert compact_mod._compaction_metrics["filter_calls"] == 1
        # 3 compactions: 1 kept, 2 hidden
        assert compact_mod._compaction_metrics["total_kept"] == 1
        assert compact_mod._compaction_metrics["total_hidden"] == 2
