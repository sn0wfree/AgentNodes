"""Tests for _convert_messages_to_history assistant-tool ordering (Level 0).

opencode-aligned: in the LLM-bound history, every assistant(tool_calls)
must be followed immediately by its tool result messages, NOT in raw
created_at order. Violating this order triggers provider errors like
MiniMax 400 "chat content is empty" (2013).

This module covers:
- Basic reorder (assistant → its tools, before next user/assistant)
- Tool group integrity during trim (drop whole group, never split)
- Multiple assistants each with their own tool calls
- Orphan tool messages (no matching assistant) → drop
- Real session 700dc7f7 turn 3 fixture (the bug that triggered this fix)
"""

from __future__ import annotations

from agentnodes.interfaces.api.session.models import Message
from agentnodes.interfaces.api.session.service import SessionService

# ── Helpers ──────────────────────────────────────────────────────────


def _make_message(
    role: str,
    content: str,
    *,
    message_id: str | None = None,
    message_type: str | None = None,
    created_at: float = 0.0,
    parts: list[dict] | None = None,
    tool_call_id: str | None = None,
) -> Message:
    return Message(
        message_id=message_id or f"msg-{role}-{content[:8]}",
        session_id="sess-1",
        role=role,
        content=content,
        message_type=message_type or role,
        created_at=created_at,
        metadata={"_parts": parts or []},
        tool_call_id=tool_call_id,
    )


def _assistant_with_tools(
    message_id: str,
    content: str,
    created_at: float,
    tool_calls: list[dict],
) -> Message:
    """Assistant message with embedded tool_call parts."""
    return _make_message(
        "assistant",
        content,
        message_id=message_id,
        created_at=created_at,
        parts=tool_calls,
    )


def _tool_result(
    tool_call_id: str,
    content: str,
    created_at: float,
    message_id: str | None = None,
) -> Message:
    return _make_message(
        "tool",
        content,
        message_id=message_id or f"tool-{tool_call_id}",
        created_at=created_at,
        tool_call_id=tool_call_id,
    )


# ── Tests ────────────────────────────────────────────────────────────


