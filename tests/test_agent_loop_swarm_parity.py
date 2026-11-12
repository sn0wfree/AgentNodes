"""SwarmWorker-parity tests for AgentLoop (unified engine, Phase 1).

Covers the three capabilities absorbed from SwarmWorker:
  1. ``iteration_timeout_s`` — per-iteration wall-clock timeout
     (sync: post-call check; async: real cancellation via wait_for)
  2. ``wrap_up_nudge`` — one-shot system nudge at 0.8×max_iterations
  3. ``force_final_text`` — final iteration calls the LLM with tools=None

All defaults keep the pre-existing behavior (backward compat).
"""
from __future__ import annotations
from llm_client_mock import patch_llm_client

import asyncio
import time
from pathlib import Path

import pytest

from agentnodes.kernel.agent.builtin_tools import build_default_registry
from agentnodes.kernel.agent.loop import (
    WRAP_UP_NUDGE_TEXT,
    WRAP_UP_RATIO,
    AgentLoop,
)
from agentnodes.foundation.llm import LLMConfig, LLMResponse, ToolCall

# ── Helpers ──────────────────────────────────────────────────────────


class RecordingMockLLM:
    """Mock that records (messages, kwargs) per call and returns queued responses."""

    def __init__(self, responses: list[LLMResponse], sleep_s: float = 0.0):
        self.responses = list(responses)
        self.calls: list[tuple[list[dict], dict]] = []
        self.sleep_s = sleep_s

    def chat(self, messages, **kwargs):
        self.calls.append((list(messages), dict(kwargs)))
        if self.sleep_s:
            time.sleep(self.sleep_s)
        if not self.responses:
            raise RuntimeError("RecordingMockLLM exhausted")
        return self.responses.pop(0)


class SlowAsyncMockLLM:
    """Async mock whose achat sleeps past the iteration timeout."""

    def __init__(self, sleep_s: float):
        self.sleep_s = sleep_s
        self.calls = 0

    async def achat(self, messages, **kwargs):
        self.calls += 1
        await asyncio.sleep(self.sleep_s)
        return LLMResponse(content="late", tool_calls=[], finish_reason="stop")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "strategies" / "foo").mkdir(parents=True)
    return tmp_path


def text_resp(content: str) -> LLMResponse:
    return LLMResponse(content=content, tool_calls=[], finish_reason="stop")


def tool_resp(tid: str) -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=[ToolCall(id=tid, name="list_history", arguments={})],
        finish_reason="tool_calls",
    )


def make_loop(workspace: Path, **kw) -> AgentLoop:
    return AgentLoop(
        stream_mode=False,
        config=LLMConfig(api_key="sk-test"),
        registry=build_default_registry(),
        workspace=workspace,
        **kw,
    )


# ── Constants ────────────────────────────────────────────────────────


class TestConstants:
    def test_wrap_up_ratio_value(self):
        assert WRAP_UP_RATIO == 0.8

    def test_nudge_text_nonempty(self):
        assert "Wrap-up" in WRAP_UP_NUDGE_TEXT


# ── Wrap-up nudge ────────────────────────────────────────────────────


