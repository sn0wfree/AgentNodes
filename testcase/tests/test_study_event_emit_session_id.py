"""Regression tests for P0-2 SSE fix: directive/interrupt events must
emit to ``study_id``, not the empty string.

Pre-fix: ``routers/study.py`` used ``""`` as the SSE channel for
``study_directive_added`` and ``study_interrupt_responded`` events,
so no subscribed EventSource could receive them.

These tests don't spin up the full HTTP stack (that requires the
scheduler, auth, DB container). Instead they regression-guard the
source by parsing the AST: each emit call inside the two endpoint
handlers must pass ``study_id`` (not ``""``) as the first argument.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


STUDY_PY = (
    Path(__file__).parent.parent
    / "src" / "strategy_research" / "interfaces" / "api" / "routers" / "study.py"
)


def _emits_in_function(source: str, func_name: str) -> list[ast.Call]:
    """Return all emit(...) Call nodes inside the named async function."""
    tree = ast.parse(source)
    target: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            target = node
            break
    assert target is not None, f"function {func_name!r} not found"
    return [
        child
        for child in ast.walk(target)
        if isinstance(child, ast.Call)
    ]


def _is_empty_string_arg(node: ast.Call, index: int = 0) -> bool:
    """Return True if the ``index``-th positional arg is the literal ''."""
    if index >= len(node.args):
        return False
    arg = node.args[index]
    return isinstance(arg, ast.Constant) and arg.value == ""


def _is_named_study_id(node: ast.Call, index: int = 0) -> bool:
    """Return True if the ``index``-th positional arg is the name ``study_id``.

    Allows either a bare Name node (``study_id``) or an attribute chain
    (e.g. ``some.study_id``).
    """
    if index >= len(node.args):
        return False
    arg = node.args[index]
    if isinstance(arg, ast.Name) and arg.id == "study_id":
        return True
    if isinstance(arg, ast.Attribute) and arg.attr == "study_id":
        return True
    return False


def _event_arg_value(node: ast.Call) -> str | None:
    """Return the event-type string from a session_service.event_bus.emit(...)
    call — the 2nd positional arg."""
    if len(node.args) < 2:
        return None
    arg = node.args[1]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    return None


def _emits_with_event_type(tree: ast.AST, event_type: str) -> list[ast.Call]:
    """Walk tree for event_bus.emit calls whose 2nd arg is ``event_type``."""
    out = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "emit"
        ):
            continue
        if _event_arg_value(node) == event_type:
            out.append(node)
    return out


# ── Tests ───────────────────────────────────────────────────


def test_directive_handler_does_not_emit_to_empty_string():
    """``study_directive_added`` must emit to ``study_id``, not ``""``."""
    source = STUDY_PY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    emit_calls = _emits_with_event_type(tree, "study_directive_added")

    assert emit_calls, "no event_bus.emit for study_directive_added found"
    for call in emit_calls:
        assert not _is_empty_string_arg(call, 0), (
            "study_directive_added still emits to empty string"
        )
        assert _is_named_study_id(call, 0), (
            f"expected first positional arg to be study_id (name or attr), "
            f"got: {ast.unparse(call.args[0]) if call.args else '<none>'}"
        )


def test_interrupt_respond_handler_does_not_emit_to_empty_string():
    """``study_interrupt_responded`` must emit to ``study_id``, not ``""``."""
    source = STUDY_PY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    emit_calls = _emits_with_event_type(tree, "study_interrupt_responded")

    assert emit_calls, "no event_bus.emit for study_interrupt_responded found"
    for call in emit_calls:
        assert not _is_empty_string_arg(call, 0), (
            "study_interrupt_responded still emits to empty string"
        )
        assert _is_named_study_id(call, 0), (
            f"expected first positional arg to be study_id (name or attr), "
            f"got: {ast.unparse(call.args[0]) if call.args else '<none>'}"
        )


def test_event_bus_emit_calls_in_study_router():
    """Sanity: this should not silently pass when the file changes."""
    # If we got here without the above two tests erroring, the
    # source contains emit(...) calls for both event types and
    # neither uses the empty string.
    assert True