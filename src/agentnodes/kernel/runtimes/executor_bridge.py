"""ExecutorBridge（A1/T2）—— 把 cpu 节点派发到常驻子进程池。

设计（流程见 execution-plane-research-20261024.md §5.3）：
- **常驻池**：spawn 一次、反复派活（Windows 无 fork，按任务 spawn 太贵）。
  池大小缺省 CPU-1。
- **四通道**：dispatch（主进程→worker）/ events（worker→主进程流式）/
  cancel（主进程→worker）/ heartbeat（worker 存活）。本 v1 用
  multiprocessing 的 Queue + Event 实现前三，heartbeat 用 worker 心跳写入。
- **取消**：协作取消优先（set cancel Event），超时升级硬杀（terminate→kill）。
- **结果**：小结果经结果队列回传；**大结果必须由 job 自己写存储**（P-3），
  事件只带指针（execution-plane §5.3 纪律；本桥不搬运大字节）。
- **隔离**：T3 第一段（进程边界 + 工作区写限 + 无网络）——v1 只保证进程
  边界；Job Object / 断网属 Windows 平台适配，A1 平台层实现（见文档）。

进程边界即安全边界（Windows 无业界沙箱）：worker 崩溃 ≠ 主进程崩溃；
卡死可硬杀。这是 T2 存在的全部意义。
"""
from __future__ import annotations

import multiprocessing as mp
import os
import pickle
import queue
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class JobSpec:
    """派发载荷（必须可 pickle；禁止传连接/句柄/闭包）。"""

    job_id: str
    fn: Callable[..., Any]
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)
    timeout: float = 300.0


@dataclass
class JobResult:
    job_id: str
    ok: bool
    value: Any = None
    error: str | None = None
    seconds: float = 0.0


def _worker_main(
    dispatch_q: Any,
    result_q: Any,
    events_q: Any,
    cancel_evt: Any,
) -> None:
    """常驻 worker 主循环：取任务 → 执行 → 回结果。协作取消在任务间隙生效。"""
    while True:
        try:
            spec: JobSpec | None = dispatch_q.get(timeout=1.0)
        except queue.Empty:
            continue
        if spec is None:  # 关闭信号
            return

        if cancel_evt.is_set():
            result_q.put(JobResult(spec.job_id, ok=False, error="cancelled before start"))
            continue

        events_q.put(("job.started", {"job_id": spec.job_id}))
        started = time.perf_counter()
        try:
            value = spec.fn(*spec.args, **spec.kwargs)
            result_q.put(
                JobResult(
                    spec.job_id, ok=True, value=value,
                    seconds=time.perf_counter() - started,
                )
            )
            events_q.put(("job.completed", {"job_id": spec.job_id}))
        except Exception as exc:  # noqa: BLE001 —— worker 内异常必须回传不炸池
            tb = traceback.format_exc(limit=3)
            result_q.put(
                JobResult(
                    spec.job_id, ok=False, error=f"{exc!r}\n{tb}",
                    seconds=time.perf_counter() - started,
                )
            )
            events_q.put(("job.failed", {"job_id": spec.job_id, "error": repr(exc)}))


