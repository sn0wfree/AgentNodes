"""P0 fixes regression tests (2026-08-28 audit wave 1).

Covers three P0 bugs found in the study-subsystem audit:

1. loop.py no-progress approval gate: ``_check_no_progress`` return
   value was discarded — an *approved* continuation still exited the
   loop. Now True (reject/timeout-reject) exits, False (approved)
   falls through to the next iteration.
2. loop.py ``_run_coro_in_sync``: coroutine exceptions were swallowed
   and surfaced as ``KeyError: 'value'``. Now the original exception
   is re-raised.
3. openai_client ``_raise_for_status``: call sites passed
   ``config.provider`` (a str) instead of the adapter, causing
   AttributeError that masked the real HTTP error. The function now
   resolves str adapters itself, and the call sites pass the adapter.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import httpx
import pytest

from agentnodes.kernel.agent.builtin_tools import build_default_registry
from agentnodes.kernel.agent.loop import AgentLoop, _run_coro_in_sync
from agentnodes.foundation.llm import LLMConfig, LLMResponse, ToolCall
from agentnodes.foundation.llm.errors import LLMAuthError, LLMRateLimitError
from agentnodes.foundation.llm.openai_client import _raise_for_status
from agentnodes.foundation.llm.provider import get_provider

# ── Helpers (mirror test_agent_loop.py) ──────────────────────────────


class MockLLM:
    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)

    def chat(self, messages, **kwargs):
        if not self.responses:
            raise RuntimeError("MockLLM exhausted")
        return self.responses.pop(0)


def tool_resp(tool_calls: list[ToolCall]) -> LLMResponse:
    return LLMResponse(content="", tool_calls=tool_calls, finish_reason="tool_calls")


def text_resp(content: str) -> LLMResponse:
    return LLMResponse(content=content, tool_calls=[], finish_reason="stop")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "strategies" / "foo").mkdir(parents=True)
    return tmp_path


# ── 1. No-progress approval gate: approved continuation ─────────────


class TestNoProgressApprovedContinuation:
    def test_approved_no_progress_keeps_looping(self, workspace):
        """The gate's docstring promises: approved → keep looping.
        Regression for the discarded return value."""
        mock = MockLLM([
            tool_resp([ToolCall(id="c1", name="list_history", arguments={})]),
            tool_resp([ToolCall(id="c2", name="list_history", arguments={})]),
            tool_resp([ToolCall(id="c3", name="list_history", arguments={})]),
            text_resp("finally done"),
        ])
        loop = AgentLoop(
            stream_mode=False,
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            max_iterations=10,
            no_progress_window=3,
            approval_timeout=5.0,
            approval_on_timeout="reject",
        )
        loop.client.chat = mock.chat

        def _approve_later():
            time.sleep(0.2)
            loop.approve_loop("approved")

        t = threading.Thread(target=_approve_later, daemon=True)
        t.start()

        r = loop.run("loop then finish")
        t.join(timeout=2)

        assert r.finished_reason == "stop"
        assert r.answer == "finally done"
        assert r.iterations == 4

    def test_rejected_no_progress_exits(self, workspace):
        """Control: reject decision must still exit with user_rejected."""
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
            approval_timeout=5.0,
            approval_on_timeout="reject",
        )
        loop.client.chat = mock.chat

        threading.Thread(
            target=lambda: (time.sleep(0.2), loop.approve_loop("reject")),
            daemon=True,
        ).start()

        r = loop.run("loop")
        assert r.finished_reason == "user_rejected"


# ── 2. _run_coro_in_sync exception propagation ───────────────────────


class TestRunCoroInSync:
    def test_exception_propagates_through_thread_path(self):
        """When called inside a running loop, the coroutine runs on a
        background thread. The original exception must surface (not
        KeyError: 'value')."""

        async def _boom():
            raise ValueError("original error")

        async def _outer():
            return _run_coro_in_sync(_boom())

        with pytest.raises(ValueError, match="original error"):
            asyncio.run(_outer())

    def test_exception_propagates_no_loop_path(self):
        """Direct (no running loop) path — regression guard."""

        async def _boom():
            raise RuntimeError("plain path error")

        with pytest.raises(RuntimeError, match="plain path error"):
            _run_coro_in_sync(_boom())

    def test_value_returned_through_thread_path(self):
        async def _ok():
            return 42

        async def _outer():
            return _run_coro_in_sync(_ok())

        assert asyncio.run(_outer()) == 42


# ── 3. _raise_for_status adapter resolution ──────────────────────────


class TestRaiseForStatus:
    @staticmethod
    def _resp(status: int) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "boom"}})

    def test_str_adapter_resolved_not_attributeerror(self):
        """Regression: passing a provider *string* must resolve to the
        adapter, not crash with AttributeError."""
        # LLMAuthError proves provider-specific mapping ran (or at
        # least default mapping — the point is no AttributeError).
        with pytest.raises(LLMAuthError):
            _raise_for_status(self._resp(401), "openai")

    def test_none_adapter_falls_back(self):
        with pytest.raises(LLMAuthError):
            _raise_for_status(self._resp(401), None)

    def test_adapter_object_handle_error_respected(self):
        adapter = get_provider("minimax")
        # Whatever MiniMax maps 403 to, it must not be AttributeError.
        with pytest.raises((LLMAuthError, LLMRateLimitError, Exception)) as ei:
            _raise_for_status(self._resp(403), adapter)
        assert not isinstance(ei.value, AttributeError)
