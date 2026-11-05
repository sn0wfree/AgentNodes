"""ExecutorBridge 测试 —— 真实子进程池（spawn），覆盖派发/结果/异常/取消/隔离。

注意：spawn 要求任务函数是**模块级**可 pickle 对象；lambda/闭包会失败——
这本身就是 T2 契约的一部分（JobSpec 载荷必须可 pickle）。
"""
from __future__ import annotations

import time

import pytest

from agentnodes.kernel.runtimes.executor_bridge import (
    ExecutorBridge,
    JobResult,
    JobSpec,
)


# ── 模块级任务（spawn 可 pickle） ────────────────────────


def add(a: int, b: int) -> int:
    return a + b


def boom() -> None:
    raise RuntimeError("worker exploded")


def slow(seconds: float) -> str:
    time.sleep(seconds)
    return "done"


@pytest.fixture
def bridge():
    b = ExecutorBridge(size=2)
    b.start()
    yield b
    b.shutdown(force=True)


# ── 基本派发 ─────────────────────────────────────────────


def test_run_job_returns_value(bridge):
    result = bridge.run(JobSpec(job_id="j1", fn=add, args=(2, 3)))
    assert isinstance(result, JobResult)
    assert result.ok and result.value == 5
    assert result.seconds >= 0


def test_kwargs_passed(bridge):
    result = bridge.run(JobSpec(job_id="j2", fn=dict, kwargs={"x": 1}))
    assert result.ok and result.value == {"x": 1}


def test_job_events_emitted(bridge):
    bridge.run(JobSpec(job_id="j3", fn=add, args=(1, 1)))
    time.sleep(0.2)  # 事件异步写入
    events = bridge.drain_events()
    kinds = [k for k, _ in events]
    assert "job.started" in kinds and "job.completed" in kinds


# ── 故障隔离（T2 的核心价值） ────────────────────────────


def test_job_exception_does_not_break_pool(bridge):
    bad = bridge.run(JobSpec(job_id="bad", fn=boom))
    assert not bad.ok and "worker exploded" in (bad.error or "")

    good = bridge.run(JobSpec(job_id="good", fn=add, args=(10, 20)))
    assert good.ok and good.value == 30  # 池仍可用


def test_worker_crash_does_not_kill_parent(bridge):
    """job 抛错只影响该 job；主进程继续运行（进程边界 = 安全边界）。"""
    bridge.run(JobSpec(job_id="crash", fn=boom))
    assert all(p.is_alive() or p.exitcode is not None for p in bridge._workers)
    assert bridge.run(JobSpec(job_id="after", fn=add, args=(1, 2))).ok


# ── 载荷约束（真实契约） ─────────────────────────────────


def test_unpicklable_payload_is_rejected_not_silent(bridge):
    """闭包不可 pickle → 派发即 TypeError（明确报错，不静默丢任务）。"""
    local = lambda: 1  # noqa: E731 —— 故意违反契约
    with pytest.raises(TypeError, match="不可 pickle"):
        bridge.dispatch(JobSpec(job_id="unpicklable", fn=local))


# ── 取消 ─────────────────────────────────────────────────


def test_cancel_with_no_in_flight_keeps_pool_alive(bridge):
    """无在飞任务时 cancel 只设标志，不得杀池（旧实现的真 bug）。"""
    bridge.cancel()
    assert all(p.is_alive() for p in bridge._workers)
    # 后续任务仍可执行（dispatch 会清标志）
    result = bridge.run(JobSpec(job_id="c1", fn=add, args=(1, 1), timeout=3.0))
    assert result.ok and result.value == 2


def test_cancel_hard_timeout_terminates_workers_then_pool_heals(bridge):
    bridge.dispatch(JobSpec(job_id="s1", fn=slow, args=(5.0,), timeout=10.0))
    time.sleep(0.2)
    bridge.cancel(hard_timeout=0.3)  # 超时后硬杀
    time.sleep(0.3)
    # 硬杀后池自愈：ensure_alive 在 run 时重建 worker
    healed = bridge.run(JobSpec(job_id="after-cancel", fn=add, args=(2, 2), timeout=10.0))
    assert healed.ok and healed.value == 4


# ── 生命周期 ─────────────────────────────────────────────


def test_context_manager_lifecycle():
    with ExecutorBridge(size=1) as b:
        assert b.run(JobSpec(job_id="x", fn=add, args=(1, 1))).ok
    assert not b._started


def test_shutdown_is_idempotent(bridge):
    bridge.shutdown()
    bridge.shutdown()  # 再次调用不抛
