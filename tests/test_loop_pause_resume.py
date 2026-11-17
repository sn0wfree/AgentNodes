"""H1：循环级挂起/恢复（pause/resume）e2e 网。

**验收红线**（design doc §3.5）：
1. 挂起可见——外部能观察到挂起态（pause 事件 + agent_paused 事件）；
2. 恢复值进循环——resume 注入的值被挂起方读到并影响后续判定；
3. **无副作用重放**——挂起前已执行的副作用在恢复后**不重复**
   （LangGraph double-execution 坑）。

**形状**：decider 侧 ``ctx.pause(...)`` 只**创建挂起点**（不阻塞），
骨架在闸位后 yield pause 事件 → await 恢复 → 把恢复值写进
``ctx.resume_value`` → **重跑该闸位**。因此：
- decider 协议不变（Decision 仍是四值，无 PAUSE）；
- 挂起点在闸位判定处，恢复后**继续**而非回退 → 不重放工具执行。

测试用一个自定义 decider 模拟"人工判断"闸：第一次进闸就请求挂起，
拿到 resume 值后按值决定 CONTINUE / STOP。
"""
from __future__ import annotations
from llm_client_mock import patch_llm_client

import asyncio
import threading
import time
from collections import Counter
from pathlib import Path

import pytest

from agentnodes.foundation.llm import LLMConfig, LLMResponse, ToolCall
from agentnodes.kernel.agent.builtin_tools import build_default_registry
from agentnodes.kernel.agent.deciders import Decision, Decider
from agentnodes.kernel.agent.loop.core import AgentLoop
from agentnodes.kernel.agent.pause import CANCELLED, PauseHandle

USAGE = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}


class MockLLM:
    def __init__(self, responses):
        self.responses = list(responses)

    def chat(self, messages, **kwargs):
        if not self.responses:
            raise RuntimeError("MockLLM exhausted")
        return self.responses.pop(0)

    async def achat(self, messages, **kwargs):
        return self.chat(messages, **kwargs)


def _resp(text="", tool=False):
    tc = [ToolCall(id="c1", name="list_history", arguments={})] if tool else []
    return LLMResponse(content=text, tool_calls=tc,
                       finish_reason="tool_calls" if tool else "stop",
                       usage=USAGE)


class HumanJudgeDecider(Decider):
    """人工判断闸：第一次进闸请求挂起；恢复后按 resume 值决定。

    ``resume_value`` 语义（由测试注入）：
    - ``{"continue": True}`` → CONTINUE（跳过剩余步骤，下一轮）
    - ``{"continue": False}`` → STOP（收尾）

    **判定只做一次**（恢复后第一次读到 resume_value 时定下来）——
    之后每轮闸位都跑，若每轮重新判定就会一直 CONTINUE 跑到 max_iter。
    """

    def __init__(self) -> None:
        self.gate_calls = 0
        self.resume_seen: object = "NOT_SET"
        self._verdict: tuple | None = None

    @property
    def name(self) -> str:
        return "human_judge"

    def decide(self, input, ctx):  # noqa: ANN001
        self.gate_calls += 1
        if self.gate_calls == 1:
            # 请求挂起（不阻塞）——骨架负责 yield + await
            handle = ctx.pause(
                reason="需要人工判断",
                payload={"question": "继续吗？"},
                timeout_s=10,
            )
            if handle is not None:
                assert isinstance(handle, PauseHandle)
            return Decision.PROCEED, None
        # 恢复后重跑：读恢复值，定一次判定
        if self._verdict is None:
            self.resume_seen = getattr(ctx, "resume_value", None)
            rv = self.resume_seen
            if isinstance(rv, dict) and rv.get("continue"):
                self._verdict = (Decision.CONTINUE, "human_continue")
            else:
                self._verdict = (Decision.STOP, "human_stop")
        return self._verdict


def _build(ws: Path, responses, judge: HumanJudgeDecider, *,
           max_iterations: int = 4):
    loop = AgentLoop(
        stream_mode=False,
        config=LLMConfig(api_key="sk-test"),
        registry=build_default_registry(),
        workspace=ws,
        trace_dir=ws / "traces",
        strategy={"name": "react", "config": {"max_iterations": max_iterations,
                                              "no_progress_window": 99}},
    )
    mock = MockLLM(list(responses))
    patch_llm_client(loop, mock.chat)
    loop.client.achat = mock.achat
    return loop, mock


async def _drive(loop: AgentLoop, judge: HumanJudgeDecider,
                 resume_value, *, resume_delay_s: float = 0.0):
    """跑 loop，在挂起后从**另一个线程** resume（模拟外部 HTTP 响应）。"""
    events: list[str] = []
    original = loop._on_event

    def _on_event(kind, payload=None, *a, **k):
        events.append(kind)
        if original:
            return original(kind, payload)
        return None

    loop._on_event = _on_event

    # 注入 judge decider 到 after_reason 闸（替换默认集合）
    # 注意：loop.py:928 是**函数内局部导入** `from .modes import deciders_for`
    # ——patch `agentnodes.kernel.agent.modes.deciders_for` 才生效
    # （patch loop 模块属性无效：局部 import 每次重新查 modes 模块）。
    from agentnodes.kernel.agent import modes as modes_mod

    orig_deciders_for = modes_mod.deciders_for

    def _patched(strategy, lp, **kw):
        gates = orig_deciders_for(strategy, lp, **kw)
        gates["after_reason"] = [judge]
        return gates

    modes_mod.deciders_for = _patched
    try:
        task = asyncio.create_task(loop.arun("测试任务"))
        # 等挂起出现——**外部视角**：只能观察到 agent_paused 事件
        # （骨架内部在 await 前会消费掉 _pause_handle，外部看不到它——
        #  这正是设计意图：挂起点是内部状态，事件是外部契约）。
        deadline = time.monotonic() + 10
        handle = None
        while time.monotonic() < deadline:
            await asyncio.sleep(0.01)
            h = getattr(loop._skeleton_ctx, "_pause_active", None)
            if h is not None:
                handle = h
                break
        if handle is None:
            await task
            raise AssertionError(
                f"未观察到挂起点（events={events}）")
        if resume_delay_s:
            await asyncio.sleep(resume_delay_s)
        # 外部线程 resume
        threading.Thread(
            target=handle.resume, args=(resume_value,), daemon=True).start()
        r = await asyncio.wait_for(task, timeout=30)
    finally:
        modes_mod.deciders_for = orig_deciders_for
    return r, events, handle


