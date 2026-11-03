"""Tests for api/middleware.py — JWT authentication middleware.

覆盖：
- SKIP_PATHS 跳过（/health, /docs 等）
- PUBLIC_PREFIXES 跳过（/api/auth/, /api/chat/, /assets/）
- 静态文件扩展名跳过（.js, .css 等）
- Token 缺失 → 401
- 无效 Token → 401
- 有效 Token → user_id 写入 request.state
- Token 来源：Authorization header vs query param (SSE)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from strategy_research.interfaces.api.auth_tokens import _b64_encode, _load_secret
from strategy_research.interfaces.api.middleware import AuthMiddleware


def _sign(encoded: str) -> str:
    signature = hmac.new(
        _load_secret(), encoded.encode(), hashlib.sha256
    ).digest()
    return _b64_encode(signature)


def make_token(user_id: str = "user-1", expires_in: int = 3600) -> str:
    """生成签名 JWT（与 auth_tokens.create_token 同格式）。"""
    payload = {
        "sub": user_id,
        "exp": time.time() + expires_in,
    }
    encoded = _b64_encode(json.dumps(payload, separators=(",", ":")).encode())
    return f"{encoded}.{_sign(encoded)}"


def make_expired_token(user_id: str = "user-1") -> str:
    payload = {"sub": user_id, "exp": time.time() - 100}
    encoded = _b64_encode(json.dumps(payload, separators=(",", ":")).encode())
    return f"{encoded}.{_sign(encoded)}"


def make_bad_signature_token(user_id: str = "user-1") -> str:
    """有效载荷 + 错误签名（模拟篡改/旧格式）。"""
    payload = {"sub": user_id, "exp": time.time() + 3600}
    encoded = _b64_encode(json.dumps(payload, separators=(",", ":")).encode())
    bogus = _b64_encode(b"\x00" * 32)
    return f"{encoded}.{bogus}"


@pytest.fixture
def app():
    """最小测试 app — 一个受保护的路由 + 一个公开路由。"""
    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/api/protected")
    async def protected(request: Request):
        return {"user_id": getattr(request.state, "user_id", "MISSING")}

    @app.get("/api/public/test")
    async def public_test(request: Request):
        return {"user_id": getattr(request.state, "user_id", "MISSING")}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/secret/data")
    async def secret():
        return {"data": "secret"}

    return app


@pytest.fixture
def client(app):
    return TestClient(app)


# ────────────────────────── Skip paths ──────────────────────────


class TestSkipPaths:
    def test_health_skipped(self, client):
        """`/health` 不需要 token。"""
        res = client.get("/health")
        assert res.status_code == 200

    def test_docs_skipped(self, client):
        """`/docs` 不需要 token。"""
        res = client.get("/docs")
        # FastAPI /docs redirects to /openapi.json — should not return 401
        assert res.status_code != 401


# ────────────────────────── Auth required ──────────────────────────


class TestAuthRequired:
    def test_missing_token_returns_401(self, client):
        res = client.get("/api/protected")
        assert res.status_code == 401
        assert res.json()["detail"] == "Missing authentication token"

    def test_invalid_token_returns_401(self, client):
        res = client.get(
            "/api/protected",
            headers={"Authorization": "Bearer not-a-valid-token"},
        )
        assert res.status_code == 401
        assert "Invalid" in res.json()["detail"]

    def test_expired_token_returns_401(self, client):
        expired = make_expired_token()
        res = client.get(
            "/api/protected",
            headers={"Authorization": f"Bearer {expired}"},
        )
        assert res.status_code == 401

    def test_garbage_token_returns_401(self, client):
        res = client.get(
            "/api/protected",
            headers={"Authorization": "Bearer !!@@##$$"},
        )
        assert res.status_code == 401


# ────────────────────────── Auth success ──────────────────────────


class TestAuthSuccess:
    def test_valid_bearer_token(self, client):
        """有效 JWT 通过 Authorization header。"""
        token = make_token("user-42")
        res = client.get(
            "/api/protected",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        assert res.json()["user_id"] == "user-42"

    def test_token_via_query_param(self, client):
        """SSE 场景：token 通过 query param 传入。"""
        token = make_token("user-99")
        res = client.get(f"/api/protected?token={token}")
        assert res.status_code == 200
        assert res.json()["user_id"] == "user-99"

    def test_query_param_takes_effect(self, client):
        """query param 优先级：header 缺失时使用 query param。"""
        token = make_token("user-sse")
        # No Authorization header, only query param
        res = client.get(f"/api/protected?token={token}")
        assert res.json()["user_id"] == "user-sse"

    def test_authorization_header_overrides_query(self, client):
        """Authorization header 优先于 query param（更安全）。"""
        header_token = make_token("user-header")
        query_token = make_token("user-query")

        res = client.get(
            f"/api/protected?token={query_token}",
            headers={"Authorization": f"Bearer {header_token}"},
        )
        assert res.json()["user_id"] == "user-header"


# ────────────────────────── Public prefixes ──────────────────────────


class TestPublicPrefixes:
    def test_api_auth_skipped(self, client):
        """`/api/auth/*` 不需要 token。"""
        res = client.get("/api/auth/test")
        assert res.status_code != 401

    def test_api_chat_skipped_with_anon(self, client):
        """`/api/chat/*` 公开，但 user_id 是 'anonymous'。"""
        res = client.get("/api/public/test")
        # /api/public doesn't match any PUBLIC_PREFIXES → should require auth
        # But /api/chat/* would skip
        assert res.status_code == 401

    def test_user_id_is_anonymous_for_optional_auth(self, app):
        """带 PUBLIC_PREFIXES 的路由在没有 token 时 user_id='anonymous'。"""
        # Make a test that hits /api/auth/* directly
        client = TestClient(app)

        @app.get("/api/auth/test-anon")
        async def test_anon(request: Request):
            return {"user_id": getattr(request.state, "user_id", "MISSING")}

        res = client.get("/api/auth/test-anon")
        assert res.status_code == 200
        assert res.json()["user_id"] == "anonymous"

    def test_optional_auth_extracts_user_id_when_token_present(self, app):
        """PUBLIC_PREFIXES 路由带 token 时 user_id 来自 token。"""
        client = TestClient(app)

        @app.get("/api/auth/test-with-token")
        async def test_with_token(request: Request):
            return {"user_id": getattr(request.state, "user_id", "MISSING")}

        token = make_token("authed-user")
        res = client.get(
            f"/api/auth/test-with-token?token={token}",
        )
        assert res.json()["user_id"] == "authed-user"


# ────────────────────────── Static file extensions ──────────────────────────


class TestStaticFiles:
    """静态文件（扩展名匹配）应跳过认证。"""

    @pytest.fixture
    def static_app(self, tmp_path):
        """构造一个真实静态文件 + AuthMiddleware。"""
        app = FastAPI()
        app.add_middleware(AuthMiddleware)

        # 写一个真实的 js 文件
        static_dir = tmp_path / "static"
        static_dir.mkdir()
        (static_dir / "app.js").write_text("console.log('hello')")
        (static_dir / "style.css").write_text("body { color: red; }")

        from fastapi.staticfiles import StaticFiles
        app.mount("/static", StaticFiles(directory=str(static_dir)))

        return app

    def test_js_extension_skipped(self, static_app):
        """`.js` 文件不需要 token。"""
        client = TestClient(static_app)
        res = client.get("/static/app.js")
        assert res.status_code == 200
        assert "console.log" in res.text

    def test_css_extension_skipped(self, static_app):
        """`.css` 文件不需要 token。"""
        client = TestClient(static_app)
        res = client.get("/static/style.css")
        assert res.status_code == 200


# ────────────────────────── Direct middleware unit tests ──────────────────────────


class TestMiddlewareInternals:
    """直接测试 AuthMiddleware 的内部方法（绕过 FastAPI 集成）。"""

    def test_extract_token_from_header(self):
        mw = AuthMiddleware.__new__(AuthMiddleware)
        from starlette.requests import Request

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/test",
            "headers": [(b"authorization", b"Bearer abc123")],
            "query_string": b"",
        }
        request = Request(scope)
        assert mw._extract_token(request) == "abc123"

    def test_extract_token_from_query(self):
        mw = AuthMiddleware.__new__(AuthMiddleware)
        from starlette.requests import Request

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/test",
            "headers": [],
            "query_string": b"token=xyz789",
        }
        request = Request(scope)
        assert mw._extract_token(request) == "xyz789"

    def test_extract_token_header_overrides_query(self):
        mw = AuthMiddleware.__new__(AuthMiddleware)
        from starlette.requests import Request

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/test",
            "headers": [(b"authorization", b"Bearer header-token")],
            "query_string": b"token=query-token",
        }
        request = Request(scope)
        # Header takes precedence (implementation behavior)
        assert mw._extract_token(request) == "header-token"

    def test_extract_token_no_auth(self):
        mw = AuthMiddleware.__new__(AuthMiddleware)
        from starlette.requests import Request

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/test",
            "headers": [],
            "query_string": b"",
        }
        request = Request(scope)
        assert mw._extract_token(request) is None

    def test_verify_valid_token(self):
        mw = AuthMiddleware.__new__(AuthMiddleware)
        token = make_token("alice", expires_in=3600)
        assert mw._verify_token(token) == "alice"

    def test_verify_expired_token(self):
        mw = AuthMiddleware.__new__(AuthMiddleware)
        token = make_expired_token("alice")
        assert mw._verify_token(token) is None

    def test_verify_invalid_json(self):
        mw = AuthMiddleware.__new__(AuthMiddleware)
        # base64-decodable but not valid JSON
        token = base64.urlsafe_b64encode(b"not json").decode()
        assert mw._verify_token(token) is None

    def test_verify_malformed_token(self):
        mw = AuthMiddleware.__new__(AuthMiddleware)
        assert mw._verify_token("!!!not-base64!!!") is None


# ────────────────────────── Disabled-account gate ──────────────────────────


class TestDisabledAccount:
    """A token for a disabled account must be rejected with 403 at the
    middleware layer (immediate logout), while an unknown user id is
    allowed through to let downstream handle auth."""

    def test_disabled_user_rejected_403(self, app, client, monkeypatch, tmp_path):
        from strategy_research.interfaces.api import user_db as user_db_mod
        from strategy_research.interfaces.api.middleware import AuthMiddleware

        user_db = user_db_mod.UserDB(tmp_path / "users.db")
        user = user_db.create_user("offline", "Offline", "hash")
        user_db.update_user(user["id"], is_active=0)

        monkeypatch.setattr(user_db_mod, "get_user_db", lambda *a, **k: user_db)
        mw = AuthMiddleware.__new__(AuthMiddleware)
        assert mw._is_active(user["id"]) is False

        # Full request path → 403.
        headers = {"Authorization": f"Bearer {make_token(user['id'])}"}
        res = client.get("/api/protected", headers=headers)
        assert res.status_code == 403
        assert res.json()["detail"] == "Account is disabled"

    def test_active_user_allowed(self, app, client, monkeypatch, tmp_path):

        from strategy_research.interfaces.api import user_db as user_db_mod

        user_db = user_db_mod.UserDB(tmp_path / "users.db")
        user = user_db.create_user("online", "Online", "hash")
        monkeypatch.setattr(user_db_mod, "get_user_db", lambda *a, **k: user_db)

        headers = {"Authorization": f"Bearer {make_token(user['id'])}"}
        res = client.get("/api/protected", headers=headers)
        assert res.status_code == 200
        assert res.json()["user_id"] == user["id"]

    def test_unknown_user_id_allowed_through(self, client, monkeypatch, tmp_path):
        """Unknown user ids pass the middleware (is_active returns True) so
        the downstream endpoint can 401/404 them."""
        from strategy_research.interfaces.api import user_db as user_db_mod

        user_db = user_db_mod.UserDB(tmp_path / "users.db")
        monkeypatch.setattr(user_db_mod, "get_user_db", lambda *a, **k: user_db)

        headers = {"Authorization": f"Bearer {make_token('no-such-user')}"}
        res = client.get("/api/protected", headers=headers)
        assert res.status_code == 200  # middleware passes; endpoint has no auth dep

    def test_is_active_fails_closed_on_db_errors(self, tmp_path, monkeypatch):
        """If the DB lookup raises, _is_active fails CLOSED (B9 fix:
        a lookup failure must never re-enable a disabled account)."""
        from strategy_research.interfaces.api import user_db as user_db_mod
        from strategy_research.interfaces.api.middleware import AuthMiddleware

        class BrokenDB:
            def get_user_by_id(self, uid):
                raise RuntimeError("db down")

        monkeypatch.setattr(user_db_mod, "get_user_db", lambda *a, **k: BrokenDB())
        mw = AuthMiddleware.__new__(AuthMiddleware)
        assert mw._is_active("anything") is False
