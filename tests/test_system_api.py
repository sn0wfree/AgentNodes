"""Tests for the /api/system/info model_info field and refresh endpoint."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from strategy_research.interfaces.api.app import create_app

    app = create_app(workspace_path=None)
    return TestClient(app)


@pytest.fixture
def auth_headers(client):
    """Signed token for the seeded admin (POST /system/* needs auth)."""
    resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_llm_config(provider="minimax", model="minimax-M3", **overrides):
    """Build a mock LLMConfig for patching."""
    from strategy_research.foundation.llm.config import LLMConfig

    # Build directly to avoid the bridge layer
    defaults = dict(
        provider=provider,
        model=model,
        base_url="https://example.com",
        api_key="",
    )
    defaults.update(overrides)
    return LLMConfig(**defaults)


def test_info_includes_model_info_when_llm_configured(client):
    """When LLM is configured, system/info includes model_info."""
    fake_llm = {
        "configured": True,
        "provider": "minimax",
        "model": "minimax-M3",
        "api_key_source": "env",
    }
    cfg = _make_llm_config(provider="minimax", model="minimax-M3")
    with patch(
        "strategy_research.interfaces.cli.llm_config_check.check_llm_config",
        return_value=fake_llm,
    ), patch(
        "strategy_research.foundation.llm.config.LLMConfig.load",
        return_value=cfg,
    ):
        resp = client.get("/api/system/info")
    assert resp.status_code == 200
    body = resp.json()
    assert body["llm"]["provider"] == "minimax"
    assert body["model_info"] is not None
    info = body["model_info"]
    assert info["provider"] == "minimax"
    assert info["context_tokens"] > 0
    assert "source" in info


def test_info_model_info_none_when_unconfigured(client):
    """When LLM is not configured, model_info is null."""
    fake_llm = {
        "configured": False,
        "provider": "unknown",
        "model": "unknown",
        "api_key_source": "unknown",
    }
    cfg = _make_llm_config(provider="auto", model="unknown")
    with patch(
        "strategy_research.interfaces.cli.llm_config_check.check_llm_config",
        return_value=fake_llm,
    ), patch(
        "strategy_research.foundation.llm.config.LLMConfig.load",
        return_value=cfg,
    ):
        resp = client.get("/api/system/info")
    assert resp.status_code == 200
    assert resp.json()["model_info"] is None


def test_info_respects_user_config_override(client):
    """User-configured model_context_tokens appears in model_info."""
    from strategy_research.foundation.llm.config import LLMConfig

    fake_llm = {
        "configured": True,
        "provider": "minimax",
        "model": "minimax-M3",
        "api_key_source": "env",
    }
    cfg = LLMConfig(
        provider="minimax",
        model="minimax-M3",
        model_context_tokens=2_500_000,
    )
    with patch(
        "strategy_research.interfaces.cli.llm_config_check.check_llm_config",
        return_value=fake_llm,
    ), patch(
        "strategy_research.foundation.llm.config.LLMConfig.load",
        return_value=cfg,
    ):
        resp = client.get("/api/system/info")
    assert resp.status_code == 200
    body = resp.json()
    assert body["model_info"]["context_tokens"] == 2_500_000
    # The legacy dict also reflects the override
    assert body["llm"]["model_context_tokens"] == 2_500_000


def test_refresh_endpoint_returns_model_info(client, auth_headers):
    """POST /api/system/model-info/refresh returns ModelInfo."""
    fake_llm = {
        "configured": True,
        "provider": "openai",
        "model": "gpt-4o-mini",
        "api_key_source": "env",
    }
    cfg = _make_llm_config(provider="openai", model="gpt-4o-mini")
    with patch(
        "strategy_research.interfaces.cli.llm_config_check.check_llm_config",
        return_value=fake_llm,
    ), patch(
        "strategy_research.foundation.llm.config.LLMConfig.load",
        return_value=cfg,
    ):
        resp = client.post(
            "/api/system/model-info/refresh",
            headers=auth_headers,
            json={"provider": "openai", "model": "gpt-4o-mini"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "openai"
    assert body["model"] == "gpt-4o-mini"
    assert body["context_tokens"] > 0


def test_refresh_endpoint_uses_current_config_when_no_body(client, auth_headers):
    """POST /api/system/model-info/refresh with empty body uses current LLM."""
    fake_llm = {
        "configured": True,
        "provider": "minimax",
        "model": "minimax-M3",
        "api_key_source": "env",
    }
    cfg = _make_llm_config(provider="minimax", model="minimax-M3")
    with patch(
        "strategy_research.interfaces.cli.llm_config_check.check_llm_config",
        return_value=fake_llm,
    ), patch(
        "strategy_research.foundation.llm.config.LLMConfig.load",
        return_value=cfg,
    ):
        resp = client.post("/api/system/model-info/refresh", headers=auth_headers, json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "minimax"


def test_refresh_endpoint_400_when_no_llm(client, auth_headers):
    """POST /api/system/model-info/refresh without LLM config returns 400."""
    fake_llm = {
        "configured": False,
        "provider": "unknown",
        "model": "unknown",
        "api_key_source": "unknown",
    }
    cfg = _make_llm_config(provider="auto", model="unknown")
    with patch(
        "strategy_research.interfaces.cli.llm_config_check.check_llm_config",
        return_value=fake_llm,
    ), patch(
        "strategy_research.foundation.llm.config.LLMConfig.load",
        return_value=cfg,
    ):
        resp = client.post("/api/system/model-info/refresh", headers=auth_headers, json={})
    assert resp.status_code == 400
