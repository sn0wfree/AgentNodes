"""Tests for API router: GET /api/search/minimax endpoints."""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import httpx
import pytest

from agentnodes.interfaces.api.auth_tokens import create_token


def _bearer(user_id: str = "tester") -> dict:
    return {"Authorization": f"Bearer {create_token(user_id)}"}


def _build_app():
    from fastapi import FastAPI
    from agentnodes.interfaces.api.middleware import AuthMiddleware
    from agentnodes.interfaces.api.routers import search as search_router
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(search_router.router)
    return app


@pytest.mark.asyncio
async def test_minimax_health_returns_configured_false(monkeypatch):
    for k in ("MINIMAX_CODE_PLAN_KEY", "MINIMAX_CODING_API_KEY",
              "MINIMAX_OAUTH_TOKEN", "MINIMAX_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    app = _build_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        r = await client.get("/api/search/minimax/health")
        assert r.status_code == 200
        assert r.json()["configured"] is False


@pytest.mark.asyncio
async def test_minimax_health_returns_configured_true(monkeypatch):
    monkeypatch.setenv("MINIMAX_CODE_PLAN_KEY", "test-key")
    app = _build_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        r = await client.get("/api/search/minimax/health")
        assert r.status_code == 200
        assert r.json()["configured"] is True


@pytest.mark.asyncio
async def test_minimax_search_returns_503_when_not_configured(monkeypatch):
    for k in ("MINIMAX_CODE_PLAN_KEY", "MINIMAX_CODING_API_KEY",
              "MINIMAX_OAUTH_TOKEN", "MINIMAX_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    app = _build_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        r = await client.get("/api/search/minimax?q=python")
        assert r.status_code == 503


@pytest.mark.asyncio
async def test_minimax_search_missing_query_returns_422():
    app = _build_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        r = await client.get("/api/search/minimax")
        assert r.status_code == 422


@pytest.mark.asyncio
async def test_minimax_search_success(monkeypatch):
    monkeypatch.setenv("MINIMAX_CODE_PLAN_KEY", "test-key")
    mock_data = {
        "results": [
            {"title": "Python Tutorial", "url": "https://python.org", "snippet": "Learn Python"},
        ],
        "related_queries": ["python basics"],
    }
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(mock_data).encode()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        app = _build_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers=_bearer(),
        ) as client:
            r = await client.get("/api/search/minimax?q=python+tutorial&count=3")
            assert r.status_code == 200
            data = r.json()
            assert data["status"] == "ok"
            assert data["n_results"] == 1
            assert data["results"][0]["title"] == "Python Tutorial"


@pytest.mark.asyncio
async def test_minimax_search_http_error_returns_502(monkeypatch):
    monkeypatch.setenv("MINIMAX_CODE_PLAN_KEY", "test-key")
    import urllib.error
    exc = urllib.error.HTTPError(
        url="https://api.minimaxi.com/v1/coding_plan/search",
        code=401, msg="Unauthorized", hdrs={}, fp=MagicMock(),
    )
    exc.read = MagicMock(return_value=b'{"error":"auth failed"}')

    with patch("urllib.request.urlopen", side_effect=exc):
        app = _build_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers=_bearer(),
        ) as client:
            r = await client.get("/api/search/minimax?q=auth-test")
            assert r.status_code == 502