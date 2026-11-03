"""Tests for run_bg_command tool — single-entry background task management."""

from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.kernel.agent.builtin_tools.bg_tools import (
    RunBgCommandTool,
    active_tasks,
    unregister_task,
)
from strategy_research.core.agent.tools import ToolContext


def _ctx(workspace: Path) -> ToolContext:
    return ToolContext(workspace=str(workspace), session_id="t")


def _invoke(ctx, **kwargs) -> dict:
    tool = RunBgCommandTool()
    raw = tool.execute(ctx, **kwargs)
    return json.loads(raw)


class TestRunBgCommandTool(unittest.TestCase):

    def setUp(self) -> None:
        self.tmpdir = TemporaryDirectory()
        self.ws = Path(self.tmpdir.name)
        self.ctx = _ctx(self.ws)

    def tearDown(self) -> None:
        # clean any lingering tasks
        for h in active_tasks():
            if h.proc is not None:
                import subprocess
                subprocess.Popen.kill(h.proc)  # noqa: E1101
            unregister_task(h.task_id)
        self.tmpdir.cleanup()

    def test_start_returns_running_with_task_id(self) -> None:
        out = _invoke(self.ctx, action="start", command="sleep 30")
        self.assertEqual(out["status"], "running")
        self.assertTrue(out["task_id"].startswith("bg_"))
        log = Path(out["log"])
        self.assertTrue(log.parent.exists())

    def test_status_running(self) -> None:
        out = _invoke(self.ctx, action="start", command="sleep 30")
        tid = out["task_id"]
        st = _invoke(self.ctx, action="status", task_id=tid)
        self.assertEqual(st["state"], "running")

    def test_wait_then_done(self) -> None:
        out = _invoke(self.ctx, action="start", command="echo hi; sleep 0.2")
        tid = out["task_id"]
        time.sleep(0.5)
        st = _invoke(self.ctx, action="wait", task_id=tid, seconds=1)
        self.assertEqual(st["state"], "done")
        self.assertEqual(st["exit_code"], 0)

    def test_log_action_returns_tail(self) -> None:
        out = _invoke(
            self.ctx, action="start",
            command="for i in $(seq 1 10); do echo line$i; done",
        )
        tid = out["task_id"]
        time.sleep(0.5)
        st = _invoke(self.ctx, action="status", task_id=tid)
        self.assertEqual(st["state"], "done")
        log = _invoke(self.ctx, action="log", task_id=tid, n_lines=3)
        self.assertIn("line10", log["log"])

    def test_kill_action(self) -> None:
        out = _invoke(self.ctx, action="start", command="sleep 60")
        tid = out["task_id"]
        st = _invoke(self.ctx, action="wait", task_id=tid, seconds=1)
        self.assertEqual(st["state"], "running")
        k = _invoke(self.ctx, action="kill", task_id=tid)
        self.assertTrue(k["killed"])
        # task deregistered
        st2 = _invoke(self.ctx, action="status", task_id=tid)
        self.assertEqual(st2["status"], "error")

    def test_unknown_action(self) -> None:
        out = _invoke(self.ctx, action="bogus")
        self.assertEqual(out["status"], "error")
        self.assertIn("unknown action", out["error"])

    def test_unknown_task_id(self) -> None:
        out = _invoke(self.ctx, action="status", task_id="bg_nope")
        self.assertEqual(out["status"], "error")

    def test_blocked_command_rejected(self) -> None:
        out = _invoke(self.ctx, action="start", command="mkfs /dev/sda1")
        self.assertEqual(out["status"], "error")
        self.assertIn("blocked", out["error"])

    def test_stalled_state_detected(self) -> None:
        # Writes one line then sleeps forever → stalled after stall window.
        out = _invoke(
            self.ctx, action="start",
            command="echo start; sleep 600",
        )
        tid = out["task_id"]
        log = Path(out["log"])
        time.sleep(0.3)  # let the child write its first line
        # age the log beyond the stall window
        old = time.time() - 3600
        import os
        os.utime(log, (old, old))
        st = _invoke(self.ctx, action="status", task_id=tid)
        self.assertEqual(st["state"], "stalled")
        self.assertIn("start", st.get("tail", ""))


if __name__ == "__main__":
    unittest.main()
