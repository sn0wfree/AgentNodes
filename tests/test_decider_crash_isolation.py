"""判定层崩溃隔离（B2-①b）注入网。

契约（docs/design/decider-crash-isolation-20261113.md）：

- 闸位 runner **逐个隔离**：decider 本体抛异常 → 按 ``crash_policy`` 合成
  保守裁决（守护型 STOP / 放行型 PROCEED），**不穿透引擎级兜底**
- record 第二入口同样防护（喂窗失败 → 降级可观测，不阻断循环）
- 崩溃可观测：``decider_error`` 事件（trace + 总线，decider/gate/error 三字段）
- budget 子串映射守卫：``decider_*`` 前缀裁决不落 ``finished_reason="budget"``
  （崩溃 ≠ 超支）
- 非崩溃路径零变化（等价网兜底）
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

from agentnodes.foundation.llm import LLMConfig, LLMResponse, ToolCall
from agentnodes.kernel.agent.builtin_tools import build_default_registry
from agentnodes.kernel.agent.policy.deciders import Decision, Decider, TextFinalDecider
from agentnodes.kernel.agent.loop.core import AgentLoop
from agentnodes.kernel.agent.loop.engine import LoopEngine
from agentnodes.kernel.agent.strategy import LoopContext


# ── 测试替身 ─────────────────────────────────────────────────────────


class _BoomDecider(Decider):
    """必崩守护型（默认 crash_policy=STOP）。"""

    @property
    def name(self) -> str:
        return "boom"

    def decide(self, input, ctx):  # noqa: ANN001, ANN202
        raise RuntimeError("judge exploded")


class _BoomContinueDecider(Decider):
    """必崩放行型。"""

    crash_policy = Decision.PROCEED

    @property
    def name(self) -> str:
        return "boom_continue"

    def decide(self, input, ctx):  # noqa: ANN001, ANN202
        raise RuntimeError("judge exploded")


class _Fixed(Decider):
    """固定裁决（探针：证明崩掉的 decider 之后闸位还在跑）。"""

    def __init__(self, decision: Decision, reason: str) -> None:
        self._d, self._r = decision, reason

    @property
    def name(self) -> str:
        return "fixed"

    def decide(self, input, ctx):  # noqa: ANN001, ANN202
        return self._d, self._r


class _StubLoop:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict | None]] = []

    def _trace_and_emit(self, event_type: str, data: dict | None = None) -> None:
        self.events.append((event_type, data))


def _engine(gates: dict) -> LoopEngine:
    return LoopEngine(reasoner=None, actor=None, deciders=gates)


class _Mock:
    """文本终答替身（挂满 4 个入口，R-008）。"""

    def __init__(self, text: str = "x") -> None:
        self.text = text

    def chat(self, messages, **kw):  # noqa: ANN001, ANN202
        return LLMResponse(content=self.text, tool_calls=[], finish_reason="stop")

    async def achat(self, messages, **kw):  # noqa: ANN001, ANN202
        return self.chat(messages, **kw)

    async def astream(self, messages, tools=None, **kw):  # noqa: ANN001, ANN202
        from agentnodes.foundation.llm.parser import StreamChunk

        yield StreamChunk(delta_content=self.text, finish_reason="stop")

    def stream(self, messages, tools=None, **kw):  # noqa: ANN001, ANN202
        from agentnodes.foundation.llm.parser import StreamChunk

        def _gen():
            yield StreamChunk(delta_content=self.text, finish_reason="stop")

        return _gen()


def _make_loop(tmp_path: Path, text: str = "x") -> AgentLoop:
    loop = AgentLoop(
        stream_mode=False,
        config=LLMConfig(api_key="sk-test"),
        registry=build_default_registry(),
        workspace=tmp_path,
        trace_dir=tmp_path / "traces",
        strategy={"name": "react", "config": {"max_iterations": 3}},
    )
    m = _Mock(text)
    loop.client.chat = m.chat
    loop.client.achat = m.achat
    loop.client.stream = m.stream
    loop.client.astream = m.astream
    return loop


# ── 1. 守护型崩 → STOP，不穿透 ───────────────────────────────────────


def test_guard_crash_stops_gate():
    eng = _engine({"precheck": [_BoomDecider()]})
    decision, reason = asyncio.run(
        eng._run_gate_once("precheck", LoopContext(task="t"))
    )
    assert decision is Decision.STOP
    assert reason == "decider_boom"


# ── 2. 放行型崩 → PROCEED，后续 decider 仍被咨询 ─────────────────────


def test_continue_crash_falls_through_to_next():
    eng = _engine({
        "after_reason": [
            _BoomContinueDecider(),
            TextFinalDecider(),
        ],
    })
    resp = LLMResponse(content="答案", tool_calls=[], finish_reason="stop")
    decision, reason = asyncio.run(
        eng._run_gate_once("after_reason", LoopContext(task="t"), payload=resp)
    )
    assert decision is Decision.STOP
    assert reason == "final_text"


# ── 3. 崩溃可观测：decider_error 事件 ────────────────────────────────


def test_crash_emits_decider_error_event():
    stub = _StubLoop()
    eng = _engine({"precheck": [_BoomDecider()]})
    eng._event_source = lambda: stub
    asyncio.run(eng._run_gate_once("precheck", LoopContext(task="t")))
    kinds = [k for k, _ in stub.events]
    assert "decider_error" in kinds, stub.events
    payload = dict(stub.events[kinds.index("decider_error")][1] or {})
    assert payload["decider"] == "boom"
    assert payload["gate"] == "precheck"
    assert "judge exploded" in payload["error"]


# ── 4. record 崩 → 循环完成（不穿透）────────────────────────────────


def test_record_crash_loop_completes(tmp_path: Path):
    loop = _make_loop(tmp_path, text="done")
    # 第一轮给 tool_call（推进到 ACT → record 被调），之后文本终答
    responses = [
        LLMResponse(
            content="",
            tool_calls=[ToolCall(id="c1", name="probe_tool", arguments={})],
            finish_reason="tool_calls",
        ),
    ]

    class _Seq(_Mock):
        def __init__(self) -> None:
            super().__init__(text="done")
            self._rest = list(responses)

        def _next(self):  # noqa: ANN202
            if self._rest:
                return self._rest.pop(0)
            return LLMResponse(content="done", tool_calls=[], finish_reason="stop")

        def chat(self, messages, **kw):  # noqa: ANN001, ANN202
            return self._next()

        async def achat(self, messages, **kw):  # noqa: ANN001, ANN202
            return self._next()

    m = _Seq()
    loop.client.chat = m.chat
    loop.client.achat = m.achat
    loop.client.stream = m.stream
    loop.client.astream = m.astream
    # 绕开 registry/permission 的不确定性：直接桩掉批量执行
    async def _fake_batch(tool_calls, result):  # noqa: ANN001, ANN202
        return [{"role": "tool", "tool_call_id": tool_calls[0].id, "content": "ok"}]

    loop._aexecute_tool_batch = _fake_batch
    loop._strategy.progress.record_hash = MagicMock(
        side_effect=RuntimeError("window boom")
    )
    r = asyncio.run(loop.arun("测试"))
    assert r.finished_reason == "stop", r.finished_reason
    assert r.answer == "done"
    assert r.error is None


# ── 5. budget 槽崩 → 具名 STOP + 映射守卫 ────────────────────────────


def test_budget_slot_crash_named_stop(tmp_path: Path):
    loop = _make_loop(tmp_path)
    loop._strategy.stop.evaluate = MagicMock(
        side_effect=RuntimeError("budget boom")
    )
    r = asyncio.run(loop.arun("测试"))
    assert r.stop_reason == "decider_budget"
    assert r.finished_reason != "budget", "崩溃 ≠ 超支：映射守卫失效"
    assert r.finished_reason == "stop"
    assert r.error is None, "gate STOP 路径不写 error"
    assert r.trace_path, "FINALIZE 漏斗未走（trace 未关闭）"


# ── 6. goal 槽崩 → 文本终答保留 ──────────────────────────────────────


def test_goal_continuation_crash_text_answer_preserved(tmp_path: Path):
    loop = _make_loop(tmp_path, text="最终答案")
    loop._strategy.continuation.evaluate = MagicMock(
        side_effect=RuntimeError("goal boom")
    )
    r = asyncio.run(loop.arun("测试"))
    assert r.answer == "最终答案", "放行型崩溃不得吞掉文本终答"
    assert r.stop_reason == "final_text"
    assert r.finished_reason == "stop"
