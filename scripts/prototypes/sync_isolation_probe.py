# -*- coding: utf-8 -*-
"""同步工具进程隔离原型（路线步骤 3 · 不接生产）。

目的：验证 §9.3 候选隔离方案在 Windows 下的可行性边界——
真实工具上下文的跨界子集、权限审批 IPC 往返、子进程树终止、
确认退出后才释放 lease。**不修改任何生产代码路径**；结论回填
gateway-timeout-and-strict-history-design-20261118.md §9.7。

用法（本机 Windows）::

    python scripts/prototypes/sync_isolation_probe.py

实验矩阵：
    E1  worker 启动/握手/干净关停 + 启动延迟
    E2  ToolContext 可序列化子集跨界（数据字段回传核对）
    E3  权限 ASK 跨进程往返（ALLOW / DENY 两路）
    E4  子进程树终止（worker → child → grandchild，Job Object 全覆盖）
    E5  截止时间语义：终止 → 确认退出 → 丢弃迟到结果 → 才释放 lease
        （真实 SessionExecutionLease 验证序列）

IPC：stdin/stdout JSON Lines（每行 flush）。
平台：Windows 专用（ctypes kernel32 Job Object）；POSIX 设计见文档，
本主机未验证。

上下文跨界清单（由 ToolContext 字段静态定性，E2 抽样验证数据字段）：
    可序列化直传  workspace / session_id / strategy_dir / results_tsv /
                  write_roots / read_roots / message_id / tool_call_id
    回调转 IPC    emit_progress / emit_event / before_execute
    进程内对象    permission_evaluator+gateway（IPC 往返，E3 验证）、
                  data_store（worker 自持连接，未在本原型验证）、
                  sandbox（配置子集可传，对象本体不可）
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
HERE = Path(__file__).resolve().parent

# ── Windows Job Object（ctypes kernel32）────────────────────────────

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_SYNCHRONIZE = 0x00100000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
JOB_OBJECT_EXTEND_LIMIT_INFO = 9
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
STILL_ACTIVE = 259


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
        ("Affinity", ctypes.c_size_t),  # ULONG_PTR
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


def _job_create_with_kill_on_close():
    h = _k32.CreateJobObjectW(None, None)
    if not h:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not _k32.SetInformationJobObject(
        h, JOB_OBJECT_EXTEND_LIMIT_INFO,
        ctypes.byref(info), ctypes.sizeof(info),
    ):
        raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
    return h


def _open_process(pid: int):
    return _k32.OpenProcess(
        PROCESS_SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid,
    )


def _wait_handle(handle, timeout_ms: int) -> int:
    return _k32.WaitForSingleObject(handle, timeout_ms)


# ── worker 模式（隔离进程内执行sync 工具替身）───────────────────────

def _worker_loop() -> int:
    """读任务行 → 执行演示工具 → 事件/结果行回传。stdin EOF = 关停。"""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        task = json.loads(line)
        tool = task["tool"]
        args = task.get("args", {})
        ctx = task.get("ctx", {})
        if tool == "read_only":
            _emit({"ev": "progress", "note": "read_only running",
                   "ctx_workspace": ctx.get("workspace"), "ctx_session": ctx.get("session_id")})
            _emit({"result": {"status": "ok", "echo": args.get("payload")}})
        elif tool == "write_file":
            target = Path(args["path"])
            target.write_text(args["content"], encoding="utf-8")
            _emit({"result": {"status": "ok", "written": str(target)}})
        elif tool == "spawn_tree":
            # 两层后代：child(grandchild)。全部长睡，专供 Job 终止实验。
            child = subprocess.Popen(
                [sys.executable, "-c",
                 "import subprocess,sys,time;"
                 "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)']);"
                 "print(p.pid,flush=True);time.sleep(120)"],
                stdout=subprocess.PIPE, text=True, encoding="utf-8",
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            grand_pid = child.stdout.readline().strip()
            _emit({"ev": "tree", "child_pid": child.pid, "grand_pid": int(grand_pid)})
            time.sleep(120)  # 等 Job 终止；正常路径不会走到这
        elif tool == "ask_permission":
            _emit({"ev": "permission_request", "id": args["id"],
                   "tool": args["tool"]})
            resp = json.loads(sys.stdin.readline())
            if resp.get("action") == "allow":
                _emit({"result": {"status": "ok", "approved": True}})
            else:
                _emit({"result": {"status": "denied", "approved": False}})
        elif tool == "slow":
            time.sleep(args.get("seconds", 10))
            _emit({"result": {"status": "ok"}})  # 截止实验中不应到达
        else:
            _emit({"result": {"status": "error", "error": f"unknown tool {tool}"}})


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


# ── 父进程侧实验 ────────────────────────────────────────────────────

class Worker:
    """隔离 worker：任务行进 / 事件行出。

    隔离原语两级降级：
    1. 首选 Job Object（spawn 后立即 Assign；子进程自动继承 job →
       TerminateJobObject 整树终止，无树遍历竞态。生产实现应再加
       CREATE_SUSPENDED+ResumeThread 消除 Assign 前的启动竞态——
       subprocess 不暴露线程句柄，本探针接受该小窗口）；
    2. 宿主自身处于受限 Job（如 agent 沙箱）时 Assign 被拒
       （ERROR_ACCESS_DENIED）→ 降级 ``taskkill /PID <pid> /T /F``
       （Windows 树终止），逐 pid 等待确认。两者语义等价点：
       **整树杀 + 确认先于释放**。本探针两种都验证并记录所用路径。
    """

    def __init__(self) -> None:
        t0 = time.perf_counter()
        self.job = None
        self.used_taskkill = False
        self._reader = None
        self._reader_thread = None
        self.proc = subprocess.Popen(
            [sys.executable, str(HERE / "sync_isolation_probe.py"), "--worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, encoding="utf-8",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        self.job = _job_create_with_kill_on_close()
        h = _open_process(self.proc.pid)
        assigned = bool(h) and bool(_k32.AssignProcessToJobObject(self.job, h))
        if h:
            _k32.CloseHandle(h)
        if not assigned:
            _k32.TerminateJobObject(self.job, 1)
            _k32.CloseHandle(self.job)
            self.job = None
            self.used_taskkill = True
            # 降级路径：Job 分配被拒（宿主处于受限 Job）→ 换一个不做 Job
            # 分配的普通 worker，终止走 taskkill /T 树杀（语义见类 docstring）
            self.proc.kill()
            self.proc.wait()
            self.proc = subprocess.Popen(
                [sys.executable, str(HERE / "sync_isolation_probe.py"), "--worker"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                text=True, encoding="utf-8",
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        self.spawn_ms = (time.perf_counter() - t0) * 1000

    def send(self, obj: dict) -> None:
        self.proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def read(self, timeout_s: float) -> dict | None:
        """读一行事件；超时返回 None（不阻塞主实验节奏）。

        Windows 上 ``select()`` 只接受 socket 句柄，管道 fd 会 EINVAL——
        用后台读线程 + 队列做带超时的行读取。
        """
        import queue as _queue

        if self._reader is None:
            self._reader = _queue.Queue()

            def _pump(proc, q):
                for line in proc.stdout:
                    q.put(json.loads(line))
                q.put(None)  # EOF 哨兵（进程死亡/管道关闭）

            import threading
            self._reader_thread = threading.Thread(
                target=_pump, args=(self.proc, self._reader), daemon=True,
            )
            self._reader_thread.start()
        try:
            return self._reader.get(timeout=timeout_s)
        except _queue.Empty:
            return None

    def _confirm_pids(self, pids, timeout_s: float) -> dict:
        confirmed = {}
        for pid in pids:
            h = _open_process(pid)
            if not h:
                confirmed[pid] = "already-gone"
                continue
            code = _wait_handle(h, int(timeout_s * 1000))
            exit_code = ctypes.c_uint32()
            _k32.GetExitCodeProcess(h, ctypes.byref(exit_code))
            confirmed[pid] = (
                "exited" if code == WAIT_OBJECT_0 else
                f"still-alive(wait={code:#x})"
            )
            _k32.CloseHandle(h)
        return confirmed

    def kill_and_confirm(self, timeout_s: float = 5.0) -> dict:
        """终止并**确认** worker 及其后代全部退出（确认先于任何释放）。"""
        pids = [self.proc.pid, *getattr(self, "descendant_pids", [])]
        if self.job is not None:
            _k32.TerminateJobObject(self.job, 1)
        else:
            # taskkill /T：树终止（worker + 全部后代）
            subprocess.run(
                ["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                capture_output=True, check=False,
            )
        return self._confirm_pids(pids, timeout_s)

    def close(self) -> None:
        if self.job is not None:
            _k32.TerminateJobObject(self.job, 1)  # KILL_ON_JOB_CLOSE 兜底
            _k32.CloseHandle(self.job)
        if self.proc.poll() is None:
            self.proc.kill()


def _drain_until(worker: Worker, ev: str, timeout_s: float) -> dict | None:
    """读事件直到指定 ev（丢弃其他事件行）。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        obj = worker.read(0.2)
        if obj is None:
            continue
        if obj.get("ev") == ev or "result" in obj:
            return obj
    return None


