"""Tests for SubAgentTool — chat-mode subagent delegation.

Covers:
  * Chat generates 2 subagents in one turn (parent AgentLoop calls
    delegate_to_agent twice, each spawns a child SwarmWorker).
  * SubAgentTool count limit (MAX_SUBAGENTS = 5) enforced.
  * No nested delegation — the child registry excludes delegate_to_agent.
  * subagent_* SSE events emitted with distinct agent_ids.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from strategy_research.kernel.agent.builtin_tools import build_default_registry
from strategy_research.kernel.agent.builtin_tools.subagent_tool import (
    MAX_SUBAGENTS,
    SubAgentTool,
)
from strategy_research.kernel.agent.loop import AgentLoop
from strategy_research.kernel.agent.tools import BaseTool, ToolRegistry
from strategy_research.foundation.llm import LLMConfig, LLMResponse, ToolCall

# ── Helpers ──────────────────────────────────────────────────────────


class AsyncMockLLM:
    """Mock parent LLM — returns queued LLMResponse from async achat()."""

    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.calls: list[int] = []

    async def achat(self, messages, **kwargs):
        self.calls.append(len(messages))
        if not self.responses:
            raise RuntimeError("AsyncMockLLM exhausted")
        return self.responses.pop(0)


class SyncMockLLM:
    """Mock child LLM — returns queued LLMResponse from sync chat().

    Used as a stand-in for the OpenAICompatClient that SubAgentTool
    constructs internally (patched via monkeypatch).
    """

    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.calls: list[int] = []

    def chat(self, messages, **kwargs):
        self.calls.append(len(messages))
        if not self.responses:
            raise RuntimeError("SyncMockLLM exhausted")
        return self.responses.pop(0)


def text_resp(content: str) -> LLMResponse:
    return LLMResponse(content=content, tool_calls=[], finish_reason="stop")


def delegate_resp(task: str, call_id: str) -> LLMResponse:
    tc = ToolCall(id=call_id, name="task", arguments={"task": task})
    return LLMResponse(content="", tool_calls=[tc], finish_reason="tool_calls")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "strategies" / "foo").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def patch_child_client(monkeypatch):
    """Make SubAgentTool's internal AgentExecutor return deterministic results.

    Yields a ``make(child_responses)`` callable that patches
    ``AgentExecutor.execute`` so each SubAgentTool.execute() produces the
    next queued LLMResponse. Returns a list capturing every patch call for
    per-subagent assertions.
    """

    def make(child_responses: list[LLMResponse]) -> list[dict[str, Any]]:
        created: list[dict[str, Any]] = []
        queue = list(child_responses)

        from strategy_research.kernel.agent import executor as executor_mod
        from strategy_research.kernel.agent.executor import AgentExecutionResult

        def fake_execute(self, plugin, task, workspace, **kwargs):
            created.append({"plugin_id": plugin.id, "task": task, "kwargs": kwargs})
            # Cycle through queued responses; fall back to a stable stub.
            resp = queue.pop(0) if queue else (child_responses[0] if child_responses else text_resp("no responses"))
            if queue:
                queue.append(resp)  # cycle
            output = resp.content or ""

            # Replay any tool_call events from the response so SSE
            # forwarding can be asserted (matches the SwarmWorker
            # callback semantics the tests expect).
            on_event = kwargs.get("on_event")
            for tc in (resp.tool_calls or []):
                if on_event is not None:
                    on_event("tool_call", {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        "id": tc.id,
                    })
                    on_event("tool_result", {
                        "tool_call_id": tc.id,
                        "name": tc.name,
                        "result": "(mocked)",
                        "status": "done",
                    })
            if output and on_event is not None:
                on_event("text_delta", {"delta": output})
            elapsed = 0.01
            return AgentExecutionResult(
                agent_id=plugin.id, status="success",
                output=output, elapsed_s=elapsed,
                summary=output[:60],
                metrics={
                    "iterations": 1,
                    "tool_calls_made": len(resp.tool_calls or []),
                    "finished_reason": "stop",
                },
            )

        monkeypatch.setattr(
            executor_mod.AgentExecutor, "execute", fake_execute,
        )
        return created

    return make


def _collect_events(loop: AgentLoop) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    loop._on_event = lambda et, data: events.append((et, data or {}))
    return events


# ── Chat generates 2 subagents ───────────────────────────────────────


class TestChatGeneratesSubagents:
    @pytest.mark.asyncio
    async def test_chat_spawns_two_subagents(self, workspace, patch_child_client):
        """Parent agent calls delegate_to_agent twice → 2 child agents run.

        The parent LLM is mocked to emit two delegate_to_agent tool calls
        then a final text answer. Each child (patched OpenAICompatClient)
        responds with its own text, so 2 subagents complete with distinct
        agent_ids and both emit subagent_started + subagent_completed.
        """
        child_clients = patch_child_client([text_resp("subagent A result")])

        parent = AsyncMockLLM([
            delegate_resp("研究动量因子", call_id="call-a"),
            delegate_resp("分析回撤", call_id="call-b"),
            text_resp("全部完成"),
        ])
        loop = AgentLoop(
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            stream_mode=False,
            max_iterations=5,
        )
        events = _collect_events(loop)
        loop.client.achat = parent.achat

        r = await loop.arun("帮我做两个分析")

        # Parent loop: 2 tool calls made, final answer reached
        assert r.tool_calls_made == 2
        assert r.answer == "全部完成"
        assert r.success

        # 2 child agents created (one per delegation)
        assert len(child_clients) == 2

        # subagent lifecycle events: 2 started, 2 completed, distinct ids
        started = [e for e in events if e[0] == "subagent_started"]
        completed = [e for e in events if e[0] == "subagent_completed"]
        assert len(started) == 2
        assert len(completed) == 2
        agent_ids = {e[1]["agent_id"] for e in started}
        assert len(agent_ids) == 2  # distinct agent_ids
        assert all(e[1]["agent_id"] in agent_ids for e in completed)

        # Each tool result contains the subagent's answer
        tool_results = [
            e[1] for e in events if e[0] == "tool_result" and e[1].get("tool") == "task"
        ]
        assert len(tool_results) == 2
        for tr in tool_results:
            assert tr["status"] == "done"
            assert "subagent A result" in tr["result"]

    @pytest.mark.asyncio
    async def test_chat_single_subagent_single_loop(self, workspace, patch_child_client):
        """A single delegation is enough to complete the turn."""
        patch_child_client([text_resp("lone subagent")])
        parent = AsyncMockLLM([
            delegate_resp("单一子任务", call_id="call-1"),
            text_resp("完成"),
        ])
        loop = AgentLoop(
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            stream_mode=False,
            max_iterations=5,
        )
        events = _collect_events(loop)
        loop.client.achat = parent.achat

        r = await loop.arun("委派一个任务")
        assert r.tool_calls_made == 1
        assert r.answer == "完成"
        started = [e for e in events if e[0] == "subagent_started"]
        assert len(started) == 1
        assert started[0][1]["message_id"] is None  # loop has no message_id


# ── Constraint: count limit ──────────────────────────────────────────


class TestSubAgentCountLimit:
    def test_max_five_delegations(self, monkeypatch):
        """SubAgentTool refuses beyond MAX_SUBAGENTS delegations per turn."""
        # Patch the child client so the first MAX_SUBAGENTS calls succeed
        # deterministically (no real LLM config / network).
        # Patch AgentExecutor.execute so the child agent returns a deterministic result.
        from strategy_research.kernel.agent import executor as executor_mod
        from strategy_research.kernel.agent.executor import AgentExecutionResult

        def _fake_exec(self, plugin, task, workspace, **kwargs):
            return AgentExecutionResult(
                agent_id=plugin.id, status="success",
                output="ok", elapsed_s=0.01, summary="ok",
                metrics={"iterations": 1, "tool_calls_made": 0, "finished_reason": "stop"},
            )
        monkeypatch.setattr(executor_mod.AgentExecutor, "execute", _fake_exec)

        tool = SubAgentTool()
        count_ref = [0]

        for i in range(MAX_SUBAGENTS):
            out = tool.execute(task=f"task {i}", _subagent_count_ref=count_ref, emit_event=None)
            assert count_ref[0] == i + 1
            parsed = json.loads(out)
            assert parsed["status"] == "ok"
            assert parsed["answer"] == "ok"

        # 6th delegation is refused BEFORE any child client is built —
        # the actionable error directs the user to the workflow path.
        out = tool.execute(task="overflow", _subagent_count_ref=count_ref, emit_event=None)
        parsed = json.loads(out)
        assert parsed["status"] == "error"
        assert "最多委派" in parsed["error"]
        assert "工作流" in parsed["error"]
        assert count_ref[0] == MAX_SUBAGENTS  # not incremented past the cap

    def test_count_ref_increments_even_without_client(self, monkeypatch):
        """Counter increments before child client construction."""
        # Patch AgentExecutor.execute so the child agent returns a deterministic result.
        from strategy_research.kernel.agent import executor as executor_mod
        from strategy_research.kernel.agent.executor import AgentExecutionResult

        def _fake_exec(self, plugin, task, workspace, **kwargs):
            return AgentExecutionResult(
                agent_id=plugin.id, status="success",
                output="ok", elapsed_s=0.01, summary="ok",
                metrics={"iterations": 1, "tool_calls_made": 0, "finished_reason": "stop"},
            )
        monkeypatch.setattr(executor_mod.AgentExecutor, "execute", _fake_exec)
        tool = SubAgentTool()
        count_ref = [0]
        tool.execute(task="x", _subagent_count_ref=count_ref, emit_event=None)
        assert count_ref[0] == 1


# ── Constraint: no nested delegation ─────────────────────────────────


class TestNoNestedDelegation:
    def test_child_registry_excludes_delegate(self):
        """Sub-agent's tool registry never contains delegate_to_agent."""
        tool = SubAgentTool()
        parent_registry = build_default_registry()
        child_registry = tool._build_child_registry(parent_registry, None)
        assert isinstance(child_registry, ToolRegistry)
        assert child_registry.get("task") is None
        # Sanity: other tools still present
        assert child_registry.get("read") is not None

    def test_child_registry_applies_whitelist(self):
        tool = SubAgentTool()
        parent_registry = build_default_registry()
        child = tool._build_child_registry(parent_registry, ["read", "write"])
        assert child.get("read") is not None
        assert child.get("write") is not None
        assert child.get("run_backtest") is None
        assert child.get("task") is None


