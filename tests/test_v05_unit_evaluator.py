"""Phase 4 - v0.5 unit tests: ExpressionEvaluator edge cases.

Fills coverage gaps for:
  - All comparison operators: <, <=, >, >=, ==, !=
  - Boolean literals: true, false, 1, 0
  - Empty expression
  - Quoted strings with single and double quotes
  - TypeError handling (comparing string to number)
  - _resolve_path: nested, missing keys, None
  - _parse_literal: int, float, quoted string
  - _split_top_level: quotes containing "and"/"or" keywords
  - Complex expressions with nested and/or/not
  - evaluate_condition convenience function
"""
from __future__ import annotations

import pytest

from strategy_research.apps.goal.expression_evaluator import (
    ExpressionEvaluator,
    _parse_literal,
    _resolve_path,
    evaluate_condition,
)

# ═══════════════════════════════════════════════════════════════════════
# _resolve_path
# ═══════════════════════════════════════════════════════════════════════


class TestResolvePath:
    def test_single_key(self):
        assert _resolve_path({"a": 1}, "a") == 1

    def test_nested_keys(self):
        assert _resolve_path({"a": {"b": {"c": 42}}}, "a.b.c") == 42

    def test_missing_key(self):
        assert _resolve_path({"a": 1}, "b") is None

    def test_missing_nested_key(self):
        assert _resolve_path({"a": {"b": 1}}, "a.c") is None

    def test_none_intermediate(self):
        assert _resolve_path({"a": None}, "a.b") is None

    def test_empty_path(self):
        assert _resolve_path({"a": 1}, "") is None


# ═══════════════════════════════════════════════════════════════════════
# _parse_literal
# ═══════════════════════════════════════════════════════════════════════


class TestParseLiteral:
    def test_int(self):
        assert _parse_literal("42") == 42

    def test_float(self):
        assert _parse_literal("3.14") == 3.14

    def test_negative_int(self):
        assert _parse_literal("-5") == -5

    def test_negative_float(self):
        assert _parse_literal("-0.5") == -0.5

    def test_double_quoted_string(self):
        assert _parse_literal('"hello"') == "hello"

    def test_single_quoted_string(self):
        assert _parse_literal("'world'") == "world"

    def test_unquoted_string_fallback(self):
        assert _parse_literal("abc") == "abc"


# ═══════════════════════════════════════════════════════════════════════
# All comparison operators
# ═══════════════════════════════════════════════════════════════════════


class TestComparisonOperators:
    def setup_method(self):
        self.ev = ExpressionEvaluator({"v": {"n": 5, "s": "hello"}})

    def test_less_than(self):
        assert self.ev.evaluate("v.n < 10") is True
        assert self.ev.evaluate("v.n < 5") is False

    def test_less_equal(self):
        assert self.ev.evaluate("v.n <= 5") is True
        assert self.ev.evaluate("v.n <= 4") is False

    def test_greater_than(self):
        assert self.ev.evaluate("v.n > 3") is True
        assert self.ev.evaluate("v.n > 5") is False

    def test_greater_equal(self):
        assert self.ev.evaluate("v.n >= 5") is True
        assert self.ev.evaluate("v.n >= 6") is False

    def test_equal(self):
        assert self.ev.evaluate('v.s == "hello"') is True
        assert self.ev.evaluate('v.s == "world"') is False

    def test_not_equal(self):
        assert self.ev.evaluate('v.s != "world"') is True
        assert self.ev.evaluate('v.s != "hello"') is False

    def test_numeric_equal(self):
        assert self.ev.evaluate("v.n == 5") is True

    def test_numeric_not_equal(self):
        assert self.ev.evaluate("v.n != 10") is True


# ═══════════════════════════════════════════════════════════════════════
# Boolean literals
# ═══════════════════════════════════════════════════════════════════════


class TestBooleanLiterals:
    def setup_method(self):
        self.ev = ExpressionEvaluator({})

    def test_true(self):
        assert self.ev.evaluate("true") is True

    def test_false(self):
        assert self.ev.evaluate("false") is False

    def test_one(self):
        assert self.ev.evaluate("1") is True

    def test_zero(self):
        assert self.ev.evaluate("0") is False

    def test_empty_string(self):
        assert self.ev.evaluate("") is False

    def test_true_uppercase(self):
        assert self.ev.evaluate("TRUE") is True

    def test_false_uppercase(self):
        assert self.ev.evaluate("FALSE") is False


# ═══════════════════════════════════════════════════════════════════════
# Complex boolean expressions
# ═══════════════════════════════════════════════════════════════════════


