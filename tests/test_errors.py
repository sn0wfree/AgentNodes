"""Tests for core/errors.py (Phase 3.3)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestStrategyResearchError(unittest.TestCase):

    def test_basic_message(self):
        from agentnodes.foundation.errors import StrategyResearchError
        err = StrategyResearchError("something went wrong")
        self.assertEqual(str(err), "something went wrong")
        self.assertEqual(err.details, {})

    def test_with_details(self):
        from agentnodes.foundation.errors import StrategyResearchError
        err = StrategyResearchError("oops", details={"hint": "retry"})
        self.assertEqual(err.details, {"hint": "retry"})

    def test_code_default_is_class_name(self):
        from agentnodes.foundation.errors import StrategyResearchError
        err = StrategyResearchError("x")
        self.assertEqual(err.code, "StrategyResearchError")

    def test_to_dict(self):
        from agentnodes.foundation.errors import StrategyResearchError
        err = StrategyResearchError("x", details={"k": "v"})
        self.assertEqual(err.to_dict()["code"], "StrategyResearchError")
        self.assertEqual(err.to_dict()["message"], "x")
        self.assertEqual(err.to_dict()["details"], {"k": "v"})

    def test_is_exception_subclass(self):
        from agentnodes.foundation.errors import StrategyResearchError
        self.assertTrue(issubclass(StrategyResearchError, Exception))


class TestMidLevelCategories(unittest.TestCase):

    def test_all_inherit_from_root(self):
        from agentnodes.foundation.errors import (
            BacktestError,
            ConfigError,
            GoalError,
            NotFoundError,
            ProviderError,
            SessionError,
            StrategyResearchError,
            SwarmError,
        )
        for cls in (ConfigError, ProviderError, SessionError,
                    BacktestError, GoalError, SwarmError, NotFoundError):
            self.assertTrue(issubclass(cls, StrategyResearchError),
                            f"{cls.__name__} should derive from StrategyResearchError")

    def test_each_can_be_raised(self):
        from agentnodes.foundation.errors import (
            BacktestError,
            ConfigError,
            GoalError,
            ProviderError,
            SessionError,
            SwarmError,
        )
        for cls in (ConfigError, ProviderError, SessionError,
                    BacktestError, GoalError, SwarmError):
            with self.assertRaises(cls):
                raise cls("test")

    def test_caught_by_root(self):
        from agentnodes.foundation.errors import (
            ConfigError,
            StrategyResearchError,
        )
        try:
            raise ConfigError("bad config")
        except StrategyResearchError as exc:
            self.assertEqual(exc.code, "ConfigError")


class TestNotFoundError(unittest.TestCase):

    def test_what_attribute(self):
        from agentnodes.foundation.errors import NotFoundError
        err = NotFoundError("session")
        self.assertEqual(err.what, "session")
        self.assertIn("session", str(err))

    def test_default_message(self):
        from agentnodes.foundation.errors import NotFoundError
        err = NotFoundError()
        self.assertIn("resource", str(err))


class TestWrapException(unittest.TestCase):

    def test_basic_wrap(self):
        from agentnodes.foundation.errors import (
            ProviderError,
            wrap_exception,
        )
        original = RuntimeError("boom")
        try:
            try:
                raise original
            except RuntimeError as exc:
                wrap_exception(exc, into=ProviderError)
        except ProviderError as wrapped:
            # __cause__ is the original
            self.assertIs(wrapped.__cause__, original)
            self.assertIn("boom", str(wrapped))
            self.assertEqual(wrapped.details.get("cause"), "RuntimeError")

    def test_custom_message(self):
        from agentnodes.foundation.errors import (
            ConfigError,
            wrap_exception,
        )
        try:
            try:
                raise ValueError("bad value")
            except ValueError as exc:
                wrap_exception(exc, into=ConfigError, message="custom")
        except ConfigError as wrapped:
            self.assertEqual(str(wrapped), "custom")

    def test_preserves_cause_chain(self):
        """The cause chain is intact — no swallowing."""
        from agentnodes.foundation.errors import (
            BacktestError,
            wrap_exception,
        )

        class CustomCause(Exception):
            pass

        try:
            try:
                raise CustomCause("root")
            except CustomCause as exc:
                wrap_exception(exc, into=BacktestError)
        except BacktestError as wrapped:
            self.assertIs(wrapped.__cause__.__class__, CustomCause)


class TestLLMErrorIntegration(unittest.TestCase):

    def test_llm_error_caught_by_root(self):
        """LLMError is part of the unified hierarchy via ConfigError."""
        from agentnodes.foundation.errors import (
            ConfigError,
            StrategyResearchError,
        )
        from agentnodes.foundation.llm.errors import LLMAuthError, LLMError

        # LLMAuthError inherits from LLMError → ConfigError → StrategyResearchError
        self.assertTrue(issubclass(LLMAuthError, StrategyResearchError))
        self.assertTrue(issubclass(LLMAuthError, ConfigError))
        self.assertTrue(issubclass(LLMError, ConfigError))

        # Catching the root catches LLM errors too
        with self.assertRaises(StrategyResearchError):
            raise LLMAuthError("bad key")

        # Catching ConfigError also catches LLM errors
        with self.assertRaises(ConfigError):
            raise LLMAuthError("bad key")


class TestErrorInheritanceContract(unittest.TestCase):

    def test_can_be_raised_and_caught(self):
        """End-to-end: raise, catch via root."""
        from agentnodes.foundation.errors import (
            StrategyResearchError,
            SwarmError,
        )

        def failing():
            raise SwarmError("agent X died")

        with self.assertRaises(StrategyResearchError) as ctx:
            failing()
        self.assertEqual(ctx.exception.code, "SwarmError")


if __name__ == "__main__":
    unittest.main()
