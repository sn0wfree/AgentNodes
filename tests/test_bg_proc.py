"""Tests for core/utils/bg_proc — background processes with log-driven
liveness detection (independent of backtest/study)."""

from __future__ import annotations

import subprocess
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.foundation.utils import bg_proc


def _py(cmd: str) -> list[str]:
    return [sys.executable, "-c", cmd]


class TestRunBg(unittest.TestCase):

    def setUp(self) -> None:
        self.tmpdir = TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.log = self.root / "run.log"

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_run_bg_returns_immediately(self) -> None:
        proc = bg_proc.run_bg(
            _py("import time; time.sleep(5)"), self.log,
        )
        try:
            # Non-blocking: the Popen came back while the child still runs.
            self.assertIsNone(proc.poll())
        finally:
            bg_proc.kill_bg(proc)

    def test_log_streams_stdout(self) -> None:
        proc = bg_proc.run_bg(
            _py("print('hello'); print('world')"), self.log,
        )
        ok, output = bg_proc.wait_bg(proc, self.log, stall_timeout=5, poll=0.1)
        self.assertTrue(ok)
        self.assertIn("hello", output)
        self.assertIn("world", output)

    def test_log_streams_stderr(self) -> None:
        proc = bg_proc.run_bg(
            _py("import sys; print('boom', file=sys.stderr)"), self.log,
        )
        ok, output = bg_proc.wait_bg(proc, self.log, stall_timeout=5, poll=0.1)
        self.assertTrue(ok)
        self.assertIn("boom", output)

    def test_exit_code_recorded_on_process(self) -> None:
        proc = bg_proc.run_bg(_py("print('x')"), self.log)
        bg_proc.wait_bg(proc, self.log, stall_timeout=5, poll=0.1)
        self.assertEqual(proc.returncode, 0)


class TestWaitBgStallDetection(unittest.TestCase):
    """Log stagnation → kill, even though the process is alive."""

    def setUp(self) -> None:
        self.tmpdir = TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.log = self.root / "run.log"

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_stalled_process_is_killed(self) -> None:
        # Writes a couple of log lines, then sleeps forever (alive but
        # silent → stalled). wait_bg must kill it and report not-ok.
        proc = bg_proc.run_bg(
            _py(
                "print('start'); import time; time.sleep(600)"
            ),
            self.log,
        )
        ok, msg = bg_proc.wait_bg(
            proc, self.log, stall_timeout=1, poll=0.2,
        )
        self.assertFalse(ok)
        self.assertIn("stalled", msg)
        # The process group must be dead.
        self.assertIsNotNone(proc.poll())

    def test_log_progress_defers_stall_indefinitely(self) -> None:
        # A writer that keeps appending past the stall timeout must not
        # be killed — log progress = alive, no wall-clock ceiling.
        script = (
            "import time\n"
            "for i in range(30):\n"
            "    print(f'step {i}', flush=True)\n"
            "    time.sleep(0.2)\n"
        )
        proc = bg_proc.run_bg(_py(script), self.log)
        ok, output = bg_proc.wait_bg(
            proc, self.log, stall_timeout=0.6, poll=0.1,
        )
        # 30 steps × 0.2s = 6s of total run, well past the 0.6s stall
        # window — but the log keeps advancing so it must complete.
        self.assertTrue(ok)
        self.assertIn("step 29", output)

    def test_startup_grace_allows_first_lines(self) -> None:
        # The child takes a moment before writing its first line; the
        # grace window must absorb it (no immediate stall).
        script = (
            "import time; time.sleep(1)\n"
            "print('late start', flush=True)\n"
        )
        proc = bg_proc.run_bg(_py(script), self.log)
        ok, output = bg_proc.wait_bg(proc, self.log, stall_timeout=1, poll=0.2)
        self.assertTrue(ok)
        self.assertIn("late start", output)


class TestHelpers(unittest.TestCase):

    def setUp(self) -> None:
        self.tmpdir = TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.log = self.root / "run.log"

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_log_tail(self) -> None:
        self.log.write_text("\n".join(f"line{i}" for i in range(10)), encoding="utf-8")
        tail = bg_proc.log_tail(self.log, n=3)
        self.assertEqual(tail.splitlines(), ["line7", "line8", "line9"])

    def test_log_tail_missing_file(self) -> None:
        self.assertEqual(bg_proc.log_tail(self.root / "nope.log"), "")

    def test_is_stalled_fresh_log(self) -> None:
        self.log.write_text("x", encoding="utf-8")
        self.assertFalse(bg_proc.is_stalled(self.log, stall_timeout=60))

    def test_is_stalled_old_log(self) -> None:
        self.log.write_text("x", encoding="utf-8")
        old = time.time() - 3600
        import os
        os.utime(self.log, (old, old))
        self.assertTrue(bg_proc.is_stalled(self.log, stall_timeout=60))

    def test_is_stalled_missing_log(self) -> None:
        self.assertTrue(bg_proc.is_stalled(self.root / "nope.log", stall_timeout=60))

    def test_log_progress_appends(self) -> None:
        bg_proc.log_progress(self.log, "step 1")
        bg_proc.log_progress(self.log, "step 2")
        content = self.log.read_text(encoding="utf-8")
        self.assertIn("step 1", content)
        self.assertIn("step 2", content)


class TestTaskRegistry(unittest.TestCase):
    """Registry: owner tagging, round-end harvest, done-task cleanup."""

    def setUp(self) -> None:
        self.tmpdir = TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.log = self.root / "run.log"

    def tearDown(self) -> None:
        bg_proc.harvest_all_tasks()
        self.tmpdir.cleanup()

    def test_register_and_harvest_by_owner(self) -> None:
        proc_a = bg_proc.run_bg(_py("import time; time.sleep(60)"), self.log)
        proc_b = bg_proc.run_bg(_py("import time; time.sleep(60)"), self.log)
        tid_a = bg_proc.register_task(proc_a, self.log, "cmd A", owner="study_1")
        tid_b = bg_proc.register_task(proc_b, self.log, "cmd B", owner="study_2")
        self.assertEqual(len(bg_proc.active_tasks()), 2)

        killed = bg_proc.harvest_by_owner("study_1")
        self.assertEqual(killed, 1)
        self.assertIsNone(bg_proc.get_task(tid_a))
        # other owner untouched
        self.assertIsNotNone(bg_proc.get_task(tid_b))
        bg_proc.harvest_by_owner("study_2")

    def test_harvest_drops_finished_without_killing(self) -> None:
        proc = bg_proc.run_bg(_py("print('quick')"), self.log)
        tid = bg_proc.register_task(proc, self.log, "quick", owner="study_x")
        ok, _ = bg_proc.wait_bg(proc, self.log, stall_timeout=5, poll=0.1)
        self.assertTrue(ok)
        self.assertEqual(bg_proc.harvest_by_owner("study_x"), 0)  # already done
        self.assertIsNone(bg_proc.get_task(tid))


if __name__ == "__main__":
    unittest.main()