class TestAssistantBeforeTools:
    """Core Level 0 invariant: assistant(tool_calls) must come before its tool results."""

    def test_assistant_comes_before_tools_when_tools_have_earlier_timestamp(self):
        """Reproduces the 700dc7f7 / 81102cc1 bug:

        Tool messages have EARLIER created_at than the assistant that
        generated them (because tools stream in before the assistant's
        final text is persisted). Raw order is wrong. Reorder logic
        must put assistant first.
        """
        messages = [
            _make_message("user", "构建一个轮动策略", created_at=100.0, message_id="u1"),
            # 5 tool results, all earlier than the assistant
            _tool_result("call_A", '{"path":"/a"}', 101.0, message_id="t1"),
            _tool_result("call_B", '{"path":"/b"}', 101.5, message_id="t2"),
            _tool_result("call_C", '{"content":"x"}', 102.0, message_id="t3"),
            _tool_result("call_D", '{"content":"y"}', 102.5, message_id="t4"),
            _tool_result("call_E", '{"goal_id":"g1"}', 103.0, message_id="t5"),
            # Assistant's final text comes later (L4 streaming)
            _assistant_with_tools(
                "a1",
                "研究目标已创建...",
                113.0,
                tool_calls=[
                    {"type": "tool_call", "id": "call_A", "name": "list", "arguments": "{}"},
                    {"type": "tool_call", "id": "call_B", "name": "list", "arguments": "{}"},
                    {"type": "tool_call", "id": "call_C", "name": "read", "arguments": "{}"},
                    {"type": "tool_call", "id": "call_D", "name": "read", "arguments": "{}"},
                    {"type": "tool_call", "id": "call_E", "name": "create_goal", "arguments": "{}"},
                ],
            ),
            # Add a follow-up exchange so the next user check has a target.
            # The current-turn user is the LAST one (always excluded).
            _make_message("user", "1A 2A 3B 4A 5A", created_at=120.0, message_id="u2"),
        ]
        history = SessionService._convert_messages_to_history(messages)
        # Last message (current turn) is excluded → 7 entries
        assert len(history) == 7

        # Find assistant and its tools
        asst_idx = next(i for i, h in enumerate(history) if h["role"] == "assistant")
        tool_indices = [i for i, h in enumerate(history) if h["role"] == "tool"]
        assert len(tool_indices) == 5

        # The assistant MUST come before all of its tools
        assert asst_idx < min(tool_indices), (
            f"assistant at {asst_idx} should come before all tools at {tool_indices}"
        )

        # All tools must appear AFTER the assistant. (No later user msg
        # because the next user is the current turn and was excluded.)
        for ti in tool_indices:
            assert asst_idx < ti, (
                f"tool at {ti} should be after assistant at {asst_idx}"
            )

        # The assistant's tc_ids match the tools in the same order they
        # were declared (the iteration over entry["tool_calls"] preserves
        # insertion order from parts).
        declared_tc_ids = [tc["id"] for tc in history[asst_idx]["tool_calls"]]
        actual_tc_ids = [history[ti]["tool_call_id"] for ti in tool_indices]
        assert declared_tc_ids == actual_tc_ids, (
            f"tool_call_id order mismatch: declared={declared_tc_ids} actual={actual_tc_ids}"
        )

    def test_assistant_tools_preserved_when_assistant_comes_first(self):
        """When assistant comes before tools by created_at, behavior is unchanged."""
        messages = [
            _make_message("user", "u1", created_at=1.0, message_id="u1"),
            _assistant_with_tools(
                "a1",
                "I will call tools",
                2.0,
                tool_calls=[
                    {"type": "tool_call", "id": "call_X", "name": "foo", "arguments": "{}"},
                ],
            ),
            _tool_result("call_X", "result", 3.0),
            _make_message("user", "u2", created_at=4.0, message_id="u2"),
        ]
        history = SessionService._convert_messages_to_history(messages)
        # Excluded: u2 (current turn) → 3 entries
        assert len(history) == 3
        assert [h["role"] for h in history] == ["user", "assistant", "tool"]


class TestMultipleAssistantGroups:
    """Multiple assistant-tool groups must each be kept intact."""

    def test_two_assistants_with_separate_tools(self):
        messages = [
            _make_message("user", "u1", created_at=1.0, message_id="u1"),
            # Turn 2: assistant_a with 2 tools
            _tool_result("ta1", "ta1 result", 2.0),
            _tool_result("ta2", "ta2 result", 2.5),
            _assistant_with_tools(
                "a_a",
                "Turn 2 reply",
                3.0,
                tool_calls=[
                    {"type": "tool_call", "id": "ta1", "name": "n1", "arguments": "{}"},
                    {"type": "tool_call", "id": "ta2", "name": "n2", "arguments": "{}"},
                ],
            ),
            # Turn 3: assistant_b with 3 tools
            _tool_result("tb1", "tb1 result", 4.0),
            _tool_result("tb2", "tb2 result", 4.2),
            _tool_result("tb3", "tb3 result", 4.5),
            _assistant_with_tools(
                "a_b",
                "Turn 3 reply",
                5.0,
                tool_calls=[
                    {"type": "tool_call", "id": "tb1", "name": "m1", "arguments": "{}"},
                    {"type": "tool_call", "id": "tb2", "name": "m2", "arguments": "{}"},
                    {"type": "tool_call", "id": "tb3", "name": "m3", "arguments": "{}"},
                ],
            ),
            _make_message("user", "u2", created_at=6.0, message_id="u2"),
        ]
        history = SessionService._convert_messages_to_history(messages)
        # Excluded: u2 → 7 entries: u1, a_a, ta1, ta2, a_b, tb1, tb2, tb3
        assert len(history) == 8

        # Group 1: a_a followed by its 2 tools
        a_a_idx = next(i for i, h in enumerate(history) if h.get("role") == "assistant" and h.get("content") == "Turn 2 reply")
        ta1_idx = next(i for i, h in enumerate(history) if h.get("tool_call_id") == "ta1")
        ta2_idx = next(i for i, h in enumerate(history) if h.get("tool_call_id") == "ta2")
        assert a_a_idx < ta1_idx < ta2_idx

        # Group 2: a_b followed by its 3 tools
        a_b_idx = next(i for i, h in enumerate(history) if h.get("role") == "assistant" and h.get("content") == "Turn 3 reply")
        tb1_idx = next(i for i, h in enumerate(history) if h.get("tool_call_id") == "tb1")
        tb2_idx = next(i for i, h in enumerate(history) if h.get("tool_call_id") == "tb2")
        tb3_idx = next(i for i, h in enumerate(history) if h.get("tool_call_id") == "tb3")
        assert a_b_idx < tb1_idx < tb2_idx < tb3_idx

        # Group 1 must come entirely before group 2
        assert ta2_idx < a_b_idx


