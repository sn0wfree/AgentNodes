# -*- coding: utf-8 -*-
"""隔离执行 adapter 平台 seam（4c B2.2 子批 3a-3c · 不接生产）。

把「进程隔离执行一个同步工具」封装为一个可替换的 adapter：Windows 用
Job Object / taskkill 树终止（从原型提炼），POSIX 尚未验证 → 禁启用
（``supported() == False``，调用抛 UnsupportedPlatform）。**本阶段零生产
接线**——只提供 seam 与平台保护，供 3b/3c 接 IPC 与试点。

顶层不 import ctypes.WinDLL——POSIX 必须能安全导入本模块（测试门禁在
POSIX CI 上也要能跑）。Windows 相关 ctypes 只在 build_worker_adapter()
内延迟加载。

设计：docs/design/isolated-execution-b22-design-20261119.md
"""
from __future__ import annotations

import json
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

__all__ = [
    "ACTUAL_STATES",
    "ApprovalProxy",
    "IsolationChannel",
    "IsolationResult",
    "IsolationWorker",
    "UnsupportedPlatform",
    "WorkerAdapter",
    "build_worker_adapter",
    "isolate_tool_call",
]


class UnsupportedPlatform(RuntimeError):
    """未验证/不支持的隔离执行平台——显式拒绝，不静默降级到 in-process。"""


@runtime_checkable
class WorkerAdapter(Protocol):
    """隔离 worker 的最小接口（spawn / 通信由 3b IPC 层填充）。"""

    def supported(self) -> bool:
        """当前平台是否已验证可用。"""
        ...

    def spawn(self, worker_script: str) -> Any:
        """启动隔离 worker，返回平台句柄。"""
        ...

    def kill_and_confirm(self, handle: Any, pids: list[int], timeout_s: float) -> dict[int, str]:
        """终止 worker 及其后代并**逐 pid 确认退出**（确认先于任何释放）。"""
        ...

    def close(self, handle: Any) -> None:
        """释放平台资源兜底。"""
        ...


class _PosixAdapter:
    """POSIX：未验证——显式禁启用（裁决：跨平台生产启用前必须验证）。"""

    def supported(self) -> bool:
        return False

    def spawn(self, worker_script: str) -> Any:
        raise UnsupportedPlatform(
            "POSIX 隔离执行尚未验证——跨平台生产启用前必须完成平台测试"
        )

    def kill_and_confirm(self, handle, pids, timeout_s):
        raise UnsupportedPlatform("POSIX 隔离执行尚未验证")

    def close(self, handle) -> None:
        return None


def build_worker_adapter() -> WorkerAdapter:
    """按当前平台返回 adapter；不支持则给禁启用 adapter（不抛，便于测试）。"""
    if sys.platform == "win32":
        from agentnodes.kernel.agent.tools._isolation_win32 import Win32JobAdapter

        return Win32JobAdapter()
    return _PosixAdapter()


# ── 3b：IPC 通道 + 审批代理 + 单入口 ────────────────────────────────

#: 实际状态词表——与 4a 的 ``timeout_tool_outcomes[].state`` 严格一致
#: （stopped / still_running / returned / unknown）。声明 ≠ 事实：这里记录
#: 的是**实际观测**，不由工具的声明能力推断。
ACTUAL_STATES = frozenset({"stopped", "still_running", "returned", "unknown"})

_WORKER_SCRIPT = str(Path(__file__).with_name("_isolation_worker.py"))

#: EOF 哨兵——与「读超时返回 None」区分（worker 只是慢 ≠ worker 已退出）
_EOF = object()

#: deadline 触发后 kill_and_confirm 的逐 pid 确认等待上限（确认先于释放）
_KILL_CONFIRM_TIMEOUT_S = 5.0

#: 任务行信封版本（B2.4 4b）。缺失/不兼容显式报错——与 ctx_v 正交：
#: ctx_v 管上下文 schema（B2.3），TASK_V 管任务行关联协议（本批）。
TASK_V = 1


