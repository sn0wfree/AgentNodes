"""P2-B: Steering inbox tests."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.kernel.agent.loop import AgentLoop
from strategy_research.kernel.agent.tools import ToolRegistry


class _FakeConfig:
    compact_config = None


def _make_loop():
    """Create a minimal AgentLoop for inbox testing."""
    return AgentLoop(
        config=_FakeConfig(),
        registry=ToolRegistry(),
        max_iterations=5,
    )


class TestSteeringInbox:
    def test_inject_adds_to_inbox(self):
        loop = _make_loop()
        msg = {"role": "user", "content": "steer me"}
        loop.inject(msg)
        assert not loop._inbox.empty()
        assert loop._inbox.get_nowait() == msg

    def test_inject_is_non_blocking(self):
        loop = _make_loop()
        # Should not raise
        for i in range(100):
            loop.inject({"role": "user", "content": f"msg {i}"})
        assert loop._inbox.qsize() == 100

    def test_drain_inbox_moves_to_messages(self):
        loop = _make_loop()
        loop.inject({"role": "user", "content": "msg1"})
        loop.inject({"role": "user", "content": "msg2"})
        messages = [{"role": "system", "content": "init"}]
        loop._drain_inbox(messages)
        assert len(messages) == 3
        assert messages[1]["content"] == "msg1"
        assert messages[2]["content"] == "msg2"
        assert loop._inbox.empty()

    def test_drain_inbox_fifo_order(self):
        loop = _make_loop()
        for i in range(5):
            loop.inject({"role": "user", "content": f"msg{i}"})
        messages = []
        loop._drain_inbox(messages)
        contents = [m["content"] for m in messages]
        assert contents == ["msg0", "msg1", "msg2", "msg3", "msg4"]

    def test_drain_inbox_empty_noop(self):
        loop = _make_loop()
        messages = [{"role": "system", "content": "init"}]
        loop._drain_inbox(messages)
        assert len(messages) == 1

    @pytest.mark.asyncio
    async def test_ainject_async(self):
        loop = _make_loop()
        msg = {"role": "user", "content": "async steer"}
        await loop.ainject(msg)
        assert not loop._inbox.empty()
        assert loop._inbox.get_nowait() == msg

    def test_inbox_initialized(self):
        """AgentLoop creates _inbox on init."""
        loop = _make_loop()
        assert hasattr(loop, "_inbox")
        assert isinstance(loop._inbox, asyncio.Queue)
