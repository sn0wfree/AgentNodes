"""Tests for the core SwarmRuntime API types + back-compat shim.

The canonical home for these types is ``core.swarm.types`` (next to
SwarmRuntime). ``core.workflow.types`` is kept as a thin re-export
shim for backward compatibility — both layers are tested here.
"""
from __future__ import annotations

import pytest

from strategy_research.kernel.swarm.types import AgentCall, AgentStatus, SwarmHook


# ── AgentStatus ─────────────────────────────────────────────────────


class TestAgentStatus:
    def test_values(self):
        assert AgentStatus.PENDING == "pending"
        assert AgentStatus.RUNNING == "running"
        assert AgentStatus.SUCCESS == "success"
        assert AgentStatus.ERROR == "error"
        assert AgentStatus.SKIPPED == "skipped"
        assert AgentStatus.AWAITING == "awaiting"

    def test_string_comparison(self):
        assert AgentStatus.SUCCESS == "success"
        assert AgentStatus("pending") == AgentStatus.PENDING
        # str-enum round-trip: any string equals itself
        assert AgentStatus("awaiting") == AgentStatus.AWAITING

    def test_unknown_string_raises(self):
        with pytest.raises(ValueError):
            AgentStatus("not_a_real_status")

    def test_is_frozen(self):
        with pytest.raises(AttributeError):
            AgentStatus.PENDING = "changed"


# ── AgentCall ───────────────────────────────────────────────────────


class TestAgentCall:
    def test_basic_creation(self):
        call = AgentCall(agent_name="researcher", prompt="test prompt")
        assert call.agent_name == "researcher"
        assert call.prompt == "test prompt"
        assert call.context == {}
        assert call.metadata == {}

    def test_with_context(self):
        call = AgentCall(
            agent_name="strategist",
            prompt="generate",
            context={"upstream": {"data": "value"}},
            metadata={"round": 3},
        )
        assert call.context == {"upstream": {"data": "value"}}
        assert call.metadata == {"round": 3}

    def test_default_factories_are_independent(self):
        """Two AgentCalls should not share the same context/metadata dict."""
        a = AgentCall(agent_name="a", prompt="x")
        b = AgentCall(agent_name="b", prompt="y")
        a.context["key"] = "value"
        assert "key" not in b.context

    def test_frozen(self):
        call = AgentCall(agent_name="a", prompt="p")
        with pytest.raises(AttributeError):
            call.agent_name = "b"
        with pytest.raises(AttributeError):
            call.context = {}


# ── SwarmHook protocol ─────────────────────────────────────────────


class _RecordingHook:
    """Concrete SwarmHook that records every callback invocation."""

    def __init__(self, name: str = "test") -> None:
        self._name = name
        self.calls: list[tuple[str, tuple, dict]] = []

    @property
    def name(self) -> str:
        return self._name

    def on_layer_start(self, layer_idx, agents, context):
        self.calls.append(("on_layer_start", (layer_idx, tuple(agents)), dict(context)))

    def on_layer_complete(self, layer_idx, agents, results):
        self.calls.append(("on_layer_complete", (layer_idx, tuple(agents)), dict(results)))

    def on_agent_complete(self, agent_id, result, context):
        self.calls.append(("on_agent_complete", (agent_id, result), dict(context)))

    def should_stop(self) -> bool:
        return False


class TestSwarmHook:
    def test_isinstance_satisfied_by_concrete_impl(self):
        """A class implementing all required methods satisfies the SwarmHook
        structural type. (Protocol structural check: attribute presence.)"""
        hook = _RecordingHook()
        # SwarmHook defines name, on_layer_start, on_layer_complete,
        # on_agent_complete, should_stop. _RecordingHook has all of them.
        for attr in ("name", "on_layer_start", "on_layer_complete",
                     "on_agent_complete", "should_stop"):
            assert hasattr(hook, attr), f"missing required attribute: {attr}"

    def test_concrete_impl_callable(self):
        """A well-formed impl can be instantiated and invoked."""
        hook = _RecordingHook(name="alpha")
        hook.on_layer_start(0, ["x"], {})
        hook.on_agent_complete("x", {"ok": True}, {})
        assert len(hook.calls) == 2
        assert hook.name == "alpha"

    def test_default_should_stop_from_protocol_body(self):
        """The Protocol body defines a default should_stop that returns False."""
        # Read the Protocol's default method directly
        import inspect
        src = inspect.getsource(SwarmHook)
        assert "should_stop" in src
        # The default body is `return False`
        assert "return False" in src

    def test_methods_are_in_protocol_source(self):
        """All expected callbacks are declared on the SwarmHook Protocol."""
        import inspect
        src = inspect.getsource(SwarmHook)
        for method in ("on_layer_start", "on_layer_complete",
                      "on_agent_complete", "should_stop"):
            assert f"def {method}" in src, f"missing method: {method}"


class TestSwarmHookCallbacks:
    def test_on_layer_start_records_args(self):
        hook = _RecordingHook()
        hook.on_layer_start(0, ["a", "b"], {"k": "v"})
        assert len(hook.calls) == 1
        kind, args, kw = hook.calls[0]
        assert kind == "on_layer_start"
        assert args == (0, ("a", "b"))
        assert kw == {"k": "v"}

    def test_on_layer_complete_records_results(self):
        hook = _RecordingHook()
        hook.on_layer_complete(1, ["x"], {"x": {"status": "success"}})
        kind, args, kw = hook.calls[0]
        assert kind == "on_layer_complete"
        assert args == (1, ("x",))
        assert kw == {"x": {"status": "success"}}

    def test_on_agent_complete_records_id_and_result(self):
        hook = _RecordingHook()
        hook.on_agent_complete("researcher", {"answer": "x"}, {"ctx": 1})
        kind, args, kw = hook.calls[0]
        assert kind == "on_agent_complete"
        assert args == ("researcher", {"answer": "x"})
        assert kw == {"ctx": 1}


# ── workflow.types back-compat shim ────────────────────────────────


class TestWorkflowTypesShim:
    def test_re_exports_swarm_types(self):
        from strategy_research.core.workflow import types as shim
        # Same class objects, not just compatible types
        assert shim.AgentCall is AgentCall
        assert shim.AgentStatus is AgentStatus
        assert shim.SwarmHook is SwarmHook

    def test_no_legacy_types(self):
        """RoundResult / SwarmTask were removed; the shim must not export
        them any more."""
        from strategy_research.core.workflow import types as shim
        assert not hasattr(shim, "RoundResult")
        assert not hasattr(shim, "SwarmTask")

    def test_package_level_imports(self):
        """Importing from the package root still works (used by tests)."""
        from strategy_research.core.workflow import (
            AgentCall as PkgAgentCall,
            AgentStatus as PkgAgentStatus,
            SwarmHook as PkgSwarmHook,
        )
        assert PkgAgentCall is AgentCall
        assert PkgAgentStatus is AgentStatus
        assert PkgSwarmHook is SwarmHook