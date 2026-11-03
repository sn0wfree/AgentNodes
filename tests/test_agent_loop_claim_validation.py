"""Integration tests: claim validation wired into AgentLoop.

See docs/claim-validation-badge-design.md §7.2.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from strategy_research.core.agent.builtin_tools import build_default_registry
from strategy_research.core.agent.loop import AgentLoop
from strategy_research.foundation.llm import LLMConfig, LLMResponse, ToolCall


class MockLLM:
    """Simple mock that returns queued LLMResponse objects."""

    def __init__(self, responses):
        self.responses = list(responses)

    def chat(self, messages, **kwargs):
        if not self.responses:
            raise RuntimeError("MockLLM exhausted")
        return self.responses.pop(0)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "strategies" / "foo").mkdir(parents=True)
    return tmp_path


def text_resp(content: str) -> LLMResponse:
    return LLMResponse(content=content, tool_calls=[], finish_reason="stop")


def _build_loop(workspace, **overrides):
    loop = AgentLoop(
        stream_mode=False,
        config=LLMConfig(api_key="sk-test"),
        registry=build_default_registry(),
        workspace=workspace,
        max_iterations=5,
        **overrides,
    )
    return loop


class TestClaimValidationEnabled:
    def test_enabled_writes_metrics(self, workspace):
        mock = MockLLM([text_resp("回测完成，sharpe 是 1.42")])
        loop = _build_loop(workspace, enable_claim_validation=True)
        loop.client.chat = mock.chat
        r = loop.run("跑回测")
        assert "claim_validation" in r.metrics
        cv = r.metrics["claim_validation"]
        assert cv["total_claims"] >= 1
        assert "sharpe=1.42" in cv["verified"] or "sharpe=1.42" in cv["unverified"]

    def test_enabled_verifies_against_tool_results(self, workspace):
        """工具返回值里的数字 → 答案引用时 verified."""
        # First turn calls list_history (a tool that returns something);
        # second turn claims sharpe that won't match → unverified.
        mock = MockLLM([
            LLMResponse(
                content="",
                tool_calls=[ToolCall(id="c1", name="list_history", arguments={})],
                finish_reason="tool_calls",
            ),
            text_resp("sharpe 是 9.99"),
        ])
        loop = _build_loop(workspace, enable_claim_validation=True)
        loop.client.chat = mock.chat
        r = loop.run("回测")
        cv = r.metrics["claim_validation"]
        assert not cv["ok"]
        assert "sharpe=9.99" in cv["unverified"]

    def test_disabled_default(self, workspace):
        """默认关闭 → 无 claim_validation 键（向后兼容）。"""
        mock = MockLLM([text_resp("sharpe 是 1.5")])
        loop = _build_loop(workspace)  # 不传 enable_claim_validation
        loop.client.chat = mock.chat
        r = loop.run("hello")
        assert "claim_validation" not in r.metrics


class TestStrictMode:
    def test_strict_rewrites_answer(self, workspace):
        mock = MockLLM([text_resp("sharpe 是 2.5")])
        loop = _build_loop(
            workspace,
            enable_claim_validation=True,
            strict_claim_validation=True,
        )
        loop.client.chat = mock.chat
        r = loop.run("hello")
        assert "数据真实性警告" in r.answer
        assert "sharpe=2.5" in r.answer

    def test_strict_does_not_rewrite_when_verified(self, workspace):
        """strict 模式 + 数字可追溯 → 不追加警告."""
        # Custom tool that returns a sharpe the model then references.
        from strategy_research.core.agent.tools import BaseTool, ToolContext

        class FakeBacktest(BaseTool):
            name = "fake_backtest"
            description = "返回回测结果"
            category = "回测"

            def execute(self, ctx: ToolContext, **kwargs) -> str:
                return '{"sharpe": 1.0, "annual_return": 0.10}'

        registry = build_default_registry()
        registry.register(FakeBacktest())

        mock = MockLLM([
            LLMResponse(
                content="",
                tool_calls=[ToolCall(id="c1", name="fake_backtest", arguments={})],
                finish_reason="tool_calls",
            ),
            text_resp("sharpe 是 1.0"),
        ])
        loop = AgentLoop(
            stream_mode=False,
            config=LLMConfig(api_key="sk-test"),
            registry=registry,
            workspace=workspace,
            max_iterations=5,
            enable_claim_validation=True,
            strict_claim_validation=True,
        )
        loop.client.chat = mock.chat
        r = loop.run("跑回测")
        cv = r.metrics["claim_validation"]
        assert cv["ok"], f"claims should verify, got {cv}"
        assert "数据真实性警告" not in r.answer
