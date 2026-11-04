"""Tests for expression_evaluator.py — Expression DSL for workflow conditions."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.apps.goal.expression_evaluator import (
    ExpressionEvaluator,
    _parse_literal,
    _resolve_path,
    evaluate_condition,
)


class TestResolvePath(unittest.TestCase):

    def test_simple_key(self) -> None:
        self.assertEqual(_resolve_path({"a": 1}, "a"), 1)

    def test_nested_path(self) -> None:
        self.assertEqual(_resolve_path({"a": {"b": 2}}, "a.b"), 2)

    def test_missing_path(self) -> None:
        self.assertIsNone(_resolve_path({"a": 1}, "b"))

    def test_deep_missing(self) -> None:
        self.assertIsNone(_resolve_path({"a": {"b": 2}}, "a.c"))


class TestParseLiteral(unittest.TestCase):

    def test_integer(self) -> None:
        self.assertEqual(_parse_literal("42"), 42)

    def test_float(self) -> None:
        self.assertEqual(_parse_literal("3.14"), 3.14)

    def test_negative_number(self) -> None:
        self.assertEqual(_parse_literal("-1"), -1)

    def test_quoted_string(self) -> None:
        self.assertEqual(_parse_literal('"hello"'), "hello")

    def test_single_quoted_string(self) -> None:
        self.assertEqual(_parse_literal("'world'"), "world")

    def test_fallback_to_raw(self) -> None:
        self.assertEqual(_parse_literal("something"), "something")


class TestExpressionEvaluator(unittest.TestCase):

    def test_less_than_true(self) -> None:
        e = ExpressionEvaluator({"a": 1})
        self.assertTrue(e.evaluate("a < 2"))

    def test_less_than_false(self) -> None:
        e = ExpressionEvaluator({"a": 2})
        self.assertFalse(e.evaluate("a < 2"))

    def test_greater_than(self) -> None:
        e = ExpressionEvaluator({"a": 5})
        self.assertTrue(e.evaluate("a > 3"))

    def test_equals(self) -> None:
        e = ExpressionEvaluator({"a": 10})
        self.assertTrue(e.evaluate("a == 10"))

    def test_not_equals(self) -> None:
        e = ExpressionEvaluator({"a": 10})
        self.assertTrue(e.evaluate("a != 5"))

    def test_less_than_or_equal(self) -> None:
        e = ExpressionEvaluator({"a": 5})
        self.assertTrue(e.evaluate("a <= 5"))
        self.assertTrue(e.evaluate("a <= 6"))

    def test_greater_than_or_equal(self) -> None:
        e = ExpressionEvaluator({"a": 5})
        self.assertTrue(e.evaluate("a >= 5"))
        self.assertTrue(e.evaluate("a >= 4"))

    def test_dot_path(self) -> None:
        e = ExpressionEvaluator({"output": {"sharpe": 1.5}})
        self.assertTrue(e.evaluate("output.sharpe > 1.0"))

    def test_and_operator(self) -> None:
        e = ExpressionEvaluator({"a": 2, "b": 3})
        self.assertTrue(e.evaluate("a > 1 and b > 2"))
        self.assertFalse(e.evaluate("a > 10 and b > 2"))

    def test_or_operator(self) -> None:
        e = ExpressionEvaluator({"a": 1, "b": 10})
        self.assertTrue(e.evaluate("a > 10 or b > 5"))
        self.assertFalse(e.evaluate("a > 10 or b < 5"))

    def test_not_operator(self) -> None:
        e = ExpressionEvaluator({"a": 1})
        self.assertTrue(e.evaluate("not a > 10"))

    def test_string_equality(self) -> None:
        e = ExpressionEvaluator({"status": "fail"})
        self.assertTrue(e.evaluate('status == "fail"'))

    def test_string_inequality(self) -> None:
        e = ExpressionEvaluator({"status": "pass"})
        self.assertTrue(e.evaluate('status != "fail"'))

    def test_boolean_true_literal(self) -> None:
        e = ExpressionEvaluator({})
        self.assertTrue(e.evaluate("true"))

    def test_boolean_false_literal(self) -> None:
        e = ExpressionEvaluator({})
        self.assertFalse(e.evaluate("false"))

    def test_missing_path_returns_false(self) -> None:
        e = ExpressionEvaluator({"a": 1})
        self.assertFalse(e.evaluate("missing > 0"))

    def test_invalid_expression_raises(self) -> None:
        e = ExpressionEvaluator({})
        with self.assertRaises(ValueError):
            e.evaluate("garbage !!! expr")


class TestEvaluateCondition(unittest.TestCase):

    def test_convenience_function(self) -> None:
        self.assertTrue(evaluate_condition("a < 2", {"a": 1}))
        self.assertFalse(evaluate_condition("a > 2", {"a": 1}))


if __name__ == "__main__":
    unittest.main()
