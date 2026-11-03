"""P1-B: Request Envelope hash fields tests."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestTraceLlmRequestHashes:
    """Verify _trace_llm_request emits hash fields."""

    def _make_loop(self):
        """Create a minimal AgentLoop-like object for testing."""
        from strategy_research.kernel.agent.loop import AgentLoop
        from strategy_research.kernel.agent.tools import ToolRegistry

        loop = object.__new__(AgentLoop)
        loop.session_id = "test_session"
        loop._emit = MagicMock()
        loop._trace_writer = None
        return loop

    def test_hash_fields_present(self):
        """llm_request entry contains system_prompt_hash, tools_hash, history_hash."""
        from strategy_research.kernel.agent.loop import AgentLoop

        loop = self._make_loop()
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello"},
        ]
        tools = [{"type": "function", "function": {"name": "read"}}]

        loop._trace_llm_request(messages, iteration=1, tools=tools)

        loop._emit.assert_called_once()
        entry = loop._emit.call_args[0][1]
        assert "system_prompt_hash" in entry
        assert "tools_hash" in entry
        assert "history_hash" in entry
        assert "estimated_tokens" in entry

    def test_system_prompt_hash_correctness(self):
        loop = self._make_loop()
        system_prompt = "You are a helpful assistant."
        messages = [{"role": "system", "content": system_prompt}]
        tools = None

        loop._trace_llm_request(messages, iteration=1, tools=tools)

        entry = loop._emit.call_args[0][1]
        expected = hashlib.sha256(system_prompt.encode()).hexdigest()[:16]
        assert entry["system_prompt_hash"] == expected

    def test_tools_hash_correctness(self):
        loop = self._make_loop()
        messages = [{"role": "system", "content": ""}]
        tools = [{"type": "function", "function": {"name": "read"}}]

        loop._trace_llm_request(messages, iteration=1, tools=tools)

        entry = loop._emit.call_args[0][1]
        tools_json = json.dumps(tools, ensure_ascii=False)
        expected = hashlib.sha256(tools_json.encode()).hexdigest()[:16]
        assert entry["tools_hash"] == expected

    def test_history_hash_changes_with_content(self):
        """Different message content produces different history_hash."""
        loop1 = self._make_loop()
        loop2 = self._make_loop()

        msgs1 = [
            {"role": "system", "content": ""},
            {"role": "user", "content": "Hello"},
        ]
        msgs2 = [
            {"role": "system", "content": ""},
            {"role": "user", "content": "Goodbye"},
        ]

        loop1._trace_llm_request(msgs1, iteration=1)
        loop2._trace_llm_request(msgs2, iteration=1)

        h1 = loop1._emit.call_args[0][1]["history_hash"]
        h2 = loop2._emit.call_args[0][1]["history_hash"]
        assert h1 != h2

    def test_history_hash_stable_for_same_content(self):
        """Same content produces same history_hash."""
        msgs = [
            {"role": "system", "content": ""},
            {"role": "user", "content": "Hello"},
        ]

        results = []
        for _ in range(3):
            loop = self._make_loop()
            loop._trace_llm_request(msgs, iteration=1)
            results.append(loop._emit.call_args[0][1]["history_hash"])

        assert len(set(results)) == 1

    def test_estimated_tokens_is_positive(self):
        loop = self._make_loop()
        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "User message"},
        ]
        loop._trace_llm_request(messages, iteration=1)
        entry = loop._emit.call_args[0][1]
        assert entry["estimated_tokens"] > 0

    def test_no_system_prompt(self):
        """Works when first message is not system role."""
        loop = self._make_loop()
        messages = [{"role": "user", "content": "Hello"}]
        loop._trace_llm_request(messages, iteration=1)
        entry = loop._emit.call_args[0][1]
        assert entry["system_prompt_hash"] == hashlib.sha256(b"").hexdigest()[:16]

    def test_empty_tools(self):
        """Works when tools is None."""
        loop = self._make_loop()
        messages = [{"role": "system", "content": ""}]
        loop._trace_llm_request(messages, iteration=1, tools=None)
        entry = loop._emit.call_args[0][1]
        assert entry["tools_hash"] == hashlib.sha256(b"[]").hexdigest()[:16]