def e1_startup_and_clean_shutdown(results: dict) -> None:
    w = Worker()
    w.send({"tool": "read_only", "args": {"payload": "ping"}, "ctx": {}})
    obj = _drain_until(w, "result", 5)
    assert obj and obj["result"]["status"] == "ok", f"E1 无结果: {obj}"
    confirmed = w.kill_and_confirm()
    assert all(v in ("exited", "already-gone") for v in confirmed.values()), confirmed
    results["E1"] = {
        "primitive": "taskkill_tree" if w.used_taskkill else "job_object",
        "spawn_ms": round(w.spawn_ms, 1),
        "echo_ok": obj["result"]["echo"] == "ping",
        "kill_confirmed": confirmed,
    }


def e2_context_boundary(results: dict) -> None:
    w = Worker()
    w.send({"tool": "read_only", "args": {"payload": "ctx-probe"},
            "ctx": {"workspace": "D:/probe-ws", "session_id": "sess-42"}})
    prog = _drain_until(w, "progress", 5)
    assert prog and prog.get("ctx_workspace") == "D:/probe-ws", f"E2 ctx 未跨界: {prog}"
    assert prog.get("ctx_session") == "sess-42"
    obj = _drain_until(w, "result", 5)
    assert obj and obj["result"]["status"] == "ok"
    results["E2"] = {
        "ctx_workspace_echo": prog["ctx_workspace"],
        "ctx_session_echo": prog["ctx_session"],
    }
    w.close()