# ── Child subagent behavior ──────────────────────────────────────────


class NoopTool(BaseTool):
    """Minimal tool registered so the child SwarmWorker has a tool to call."""

    name = "noop_tool"
    description = "no-op"
    parameters = {"type": "object", "properties": {}, "required": []}
    is_readonly = True

    def execute(self, **kwargs):
        return json.dumps({"status": "ok", "value": 42})


def child_tool_resp(name: str, args: dict | None = None, call_id: str = "c-child") -> LLMResponse:
    tc = ToolCall(id=call_id, name=name, arguments=args or {})
    return LLMResponse(content="", tool_calls=[tc], finish_reason="tool_calls")


class TestChildToolCallsForwarded:
    @pytest.mark.asyncio
    async def test_child_tool_call_and_result_events(self, workspace, patch_child_client):
        """Sub-agent's internal tool calls surface as subagent_tool_call/result."""
        # Child: calls noop_tool once, then returns text.
        patch_child_client([
            child_tool_resp("noop_tool", {}, call_id="child-call-1"),
            text_resp("subagent done"),
        ])

        parent = AsyncMockLLM([
            delegate_resp("用工具分析", call_id="call-x"),
            text_resp("完成"),
        ])
        # Build a parent registry containing the noop tool so the child
        # inherits it (minus delegate_to_agent).
        registry = build_default_registry()
        registry.register(NoopTool())
        loop = AgentLoop(
            config=LLMConfig(api_key="sk-test"),
            registry=registry,
            workspace=workspace,
            stream_mode=False,
            max_iterations=5,
        )
        events = _collect_events(loop)
        loop.client.achat = parent.achat

        r = await loop.arun("委派带工具的任务")
        assert r.tool_calls_made == 1
        assert r.answer == "完成"

        subagent_tool_calls = [e for e in events if e[0] == "subagent_tool_call"]
        subagent_tool_results = [e for e in events if e[0] == "subagent_tool_result"]
        assert len(subagent_tool_calls) == 1
        assert len(subagent_tool_results) == 1
        assert subagent_tool_calls[0][1]["name"] == "noop_tool"
        assert subagent_tool_results[0][1]["tool_call_id"] == "child-call-1"
        assert subagent_tool_results[0][1]["status"] == "done"

    @pytest.mark.asyncio
    async def test_child_text_delta_forwarded(self, workspace, patch_child_client):
        """Child final text is forwarded as a subagent_text_delta event."""
        patch_child_client([text_resp("child final text")])
        parent = AsyncMockLLM([
            delegate_resp("写文本", call_id="call-t"),
            text_resp("ok"),
        ])
        loop = AgentLoop(
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            stream_mode=False,
            max_iterations=5,
        )
        events = _collect_events(loop)
        loop.client.achat = parent.achat

        await loop.arun("委派")
        deltas = [e for e in events if e[0] == "subagent_text_delta"]
        assert len(deltas) == 1
        assert deltas[0][1]["delta"] == "child final text"

    @pytest.mark.asyncio
    async def test_subagent_failed_event_on_child_exception(self, workspace, monkeypatch):
        """If the child agent raises, subagent_failed is emitted and the
        parent tool result is an error."""

        from strategy_research.kernel.agent import executor as executor_mod

        def _explode(self, plugin, task, workspace, **kwargs):
            raise RuntimeError("child boom")

        monkeypatch.setattr(executor_mod.AgentExecutor, "execute", _explode)

        parent = AsyncMockLLM([
            delegate_resp("会失败的任务", call_id="call-f"),
            text_resp("已处理失败"),
        ])
        loop = AgentLoop(
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            stream_mode=False,
            max_iterations=5,
        )
        events = _collect_events(loop)
        loop.client.achat = parent.achat

        r = await loop.arun("委派一个会失败的任务")
        assert r.answer == "已处理失败"
        assert r.tool_calls_made == 1

        failed = [e for e in events if e[0] == "subagent_failed"]
        assert len(failed) == 1
        assert "child boom" in failed[0][1]["error"]
        # Parent sees an error tool_result for the delegation
        tool_results = [
            e[1] for e in events if e[0] == "tool_result" and e[1].get("tool") == "task"
        ]
        assert tool_results and tool_results[0]["status"] == "error"


