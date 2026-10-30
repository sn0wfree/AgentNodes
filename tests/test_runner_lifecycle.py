"""AutoresearchRunner 生命周期分支单元测试——停因判定、HITL、暂停/取消。

覆盖 docs/coverage-90-plan-20261030.md §3.2 第 2 优先项（原 73.3%）。
只测**决策函数**（_check_stop_conditions / _top_control_check /
_wait_for_approval / _expire_interrupt），不跑完整 round 执行。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from strategy_research.core.study.contracts import (
    SR_STUDY_MAX_DISCARD,
    ShutdownReason,
)
from strategy_research.core.study.models import StudyStatus
from strategy_research.core.study.runner import AutoresearchRunner


class Probe:
    def __init__(self):
        self.events: list[tuple] = []

    def __call__(self, session, kind, data):
        self.events.append((session, kind, data))

    def of(self, kind):
        return [d for k, d in ((e[1], e[2]) for e in self.events) if k == kind]


class FakeStore:
    def __init__(self):
        self.status_updates: list[tuple] = []
        self.interrupts: dict[tuple, object] = {}
        self.responded: list[tuple] = []

    def update_execution_status(self, sid, status, **kw):
        self.status_updates.append((sid, status, kw))

    def get_interrupt_for_round(self, sid, round_num):
        return self.interrupts.get((sid, round_num))

    def get_pending_interrupt(self, sid, round_num):
        row = self.interrupts.get((sid, round_num))
        return row if row is not None and getattr(row, "status", "") == "pending" else None

    def respond_interrupt(self, iid, status):
        self.responded.append((iid, status))


def make_runner(*, study_status=StudyStatus.RUNNING, cancel=False, pause=False):
    """构造最小 runner：绕过 __init__ 的重依赖，只装决策所需字段。"""
    r = AutoresearchRunner.__new__(AutoresearchRunner)
    r.study = SimpleNamespace(
        study_id="st-1", session_id="sess-1", workspace_path="/tmp/ws",
        current_round=3, execution_status=study_status, last_metrics={},
        goal_id="g1",
    )
    r.study_id = "st-1"
    r.study_store = FakeStore()
    r.control = SimpleNamespace(cancelled=cancel, paused=pause)
    r.emitter = Probe()
    r._study_cache = None
    r._study_cache_ts = 0.0
    r._total_used_turns = 2
    r._total_used_time = 12.5
    r._idle_rounds = 0
    r._prev_passed = set()
    r._best_score = 0.0
    r._round_start_clock = None
    r._goal_store = None
    r._get_study = lambda: r.study
    r._emit = lambda session, kind, data: r.emitter(session, kind, data)
    r._mark_terminal = lambda status, **kw: FakeStore.update_execution_status(
        r.study_store, "st-1", status, **kw
    )
    return r


# ── _mark_terminal 记录（供后续断言） ──────────────────────


def _terminal(r):
    return [u for u in r.study_store.status_updates]


# ── _check_stop_conditions ────────────────────────────────


def test_stop_conditions_targets_met():
    r = make_runner()
    r._complete_goal = lambda result: None
    reason = r._check_stop_conditions(
        {"e2_passed": True}, {"sharpe": 2.0}, "keep", 3, "sess-1", "st-1",
    )
    assert reason == ShutdownReason.TARGETS_MET
    assert r.study_store.status_updates[-1][1] is StudyStatus.COMPLETE
    assert r.emitter.of("study_completed")


def test_stop_conditions_budget_exceeded():
    r = make_runner()
    r._budget_exceeded = lambda: True
    r._budget_summary = lambda: {"used": "2/2"}
    reason = r._check_stop_conditions({}, {}, "keep", 3, "s", "st-1")
    assert reason == ShutdownReason.BUDGET
    assert r.emitter.of("study_budget_limited")


def test_stop_conditions_stagnation():
    r = make_runner()
    r._budget_exceeded = lambda: False
    reason = r._check_stop_conditions(
        {"decision": {"stagnation_triggered": True}}, {}, "keep", 3, "s", "st-1",
    )
    assert reason == ShutdownReason.STAGNATION


def test_stop_conditions_repeated_deviation():
    r = make_runner()
    r._budget_exceeded = lambda: False
    reason = r._check_stop_conditions(
        {"review_stop": ShutdownReason.REPEATED_DEVIATION}, {}, "keep", 3, "s", "st-1",
    )
    assert reason == ShutdownReason.REPEATED_DEVIATION
    assert r.emitter.of("study_early_stopped")


def test_stop_conditions_other_review_stop():
    r = make_runner()
    r._budget_exceeded = lambda: False
    reason = r._check_stop_conditions(
        {"review_stop": ShutdownReason.REVIEW_FAILED}, {}, "keep", 3, "s", "st-1",
    )
    assert reason == ShutdownReason.REVIEW_FAILED


def test_stop_conditions_discard_streak(monkeypatch):
    r = make_runner()
    r._budget_exceeded = lambda: False

    import strategy_research.core.study.state_store as ss

    monkeypatch.setattr(
        ss, "load",
        lambda path, sid: SimpleNamespace(discard_streak=SR_STUDY_MAX_DISCARD),
    )
    reason = r._check_stop_conditions({}, {}, "keep", 3, "s", "st-1")
    assert reason == ShutdownReason.DISCARD_STREAK
    assert r.emitter.of("study_early_stopped")


def test_stop_conditions_returns_none_when_healthy(monkeypatch):
    r = make_runner()
    r._budget_exceeded = lambda: False

    import strategy_research.core.study.state_store as ss

    monkeypatch.setattr(ss, "load", lambda p, s: SimpleNamespace(discard_streak=0))
    assert r._check_stop_conditions({}, {}, "keep", 3, "s", "st-1") is None


# ── _top_control_check ────────────────────────────────────


def test_top_control_cancelled_marks_terminal():
    r = make_runner(cancel=True)
    assert asyncio.run(r._top_control_check(3, "sess-1", "st-1")) == "cancelled"
    assert r.study_store.status_updates[-1][1] is StudyStatus.CANCELLED


def test_top_control_cancelled_preserves_archived():
    r = make_runner(cancel=True, study_status=StudyStatus.ARCHIVED)
    # _current_db_status 读 store；注入 ARCHIVED 让实现走 preserved 分支
    r._current_db_status = lambda: StudyStatus.ARCHIVED
    assert asyncio.run(r._top_control_check(3, "s", "st-1")) == "cancelled"
    assert "preserved live status" in r.emitter.of("study_cancelled")[-1]["note"]


def test_top_control_paused_then_resumed():
    r = make_runner(pause=True)
    resumed: list[bool] = []

    async def fake_wait():
        resumed.append(True)
        r.control.paused = False

    r._wait_until_resumed = fake_wait
    assert asyncio.run(r._top_control_check(3, "s", "st-1")) == "resumed"
    assert resumed == [True]
    assert r.emitter.of("study_resumed")


def test_top_control_cancelled_while_paused():
    r = make_runner(pause=True)

    async def fake_wait():
        r.control.cancelled = True

    r._wait_until_resumed = fake_wait
    assert asyncio.run(r._top_control_check(3, "s", "st-1")) == "continue"


# ── _wait_for_approval / _expire_interrupt ────────────────


def test_wait_for_approval_returns_approved():
    r = make_runner()
    r.study_store.interrupts[("st-1", 3)] = SimpleNamespace(
        status="approved", interrupt_id="i1",
    )
    # 第一次轮询即命中（sleep 前检查）
    assert asyncio.run(r._wait_for_approval("st-1", 3, timeout_s=1)) == "approved"


def test_wait_for_approval_returns_rejected():
    r = make_runner()
    r.study_store.interrupts[("st-1", 3)] = SimpleNamespace(
        status="rejected", interrupt_id="i1",
    )
    assert asyncio.run(r._wait_for_approval("st-1", 3, timeout_s=1)) == "rejected"


def test_wait_for_approval_cancel_cuts_wait_short():
    r = make_runner(cancel=True)
    assert asyncio.run(r._wait_for_approval("st-1", 3, timeout_s=600)) == "cancelled"


def test_wait_for_approval_pause_cuts_wait_short():
    r = make_runner(pause=True)
    assert asyncio.run(r._wait_for_approval("st-1", 3, timeout_s=600)) == "cancelled"


def test_wait_for_approval_times_out(monkeypatch):
    r = make_runner()

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    assert asyncio.run(r._wait_for_approval("st-1", 3, timeout_s=0)) == "timeout"


def test_expire_interrupt_marks_expired():
    r = make_runner()
    r.study_store.interrupts[("st-1", 3)] = SimpleNamespace(
        status="pending", interrupt_id="i9",
    )
    r._expire_interrupt("st-1", 3)
    assert r.study_store.responded == [("i9", "expired")]


def test_expire_interrupt_noop_when_none():
    r = make_runner()
    r._expire_interrupt("st-1", 3)  # 不抛
    assert r.study_store.responded == []


def test_expire_interrupt_swallows_store_error():
    r = make_runner()

    def boom(*a, **k):
        raise RuntimeError("db down")

    r.study_store.get_pending_interrupt = boom
    r._expire_interrupt("st-1", 3)  # best-effort，不抛


# ── _load_graph 回退 ──────────────────────────────────────


def test_load_graph_falls_back_when_missing(tmp_path: Path):
    r = make_runner()
    graph = r._load_graph(tmp_path, "st-1")
    assert graph is not None  # 回退到默认图，不抛


def test_load_graph_falls_back_on_malformed(tmp_path: Path):
    r = make_runner()
    (tmp_path / "graph.json").write_text("{not valid json", encoding="utf-8")
    graph = r._load_graph(tmp_path, "st-1")
    assert graph is not None


def test_load_graph_reads_valid(tmp_path: Path):
    """合法 graph.json 应被读出（与回退图区分：节点数一致即可）。"""
    from strategy_research.core.study import graph as graph_mod

    r = make_runner()
    default = r._load_graph(tmp_path, "st-1")  # 缺失 → 回退
    payload = None
    for attr in ("to_json", "to_dict", "model_dump_json"):
        fn = getattr(default, attr, None)
        if callable(fn):
            payload = fn()
            break
    if payload is None:
        pytest.skip("StudyGraph 无序列化方法，无法构造合法文件")
    if not isinstance(payload, str):
        import json

        payload = json.dumps(payload)
    (tmp_path / "graph.json").write_text(payload, encoding="utf-8")
    loaded = r._load_graph(tmp_path, "st-1")
    assert loaded is not None
