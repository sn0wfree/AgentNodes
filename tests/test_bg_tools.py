"""Tests for run_bg_command tool — single-entry background task management."""

from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentnodes.kernel.agent.builtin_tools.bg_tools import (
    RunBgCommandTool,
    active_tasks,
    unregister_task,
)
from agentnodes.kernel.agent.tools import ToolContext


def _ctx(workspace: Path) -> ToolContext:
    return ToolContext(workspace=str(workspace), session_id="t")


def _invoke(ctx, **kwargs) -> dict:
    tool = RunBgCommandTool()
    raw = tool.execute(ctx, **kwargs)
    return json.loads(raw)


class TestRunBgCommandTool(unittest.TestCase):

    # ── 跨平台命令构造器 ──────────────────────────────────────────
    #
    # 2026-11-05：原测试用 `sleep` / `echo` / `for i in $(seq ...)`，这些是
    # Unix shell 内建，Windows 上不可执行（后台工具起的是真实子进程）。
    # 统一改为 `sys.executable -c ...`，语义等价且跨平台。

    @staticmethod
    def _python(code: str) -> str:
        """构造跨平台 shell 命令（写临时脚本再执行）。

        ``bg_tools`` 用 ``shell_command()`` 包装——POSIX ``bash -lc``、
        Windows ``cmd /c``。含引号/括号的一行 ``python -c`` 在 cmd 下会被
        截断（实测 SyntaxError: unterminated string literal），因此改为
        「写 .py 文件 + 执行」，引号问题彻底消失。
        """
        import sys
        import tempfile
        from pathlib import Path

        fd, path = tempfile.mkstemp(suffix=".py", text=True)
        with open(fd, "w", encoding="utf-8") as fh:
            fh.write(code)
        return f'"{sys.executable}" "{Path(path).as_posix()}"'

    @classmethod
    def _sleep(cls, seconds: float) -> str:
        return cls._python(f"import time\ntime.sleep({seconds})\n")

    @classmethod
    def _print(cls, text: str) -> str:
        return cls._python(f"print({text!r})\n")

    @classmethod
    def _print_lines(cls, n: int) -> str:
        return cls._python(
            "for i in range(1, %d + 1):\n    print('line%%d' %% i)\n" % n
        )

    def setUp(self) -> None:
        self.tmpdir = TemporaryDirectory()
        self.ws = Path(self.tmpdir.name)
        self.ctx = _ctx(self.ws)

    def tearDown(self) -> None:
        # clean any lingering tasks
        import subprocess

        for h in active_tasks():
            if h.proc is not None:
                subprocess.Popen.kill(h.proc)  # noqa: E1101
                try:
                    h.proc.wait(timeout=5)
                except Exception:  # noqa: BLE001
                    pass
            unregister_task(h.task_id)
        # Windows: 子进程被杀后日志句柄可能还没释放，rmtree 会撞
        # WinError 32。重试几次；仍失败则忽略（临时目录会被系统清理）。
        for _ in range(5):
            try:
                self.tmpdir.cleanup()
                return
            except (PermissionError, OSError):
                time.sleep(0.2)
        self.tmpdir.cleanup  # noqa: B018 —— 放弃清理，避免 teardown 报错

    def test_start_returns_running_with_task_id(self) -> None:
        out = _invoke(self.ctx, action="start", command=self._sleep(30))
        self.assertEqual(out["status"], "running")
        self.assertTrue(out["task_id"].startswith("bg_"))
        log = Path(out["log"])
        self.assertTrue(log.parent.exists())

    def test_status_running(self) -> None:
        out = _invoke(self.ctx, action="start", command=self._sleep(30))
        tid = out["task_id"]
        st = _invoke(self.ctx, action="status", task_id=tid)
        self.assertEqual(st["state"], "running")

    def test_wait_then_done(self) -> None:
        out = _invoke(self.ctx, action="start", command=self._print("hi"))
        tid = out["task_id"]
        time.sleep(0.5)
        st = _invoke(self.ctx, action="wait", task_id=tid, seconds=1)
        self.assertEqual(st["state"], "done")
        self.assertEqual(st["exit_code"], 0)

    def test_log_action_returns_tail(self) -> None:
        out = _invoke(
            self.ctx, action="start",
            command=self._print_lines(10),
        )
        tid = out["task_id"]
        time.sleep(0.5)
        st = _invoke(self.ctx, action="status", task_id=tid)
        self.assertEqual(st["state"], "done")
        log = _invoke(self.ctx, action="log", task_id=tid, n_lines=3)
        self.assertIn("line10", log["log"])

    def test_kill_action(self) -> None:
        out = _invoke(self.ctx, action="start", command=self._sleep(60))
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
            command=self._python("import time;print('start',flush=True);time.sleep(600)"),
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
