"""P2-A: Scoped Tools + Guard Pipeline tests."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.kernel.agent.tools import ToolGuard, ToolRegistry, BaseTool


# ── Test tools ────────────────────────────────────────────────────


class _DummyTool(BaseTool):
    name = "dummy"
    description = "A dummy tool"
    parameters = {}

    def execute(self, **kwargs):
        return '{"status": "ok"}'


class _AnotherTool(BaseTool):
    name = "another"
    description = "Another tool"
    parameters = {}

    def execute(self, **kwargs):
        return '{"status": "ok"}'


class _DenyGuard:
    """Simple deny guard for testing."""

    def __init__(self, deny_list: list[str]):
        self._deny = set(deny_list)

    def check(self, name: str, params: Dict[str, Any]) -> Optional[str]:
        if name in self._deny:
            return f"Tool '{name}' is denied"
        return None


class _AlwaysDenyGuard:
    def check(self, name: str, params: Dict[str, Any]) -> Optional[str]:
        return "Always denied"


# ── ToolGuard protocol ───────────────────────────────────────────


class TestToolGuardProtocol:
    def test_satisfies_protocol(self):
        guard = _DenyGuard(["bad_tool"])
        assert isinstance(guard, ToolGuard)

    def test_check_returns_none_for_allowed(self):
        guard = _DenyGuard(["bad_tool"])
        assert guard.check("good_tool", {}) is None

    def test_check_returns_reason_for_denied(self):
        guard = _DenyGuard(["bad_tool"])
        assert guard.check("bad_tool", {}) is not None


# ── ToolRegistry.restrict ────────────────────────────────────────


class TestToolRegistryRestrict:
    def _make_registry(self):
        r = ToolRegistry()
        r.register(_DummyTool())
        r.register(_AnotherTool())
        return r

    def test_deny_removes_tools(self):
        r = self._make_registry()
        assert len(r) == 2
        r.restrict(deny=["dummy"])
        assert len(r) == 1
        assert r.get("dummy") is None
        assert r.get("another") is not None

    def test_allow_keeps_only_those(self):
        r = self._make_registry()
        r.restrict(allow=["dummy"])
        assert len(r) == 1
        assert r.get("dummy") is not None
        assert r.get("another") is None

    def test_deny_is_cumulative(self):
        r = self._make_registry()
        r.restrict(deny=["dummy"])
        r.restrict(deny=["another"])
        assert len(r) == 0

    def test_allow_replaces_previous(self):
        r = self._make_registry()
        r.restrict(allow=["dummy"])
        assert len(r) == 1
        # Second allow filters from current _tools (which only has "dummy")
        r.restrict(allow=["another"])
        # "another" was already removed by first allow, so result is empty
        assert len(r) == 0

    def test_restricted_returns_new_registry(self):
        r = self._make_registry()
        r2 = r.restricted(deny=["dummy"])
        # Original unchanged
        assert len(r) == 2
        # New has restriction
        assert len(r2) == 1


# ── ToolRegistry.guard ──────────────────────────────────────────


class TestToolRegistryGuard:
    def _make_registry(self):
        r = ToolRegistry()
        r.register(_DummyTool())
        r.register(_AnotherTool())
        return r

    def test_guard_allows_tool(self):
        r = self._make_registry()
        r.guard(_DenyGuard(["bad_tool"]))
        assert r.check_guards("dummy", {}) is None

    def test_guard_denies_tool(self):
        r = self._make_registry()
        r.guard(_DenyGuard(["dummy"]))
        reason = r.check_guards("dummy", {})
        assert reason is not None
        assert "denied" in reason.lower()

    def test_first_denial_wins(self):
        """Monotonic: first guard denial can't be overridden."""
        r = self._make_registry()
        r.guard(_DenyGuard(["dummy"]))
        r.guard(_AlwaysDenyGuard())  # Would deny everything, but first already denied
        reason = r.check_guards("dummy", {})
        assert reason is not None

    def test_denied_set_also_blocks(self):
        r = self._make_registry()
        r.restrict(deny=["dummy"])
        reason = r.check_guards("dummy", {})
        assert reason is not None
        assert "restriction" in reason.lower()

    def test_guard_exception_is_swallowed(self):
        """Guard that raises is skipped gracefully."""

        class _BrokenGuard:
            def check(self, name, params):
                raise RuntimeError("broken")

        r = self._make_registry()
        r.guard(_BrokenGuard())
        # Should not raise, just skip the broken guard
        assert r.check_guards("dummy", {}) is None
