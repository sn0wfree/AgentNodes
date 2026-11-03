"""Background process utilities — nohup-style long tasks with log-driven
liveness detection.

Any long-running program (backtest, factor computation, data prep, training,
...) can be backgrounded via ``run_bg`` + ``wait_bg``; liveness is judged by
the *log file* making progress, not by wall clock:

- log mtime keeps advancing  → the task is alive and healthy (no timeout —
  run as long as it needs)
- process alive but log stalled > ``stall_timeout`` → the task is stuck →
  ``killpg`` the whole process group
- process exited → return its full log output for parsing

See ``docs/study-long-task-background-plan.md`` §2 for the judgement model.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

DEFAULT_STALL_TIMEOUT = float(
    os.environ.get("SR_BACKTEST_STALL_TIMEOUT", "300")
)


# ── Task registry ───────────────────────────────────────────────────
# Process-wide registry for background tasks (subprocess or thread
# mode). Shared by backtest.run_strategy, the run_bg_command tool and
# RunBacktestTool(background=True); watchdogs and round-end harvesters
# sweep it via ``active_tasks`` / ``harvest_all``.


@dataclass
class BgTaskHandle:
    task_id: str
    log_path: Path
    command: str
    started_at: float
    proc: Optional[subprocess.Popen] = None   # subprocess mode
    thread: Optional[threading.Thread] = None  # thread mode
    result: Any = None                         # thread-mode completion result
    owner: Optional[str] = None                # e.g. study_id for round-end harvest

    def is_alive(self) -> bool:
        if self.proc is not None:
            return self.proc.poll() is None
        if self.thread is not None:
            return self.thread.is_alive()
        return False


_TASKS: dict[str, BgTaskHandle] = {}
_TASKS_LOCK = threading.Lock()


def register_task(
    proc: Any,
    log_path: Path | str,
    command: str,
    *,
    task_id: str = "",
    owner: Optional[str] = None,
) -> str:
    """Register a background *process*; returns the task_id."""
    tid = task_id or f"bg_{uuid.uuid4().hex[:8]}"
    with _TASKS_LOCK:
        _TASKS[tid] = BgTaskHandle(
            task_id=tid, proc=proc, thread=None,
            log_path=Path(log_path), command=command,
            started_at=time.time(), owner=owner,
        )
    return tid


def register_thread_task(
    thread: Any,
    log_path: Path | str,
    command: str,
    *,
    task_id: str = "",
    owner: Optional[str] = None,
) -> str:
    """Register a background *thread* task; returns the task_id."""
    tid = task_id or f"bg_{uuid.uuid4().hex[:8]}"
    with _TASKS_LOCK:
        _TASKS[tid] = BgTaskHandle(
            task_id=tid, proc=None, thread=thread,
            log_path=Path(log_path), command=command,
            started_at=time.time(), owner=owner,
        )
    return tid


def get_task(task_id: str) -> Optional[BgTaskHandle]:
    with _TASKS_LOCK:
        return _TASKS.get(task_id)


def unregister_task(task_id: str) -> None:
    with _TASKS_LOCK:
        _TASKS.pop(task_id, None)


def set_task_result(task_id: str, result: Any) -> None:
    with _TASKS_LOCK:
        handle = _TASKS.get(task_id)
        if handle is not None:
            handle.result = result


def active_tasks() -> list[BgTaskHandle]:
    """Snapshot of live tasks (watchdog / round-end harvest)."""
    with _TASKS_LOCK:
        return [h for h in _TASKS.values() if h.is_alive()]


def harvest_all_tasks() -> int:
    """Kill + deregister every live background task (round end / shutdown).

    Returns the number of tasks killed.
    """
    killed = 0
    with _TASKS_LOCK:
        for tid, h in list(_TASKS.items()):
            if h.is_alive():
                if h.proc is not None:
                    kill_bg(h.proc)
                killed += 1
            _TASKS.pop(tid, None)
    return killed


def harvest_by_owner(owner: str) -> int:
    """Kill + deregister all live tasks owned by ``owner`` (e.g. a study
    round end). Finished tasks are deregistered without killing.

    Returns the number of live tasks killed.
    """
    killed = 0
    with _TASKS_LOCK:
        for tid, h in list(_TASKS.items()):
            if h.owner != owner:
                continue
            if h.is_alive():
                if h.proc is not None:
                    kill_bg(h.proc)
                killed += 1
            _TASKS.pop(tid, None)
    return killed


def shell_command(command: str) -> list[str]:
    """Wrap a shell string as a process argv for the current platform.

    - POSIX: ``bash -lc command``
    - Windows: ``cmd /c command`` (bash is often unavailable; the
      previous hard-coded ``bash -lc`` raised FileNotFoundError).
    """
    import sys

    if sys.platform.startswith("win"):
        return ["cmd", "/c", command]
    return ["bash", "-lc", command]


def run_bg(
    command: Sequence[str],
    log_path: Path | str,
    *,
    env: Optional[dict[str, str]] = None,
    cwd: Path | str | None = None,
) -> subprocess.Popen:
    """Start ``command`` in the background, streaming stdout+stderr to
    ``log_path`` (nohup semantics).

    - ``start_new_session=True``: the child gets its own process group so
      ``killpg`` can reap grandchildren.
    - Returns immediately (non-blocking); call ``wait_bg`` to poll.
    """
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "a", encoding="utf-8")  # noqa: SIM115 — long-lived handle
    proc = subprocess.Popen(  # noqa: S603
        list(command),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        cwd=str(cwd) if cwd is not None else None,
        start_new_session=True,
    )
    return proc


def log_tail(log_path: Path | str, n: int = 20) -> str:
    """Return the last ``n`` lines of a log file (missing file → empty)."""
    path = Path(log_path)
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-n:])


def is_stalled(log_path: Path | str, stall_timeout: float = DEFAULT_STALL_TIMEOUT,
               *, since: Optional[float] = None) -> bool:
    """True when the log stopped advancing ``stall_timeout`` seconds ago.

    ``since`` defaults to the current time; a missing log file counts as
    stalled (callers should guard with a startup grace window).
    """
    path = Path(log_path)
    if not path.exists():
        return True
    mtime = path.stat().st_mtime
    ref = since if since is not None else time.time()
    return (ref - mtime) > stall_timeout


def kill_bg(proc: subprocess.Popen) -> None:
    """Kill the whole process group (children included)."""
    if proc.poll() is not None:
        return
    import sys

    if hasattr(os, "killpg"):
        try:
            os.killpg(proc.pid, signal.SIGKILL)  # noqa: S606
        except (ProcessLookupError, PermissionError, OSError):
            pass
    elif sys.platform.startswith("win"):
        # No process-group kill on Windows — take the tree down via taskkill.
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                check=False,
            )
        except Exception:  # noqa: BLE001
            pass
    try:
        proc.kill()
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.wait(timeout=5)
    except (subprocess.TimeoutExpired, Exception):  # noqa: BLE001
        pass


def wait_bg(
    proc: subprocess.Popen,
    log_path: Path | str,
    stall_timeout: float = DEFAULT_STALL_TIMEOUT,
    *,
    poll: float = 2.0,
) -> tuple[bool, str]:
    """Poll a background process until it exits or stalls.

    Returns ``(ok, payload)``:
    - ``(True, log_output)`` — process exited (any exit code); payload is
      the full log text.
    - ``(False, "stalled after Ns...")`` — process alive but the log
      stopped advancing; the process group has been killed.

    A startup grace window (``max(10s, 5×poll)``) allows the child to
    spawn and write its first log lines before staleness applies.
    """
    log_path = Path(log_path)
    grace_until = time.time() + max(10.0, poll * 5)
    while True:
        if proc.poll() is not None:
            break
        if time.time() > grace_until and is_stalled(log_path, stall_timeout):
            kill_bg(proc)
            return False, (
                f"stalled after {stall_timeout:.0f}s without log progress "
                f"(log: {log_path})"
            )
        time.sleep(poll)
    output = log_path.read_text(encoding="utf-8", errors="replace") \
        if log_path.exists() else ""
    return True, output


def log_progress(log_path: Path | str, line: str) -> None:
    """Append a progress line to a log (in-process/thread-mode writers)."""
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{line}\n")
