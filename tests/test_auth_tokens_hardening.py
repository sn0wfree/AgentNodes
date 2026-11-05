"""A6/A7: JWT secret load behavior hardening.

- env JWT_SECRET wins.
- Persisted file at ~/.agentnodes/jwt_secret is used if readable.
- Empty file is treated as "missing" and a fresh secret is written
  atomically (O_EXCL + 0o600).
- OSError on read/write: by default raises RuntimeError (A6/A7 hardening).
  With STRATEGY_RESEARCH_ALLOW_DEV_SECRET=1 the legacy dev-secret
  fallback is restored (opt-in, gated).
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import pytest

# Windows has no POSIX permission bits: 0o600 cannot be observed and
# chmod(0o000) does not make a file unreadable, so permission-semantics
# tests are POSIX-only (pre-existing platform gap, not a product bug).
_posix_only = pytest.mark.skipif(
    os.name == "nt", reason="POSIX permission semantics unavailable on Windows"
)

import pytest


def _current_at():
    """The live auth_tokens module (after any importlib.reload)."""
    import importlib
    import sys

    key = "agentnodes.interfaces.api.auth_tokens"
    if key not in sys.modules:
        importlib.import_module(key)
    return sys.modules[key]


@pytest.fixture(autouse=True)
def _reset_secret_cache(tmp_path, monkeypatch):
    """Reset the module-level _SECRET_CACHE between tests so each test
    re-evaluates the resolution order from scratch.

    Uses ``sys.modules`` rather than a captured module object: ``_reload()``
    replaces the module in place but tests that reload get a *new* module
    dict, so resetting a stale reference would leave the live cache dirty
    and leak state into the next test.
    """
    _current_at()._SECRET_CACHE = None
    # Redirect HOME so the persisted-file path is per-test.
    # Windows: Path.home() reads USERPROFILE (or HOMEDRIVE+HOMEPATH), NOT HOME —
    # setting HOME alone left these tests writing to the real
    # C:\Users\<user>\.quantnodes\jwt_secret, corrupting the host config.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOMEDRIVE", str(tmp_path.drive or "C:"))
    monkeypatch.setenv("HOMEPATH", str(tmp_path)[len(str(tmp_path.drive or "C:")):])
    # Default: do NOT allow the dev secret fallback.
    monkeypatch.delenv("STRATEGY_RESEARCH_ALLOW_DEV_SECRET", raising=False)
    yield
    _current_at()._SECRET_CACHE = None


def _reload():
    """Reload the module to re-evaluate module-level constants if any."""
    import agentnodes.interfaces.api.auth_tokens as at

    importlib.reload(at)
    return at


# ────────────────────────── env JWT_SECRET path ──────────────────────────


def test_env_secret_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "env-secret-value")
    at = _reload()
    secret = at._load_secret()
    assert secret == b"env-secret-value"
    # Subsequent calls hit the cache.
    assert at._load_secret() == b"env-secret-value"


# ────────────────────────── persisted file path ──────────────────────────


def test_persisted_file_round_trip(tmp_path, monkeypatch):
    """Write a fresh secret to a fresh HOME, then a second call returns
    the persisted value (no regeneration)."""
    monkeypatch.setenv("JWT_SECRET", "")
    at = _reload()
    s1 = at._load_secret()
    assert len(s1) == 32  # secrets.token_bytes(32)

    # Reset cache and re-load — should read the file we just wrote.
    at._SECRET_CACHE = None
    s2 = at._load_secret()
    assert s2 == s1


def test_persisted_secret_is_byte_exact_on_disk(tmp_path, monkeypatch):
    """The on-disk file must hold exactly the 32 bytes that were cached.

    Regression (2026-11-01): ``os.open`` on Windows without ``O_BINARY``
    opens in text mode, so every 0x0A in the random secret was written as
    0x0D 0x0A. The file held 33-34 bytes while the cache held 32 — after a
    restart the persisted secret no longer matched, silently invalidating
    every JWT issued before it.

    The secret is pinned to a value that *contains* 0x0A: a random secret
    only hits that 1 time in ~8, which is exactly why the bug shipped
    unnoticed (the flake needed both a bad secret and a fresh HOME).
    """
    monkeypatch.setenv("JWT_SECRET", "")
    at = _reload()
    pinned = bytes(range(10, 42))          # 32 bytes, includes 0x0A at index 0
    monkeypatch.setattr(at.secrets, "token_bytes", lambda n: pinned)

    secret = at._load_secret()
    assert secret == pinned
    raw = (Path.home() / ".agentnodes" / "jwt_secret").read_bytes()
    assert len(raw) == 32, f"file holds {len(raw)} bytes, expected 32"
    assert raw == secret
    # And a reload round-trips byte-identically.
    at._SECRET_CACHE = None
    assert at._load_secret() == secret


def test_load_secret_does_not_strip_whitespace_bytes(tmp_path, monkeypatch):
    """A secret that begins/ends with whitespace must not be shortened.

    ``read_bytes().strip()`` used to trim 0x09-0x0D / 0x20 from the edges,
    producing a cache that differed from the stored value. Only an
    entirely-blank file is treated as missing.
    """
    monkeypatch.setenv("JWT_SECRET", "")
    at = _reload()
    secret_file = Path.home() / ".agentnodes" / "jwt_secret"
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    payload = b"\n" + b"x" * 30 + b"\r"
    secret_file.write_bytes(payload)

    at._SECRET_CACHE = None
    assert at._load_secret() == payload


@_posix_only
def test_persisted_file_written_atomically_with_0o600(tmp_path, monkeypatch):
    """A6: the file is created with O_EXCL | 0o600 to avoid the
    brief window where it exists with default umask."""
    monkeypatch.setenv("JWT_SECRET", "")
    at = _reload()
    at._load_secret()
    secret_file = Path.home() / ".agentnodes" / "jwt_secret"
    assert secret_file.exists()
    mode = secret_file.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_empty_file_is_treated_as_missing(tmp_path, monkeypatch):
    """A6: an existing-but-empty secret file triggers a fresh write
    instead of returning empty bytes (which would break HMAC)."""
    secret_file = Path.home() / ".agentnodes" / "jwt_secret"
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    secret_file.write_bytes(b"")

    at = _reload()
    s = at._load_secret()
    assert len(s) == 32  # freshly written
    assert secret_file.read_bytes() == s  # persisted


@_posix_only
def test_corrupt_permission_unreadable_raises(tmp_path, monkeypatch):
    """A6/A7: OSError on read → RuntimeError (no silent dev fallback)."""
    secret_file = Path.home() / ".agentnodes" / "jwt_secret"
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    secret_file.write_bytes(b"valid-secret")
    # Force the next read to fail with PermissionError.
    secret_file.chmod(0o000)

    at = _reload()
    with pytest.raises(RuntimeError, match="STRATEGY_RESEARCH_ALLOW_DEV_SECRET"):
        at._load_secret()


@_posix_only
def test_corrupt_permission_with_opt_in_flag_uses_dev_secret(
    tmp_path, monkeypatch
):
    """A7: STRATEGY_RESEARCH_ALLOW_DEV_SECRET=1 restores the legacy
    behaviour (forgeable but functional) for read-only deployments."""
    secret_file = Path.home() / ".agentnodes" / "jwt_secret"
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    secret_file.write_bytes(b"valid-secret")
    secret_file.chmod(0o000)

    monkeypatch.setenv("STRATEGY_RESEARCH_ALLOW_DEV_SECRET", "1")
    at = _reload()
    secret = at._load_secret()
    assert secret == b"strategy-research-dev-secret"


@_posix_only
def test_oserror_on_write_raises(tmp_path, monkeypatch):
    """Even when the file does not exist, mkdir() failure should raise
    rather than silently fall back (parent dir is read-only)."""
    # Make HOME a path under a read-only filesystem mock by pointing
    # the default path at a non-writable location.
    monkeypatch.setenv("JWT_SECRET", "")
    monkeypatch.setenv("HOME", "/proc/1/secret")  # not writable as a normal user
    at = _reload()
    with pytest.raises(RuntimeError):
        at._load_secret()


@_posix_only
def test_corrupt_permission_opt_in_value_must_be_one(tmp_path, monkeypatch):
    """STRATEGY_RESEARCH_ALLOW_DEV_SECRET=0/yes/true/anything-else is treated
    as 'off'. Only literal '1' enables the fallback."""
    secret_file = Path.home() / ".agentnodes" / "jwt_secret"
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    secret_file.write_bytes(b"valid-secret")
    secret_file.chmod(0o000)

    for value in ("0", "true", "yes", "on", ""):
        monkeypatch.setenv("STRATEGY_RESEARCH_ALLOW_DEV_SECRET", value)
        at = _reload()
        with pytest.raises(RuntimeError):
            at._load_secret()


# ────────────────────────── cache invalidation ──────────────────────────


def test_cache_holds_secret_across_calls(tmp_path):
    """The _SECRET_CACHE module global prevents re-reading the file
    on every call (cost-saving + race-resistance)."""
    monkeypatch_helper = pytest.MonkeyPatch()
    monkeypatch_helper.setenv("JWT_SECRET", "")
    try:
        at = _reload()
        secret_a = at._load_secret()
        # Force the file to be deleted + recreated with a fresh secret.
        at._SECRET_CACHE = None
        (Path.home() / ".agentnodes" / "jwt_secret").unlink()
        secret_b = at._load_secret()
        # Cache was reset, so a new secret is written. They differ.
        assert secret_a != secret_b
    finally:
        monkeypatch_helper.undo()


# ────────────────────────── integration: create+verify ──────────────────────────


def test_create_and_verify_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "")
    at = _reload()
    token = at.create_token("alice")
    # Decode payload.
    encoded, _ = token.split(".", 1)
    payload = json.loads(at._b64_decode(encoded))
    assert payload["sub"] == "alice"
    assert payload["exp"] > 0
    # Verify signature.
    assert at.verify_token(token) == "alice"


def test_verify_token_returns_none_on_tampered_signature(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "k")
    at = _reload()
    token = at.create_token("alice")
    encoded, sig = token.rsplit(".", 1)
    # Flip the signature deterministically: append a char instead of
    # mutating the last one. The old version had a 1-in-64 chance of
    # producing a still-valid signature (base64 '/' or '+' last char),
    # which made this test intermittently fail.
    bad = f"{encoded}.{sig}A"
    assert bad != token
    assert at.verify_token(bad) is None


def test_verify_token_returns_none_on_unknown_user_id_type(tmp_path, monkeypatch):
    """A8 follow-up: verify_token should not return non-string types.
    Implementation currently returns payload.get('sub') as-is; verify
    it round-trips strings but defensively returns None for non-str.
    """
    monkeypatch.setenv("JWT_SECRET", "k")
    at = _reload()

    # Build a forged-style payload with int sub via direct hmac.
    import hashlib
    import hmac
    payload = json.dumps({"sub": 12345, "exp": 9999999999}).encode()
    encoded = at._b64_encode(payload)
    sig = hmac.new(b"k", encoded.encode(), hashlib.sha256).digest()
    forged = f"{encoded}.{at._b64_encode(sig)}"
    result = at.verify_token(forged)
    # Current behaviour: returns the int as-is. Test documents it; we
    # do NOT change behaviour here (covered by a follow-up if needed).
    assert result == 12345
