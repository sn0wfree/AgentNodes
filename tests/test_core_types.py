"""核心抽象类型测试（StepResult / StepHandler / StreamingHandler / Pipeline）。

**T11（2026-11-17）后**：旧形状互操作桥 ``LegacyStepAdapter`` 已删除——
全部 step 都是新式 ``handle(input, ctx) → StepResult`` 契约；
``AgentLoop._call_step`` 对无 handle 的 step 走错误隔离通道（见
``TestCallStepRejectsLegacyShape``）。
"""
from __future__ import annotations

import asyncio

from agentnodes.kernel.agent._core_types import (
    Pipeline,
    StepHandler,
    StepResult,
    StreamingHandler,
    maybe_await,
)
from agentnodes.kernel.agent.step_adapters import FnStreamingHandler


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

    def test_pipeline_is_stephandler(self):
        """Pipeline 自身可再嵌套（组合性）。"""
        inner = Pipeline(_AddOne(), _AddOne())
        outer = Pipeline(inner, _AddOne())
        r = asyncio.run(outer.handle(0, None))
        assert r.output == 3


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


# ── _call_step 对旧形状 step 的拒绝（T11 新分支覆盖）──────────────────


class TestCallStepRejectsLegacyShape:
    def test_handle_less_step_is_isolated_as_fail(self):
        """T11 后：无 handle 的 step（旧形状 execute 残留）→ StepResult.fail，
        异常被错误隔离吞掉而不是炸整个 loop。"""

        class _LegacyShape:
            name = "legacy_stub"

            def execute(self, ctx, *, async_mode):  # 旧形状：无 handle
                raise AssertionError("execute 不应被调用")

        from agentnodes.kernel.agent.loop import AgentLoop

        r = asyncio.run(AgentLoop._call_step(object(), _LegacyShape(), None))
        assert r.success is False
        assert "没有 handle" in r.error

    def test_handle_step_normal_path(self):
        """新形状 step 经 _call_step 正常执行（改动后的回归对照）。"""

        class _Ok:
            name = "ok_step"

            async def handle(self, input, ctx):
                return StepResult.ok("ran")

        from agentnodes.kernel.agent.loop import AgentLoop

        r = asyncio.run(AgentLoop._call_step(object(), _Ok(), None))
        assert r.success is True and r.output == "ran"