# ── Integration: count limit through AgentLoop ───────────────────────


class TestCountLimitIntegration:
    @pytest.mark.asyncio
    async def test_five_ok_sixth_refused(self, workspace, patch_child_client):
        """AgentLoop: 5 delegations succeed, the 6th is refused with the
        workflow-path error and no 6th child client is created."""
        patch_child_client([text_resp("child ok")])
        parent = AsyncMockLLM([
            delegate_resp(f"task {i}", call_id=f"call-{i}") for i in range(6)
        ] + [text_resp("final")])
        loop = AgentLoop(
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            stream_mode=False,
            max_iterations=10,
        )
        events = _collect_events(loop)
        loop.client.achat = parent.achat

        r = await loop.arun("委派 6 个任务")
        assert r.tool_calls_made == 6
        assert r.answer == "final"

        started = [e for e in events if e[0] == "subagent_started"]
        assert len(started) == 5  # only 5 spawned
        # The 6th delegation's tool result is an error with the hint
        tool_results = [
            e[1] for e in events if e[0] == "tool_result" and e[1].get("tool") == "task"
        ]
        assert len(tool_results) == 6
        refused = [tr for tr in tool_results if tr["status"] == "error"]
        assert len(refused) == 1
        assert "最多委派" in refused[0]["result"]

    @pytest.mark.asyncio
    async def test_count_resets_between_arun_calls(self, workspace, patch_child_client):
        """The per-turn counter resets on each arun() invocation."""
        patch_child_client([text_resp('child ok')])
        parent = AsyncMockLLM([
            delegate_resp("a", call_id="c1"),
            text_resp("done1"),
        ])
        loop = AgentLoop(
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            stream_mode=False,
            max_iterations=5,
        )
        loop.client.achat = parent.achat
        r1 = await loop.arun("round 1")
        assert r1.tool_calls_made == 1
        assert loop._subagent_count[0] == 1

        # Second run: fresh counter (even though the first used 1)
        parent2 = AsyncMockLLM([
            delegate_resp("b", call_id="c2"),
            text_resp("done2"),
        ])
        loop.client.achat = parent2.achat
        r2 = await loop.arun("round 2")
        assert r2.tool_calls_made == 1
        assert loop._subagent_count[0] == 1


