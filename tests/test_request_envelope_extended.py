"""P1-B extended: Request Envelope trace_writer + emit failure tests."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestTraceLlmRequestEdgeCases:
    def _make_loop(self):
        from agentnodes.kernel.agent.loop import AgentLoop

        loop = object.__new__(AgentLoop)
        loop.session_id = "test_session"
        loop._emit = MagicMock()
        loop._trace_writer = None
        return loop

    def test_emit_failure_is_swallowed(self):
        """_emit raising exception should not crash _trace_llm_request."""
        loop = self._make_loop()
        loop._emit.side_effect = RuntimeError("emit failed")
        messages = [{"role": "system", "content": "test"}]
        # Should not raise
        loop._trace_llm_request(messages, iteration=1)

    def test_trace_writer_sidecar_offload(self):
        """When trace_writer is set, large fields are offloaded."""
        loop = self._make_loop()
        trace_writer = MagicMock()
        loop._trace_writer = trace_writer
        messages = [
            {"role": "system", "content": "x" * 20000},
            {"role": "user", "content": "hello"},
        ]
        loop._trace_llm_request(messages, iteration=1)
        # trace_writer.write_text_entry should be called for system_prompt
        assert trace_writer.write_text_entry.call_count >= 1

    def test_empty_messages(self):
        """Works with empty messages list."""
        loop = self._make_loop()
        loop._trace_llm_request([], iteration=1)
        entry = loop._emit.call_args[0][1]
        assert entry["history_count"] == 0
        assert entry["system_prompt_hash"] == hashlib.sha256(b"").hexdigest()[:16]

    def test_large_history_meta(self):
        """history_meta contains all non-system messages."""
        loop = self._make_loop()
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
        ]
        loop._trace_llm_request(messages, iteration=1)
        entry = loop._emit.call_args[0][1]
        assert len(entry["history_meta"]) == 3  # excludes system
        assert entry["history_meta"][0]["role"] == "user"
        assert entry["history_meta"][1]["role"] == "assistant"

    def test_tool_calls_in_history_meta(self):
        """history_meta tracks has_tool_calls correctly."""
        loop = self._make_loop()
        messages = [
            {"role": "system", "content": ""},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "t1"}]},
            {"role": "tool", "content": "result"},
        ]
        loop._trace_llm_request(messages, iteration=1)
        entry = loop._emit.call_args[0][1]
        assert entry["history_meta"][0]["has_tool_calls"] is True
        assert entry["history_meta"][1]["has_tool_calls"] is False

    def test_tools_count(self):
        """tools_count reflects number of tools."""
        loop = self._make_loop()
        messages = [{"role": "system", "content": ""}]
        tools = [{"type": "function", "function": {"name": "a"}} for _ in range(5)]
        loop._trace_llm_request(messages, iteration=1, tools=tools)
        entry = loop._emit.call_args[0][1]
        assert entry["tools_count"] == 5

    def test_iteration_number(self):
        """Iteration is correctly recorded."""
        loop = self._make_loop()
        messages = [{"role": "system", "content": ""}]
        loop._trace_llm_request(messages, iteration=42)
        entry = loop._emit.call_args[0][1]
        assert entry["iteration"] == 42

    def test_session_id_recorded(self):
        """session_id is included in the entry."""
        loop = self._make_loop()
        loop.session_id = "my_session"
        messages = [{"role": "system", "content": ""}]
        loop._trace_llm_request(messages, iteration=1)
        entry = loop._emit.call_args[0][1]
        assert entry["session_id"] == "my_session"

    def test_no_session_id_empty_string(self):
        """Missing session_id becomes empty string."""
        loop = self._make_loop()
        loop.session_id = None
        messages = [{"role": "system", "content": ""}]
        loop._trace_llm_request(messages, iteration=1)
        entry = loop._emit.call_args[0][1]
        assert entry["session_id"] == ""
