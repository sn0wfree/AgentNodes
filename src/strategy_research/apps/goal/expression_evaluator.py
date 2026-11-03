"""Expression DSL for workflow branch conditions (P3.7).

Wired into the Goal Workflow (2026-08-02): ``SwarmRuntime._apply_branches``
evaluates ``BranchConfig.condition`` against per-layer ``layer_results``
after each layer and applies ``skip`` / ``retry`` actions. ``redirect``
is parsed but not implemented (documented as future work). The
``layer_results`` shape is ``{agent_id: {"output": {field: val}}}`` built
by ``SwarmRuntime._build_layer_results`` from each agent's worker output
JSON; when ``answer`` is itself JSON its keys are merged up so
``agent_id.output.field`` resolves directly.

Evaluates expressions like:
  ``factor_analyst.output.sharpe < 0.3``
  ``risk_controller.output.verdict == "fail"``
  ``factor.ic > 0.1 and factor.sharpe > 1.0``
  ``not risk_controller.output.verdict == "fail"``

Supports:
  - Dot-path field access (agent_id.output.field)
  - Comparison operators: <, <=, >, >=, ==, !=
  - Boolean logic: and, or, not (precedence: not > and > or)
  - Numeric literals and string literals (quoted)

Usage:
    evaluator = ExpressionEvaluator(layer_results)
    result = evaluator.evaluate('factor_analyst.output.sharpe < 0.3')
    result = evaluator.evaluate('factor.ic > 0.1 and factor.sharpe > 1.0')
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ── Comparison operators ──────────────────────────────────────

_COMPARATORS = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}

# Regex: <path> <op> <literal>
_EXPR_RE = re.compile(
    r"""
    ^\s*
    ([a-zA-Z_][\w.]*?)        # left: dot-separated path
    \s*
    (<=|>=|!=|==|<|>)          # operator
    \s*
    (".*?"|'.*?'|[0-9.+-]+)   # right: string or number literal
    \s*$
    """,
    re.VERBOSE,
)

# Regex for a single atomic expression: path op literal
_ATOM_RE = re.compile(
    r"""
    ([a-zA-Z_][\w.]*?)        # left: dot-separated path
    \s*
    (<=|>=|!=|==|<|>)          # operator
    \s*
    (".*?"|'.*?'|[0-9.+-]+)   # right: string or number literal
    """,
    re.VERBOSE,
)


def _resolve_path(data: dict[str, Any], path: str) -> Any:
    """Resolve a dot-separated path like ``a.b.c`` in a nested dict."""
    parts = path.split(".")
    current = data
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
        if current is None:
            return None
    return current


def _parse_literal(raw: str) -> Any:
    """Parse a literal value: number or quoted string."""
    # Quoted string
    if (raw.startswith('"') and raw.endswith('"')) or \
       (raw.startswith("'") and raw.endswith("'")):
        return raw[1:-1]
    # Number
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        return raw


class ExpressionEvaluator:
    """Evaluates comparison expressions against layer results.

    Supports:
      - Simple comparison: ``path <op> <literal>``
      - Boolean logic: ``<expr> and <expr>``, ``<expr> or <expr>``
      - Prefix negation: ``not <expr>``
      - Boolean literals: ``true``, ``false``, ``1``, ``0``

    Operator precedence (highest to lowest): ``not`` > ``and`` > ``or``.
    """

    def __init__(self, layer_results: dict[str, Any]) -> None:
        self._results = layer_results

    def evaluate(self, expression: str) -> bool:
        """Evaluate an expression string. Returns bool result.

        Operator precedence (highest to lowest): ``not`` > ``and`` > ``or``.

        Raises:
            ValueError: If the expression cannot be parsed.
        """
        expr = expression.strip()

        # Handle simple boolean literals
        if expr.lower() in ("true", "1"):
            return True
        if expr.lower() in ("false", "0", ""):
            return False

        # ── Split on ``or`` first (lowest precedence) ──
        or_parts = self._split_top_level(expr, "or")
        if len(or_parts) > 1:
            return any(self.evaluate(part) for part in or_parts)

        # ── Split on ``and`` (middle precedence) ──
        and_parts = self._split_top_level(expr, "and")
        if len(and_parts) > 1:
            return all(self.evaluate(part) for part in and_parts)

        # ── Handle ``not`` prefix (highest precedence) ──
        if expr.lower().startswith("not "):
            inner = expr[4:].strip()
            return not self.evaluate(inner)

        # ── Single atomic comparison ──
        return self._evaluate_atom(expr)

    def _evaluate_atom(self, expr: str) -> bool:
        """Evaluate a single ``path <op> literal`` expression."""
        m = _ATOM_RE.search(expr)
        if m is None:
            raise ValueError(f"Cannot parse expression: {expr!r}")

        path, op, raw_value = m.group(1), m.group(2), m.group(3)
        expected = _parse_literal(raw_value)
        actual = _resolve_path(self._results, path)

        if actual is None:
            logger.debug("Path %r resolved to None in expression %r", path, expr)
            return False

        comparator = _COMPARATORS.get(op)
        if comparator is None:
            raise ValueError(f"Unknown operator: {op!r}")

        try:
            return comparator(actual, expected)
        except TypeError:
            logger.warning(
                "Type mismatch in %r: %r (%s) vs %r (%s)",
                expr, actual, type(actual).__name__,
                expected, type(expected).__name__,
            )
            return False

    @staticmethod
    def _split_top_level(expr: str, keyword: str) -> list[str]:
        """Split ``expr`` on ``keyword`` at the top level (outside quotes).

        Returns a list of 1+ parts. If ``keyword`` doesn't appear at the
        top level, returns ``[expr]`` (no split).

        Keyword must be surrounded by spaces or at start/end of expression,
        and must not be part of a longer word.
        """
        keyword_lower = keyword.lower()
        parts: list[str] = []
        current: list[str] = []
        in_quote: str | None = None
        i = 0
        n = len(keyword)
        expr_lower = expr.lower()

        while i < len(expr):
            ch = expr[i]

            # Track quote state
            if ch in ('"', "'") and in_quote is None:
                in_quote = ch
                current.append(ch)
                i += 1
                continue
            if ch == in_quote:
                in_quote = None
                current.append(ch)
                i += 1
                continue

            if in_quote is not None:
                current.append(ch)
                i += 1
                continue

            # Check for keyword at this position
            # Match `` keyword `` (with surrounding spaces) or
            # ``keyword `` at start or `` keyword`` at end.
            if expr_lower[i:i + n] == keyword_lower:
                # Check word boundaries
                before_ok = (i == 0 or not expr[i - 1].isalnum())
                after_pos = i + n
                after_ok = (after_pos >= len(expr) or not expr[after_pos].isalnum())
                if before_ok and after_ok:
                    parts.append("".join(current).strip())
                    current = []
                    i = after_pos
                    # Skip trailing space if present
                    if i < len(expr) and expr[i] == ' ':
                        i += 1
                    continue

            current.append(ch)
            i += 1

        remaining = "".join(current).strip()
        if remaining:
            parts.append(remaining)

        return parts if parts else [expr]


def evaluate_condition(
    expression: str,
    layer_results: dict[str, Any],
) -> bool:
    """Convenience function: evaluate a single expression."""
    evaluator = ExpressionEvaluator(layer_results)
    return evaluator.evaluate(expression)


__all__ = ["ExpressionEvaluator", "evaluate_condition"]
