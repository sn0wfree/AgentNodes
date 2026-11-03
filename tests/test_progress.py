"""Tests for progress.py — HeartbeatTimer (structured-progress half was
removed in P1.5-c (B3))."""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from strategy_research.kernel.agent.progress import (
    HeartbeatTimer,
)


class TestHeartbeatTimer(unittest.TestCase):

    def test_basic_heartbeat(self) -> None:
        received = []
        with HeartbeatTimer(tool_name="test", interval=0.1, emit=lambda d: received.append(d)):
            time.sleep(1.0)
        self.assertGreater(len(received), 0)
        self.assertIn("tool", received[0])
        self.assertEqual(received[0]["tool"], "test")
        self.assertIn("elapsed_s", received[0])

    def test_interval_clamped_to_0_5(self) -> None:
        received = []
        with HeartbeatTimer(tool_name="t", interval=0.01, emit=lambda d: received.append(d)):
            time.sleep(1.0)
        self.assertGreater(len(received), 0)

    def test_emitter_exception_does_not_crash_thread(self) -> None:
        with HeartbeatTimer(tool_name="t", interval=0.1, emit=lambda d: (_ for _ in ()).throw(RuntimeError("fail"))):
            time.sleep(1.0)


if __name__ == "__main__":
    unittest.main()
