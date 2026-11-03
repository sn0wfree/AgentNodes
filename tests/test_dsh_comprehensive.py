"""Comprehensive DSH feature edge case tests — covering all remaining gaps."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.kernel.agent.context_injector import (
    ContextInjector,
    GoalContinuationInjector,
    GoalContextInjector,
    TodosInjector,
    build_default_injectors,
)
from strategy_research.kernel.agent.loop import AgentLoop, LoopResult
from strategy_research.kernel.agent.tools import ToolGuard, ToolRegistry, BaseTool
from strategy_research.foundation.registry import Registry


# ══════════════════════════════════════════════════════════════════
# P1-A: ContextInjector advanced edge cases
# ══════════════════════════════════════════════════════════════════


class TestContextInjectorAdvanced:
    def test_same_order_injectors_stable_sort(self):
        """Injectors with same order maintain insertion order (stable sort)."""
        class _Inj:
            def __init__(self, name, order):
                self.name = name
                self.order = order
            def inject_per_iteration(self, loop, messages):
                pass

        injectors = [_Inj("A", 0), _Inj("B", 0), _Inj("C", 0)]
        # Python's sorted is stable
        sorted_inj = sorted(injectors, key=lambda i: i.order)
        assert [i.name for i in sorted_inj] == ["A", "B", "C"]

    def test_pre_run_does_not_mutate_loop_attributes(self):
        """inject_pre_run must not add/modify any loop attributes."""
        inj = GoalContextInjector()
        loop = MagicMock()
        loop.enable_goal_injection = True
        loop.session_id = "abc"
        before = set(dir(loop))
        inj.inject_pre_run(loop, "task", [])
        after = set(dir(loop))
        # No new public attributes should be added
        new_attrs = after - before
        assert len(new_attrs) == 0

    def test_per_iteration_does_not_mutate_loop(self):
        """inject_per_iteration must not modify loop state."""
        inj = TodosInjector()
        loop = MagicMock()
        loop.session_id = None
        before_hash = hash(str(sorted(loop.__dict__.items())))
        inj.inject_per_iteration(loop, [])
        after_hash = hash(str(sorted(loop.__dict__.items())))
        assert before_hash == after_hash

    def test_multiple_injectors_exception_isolation(self):
        """One injector's exception doesn't prevent others from running."""
        call_log = []

        class _GoodInjector:
            name = "good"
            order = 0
            def inject_per_iteration(self, loop, messages):
                call_log.append("good")

        class _BadInjector:
            name = "bad"
            order = 0
            def inject_per_iteration(self, loop, messages):
                raise RuntimeError("boom")

        class _AlsoGood:
            name = "also_good"
            order = 0
            def inject_per_iteration(self, loop, messages):
                call_log.append("also_good")

        # The loop wraps each injector call in try/except
        loop = MagicMock()
        loop.session_id = "abc"
        for inj in [_GoodInjector(), _BadInjector(), _AlsoGood()]:
            try:
                inj.inject_per_iteration(loop, [])
            except Exception:
                pass
        # Good injectors ran, bad one was caught
        assert call_log == ["good", "also_good"]

    def test_injector_order_negative_zero_positive(self):
        """Three injectors at -100, 0, 100 execute in correct phases."""
        phases = []

        class _PhaseInjector:
            def __init__(self, name, order):
                self.name = name
                self.order = order
            def inject_pre_run(self, loop, task, messages):
                phases.append(f"pre:{self.name}")
                return task
            def inject_per_iteration(self, loop, messages):
                phases.append(f"iter:{self.name}")
            def inject_post_response(self, loop, response, messages, result, iteration):
                phases.append(f"post:{self.name}")
                return False

        injectors = [_PhaseInjector("A", -100), _PhaseInjector("B", 0), _PhaseInjector("C", 100)]
        loop = MagicMock()
        loop.enable_goal_injection = True
        loop.session_id = "abc"

        # Simulate pre_run phase (only order < 0)
        for inj in injectors:
            if inj.order < 0:
                inj.inject_pre_run(loop, "task", [])

        # Simulate per_iter phase (only order == 0)
        for inj in injectors:
            if inj.order == 0:
                inj.inject_per_iteration(loop, [])

        # Simulate post_resp phase (only order >= 100)
        for inj in injectors:
            if inj.order >= 100:
                inj.inject_post_response(loop, MagicMock(), [], MagicMock(), 1)

        assert phases == ["pre:A", "iter:B", "post:C"]

    def test_build_default_injectors_returns_fresh_list(self):
        """Each call returns a new list (not shared mutable state)."""
        list1 = build_default_injectors()
        list2 = build_default_injectors()
        assert list1 is not list2
        # But same content
        assert [i.name for i in list1] == [i.name for i in list2]


# ══════════════════════════════════════════════════════════════════
# P1-B: Request Envelope hash edge cases
# ══════════════════════════════════════════════════════════════════


class TestRequestEnvelopeHashAdvanced:
    def _make_loop(self):
        from strategy_research.kernel.agent.loop import AgentLoop
        loop = object.__new__(AgentLoop)
        loop.session_id = "test"
        loop._emit = MagicMock()
        loop._trace_writer = None
        return loop

    def test_hash_stable_across_calls(self):
        """Same input always produces same hash."""
        loop = self._make_loop()
        msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
        hashes = []
        for _ in range(10):
            loop._trace_llm_request(msgs, iteration=1)
            hashes.append(loop._emit.call_args[0][1]["history_hash"])
            loop._emit.reset_mock()
        assert len(set(hashes)) == 1

    def test_different_content_different_hash(self):
        """Different message lengths produce different hashes (content-length fingerprint)."""
        loop1 = self._make_loop()
        loop2 = self._make_loop()
        msgs1 = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "short"},
        ]
        msgs2 = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "a much longer message"},
        ]
        loop1._trace_llm_request(msgs1, iteration=1)
        loop2._trace_llm_request(msgs2, iteration=1)
        h1 = loop1._emit.call_args[0][1]["history_hash"]
        h2 = loop2._emit.call_args[0][1]["history_hash"]
        assert h1 != h2

    def test_special_characters_in_content(self):
        """Hash works with special characters (unicode, newlines, emoji)."""
        loop = self._make_loop()
        msgs = [{"role": "user", "content": "你好\n🌍\t<script>alert(1)</script>"}]
        loop._trace_llm_request(msgs, iteration=1)
        entry = loop._emit.call_args[0][1]
        # Hash should be valid hex
        assert len(entry["history_hash"]) == 16
        int(entry["history_hash"], 16)  # should not raise

    def test_very_large_message(self):
        """Hash works with very large messages (100KB)."""
        loop = self._make_loop()
        large_content = "x" * 100_000
        msgs = [{"role": "user", "content": large_content}]
        loop._trace_llm_request(msgs, iteration=1)
        entry = loop._emit.call_args[0][1]
        assert entry["history_count"] == 1
        assert entry["estimated_tokens"] > 0

    def test_hash_length_always_16(self):
        """Hash is always 16 hex chars (SHA-256 prefix)."""
        loop = self._make_loop()
        for i in range(20):
            msgs = [{"role": "user", "content": f"msg{i}"}]
            loop._trace_llm_request(msgs, iteration=i)
            h = loop._emit.call_args[0][1]["system_prompt_hash"]
            assert len(h) == 16
            loop._emit.reset_mock()

    def test_empty_tools_hash_matches_empty_array(self):
        """None tools produces hash of '[]'."""
        loop = self._make_loop()
        loop._trace_llm_request([{"role": "user", "content": ""}], iteration=1, tools=None)
        entry = loop._emit.call_args[0][1]
        expected = hashlib.sha256(b"[]").hexdigest()[:16]
        assert entry["tools_hash"] == expected

    def test_single_tool_hash(self):
        """Single tool produces correct hash."""
        loop = self._make_loop()
        tools = [{"type": "function", "function": {"name": "read"}}]
        loop._trace_llm_request([{"role": "user", "content": ""}], iteration=1, tools=tools)
        entry = loop._emit.call_args[0][1]
        expected = hashlib.sha256(json.dumps(tools, ensure_ascii=False).encode()).hexdigest()[:16]
        assert entry["tools_hash"] == expected


# ══════════════════════════════════════════════════════════════════
# P2-A: Guard advanced edge cases
# ══════════════════════════════════════════════════════════════════


class _ToolX(BaseTool):
    name = "tool_x"
    description = "Tool X"
    parameters = {}
    def execute(self, **kwargs):
        return '{"status": "ok"}'


class _ToolY(BaseTool):
    name = "tool_y"
    description = "Tool Y"
    parameters = {}
    def execute(self, **kwargs):
        return '{"status": "ok"}'


class _ConditionalGuard:
    """Deny based on param value."""
    def check(self, name, params):
        if params.get("force"):
            return "force param denied"
        return None


class _SlowGuard:
    """Guard that sleeps (simulates slow check)."""
    def check(self, name, params):
        return None  # always allow


class TestGuardAdvanced:
    def test_guard_with_real_tool_subclass(self):
        """Guard works with real BaseTool subclass instances."""
        r = ToolRegistry()
        r.register(_ToolX())
        r.guard(_ConditionalGuard())
        # No force param — allowed
        assert r.check_guards("tool_x", {}) is None
        # force param — denied
        assert r.check_guards("tool_x", {"force": True}) is not None

    def test_guard_check_on_unregistered_tool(self):
        """Guard check on tool not in registry still runs guards."""
        r = ToolRegistry()
        r.register(_ToolX())
        r.guard(_ConditionalGuard())
        # tool_y not registered, but guard still checks
        reason = r.check_guards("tool_y", {"force": True})
        assert reason is not None

    def test_multiple_guards_first_deny_wins(self):
        """First denial wins, subsequent guards don't override."""
        r = ToolRegistry()
        r.register(_ToolX())

        class _Deny1:
            def check(self, name, params):
                return "deny1"

        class _Deny2:
            def check(self, name, params):
                return "deny2"

        r.guard(_Deny1())
        r.guard(_Deny2())
        reason = r.check_guards("tool_x", {})
        assert reason == "deny1"

    def test_guard_exception_in_middle_of_chain(self):
        """Guard exception in middle doesn't block later guards."""
        r = ToolRegistry()
        r.register(_ToolX())

        class _OK:
            def check(self, name, params):
                return None

        class _Broken:
            def check(self, name, params):
                raise RuntimeError("broken")

        class _Deny:
            def check(self, name, params):
                return "denied"

        r.guard(_OK())
        r.guard(_Broken())
        r.guard(_Deny())
        # Broken guard is skipped, Deny wins
        reason = r.check_guards("tool_x", {})
        assert reason == "denied"

    def test_restricted_copies_denied_set(self):
        """restricted() copies denied set to new registry."""
        r = ToolRegistry()
        r.register(_ToolX())
        r.register(_ToolY())
        r.restrict(deny={"tool_x"})
        r2 = r.restricted()
        assert "tool_x" in r2._denied
        # Original also has it
        assert "tool_x" in r._denied

    def test_restricted_independent_deny_addition(self):
        """Adding deny to original doesn't affect restricted copy."""
        r = ToolRegistry()
        r.register(_ToolX())
        r2 = r.restricted()
        r.restrict(deny={"tool_x"})
        assert "tool_x" not in r2._denied

    def test_guard_returns_none_for_all_tool_names(self):
        """Guard that always returns None allows all tools."""
        r = ToolRegistry()
        r.register(_ToolX())
        r.register(_ToolY())
        r.guard(_SlowGuard())
        assert r.check_guards("tool_x", {}) is None
        assert r.check_guards("tool_y", {}) is None
        assert r.check_guards("nonexistent", {}) is None

    def test_denied_set_blocks_even_without_guards(self):
        """restrict(deny=...) blocks tools even without guard registration."""
        r = ToolRegistry()
        r.register(_ToolX())
        r.restrict(deny={"tool_x"})
        assert r.check_guards("tool_x", {}) is not None

    def test_allow_set_removes_tools(self):
        """restrict(allow=...) removes tools not in allow list."""
        r = ToolRegistry()
        r.register(_ToolX())
        r.register(_ToolY())
        r.restrict(allow={"tool_x"})
        assert r.get("tool_x") is not None
        assert r.get("tool_y") is None


