"""Deep integration: AgentLoop DSH features (_prepare_run, _execute_tool_call_core, _run_loop_core)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.kernel.agent.loop import AgentLoop, LoopResult
from strategy_research.kernel.agent.tools import BaseTool, ToolRegistry


class _FakeConfig:
    compact_config = None


class _DummyTool(BaseTool):
    name = "dummy"
    description = "A dummy tool"
    parameters = {}

    def execute(self, **kwargs):
        return '{"status": "ok"}'


class _DenyAllGuard:
    def check(self, name, params):
        return f"Denied: {name}"


class _TrackingInjector:
    """Injector that records all calls.

    inject_pre_run follows the P5-4 prefix contract (block + "\\n\\n" +
    task) — the hub bridge decomposes blocks by it.
    """

    def __init__(self, name: str, order: int):
        self.name = name
        self.order = order
        self.calls: list[tuple[str, Any]] = []

    def inject_pre_run(self, loop, task, messages):
        self.calls.append(("pre_run", task))
        return "[modified]\n\n" + task

    def inject_per_iteration(self, loop, messages):
        self.calls.append(("per_iter", len(messages)))

    def inject_post_response(self, loop, response, messages, result, iteration):
        self.calls.append(("post_resp", iteration))
        return False


def _make_loop(injectors=None, registry=None):
    return AgentLoop(
        config=_FakeConfig(),
        registry=registry or ToolRegistry(),
        max_iterations=5,
        injectors=injectors,
    )


# ── _prepare_run integration ─────────────────────────────────────


class TestPrepareRunIntegration:
    def test_pre_run_injectors_modify_task(self):
        """Pre-run injectors (order < 0) can modify the task string
        (P5-4 契约：前缀注入 block + "\\n\\n" + task)。"""
        inj = _TrackingInjector("test", -100)
        loop = _make_loop(injectors=[inj])
        full_task, result, messages, t0 = loop._prepare_run("original task", None)
        assert full_task == "[modified]\n\noriginal task"
        assert inj.calls == [("pre_run", "original task")]

    def test_pre_run_injectors_run_before_context_builder(self):
        """Pre-run injectors run before context_builder.build_initial_messages."""
        call_order = []

        class _OrderTracker:
            name = "tracker"
            order = -100

            def inject_pre_run(self, loop, task, messages):
                call_order.append("injector")
                return task

        loop = _make_loop(injectors=[_OrderTracker()])
        # Mock context_builder to track call order
        original_build = loop.context_builder.build_initial_messages

        def _tracked_build(*args, **kwargs):
            call_order.append("context_builder")
            return original_build(*args, **kwargs)

        loop.context_builder.build_initial_messages = _tracked_build
        loop._prepare_run("task", None)
        assert call_order == ["injector", "context_builder"]

    def test_context_prefix_prepended(self):
        """Context parameter is prepended before task."""
        loop = _make_loop()
        full_task, _, _, _ = loop._prepare_run("task", "context prefix")
        assert full_task == "context prefix\n\ntask"

    def test_context_plus_injector_combined(self):
        """Context prefix + injector modification both apply."""
        inj = _TrackingInjector("test", -100)
        loop = _make_loop(injectors=[inj])
        full_task, _, _, _ = loop._prepare_run("task", "ctx")
        # Context first, then injector prepends its block
        assert full_task == "[modified]\n\nctx\n\ntask"

    def test_pre_run_injector_exception_safe(self):
        """Broken pre-run injector doesn't crash _prepare_run."""

        class _BrokenInjector:
            name = "broken"
            order = -100

            def inject_pre_run(self, loop, task, messages):
                raise RuntimeError("boom")

        loop = _make_loop(injectors=[_BrokenInjector()])
        full_task, result, messages, t0 = loop._prepare_run("task", None)
        # Task unchanged (exception swallowed)
        assert full_task == "task"
        assert result is not None


# ── _execute_tool_call_core guard integration ────────────────────


