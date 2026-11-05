"""Tests for _fix_tool_pairs — orphaned tool_call/tool_result repair."""
from __future__ import annotations

from agentnodes.kernel.agent.compact import _fix_tool_pairs


class TestFixToolPairsNoOrphans:
    def test_empty_messages(self):
        assert _fix_tool_pairs([]) == []

    def test_no_tool_calls(self):
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        result = _fix_tool_pairs(msgs)
        assert len(result) == 2

    def test_matched_pairs(self):
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read", "arguments": ""}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "result"},
        ]
        result = _fix_tool_pairs(msgs)
        assert len(result) == 2

    def test_multiple_matched_pairs(self):
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "read", "arguments": ""}},
                {"id": "c2", "type": "function", "function": {"name": "write", "arguments": ""}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "r1"},
            {"role": "tool", "tool_call_id": "c2", "content": "r2"},
        ]
        result = _fix_tool_pairs(msgs)
        assert len(result) == 3


class TestFixToolPairsOrphanResults:
    def test_remove_single_orphan_result(self):
        msgs = [
            {"role": "tool", "tool_call_id": "c_missing", "content": "orphan"},
            {"role": "user", "content": "hello"},
        ]
        result = _fix_tool_pairs(msgs)
        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_remove_multiple_orphan_results(self):
        msgs = [
            {"role": "tool", "tool_call_id": "c1", "content": "orphan1"},
            {"role": "tool", "tool_call_id": "c2", "content": "orphan2"},
            {"role": "user", "content": "hello"},
        ]
        result = _fix_tool_pairs(msgs)
        assert len(result) == 1

    def test_keep_matched_result_remove_orphan(self):
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read", "arguments": ""}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "matched"},
            {"role": "tool", "tool_call_id": "c_missing", "content": "orphan"},
        ]
        result = _fix_tool_pairs(msgs)
        assert len(result) == 2
        assert result[0]["role"] == "assistant"
        assert result[1]["content"] == "matched"


class TestFixToolPairsOrphanCalls:
    def test_remove_orphan_call(self):
        """Assistant has tool_call with id=c1, but no tool result for c1.
        Orphan calls are kept (not removed) when assistant has content."""
        msgs = [
            {"role": "assistant", "content": "text", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "read", "arguments": ""}},
            ]},
            {"role": "user", "content": "hello"},
        ]
        result = _fix_tool_pairs(msgs)
        assert len(result) == 2
        # Message kept because it has content (orphan calls not stripped from tool_calls)
        assert result[0]["content"] == "text"

    def test_remove_orphan_call_no_content(self):
        """Orphan call AND no text content → message removed entirely."""
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "read", "arguments": ""}},
            ]},
            {"role": "user", "content": "hello"},
        ]
        result = _fix_tool_pairs(msgs)
        # Assistant message with only orphan call and no content → removed
        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_remove_one_orphan_keep_matched(self):
        """Two tool_calls: one matched, one orphaned."""
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "read", "arguments": ""}},
                {"id": "c2", "type": "function", "function": {"name": "write", "arguments": ""}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "matched"},
        ]
        result = _fix_tool_pairs(msgs)
        assert len(result) == 2
        # Only c1's tool_call remains
        assert len(result[0]["tool_calls"]) == 1
        assert result[0]["tool_calls"][0]["id"] == "c1"


class TestFixToolPairsMixed:
    def test_orphan_result_and_orphan_call(self):
        """Both orphaned result and orphaned call exist.
        Orphan results are removed, orphan calls kept when there's content."""
        msgs = [
            {"role": "assistant", "content": "text", "tool_calls": [
                {"id": "c_missing", "type": "function", "function": {"name": "read", "arguments": ""}},
            ]},
            {"role": "tool", "tool_call_id": "c_other_missing", "content": "orphan result"},
            {"role": "user", "content": "hello"},
        ]
        result = _fix_tool_pairs(msgs)
        # Orphan result removed, message kept because it has content
        assert len(result) == 2
        assert result[0]["content"] == "text"
        assert result[1]["role"] == "user"

    def test_preserves_order(self):
        msgs = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "tool", "tool_call_id": "c_missing", "content": "orphan"},
            {"role": "user", "content": "q2"},
        ]
        result = _fix_tool_pairs(msgs)
        assert len(result) == 3
        assert result[0]["content"] == "q1"
        assert result[1]["content"] == "a1"
        assert result[2]["content"] == "q2"


class TestFixToolPairsEdgeCases:
    def test_tool_call_without_id(self):
        """tool_call without id field — should not crash."""
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"type": "function", "function": {"name": "read", "arguments": ""}},
            ]},
            {"role": "user", "content": "hello"},
        ]
        result = _fix_tool_pairs(msgs)
        assert len(result) == 2

    def test_tool_result_without_tool_call_id(self):
        """tool result without tool_call_id — not tracked as orphan."""
        msgs = [
            {"role": "tool", "content": "no id"},
            {"role": "user", "content": "hello"},
        ]
        result = _fix_tool_pairs(msgs)
        assert len(result) == 2  # both kept

    def test_empty_tool_calls_list(self):
        msgs = [
            {"role": "assistant", "content": "text", "tool_calls": []},
            {"role": "user", "content": "hello"},
        ]
        result = _fix_tool_pairs(msgs)
        assert len(result) == 2

    def test_non_dict_tool_call(self):
        """tool_calls entry is not a dict — should not crash."""
        msgs = [
            {"role": "assistant", "content": "text", "tool_calls": ["invalid"]},
            {"role": "user", "content": "hello"},
        ]
        result = _fix_tool_pairs(msgs)
        assert len(result) == 2

    def test_large_tool_calls_batch(self):
        """Many tool_calls, some matched, some orphaned.
        Orphan calls are stripped; matched calls kept."""
        tool_calls = [{"id": f"c{i}", "type": "function", "function": {"name": f"fn{i}", "arguments": ""}} for i in range(20)]
        tool_results = [{"role": "tool", "tool_call_id": f"c{i}", "content": f"r{i}"} for i in range(10)]
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": tool_calls},
            *tool_results,
        ]
        result = _fix_tool_pairs(msgs)
        # assistant_call_ids = {c0..c19}, result_ids = {c0..c9}
        # orphan_calls = {c10..c19} → cleaned = [c0..c9] (10 matched)
        # cleaned is non-empty → msg kept with 10 matched tool_calls
        assert len(result) == 11  # assistant (10 matched calls) + 10 tool results
        assistant_msg = result[0]
        assert len(assistant_msg["tool_calls"]) == 10
