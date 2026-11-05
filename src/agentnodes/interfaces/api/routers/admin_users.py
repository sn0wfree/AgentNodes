"""Superuser (admin) user-management API.

These endpoints let an admin manage user accounts: list, create, update
role / display_name / is_active, reset password, and view a user's data.

Authorization: an admin may authenticate via either
  - the ``X-Admin-Token`` header (AN_ADMIN_TOKEN env), OR
  - a valid login token whose account has ``role == 'admin'``.

This is intentionally separate from the ops-only ``admin.py`` (compaction
toggles / metrics) so user management can be driven by a real superuser
account rather than only a shared static token.
"""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel

from .admin import _get_admin_token, _record_audit
from .auth import _get_user_db, _hash_password, _user_public

router = APIRouter()


# ── Admin authorization ───────────────────────────────────────────────


def require_admin(
    request: Request,
    x_admin_token: str | None = Header(None),
) -> str:
    """Require superuser authorization; return the acting admin user_id.

    Accepts either the static ``X-Admin-Token`` (returns ``"__token__"``)
    or a login token from an account with ``role == 'admin'`` (returns the
    account id). Raises 401 / 403 otherwise.
    """
    # 1) Static admin token (ops style).
    expected = _get_admin_token()
    if expected and x_admin_token and hmac.compare_digest(x_admin_token, expected):
        return "__token__"

    # 2) Role-based: an authenticated admin account.
    user_id = getattr(request.state, "user_id", None)
    if not user_id or user_id == "anonymous":
        raise HTTPException(status_code=401, detail="Admin authorization required")
    db = _get_user_db()
    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Account not found")
    if not bool(user.get("is_active", 1)):
        raise HTTPException(status_code=403, detail="Account is disabled")
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Requires admin role")
    return user_id


def _target_user(user_id: str, acting: str) -> dict:
    """Load a target user by id, 404 if missing."""
    db = _get_user_db()
    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


# ── Schemas ───────────────────────────────────────────────────────────


class AdminCreateUser(BaseModel):
    username: str
    password: str
    display_name: str | None = None
    role: str = "user"


class AdminUpdateUser(BaseModel):
    role: str | None = None
    display_name: str | None = None
    is_active: bool | None = None


class AdminResetPassword(BaseModel):
    new_password: str


# ── Endpoints ─────────────────────────────────────────────────────────


@router.get("/users")
async def list_users(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    admin: str = Depends(require_admin),
):
    """List users (paginated)."""
    db = _get_user_db()
    users = [
        _user_public(u) for u in db.list_users(limit=limit, offset=offset)
    ]
    return {
        "users": users,
        "total": db.count_users(),
        "limit": limit,
        "offset": offset,
    }


@router.post("/users", status_code=201)
async def create_user(body: AdminCreateUser, admin: str = Depends(require_admin)):
    """Create a new user (optionally as admin)."""
    db = _get_user_db()
    if db.get_user_by_username(body.username):
        raise HTTPException(status_code=409, detail="Username already exists")
    role = body.role if body.role in ("user", "admin") else "user"
    user = db.create_user(
        body.username,
        body.display_name or body.username,
        _hash_password(body.password),
        role=role,
    )
    _record_audit("admin.create_user", {"username": body.username, "by": admin})
    return _user_public(user)


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str,
    body: AdminUpdateUser,
    request: Request,
    admin: str = Depends(require_admin),
):
    """Update a user's role / display_name / is_active.

    Self-protection: an admin cannot disable or de-role their own account.
    """
    db = _get_user_db()
    _target_user(user_id, admin)  # validates existence / not self

    # Self-protection: never allow disabling or demoting the acting admin.
    if user_id == admin and admin != "__token__":
        if body.is_active is False or (body.role is not None and body.role != "admin"):
            raise HTTPException(
                status_code=400,
                detail="You cannot disable or demote your own account",
            )

    updated = db.update_user(
        user_id,
        role=body.role if body.role in ("user", "admin") else None,
        display_name=body.display_name,
        is_active=1 if body.is_active else (0 if body.is_active is False else None),
    )
    _record_audit(
        "admin.update_user",
        {"user_id": user_id, "by": admin, "fields": body.model_dump(exclude_none=True)},
    )
    return _user_public(updated)


@router.post("/users/{user_id}/reset-password")
async def reset_password(
    user_id: str,
    body: AdminResetPassword,
    admin: str = Depends(require_admin),
):
    """Reset a user's password."""
    db = _get_user_db()
    _target_user(user_id, admin)
    db.update_password(user_id, _hash_password(body.new_password))
    _record_audit("admin.reset_password", {"user_id": user_id, "by": admin})
    return {"message": "Password reset"}


@router.post("/users/{user_id}/disable")
async def disable_user(user_id: str, admin: str = Depends(require_admin)):
    """Disable a user account (immediate logout via middleware)."""
    if user_id == admin and admin != "__token__":
        raise HTTPException(status_code=400, detail="You cannot disable your own account")
    db = _get_user_db()
    _target_user(user_id, admin)
    db.update_user(user_id, is_active=0)
    _record_audit("admin.disable_user", {"user_id": user_id, "by": admin})
    return {"message": "User disabled"}


@router.post("/users/{user_id}/enable")
async def enable_user(user_id: str, admin: str = Depends(require_admin)):
    """Re-enable a disabled user account."""
    db = _get_user_db()
    _target_user(user_id, admin)
    db.update_user(user_id, is_active=1)
    _record_audit("admin.enable_user", {"user_id": user_id, "by": admin})
    return {"message": "User enabled"}


@router.get("/users/{user_id}/data")
async def user_data(user_id: str, admin: str = Depends(require_admin)):
    """View a user's ticket/assignment/session counts (audit view).

    Returns aggregate counts scoped to the user rather than raw rows.
    2026-11-05 批次 3：``studies`` 计数随 apps/study 移除，改为
    ``tickets`` / ``assignments``（L3 表仍在，审计视图仍然有用）。
    """
    _target_user(user_id, admin)
    import sqlite3

    from agentnodes.interfaces.api.session.web_session_db import _get_db_path
    from agentnodes.kernel.orchestration.ticket_store import (
        default_db_path as _ticket_db_path,
    )

    data: dict[str, Any] = {"user_id": user_id}
    try:
        conn = sqlite3.connect(str(_get_db_path()))
        try:
            data["sessions"] = conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE user_id = ?", (user_id,)
            ).fetchone()[0]
        finally:
            conn.close()
    except Exception:
        data["sessions"] = None
    try:
        conn = sqlite3.connect(str(_ticket_db_path()))
        try:
            data["tickets"] = conn.execute(
                "SELECT COUNT(*) FROM tickets WHERE origin_session_key IN "
                "(SELECT id FROM sessions WHERE user_id = ?)",
                (user_id,),
            ).fetchone()[0]
            data["assignments"] = conn.execute(
                "SELECT COUNT(*) FROM assignments WHERE worker_session_id IN "
                "(SELECT id FROM sessions WHERE user_id = ?)",
                (user_id,),
            ).fetchone()[0]
        finally:
            conn.close()
    except Exception:
        data["tickets"] = None
        data["assignments"] = None
    return data
