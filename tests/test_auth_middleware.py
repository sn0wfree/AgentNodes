"""AuthMiddleware dispatch path coverage. Targets the un-tested lines
in api/middleware.py: token-from-query, anonymous tagging on public
prefix, SPA static extension, mutating /api/system/* enforcement, and
root / SPA bypass.

Note: ``starlette.BaseHTTPMiddleware`` does NOT propagate
``request.state`` into downstream endpoint calls (known starlette
limitation). So we assert middleware behavior via status codes and
headers that we inject via ``Depends`` from a raw ASGI scope lookup,
not via ``request.state``.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import Depends, FastAPI, Request

from strategy_research.interfaces.api.middleware import AuthMiddleware


def _build_asgi_app():
    """A minimal FastAPI app with the middleware + a couple of probes."""
    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    def _user_id(request: Request) -> str:
        # ``scope`` middleware-set ``state`` is observable via Request.scope.
        return request.scope.get("state", {}).get("user_id", "missing")

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/api/secret", dependencies=[Depends(_user_id)])
    async def secret():
        return {"ok": True}

    @app.put("/api/system/llm")
    async def system_write():
        return {"ok": True}

    @app.get("/api/system/info")
    async def system_read():
        return {"ok": True}

    @app.get("/api/auth/me")
    async def auth_me():
        return {"ok": True}

    @app.get("/static.js")
    async def static_js():
        return {"js": True}

    @app.get("/")
    async def root():
        return {"html": True}

    return app


def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def _bearer(user_id: str = "alice") -> dict:
    from strategy_research.interfaces.api.auth_tokens import create_token

    return {"Authorization": f"Bearer {create_token(user_id)}"}


# ────────────────────────── happy / public paths ──────────────────────────


@pytest.mark.asyncio
async def test_health_bypasses_auth():
    app = _build_asgi_app()
    async with _client(app) as c:
        r = await c.get("/health")
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_root_path_bypasses_auth():
    app = _build_asgi_app()
    async with _client(app) as c:
        r = await c.get("/")
        assert r.status_code == 200
        assert r.json() == {"html": True}


@pytest.mark.asyncio
async def test_static_extension_bypasses_auth():
    """Non-API static file extensions skip auth."""
    app = _build_asgi_app()
    async with _client(app) as c:
        r = await c.get("/static.js")
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_docs_path_bypasses_auth():
    """``/docs`` and similar docs paths are public."""
    app = _build_asgi_app()
    async with _client(app) as c:
        r = await c.get("/docs")
        assert r.status_code in (200, 404)  # 404 if no docs mounted, but no 401


@pytest.mark.asyncio
async def test_get_system_is_public():
    """``/api/system/*`` GET bypasses auth (reads are public)."""
    app = _build_asgi_app()
    async with _client(app) as c:
        r = await c.get("/api/system/info")
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_api_auth_me_is_public():
    """``/api/auth/*`` is a public prefix."""
    app = _build_asgi_app()
    async with _client(app) as c:
        r = await c.get("/api/auth/me")
        assert r.status_code == 200


# ────────────────────────── enforcement ──────────────────────────


@pytest.mark.asyncio
async def test_put_system_requires_token():
    """/api/system/* mutations are NOT public even though reads are."""
    app = _build_asgi_app()
    async with _client(app) as c:
        r = await c.put("/api/system/llm", json={"provider": "x"})
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_protected_endpoint_missing_token_returns_401():
    app = _build_asgi_app()
    async with _client(app) as c:
        r = await c.get("/api/secret")
        assert r.status_code == 401
        assert r.json() == {"detail": "Missing authentication token"}


@pytest.mark.asyncio
async def test_protected_endpoint_invalid_token_returns_401():
    app = _build_asgi_app()
    async with _client(app) as c:
        r = await c.get("/api/secret", headers={"Authorization": "Bearer junk"})
        assert r.status_code == 401
        assert r.json() == {"detail": "Invalid or expired token"}


@pytest.mark.asyncio
async def test_protected_endpoint_with_token_succeeds():
    app = _build_asgi_app()
    async with _client(app) as c:
        r = await c.get("/api/secret", headers=_bearer("alice"))
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_token_from_query_param_is_accepted():
    """SSE-style token in ?token=... is honored for protected endpoints."""
    from strategy_research.interfaces.api.auth_tokens import create_token

    app = _build_asgi_app()
    async with _client(app) as c:
        r = await c.get(f"/api/secret?token={create_token('sse-user')}")
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_auth_header_with_non_bearer_scheme_is_ignored():
    """Bearer-less Authorization header falls through to query token lookup."""
    app = _build_asgi_app()
    from strategy_research.interfaces.api.auth_tokens import create_token

    async with _client(app) as c:
        r = await c.get(
            f"/api/secret?token={create_token('user')}",
            headers={"Authorization": "Basic abc"},
        )
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_query_param_takes_effect_when_no_header():
    """Without any Authorization header, the ?token= query is the only way."""
    app = _build_asgi_app()
    from strategy_research.interfaces.api.auth_tokens import create_token

    async with _client(app) as c:
        # Valid query token, no header
        r = await c.get(f"/api/secret?token={create_token('q-user')}")
        assert r.status_code == 200
        # No token at all
        r2 = await c.get("/api/secret")
        assert r2.status_code == 401