class TestOrphanTools:
    """Tools without a matching assistant are dropped (not emitted)."""

    def test_orphan_tool_dropped(self):
        """Tool message whose tc_id has no matching assistant → drop."""
        messages = [
            _make_message("user", "u1", created_at=1.0, message_id="u1"),
            _make_message("assistant", "no tools", created_at=2.0, message_id="a1"),
            _tool_result("orphan_tc", "this tool has no assistant", created_at=3.0),
            _make_message("user", "u2", created_at=4.0, message_id="u2"),
        ]
        history = SessionService._convert_messages_to_history(messages)
        # Excluded: u2 → 2 entries: u1, a1 (orphan dropped)
        assert len(history) == 2
        assert [h["role"] for h in history] == ["user", "assistant"]
        assert not any(h["role"] == "tool" for h in history)

    def test_assistant_with_no_tools_unchanged(self):
        """Assistant without tool_calls → emitted normally, no group follow."""
        messages = [
            _make_message("user", "u1", created_at=1.0, message_id="u1"),
            _make_message("assistant", "just a reply, no tools", created_at=2.0, message_id="a1"),
            _make_message("user", "u2", created_at=3.0, message_id="u2"),
        ]
        history = SessionService._convert_messages_to_history(messages)
        assert [h["role"] for h in history] == ["user", "assistant"]

    def test_assistant_with_tool_call_but_no_tool_message(self):
        """Assistant has tool_calls but tool result message is missing.
        Emits the assistant anyway plus an empty tool result, keeping
        the OpenAI tool protocol pairing intact.
        """
        messages = [
            _make_message("user", "u1", created_at=1.0, message_id="u1"),
            _assistant_with_tools(
                "a1",
                "Called a tool but result was lost",
                2.0,
                tool_calls=[
                    {"type": "tool_call", "id": "lost_tc", "name": "x", "arguments": "{}"},
                ],
            ),
            _make_message("user", "u2", created_at=3.0, message_id="u2"),
        ]
        history = SessionService._convert_messages_to_history(messages)
        # Assistant emitted with empty tool result immediately after
        assert len(history) == 3
        assert history[1]["role"] == "assistant"
        assert history[1].get("tool_calls")
        assert history[2]["role"] == "tool"
        assert history[2]["tool_call_id"] == "lost_tc"


class TestTrimGroupIntegrity:
    """Trim by character budget must keep assistant-tool groups intact."""

    def test_group_dropped_atomically_when_too_large(self, monkeypatch):
        """If a group is too large, drop the whole group, never split."""
        # Pad content so the group exceeds budget
        long_content = "X" * 20000
        messages = [
            _make_message("user", "u1", created_at=1.0, message_id="u1"),
            _assistant_with_tools(
                "a1",
                long_content,
                2.0,
                tool_calls=[
                    {"type": "tool_call", "id": "tc_big", "name": "x", "arguments": "{}"},
                ],
            ),
            _tool_result("tc_big", long_content, 3.0),
            _make_message("user", "u2", created_at=4.0, message_id="u2"),
        ]
        history = SessionService._convert_messages_to_history(messages)
        # Group is too large → dropped entirely. u1 (oldest) is dropped first.
        # The assistant+tool group is also dropped because it doesn't fit.
        # Only u2 (current turn) is excluded by design.
        assert not any(h["role"] == "assistant" for h in history)
        assert not any(h["role"] == "tool" for h in history)

    def test_group_kept_when_fits(self):
        """Normal case: small group fits and is kept intact."""
        messages = [
            _make_message("user", "u1", created_at=1.0, message_id="u1"),
            _assistant_with_tools(
                "a1",
                "ok",
                2.0,
                tool_calls=[
                    {"type": "tool_call", "id": "tc1", "name": "x", "arguments": "{}"},
                ],
            ),
            _tool_result("tc1", "result", 3.0),
            _make_message("user", "u2", created_at=4.0, message_id="u2"),
        ]
        history = SessionService._convert_messages_to_history(messages)
        # u1, a1, tc1 (u2 excluded)
        assert [h["role"] for h in history] == ["user", "assistant", "tool"]


