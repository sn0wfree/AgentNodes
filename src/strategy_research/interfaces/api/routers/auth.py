"""Authentication API — login / me。

Registration is disabled (backend code kept but not exposed).
Default admin account: admin / admin (seeded on first startup).

SEC-1: password hashing uses PBKDF2-HMAC-SHA256 (260k iterations + salt).
Legacy SHA-256 hashes are auto-upgraded on successful login.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class UserLogin(BaseModel):
    username: str
    password: str


class UserCreate(BaseModel):
    username: str
    password: str
    display_name: str | None = None


class ChangePassword(BaseModel):
    old_password: str
    new_password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


# ── Token helpers (signed HMAC tokens — see api/auth_tokens.py) ──


def _registration_enabled() -> bool:
    """Registration is opt-in (``SR_ALLOW_REGISTRATION=1``); off by default."""
    return os.environ.get("SR_ALLOW_REGISTRATION", "").lower() in ("1", "true", "yes")


# ── SEC-1: password hashing ────────────────────────────────────

# Format: "pbkdf2:<iterations>:<salt_hex>:<hash_hex>"
# Legacy: plain hex (SHA-256, no salt) — auto-upgraded on login.
_PWHASH_RE = re.compile(r"^pbkdf2:(\d+):([0-9a-f]+):([0-9a-f]+)$", re.I)
_DEFAULT_ITERATIONS = 260_000


def _hash_password(password: str) -> str:
    """Hash a password with PBKDF2-HMAC-SHA256 (SEC-1).

    Returns ``pbkdf2:<iterations>:<salt_hex>:<hash_hex>``.
    """
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _DEFAULT_ITERATIONS)
    return f"pbkdf2:{_DEFAULT_ITERATIONS}:{salt.hex()}:{dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    """Verify password against stored hash (supports legacy SHA-256).

    On success with a legacy hash, returns a second value indicating
    whether the hash should be upgraded — but we keep the signature
    simple: callers that want to upgrade call ``_hash_password`` again.
    """
    m = _PWHASH_RE.match(stored)
    if m:
        iterations = int(m.group(1))
        salt = bytes.fromhex(m.group(2))
        expected = bytes.fromhex(m.group(3))
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
        return hmac.compare_digest(dk, expected)

    # Legacy: bare SHA-256 hex (no salt, no iterations)
    legacy = hashlib.sha256(password.encode()).hexdigest()
    return hmac.compare_digest(stored, legacy)


def _is_legacy_hash(stored: str) -> bool:
    """Return True if the hash is the old bare SHA-256 format."""
    return not _PWHASH_RE.match(stored)


def _create_token(user_id: str) -> str:
    """Create a signed token for the user (24h expiry)."""
    from ..auth_tokens import create_token
    return create_token(user_id)


def _user_public(user: dict) -> dict:
    """Return the public-facing user dict (id/username/display_name/role/is_active)."""
    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user["display_name"],
        "role": user.get("role", "user"),
        "is_active": bool(user.get("is_active", 1)),
    }


def _verify_token(token: str) -> Optional[str]:
    """Verify signed token and return user_id, or None."""
    from ..auth_tokens import verify_token
    return verify_token(token)


async def get_current_user_id(request: Request) -> str:
    """Dependency: return the user_id set by AuthMiddleware.

    The middleware stores the verified token's user_id on
    ``request.state.user_id`` (see api/middleware.py). Unauthenticated
    requests that pass through a public prefix get ``"anonymous"``.
    """
    user_id = getattr(request.state, "user_id", None)
    return user_id or "anonymous"


# ── User DB (lazy init with workspace path) ──────────────────

_user_db = None


def _get_user_db():
    """Lazy-init: get the user DB instance.

    On first call, initializes SQLite + seeds admin/admin if empty.
    Uses a default path since workspace_path isn't directly available here.
    """
    global _user_db
    if _user_db is None:
        from strategy_research.interfaces.api.user_db import get_user_db, seed_admin_if_empty
        _user_db = get_user_db()
        seed_admin_if_empty(_user_db)
    return _user_db


# ── Endpoints ────────────────────────────────────────────────

# Registration endpoint — disabled by default (opt-in via
# SR_ALLOW_REGISTRATION=1, used by E2E tests / single-user setups).
# Manual DB insertion alternative:
#   python3 -c "
#   from strategy_research.interfaces.api.user_db import get_user_db, hash_password
#   db = get_user_db()
#   db.create_user('myuser', 'My User', hash_password('mypassword'))
#   "
@router.post("/register", response_model=TokenResponse)
async def register(body: UserCreate):
    """Register a new user (opt-in via SR_ALLOW_REGISTRATION=1)."""
    if not _registration_enabled():
        raise HTTPException(status_code=403, detail="Registration is disabled")

    db = _get_user_db()
    if db.get_user_by_username(body.username):
        raise HTTPException(status_code=409, detail="Username already exists")

    user = db.create_user(
        body.username,
        body.display_name or body.username,
        _hash_password(body.password),
    )
    token = _create_token(user["id"])
    return TokenResponse(
        access_token=token,
        user=_user_public(user),
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: UserLogin):
    """Login with username + password."""
    db = _get_user_db()
    user = db.get_user_by_username(body.username)
    if not user or not _verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Disabled accounts cannot log in.
    if not bool(user.get("is_active", 1)):
        raise HTTPException(status_code=403, detail="Account is disabled")

    # SEC-1: auto-upgrade legacy SHA-256 hashes on successful login
    if _is_legacy_hash(user["password_hash"]):
        db.update_password(user["id"], _hash_password(body.password))

    token = _create_token(user["id"])
    return TokenResponse(
        access_token=token,
        user=_user_public(user),
    )


@router.get("/me")
async def me(user_id: str = Depends(get_current_user_id)):
    """Get current user info (requires a valid token).

    Reads the live user row so role/is_active changes take effect without
    a token refresh (e.g. an admin manually disables the account).
    """
    if not user_id or user_id == "anonymous":
        raise HTTPException(status_code=401, detail="Not authenticated")
    db = _get_user_db()
    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not bool(user.get("is_active", 1)):
        raise HTTPException(status_code=403, detail="Account is disabled")
    return _user_public(user)


@router.post("/change-password")
async def change_password(
    body: ChangePassword,
    user_id: str = Depends(get_current_user_id),
):
    """Change password for the currently authenticated user.

    The old password is verified against the token-bound account —
    not a global password-hash scan (which could update the wrong
    account when two users share a password).
    """
    if not user_id or user_id == "anonymous":
        raise HTTPException(status_code=401, detail="Not authenticated")

    db = _get_user_db()
    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not _verify_password(body.old_password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Old password incorrect")

    db.update_password(user["id"], _hash_password(body.new_password))
    return {"message": "Password updated"}
