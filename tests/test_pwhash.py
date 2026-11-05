"""SEC-1 tests — PBKDF2 password hashing + legacy SHA-256 migration."""

from __future__ import annotations

from agentnodes.interfaces.api.routers.auth import (
    _hash_password,
    _verify_password,
    _is_legacy_hash,
)


class TestPwhash:
    def test_new_hash_has_prefix(self):
        h = _hash_password("test123")
        assert h.startswith("pbkdf2:")
        parts = h.split(":")
        assert len(parts) == 4
        assert int(parts[1]) >= 260_000

    def test_same_password_different_hashes(self):
        h1 = _hash_password("test123")
        h2 = _hash_password("test123")
        assert h1 != h2, "different salts must produce different hashes"

    def test_verify_correct(self):
        h = _hash_password("hello")
        assert _verify_password("hello", h)
        assert not _verify_password("wrong", h)

    def test_legacy_hash_accepted(self):
        import hashlib
        legacy = hashlib.sha256("admin".encode()).hexdigest()
        assert _verify_password("admin", legacy)
        assert _verify_password("wrong", legacy) is False
        assert _is_legacy_hash(legacy)

    def test_new_hash_is_not_legacy(self):
        h = _hash_password("test")
        assert not _is_legacy_hash(h)

    def test_verify_old_password_is_not_legacy(self):
        import hashlib
        legacy = hashlib.sha256("old".encode()).hexdigest()
        assert _is_legacy_hash(legacy)
        h = _hash_password("new")
        assert not _is_legacy_hash(h)