def e3_permission_roundtrip(results: dict) -> None:
    # ALLOW 路
    w = Worker()
    w.send({"tool": "ask_permission",
            "args": {"id": "p-allow", "tool": "risky_write"}})
    req = _drain_until(w, "permission_request", 5)
    assert req and req["id"] == "p-allow", f"E3 无权限请求: {req}"
    t0 = time.perf_counter()
    w.send({"op": "permission_response", "id": "p-allow", "action": "allow"})
    obj = _drain_until(w, "result", 5)
    rt_ms = (time.perf_counter() - t0) * 1000
    assert obj and obj["result"]["status"] == "ok", f"E3 ALLOW 失败: {obj}"
    w.close()
    # DENY 路
    w2 = Worker()
    w2.send({"tool": "ask_permission",
             "args": {"id": "p-deny", "tool": "risky_write"}})
    req2 = _drain_until(w2, "permission_request", 5)
    assert req2 and req2["id"] == "p-deny"
    w2.send({"op": "permission_response", "id": "p-deny", "action": "deny"})
    obj2 = _drain_until(w2, "result", 5)
    assert obj2 and obj2["result"]["status"] == "denied", f"E3 DENY 失败: {obj2}"
    w2.close()
    results["E3"] = {"allow_ok": True, "deny_ok": True, "roundtrip_ms": round(rt_ms, 1)}


def e4_kill_process_tree(results: dict) -> None:
    w = Worker()
    w.send({"tool": "spawn_tree", "args": {}})
    tree = _drain_until(w, "tree", 5)
    assert tree and "child_pid" in tree, f"E4 未回报树: {tree}"
    w.descendant_pids = [tree["child_pid"], tree["grand_pid"]]
    time.sleep(0.3)  # 让两层后代稳定存活
    alive_before = []
    for pid in w.descendant_pids:
        h = _open_process(pid)
        alive_before.append(bool(h))
        if h:
            _k32.CloseHandle(h)
    confirmed = w.kill_and_confirm()
    w.close()
    results["E4"] = {
        "primitive": "taskkill_tree" if w.used_taskkill else "job_object",
        "descendants_alive_before_kill": alive_before,
        "kill_confirmed": confirmed,
    }
    assert all(alive_before), f"E4 后代未存活: {alive_before}"
    assert all(v in ("exited", "already-gone") for v in confirmed.values()), confirmed


def e5_deadline_and_lease(results: dict) -> None:
    from agentnodes.kernel.gateway.session_lease import SessionExecutionLease

    lease = SessionExecutionLease()
    token = lease.acquire("probe:s1", "proto:r1")
    assert token is not None
    w = Worker()
    w.send({"tool": "slow", "args": {"seconds": 10}})
    time.sleep(0.5)  # 预算到点
    assert lease.holding("probe:s1"), "终止前 lease 必须仍被持有"
    confirmed = w.kill_and_confirm()
    assert all(v in ("exited", "already-gone") for v in confirmed.values()), confirmed
    # 确认退出**之后**才释放
    late = w.read(0.3)  # 迟到结果探测（应为 None——进程已死）
    lease.release("probe:s1", token)
    assert not lease.holding("probe:s1")
    w.close()
    results["E5"] = {
        "lease_held_before_kill": True,
        "kill_confirmed": confirmed,
        "late_result": late,
        "lease_released_after_confirm": True,
    }


def main() -> int:
    if "--worker" in sys.argv:
        return _worker_loop()

    from agentnodes.kernel.gateway.session_lease import SessionExecutionLease  # noqa: F401

    results: dict = {}
    experiments = [
        ("E1 启动/关停+延迟", e1_startup_and_clean_shutdown),
        ("E2 上下文可序列化子集", e2_context_boundary),
        ("E3 权限 ASK 往返", e3_permission_roundtrip),
        ("E4 子进程树终止", e4_kill_process_tree),
        ("E5 截止+lease 序列", e5_deadline_and_lease),
    ]
    failures = []
    for name, fn in experiments:
        try:
            fn(results)
            print(f"[PASS] {name}")
        except Exception as exc:  # noqa: BLE001
            import traceback
            failures.append(name)
            results[name] = {"error": f"{type(exc).__name__}: {exc}"}
            print(f"[FAIL] {name}: {type(exc).__name__}: {exc}")
            traceback.print_exc()
    print("\n=== 原型结果 ===")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