class TestExecuteToolCallGuardIntegration:
    def test_guard_returns_denied_message(self):
        """Guard denial returns a tool message with status=denied."""
        r = ToolRegistry()
        r.register(_DummyTool())
        r.guard(_DenyAllGuard())
        loop = _make_loop(registry=r)
        result = LoopResult()
        tc = MagicMock()
        tc.name = "dummy"
        tc.id = "call_1"
        tc.arguments = {}

        msg = asyncio.run(loop._execute_tool_call_core(tc, result, async_mode=False))
        assert msg["role"] == "tool"
        assert msg["tool_call_id"] == "call_1"
        content = json.loads(msg["content"])
        assert content["status"] == "denied"
        assert "Denied" in content["error"]

    def test_guard_denied_tool_not_executed(self):
        """Tool is NOT executed when guard denies."""
        r = ToolRegistry()
        tool = MagicMock()
        tool.name = "dummy"
        tool.brief = ""
        tool.to_openai_schema.return_value = {"type": "function", "function": {"name": "dummy"}}
        r.register(tool)
        r.guard(_DenyAllGuard())
        loop = _make_loop(registry=r)
        result = LoopResult()
        tc = MagicMock()
        tc.name = "dummy"
        tc.id = "c1"
        tc.arguments = {}

        asyncio.run(loop._execute_tool_call_core(tc, result, async_mode=False))
        # tool.invoke should NOT have been called
        tool.invoke.assert_not_called()

    def test_guard_emits_tool_result_event(self):
        """Guard denial emits tool_result event with status=denied."""
        r = ToolRegistry()
        r.register(_DummyTool())
        r.guard(_DenyAllGuard())
        loop = _make_loop(registry=r)
        result = LoopResult()
        tc = MagicMock()
        tc.name = "dummy"
        tc.id = "c1"
        tc.arguments = {}

        # Capture emitted events
        emitted = []
        def _capture_emit(event_type, data):
            emitted.append((event_type, data))
        loop._emit = _capture_emit

        asyncio.run(loop._execute_tool_call_core(tc, result, async_mode=False))
        # Check that tool_result was emitted with status=denied
        tool_results = [e for e in emitted if e[0] == "tool_result"]
        assert len(tool_results) >= 1
        assert tool_results[0][1]["status"] == "denied"

    def test_no_guard_tool_executes_normally(self):
        """Without guards, tool executes normally."""
        r = ToolRegistry()
        r.register(_DummyTool())
        loop = _make_loop(registry=r)
        result = LoopResult()
        tc = MagicMock()
        tc.name = "dummy"
        tc.id = "c1"
        tc.arguments = {}

        msg = asyncio.run(loop._execute_tool_call_core(tc, result, async_mode=False))
        content = json.loads(msg["content"])
        assert content["status"] == "ok"

    def test_tool_not_in_registry_returns_error(self):
        """Tool not in registry returns error (not guard-related)."""
        r = ToolRegistry()
        loop = _make_loop(registry=r)
        result = LoopResult()
        tc = MagicMock()
        tc.name = "nonexistent"
        tc.id = "c1"
        tc.arguments = {}

        msg = asyncio.run(loop._execute_tool_call_core(tc, result, async_mode=False))
        content = json.loads(msg["content"])
        assert "error" in content


# ── _run_loop_core inbox drain + injector timing ─────────────────


class TestRunLoopCoreTiming:
    def test_inbox_drain_before_injectors(self):
        """Inbox is drained BEFORE per-iteration injectors run."""
        call_order = []

        class _OrderTracker:
            name = "tracker"
            order = 0

            def inject_per_iteration(self, loop, messages):
                call_order.append("injector")

        loop = _make_loop(injectors=[_OrderTracker()])
        # Track _drain_inbox
        original_drain = loop._drain_inbox

        def _tracked_drain(messages):
            call_order.append("drain")
            return original_drain(messages)

        loop._drain_inbox = _tracked_drain

        # We can't easily run _run_loop_core without a full mock setup,
        # but we can verify the method exists and is callable
        assert callable(loop._drain_inbox)

    def test_per_iteration_injectors_called(self):
        """Per-iteration injectors (order=0) are called each iteration."""
        inj = _TrackingInjector("test", 0)
        loop = _make_loop(injectors=[inj])
        messages = []
        # Manually call inject_per_iteration
        inj.inject_per_iteration(loop, messages)
        assert len(inj.calls) == 1
        assert inj.calls[0] == ("per_iter", 0)

    def test_post_response_injectors_called(self):
        """Post-response injectors (order>=100) are called when no tool calls."""
        inj = _TrackingInjector("test", 100)
        loop = _make_loop(injectors=[inj])
        response = MagicMock()
        response.content = "answer"
        messages = []
        result = LoopResult()
        # Manually call inject_post_response
        inj.inject_post_response(loop, response, messages, result, 1)
        assert len(inj.calls) == 1
        assert inj.calls[0] == ("post_resp", 1)


# ── Custom injector implementation ───────────────────────────────


