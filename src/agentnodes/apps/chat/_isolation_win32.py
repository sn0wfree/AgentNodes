# -*- coding: utf-8 -*-
"""Windows 隔离执行 adapter（4c B2.2 · 从原型提炼，不接生产）。

Job Object 首选（整树终止无竞态）；宿主处于受限 Job 时 Assign 被拒 →
降级 ``taskkill /PID <pid> /T /F``（同样整树杀 + 逐 pid 确认）。
语义等价点：**终止 → 确认退出 → 才释放**（原型 E4/E5 已验证）。

本模块只在 Windows 平台被 build_worker_adapter() 延迟 import——POSIX
永不加载（顶层 import ctypes.WinDLL 在 POSIX 会失败，这是有意的隔离）。
"""
from __future__ import annotations

import ctypes
import subprocess
import time
from typing import Any

from agentnodes.apps.chat.isolation import WorkerAdapter

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_SYNCHRONIZE = 0x00100000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WAIT_OBJECT_0 = 0x00000000
JOB_OBJECT_EXTEND_LIMIT_INFO = 9
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _open_process(pid: int):
    return _k32.OpenProcess(
        PROCESS_SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid,
    )


def _job_create_with_kill_on_close():
    h = _k32.CreateJobObjectW(None, None)
    if not h:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not _k32.SetInformationJobObject(
        h, JOB_OBJECT_EXTEND_LIMIT_INFO, ctypes.byref(info), ctypes.sizeof(info),
    ):
        raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
    return h


class Win32JobAdapter(WorkerAdapter):
    """Windows 进程树隔离 adapter。

    handle = dict{proc, job|None, used_taskkill}; 3b IPC 层通过 handle["proc"]
    通信。本阶段只做 spawn / kill_and_confirm / close 三行为。
    """

    def supported(self) -> bool:
        return True

    def spawn(self, worker_script: str) -> dict[str, Any]:
        proc = subprocess.Popen(
            [__import__("sys").executable, worker_script, "--worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, encoding="utf-8",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        job = _job_create_with_kill_on_close()
        h = _open_process(proc.pid)
        assigned = bool(h) and bool(_k32.AssignProcessToJobObject(job, h))
        if h:
            _k32.CloseHandle(h)
        if not assigned:
            _k32.TerminateJobObject(job, 1)
            _k32.CloseHandle(job)
            proc.kill()
            proc.wait()
            # 降级：普通 worker + taskkill /T 树终止（语义见模块 docstring）
            proc = subprocess.Popen(
                [__import__("sys").executable, worker_script, "--worker"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                text=True, encoding="utf-8",
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            return {"proc": proc, "job": None, "used_taskkill": True}
        return {"proc": proc, "job": job, "used_taskkill": False}

    def kill_and_confirm(self, handle, pids, timeout_s):
        confirmed: dict[int, str] = {}
        job = handle["job"]
        if job is not None:
            _k32.TerminateJobObject(job, 1)
        else:
            subprocess.run(
                ["taskkill", "/PID", str(handle["proc"].pid), "/T", "/F"],
                capture_output=True, check=False,
            )
        for pid in pids:
            h = _open_process(pid)
            if not h:
                confirmed[pid] = "already-gone"
                continue
            code = _k32.WaitForSingleObject(h, int(timeout_s * 1000))
            confirmed[pid] = "exited" if code == WAIT_OBJECT_0 else f"still-alive(wait={code:#x})"
            _k32.CloseHandle(h)
        return confirmed

    def close(self, handle) -> None:
        job = handle.get("job")
        if job is not None:
            _k32.TerminateJobObject(job, 1)
            _k32.CloseHandle(job)
        proc = handle["proc"]
        if proc.poll() is None:
            proc.kill()
