"""Tests for profile-aware /api/system/llm GET/PUT and check_llm_config.

Covers:
    - GET returns the effective (profile-resolved) configuration
    - GET provider catalogue includes adapter + profile providers
    - PUT switches active_profile and upserts the profile
    - PUT auto-migrates legacy top-level config into profiles
    - PUT never writes masked (••••) API keys back
    - PUT writes new keys to .env under <PROVIDER>_API_KEY
    - check_llm_config resolves provider/model through profiles
"""

from __future__ import annotations

import json
import stat
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from strategy_research.foundation.llm.config import ENV_PROFILE_ACTIVE

PROFILE_JSON = {
    "llm": {
        "active_profile": "minimax",
        "timeout": 300,
        "profiles": {
            "minimax": {
                "provider": "minimax",
                "model": "minimax-M3",
                "api_key": "env:MINIMAX_API_KEY",
                "base_url": "https://api.minimaxi.com/v1",
            },
            "nvidia": {
                "provider": "nvidia",
                "model": "z-ai/glm-5.2",
                "api_key": "env:NVIDIA_API_KEY",
                "base_url": "https://integrate.api.nvidia.com/v1",
            },
        },
    }
}

LEGACY_JSON = {
    "llm": {
        "provider": "minimax",
        "model": "minimax-M3",
        "api_key": "env:MINIMAX_API_KEY",
        "base_url": "https://api.minimaxi.com/v1",
        "timeout": 300,
    }
}


@pytest.fixture
def client():
    from strategy_research.interfaces.api.app import create_app

    app = create_app(workspace_path=None)
    return TestClient(app)


