"""Redaction comprehensive tests — all PII patterns, deep nesting, edge cases."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.kernel.agent.redaction import (
    _fold_key,
    _internal_roots,
    is_sensitive_arg,
    redact_internal_paths,
    redact_payload,
)


# ── is_sensitive_arg ─────────────────────────────────────────────


class TestIsSensitiveArg:
    def test_credential_keys(self):
        for key in ["api_key", "authorization", "password", "secret", "token"]:
            assert is_sensitive_arg(key) is True

    def test_pii_keys(self):
        for key in [
            "account_number", "account_id", "account_no", "account_num",
            "brokerage_account_number", "brokerage_account_id",
            "ssn", "social_security_number", "tax_id", "taxpayer_id", "tin",
            "routing_number", "bank_account_number",
        ]:
            assert is_sensitive_arg(key) is True

    def test_robinhood_keys(self):
        for key in ["account_url", "rhs_account_number"]:
            assert is_sensitive_arg(key) is True

    def test_safe_keys(self):
        for key in ["name", "email", "phone", "address", "account_ref", "title"]:
            assert is_sensitive_arg(key) is False

    def test_marker_in_compound_name(self):
        assert is_sensitive_arg("my_api_token") is True
        assert is_sensitive_arg("password_field") is True

    def test_camel_case(self):
        assert is_sensitive_arg("apiKey") is True
        assert is_sensitive_arg("accessToken") is True

    def test_kebab_case(self):
        assert is_sensitive_arg("api-key") is True
        assert is_sensitive_arg("access-token") is True

    def test_case_insensitive(self):
        assert is_sensitive_arg("API_KEY") is True
        assert is_sensitive_arg("Password") is True

    def test_fold_key(self):
        assert _fold_key("API_KEY") == "apikey"
        assert _fold_key("api-key") == "apikey"
        assert _fold_key("Api_Key") == "apikey"


# ── redact_payload ────────────────────────────────────────────────


class TestRedactPayload:
    def test_redacts_sensitive_dict(self):
        obj = {"api_key": "secret123", "name": "John"}
        result = redact_payload(obj)
        assert result["api_key"] == "[redacted]"
        assert result["name"] == "John"

    def test_no_mutation(self):
        obj = {"api_key": "secret123"}
        original = obj.copy()
        redact_payload(obj)
        assert obj == original

    def test_nested_dict(self):
        obj = {"config": {"password": "pass123", "host": "localhost"}}
        result = redact_payload(obj)
        assert result["config"]["password"] == "[redacted]"
        assert result["config"]["host"] == "localhost"

    def test_deeply_nested(self):
        obj = {"a": {"b": {"c": {"secret": "val"}}}}
        result = redact_payload(obj)
        assert result["a"]["b"]["c"]["secret"] == "[redacted]"

    def test_list_of_dicts(self):
        obj = [{"api_key": "k1"}, {"name": "n1"}]
        result = redact_payload(obj)
        assert result[0]["api_key"] == "[redacted]"
        assert result[1]["name"] == "n1"

    def test_mixed_dict_list(self):
        obj = {"items": [{"token": "t1"}, {"id": "i1"}], "count": 2}
        result = redact_payload(obj)
        assert result["items"][0]["token"] == "[redacted]"
        assert result["items"][1]["id"] == "i1"
        assert result["count"] == 2

    def test_scalar_passthrough(self):
        assert redact_payload("hello") == "hello"
        assert redact_payload(42) == 42
        assert redact_payload(True) is True
        assert redact_payload(None) is None

    def test_empty_dict(self):
        assert redact_payload({}) == {}

    def test_empty_list(self):
        assert redact_payload([]) == []

    def test_account_ref_preserved(self):
        """account_ref is NOT in sensitive set."""
        obj = {"account_ref": "REF123"}
        result = redact_payload(obj)
        assert result["account_ref"] == "REF123"

    def test_content_key_redacted(self):
        """content is in _SENSITIVE_ARG_KEYS."""
        obj = {"content": "sensitive content"}
        result = redact_payload(obj)
        assert result["content"] == "[redacted]"

    def test_env_key_redacted(self):
        obj = {"env": {"API_KEY": "secret"}}
        result = redact_payload(obj)
        assert result["env"] == "[redacted]"

    def test_headers_key_redacted(self):
        obj = {"headers": {"Authorization": "Bearer token"}}
        result = redact_payload(obj)
        assert result["headers"] == "[redacted]"


# ── redact_internal_paths ─────────────────────────────────────────


class TestRedactInternalPaths:
    def test_none_returns_empty(self):
        assert redact_internal_paths(None) == ""

    def test_non_string_converted(self):
        result = redact_internal_paths(42)
        assert "42" in result

    def test_empty_string(self):
        assert redact_internal_paths("") == ""

    def test_safe_string_unchanged(self):
        assert redact_internal_paths("hello world") == "hello world"

    def test_home_path_redacted(self):
        import os
        home = os.path.expanduser("~")
        text = f"File at {home}/secret.txt"
        result = redact_internal_paths(text)
        assert home not in result
        assert "<redacted>" in result


# ── _internal_roots ──────────────────────────────────────────────


class TestInternalRoots:
    def test_returns_list(self):
        roots = _internal_roots()
        assert isinstance(roots, list)
        assert len(roots) > 0

    def test_sorted_longest_first(self):
        roots = _internal_roots()
        lengths = [len(r) for r in roots]
        assert lengths == sorted(lengths, reverse=True)

    def test_home_dir_included(self):
        import os
        roots = _internal_roots()
        home = os.path.expanduser("~")
        assert any(home in r for r in roots)