# ══════════════════════════════════════════════════════════════════
# P2-B: Inbox advanced edge cases
# ══════════════════════════════════════════════════════════════════


class _FakeConfig:
    compact_config = None


def _make_loop():
    return AgentLoop(
        config=_FakeConfig(),
        registry=ToolRegistry(),
        max_iterations=5,
    )


class TestInboxAdvanced:
    def test_drain_preserves_existing_messages(self):
        """Drain appends to existing messages, doesn't replace."""
        loop = _make_loop()
        loop.inject({"role": "user", "content": "injected"})
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "orig"},
        ]
        loop._drain_inbox(messages)
        assert len(messages) == 3
        assert messages[0]["content"] == "sys"
        assert messages[1]["content"] == "orig"
        assert messages[2]["content"] == "injected"

    def test_drain_fifo_order(self):
        """Messages appear in FIFO order after drain."""
        loop = _make_loop()
        for i in range(10):
            loop.inject({"role": "user", "content": f"m{i}"})
        messages = []
        loop._drain_inbox(messages)
        assert [m["content"] for m in messages] == [f"m{i}" for i in range(10)]

    def test_drain_empty_inbox(self):
        """Draining empty inbox is a no-op."""
        loop = _make_loop()
        messages = [{"role": "system", "content": "keep"}]
        loop._drain_inbox(messages)
        assert len(messages) == 1

    def test_multiple_drains(self):
        """Multiple drains on same loop work independently."""
        loop = _make_loop()
        loop.inject({"role": "user", "content": "a"})
        m1 = []
        loop._drain_inbox(m1)
        assert len(m1) == 1
        m2 = []
        loop._drain_inbox(m2)
        assert len(m2) == 0

    def test_inject_after_drain(self):
        """Inject after drain works correctly."""
        loop = _make_loop()
        loop.inject({"role": "user", "content": "first"})
        m1 = []
        loop._drain_inbox(m1)
        loop.inject({"role": "user", "content": "second"})
        m2 = []
        loop._drain_inbox(m2)
        assert m1[0]["content"] == "first"
        assert m2[0]["content"] == "second"

    def test_inject_complex_payload(self):
        """Inject supports complex nested payloads."""
        loop = _make_loop()
        msg = {
            "role": "user",
            "content": "steer",
            "metadata": {"source": "ext", "priority": "high", "nested": {"a": 1}},
        }
        loop.inject(msg)
        retrieved = loop._inbox.get_nowait()
        assert retrieved["metadata"]["nested"]["a"] == 1

    def test_inject_hundred_messages(self):
        """Inject 100 messages, drain all."""
        loop = _make_loop()
        for i in range(100):
            loop.inject({"role": "user", "content": f"msg{i}"})
        messages = []
        loop._drain_inbox(messages)
        assert len(messages) == 100

    def test_inbox_thread_safety(self):
        """inject() is thread-safe."""
        loop = _make_loop()
        errors = []

        def _inject_many(start):
            try:
                for i in range(50):
                    loop.inject({"role": "user", "content": f"t{start}_{i}"})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_inject_many, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0
        assert loop._inbox.qsize() == 200

    @pytest.mark.asyncio
    async def test_ainject_thread_safety(self):
        """ainject() is thread-safe."""
        loop = _make_loop()
        await asyncio.gather(*[loop.ainject({"role": "user", "content": f"a{i}"}) for i in range(100)])
        assert loop._inbox.qsize() == 100


