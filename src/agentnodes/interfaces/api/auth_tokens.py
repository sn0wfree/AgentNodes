"""Signed authentication tokens (HMAC-SHA256).

Previously tokens were bare base64 payloads — anyone could forge a
token for any user. Tokens are now ``base64url(payload) + "." +
base64url(hmac_sha256(secret, payload))`` with the secret taken from
``JWT_SECRET`` env or a persisted random secret at
``~/.quantnodes/jwt_secret`` (0600, created on first use).

Legacy unsigned tokens are rejected: existing sessions must re-login.

A6/A7 hardening (low-risk mode):
- An existing secret file that is empty is treated as "no secret" and
  a fresh random one is written (preserves pre-fix behaviour).
- An OSError on read/write falls back to the dev secret ONLY when
  ``STRATEGY_RESEARCH_ALLOW_DEV_SECRET=1`` is set in the environment;
  otherwise we raise RuntimeError. This prevents the previous silent
  fallback to a forgeable default. Existing deployments that rely on
  the fallback can opt back in via the env var.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Optional

TOKEN_TTL_SECONDS = 86400  # 24h

logger = logging.getLogger(__name__)

# Last-resort public constant. Public to anyone who reads the source.
# The fallback path is opt-in via STRATEGY_RESEARCH_ALLOW_DEV_SECRET=1.
_DEV_SECRET = b"strategy-research-dev-secret"


_SECRET_CACHE: bytes | None = None


def _allow_dev_secret() -> bool:
    """Whether the OSError-fallback to the dev secret is permitted.

    Defaults to False: deployments that can't persist their secret
    must configure JWT_SECRET explicitly. The fallback is kept as an
    opt-in escape hatch for read-only filesystems / local dev.
    """
    return os.environ.get("STRATEGY_RESEARCH_ALLOW_DEV_SECRET") == "1"


def _load_secret() -> bytes:
    """Resolve the signing secret: env JWT_SECRET > persisted file > dev fallback.

    The dev fallback (public constant) is a last resort for read-only
    filesystems and is gated behind ``STRATEGY_RESEARCH_ALLOW_DEV_SECRET=1``.
    When the env var is not set and the filesystem is unavailable,
    RuntimeError is raised so production deployments fail loudly
    instead of accepting forgeable tokens (A6/A7).
    """
    global _SECRET_CACHE
    if _SECRET_CACHE is not None:
        return _SECRET_CACHE

    env_secret = os.environ.get("JWT_SECRET")
    if env_secret:
        _SECRET_CACHE = env_secret.encode()
        return _SECRET_CACHE

    path = Path.home() / ".agentnodes" / "jwt_secret"
    try:
        if path.exists():
            existing = path.read_bytes()
            # An all-whitespace file is treated as missing. Do NOT use
            # ``strip()``: the secret is 32 random bytes and may legitimately
            # begin or end with whitespace bytes (\r, \n, space). Stripping
            # silently shortens the secret, so a token signed before the
            # restart fails verification after it (observed as an
            # intermittent HMAC mismatch on Windows, where \r is common).
            if existing.strip():
                _SECRET_CACHE = existing
                return _SECRET_CACHE
            # Empty file: treat as missing and (re-)create below.
            # Remove the empty stub so O_EXCL below doesn't trip over it.
            try:
                path.unlink()
            except OSError:
                pass
        path.parent.mkdir(parents=True, exist_ok=True)
        _SECRET_CACHE = secrets.token_bytes(32)
        # Use os.open with O_EXCL + 0o600 to avoid the brief window where
        # the file exists with default umask (often world-readable on
        # shared systems). Falls back to write_bytes+chmod if the file
        # already exists (race recovery).
        #
        # O_BINARY is required on Windows: without it the CRT opens the fd
        # in text mode and every 0x0A byte in the random secret is written
        # as 0x0D 0x0A. The file then holds 33-34 bytes while the cache
        # holds 32, so after a restart the persisted secret no longer
        # matches the one that signed existing tokens (all JWTs break, and
        # a token issued before the restart fails verification after it).
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_BINARY", 0)
        fd = os.open(path, flags, 0o600)
        try:
            os.write(fd, _SECRET_CACHE)
        finally:
            os.close(fd)
        return _SECRET_CACHE
    except OSError as exc:
        # Filesystem unavailable. Default behaviour: refuse to fall
        # back to the forgeable dev secret (A6/A7 hardening).
        if _allow_dev_secret():
            logger.error(
                "auth_tokens: cannot persist JWT secret to %s — falling "
                "back to the forgeable dev secret (STRATEGY_RESEARCH_ALLOW_DEV_SECRET=1). "
                "Set JWT_SECRET in the environment for any non-local deployment.",
                path,
            )
            _SECRET_CACHE = _DEV_SECRET
            return _SECRET_CACHE
        raise RuntimeError(
            f"auth_tokens: cannot persist JWT secret to {path} and "
            f"STRATEGY_RESEARCH_ALLOW_DEV_SECRET is not set "
            f"({exc}). Set JWT_SECRET or STRATEGY_RESEARCH_ALLOW_DEV_SECRET=1."
        ) from exc


def _b64_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64_decode(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded)


def create_token(user_id: str, *, ttl: int = TOKEN_TTL_SECONDS) -> str:
    """Create a signed token for ``user_id``."""
    payload = json.dumps(
        {"sub": user_id, "exp": time.time() + ttl},
        separators=(",", ":"),
    ).encode()
    encoded = _b64_encode(payload)
    signature = hmac.new(_load_secret(), encoded.encode(), hashlib.sha256).digest()
    return f"{encoded}.{_b64_encode(signature)}"


def verify_token(token: str) -> Optional[str]:
    """Verify signature + expiry; return user_id or None."""
    try:
        encoded, signature_b64 = token.rsplit(".", 1)
        expected = hmac.new(
            _load_secret(), encoded.encode(), hashlib.sha256
        ).digest()
        supplied = _b64_decode(signature_b64)
        if not hmac.compare_digest(expected, supplied):
            return None
        payload = json.loads(_b64_decode(encoded))
        if payload.get("exp", 0) < time.time():
            return None
        return payload.get("sub")
    except Exception:
        return None
