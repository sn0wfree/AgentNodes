"""monitor_phase 单元测试（B 档收尾）——取消/暂停/检查失败分支。

覆盖 docs/coverage-90-plan-20261030.md §3.2：`study/monitor.py` 77.2%。
monitor_phase(runner) 的分支用假 runner 驱动。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from strategy_research.core.study.monitor import monitor_phase
from strategy_research.core.study.models import StudyStatus


class Probe:
    def __init__(self):
        self.events: list[tuple] = []

    def __call__(self, session, kind, data):
        self.events.append((session, kind, data))


class FakeStore:
    def __init__(self):
        self.status_updates: list[tuple] = []

    def update_execution_status(self, sid, status, **kw):
        self.status_updates.append((sid, status, kw))


def _runner(*, cancel=False, pause=False, live_status=None,
            monitor_interval=1):
    r = SimpleNamespace()
    r.study = SimpleNamespace(
        study_id="st-1", session_id="sess-1",
        monitor_interval_seconds=monitor_interval, current_round=3,
        workspace_path="/tmp/ws",
    )
    r.study_store = FakeStore()
    r.control = SimpleNamespace(cancelled=cancel, paused=pause)
    r.emitter = Probe()
    r._get_study = lambda: r.study
    r._current_db_status = lambda: live_status
    r._emit = lambda session, kind, data: r.emitter(session, kind, data)
    r._wait_until_resumed = None  # 各测试注入
    r._rebacktest_keep_run = lambda **kw: None  # 默认不抛
    r._mark_terminal = lambda status, **kw: r.study_store.update_execution_status(
        "st-1", status, **kw)
    return r


def _emit_of(r, kind):
    return [d for sess, k, d in r.emitter.events if k == kind]


def test_monitor_cancel_preserves_archived():
    r = _runner(cancel=True, live_status=StudyStatus.ARCHIVED)
    r._wait_until_resumed = None
    reason = asyncio.run(monitor_phase(r))
    assert reason == "cancelled"
    assert "preserved live status" in _emit_of(r, "study_cancelled")[-1]["note"]


def test_monitor_cancel_marks_terminal():
    r = _runner(cancel=True, live_status=StudyStatus.RUNNING)
    # 停止后 while True 会再进 monitor check → 注入 check 抛错容忍
    checks = {"n": 0}

    async def fake_check(**kw):
        checks["n"] += 1
        raise RuntimeError("no re-backtest in unit test")

    r._rebacktest_keep_run = fake_check
    reason = asyncio.run(monitor_phase(r))
    assert reason == "cancelled"
    assert r.study_store.status_updates[-1][1] is StudyStatus.CANCELLED


def test_monitor_paused_then_cancelled_while_paused():
    r = _runner(pause=True, monitor_interval=0)
    r._rebacktest_keep_run = lambda **kw: None

    async def wait_then_cancel():
        r.control.paused = False
        r.control.cancelled = True

    r._wait_until_resumed = wait_then_cancel
    reason = asyncio.run(monitor_phase(r))
    assert reason == "cancelled"
    assert _emit_of(r, "study_paused")


def test_monitor_check_failure_emits_and_continues():
    """monitor check 抛错 → study_monitor_check_failed 事件 + 继续。"""
    r = _runner(monitor_interval=0)
    r._rebacktest_keep_run = lambda **kw: (_ for _ in ()).throw(
        RuntimeError("re-backtest down"))
    # check_failed 后 sleep(0)→检查 cancelled→False→再 check……
    # 用一次后把 cancelled 置位退出
    real_emit = r._emit
    fail_count = {"n": 0}

    def emit_and_stop(session, kind, data):
        real_emit(session, kind, data)
        if kind == "study_monitor_check_failed":
            fail_count["n"] += 1
            if fail_count["n"] >= 2:
                r.control.cancelled = True

    r._emit = emit_and_stop
    reason = asyncio.run(monitor_phase(r))
    assert reason == "cancelled"
    assert fail_count["n"] >= 2
    assert _emit_of(r, "study_monitor_check_failed")


def test_monitor_start_event_emitted():
    r = _runner(monitor_interval=0, cancel=True)
    started = asyncio.run(monitor_phase(r))
    assert started == "cancelled"
    assert _emit_of(r, "study_monitoring_started")