# ── Execute() parameter handling ─────────────────────────────────────


class TestExecuteParamHandling:
    def test_missing_task_returns_error(self, monkeypatch):
        tool = SubAgentTool()
        out = tool.execute(_subagent_count_ref=[0], emit_event=None)
        parsed = json.loads(out)
        assert parsed["status"] == "error"
        assert "task" in parsed["error"]

    def test_empty_task_returns_error(self, monkeypatch):
        tool = SubAgentTool()
        out = tool.execute(task="", _subagent_count_ref=[0], emit_event=None)
        parsed = json.loads(out)
        assert parsed["status"] == "error"

    def test_max_iterations_clamped_to_20(self, monkeypatch):
        """max_iterations > 20 is clamped to 20 (never escapes the cap)."""
        captured: dict = {}
        orig_chat = SyncMockLLM.chat

        def _spy_chat(self, messages, **kwargs):
            captured["iterations"] = len(captured.get("calls", []))
            captured.setdefault("calls", 0)
            captured["calls"] += 1
            return orig_chat(self, messages, **kwargs)

        monkeypatch.setattr(SyncMockLLM, "chat", _spy_chat)
        # Patch AgentExecutor.execute so the child agent returns a deterministic result.
        from strategy_research.kernel.agent import executor as executor_mod
        from strategy_research.kernel.agent.executor import AgentExecutionResult

        def _fake_exec(self, plugin, task, workspace, **kwargs):
            return AgentExecutionResult(
                agent_id=plugin.id, status="success",
                output="ok", elapsed_s=0.01, summary="ok",
                metrics={"iterations": 1, "tool_calls_made": 0, "finished_reason": "stop"},
            )
        monkeypatch.setattr(executor_mod.AgentExecutor, "execute", _fake_exec)
        # max_iterations=100 is clamped to 20, so the worker still runs and
        # completes on the first text response.
        tool = SubAgentTool()
        out = tool.execute(task="x", max_iterations=100, _subagent_count_ref=[0], emit_event=None)
        parsed = json.loads(out)
        assert parsed["status"] == "ok"
        assert parsed["answer"] == "ok"

    def test_message_id_and_name_in_started_event(self, monkeypatch):
        """subagent_started carries name (task preview) + message_id."""
        seen: list[tuple[str, dict]] = []

        def _emit(et, data):
            seen.append((et, data))

        # Patch AgentExecutor.execute so the child agent returns a deterministic result.
        from strategy_research.kernel.agent import executor as executor_mod
        from strategy_research.kernel.agent.executor import AgentExecutionResult

        def _fake_exec(self, plugin, task, workspace, **kwargs):
            return AgentExecutionResult(
                agent_id=plugin.id, status="success",
                output="ok", elapsed_s=0.01, summary="ok",
                metrics={"iterations": 1, "tool_calls_made": 0, "finished_reason": "stop"},
            )
        monkeypatch.setattr(executor_mod.AgentExecutor, "execute", _fake_exec)
        tool = SubAgentTool()
        tool.execute(
            task="这是一个较长的子任务描述", message_id="msg-1",
            _subagent_count_ref=[0], emit_event=_emit,
        )
        started = [e for e in seen if e[0] == "subagent_started"]
        assert len(started) == 1
        assert started[0][1]["message_id"] == "msg-1"
        assert started[0][1]["name"] == "这是一个较长的子任务描述"
        completed = [e for e in seen if e[0] == "subagent_completed"]
        assert len(completed) == 1
        assert completed[0][1]["message_id"] == "msg-1"

    def test_tool_result_contains_answer_and_metrics(self, monkeypatch):
        from strategy_research.kernel.agent import executor as executor_mod
        from strategy_research.kernel.agent.executor import AgentExecutionResult

        def _fake(self, plugin, task, workspace, **kwargs):
            return AgentExecutionResult(
                agent_id=plugin.id, status="success",
                output="the final answer", elapsed_s=0.01,
                summary="the final answer",
                metrics={"iterations": 1, "tool_calls_made": 0,
                         "finished_reason": "stop"},
            )

        monkeypatch.setattr(executor_mod.AgentExecutor, "execute", _fake)
        tool = SubAgentTool()
        out = tool.execute(task="x", _subagent_count_ref=[0], emit_event=None)
        parsed = json.loads(out)
        assert parsed["status"] == "ok"
        assert parsed["answer"] == "the final answer"
        assert parsed["iterations"] == 1
        assert parsed["tool_calls_made"] == 0