def _normalize_ctx_wire(ctx: Any) -> dict[str, Any]:
    """把调用方 ctx 统一编码为带 ``ctx_v`` 的 wire dict（B2.3 3b）。

    接受（方案 §5 决策 5 双形态）：
    - ``None`` → 空 ToolContext 编码（只有 ``ctx_v``）；
    - :class:`ToolContext` → ``to_wire``（唯一编码器，回调字段不可能泄漏）；
    - **带 ``ctx_v`` 的 dict**（codec 产出或校验过的 wire）→ 原样透传；
    - 裸 dict（无 ``ctx_v``）→ 显式拒绝——不静默补版本（决策 3 零容忍），
      调用方必须经 :func:`to_wire` 构造，schema 漂移当场可见。
    """
    from agentnodes.kernel.agent.tools._isolation_context import IsolationContextError
    from agentnodes.kernel.agent.tools.core import ToolContext

    if ctx is None:
        ctx = ToolContext()
    if isinstance(ctx, ToolContext):
        from agentnodes.kernel.agent.tools._isolation_context import to_wire

        return to_wire(ctx)
    if isinstance(ctx, dict):
        if "ctx_v" not in ctx:
            raise IsolationContextError(
                "bare ctx dict is not allowed — use "
                "agentnodes.kernel.agent.tools._isolation_context.to_wire(...) "
                "to build a versioned wire dict"
            )
        return ctx  # 已带版本的 wire dict，由 worker from_wire 校验
    raise IsolationContextError(
        f"ctx must be ToolContext | versioned dict | None, got {type(ctx).__name__}"
    )


@dataclass
class IsolationResult:
    """一次隔离执行的结果。"""

    status: str                      # completed / terminated / failed
    payload: dict[str, Any] = field(default_factory=dict)
    actual_state: str = "unknown"    # ACTUAL_STATES 之一
    platform: str = ""              # 实际使用的 adapter 标识
    timed_out: bool = False         # deadline 触发（调用方据此杀+确认，3c D1）
    call_id: str = ""               # per-call id（B2.4 4b，任务↔结果关联）
    discarded_rows: int = 0         # 不匹配 call_id 被丢弃的回传行数（可观测）


class ApprovalProxy:
    """父进程侧的审批代理：worker 发 ASK → 父进程裁决 → 回传 worker。

    ``decider(request) -> str`` 返回 ``"allow"`` / ``"deny"``；由组合根注入
    （生产走 PermissionGateway，测试走 stub）。**worker 内不做权限判定**。
    """

    def __init__(self, decider: Callable[[dict[str, Any]], str]) -> None:
        self._decider = decider

    def decide(self, request: dict[str, Any]) -> str:
        action = self._decider(request)
        return action if action in ("allow", "deny") else "deny"


class IsolationChannel:
    """父进程侧的 JSON Lines 通道（任务行出 / 事件行入）。

    Windows 上 ``select()`` 不接受管道 fd → 用后台读线程 + 队列做带超时读取
    （原型同款实现）。
    """

    def __init__(self, handle: dict[str, Any]) -> None:
        self._handle = handle
        self._proc = handle["proc"]
        self._queue: "queue.Queue[Any]" = queue.Queue()
        self._reader: threading.Thread | None = None
        self._eof = False

    def start_reader(self) -> None:
        if self._reader is not None:
            return

        def _pump(proc, q):
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    q.put(json.loads(line))
                except ValueError:
                    # worker stdout 上混入非协议行（工具构造日志等）→ 跳过，
                    # 不让读线程崩溃（否则后续行全丢）。
                    continue
            q.put(_EOF)  # EOF 哨兵（worker 死亡/管道关闭）

        self._reader = threading.Thread(
            target=_pump, args=(self._proc, self._queue), daemon=True,
        )
        self._reader.start()

    def send(self, obj: dict[str, Any]) -> None:
        self._proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()

    def read(self, timeout_s: float) -> dict[str, Any] | None:
        """读一行事件。**返回 None 表示超时（尚无数据）**；worker 退出由
        ``_EOF`` 哨兵表示——两者必须可区分（否则 worker 只是慢就会被
        误判为已退出）。
        """
        try:
            obj = self._queue.get(timeout=timeout_s)
        except queue.Empty:
            return None
        if obj is _EOF:
            self._eof = True
            return None
        return obj

    def drain_until(
        self, predicate: Callable[[dict[str, Any]], bool], timeout_s: float,
    ) -> dict[str, Any] | None:
        """读事件直到命中 predicate（或超时/EOF）。"""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            obj = self.read(0.2)
            if obj is None:
                if self._eof:
                    return None
                continue
            if predicate(obj):
                return obj
        return None


