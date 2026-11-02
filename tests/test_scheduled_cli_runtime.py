"""H 档补测（P1-5b）—— `scheduled_research/cli.py` 的 run/start 命令。

``cmd_schedule_run`` 与 ``cmd_schedule_start`` 此前完全空白。它们是
「定时研究」真正的执行入口：

- ``run``：立即跑一次任务，然后**轮询 study 直到终态**再打印结果。
  轮询条件写错就会永远挂住或提前退出。
- ``start``：常驻循环，负责信号注册、崩溃恢复（stale running）、
  以及停止时的收尾。

两个函数都靠 mock 掉 scheduler/executor/store 来测——不启动真循环。
"""
from __future__ import annotations

import argparse
import asyncio

import pytest

from strategy_research.core.scheduled_research import cli
from strategy_research.core.scheduled_research.models import (
    JobStatus,
    ScheduledResearchJob,
)


def _job(job_id: str = "j1", last_run_id: str | None = None) -> ScheduledResearchJob:
    return ScheduledResearchJob(
        id=job_id,
        workspace="/tmp/ws",
        strategy_name="s1",
        prompt="do research",
        cron="",
        interval_ms=0,
        max_rounds=1,
        status=JobStatus.PENDING,
        last_run_id=last_run_id,
    )


class FakeStore:
    def __init__(self, jobs: dict | None = None):
        self._jobs = jobs or {}

    def get(self, job_id):
        return self._jobs.get(job_id)

    def recover_stale_running(self) -> int:
        return 0

    def list_jobs(self, status=None):
        return [j for j in self._jobs.values() if status is None or j.status == status]


class FakeStudy:
    """study 记录的替身：execution_status 有 .value，另有 current_round 等。"""

    class _Status:
        def __init__(self, v):
            self.value = v

    def __init__(self, status="completed", rounds=3, metrics=None, error=None):
        self.execution_status = self._Status(status)
        self.current_round = rounds
        self.last_metrics = metrics
        self.last_error = error


class FakeStudyStore:
    def __init__(self, records: list):
        self._records = records
        self._i = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_study(self, study_id):
        if self._i < len(self._records):
            rec = self._records[self._i]
            self._i += 1
            return rec
        return self._records[-1] if self._records else None


class FakeExecutor:
    def __init__(self, *a, **kw):
        self.stopped = False
        self.started = False
        self.tick_interval = kw.get("tick_interval")

    async def run_once_async(self, job_id):
        return None

    def start(self, loop=None):
        self.started = True

    def stop(self):
        self.stopped = True

    _running = False


# ── _build_study_scheduler ────────────────────────────────


def test_build_study_scheduler_has_no_session_service(monkeypatch):
    """CLI 上下文无 SSE——scheduler 必须用 None session_service。"""
    captured = {}

    class FakeScheduler:
        def __init__(self, store=None, session_service=None):
            captured["session_service"] = session_service

    monkeypatch.setattr(
        "strategy_research.core.study.StudyScheduler", FakeScheduler, raising=False)
    monkeypatch.setattr(
        "strategy_research.core.study.StudyStore", lambda *a, **k: object(), raising=False)
    cli._build_study_scheduler()
    assert captured["session_service"] is None


# ── cmd_schedule_run ─────────────────────────────────────


def test_run_unknown_job_returns_1(monkeypatch, capsys):
    monkeypatch.setattr(cli, "ScheduledResearchStore", lambda: FakeStore({}))
    assert cli.cmd_schedule_run(argparse.Namespace(job_id="missing")) == 1
    assert "不存在" in capsys.readouterr().out


def test_run_without_study_id_returns_1(monkeypatch, capsys):
    """任务跑了但没产出 study——必须报错，不能假装成功。"""
    monkeypatch.setattr(cli, "ScheduledResearchStore", lambda: FakeStore({"j1": _job()}))
    monkeypatch.setattr(cli, "_build_study_scheduler", lambda: object())
    monkeypatch.setattr(cli, "ScheduledResearchExecutor", FakeExecutor)
    monkeypatch.setattr(
        "strategy_research.core.study.StudyStore", lambda: FakeStudyStore([]), raising=False)
    code = cli.cmd_schedule_run(argparse.Namespace(job_id="j1"))
    assert code == 1
    assert "未产生 study" in capsys.readouterr().out