class TestRealSessionFixture:
    """The exact pattern from session 81102cc1 turn 2 → turn 3 trigger."""

    def test_81102cc1_turn_2_pattern(self):
        """Reproduces the conversation that triggered MiniMax 2013.

        Session 81102cc1 turn 2:
        - User: "构建一个轮动策略"
        - 5 tool results (created_at 1785478819-1785478833)
        - Assistant: long response with 5 tool_calls (created_at 1785478862)

        Then turn 3 user message would trigger the LLM call. With
        Level 0 reorder, the history for turn 3's LLM call has
        assistant BEFORE its tools.
        """
        T0 = 1785478816.305  # user
        T_TOOLS = [1785478819.281, 1785478819.307, 1785478824.645, 1785478824.680, 1785478833.011]
        T_ASST = 1785478862.404  # assistant
        T_NEXT_USER = 1785478871.643

        tc_ids = ["call_A", "call_B", "call_C", "call_D", "call_E"]
        tool_results = [
            '{"status":"ok","path":"/a","entries":[...]}'[:200],
            '{"status":"ok","path":"/b","entries":[...]}'[:200],
            '{"content":"template..."}'[:200],
            '{"content":"config..."}'[:200],
            '{"status":"ok","goal_id":"g1"}'[:200],
        ]

        messages = [
            _make_message("user", "构建一个轮动策略", created_at=T0, message_id="u_build"),
        ]
        for i, (tc_id, t, content) in enumerate(zip(tc_ids, T_TOOLS, tool_results)):
            messages.append(_tool_result(tc_id, content, t, message_id=f"tool_{i}"))
        messages.append(
            _assistant_with_tools(
                "asst_build",
                "研究目标已创建...",
                T_ASST,
                tool_calls=[
                    {"type": "tool_call", "id": tc_ids[0], "name": "list", "arguments": "{}"},
                    {"type": "tool_call", "id": tc_ids[1], "name": "list", "arguments": "{}"},
                    {"type": "tool_call", "id": tc_ids[2], "name": "read", "arguments": "{}"},
                    {"type": "tool_call", "id": tc_ids[3], "name": "read", "arguments": "{}"},
                    {"type": "tool_call", "id": tc_ids[4], "name": "create_goal", "arguments": "{}"},
                ],
            )
        )
        # Current turn (will be excluded)
        messages.append(_make_message("user", "1A 2A 3B 4A 5A", created_at=T_NEXT_USER, message_id="u_3"))

        history = SessionService._convert_messages_to_history(messages)
        # Excluded: u_3 (current turn) → 7 entries
        assert len(history) == 7

        # CRITICAL: assistant index < all tool indices
        asst_idx = next(i for i, h in enumerate(history) if h["role"] == "assistant")
        tool_indices = [i for i, h in enumerate(history) if h["role"] == "tool"]
        assert asst_idx < min(tool_indices), (
            f"FAIL: assistant at {asst_idx}, tools at {tool_indices}. "
            f"Order would cause MiniMax 2013."
        )

        # All 5 tools preserved (no orphans)
        assert len(tool_indices) == 5
        assert sorted(h["tool_call_id"] for h in history if h["role"] == "tool") == sorted(tc_ids)
