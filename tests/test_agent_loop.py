"""Tests for AgentLoop (mock LLM)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from strategy_research.core.agent.builtin_tools import build_default_registry
from strategy_research.core.agent.loop import AgentLoop, LoopResult, _tool_call_hash
from strategy_research.core.agent.tools import ToolRegistry
from strategy_research.foundation.llm import LLMConfig, LLMResponse, ToolCall
from strategy_research.foundation.llm.errors import LLMAuthError

# ── Helpers ──────────────────────────────────────────────────────────


class MockLLM:
    """Simple mock that returns queued LLMResponse objects."""

    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.calls: list[int] = []

    def chat(self, messages, **kwargs):
        self.calls.append(len(messages))
        if not self.responses:
            raise RuntimeError("MockLLM exhausted; no more responses queued")
        return self.responses.pop(0)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "strategies" / "foo").mkdir(parents=True)
    return tmp_path


def text_resp(content: str, **kwargs) -> LLMResponse:
    return LLMResponse(content=content, tool_calls=[], finish_reason="stop", **kwargs)


def tool_resp(tool_calls: list[ToolCall], content: str | None = None, **kwargs) -> LLMResponse:
    return LLMResponse(
        content=content, tool_calls=tool_calls,
        finish_reason="tool_calls", **kwargs,
    )


# ── Basic loop ───────────────────────────────────────────────────────


class TestBasicLoop:
    def test_single_iteration_stop(self, workspace):
        mock = MockLLM([text_resp("the answer")])
        loop = AgentLoop(
            stream_mode=False,
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            max_iterations=5,
        )
        loop.client.chat = mock.chat
        r = loop.run("hello")
        assert r.iterations == 1
        assert r.answer == "the answer"
        assert r.finished_reason == "stop"
        assert r.success
        assert r.tool_calls_made == 0

    def test_single_tool_call_then_answer(self, workspace):
        mock = MockLLM([
            tool_resp([ToolCall(id="c1", name="list_history", arguments={})]),
            text_resp("done"),
        ])
        loop = AgentLoop(
            stream_mode=False,
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
        )
        loop.client.chat = mock.chat
        r = loop.run("improve")
        assert r.iterations == 2
        assert r.tool_calls_made == 1
        assert r.answer == "done"
        assert r.finished_reason == "stop"

    def test_multiple_tool_calls_in_one_response(self, workspace):
        mock = MockLLM([
            tool_resp([
                ToolCall(id="c1", name="read", arguments={"path": "README.md"}),
                ToolCall(id="c2", name="list_history", arguments={}),
            ]),
            text_resp("got both"),
        ])
        loop = AgentLoop(
            stream_mode=False,
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
        )
        loop.client.chat = mock.chat
        r = loop.run("multi")
        assert r.tool_calls_made == 2
        assert r.answer == "got both"


# ── max_iterations ───────────────────────────────────────────────────


class TestMaxIterations:
    def test_max_iterations_reached(self, workspace):
        # Different tool calls each iter to avoid no_progress trigger
        mock = MockLLM([
            tool_resp([ToolCall(id=f"c{i}", name="read",
                                arguments={"path": f"file_{i}.txt"})])
            for i in range(5)
        ])
        loop = AgentLoop(
            stream_mode=False,
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            max_iterations=3,
            no_progress_window=10,  # disable no_progress for this test
        )
        loop.client.chat = mock.chat
        r = loop.run("endless")
        assert r.iterations == 3
        assert r.finished_reason == "max_iter"
        assert r.tool_calls_made == 3
        assert "max_iterations" in r.answer.lower() or r.answer == ""


# ── No-progress detection ───────────────────────────────────────────


class TestNoProgress:
    def test_same_tool_call_3_times_triggers_no_progress(self, workspace):
        # Always call list_history with same args. The no-progress path
        # now routes through the approval gate: short timeout + reject
        # policy keeps the test non-blocking.
        mock = MockLLM([
            tool_resp([ToolCall(id=f"c{i}", name="list_history", arguments={})])
            for i in range(5)
        ])
        loop = AgentLoop(
            stream_mode=False,
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            max_iterations=10,
            no_progress_window=3,
            approval_timeout=0.1,
            approval_on_timeout="reject",
        )
        loop.client.chat = mock.chat
        r = loop.run("loop")
        assert r.finished_reason == "approval_timeout_rejected"
        assert "no-progress approval timed out" in r.answer

    def test_different_tool_calls_continue(self, workspace):
        # Each call has different arguments → no_progress NOT triggered
        mock = MockLLM([
            tool_resp([ToolCall(id="c1", name="read",
                                arguments={"path": f"file_{i}.py"})])
            for i in range(5)
        ] + [text_resp("done")])
        loop = AgentLoop(
            stream_mode=False,
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            max_iterations=10,
            no_progress_window=3,
        )
        loop.client.chat = mock.chat
        r = loop.run("varied")
        assert r.finished_reason == "stop"
        assert r.iterations == 6

    def test_no_progress_window_configurable(self, workspace):
        # With window=2, 2 identical calls triggers (approval gate
        # configured to reject immediately on timeout).
        mock = MockLLM([
            tool_resp([ToolCall(id="c1", name="list_history", arguments={})]),
            tool_resp([ToolCall(id="c2", name="list_history", arguments={})]),
        ])
        loop = AgentLoop(
            stream_mode=False,
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            max_iterations=10,
            no_progress_window=2,
            approval_timeout=0.1,
            approval_on_timeout="reject",
        )
        loop.client.chat = mock.chat
        r = loop.run("window")
        assert r.finished_reason == "approval_timeout_rejected"


# ── Error handling ───────────────────────────────────────────────────


class TestErrorHandling:
    def test_llm_error_mid_loop(self, workspace):
        mock = MockLLM([
            tool_resp([ToolCall(id="c1", name="read",
                                arguments={"path": "file.txt"})]),
            # Second call raises
        ])
        loop = AgentLoop(
            stream_mode=False,
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            no_progress_window=10,
        )

        def side_effect(messages, **kwargs):
            mock.calls.append(len(messages))
            if len(mock.calls) >= 2:
                raise LLMAuthError("auth failed")
            return mock.responses.pop(0)

        loop.client.chat = side_effect
        r = loop.run("err")
        assert r.finished_reason == "error"
        assert "LLMAuthError" in r.error
        assert "auth failed" in r.error

    def test_tool_execution_error_does_not_crash(self, workspace):
        mock = MockLLM([
            tool_resp([ToolCall(id="c1", name="read",
                                arguments={"path": "nonexistent.txt"})]),
            text_resp("ok"),
        ])
        loop = AgentLoop(
            stream_mode=False,
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
        )
        loop.client.chat = mock.chat
        r = loop.run("missing file")
        # Tool error should be reported in tool_result msg, loop continues
        assert r.iterations == 2
        assert r.finished_reason == "stop"

    def test_unknown_tool_returns_error_message(self, workspace):
        mock = MockLLM([
            tool_resp([ToolCall(id="c1", name="nonexistent_tool",
                                arguments={"foo": "bar"})]),
            text_resp("ok"),
        ])
        loop = AgentLoop(
            stream_mode=False,
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
        )
        loop.client.chat = mock.chat
        r = loop.run("bad tool")
        assert r.iterations == 2


# ── Workspace injection ─────────────────────────────────────────────


class TestWorkspaceInjection:
    def test_workspace_injected_into_tool_call(self, workspace):
        # Tool call without workspace kwarg → should be auto-injected
        from strategy_research.core.agent.tools import BaseTool
        captured_kwargs = []

        class FakeTool(BaseTool):
            name = "fake_tool"
            description = "test"
            parameters = {"type": "object", "properties": {}}

            def execute(self, **kwargs):
                captured_kwargs.append(kwargs)
                return json.dumps({"ok": True})

        reg = ToolRegistry()
        reg.register(FakeTool())

        mock = MockLLM([
            tool_resp([ToolCall(id="c1", name="fake_tool", arguments={"x": 1})]),
            text_resp("done"),
        ])
        loop = AgentLoop(
            stream_mode=False,
            config=LLMConfig(api_key="sk-test"),
            registry=reg,
            workspace=workspace,
        )
        loop.client.chat = mock.chat
        loop.run('inject')
        assert "workspace" in captured_kwargs[0]
        assert captured_kwargs[0]["workspace"] == workspace
        assert captured_kwargs[0]["x"] == 1


# ── Messages history ───────────────────────────────────────────────


class TestMessagesHistory:
    def test_messages_populated(self, workspace):
        mock = MockLLM([text_resp("final")])
        loop = AgentLoop(
            stream_mode=False,
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
        )
        loop.client.chat = mock.chat
        r = loop.run("test")
        # 2 messages initially (system + user), then 1 assistant
        assert len(r.messages) == 3
        assert r.messages[0]["role"] == "system"
        assert r.messages[1]["role"] == "user"
        assert r.messages[2]["role"] == "assistant"

    def test_messages_with_tool_calls(self, workspace):
        mock = MockLLM([
            tool_resp([ToolCall(id="c1", name="list_history", arguments={})]),
            text_resp("final"),
        ])
        loop = AgentLoop(
            stream_mode=False,
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
        )
        loop.client.chat = mock.chat
        r = loop.run("tooling")
        # system + user + assistant(tool_call) + tool + assistant(final)
        assert len(r.messages) == 5
        assert r.messages[0]["role"] == "system"
        assert r.messages[1]["role"] == "user"
        assert r.messages[2]["role"] == "assistant"
        assert "tool_calls" in r.messages[2]
        assert r.messages[3]["role"] == "tool"
        assert r.messages[3]["tool_call_id"] == "c1"
        assert r.messages[4]["role"] == "assistant"


# ── LoopResult dataclass ────────────────────────────────────────────


class TestLoopResult:
    def test_default_values(self):
        r = LoopResult()
        assert r.answer == ""
        assert r.iterations == 0
        assert r.tool_calls_made == 0
        assert r.finished_reason == "stop"
        assert r.error is None
        assert r.messages == []
        assert r.success is False  # no answer

    def test_success_with_answer(self):
        r = LoopResult(answer="done", iterations=1, finished_reason="stop")
        assert r.success is True

    def test_max_iter_with_answer_is_success(self):
        r = LoopResult(answer="partial", iterations=10, finished_reason="max_iter")
        assert r.success is True  # has answer

    def test_error_not_success(self):
        r = LoopResult(finished_reason="error", error="boom")
        assert r.success is False


# ── Hash helper ─────────────────────────────────────────────────────


class TestHashHelper:
    def test_same_call_same_hash(self):
        tc1 = ToolCall(id="c1", name="foo", arguments={"a": 1})
        tc2 = ToolCall(id="c2", name="foo", arguments={"a": 1})
        assert _tool_call_hash(tc1) == _tool_call_hash(tc2)

    def test_different_args_different_hash(self):
        tc1 = ToolCall(id="c1", name="foo", arguments={"a": 1})
        tc2 = ToolCall(id="c2", name="foo", arguments={"a": 2})
        assert _tool_call_hash(tc1) != _tool_call_hash(tc2)

    def test_different_name_different_hash(self):
        tc1 = ToolCall(id="c1", name="foo", arguments={"a": 1})
        tc2 = ToolCall(id="c2", name="bar", arguments={"a": 1})
        assert _tool_call_hash(tc1) != _tool_call_hash(tc2)

    def test_hash_is_short(self):
        tc = ToolCall(id="c", name="foo", arguments={})
        assert len(_tool_call_hash(tc)) == 12


# ── Integration ─────────────────────────────────────────────────────


class TestIntegration:
    def test_full_run_with_tool_calls_and_final_answer(self, workspace):
        # Realistic flow: LLM reads history, modifies strategy, runs backtest, gives summary
        mock = MockLLM([
            tool_resp([ToolCall(id="c1", name="read",
                                arguments={"path": "strategies/foo/strategy.py"})]),
            tool_resp([ToolCall(id="c2", name="write",
                                arguments={"path": "strategies/foo/strategy.py",
                                           "content": "# updated\nx = 1\n"})]),
            text_resp("Strategy updated with momentum_20_60 factor."),
        ])
        loop = AgentLoop(
            stream_mode=False,
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
        )
        loop.client.chat = mock.chat
        r = loop.run("update momentum strategy")
        assert r.iterations == 3
        assert r.tool_calls_made == 2
        assert r.finished_reason == "stop"
        assert "momentum_20_60" in r.answer
        assert r.success

    def test_run_with_no_max_iterations_unlimited(self, workspace):
        # All calls return tool_calls forever; verify the no-progress
        # approval gate stops it (short timeout + reject policy).
        mock = MockLLM([
            tool_resp([ToolCall(id="c1", name="list_history", arguments={})])
            for _ in range(20)
        ])
        loop = AgentLoop(
            stream_mode=False,
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            max_iterations=100,
            no_progress_window=3,
            approval_timeout=0.1,
            approval_on_timeout="reject",
        )
        loop.client.chat = mock.chat
        r = loop.run("loopy")
        assert r.finished_reason == "approval_timeout_rejected"
        assert r.iterations == 3  # stopped at window=3


# ── Allowed tools filtering ──────────────────────────────────────────


class TestAllowedTools:
    def test_allowed_tools_filtering(self, workspace):
        """allowed_tools 白名单只保留指定工具"""
        loop = AgentLoop(
            stream_mode=False,
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            allowed_tools=["read", "list_history"],
        )
        assert loop.registry.get("read") is not None
        assert loop.registry.get("list_history") is not None
        assert loop.registry.get("write") is None
        assert loop.registry.get("run_backtest") is None

    def test_allowed_tools_none_means_all(self, workspace):
        """allowed_tools=None 时全部工具保留"""
        loop = AgentLoop(
            stream_mode=False,
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            allowed_tools=None,
        )
        assert loop.registry.get("read") is not None
        assert loop.registry.get("write") is not None
        assert loop.registry.get("run_backtest") is not None


# ── Readonly mode ────────────────────────────────────────────────────


class TestReadonly:
    def test_readonly_filters_write_file(self, workspace):
        """readonly=True 时 write_file 被过滤"""
        loop = AgentLoop(
            stream_mode=False,
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            readonly=True,
        )
        assert loop.registry.get("write") is None

    def test_readonly_filters_run_backtest(self, workspace):
        """readonly=True 时 run_backtest 被过滤（写工具，产 runs/）"""
        loop = AgentLoop(
            stream_mode=False,
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            readonly=True,
        )
        assert loop.registry.get("run_backtest") is None

    def test_readonly_preserves_read_tools(self, workspace):
        """readonly=True 时所有只读工具保留"""
        loop = AgentLoop(
            stream_mode=False,
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            readonly=True,
        )
        assert loop.registry.get("read") is not None
        assert loop.registry.get("list_history") is not None
        assert loop.registry.get("git_diff") is not None
        assert loop.registry.get("compute_factor") is not None

    def test_readonly_false_keeps_all(self, workspace):
        """readonly=False 时全部工具保留"""
        loop = AgentLoop(
            stream_mode=False,
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            readonly=False,
        )
        assert loop.registry.get("write") is not None

    @pytest.mark.parametrize("write_tool", [
        "write",
        "run_backtest",
        "get_market_data",
        "import_data",
        "clean_data",
        "create_goal",
        "add_evidence",
        "complete_goal",
        "shell",
    ])
    def test_readonly_filters_every_write_tool(self, workspace, write_tool):
        """readonly=True 过滤全部 is_readonly=False 工具。"""
        loop = AgentLoop(
            stream_mode=False,
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            readonly=True,
        )
        assert loop.registry.get(write_tool) is None

    def test_readonly_filters_by_is_readonly_metadata(self, workspace):
        """元数据驱动：readonly 过滤 = 全部 is_readonly=False 工具被滤。"""
        full = build_default_registry()
        loop = AgentLoop(
            stream_mode=False,
            config=LLMConfig(api_key="sk-test"),
            registry=full,
            workspace=workspace,
            readonly=True,
        )
        for name in full.tool_names:
            is_readonly = getattr(full.get(name), "is_readonly", True)
            if is_readonly:
                assert loop.registry.get(name) is not None, name
            else:
                assert loop.registry.get(name) is None, name


# ── Run with context ─────────────────────────────────────────────────


class TestRunWithContext:
    def test_context_prepended_to_task(self, workspace):
        """run(context=...) 把 context 拼在 task 前面"""
        mock = MockLLM([text_resp("done")])
        loop = AgentLoop(
            stream_mode=False,
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
        )
        loop.client.chat = mock.chat
        r = loop.run("improve", context="## 当前状态\ncalmar=0.42")
        user_msg = r.messages[1]["content"]
        assert "calmar=0.42" in user_msg
        assert "improve" in user_msg

    def test_run_without_context_unchanged(self, workspace):
        """不传 context 时行为不变"""
        mock = MockLLM([text_resp("done")])
        loop = AgentLoop(
            stream_mode=False,
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
        )
        loop.client.chat = mock.chat
        r = loop.run("hello")
        assert r.answer == "done"
        assert r.finished_reason == "stop"


# ── Custom system prompt ─────────────────────────────────────────────


class TestCustomSystemPrompt:
    def test_custom_system_prompt_used(self, workspace):
        """传入 system_prompt 时使用自定义 prompt"""
        mock = MockLLM([text_resp("done")])
        custom = "你是风险控制员。{tool_list}"
        loop = AgentLoop(
            stream_mode=False,
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            system_prompt=custom,
        )
        loop.client.chat = mock.chat
        r = loop.run("check risk")
        # system message 应该包含自定义 prompt
        sys_msg = r.messages[0]["content"]
        assert "风险控制员" in sys_msg
        assert "read" in sys_msg  # {tool_list} 被替换
