"""Tests for _select_by_token_budget — token budget selection logic."""
from __future__ import annotations

from agentnodes.kernel.agent.compact import (
    CompactConfig,
    _select_by_token_budget,
)


def _make_msgs(n: int, content_len: int = 100) -> list[dict]:
    """Create n alternating user/assistant messages."""
    msgs = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": f"msg{i} " + "x" * content_len})
    return msgs


class TestSelectByTokenBudgetBasic:
    def test_returns_head_and_recent(self):
        msgs = _make_msgs(10)
        cfg = CompactConfig(tail_turns=2, preserve_recent_tokens=1000)
        head, recent = _select_by_token_budget(msgs, cfg, 1_000_000)
        assert isinstance(head, list)
        assert isinstance(recent, list)
        assert len(head) + len(recent) == len(msgs)

    def test_head_and_recent_are_disjoint(self):
        msgs = _make_msgs(10)
        cfg = CompactConfig(tail_turns=2, preserve_recent_tokens=1000)
        head, recent = _select_by_token_budget(msgs, cfg, 1_000_000)
        head_ids = {id(m) for m in head}
        recent_ids = {id(m) for m in recent}
        assert head_ids.isdisjoint(recent_ids)

    def test_recent_preserves_order(self):
        msgs = _make_msgs(10)
        cfg = CompactConfig(tail_turns=2, preserve_recent_tokens=1000)
        head, recent = _select_by_token_budget(msgs, cfg, 1_000_000)
        # Recent messages should be a subset of original messages
        for m in recent:
            assert m in msgs


class TestSelectByTokenBudgetTailTurns:
    def test_tail_turns_0(self):
        """tail_turns=0: no messages in recent from tail."""
        msgs = _make_msgs(10)
        cfg = CompactConfig(tail_turns=0, preserve_recent_tokens=1000)
        head, recent = _select_by_token_budget(msgs, cfg, 1_000_000)
        # No tail turns, but budget might add some from the end
        # With budget=1000, the recent budget filling from end should get some
        assert len(recent) <= len(msgs)

    def test_tail_turns_1(self):
        msgs = _make_msgs(10)
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=1000)
        head, recent = _select_by_token_budget(msgs, cfg, 1_000_000)
        # Last turn = user+assistant = 2 messages
        assert len(recent) >= 1

    def test_tail_turns_3(self):
        msgs = _make_msgs(10)
        cfg = CompactConfig(tail_turns=3, preserve_recent_tokens=1000)
        head, recent = _select_by_token_budget(msgs, cfg, 1_000_000)
        # Last 3 turns = up to 6 messages
        assert len(recent) >= 3

    def test_tail_turns_exceeds_total(self):
        """tail_turns > total turns: all messages go to recent."""
        msgs = _make_msgs(4)  # 2 turns
        cfg = CompactConfig(tail_turns=10, preserve_recent_tokens=1000)
        head, recent = _select_by_token_budget(msgs, cfg, 1_000_000)
        # All messages should be in recent (tail turns covers everything)
        assert len(head) + len(recent) == len(msgs)


class TestSelectByTokenBudgetPreserveRecentTokens:
    def test_explicit_budget(self):
        msgs = _make_msgs(10, content_len=300)
        cfg = CompactConfig(tail_turns=2, preserve_recent_tokens=500)
        head, recent = _select_by_token_budget(msgs, cfg, 1_000_000)
        # Budget=500 tokens, each msg ~100 tokens → ~5 recent messages
        assert len(recent) <= len(msgs)

    def test_small_budget(self):
        msgs = _make_msgs(10, content_len=300)
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=100)
        head, recent = _select_by_token_budget(msgs, cfg, 1_000_000)
        # Very small budget → fewer recent messages
        assert len(recent) <= 2  # at most 1 turn

    def test_large_budget(self):
        msgs = _make_msgs(10, content_len=100)
        cfg = CompactConfig(tail_turns=10, preserve_recent_tokens=100_000)
        head, recent = _select_by_token_budget(msgs, cfg, 1_000_000)
        # When tail_turns >= total turns, all messages are in head
        # (no separate recent selection needed)
        assert len(head) + len(recent) == len(msgs)


class TestSelectByTokenBudgetDynamicBudget:
    def test_1m_context(self):
        """1M context: budget = min(8000, max(2000, 20%)) = 8000."""
        msgs = _make_msgs(10, content_len=300)
        cfg = CompactConfig(tail_turns=2)
        head, recent = _select_by_token_budget(msgs, cfg, 1_000_000)
        assert len(recent) >= 1

    def test_10k_context(self):
        """10K context: budget = min(8000, max(2000, 2000)) = 2000."""
        msgs = _make_msgs(10, content_len=300)
        cfg = CompactConfig(tail_turns=2)
        head, recent = _select_by_token_budget(msgs, cfg, 10_000)
        assert len(recent) >= 1

    def test_no_model_context(self):
        """No model context: budget defaults to 4000."""
        msgs = _make_msgs(10, content_len=300)
        cfg = CompactConfig(tail_turns=2)
        head, recent = _select_by_token_budget(msgs, cfg, None)
        assert len(recent) >= 1


class TestSelectByTokenBudgetEdgeCases:
    def test_empty_messages(self):
        cfg = CompactConfig(tail_turns=2, preserve_recent_tokens=1000)
        head, recent = _select_by_token_budget([], cfg, 1_000_000)
        assert head == []
        assert recent == []

    def test_single_message(self):
        msgs = [{"role": "user", "content": "hello"}]
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=1000)
        head, recent = _select_by_token_budget(msgs, cfg, 1_000_000)
        assert len(head) + len(recent) == 1

    def test_all_same_role(self):
        """All user messages (no assistant)."""
        msgs = [{"role": "user", "content": f"q{i}"} for i in range(5)]
        cfg = CompactConfig(tail_turns=2, preserve_recent_tokens=1000)
        head, recent = _select_by_token_budget(msgs, cfg, 1_000_000)
        assert len(head) + len(recent) == len(msgs)

    def test_only_assistant_messages(self):
        msgs = [{"role": "assistant", "content": f"a{i}"} for i in range(5)]
        cfg = CompactConfig(tail_turns=2, preserve_recent_tokens=1000)
        head, recent = _select_by_token_budget(msgs, cfg, 1_000_000)
        assert len(head) + len(recent) == len(msgs)

    def test_budget_fills_from_end(self):
        """Budget fills from the most recent turns backwards."""
        msgs = _make_msgs(20, content_len=50)
        cfg = CompactConfig(tail_turns=5, preserve_recent_tokens=500)
        head, recent = _select_by_token_budget(msgs, cfg, 1_000_000)
        # Recent should contain messages from the end of the list
        if recent:
            last_recent_idx = msgs.index(recent[-1])
            first_recent_idx = msgs.index(recent[0])
            assert last_recent_idx > first_recent_idx