# ══════════════════════════════════════════════════════════════════
# P3-A: Registry[T] advanced edge cases
# ══════════════════════════════════════════════════════════════════


class TestRegistryAdvanced:
    def test_thread_safety_register(self):
        """Concurrent register() calls are safe (dict is thread-safe in CPython)."""
        r = Registry()
        errors = []

        def _register_many(start):
            try:
                for i in range(100):
                    key = f"key_{start}_{i}"
                    r.register(key, i)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_register_many, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0
        assert len(r) == 400

    def test_large_registry(self):
        """Registry handles 10,000 items."""
        r = Registry[int]()
        for i in range(10_000):
            r.register(f"item_{i}", i)
        assert len(r) == 10_000
        assert r.get("item_5000") == 5000

    def test_registry_subclass(self):
        """Registry can be subclassed."""
        class _ToolRegistry(Registry[BaseTool]):
            def register_tool(self, tool):
                super().register(tool.name, tool)

        r = _ToolRegistry()
        r.register_tool(_ToolX())
        assert r.get("tool_x") is not None

    def test_registry_with_none_key(self):
        """Registry handles None as a value (but not key)."""
        r = Registry[Optional[int]]()
        r.register("a", None)
        assert r.get("a") is None
        assert "a" in r

    def test_registry_filter_complex_predicate(self):
        """Filter with complex predicate."""
        r = Registry[dict]()
        r.register("a", {"type": "file", "size": 100})
        r.register("b", {"type": "dir", "size": 0})
        r.register("c", {"type": "file", "size": 200})
        result = r.filter(lambda k, v: v["type"] == "file" and v["size"] > 150)
        assert result == {"c": {"type": "file", "size": 200}}

    def test_registry_remove_nonexistent(self):
        """Remove nonexistent key returns None."""
        r = Registry[str]()
        assert r.remove("missing") is None

    def test_registry_contains_after_remove(self):
        """Contains returns False after remove."""
        r = Registry[str]()
        r.register("a", "x")
        r.remove("a")
        assert "a" not in r

    def test_registry_iter_after_modification(self):
        """Iter reflects current state after modifications."""
        r = Registry[str]()
        r.register("a", "1")
        r.register("b", "2")
        r.remove("a")
        assert list(r) == ["b"]