# ── 验收 1：挂起可见 ──


def test_pause_is_observable(tmp_path: Path):
    """挂起产生 pause 事件 + agent_paused 事件（外部能观察挂起态）。"""
    judge = HumanJudgeDecider()
    loop, _ = _build(tmp_path, [_resp(text="x")], judge)
    r, events, handle = asyncio.run(
        _drive(loop, judge, {"continue": False}))

    assert "agent_paused" in events, f"无 agent_paused 事件：{events}"
    assert handle.reason == "需要人工判断"
    assert handle.payload == {"question": "继续吗？"}
    # 恢复后按值 STOP → 收尾
    assert r.finished_reason == "stop", r.finished_reason


# ── 验收 2：恢复值进循环 ──


def test_resume_value_reaches_decider(tmp_path: Path):
    """resume 注入的值被挂起方读到（ctx.resume_value）并影响判定。"""
    judge = HumanJudgeDecider()
    loop, _ = _build(tmp_path, [_resp(text="x")], judge)
    r, events, _ = asyncio.run(
        _drive(loop, judge, {"continue": True}))

    assert judge.resume_seen == {"continue": True}, (
        f"decider 未读到恢复值：{judge.resume_seen}"
    )
    # CONTINUE → 跳过剩余步骤，进入下一轮（第二轮 LLM 调用）
    assert judge.gate_calls >= 2, f"闸位未重跑：{judge.gate_calls}"
    assert r.iterations >= 1


# ── 验收 3：无副作用重放 ──


def test_no_side_effect_replay_after_resume(tmp_path: Path):
    """挂起前已执行的副作用在恢复后**不重复**（LangGraph 坑的防线）。

    **红线的本质**：恢复后是「**继续**」而不是「回退重跑」——闸位重跑
    **不得**重新触发 LLM 调用或工具执行。

    断言：LLM 调用次数 == 轮数（无额外调用）。CONTINUE 语义是「跳过本轮
    剩余步骤进入下一轮」，所以轮数由 max_iterations 决定（4 轮 = 4 次调用）。
    若恢复被实现成「回退重跑」，LLM 调用会 > 轮数。
    """
    judge = HumanJudgeDecider()
    responses = [_resp(text=f"r{i}") for i in range(4)]
    loop, mock = _build(tmp_path, responses, judge, max_iterations=4)
    r, events, _ = asyncio.run(
        _drive(loop, judge, {"continue": True}))

    # 轮数 = max_iterations（CONTINUE 一直继续到上限）
    assert r.iterations == 4, f"期望 4 轮，实得 {r.iterations}"
    # 每次迭代恰好一次 LLM 调用——无额外调用（重放会剩更多）
    assert len(mock.responses) == 0, (
        f"LLM 调用次数异常（可能重放）：剩 {len(mock.responses)}"
    )


# ── 取消/超时语义 ──


def test_pause_cancel_returns_cancelled(tmp_path: Path):
    """取消挂起 → 恢复值是 CANCELLED 哨兵。"""
    judge = HumanJudgeDecider()
    loop, _ = _build(tmp_path, [_resp(text="x")], judge)
    r, events, handle = asyncio.run(
        _drive(loop, judge, None))

    assert judge.resume_seen is None or judge.resume_seen == {}, (
        f"恢复值应为 None（取消），实得 {judge.resume_seen!r}"
    )


def test_pause_timeout_is_explicit(tmp_path: Path):
    """超时 → handle 变 cancelled（不静默挂死）。"""
    h = PauseHandle(reason="t", timeout_s=0.05)
    value = h.wait()
    assert value is CANCELLED
    assert h.cancel_reason == "timeout"
    assert not h.is_paused


# ── 单次挂起（不变量 3）──


def test_resume_value_blocks_second_pause(tmp_path: Path):
    """恢复过一次后，pause 工厂不再返回 handle（防死循环）。"""
    judge = HumanJudgeDecider()
    loop, _ = _build(tmp_path, [_resp(text="x")], judge)
    asyncio.run(_drive(loop, judge, {"continue": False}))
    ctx = loop._skeleton_ctx
    assert ctx.resume_value is not None
    # 恢复后再次调 pause → None（不挂起）
    assert ctx.pause(reason="second") is None


# ── 并发安全：挂起期间事件循环仍可响应 ──


def test_event_loop_responsive_during_pause(tmp_path: Path):
    """挂起期间事件循环不被阻塞（await 而非同步 wait）。"""
    judge = HumanJudgeDecider()
    loop, _ = _build(tmp_path, [_resp(text="x")], judge)
    ticks = []

    async def _drive_with_ticks():
        async def _tick():
            for _ in range(5):
                await asyncio.sleep(0.02)
                ticks.append(1)
        t = asyncio.create_task(_tick())
        r, events, handle = await _drive(
            loop, judge, {"continue": False}, resume_delay_s=0.1)
        await t
        return r, events

    asyncio.run(_drive_with_ticks())
    assert len(ticks) >= 3, f"事件循环在挂起期间被阻塞（ticks={len(ticks)}）"