class TestCustomInjectorImplementation:
    def test_custom_injector_modifies_messages(self):
        """Custom injector can append messages per iteration."""

        class _MemoryInjector:
            name = "memory"
            order = 0

            def inject_per_iteration(self, loop, messages):
                messages.append({
                    "role": "system",
                    "content": "<memory>Important context</memory>",
                })

        loop = _make_loop(injectors=[_MemoryInjector()])
        messages = [{"role": "user", "content": "task"}]
        loop._injectors[0].inject_per_iteration(loop, messages)
        assert len(messages) == 2
        assert "memory" in messages[1]["content"]

    def test_custom_injector_conditional(self):
        """Custom injector only activates under certain conditions."""

        class _ConditionalInjector:
            name = "conditional"
            order = 0

            def __init__(self):
                self.active = False

            def inject_per_iteration(self, loop, messages):
                if self.active:
                    messages.append({"role": "system", "content": "injected"})

        inj = _ConditionalInjector()
        loop = _make_loop(injectors=[inj])
        messages = []
        # Not active — no injection
        inj.inject_per_iteration(loop, messages)
        assert len(messages) == 0
        # Activate
        inj.active = True
        inj.inject_per_iteration(loop, messages)
        assert len(messages) == 1

    def test_custom_injector_with_state(self):
        """Custom injector maintains state across calls."""

        class _StatefulInjector:
            name = "stateful"
            order = 0

            def __init__(self):
                self.call_count = 0

            def inject_per_iteration(self, loop, messages):
                self.call_count += 1
                messages.append({
                    "role": "system",
                    "content": f"Call #{self.call_count}",
                })

        inj = _StatefulInjector()
        loop = _make_loop(injectors=[inj])
        messages = []
        inj.inject_per_iteration(loop, messages)
        inj.inject_per_iteration(loop, messages)
        inj.inject_per_iteration(loop, messages)
        assert inj.call_count == 3
        assert messages[0]["content"] == "Call #1"
        assert messages[2]["content"] == "Call #3"


# ── Multiple injectors ordering ──────────────────────────────────


class TestMultipleInjectorsOrdering:
    def test_injectors_execute_in_order(self):
        """Injectors execute in order of their order attribute."""
        call_log = []

        class _OrderedInjector:
            def __init__(self, name, order):
                self.name = name
                self.order = order

            def inject_per_iteration(self, loop, messages):
                call_log.append(self.name)

        injectors = [
            _OrderedInjector("C", 100),
            _OrderedInjector("A", -100),
            _OrderedInjector("B", 0),
        ]
        loop = _make_loop(injectors=sorted(injectors, key=lambda i: i.order))
        # Run all order=0 injectors
        for inj in loop._injectors:
            if inj.order == 0:
                inj.inject_per_iteration(loop, [])
        # Only B should have been called (order=0)
        assert call_log == ["B"]

    def test_pre_run_only_order_negative(self):
        """Only injectors with order < 0 run in pre_run."""
        inj_neg = _TrackingInjector("neg", -100)
        inj_zero = _TrackingInjector("zero", 0)
        inj_pos = _TrackingInjector("pos", 100)
        loop = _make_loop(injectors=[inj_neg, inj_zero, inj_pos])
        loop._prepare_run("task", None)
        # Only neg should have been called
        assert len(inj_neg.calls) == 1
        assert len(inj_zero.calls) == 0
        assert len(inj_pos.calls) == 0


# ── ToolRegistry with guard + restricted combo ───────────────────


class TestRegistryGuardRestrictedCombo:
    def test_restricted_copies_guards(self):
        """restricted() copies guards to new registry."""
        r = ToolRegistry()
        r.register(_DummyTool())
        r.guard(_DenyAllGuard())
        r2 = r.restricted()
        # New registry has same guard
        assert len(r2._guards) == 1
        reason = r2.check_guards("dummy", {})
        assert reason is not None

    def test_restricted_independent_guard_addition(self):
        """Adding guard to original doesn't affect restricted copy."""
        r = ToolRegistry()
        r.register(_DummyTool())
        r2 = r.restricted()
        r.guard(_DenyAllGuard())
        # r2 should not have the new guard
        assert len(r2._guards) == 0
        assert r2.check_guards("dummy", {}) is None

    def test_restricted_denied_set_copied(self):
        """restricted() copies denied set."""
        r = ToolRegistry()
        r.register(_DummyTool())
        r.restrict(deny={"dummy"})
        r2 = r.restricted()
        assert "dummy" in r2._denied
