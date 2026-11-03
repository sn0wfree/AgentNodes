"""Tests for tool message storage (方案B: role=tool messages).

Verifies:
- _convert_messages_to_history handles role=tool messages (new format)
- _convert_messages_to_history reconstructs tool_calls from parts (legacy format)
- Tool pairs are preserved correctly
- Empty content messages with tool_calls are kept
"""
from __future__ import annotations

from strategy_research.interfaces.api.session.models import Message
from strategy_research.interfaces.api.session.service import SessionService


def _make_msg(role, content, **kwargs):
    """Helper to build a Message with sensible defaults."""
    defaults = {"message_id": "m1", "session_id": "s1", "role": role, "content": content}
    defaults.update(kwargs)
    return Message(**defaults)


class TestConvertHistoryNewFormat:
    """Test history conversion with role=tool messages (new storage format)."""

    def test_user_assistant_only(self):
        messages = [
            _make_msg("user", "hello", message_id="u1"),
            _make_msg("assistant", "hi there", message_id="a1"),
            _make_msg("user", "current turn", message_id="u2"),
        ]
        history = SessionService._convert_messages_to_history(messages)
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "hello"
        assert history[1]["role"] == "assistant"
        assert history[1]["content"] == "hi there"

    def test_tool_message_preserved(self):
        messages = [
            _make_msg("user", "read file", message_id="u1"),
            _make_msg("assistant", "", message_id="a1",
                      metadata={"_parts": [{"type": "tool_call", "id": "call_1", "name": "read", "arguments": '{"path":"x"}'}]}),
            _make_msg("tool", "file content", message_id="t1", tool_call_id="call_1"),
            _make_msg("user", "current", message_id="u2"),
        ]
        history = SessionService._convert_messages_to_history(messages)
        assert len(history) == 3
        assert history[0]["role"] == "user"
        assert history[1]["role"] == "assistant"
        assert len(history[1]["tool_calls"]) == 1
        assert history[1]["tool_calls"][0]["id"] == "call_1"
        assert history[1]["tool_calls"][0]["function"]["name"] == "read"
        assert history[2]["role"] == "tool"
        assert history[2]["tool_call_id"] == "call_1"
        assert history[2]["content"] == "file content"

    def test_multiple_tool_calls(self):
        parts = [
            {"type": "tool_call", "id": "call_1", "name": "read", "arguments": '{"path":"a.py"}'},
            {"type": "tool_call", "id": "call_2", "name": "list", "arguments": '{"path":"."}'},
        ]
        messages = [
            _make_msg("user", "do stuff", message_id="u1"),
            _make_msg("assistant", "", message_id="a1", metadata={"_parts": parts}),
            _make_msg("tool", "content1", message_id="t1", tool_call_id="call_1"),
            _make_msg("tool", "content2", message_id="t2", tool_call_id="call_2"),
            _make_msg("user", "current", message_id="u2"),
        ]
        history = SessionService._convert_messages_to_history(messages)
        assert len(history) == 4
        assert len(history[1]["tool_calls"]) == 2
        assert history[1]["tool_calls"][0]["function"]["name"] == "read"
        assert history[1]["tool_calls"][1]["function"]["name"] == "list"
        assert history[2]["tool_call_id"] == "call_1"
        assert history[3]["tool_call_id"] == "call_2"

    def test_tool_without_id_skipped(self):
        messages = [
            _make_msg("user", "hi", message_id="u1"),
            _make_msg("tool", "orphan", message_id="t1", tool_call_id=None),
            _make_msg("user", "current", message_id="u2"),
        ]
        history = SessionService._convert_messages_to_history(messages)
        assert len(history) == 1
        assert history[0]["role"] == "user"

    def test_empty_assistant_with_tool_calls_kept(self):
        """Assistant messages with empty content but tool_calls must be kept."""
        messages = [
            _make_msg("user", "read", message_id="u1"),
            _make_msg("assistant", "", message_id="a1",
                      metadata={"_parts": [{"type": "tool_call", "id": "c1", "name": "r", "arguments": "{}"}]}),
            _make_msg("tool", "result", message_id="t1", tool_call_id="c1"),
            _make_msg("user", "current", message_id="u2"),
        ]
        history = SessionService._convert_messages_to_history(messages)
        assert len(history) == 3
        assert history[1]["role"] == "assistant"
        assert "tool_calls" in history[1]

    def test_tool_call_args_dict_normalized_to_string(self):
        """Arguments stored as dict (not string) should be JSON-serialized."""
        messages = [
            _make_msg("user", "hi", message_id="u1"),
            _make_msg("assistant", "", message_id="a1",
                      metadata={"_parts": [{"type": "tool_call", "id": "c1", "name": "r", "arguments": {"path": "x"}}]}),
            _make_msg("user", "current", message_id="u2"),
        ]
        history = SessionService._convert_messages_to_history(messages)
        args = history[1]["tool_calls"][0]["function"]["arguments"]
        assert isinstance(args, str)
        assert "path" in args