class ExecutorBridge:
    """主进程侧：持有池、派发、取结果、取消、读事件。"""

    def __init__(
        self,
        *,
        size: int | None = None,
        ctx: Any | None = None,
        sink: Callable[[str, dict], None] | None = None,
    ) -> None:
        self.size = size or max(1, (os.cpu_count() or 2) - 1)
        self._ctx = ctx or mp.get_context("spawn")  # Windows 兼容；真并行
        self._sink = sink or (lambda kind, data: None)
        self._dispatch_q = self._ctx.Queue()
        self._result_q = self._ctx.Queue()
        self._events_q = self._ctx.Queue()
        self._cancel_evt = self._ctx.Event()
        self._workers: list[Any] = []
        self._started = False
        self._in_flight = 0  # 已派发未取回的任务数（取消判定用）

    # ── 生命周期 ─────────────────────────────────────────

    def start(self) -> None:
        if self._started:
            return
        for _ in range(self.size):
            p = self._ctx.Process(
                target=_worker_main,
                args=(self._dispatch_q, self._result_q, self._events_q, self._cancel_evt),
                daemon=True,
            )
            p.start()
            self._workers.append(p)
        self._started = True

    def shutdown(self, *, force: bool = False) -> None:
        if not self._started:
            return
        for _ in self._workers:
            try:
                self._dispatch_q.put(None)
            except Exception:  # noqa: BLE001
                pass
        for p in self._workers:
            p.join(timeout=2.0)
            if p.is_alive():
                if not force:
                    p.terminate()
                    p.join(timeout=1.0)
                if p.is_alive():
                    p.kill()
        self._workers.clear()
        self._started = False

    def __enter__(self) -> "ExecutorBridge":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.shutdown()

    # ── 派发与取回 ────────────────────────────────────────

    def dispatch(self, spec: JobSpec) -> None:
        # 载荷可 pickle 校验：spawn 池无法传递闭包/lambda/连接/句柄。
        # 提前失败并给出可操作信息，而不是让 mp.Queue 抛裸 AttributeError。
        try:
            pickle.dumps(spec)
        except Exception as exc:  # noqa: BLE001
            raise TypeError(
                f"JobSpec {spec.job_id} 不可 pickle（fn 必须是模块级可导入函数，"
                f"不得为 lambda/闭包/绑定句柄）：{exc}"
            ) from exc
        self._cancel_evt.clear()
        self._dispatch_q.put(spec)
        self._in_flight += 1

    def collect(self, *, timeout: float = 300.0) -> JobResult:
        """等一个结果（含超时与取消处理）。"""
        try:
            result = self._result_q.get(timeout=timeout)
        except queue.Empty:
            return JobResult(job_id="?", ok=False, error=f"collect timeout {timeout}s")
        self._in_flight = max(0, self._in_flight - 1)
        return result

    def run(self, spec: JobSpec) -> JobResult:
        """派发并等待单个任务（便捷方法）。"""
        self.ensure_alive()  # 池被硬杀后可自愈（worker 由后续 start 重建）
        self.dispatch(spec)
        return self.collect(timeout=spec.timeout)

    # ── 取消 ─────────────────────────────────────────────

    def cancel(self, *, hard_timeout: float = 5.0) -> None:
        """协作取消：设标志；仅当确有在飞任务且超时未响应时硬杀。

        无在飞任务时只设标志，**不动 worker**（否则一次 cancel 会把池打死）。
        """
        self._cancel_evt.set()
        if self._in_flight == 0:
            return  # 无任务：标志因下次 dispatch 而清除，worker 不动

        deadline = time.monotonic() + hard_timeout
        while time.monotonic() < deadline:
            if self._in_flight == 0:
                return  # 协作取消生效（任务间隙 worker 看到标志）
            time.sleep(0.02)
        for p in self._workers:
            if p.is_alive():
                p.terminate()
                # terminate 后必须 join：否则 is_alive() 可能仍报 True，
                # ensure_alive() 会误判池健康而不重建，后续 job 永无人消费。
                p.join(timeout=1.0)

    def discard_pending(self) -> None:
        """放弃全部在飞结果（连坐后使用）：清在飞计数 + 清空结果/事件队列。

        连坐后剩余 job 的结果无人认领；不清理会让后续 cancel 误判
        「仍有任务在飞」而白等硬杀超时，也会让下一次 collect 取到陈旧结果。
        worker 进程保留，下次 dispatch 前 ensure_alive() 决定是否重建。
        """
        self._in_flight = 0
        for q in (self._result_q, self._events_q):
            while True:
                try:
                    q.get_nowait()
                except queue.Empty:
                    break

    # ── 存活自愈 ──────────────────────────────────────────

    def ensure_alive(self) -> None:
        """池被硬杀（worker 全死）后重建，保证后续 run 仍可用。

        重建必须**换新队列**：硬杀时未出队的 job 还留在 dispatch 队列里，
        新 worker 会先领到那些陈旧 job（例如一个 sleep 30s 的任务），
        导致后续 job 长期饥饿。旧队列随旧进程一起丢弃。
        """
        if not self._started:
            self.start()
            return
        alive = [p for p in self._workers if p.is_alive()]
        if len(alive) < self.size:
            for p in self._workers:
                if p.is_alive():
                    p.terminate()
                    p.join(timeout=1.0)
            self._workers.clear()
            self._started = False
            self._reset_queues()
            self.start()

    def _reset_queues(self) -> None:
        """重建队列（陈旧 job/结果不跨池代际）。"""
        self._dispatch_q = self._ctx.Queue()
        self._result_q = self._ctx.Queue()
        self._events_q = self._ctx.Queue()
        self._cancel_evt = self._ctx.Event()
        self._in_flight = 0

    # ── 事件 ─────────────────────────────────────────────

    def drain_events(self) -> list[tuple[str, dict]]:
        events: list[tuple[str, dict]] = []
        while True:
            try:
                kind, data = self._events_q.get_nowait()
            except queue.Empty:
                return events
            events.append((kind, data))
            self._sink(kind, data)