@pytest.fixture
def auth_headers(client):
    """Login as the seeded admin and return signed Authorization headers.

    PUT /api/system/llm and other mutating system endpoints require a
    valid (HMAC-signed) token.
    """
    resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin"},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch):
    """Point system router paths at tmp_path + freeze dotenv loading."""
    llm_json = tmp_path / "llm.json"
    dotenv = tmp_path / ".env"
    monkeypatch.setattr(
        "strategy_research.interfaces.api.routers.system._LLM_JSON_PATH", llm_json
    )
    monkeypatch.setattr(
        "strategy_research.interfaces.api.routers.system._DOTENV_PATH", dotenv
    )
    monkeypatch.setattr(
        "strategy_research.foundation.llm.config._resolve_bridge_path",
        lambda env: llm_json,
    )
    monkeypatch.setattr(
        "strategy_research.foundation.llm.config._try_load_dotenv", lambda: None
    )
    for k in ("OPENAI_API_KEY", "LLM_API_KEY", "MINIMAX_API_KEY",
              "NVIDIA_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL",
              "LLM_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    return llm_json, dotenv


# ── GET /api/system/llm ─────────────────────────────────────────────


class TestGetLLM:
    def test_returns_effective_profile_config(self, client, isolated):
        llm_json, _ = isolated
        llm_json.write_text(json.dumps(PROFILE_JSON))
        with patch.dict("os.environ", {"MINIMAX_API_KEY": "sk-minimax-real"}):
            resp = client.get("/api/system/llm")
        assert resp.status_code == 200
        body = resp.json()
        assert body["provider"] == "minimax"
        assert body["model"] == "minimax-M3"
        assert body["base_url"] == "https://api.minimaxi.com/v1"
        assert body["api_key_masked"] is True
        assert body["api_key"] == "sk-m••••••••real"
        assert body["key_var"] == "MINIMAX_API_KEY"
        assert body["active_profile"] == "minimax"
        assert body["profiles"] == ["minimax", "nvidia"]

    def test_catalog_includes_adapter_and_key_state(self, client, isolated):
        llm_json, dotenv = isolated
        llm_json.write_text(json.dumps(PROFILE_JSON))
        dotenv.write_text("NVIDIA_API_KEY=nvapi-abc123\n", encoding="utf-8")
        resp = client.get("/api/system/llm")
        assert resp.status_code == 200
        providers = {p["name"]: p for p in resp.json()["providers"]}
        assert "nvidia" in providers
        nv = providers["nvidia"]
        assert nv["label"] == "NVIDIA NIM"
        assert nv["model"] == "z-ai/glm-5.2"
        assert nv["key_var"] == "NVIDIA_API_KEY"
        assert nv["key_configured"] is True
        assert providers["minimax"]["key_configured"] is False

    def test_legacy_config_without_profiles(self, client, isolated):
        llm_json, _ = isolated
        llm_json.write_text(json.dumps(LEGACY_JSON))
        with patch.dict("os.environ", {"MINIMAX_API_KEY": "sk-minimax-real"}):
            resp = client.get("/api/system/llm")
        assert resp.status_code == 200
        body = resp.json()
        assert body["provider"] == "minimax"
        assert body["model"] == "minimax-M3"
        assert body["active_profile"] == ""

    def test_no_config_returns_empty(self, client, isolated):
        resp = client.get("/api/system/llm")
        assert resp.status_code == 200
        body = resp.json()
        assert body["provider"] == ""
        assert body["api_key"] == ""
        assert body["api_key_masked"] is False


# ── PUT /api/system/llm ─────────────────────────────────────────────


class TestPutLLM:
    def test_switches_profile_and_upserts(self, client, auth_headers, isolated):
        llm_json, _ = isolated
        llm_json.write_text(json.dumps(PROFILE_JSON))
        resp = client.put("/api/system/llm", headers=auth_headers, json={
            "provider": "nvidia",
            "model": "deepseek-ai/deepseek-v4-flash",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["active_profile"] == "nvidia"
        data = json.loads(llm_json.read_text())
        assert data["llm"]["active_profile"] == "nvidia"
        profile = data["llm"]["profiles"]["nvidia"]
        assert profile["model"] == "deepseek-ai/deepseek-v4-flash"
        assert profile["base_url"] == "https://integrate.api.nvidia.com/v1"
        assert data["llm"]["timeout"] == 300  # defaults preserved

    def test_writes_new_key_to_dotenv(self, client, auth_headers, isolated):
        llm_json, dotenv = isolated
        llm_json.write_text(json.dumps(PROFILE_JSON))
        resp = client.put("/api/system/llm", headers=auth_headers, json={
            "provider": "nvidia",
            "model": "z-ai/glm-5.2",
            "api_key": "nvapi-new-key-123",
        })
        assert resp.status_code == 200
        content = dotenv.read_text(encoding="utf-8")
        assert "NVIDIA_API_KEY=nvapi-new-key-123" in content
        data = json.loads(llm_json.read_text())
        assert data["llm"]["profiles"]["nvidia"]["api_key"] == "env:NVIDIA_API_KEY"
        mode = stat.S_IMODE(dotenv.stat().st_mode)
        assert mode == 0o600

    def test_masked_key_never_written(self, client, auth_headers, isolated):
        llm_json, dotenv = isolated
        llm_json.write_text(json.dumps(PROFILE_JSON))
        dotenv.write_text("MINIMAX_API_KEY=sk-original\n", encoding="utf-8")
        resp = client.put("/api/system/llm", headers=auth_headers, json={
            "provider": "minimax",
            "model": "minimax-M3",
            "api_key": "sk-m••••••••real",
        })
        assert resp.status_code == 200
        content = dotenv.read_text(encoding="utf-8")
        assert "sk-original" in content
        assert "••••" not in content

    def test_legacy_config_auto_migrates(self, client, auth_headers, isolated):
        llm_json, dotenv = isolated
        llm_json.write_text(json.dumps(LEGACY_JSON))
        resp = client.put("/api/system/llm", headers=auth_headers, json={
            "provider": "minimax",
            "model": "minimax-M3",
        })
        assert resp.status_code == 200
        data = json.loads(llm_json.read_text())
        llm = data["llm"]
        assert llm["active_profile"] == "minimax"
        assert llm["profiles"]["minimax"]["provider"] == "minimax"
        assert llm["profiles"]["minimax"]["model"] == "minimax-M3"
        assert llm["profiles"]["minimax"]["api_key"] == "env:MINIMAX_API_KEY"
        assert llm["profiles"]["minimax"]["base_url"] == \
            "https://api.minimaxi.com/v1"

    def test_creates_file_from_scratch(self, client, auth_headers, isolated):
        llm_json, _ = isolated
        assert not llm_json.exists()
        resp = client.put("/api/system/llm", headers=auth_headers, json={
            "provider": "nvidia",
            "model": "z-ai/glm-5.2",
            "api_key": "nvapi-fresh",
        })
        assert resp.status_code == 200
        data = json.loads(llm_json.read_text())
        assert data["llm"]["active_profile"] == "nvidia"
        assert data["llm"]["profiles"]["nvidia"]["model"] == "z-ai/glm-5.2"

    def test_preserves_other_top_level_keys(self, client, auth_headers, isolated):
        llm_json, _ = isolated
        data = dict(PROFILE_JSON)
        data["tools"] = ["mcp_tool"]
        llm_json.write_text(json.dumps(data))
        client.put("/api/system/llm", headers=auth_headers, json={
            "provider": "nvidia", "model": "z-ai/glm-5.2",
        })
        saved = json.loads(llm_json.read_text())
        assert saved["tools"] == ["mcp_tool"]

    def test_backs_up_before_write(self, client, auth_headers, isolated):
        llm_json, _ = isolated
        llm_json.write_text(json.dumps(PROFILE_JSON))
        client.put("/api/system/llm", headers=auth_headers, json={
            "provider": "nvidia", "model": "z-ai/glm-5.2",
        })
        backups = sorted(llm_json.parent.glob("llm.json.bak-*"))
        assert len(backups) >= 1
        assert json.loads(backups[-1].read_text())["llm"]["active_profile"] \
            == "minimax"

    def test_provider_required(self, client, auth_headers, isolated):
        llm_json, _ = isolated
        llm_json.write_text(json.dumps(PROFILE_JSON))
        resp = client.put("/api/system/llm", headers=auth_headers, json={"model": "x"})
        assert resp.status_code == 400


# ── check_llm_config (profile-aware) ────────────────────────────────


class TestCheckLLMConfig:
    def test_profiles_only_config_resolves_effective(
        self, tmp_path: Path, monkeypatch
    ):
        from strategy_research.cli import llm_config_check as lcc

        llm_json = tmp_path / "llm.json"
        dotenv = tmp_path / ".env"
        llm_json.write_text(json.dumps(PROFILE_JSON))
        monkeypatch.setattr(lcc, "LLM_JSON_PATH", llm_json)
        monkeypatch.setattr(lcc, "DOTENV_PATH", dotenv)
        monkeypatch.setattr(
            "strategy_research.foundation.llm.config._resolve_bridge_path",
            lambda env: llm_json,
        )
        monkeypatch.setattr(
            "strategy_research.foundation.llm.config._try_load_dotenv", lambda: None
        )
        monkeypatch.setenv("MINIMAX_API_KEY", "sk-minimax-real")
        for k in ("OPENAI_API_KEY", "LLM_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        # Defensive: a prior test may have left LLM_PROFILE set (a
        # known leakage vector — see test_llm_profile_flag_sets_env).
        monkeypatch.delenv(ENV_PROFILE_ACTIVE, raising=False)

        status = lcc.check_llm_config()
        assert status["configured"] is True
        assert status["provider"] == "minimax"
        assert status["model"] == "minimax-M3"

    def test_unconfigured_stays_false(self, tmp_path: Path, monkeypatch):
        from strategy_research.cli import llm_config_check as lcc

        llm_json = tmp_path / "llm.json"
        dotenv = tmp_path / ".env"
        llm_json.write_text(json.dumps({"llm": {}}))
        monkeypatch.setattr(lcc, "LLM_JSON_PATH", llm_json)
        monkeypatch.setattr(lcc, "DOTENV_PATH", dotenv)
        monkeypatch.setattr(
            "strategy_research.foundation.llm.config._resolve_bridge_path",
            lambda env: llm_json,
        )
        monkeypatch.setattr(
            "strategy_research.foundation.llm.config._try_load_dotenv", lambda: None
        )
        for k in ("OPENAI_API_KEY", "LLM_API_KEY", "MINIMAX_API_KEY"):
            monkeypatch.delenv(k, raising=False)

        status = lcc.check_llm_config()
        assert status["configured"] is False