def test_run_reports_terminal_status_and_rounds(monkeypatch, capsys):
    jobs = {"j1": _job(last_run_id="st1")}
    monkeypatch.setattr(cli, "ScheduledResearchStore", lambda: FakeStore(jobs))
    monkeypatch.setattr(cli, "_build_study_scheduler", lambda: object())
    monkeypatch.setattr(cli, "ScheduledResearchExecutor", FakeExecutor)
    # 第一次查询返回 running（触发轮询），第二次 complete（终态）
    records = [FakeStudy("running"), FakeStudy("complete", rounds=4),
               FakeStudy("complete", rounds=4)]
    monkeypatch.setattr(
        "strategy_research.core.study.StudyStore", lambda: FakeStudyStore(records),
        raising=False)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    code = cli.cmd_schedule_run(argparse.Namespace(job_id="j1"))
    out = capsys.readouterr().out
    assert code == 0
    assert "complete" in out
    assert "rounds=4" in out


def test_run_prints_metrics_and_error_when_present(monkeypatch, capsys):
    jobs = {"j1": _job(last_run_id="st1")}
    monkeypatch.setattr(cli, "ScheduledResearchStore", lambda: FakeStore(jobs))
    monkeypatch.setattr(cli, "_build_study_scheduler", lambda: object())
    monkeypatch.setattr(cli, "ScheduledResearchExecutor", FakeExecutor)
    rec = FakeStudy("error", metrics={"calmar": 0.3}, error="data missing")
    monkeypatch.setattr(
        "strategy_research.core.study.StudyStore", lambda: FakeStudyStore([rec]),
        raising=False)

    cli.cmd_schedule_run(argparse.Namespace(job_id="j1"))
    out = capsys.readouterr().out
    assert "calmar" in out
    assert "data missing" in out


def test_run_exits_when_study_record_disappears(monkeypatch, capsys):
    """study 记录不见了（被删）也算终态——不能无限轮询。"""
    jobs = {"j1": _job(last_run_id="st1")}
    monkeypatch.setattr(cli, "ScheduledResearchStore", lambda: FakeStore(jobs))
    monkeypatch.setattr(cli, "_build_study_scheduler", lambda: object())
    monkeypatch.setattr(cli, "ScheduledResearchExecutor", FakeExecutor)
    monkeypatch.setattr(
        "strategy_research.core.study.StudyStore", lambda: FakeStudyStore([None]),
        raising=False)

    assert cli.cmd_schedule_run(argparse.Namespace(job_id="j1")) == 0
    assert "gone" in capsys.readouterr().out


# ── cmd_schedule_start ───────────────────────────────────


def test_start_registers_signal_handlers_and_stops(monkeypatch, capsys):
    """启动 → 恢复 → 列表 → start → 循环退出 → 收尾。"""
    jobs = {"j1": _job()}
    store = FakeStore(jobs)
    recovered = {"n": 0}

    def fake_recover():
        recovered["n"] += 1
        return 2

    store.recover_stale_running = fake_recover
    monkeypatch.setattr(cli, "ScheduledResearchStore", lambda: store)
    monkeypatch.setattr(cli, "_build_study_scheduler", lambda: _FakeSched())

    class Exec(FakeExecutor):
        _running = True

        def start(self, loop=None):
            self.started = True
            Exec._running = False  # 一次循环后退出

        def stop(self):
            self.stopped = True
            Exec._running = False

    monkeypatch.setattr(cli, "ScheduledResearchExecutor", Exec)

    handler_calls = []

    class FakeLoop:
        def add_signal_handler(self, sig, cb):
            handler_calls.append(sig)

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: FakeLoop())
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    code = cli.cmd_schedule_start(argparse.Namespace(tick=1))
    out = capsys.readouterr().out
    assert code == 0
    assert recovered["n"] == 1
    assert "恢复了 2 个中断的任务" in out
    assert "调度器已停止" in out


def test_start_survives_unsupported_signal_handlers(monkeypatch):
    """Windows 上 add_signal_handler 会抛 NotImplementedError——必须吞掉。"""
    monkeypatch.setattr(cli, "ScheduledResearchStore", lambda: FakeStore({}))
    monkeypatch.setattr(cli, "_build_study_scheduler", lambda: _FakeSched())

    class Exec(FakeExecutor):
        _running = True

        def start(self, loop=None):
            Exec._running = False

    monkeypatch.setattr(cli, "ScheduledResearchExecutor", Exec)

    class BadLoop:
        def add_signal_handler(self, *a):
            raise NotImplementedError

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: BadLoop())
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    assert cli.cmd_schedule_start(argparse.Namespace(tick=1)) == 0


def test_start_swallows_keyboard_interrupt(monkeypatch):
    def boom(coro):
        coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(asyncio, "run", boom)
    assert cli.cmd_schedule_start(argparse.Namespace(tick=1)) == 0


# ── 辅助 ──────────────────────────────────────────────────


async def _no_sleep(*a, **k):
    return None


class _FakeSched:
    async def shutdown(self):
        return None