# ── Additional coverage ──────────────────────────────────────────────


class TestWhitelistAtExecute:
    def test_tools_whitelist_limits_child_registry(self, monkeypatch):
        """Passing tools=[] gives the child an empty registry; the child
        then has no tools to call (text-only worker)."""
        from strategy_research.kernel.agent import executor as executor_mod
        from strategy_research.kernel.agent.executor import AgentExecutionResult

        def _fake(self, plugin, task, workspace, **kwargs):
            return AgentExecutionResult(
                agent_id=plugin.id, status="success",
                output="text only", elapsed_s=0.01,
                summary="text only",
                metrics={"iterations": 1, "tool_calls_made": 0,
                         "finished_reason": "stop"},
            )

        monkeypatch.setattr(executor_mod.AgentExecutor, "execute", _fake)
        tool = SubAgentTool()
        parent = build_default_registry()
        out = tool.execute(
            task="x", tools=["read"], _parent_registry=parent,
            _subagent_count_ref=[0], emit_event=None,
        )
        parsed = json.loads(out)
        assert parsed["status"] == "ok"
        assert parsed["answer"] == "text only"


class TestMultiIterationChild:
    def test_child_with_tool_then_answer(self, monkeypatch):
        """A child that calls a tool then returns text completes with
        tool_calls_made=1 and iterations=2 in the returned metrics."""
        from strategy_research.kernel.agent import executor as executor_mod
        from strategy_research.kernel.agent.executor import AgentExecutionResult

        def _fake(self, plugin, task, workspace, **kwargs):
            return AgentExecutionResult(
                agent_id=plugin.id, status="success",
                output="done after tool", elapsed_s=0.02,
                summary="done after tool",
                metrics={"iterations": 2, "tool_calls_made": 1,
                         "finished_reason": "stop"},
            )

        monkeypatch.setattr(executor_mod.AgentExecutor, "execute", _fake)
        tool = SubAgentTool()
        parent = build_default_registry()
        parent.register(NoopTool())
        out = tool.execute(
            task="multi-step", _parent_registry=parent,
            _subagent_count_ref=[0], emit_event=None,
        )
        parsed = json.loads(out)
        assert parsed["status"] == "ok"
        assert parsed["answer"] == "done after tool"
        assert parsed["iterations"] == 2
        assert parsed["tool_calls_made"] == 1