class TestConvertHistoryLegacyFormat:
    """Test history conversion with tool_calls in parts (legacy format, no role=tool rows)."""

    def test_legacy_tool_calls_reconstructed(self):
        """Legacy: tool_call info comes from parts inside assistant message.

        The assistant's tool_call parts are emitted as tool_calls, and
        the embedded result becomes a role=tool message immediately
        after (opencode-aligned assistant-tool grouping)."""
        parts = [
            {"type": "tool_call", "id": "call_1", "name": "read",
             "arguments": '{"path":"test.py"}', "result": "file content", "status": "done"},
        ]
        messages = [
            _make_msg("user", "read file", message_id="u1"),
            _make_msg("assistant", "I'll read the file", message_id="a1",
                      metadata={"_parts": parts}),
            _make_msg("user", "current", message_id="u2"),
        ]
        history = SessionService._convert_messages_to_history(messages)
        assert len(history) == 3
        assert history[1]["role"] == "assistant"
        assert len(history[1]["tool_calls"]) == 1
        assert history[1]["tool_calls"][0]["function"]["name"] == "read"
        assert history[2]["role"] == "tool"
        assert history[2]["tool_call_id"] == "call_1"
        assert history[2]["content"] == "file content"

    def test_legacy_mixed_text_and_tool_call_parts(self):
        parts = [
            {"type": "text", "text": "Let me check..."},
            {"type": "tool_call", "id": "call_1", "name": "search",
             "arguments": '{"query":"test"}', "result": "[]", "status": "done"},
        ]
        messages = [
            _make_msg("user", "search", message_id="u1"),
            _make_msg("assistant", "Let me check...", message_id="a1",
                      metadata={"_parts": parts}),
            _make_msg("user", "current", message_id="u2"),
        ]
        history = SessionService._convert_messages_to_history(messages)
        assert len(history) == 3
        assert history[1]["content"] == "Let me check..."
        assert len(history[1]["tool_calls"]) == 1
        assert history[2]["role"] == "tool"
        assert history[2]["content"] == "[]"

    def test_legacy_no_tool_parts(self):
        messages = [
            _make_msg("user", "hi", message_id="u1"),
            _make_msg("assistant", "hello", message_id="a1",
                      metadata={"_parts": [{"type": "text", "text": "hello"}]}),
            _make_msg("user", "current", message_id="u2"),
        ]
        history = SessionService._convert_messages_to_history(messages)
        assert len(history) == 2
        assert "tool_calls" not in history[1]


class TestConvertHistoryOrdering:
    """Test message ordering and exclusion of last message."""

    def test_last_message_excluded(self):
        messages = [
            _make_msg("user", "first", message_id="u1"),
            _make_msg("assistant", "reply", message_id="a1"),
            _make_msg("user", "last (current turn)", message_id="u2"),
        ]
        history = SessionService._convert_messages_to_history(messages)
        assert len(history) == 2
        assert history[-1]["content"] == "reply"

    def test_single_message_returns_empty(self):
        messages = [
            _make_msg("user", "only message", message_id="u1"),
        ]
        history = SessionService._convert_messages_to_history(messages)
        assert len(history) == 0

    def test_tool_message_order_preserved(self):
        parts = [
            {"type": "tool_call", "id": "c1", "name": "r", "arguments": "{}"},
            {"type": "tool_call", "id": "c2", "name": "w", "arguments": "{}"},
        ]
        messages = [
            _make_msg("user", "do it", message_id="u1"),
            _make_msg("assistant", "", message_id="a1", metadata={"_parts": parts}),
            _make_msg("tool", "r1", message_id="t1", tool_call_id="c1"),
            _make_msg("tool", "r2", message_id="t2", tool_call_id="c2"),
            _make_msg("user", "current", message_id="u2"),
        ]
        history = SessionService._convert_messages_to_history(messages)
        roles = [m["role"] for m in history]
        assert roles == ["user", "assistant", "tool", "tool"]