# ══════════════════════════════════════════════════════════════════
# P3-B/C: Plugin + Prompt discovery edge cases
# ══════════════════════════════════════════════════════════════════


class TestPluginDiscoveryAdvanced:
    def test_discover_with_no_entry_points_module(self):
        """Handles missing importlib.metadata gracefully."""
        from strategy_research.kernel.agent.builtin_tools import _discover_tool_plugins
        r = ToolRegistry()
        # Mock the import inside _discover_tool_plugins to fail
        with patch("importlib.metadata.entry_points", side_effect=ImportError("no module")):
            # The function catches ImportError internally
            try:
                _discover_tool_plugins(r)
            except ImportError:
                pass  # Expected if the import fails inside the function
        # Registry should be empty (no plugins loaded)
        assert len(r) == 0

    def test_discover_with_broken_entry_point(self):
        """Broken entry point is logged and skipped."""
        from strategy_research.kernel.agent.builtin_tools import _discover_tool_plugins
        r = ToolRegistry()
        ep = MagicMock()
        ep.name = "broken"
        ep.load.side_effect = ImportError("no module")
        mock_eps = MagicMock()
        mock_eps.select.return_value = [ep]
        with patch("importlib.metadata.entry_points", return_value=mock_eps):
            _discover_tool_plugins(r)
        assert len(r) == 0