class TestWrapUpNudge:
    def test_nudge_injected_once_at_80pct(self, workspace):
        # max_iter=5 → nudge at iteration int(5*0.8)=4.
        # no_progress_window=99: 4 identical list_history calls would
        # otherwise trip the no-progress approval gate (blocks 30 min).
        mock = RecordingMockLLM([tool_resp("t1"), tool_resp("t2"),
                                 tool_resp("t3"), tool_resp("t4"),
                                 text_resp("done")])
        loop = make_loop(
            workspace, max_iterations=5, wrap_up_nudge=True,
            no_progress_window=99,
        )
        patch_llm_client(loop, mock.chat)
        r = loop.run("task")
        assert r.finished_reason == "stop"

        # The nudge persists in messages; count occurrences in the
        # final call's snapshot (accumulated view), not across calls.
        final_msgs = mock.calls[-1][0]
        nudge_count = sum(
            1 for m in final_msgs
            if m.get("role") == "system" and m.get("content") == WRAP_UP_NUDGE_TEXT
        )
        assert nudge_count == 1
        # The nudge is visible from call 4 onward (1-indexed iteration 4).
        msgs_at_call4 = mock.calls[3][0]
        assert any(
            m.get("role") == "system" and m.get("content") == WRAP_UP_NUDGE_TEXT
            for m in msgs_at_call4
        )
        # Not yet visible at call 3.
        msgs_at_call3 = mock.calls[2][0]
        assert not any(
            m.get("role") == "system" and m.get("content") == WRAP_UP_NUDGE_TEXT
            for m in msgs_at_call3
        )

    def test_nudge_disabled_by_default(self, workspace):
        mock = RecordingMockLLM([tool_resp("t1"), tool_resp("t2"),
                                 tool_resp("t3"), tool_resp("t4"),
                                 text_resp("done")])
        loop = make_loop(
            workspace, max_iterations=5, no_progress_window=99,
        )
        patch_llm_client(loop, mock.chat)
        r = loop.run("task")
        assert r.finished_reason == "stop"
        for msgs, _kw in mock.calls:
            assert not any(
                m.get("role") == "system" and m.get("content") == WRAP_UP_NUDGE_TEXT
                for m in msgs
            )

    def test_nudge_single_iteration_loop(self, workspace):
        # max_iter=1 → nudge at max(1, int(0.8)) = 1 (same iteration as
        # the only LLM call; must not crash and must inject exactly once).
        mock = RecordingMockLLM([text_resp("quick")])
        loop = make_loop(workspace, max_iterations=1, wrap_up_nudge=True)
        patch_llm_client(loop, mock.chat)
        r = loop.run("task")
        assert r.finished_reason == "stop"
        assert any(
            m.get("content") == WRAP_UP_NUDGE_TEXT for m in mock.calls[0][0]
        )


# ── Force final text ─────────────────────────────────────────────────


class TestForceFinalText:
    def test_final_call_has_no_tools(self, workspace):
        mock = RecordingMockLLM([tool_resp("t1"), tool_resp("t2"), text_resp("done")])
        loop = make_loop(workspace, max_iterations=3, force_final_text=True)
        patch_llm_client(loop, mock.chat)
        r = loop.run("task")
        assert r.finished_reason == "stop"
        tools_per_call = [kw.get("tools") for _m, kw in mock.calls]
        assert tools_per_call[0] is not None
        assert tools_per_call[1] is not None
        assert tools_per_call[2] is None  # final iteration: tools=None

    def test_tools_present_by_default(self, workspace):
        mock = RecordingMockLLM([tool_resp("t1"), tool_resp("t2"), text_resp("done")])
        loop = make_loop(workspace, max_iterations=3)
        patch_llm_client(loop, mock.chat)
        r = loop.run("task")
        assert r.finished_reason == "stop"
        for _m, kw in mock.calls:
            assert kw.get("tools") is not None

    def test_early_stop_never_reaches_final(self, workspace):
        # stop on iteration 1 → all calls carry tools.
        mock = RecordingMockLLM([text_resp("immediate")])
        loop = make_loop(workspace, max_iterations=3, force_final_text=True)
        patch_llm_client(loop, mock.chat)
        r = loop.run("task")
        assert r.finished_reason == "stop"
        assert mock.calls[0][1].get("tools") is not None


# ── Iteration timeout ────────────────────────────────────────────────


