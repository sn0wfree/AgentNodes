# API Test Debt Fix + Auth Semantics Clarification

Date: 2026-08-02
Related: `tests/test_api.py`, `api/app.py`, `api/middleware.py`

## Problem

`tests/test_api.py` had 32 pre-existing failures, in two classes:

1. **401s on goal / hypothesis / validate / session / memory / run routers.**
   These routers sit outside `AuthMiddleware.PUBLIC_PREFIXES`, so they
   require a valid token. The tests never sent one. Baseline behavior
   predates the Phase-1 auth hardening (the tests had already drifted
   before it landed); the hardening simply made the drift visible.
2. **`test_root` / `test_health` JSONDecodeError.** `/` now serves the SPA
   `index.html` (FileResponse), not a JSON envelope.

Plus one real bug uncovered while triaging: **`/health` is shadowed by the
SPA fallback route** (`app.py` registers `@app.get("/{full_path:path}")`
at line ~172, before the `/health` route at line ~203, so `/health` returns
SPA HTML and a JSON client gets a decode error). The middleware already
lists `/health` in `SKIP_PATHS`; the routing order defeated that intent.

## Design

### Auth contract (made explicit)

| Router prefix        | Auth required | Notes                                |
|----------------------|---------------|--------------------------------------|
| `/api/auth/`         | no            | login/register; `change-password`    |
| `/api/chat/`         | no            | anonymous = "anonymous", ownership   |
| `/api/chat/session/` | no            | same as above                        |
| `/api/system/`       | GET public    | PUT/POST require token (writes keys) |
| `/api/admin/`        | no            | X-Admin-Token header                 |
| `/api/goal|hypothesis|validate|session|memory|run/` | **yes** | protected read/write |

Tests must authenticate against the protected routers with a signed token
(`api.auth_tokens.create_token`).

### Fixes

1. **`app.py`**: register `/health` before the SPA catch-all route.
2. **`test_api.py`**:
   - `auth_headers` fixture: `create_token("admin")` → Bearer header.
   - All protected-router tests pass `auth_headers`.
   - `test_root`: assert 200 + `text/html` (SPA served).
   - `test_health`: assert 200 + `{"status": "ok"}` (after routing fix).
   - Add a regression test asserting `/health` returns JSON (not HTML)
     while static serving is active.

## Verification

- `pytest tests/test_api.py -q` → all green.
- Regression: `tests/test_security_hardening.py`, `tests/test_system_llm_api.py`
  still green (auth semantics unchanged for public prefixes).