class TestDelegateIsWriteTool:
    def test_effects_declared_not_readonly(self):
        """delegate_to_agent declares EFFECT_FS and is not readonly, so the
        AgentLoop runs it serially (avoids count-ref races)."""
        tool = build_default_registry().get("task")
        assert tool is not None
        assert tool.effects
        assert tool.is_readonly is False


class TestBatchOfDelegates:
    @pytest.mark.asyncio
    async def test_two_delegates_in_one_response(self, workspace, patch_child_client):
        """Two delegate_to_agent calls in a single LLM response both run,
        spawning 2 subagents with distinct ids."""
        child_clients = patch_child_client([text_resp("child ok")])
        tc1 = ToolCall(id="b1", name="task", arguments={"task": "t1"})
        tc2 = ToolCall(id="b2", name="task", arguments={"task": "t2"})
        parent = AsyncMockLLM([
            LLMResponse(content="", tool_calls=[tc1, tc2], finish_reason="tool_calls"),
            text_resp("both done"),
        ])
        loop = AgentLoop(
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            stream_mode=False,
            max_iterations=5,
        )
        events = _collect_events(loop)
        loop.client.achat = parent.achat

        r = await loop.arun("一次委派两个")
        assert r.tool_calls_made == 2
        assert r.answer == "both done"
        assert len(child_clients) == 2
        started = [e for e in events if e[0] == "subagent_started"]
        assert len(started) == 2
        assert len({e[1]["agent_id"] for e in started}) == 2


