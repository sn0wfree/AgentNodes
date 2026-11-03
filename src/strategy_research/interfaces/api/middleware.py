"""JWT authentication middleware for FastAPI."""

from __future__ import annotations

from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class AuthMiddleware(BaseHTTPMiddleware):
    """JWT authentication middleware.

    Checks Authorization header or query parameter for token.
    Skips health check and docs endpoints.
    """

    SKIP_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}
    PUBLIC_PREFIXES = [
        "/api/auth/",
        # /api/chat/ removed from public prefixes (P5-5①): chat carries
        # private sessions AND the permission-respond gate — anonymous
        # access let anyone read/export/delete any anonymous session and
        # answer permission prompts (poisoning the process-wide ruleset
        # with permanent rules). The SPA client already sends the bearer
        # token on every call and 401-redirects to /login; the login
        # page only calls /api/auth/* (still public).
        # /api/study/ removed from public prefixes: study task endpoints
        # require authentication + session ownership like chat (see
        # _fetch_session_owned in routers/web_session.py). The
        # ``/api/study/_internal/*`` ops namespace is exempt: it is
        # gated by ``X-Admin-Token`` and session ownership is
        # irrelevant for the operator dump.
        "/api/study/_internal/",
        "/api/system/",
        # Agent schemas are read-only prompt-derived metadata (no user
        # data) — same exposure class as /api/system/*.
        "/api/agents/",
        "/api/admin/",  # Admin endpoints use X-Admin-Token header
        "/assets/",
    ]

    async def dispatch(self, request: Request, call_next):
        """Authenticate API requests; public paths pass through.

        Security-relevant order (keep it):
        1. health/docs always public.
        2. Static-file extension skip applies ONLY to non-``/api/``
           paths — gating it to API paths would let any path-param
           route be reached without auth by suffixing ``.json`` etc.
        3. ``PUBLIC_PREFIXES`` pass through, tagging
           ``request.state.user_id`` (token-derived, else "anonymous").
           Note: ``/api/system/*`` mutations still require a token.
        4. Any other non-API path is SPA/static → public.
        5. Everything else: a valid signed token is required.
        """
        path = request.url.path

        # Skip auth for health/docs
        if path in self.SKIP_PATHS or path.startswith("/docs"):
            return await call_next(request)

        # Non-API paths: SPA static files are public. Static files have
        # extensions like .js, .css, .png, .svg, .ico, .json, .html, etc.
        # IMPORTANT: this must be gated to non-API paths — otherwise any
        # protected route with a trailing {param} path segment could be
        # hit without auth by suffixing an extension (e.g.
        # /api/hypothesis/{id}.json).
        if not path.startswith("/api/") and any(
            path.endswith(ext) for ext in [
                ".js", ".css", ".png", ".jpg", ".jpeg", ".svg", ".ico",
                ".json", ".html", ".woff", ".woff2", ".ttf", ".eot", ".map",
            ]
        ):
            return await call_next(request)

        # Skip auth for public endpoints
        for prefix in self.PUBLIC_PREFIXES:
            if path.startswith(prefix):
                # Mutating /api/system/* (e.g. PUT /llm writes API keys)
                # requires a valid token even though GET reads are public.
                if prefix == "/api/system/" and request.method not in ("GET", "HEAD"):
                    break
                # For session/goal endpoints, extract user from token if present
                user_id = self._extract_user_id(request)
                request.state.user_id = user_id or "anonymous"
                return await call_next(request)

        # Root path "/" serves the SPA index.html — skip auth
        if path == "/" or (not path.startswith("/api/") and not path.startswith("/docs")):
            return await call_next(request)

        # Extract token from header or query
        token = self._extract_token(request)
        if not token:
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing authentication token"},
            )

        # Verify token
        user_id = self._verify_token(token)
        if not user_id:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or expired token"},
            )

        # Disabled accounts are rejected at every request.
        if not self._is_active(user_id):
            return JSONResponse(
                status_code=403,
                content={"detail": "Account is disabled"},
            )

        request.state.user_id = user_id
        response = await call_next(request)
        return response

    def _extract_token(self, request: Request) -> Optional[str]:
        """Extract token from Authorization header or query parameter."""
        # Check Authorization header
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:]

        # Check query parameter (for SSE)
        return request.query_params.get("token")

    def _extract_user_id(self, request: Request) -> Optional[str]:
        """Extract user_id from token if present."""
        token = self._extract_token(request)
        if not token:
            return None
        return self._verify_token(token)

    def _verify_token(self, token: str) -> Optional[str]:
        """Verify signed token and return user_id, or None."""
        from .auth_tokens import verify_token
        return verify_token(token)

    def _is_active(self, user_id: str) -> bool:
        """Return False if the user exists but is disabled.

        DB errors fail CLOSED (return False → 403): the disabled-account
        check must not be bypassable by a database fault. Availability
        recovers when the DB does; silently treating an unreadable user
        table as "active" would let disabled accounts keep operating.
        """
        try:
            from .user_db import get_user_db
            db = get_user_db()
            user = db.get_user_by_id(user_id)
            if user is not None:
                return bool(user.get("is_active", 1))
            return True  # unknown user id → let downstream 401/404
        except Exception:
            # Fail closed, and log loudly so an outage is diagnosable
            # (the old comment said "never fail-open" while returning
            # True — exactly the opposite).
            import logging
            logging.getLogger(__name__).warning(
                "auth: user lookup failed for %s — failing closed", user_id,
                exc_info=True,
            )
            return False
