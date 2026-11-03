"""Tests for redaction.py — path redaction and payload scrubbing."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from strategy_research.kernel.agent.redaction import (
    is_sensitive_arg,
    redact_internal_paths,
    redact_payload,
)


class TestIsSensitiveArg(unittest.TestCase):

    def test_api_key(self) -> None:
        self.assertTrue(is_sensitive_arg("api_key"))

    def test_authorization(self) -> None:
        self.assertTrue(is_sensitive_arg("authorization"))

    def test_password(self) -> None:
        self.assertTrue(is_sensitive_arg("password"))

    def test_secret(self) -> None:
        self.assertTrue(is_sensitive_arg("secret"))

    def test_token(self) -> None:
        self.assertTrue(is_sensitive_arg("token"))

    def test_access_token(self) -> None:
        self.assertTrue(is_sensitive_arg("access_token"))

    def test_account_number(self) -> None:
        self.assertTrue(is_sensitive_arg("account_number"))

    def test_ssn(self) -> None:
        self.assertTrue(is_sensitive_arg("ssn"))

    def test_routing_number(self) -> None:
        self.assertTrue(is_sensitive_arg("routing_number"))

    def test_bank_account_number(self) -> None:
        self.assertTrue(is_sensitive_arg("bank_account_number"))

    def test_safe_key_not_sensitive(self) -> None:
        self.assertFalse(is_sensitive_arg("name"))

    def test_account_ref_not_sensitive(self) -> None:
        self.assertFalse(is_sensitive_arg("account_ref"))

    def test_camel_case_account_number(self) -> None:
        self.assertTrue(is_sensitive_arg("accountNumber"))

    def test_kebab_case_account_number(self) -> None:
        self.assertTrue(is_sensitive_arg("account-number"))

    def test_api_token_marker(self) -> None:
        self.assertTrue(is_sensitive_arg("my_api_token"))


class TestRedactInternalPaths(unittest.TestCase):

    def test_none_returns_empty(self) -> None:
        self.assertEqual(redact_internal_paths(None), "")

    def test_empty_string(self) -> None:
        self.assertEqual(redact_internal_paths(""), "")

    def test_non_string_converted(self) -> None:
        self.assertEqual(redact_internal_paths(42), "42")


class TestRedactPayload(unittest.TestCase):

    def test_redacts_api_key(self) -> None:
        result = redact_payload({"api_key": "sk-123", "name": "test"})
        self.assertEqual(result["api_key"], "[redacted]")
        self.assertEqual(result["name"], "test")

    def test_recursive_redact_dict(self) -> None:
        result = redact_payload({"nested": {"api_key": "secret", "a": 1}})
        self.assertEqual(result["nested"]["api_key"], "[redacted]")
        self.assertEqual(result["nested"]["a"], 1)

    def test_recursive_redact_list(self) -> None:
        result = redact_payload([{"api_key": "secret"}, {"name": "safe"}])
        self.assertEqual(result[0]["api_key"], "[redacted]")
        self.assertEqual(result[1]["name"], "safe")

    def test_scalar_passthrough(self) -> None:
        self.assertEqual(redact_payload("string"), "string")
        self.assertEqual(redact_payload(42), 42)
        self.assertEqual(redact_payload(None), None)

    def test_preserves_account_ref(self) -> None:
        result = redact_payload({"account_ref": "broker-123", "data": 1})
        self.assertEqual(result["account_ref"], "broker-123")


if __name__ == "__main__":
    unittest.main()