class TestComplexExpressions:
    def test_and_or_precedence(self):
        ev = ExpressionEvaluator({"a": {"x": 1, "y": 2, "z": 3}})
        # (a.x > 0) or (a.y > 5 and a.z > 5) -> True
        assert ev.evaluate("a.x > 0 or a.y > 5 and a.z > 5") is True
        # (a.x > 5) or (a.y > 1 and a.z > 1) -> True (second clause)
        assert ev.evaluate("a.x > 5 or a.y > 1 and a.z > 1") is True
        # (a.x > 5) or (a.y > 5 and a.z > 5) -> False
        assert ev.evaluate("a.x > 5 or a.y > 5 and a.z > 5") is False

    def test_not_with_and(self):
        ev = ExpressionEvaluator({"a": {"x": 0.05}})
        assert ev.evaluate("not a.x > 0.1 and a.x > 0") is True

    def test_not_with_or(self):
        # With correct precedence: (not a.x > 10) or (a.x > 3) = True or True = True
        ev = ExpressionEvaluator({"a": {"x": 5}})
        assert ev.evaluate("not a.x > 10 or a.x > 3") is True


    def test_double_not(self):
        ev = ExpressionEvaluator({"a": {"x": 5}})
        assert ev.evaluate("not not a.x > 3") is True

    def test_chained_and(self):
        ExpressionEvaluator({'a': 1, 'b': 2, 'c': 3})
        # Can't directly chain since paths need dots, but test the logic
        ev2 = ExpressionEvaluator({"x": {"a": 1, "b": 2, "c": 3}})
        assert ev2.evaluate("x.a > 0 and x.b > 0 and x.c > 0") is True
        assert ev2.evaluate("x.a > 5 and x.b > 0 and x.c > 0") is False

    def test_chained_or(self):
        ev = ExpressionEvaluator({"x": {"a": 0, "b": 0, "c": 5}})
        assert ev.evaluate("x.a > 1 or x.b > 1 or x.c > 1") is True
        assert ev.evaluate("x.a > 1 or x.b > 1 or x.c > 10") is False


# ═══════════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════════


class TestEvaluatorEdgeCases:
    def test_missing_path_returns_false(self):
        ev = ExpressionEvaluator({"a": 1})
        assert ev.evaluate("missing.path > 0.5") is False

    def test_type_mismatch_returns_false(self):
        ev = ExpressionEvaluator({"a": {"s": "hello"}})
        # Comparing string "hello" > 0.5 should not crash
        assert ev.evaluate("a.s > 0.5") is False

    def test_string_comparison(self):
        ev = ExpressionEvaluator({"r": {"verdict": "pass"}})
        assert ev.evaluate('r.verdict == "pass"') is True
        assert ev.evaluate('r.verdict == "fail"') is False

    def test_negative_numbers(self):
        ev = ExpressionEvaluator({"r": {"dd": -0.15}})
        assert ev.evaluate("r.dd > -0.2") is True
        assert ev.evaluate("r.dd > -0.1") is False

    def test_single_quoted_literal(self):
        ev = ExpressionEvaluator({"r": {"v": "pass"}})
        assert ev.evaluate("r.v == 'pass'") is True

    def test_unparseable_expression_raises(self):
        ev = ExpressionEvaluator({})
        with pytest.raises(ValueError):
            ev.evaluate("??? this is not valid")

    def test_evaluate_condition_function(self):
        result = evaluate_condition(
            "data.quality > 0.8",
            {"data": {"quality": 0.9}},
        )
        assert result is True

    def test_evaluate_condition_false(self):
        result = evaluate_condition(
            "data.quality > 0.8",
            {"data": {"quality": 0.5}},
        )
        assert result is False


# ═══════════════════════════════════════════════════════════════════════
# _split_top_level with quotes
# ═══════════════════════════════════════════════════════════════════════


class TestSplitTopLevel:
    def test_split_or(self):
        ev = ExpressionEvaluator({})
        parts = ev._split_top_level("a > 1 or b > 2", "or")
        assert len(parts) == 2
        assert "a > 1" in parts[0]
        assert "b > 2" in parts[1]

    def test_split_and(self):
        ev = ExpressionEvaluator({})
        parts = ev._split_top_level("a > 1 and b > 2", "and")
        assert len(parts) == 2

    def test_no_split(self):
        ev = ExpressionEvaluator({})
        parts = ev._split_top_level("a > 1", "or")
        assert len(parts) == 1

    def test_split_with_quoted_string_containing_or(self):
        ev = ExpressionEvaluator({})
        expr = 'r.verdict == "pass or fail"'
        parts = ev._split_top_level(expr, "or")
        assert len(parts) == 1  # should NOT split inside quotes

    def test_split_with_quoted_string_containing_and(self):
        ev = ExpressionEvaluator({})
        expr = 'r.verdict == "this and that"'
        parts = ev._split_top_level(expr, "and")
        assert len(parts) == 1

    def test_split_multiple_or(self):
        ev = ExpressionEvaluator({})
        parts = ev._split_top_level("a > 1 or b > 2 or c > 3", "or")
        assert len(parts) == 3

    def test_split_preserves_case_insensitive(self):
        ev = ExpressionEvaluator({})
        parts = ev._split_top_level("a > 1 OR b > 2", "or")
        assert len(parts) == 2