class IsolationWorker:
    """常驻隔离 worker：spawn 一次，多次投递任务（进程池语义）。

    每次调用 spawn 一个 worker 的成本 ~500ms（worker 需 import agentnodes
    全量依赖）——生产与批量测试都应复用。生命周期：``with`` 或显式
    ``close()``；``close`` 走 adapter 的终止+确认路径。
    """

    def __init__(
        self,
        adapter: WorkerAdapter | None = None,
        *,
        timeout_s: float = 30.0,
    ) -> None:
        self._adapter = adapter or build_worker_adapter()
        if not self._adapter.supported():
            raise UnsupportedPlatform(
                "当前平台隔离执行未验证——跨平台生产启用前必须完成平台测试"
            )
        self._handle = self._adapter.spawn(_WORKER_SCRIPT)
        self._channel = IsolationChannel(self._handle)
        self._channel.start_reader()
        self._timeout_s = timeout_s
        self._closed = False
        self._poisoned = False

    @property
    def platform(self) -> str:
        return "isolated"

    def call(
        self,
        tool_name: str,
        args: dict[str, Any] | None = None,
        *,
        ctx: Any = None,  # ToolContext | versioned wire dict | None（B2.3 3b）
        approval: ApprovalProxy | None = None,
        timeout_s: float | None = None,
    ) -> IsolationResult:
        """在常驻 worker 内执行一次工具调用。

        调用以 ``unknown`` 结束（deadline 触发，或 worker 无结果行退出）
        → worker **毒化**：杀+确认后关闭，后续调用抛 ``RuntimeError``。
        理由（3c D2）：worker 可能仍在跑旧工具，旧任务的迟到结果行会
        被新调用冒名接收——毒化是拒收保证的最小实现（per-call id 关联
        协议留 B2.3 演进）。
        """
        if self._poisoned:
            raise RuntimeError(
                "IsolationWorker 已毒化（上次调用超时/无结果，工具可能仍在"
                "运行，迟到结果不得被新调用冒名接收）——禁止复用"
            )
        if self._closed:
            raise RuntimeError("IsolationWorker 已关闭")
        result = _execute_on_channel(
            self._channel, tool_name, args or {}, _normalize_ctx_wire(ctx),
            approval=approval, timeout_s=timeout_s or self._timeout_s,
            platform=self.platform,
        )
        if result.actual_state == "unknown":
            if result.timed_out:
                _confirm_termination(
                    self._adapter, self._handle, result,
                    _KILL_CONFIRM_TIMEOUT_S,
                )
            self._poisoned = True
            self._closed = True
            self._adapter.close(self._handle)
        return result

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._adapter.close(self._handle)

    def __enter__(self) -> "IsolationWorker":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _execute_on_channel(
    channel: IsolationChannel,
    tool_name: str,
    args: dict[str, Any],
    ctx: dict[str, Any],
    *,
    approval: ApprovalProxy | None,
    timeout_s: float,
    platform: str,
) -> IsolationResult:
    """在给定通道上执行一次工具调用并解析结果行。

    B2.4 4b：任务行带信封 ``v`` + 每次调用全新 ``call_id``；只消费
    回显同一 ``call_id`` 的行，不匹配行**丢弃并计数**（可观测，绝不
    接纳其 payload）——这是「常驻 worker 复用时迟到结果错配」的
    精确防线，与 3c D2 毒化构成纵深防御（二者互不替代）。
    """
    import uuid

    deadline = time.monotonic() + timeout_s
    call_id = f"{tool_name}:{uuid.uuid4().hex[:12]}"
    channel.send({
        "v": TASK_V, "call_id": call_id,
        "tool": tool_name, "args": args, "ctx": ctx,
    })

    timed_out = False
    discarded = 0
    while True:
        obj = channel.read(0.2)
        if obj is None:
            if channel._eof:
                # worker 已退出且无结果行 → 下方 unknown 分支
                break
            if time.monotonic() > deadline:
                # 截止触发 → 调用方负责杀+确认（3c D1），不无限等
                timed_out = True
                break
            continue
        # B2.4 4b：id 过滤——不匹配行丢弃计数，绝不接纳其 payload
        if obj.get("call_id") != call_id:
            discarded += 1
            continue
        if obj.get("ev") == "permission_request":
            action = "deny"
            if approval is not None:
                action = approval.decide(obj)
            channel.send({
                "v": TASK_V, "call_id": call_id,
                "op": "permission_response",
                "id": obj.get("id"),
                "action": action,
            })
            continue
        if "result" in obj:
            result = obj["result"]
            status = result.get("status")
            if status == "ok":
                # 工具自身可能返回结构化错误（例：写根保护拒绝）——
                # 那是**工具级失败**，worker 执行成功；实际状态按
                # 「已返回结果」记 returned。
                payload = result.get("output")
                tool_failed = False
                if isinstance(payload, str):
                    try:
                        decoded = json.loads(payload)
                    except ValueError:
                        decoded = None
                    if isinstance(decoded, dict) and decoded.get("status") == "error":
                        tool_failed = True
                if tool_failed:
                    return IsolationResult(
                        status="failed", payload=result,
                        actual_state="returned", platform=platform,
                        call_id=call_id, discarded_rows=discarded,
                    )
                return IsolationResult(
                    status="completed", payload=result,
                    actual_state="stopped", platform=platform,
                    call_id=call_id, discarded_rows=discarded,
                )
            if status == "denied":
                return IsolationResult(
                    status="failed", payload=result,
                    actual_state="returned", platform=platform,
                    call_id=call_id, discarded_rows=discarded,
                )
            return IsolationResult(
                status="failed", payload=result,
                actual_state="returned", platform=platform,
                call_id=call_id, discarded_rows=discarded,
            )
    # 未收到结果行：deadline 触发（待杀+确认）或 worker 异常退出 → 未知
    stderr_tail = ""
    err = channel._handle["proc"].stderr
    if err is not None:
        try:
            stderr_tail = err.read1(500) if hasattr(err, "read1") else ""
        except Exception:  # noqa: BLE001 —— 诊断信息尽力而为
            stderr_tail = ""
    return IsolationResult(
        status="failed",
        payload={
            "error": (
                "deadline exceeded, worker kill pending"
                if timed_out else "worker exited without result"
            ),
            "worker_stderr": stderr_tail[-500:],
        },
        actual_state="unknown", platform=platform, timed_out=timed_out,
        call_id=call_id, discarded_rows=discarded,
    )


