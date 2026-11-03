"""Tests for hooks module — UnifiedHook, CompositeHook, AgentHookAdapter, maybe_await."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.kernel.hooks import (
    AgentHook,
    AgentHookContext,
    CompositeHook,
    NoOpHook,
    maybe_await,
)


class TestAgentHookContext(unittest.TestCase):

    def test_defaults(self) -> None:
        ctx = AgentHookContext()
        self.assertEqual(ctx.iteration, 0)
        self.assertEqual(ctx.messages, [])
        self.assertIsNone(ctx.response)
        self.assertEqual(ctx.usage, {})
        self.assertEqual(ctx.tool_calls, [])
        self.assertFalse(ctx.streamed_content)
        self.assertFalse(ctx.cancelled)

    def test_custom_values(self) -> None:
        ctx = AgentHookContext(
            iteration=5,
            messages=[{"role": "user"}],
            error="something failed",
            cancelled=True,
            chars_saved=100,
        )
        self.assertEqual(ctx.iteration, 5)
        self.assertEqual(ctx.messages, [{"role": "user"}])
        self.assertEqual(ctx.error, "something failed")
        self.assertTrue(ctx.cancelled)
        self.assertEqual(ctx.chars_saved, 100)

    def test_slots(self) -> None:
        ctx = AgentHookContext()
        with self.assertRaises(AttributeError):
            ctx.new_attr = 1  # type: ignore[attr-defined]


class SpyHook(AgentHook):
    name = "spy"
    def __init__(self) -> None:
        self.events: list[str] = []
    def before_iteration(self, ctx: AgentHookContext) -> None:
        self.events.append("before_iteration")
    def after_iteration(self, ctx: AgentHookContext) -> None:
        self.events.append("after_iteration")
    def on_stream(self, ctx: AgentHookContext, delta: str) -> None:
        self.events.append(f"on_stream:{delta}")
    def finalize_content(self, ctx: AgentHookContext, content: str | None) -> str | None:
        self.events.append("finalize_content")
        return content + " modified"


class TestCompositeHook(unittest.TestCase):

    def test_empty(self) -> None:
        hook = CompositeHook()
        self.assertEqual(len(hook), 0)
        self.assertFalse(hook.wants_streaming())

    def test_add_and_fire(self) -> None:
        spy = SpyHook()
        hook = CompositeHook([spy])
        ctx = AgentHookContext()
        import asyncio
        asyncio.run(hook.before_iteration(ctx))
        self.assertEqual(spy.events, ["before_iteration"])

    def test_remove(self) -> None:
        spy = SpyHook()
        hook = CompositeHook([spy])
        hook.remove("spy")
        self.assertEqual(len(hook), 0)

    def test_clear(self) -> None:
        spy = SpyHook()
        hook = CompositeHook([spy])
        hook.clear()
        self.assertEqual(len(hook), 0)

    def test_wants_streaming(self) -> None:
        class StreamingHook(AgentHook):
            name = "streaming"
            def wants_streaming(self) -> bool:
                return True
        hook = CompositeHook([SpyHook(), StreamingHook()])
        self.assertTrue(hook.wants_streaming())

    def test_error_isolation(self) -> None:
        class BrokenHook(AgentHook):
            name = "broken"
            def before_iteration(self, ctx: AgentHookContext) -> None:
                raise RuntimeError("broken")
        spy = SpyHook()
        hook = CompositeHook([BrokenHook(), spy])
        ctx = AgentHookContext()
        import asyncio
        asyncio.run(hook.before_iteration(ctx))
        self.assertIn("before_iteration", spy.events)

    def test_finalize_pipeline(self) -> None:
        spy = SpyHook()
        hook = CompositeHook([spy])
        ctx = AgentHookContext()
        import asyncio
        result = asyncio.run(hook.finalize_content(ctx, "hello"))
        self.assertEqual(result, "hello modified")
        self.assertIn("finalize_content", spy.events)


class TestNoOpHook(unittest.TestCase):

    def test_name(self) -> None:
        self.assertEqual(NoOpHook().name, "noop")

    def test_wants_streaming(self) -> None:
        self.assertFalse(NoOpHook().wants_streaming())


class TestMaybeAwait(unittest.TestCase):

    async def test_sync_callable(self) -> None:
        result = await maybe_await(lambda x: x + 1, 5)
        self.assertEqual(result, 6)

    async def test_async_callable(self) -> None:
        async def add(a, b):
            return a + b
        result = await maybe_await(add, 3, 4)
        self.assertEqual(result, 7)

    async def test_value_direct(self) -> None:
        result = await maybe_await(42)
        self.assertEqual(result, 42)

    async def test_awaitable_value(self) -> None:
        async def returns_10():
            return 10
        result = await maybe_await(returns_10())
        self.assertEqual(result, 10)


if __name__ == "__main__":
    unittest.main()