# ── _forward_event unit tests ────────────────────────────────────────


class TestForwardEvent:
    def test_lifecycle_events_pass_through(self):
        from strategy_research.kernel.agent.builtin_tools.subagent_tool import _forward_event

        seen: list[tuple[str, dict]] = []
        _forward_event(lambda et, d: seen.append((et, d)), "sub-1", "msg-1", "subagent_started", {"a": 1})
        _forward_event(lambda et, d: seen.append((et, d)), "sub-1", "msg-1", "subagent_completed", {"b": 2})
        assert seen[0] == ("subagent_started", {"agent_id": "sub-1", "message_id": "msg-1", "a": 1})
        assert seen[1][0] == "subagent_completed"
        assert seen[1][1]["b"] == 2

    def test_child_events_namespaced(self):
        from strategy_research.kernel.agent.builtin_tools.subagent_tool import _forward_event

        seen: list[tuple[str, dict]] = []
        _forward_event(lambda et, d: seen.append((et, d)), "sub-1", "msg-1", "tool_call", {"name": "read"})
        _forward_event(lambda et, d: seen.append((et, d)), "sub-1", "msg-1", "tool_result", {"ok": True})
        _forward_event(lambda et, d: seen.append((et, d)), "sub-1", "msg-1", "text_delta", {"delta": "x"})
        assert [e[0] for e in seen] == [
            "subagent_tool_call", "subagent_tool_result", "subagent_text_delta",
        ]
        assert seen[0][1]["agent_id"] == "sub-1"
        assert seen[0][1]["message_id"] == "msg-1"
        assert seen[0][1]["name"] == "read"

    def test_none_callback_is_noop(self):
        from strategy_research.kernel.agent.builtin_tools.subagent_tool import _forward_event

        _forward_event(None, "sub-1", "msg-1", "tool_call", {"x": 1})  # must not raise

    def test_unknown_event_type_passes_through_unchanged(self):
        from strategy_research.kernel.agent.builtin_tools.subagent_tool import _forward_event

        seen: list[tuple[str, dict]] = []
        _forward_event(lambda et, d: seen.append((et, d)), "sub-1", "msg-1", "thinking_delta", {"t": "..."})
        assert seen[0][0] == "thinking_delta"