def _confirm_termination(
    adapter: WorkerAdapter,
    handle: dict[str, Any],
    result: IsolationResult,
    confirm_timeout_s: float,
) -> None:
    """deadline 路径（3c D1）：杀 worker 并逐 pid 确认；全部确认死 →
    ``terminated``/``stopped``（4a 词表：确认终止才可记 stopped）；
    有 pid 未确认 → 保持 ``failed``/``unknown``。

    确认口径：确认的是**根 pid**；树内其余成员由 Job Object 语义覆盖
    （TerminateJobObject 原子杀全树 + KILL_ON_JOB_CLOSE 兜底）。
    """
    confirmed = adapter.kill_and_confirm(
        handle, [handle["proc"].pid], confirm_timeout_s,
    )
    if confirmed and all(
        v in ("exited", "already-gone") for v in confirmed.values()
    ):
        result.status = "terminated"
        result.actual_state = "stopped"


def isolate_tool_call(
    tool_name: str,
    args: dict[str, Any] | None = None,
    *,
    ctx: Any = None,  # ToolContext | versioned wire dict | None（B2.3 3b）
    approval: ApprovalProxy | None = None,
    timeout_s: float = 30.0,
    adapter: WorkerAdapter | None = None,
    worker: "IsolationWorker | None" = None,
) -> IsolationResult:
    """在隔离 worker 内执行一次工具调用（3b 单入口，**不接生产路径**）。

    流程：spawn worker → 任务行（含可序列化 ctx，B2.3 3b 起经 codec
    编码为带 ``ctx_v`` 的 wire dict）→ 处理事件行（审批请求转交
    ``approval`` 代理裁决）→ 结果行 → 收尾。返回的实际状态与 4a
    词表一致：正常完成 ``stopped``；审批拒绝/工具级失败/ctx 解码失败
    ``returned``（有明确结果但非成功）；deadline 触发 → 杀 worker 并逐
    pid 确认，确认死 → ``terminated``/``stopped``，未确认 → ``unknown``；
    worker 无结果行退出（EOF）→ ``unknown``。

    ``ctx`` 接受 :class:`ToolContext` / codec 产出的带版本 dict / None；
    裸 dict（无 ``ctx_v``）显式拒绝——调用方必须经 codec 构造。发送前
    编码失败在父进程抛 :class:`IsolationContextError`（坏 ctx 不出父进程）。

    平台未验证（POSIX）→ adapter 抛 UnsupportedPlatform（显式拒绝，
    不静默降级到 in-process 执行）。

    ``worker`` 传入常驻 worker 可复用进程（省 ~500ms/次的 spawn 成本）；
    不传则临时建一个并在返回前关闭（deadline 杀+确认发生在释放之前）。
    """
    if worker is not None:
        return worker.call(
            tool_name, args, ctx=ctx, approval=approval, timeout_s=timeout_s,
        )

    adapter = adapter or build_worker_adapter()
    if not adapter.supported():
        raise UnsupportedPlatform(
            "当前平台隔离执行未验证——跨平台生产启用前必须完成平台测试"
        )

    handle = adapter.spawn(_WORKER_SCRIPT)
    channel = IsolationChannel(handle)
    channel.start_reader()
    try:
        result = _execute_on_channel(
            channel, tool_name, args or {}, _normalize_ctx_wire(ctx),
            approval=approval, timeout_s=timeout_s, platform="isolated",
        )
        if result.timed_out:
            # 截止终止：先杀+确认，再在 finally 释放句柄（确认先于释放）
            _confirm_termination(adapter, handle, result, _KILL_CONFIRM_TIMEOUT_S)
        return result
    finally:
        adapter.close(handle)
