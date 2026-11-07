"""批次 9 · B1 核心抽象类型测试（StepResult / StepHandler / StreamingHandler / Pipeline / 适配器）。

验收核心：**互操作证明**——真实 legacy step（``DefaultLLMCallStep``）
经 ``LegacyStepAdapter`` 与新式 handler 同 Pipeline 串联。
"""
from __future__ import annotations

import asyncio

import pytest

from agentnodes.kernel.agent._core_types import (
    Pipeline,
    StepHandler,
    StepResult,
    StreamingHandler,
    maybe_await,
)
from agentnodes.kernel.agent.step_adapters import (
    FnStreamingHandler,
    LegacyStepAdapter,
)


# ── StepResult ────────────────────────────────────────────────────────


class TestStepResult:
    def test_ok_factory(self):
        r = StepResult.ok({"x": 1}, events=[{"type": "delta"}])
        assert r.success is True
        assert r.output == {"x": 1}
        assert r.events == [{"type": "delta"}]
        assert r.error is None

    def test_fail_factory(self):
        r = StepResult.fail("boom", events=[{"type": "e"}])
        assert r.success is False
        assert r.error == "boom"
        assert r.events == [{"type": "e"}]

    def test_events_default_fresh_list(self):
        """两个实例的 events 不共享（dataclass field 默认值陷阱）。"""
        a, b = StepResult.ok(), StepResult.ok()
        a.events.append({"type": "x"})
        assert b.events == []


# ── maybe_await ───────────────────────────────────────────────────────


class TestMaybeAwait:
    def test_sync_passthrough(self):
        assert asyncio.run(maybe_await(StepResult.ok(1))) == StepResult.ok(1)

    def test_awaitable_resolved(self):
        async def make():
            return StepResult.ok(2)

        assert asyncio.run(maybe_await(make())) == StepResult.ok(2)


# ── StepHandler 双栖（sync / async handle）───────────────────────────


class _SyncHandler(StepHandler):
    def handle(self, input, ctx):
        return StepResult.ok(input + 1)


class _AsyncHandler(StepHandler):
    async def handle(self, input, ctx):
        return StepResult.ok(input * 2)


class TestStepHandler:
    def test_sync_handler(self):
        r = asyncio.run(maybe_await(_SyncHandler().handle(1, None)))
        assert r.output == 2

    def test_async_handler(self):
        assert asyncio.run(_AsyncHandler().handle(3, None)).output == 6

    def test_pipeline_mixes_sync_and_async(self):
        p = Pipeline(_SyncHandler(), _AsyncHandler())
        r = asyncio.run(p.handle(5, None))
        assert r.success and r.output == 12  # (5+1)*2


# ── Pipeline ──────────────────────────────────────────────────────────


class _AddOne(StepHandler):
    def handle(self, input, ctx):
        return StepResult.ok((input or 0) + 1, events=[{"type": "add"}])


class _Boom(StepHandler):
    def handle(self, input, ctx):
        return StepResult.fail("exploded")


class TestPipeline:
    def test_chaining_and_event_accumulation(self):
        p = Pipeline(_AddOne(), _AddOne(), _AddOne())
        r = asyncio.run(p.handle(0, None))
        assert r.success and r.output == 3
        assert r.events == [{"type": "add"}] * 3

    def test_fail_fast_preserves_accumulated_events(self):
        p = Pipeline(_AddOne(), _Boom(), _AddOne())
        r = asyncio.run(p.handle(0, None))
        assert r.success is False
        assert r.error == "exploded"
        assert r.events == [{"type": "add"}]  # 第一步的 events 保留

    def test_empty_pipeline_passthrough(self):
        r = asyncio.run(Pipeline().handle("x", None))
        assert r.success and r.output == "x"

    def test_repr(self):
        assert "AddOne" in repr(Pipeline(_AddOne()))


# ── StreamingHandler 契约 ─────────────────────────────────────────────


class _CountingStreamer(StreamingHandler):
    def __init__(self) -> None:
        self.seen: list = []

    async def _gen(self, input, ctx):
        for i in range(3):
            yield {"type": "delta", "i": i}
        yield StepResult.ok(input * 10)

    def stream(self, input, ctx):
        return self._gen(input, ctx)


class TestStreaming:
    def test_last_yield_is_stepresult(self):
        handler = _CountingStreamer()
        events: list = []
        final = None
        async def consume():
            nonlocal final
            async for ev in handler.stream(4, None):
                if isinstance(ev, StepResult):
                    final = ev
                else:
                    events.append(ev)

        asyncio.run(consume())
        assert [e["i"] for e in events] == [0, 1, 2]
        assert final is not None and final.output == 40

    def test_fn_streaming_handler_delegates(self):
        async def gen(input, ctx):
            yield {"type": "one"}
            yield StepResult.ok("done")

        h = FnStreamingHandler(gen, name="delegated")
        assert h.name == "delegated"
        async def consume():
            return [ev async for ev in h.stream(None, None)]
        out = asyncio.run(consume())
        assert out[0] == {"type": "one"}
        assert isinstance(out[-1], StepResult) and out[-1].output == "done"


# ── 互操作：真实 legacy step 经适配器进 Pipeline ─────────────────────


class TestLegacyInterop:
    def test_real_legacy_step_through_adapter(self):
        """真实步骤（DefaultLLMCallStep，loop=None 路径）经适配器返回 ctx。"""
        from agentnodes.kernel.agent.strategy.steps.llm_call import (
            DefaultLLMCallStep,
        )
        from agentnodes.kernel.agent.strategy.loop_context import LoopContext

        ctx = LoopContext(task="e2e")
        adapter = LegacyStepAdapter(DefaultLLMCallStep())
        assert adapter.name == "llm_call"

        r = asyncio.run(adapter.handle(None, ctx))
        assert r.success is True
        assert r.output is ctx  # legacy 契约：返回变异后的 ctx

    def test_legacy_adapter_maps_exception_to_fail(self):
        class _Broken:
            name = "broken"

            def execute(self, ctx, *, async_mode):
                raise RuntimeError("legacy blew up")

        r = asyncio.run(LegacyStepAdapter(_Broken()).handle(None, None))
        assert r.success is False
        assert "legacy blew up" in r.error

    def test_interop_pipeline_legacy_plus_new(self):
        """验收核心：真实 legacy step 与新式 handler 同管道串联。"""
        from agentnodes.kernel.agent.strategy.steps.llm_call import (
            DefaultLLMCallStep,
        )
        from agentnodes.kernel.agent.strategy.loop_context import LoopContext

        class _Mark(StepHandler):
            def handle(self, input, ctx):
                input.marked = True  # type: ignore[attr-defined]
                return StepResult.ok(input)

        ctx = LoopContext(task="e2e")
        pipeline = Pipeline(
            LegacyStepAdapter(DefaultLLMCallStep()),
            _Mark(),
        )
        r = asyncio.run(pipeline.handle(None, ctx))
        assert r.success is True
        assert r.output.marked is True
        assert r.output is ctx

    def test_pipeline_is_stephandler(self):
        """Pipeline 自身可再嵌套（组合性）。"""
        inner = Pipeline(_AddOne(), _AddOne())
        outer = Pipeline(inner, _AddOne())
        r = asyncio.run(outer.handle(0, None))
        assert r.output == 3
