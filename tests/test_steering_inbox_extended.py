"""P2-B extended: Steering inbox integration tests."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentnodes.kernel.agent.loop import AgentLoop
from agentnodes.kernel.agent.tools import ToolRegistry


class _FakeConfig:
    compact_config = None


def _make_loop():
    return AgentLoop(
        config=_FakeConfig(),
        registry=ToolRegistry(),
        max_iterations=5,
    )


class TestSteeringInboxExtended:
    def test_inject_multiple_messages(self):
        """Multiple inject calls queue all messages."""
        loop = _make_loop()
        for i in range(10):
            loop.inject({"role": "user", "content": f"msg{i}"})
        assert loop._inbox.qsize() == 10

    def test_drain_preserves_existing_messages(self):
        """Drain appends to existing messages, doesn't replace."""
        loop = _make_loop()
        loop.inject({"role": "user", "content": "injected"})
        messages = [{"role": "system", "content": "init"}, {"role": "user", "content": "orig"}]
        loop._drain_inbox(messages)
        assert len(messages) == 3
        assert messages[0]["content"] == "init"
        assert messages[1]["content"] == "orig"
        assert messages[2]["content"] == "injected"

    def test_drain_injects_in_fifo_order(self):
        """Messages appear in FIFO order after drain."""
        loop = _make_loop()
        for i in range(5):
            loop.inject({"role": "user", "content": f"step{i}"})
        messages = []
        loop._drain_inbox(messages)
        assert [m["content"] for m in messages] == [
            "step0", "step1", "step2", "step3", "step4"
        ]

    def test_drain_empty_inbox_noop(self):
        """Draining empty inbox doesn't modify messages."""
        loop = _make_loop()
        messages = [{"role": "system", "content": "keep"}]
        loop._drain_inbox(messages)
        assert len(messages) == 1

    def test_multiple_drains(self):
        """Multiple drains work independently."""
        loop = _make_loop()
        loop.inject({"role": "user", "content": "a"})
        messages1 = []
        loop._drain_inbox(messages1)
        assert len(messages1) == 1
        # Second drain on empty inbox
        messages2 = []
        loop._drain_inbox(messages2)
        assert len(messages2) == 0

    @pytest.mark.asyncio
    async def test_ainject_multiple(self):
        """async inject queues multiple messages."""
        loop = _make_loop()
        for i in range(5):
            await loop.ainject({"role": "user", "content": f"async{i}"})
        assert loop._inbox.qsize() == 5

    def test_inject_with_complex_payload(self):
        """Inject supports complex nested payloads."""
        loop = _make_loop()
        msg = {
            "role": "user",
            "content": "steer",
            "metadata": {"source": "external", "priority": "high"},
        }
        loop.inject(msg)
        retrieved = loop._inbox.get_nowait()
        assert retrieved["metadata"]["source"] == "external"

    def test_inbox_thread_safe(self):
        """inject() is thread-safe (uses put_nowait)."""
        loop = _make_loop()
        import threading

        def _inject_many():
            for i in range(100):
                loop.inject({"role": "user", "content": f"t{i}"})

        threads = [threading.Thread(target=_inject_many) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert loop._inbox.qsize() == 400
