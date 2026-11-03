"""Tests for TodoWriteTool — opencode-style todo/task tracking.

Covers:
  * Full-snapshot replacement + todo_updated SSE event
  * Status validation (pending/in_progress/completed)
  * Missing/invalid params errors
  * Empty list clears the session todos
  * Per-session isolation in TodoStore
  * <current-todos> snapshot injection into the AgentLoop messages
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from strategy_research.core.agent.builtin_tools.todo_tools import (
    TodoStore,
    TodoWriteTool,
    _format_todos_snapshot,
    _normalize_todos,
)
from strategy_research.core.agent.loop import AgentLoop
from strategy_research.core.agent.tools import ToolContext
from strategy_research.foundation.llm import LLMConfig, LLMResponse, ToolCall


def todo_resp(todos: list[dict], call_id: str = "todo-1") -> LLMResponse:
    tc = ToolCall(id=call_id, name="todowrite", arguments={"todos": todos})
    return LLMResponse(content="", tool_calls=[tc], finish_reason="tool_calls")


def text_resp(content: str) -> LLMResponse:
    return LLMResponse(content=content, tool_calls=[], finish_reason="stop")


class MockLLM:
    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.calls: list[list[dict]] = []

    async def achat(self, messages, **kwargs):
        self.calls.append(list(messages))
        if not self.responses:
            raise RuntimeError("MockLLM exhausted")
        return self.responses.pop(0)


@pytest.fixture(autouse=True)
def _clean_todos():
    TodoStore.reset_all()
    yield
    TodoStore.reset_all()


@pytest.fixture
def ctx():
    return ToolContext(workspace=Path("/tmp/ws"), session_id="s-1")


# ── TodoWriteTool unit ────────────────────────────────────────────────


class TestTodoWriteTool:
    def test_replaces_list_and_emits_snapshot(self, ctx):
        tool = TodoWriteTool()
        events: list[tuple[str, dict]] = []
        todos = [
            {"id": "t1", "content": "加载数据", "status": "in_progress"},
            {"id": "t2", "content": "计算因子", "status": "pending"},
        ]
        out = json.loads(tool.execute(ctx, session_id="s-1",
                                      emit_event=lambda et, d: events.append((et, d)),
                                      todos=todos))
        assert out["status"] == "ok"
        assert out["count"] == 2
        assert TodoStore.get("s-1") == todos
        assert events == [("todo_updated", {"todos": todos})]

    def test_missing_todos_param_errors(self, ctx):
        tool = TodoWriteTool()
        out = json.loads(tool.execute(ctx, session_id="s-1"))
        assert out["status"] == "error"
        assert "todos" in out["error"]

    def test_invalid_status_rejected(self, ctx):
        tool = TodoWriteTool()
        out = json.loads(tool.execute(ctx, session_id="s-1", todos=[
            {"id": "t1", "content": "x", "status": "done"},
        ]))
        assert out["status"] == "error"
        assert "'done'" in out["error"]
        assert TodoStore.get("s-1") == []

    def test_missing_content_rejected(self, ctx):
        tool = TodoWriteTool()
        out = json.loads(tool.execute(ctx, session_id="s-1", todos=[
            {"id": "t1", "status": "pending"},
        ]))
        assert out["status"] == "error"
        assert "content" in out["error"]

    def test_empty_list_clears(self, ctx):
        tool = TodoWriteTool()
        TodoStore.set("s-1", [{"id": "t1", "content": "x", "status": "pending"}])
        out = json.loads(tool.execute(ctx, session_id="s-1", todos=[]))
        assert out["status"] == "ok"
        assert out["count"] == 0
        assert TodoStore.get("s-1") == []

    def test_no_emit_event_is_safe(self, ctx):
        tool = TodoWriteTool()
        out = json.loads(tool.execute(ctx, session_id="s-1", todos=[
            {"id": "t1", "content": "x", "status": "pending"},
        ]))
        assert out["status"] == "ok"


class TestNormalizeTodos:
    def test_valid_input_passes(self):
        todos, err = _normalize_todos([
            {"id": "a", "content": "1", "status": "pending"},
            {"id": "b", "content": "2", "status": "completed"},
        ])
        assert err == ""
        assert len(todos) == 2

    def test_not_a_list(self):
        todos, err = _normalize_todos({"id": "a"})
        assert todos is None
        assert "list" in err

    def test_invalid_item_shape(self):
        todos, err = _normalize_todos(["string-item"])
        assert todos is None
        assert "object" in err


# ── TodoStore ─────────────────────────────────────────────────────────


class TestTodoStore:
    def test_per_session_isolation(self):
        TodoStore.set("s-1", [{"id": "a", "content": "1", "status": "pending"}])
        TodoStore.set("s-2", [{"id": "b", "content": "2", "status": "completed"}])
        assert TodoStore.get("s-1") != TodoStore.get("s-2")

    def test_get_returns_copy(self):
        TodoStore.set("s-1", [{"id": "a", "content": "1", "status": "pending"}])
        copy = TodoStore.get("s-1")
        copy.append({"id": "b", "content": "2", "status": "pending"})
        assert len(TodoStore.get("s-1")) == 1

    def test_format_snapshot(self):
        block = _format_todos_snapshot([
            {"id": "t1", "content": "加载数据", "status": "in_progress"},
        ])
        assert "<current-todos>" in block
        assert "[in_progress] 加载数据" in block


# ── AgentLoop integration ─────────────────────────────────────────────


class TestTodoLoopIntegration:
    async def test_todos_injected_into_following_iterations(self):
        from strategy_research.core.agent.builtin_tools import build_default_registry

        events: list[tuple[str, dict]] = []
        mock = MockLLM([
            todo_resp([
                {"id": "t1", "content": "加载数据", "status": "in_progress"},
                {"id": "t2", "content": "计算因子", "status": "pending"},
            ]),
            text_resp("完成"),
        ])
        loop = AgentLoop(
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            session_id="s-1",
            stream_mode=False,
            max_iterations=5,
        )
        loop.client = mock  # type: ignore[assignment]
        loop._emit = lambda et, d: events.append((et, d))  # type: ignore[method-assign]

        result = await loop.arun("多步任务")
        assert result.answer == "完成"
        # Second iteration should carry the <current-todos> snapshot
        second_call = mock.calls[1]
        injected = [m for m in second_call if m.get("role") == "system"
                    and "<current-todos>" in (m.get("content") or "")]
        assert len(injected) == 1
        assert "[in_progress] 加载数据" in injected[0]["content"]

    async def test_todo_updated_event_emitted_through_loop(self):
        from strategy_research.core.agent.builtin_tools import build_default_registry

        events: list[tuple[str, dict]] = []
        mock = MockLLM([
            todo_resp([{"id": "t1", "content": "任务", "status": "pending"}]),
            text_resp("ok"),
        ])
        loop = AgentLoop(
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            session_id="s-2",
            stream_mode=False,
            max_iterations=5,
        )
        loop.client = mock  # type: ignore[assignment]
        loop._emit = lambda et, d: events.append((et, d))  # type: ignore[method-assign]

        await loop.arun("任务")
        todo_events = [d for et, d in events if et == "todo_updated"]
        assert len(todo_events) == 1
        assert todo_events[0]["todos"][0]["id"] == "t1"
        assert TodoStore.get("s-2") == [{"id": "t1", "content": "任务", "status": "pending"}]