class TestIterationTimeout:
    def test_timeout_reported(self, workspace):
        """超时 → finished_reason="timeout"（引擎无关的断言目标）。

        **统一引擎 U5**：``run()`` 现在走 ``arun()``（async）→ 超时由
        ``asyncio.wait_for`` **主动取消**（``_with_iteration_timeout``）；
        legacy 的同步路径是**事后测量**（``_with_iteration_timeout_sync``
        ——同步阻塞无法安全取消，见 loop.py:816-820 的注释）。

        两者对调用方的**可见结论一致**（timeout / error 含 "exceeded" /
        not success），差别只在**机制**。本测试断言可见结论，用 **async
        替身**（``SlowAsyncMockLLM``）——同步 ``time.sleep`` 的替身在
        async 路径上不可取消，会挂住而不是超时（实测）。
        """
        loop = make_loop(workspace, iteration_timeout_s=0.05)
        slow = SlowAsyncMockLLM(sleep_s=0.5)
        patch_llm_client(loop, slow.achat)
        r = loop.run("task")
        assert r.finished_reason == "timeout"
        assert "exceeded" in (r.error or "")
        assert not r.success

    def test_sync_no_timeout_when_fast(self, workspace):
        mock = RecordingMockLLM([text_resp("fast")])
        loop = make_loop(workspace, iteration_timeout_s=5.0)
        patch_llm_client(loop, mock.chat)
        r = loop.run("task")
        assert r.finished_reason == "stop"
        assert r.success

    def test_async_timeout_wait_for_cancels(self, workspace):
        loop = make_loop(workspace, iteration_timeout_s=0.05)
        slow = SlowAsyncMockLLM(sleep_s=0.5)
        loop.client.achat = slow.achat
        r = asyncio.run(loop.arun("task"))
        assert r.finished_reason == "timeout"
        assert not r.success
        assert slow.calls == 1  # cancelled once, loop broke

    def test_async_no_timeout_when_fast(self, workspace):
        loop = make_loop(workspace, iteration_timeout_s=5.0)

        async def fast_achat(messages, **kwargs):
            return LLMResponse(content="ok", tool_calls=[], finish_reason="stop")

        loop.client.achat = fast_achat
        r = asyncio.run(loop.arun("task"))
        assert r.finished_reason == "stop"
        assert r.success


# ── Effective max iterations ─────────────────────────────────────────


class TestEffectiveMaxIterations:
    def test_constructor_value_when_no_strategy(self, workspace):
        loop = make_loop(workspace, max_iterations=7)
        assert loop._effective_max_iterations() == 7
        assert loop._is_final_iteration(7)
        assert not loop._is_final_iteration(6)

    def test_explicit_strategy_config_wins(self, workspace):
        loop = make_loop(
            workspace, max_iterations=10,
            strategy={"name": "react", "config": {"max_iterations": 4}},
        )
        assert loop._effective_max_iterations() == 4

    def test_force_final_text_respects_strategy_cap(self, workspace):
        # strategy cap 2 wins over constructor 10: final call (iter 2)
        # has tools=None.
        mock = RecordingMockLLM([tool_resp("t1"), text_resp("done")])
        loop = make_loop(
            workspace, max_iterations=10,
            strategy={"name": "react", "config": {"max_iterations": 2}},
            force_final_text=True,
        )
        patch_llm_client(loop, mock.chat)
        r = loop.run("task")
        assert r.iterations == 2
        assert mock.calls[0][1].get("tools") is not None
        assert mock.calls[1][1].get("tools") is None


# ── Backward compat ──────────────────────────────────────────────────


class TestBackwardCompat:
    def test_defaults_keep_legacy_behavior(self, workspace):
        mock = RecordingMockLLM([tool_resp("t1"), text_resp("done")])
        loop = make_loop(workspace, max_iterations=5)
        patch_llm_client(loop, mock.chat)
        r = loop.run("task")
        # No nudge, tools on every call, no timeout, normal stop.
        assert r.finished_reason == "stop"
        assert r.answer == "done"
        for msgs, kw in mock.calls:
            assert kw.get("tools") is not None
            assert not any(m.get("content") == WRAP_UP_NUDGE_TEXT for m in msgs)
