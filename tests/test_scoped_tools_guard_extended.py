"""P2-A extended: Guard chain ordering + restricted() immutability tests."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentnodes.kernel.agent.tools import ToolGuard, ToolRegistry, BaseTool


# ── Test tools ────────────────────────────────────────────────────


class _ToolA(BaseTool):
    name = "tool_a"
    description = "Tool A"
    parameters = {}

    def execute(self, **kwargs):
        return '{"status": "ok"}'


class _ToolB(BaseTool):
    name = "tool_b"
    description = "Tool B"
    parameters = {}

    def execute(self, **kwargs):
        return '{"status": "ok"}'


class _ToolC(BaseTool):
    name = "tool_c"
    description = "Tool C"
    parameters = {}

    def execute(self, **kwargs):
        return '{"status": "ok"}'


class _DenyByName:
    def __init__(self, names: set[str]):
        self._names = names
        self.call_count = 0

    def check(self, name: str, params: Dict[str, Any]) -> Optional[str]:
        self.call_count += 1
        if name in self._names:
            return f"Denied: {name}"
        return None


class _AlwaysAllow:
    def check(self, name: str, params: Dict[str, Any]) -> Optional[str]:
        return None


class _AlwaysDeny:
    def check(self, name: str, params: Dict[str, Any]) -> Optional[str]:
        return "Always denied"


class _ConditionalDeny:
    """Deny based on params."""

    def check(self, name: str, params: Dict[str, Any]) -> Optional[str]:
        if params.get("dangerous"):
            return "Dangerous params"
        return None


# ── Guard chain ordering ─────────────────────────────────────────


class TestGuardChainOrdering:
    def test_first_denial_wins(self):
        """First guard that denies wins; later guards don't override."""
        r = ToolRegistry()
        r.register(_ToolA())
        r.guard(_DenyByName({"tool_a"}))
        r.guard(_AlwaysAllow())
        reason = r.check_guards("tool_a", {})
        assert reason is not None
        assert "Denied: tool_a" in reason

    def test_first_allow_then_deny(self):
        """Allow first, then deny — deny wins."""
        r = ToolRegistry()
        r.register(_ToolA())
        r.guard(_AlwaysAllow())
        r.guard(_DenyByName({"tool_a"}))
        reason = r.check_guards("tool_a", {})
        assert reason is not None

    def test_all_allow_means_no_denial(self):
        """All guards allow — no denial."""
        r = ToolRegistry()
        r.register(_ToolA())
        r.guard(_AlwaysAllow())
        r.guard(_AlwaysAllow())
        assert r.check_guards("tool_a", {}) is None


# ── restricted() immutability ────────────────────────────────────


class TestRestrictedImmutability:
    def _make_registry(self):
        r = ToolRegistry()
        r.register(_ToolA())
        r.register(_ToolB())
        r.register(_ToolC())
        return r

    def test_original_unchanged_after_restrict(self):
        r = self._make_registry()
        original_names = set(r.tool_names)
        r.restricted(deny={"tool_a"})
        assert set(r.tool_names) == original_names

    def test_restricted_has_deny(self):
        r = self._make_registry()
        r2 = r.restricted(deny={"tool_a"})
        assert "tool_a" not in r2.tool_names
        assert "tool_b" in r2.tool_names
        assert "tool_c" in r2.tool_names

    def test_restricted_has_allow(self):
        r = self._make_registry()
        r2 = r.restricted(allow={"tool_a", "tool_b"})
        assert set(r2.tool_names) == {"tool_a", "tool_b"}

    def test_restricted_combined(self):
        r = self._make_registry()
        r2 = r.restricted(deny={"tool_b"}, allow={"tool_a", "tool_b", "tool_c"})
        # deny happens first, then allow filters remaining
        assert "tool_b" not in r2.tool_names
        assert "tool_a" in r2.tool_names
        assert "tool_c" in r2.tool_names

    def test_restricted_independent_of_original(self):
        """Changing original after restricted doesn't affect the copy."""
        r = self._make_registry()
        r2 = r.restricted(deny={"tool_a"})
        r.register(_ToolA())  # re-register on original
        # r2 should still not have tool_a
        assert "tool_a" not in r2.tool_names


# ── Multiple guards interaction ──────────────────────────────────


class TestMultipleGuards:
    def test_multiple_guards_all_checked(self):
        """All guards are checked in order."""
        r = ToolRegistry()
        r.register(_ToolA())
        g1 = _DenyByName(set())
        g2 = _DenyByName(set())
        r.guard(g1)
        r.guard(g2)
        r.check_guards("tool_a", {})
        assert g1.call_count == 1
        assert g2.call_count == 1

    def test_early_deny_stops_chain(self):
        """First denial stops the guard chain (returns early)."""
        r = ToolRegistry()
        r.register(_ToolA())
        g1 = _DenyByName({"tool_a"})
        g2 = _DenyByName(set())
        r.guard(g1)
        r.guard(g2)
        r.check_guards("tool_a", {})
        # g1 denies, g2 is NOT called (early return)
        assert g1.call_count == 1
        assert g2.call_count == 0

    def test_conditional_guard_based_on_params(self):
        """Guard can deny based on tool parameters."""
        r = ToolRegistry()
        r.register(_ToolA())
        r.guard(_ConditionalDeny())
        # Safe params — allowed
        assert r.check_guards("tool_a", {"safe": True}) is None
        # Dangerous params — denied
        assert r.check_guards("tool_a", {"dangerous": True}) is not None

    def test_guard_exception_is_swallowed(self):
        """Guard that raises is skipped, doesn't block others."""

        class _BrokenGuard:
            def check(self, name, params):
                raise ValueError("broken")

        r = ToolRegistry()
        r.register(_ToolA())
        r.guard(_BrokenGuard())
        r.guard(_AlwaysAllow())
        # Should not raise, broken guard skipped
        assert r.check_guards("tool_a", {}) is None

    def test_denied_set_plus_guards(self):
        """restrict(deny=...) and guard() work together."""
        r = ToolRegistry()
        r.register(_ToolA())
        r.register(_ToolB())
        r.restrict(deny={"tool_a"})
        r.guard(_DenyByName({"tool_b"}))
        # tool_a denied by restrict
        assert r.check_guards("tool_a", {}) is not None
        # tool_b denied by guard
        assert r.check_guards("tool_b", {}) is not None
