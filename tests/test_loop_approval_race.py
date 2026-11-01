"""竞态复现：审批决策在 gate 等待前到达就被丢弃（产品缺陷，非测试脆弱）。

背景：2026-10-31 最终全量里
``tests/test_p0_loop_fixes.py::TestNoProgressApprovedContinuation::test_rejected_no_progress_exits``
失败过一次（`user_rejected` 变成 `approval_timeout_rejected`），单跑必过。
根因不是测试时序，而是 ``_check_no_progress`` 的这段：

    self._approval_event.clear()
    if self._approval_response is not None:
        self._approval_response = None      # 丢掉提前到达的决策
    approved = self._approval_event.wait(timeout=...)

用户在 agent 走到 gate **之前**点了批准/拒绝（生产里人机交互的正常时序），
决策被 clear/drain 静默丢弃 → 阻塞到 ``approval_timeout``（默认 1800s）。

本文件用**确定性方式**复现（不靠 sleep 竞速）：直接模拟「决策先到」。
"""
from __future__ import annotations

import threading
import time

import pytest

from strategy_research.core.agent.builtin_tools import build_default_registry
from strategy_research.core.agent.loop import AgentLoop
from strategy_research.core.llm import LLMConfig, LLMResponse, ToolCall


class MockLLM:
    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)

    def chat(self, messages, **kwargs):
        if not self.responses:
            raise RuntimeError("MockLLM exhausted")
        return self.responses.pop(0)


def tool_resp(tool_calls: list[ToolCall]) -> LLMResponse:
    return LLMResponse(content="", tool_calls=tool_calls, finish_reason="tool_calls")


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "strategies" / "foo").mkdir(parents=True)
    return tmp_path


def _loop(workspace, *, timeout: float = 5.0) -> AgentLoop:
    mock = MockLLM([
        tool_resp([ToolCall(id="c1", name="list_history", arguments={})]),
        tool_resp([ToolCall(id="c2", name="list_history", arguments={})]),
        tool_resp([ToolCall(id="c3", name="list_history", arguments={})]),
    ])
    loop = AgentLoop(
        stream_mode=False,
        config=LLMConfig(api_key="sk-test"),
        registry=build_default_registry(),
        workspace=workspace,
        max_iterations=10,
        no_progress_window=3,
        approval_timeout=timeout,
        approval_on_timeout="reject",
    )
    loop.client.chat = mock.chat
    return loop


def test_early_reject_is_not_discarded(workspace):
    """决策在 gate 打开前到达 → 必须生效，不能被 drain 丢掉。

    这是确定性的：approve_loop 在 run() 之前调用，gate 尚未 clear/等待。
    """
    loop = _loop(workspace)
    loop.approve_loop("reject")      # 用户提前点了拒绝
    r = loop.run("loop")
    assert r.finished_reason == "user_rejected", (
        f"提前到达的 reject 被丢弃了：{r.finished_reason}"
    )


def test_early_approve_is_not_discarded(workspace):
    """对称场景：提前批准 → 应继续循环，而不是阻塞到超时。"""
    loop = _loop(workspace)
    loop.approve_loop("approved")
    started = time.perf_counter()
    r = loop.run("loop")
    elapsed = time.perf_counter() - started
    assert r.finished_reason != "approval_timeout_rejected"
    # 若决策被丢弃，会阻塞满 approval_timeout（5s）；此处必须远小于它
    assert elapsed < 3.0, f"决策被丢弃，阻塞了 {elapsed:.1f}s"


def test_stale_decision_does_not_leak_into_next_gate(workspace):
    """上一个 gate 的残留决策不得被下一个 gate 复用。

    两轮 gate：第一轮 approved（消费），第二轮不响应 → 走超时策略 reject。
    若实现只是"不清"，第二轮会误用第一轮的 approved。
    """
    mock = MockLLM([
        tool_resp([ToolCall(id=f"c{i}", name="list_history", arguments={})])
        for i in range(1, 7)
    ])
    loop = AgentLoop(
        stream_mode=False,
        config=LLMConfig(api_key="sk-test"),
        registry=build_default_registry(),
        workspace=workspace,
        max_iterations=20,
        no_progress_window=3,
        approval_timeout=0.3,
        approval_on_timeout="reject",
    )
    loop.client.chat = mock.chat
    loop.approve_loop("approved")     # 只够第一轮

    r = loop.run("loop")
    # 第二轮无决策 → 超时 reject；证明第一轮的 approved 没被复用
    assert r.finished_reason == "approval_timeout_rejected", r.finished_reason