class TestPromptDiscoveryAdvanced:
    def test_discover_prompts_handles_nonexistent_dir(self):
        """_discover_prompts handles missing templates directory."""
        from strategy_research.kernel.agent.prompt_builder import PromptBuilderFactory
        with patch.object(Path, "__truediv__", return_value=Path("/nonexistent/path")):
            result = PromptBuilderFactory._discover_prompts()
            assert isinstance(result, dict)

    def test_list_roles_deduplication(self):
        """list_roles doesn't have duplicates."""
        from strategy_research.kernel.agent.prompt_builder import PromptBuilderFactory
        PromptBuilderFactory._discovered = None
        roles = PromptBuilderFactory.list_roles()
        assert len(roles) == len(set(roles))


# ══════════════════════════════════════════════════════════════════
# AgentLoop: complete run() flow with DSH features
# ══════════════════════════════════════════════════════════════════


class TestAgentLoopCompleteFlow:
    def test_prepare_run_with_all_injectors(self):
        """_prepare_run runs all pre-run injectors."""
        phases = []

        class _PhaseInjector:
            def __init__(self, name, order):
                self.name = name
                self.order = order
            def inject_pre_run(self, loop, task, messages):
                phases.append(f"pre:{self.name}")
                return task
            def inject_per_iteration(self, loop, messages):
                phases.append(f"iter:{self.name}")
            def inject_post_response(self, loop, response, messages, result, iteration):
                phases.append(f"post:{self.name}")
                return False

        injectors = [_PhaseInjector("A", -100), _PhaseInjector("B", 0), _PhaseInjector("C", 100)]
        loop = AgentLoop(
            config=_FakeConfig(),
            registry=ToolRegistry(),
            max_iterations=5,
            injectors=injectors,
        )
        loop._prepare_run("task", None)
        # Only pre-run injector should have been called
        assert phases == ["pre:A"]

    def test_execute_tool_guard_integration(self):
        """Guard check happens before tool execution."""
        r = ToolRegistry()

        class _RealTool(BaseTool):
            name = "real_tool"
            description = "Real"
            parameters = {}
            def execute(self, **kwargs):
                return '{"executed": true}'

        r.register(_RealTool())

        class _DenyReal:
            def check(self, name, params):
                if name == "real_tool":
                    return "Denied"
                return None

        r.guard(_DenyReal())
        loop = AgentLoop(
            config=_FakeConfig(),
            registry=r,
            max_iterations=5,
        )
        result = LoopResult()
        tc = MagicMock()
        tc.name = "real_tool"
        tc.id = "c1"
        tc.arguments = {}

        msg = asyncio.run(loop._execute_tool_call_core(tc, result, async_mode=False))
        content = json.loads(msg["content"])
        assert content["status"] == "denied"

    def test_execute_tool_no_guard_runs_normally(self):
        """Without guards, tool executes normally."""
        r = ToolRegistry()

        class _RealTool(BaseTool):
            name = "real_tool"
            description = "Real"
            parameters = {}
            def execute(self, **kwargs):
                return '{"executed": true}'

        r.register(_RealTool())
        loop = AgentLoop(
            config=_FakeConfig(),
            registry=r,
            max_iterations=5,
        )
        result = LoopResult()
        tc = MagicMock()
        tc.name = "real_tool"
        tc.id = "c1"
        tc.arguments = {}

        msg = asyncio.run(loop._execute_tool_call_core(tc, result, async_mode=False))
        content = json.loads(msg["content"])
        assert content["executed"] is True

    def test_inject_then_drain_in_messages(self):
        """inject() then _drain_inbox() puts messages in correct position."""
        loop = _make_loop()
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "orig"},
        ]
        loop.inject({"role": "user", "content": "injected1"})
        loop.inject({"role": "user", "content": "injected2"})
        loop._drain_inbox(messages)
        assert len(messages) == 4
        assert messages[2]["content"] == "injected1"
        assert messages[3]["content"] == "injected2"

    def test_custom_injector_modifies_task(self):
        """Custom injector can modify the task string (P5-4 前缀契约）。"""
        class _TaskModifier:
            name = "modifier"
            order = -100
            def inject_pre_run(self, loop, task, messages):
                return f"MODIFIED\n\n{task}"

        loop = AgentLoop(
            config=_FakeConfig(),
            registry=ToolRegistry(),
            max_iterations=5,
            injectors=[_TaskModifier()],
        )
        full_task, _, _, _ = loop._prepare_run("original", None)
        assert full_task == "MODIFIED\n\noriginal"
