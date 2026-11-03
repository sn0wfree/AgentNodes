"""Study v2 M7 tests — monitor perpetual mechanism (design §15) + events.

Covers: E2 completion → MONITORING, monitor check (last keep run, no LLM),
drift → NEEDS_REFRESH + auto repair rounds (back to MONITORING / stay
needs_refresh after 3 failed), pause/cancel via control token, MONITORING
recover (skips research rounds), and the event payloads.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from strategy_research.core.goal import GoalStore
from strategy_research.core.goal.context import default_goal_criteria
from strategy_research.core.study import StudyStatus, StudyStore
from strategy_research.core.study import state_store as ss
from strategy_research.core.study.runner import (
    AutoresearchRunner,
    ControlToken,
    NullEmitter,
    ShutdownReason,
)


class _Collector:
    def __init__(self):
        self.events: list[tuple[str, str, dict]] = []

    def emit(self, session_id: str, event: str, data: dict) -> None:
        self.events.append((session_id, event, data))

    def names(self) -> list[str]:
        return [e for _, e, _ in self.events]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "g.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "h.json"))
    ws = tmp_path / "ws"
    (ws / "strategies" / "demo").mkdir(parents=True)
    (ws / "strategies" / "demo" / "strategy.py").write_text("PARAMS = {}\n", encoding="utf-8")
    return ws


def _make_runner(env, *, monitor_interval: int | None = 60, status=None,
                 metrics=None, budget_turn: int | None = None):
    gs = GoalStore()
    store = StudyStore()
    goal = gs.replace_goal(
        session_id="sess-m", objective="x",
        criteria=default_goal_criteria(), supersede=False,
    )
    study = store.create_study(
        owner_session_id="sess-m", goal_id=goal.goal_id, objective="x",
        workspace_path=str(env), strategy_name="demo",
        metric_targets=[{"name": "calmar", "op": ">=", "value": 0.5}],
        cooldown_base=0.01, cooldown_jitter=0.01, min_cooldown=0.01,
        max_rounds=None, monitor_interval_seconds=monitor_interval,
        budget_turn=budget_turn,
    )
    store.update_goal_id(study.study_id, goal.goal_id)
    from strategy_research.core.study.bootstrap import init_study_dir as _init_study_dir
    _init_study_dir(env, study.study_id, "demo", "x")
    if status is not None:
        store.update_execution_status(study.study_id, status)
        study = store.get_study(study.study_id)  # fresh snapshot (status)
    return gs, store, study


def _patch_round(monkeypatch, *, e2_passed=True, rounds_counter=None):
    if callable(e2_passed):
        passes = e2_passed
    else:
        val = e2_passed

        def passes(call_index):
            return val

    def _round(self, r, prev, directive_text=None):
        if rounds_counter is not None:
            rounds_counter["n"] += 1
        call_index = rounds_counter["n"] if rounds_counter is not None else r
        return {
            "round": r, "run_name": f"run_{r:04d}", "run_dir": Path("/tmp/fake"),
            "metrics": {"calmar": 0.6, "sharpe": 0.4, "max_dd": -0.1},
            "verdict": "keep",
            "e2_passed": passes(call_index),
            "decision": {"stagnation_triggered": False},
            "agent_outputs": {}, "summary": {"round": r},
            "backtest_error": None, "passed_now": {"calmar"},
        }
    monkeypatch.setattr(AutoresearchRunner, "_run_one_round", _round)
    monkeypatch.setattr(AutoresearchRunner, "_round_cooldown", lambda self: 0.0)
    monkeypatch.setattr(AutoresearchRunner, "_maybe_load_previous_summary", lambda self, s: None)


def _patch_monitor_check(monkeypatch, results: list[dict]):
    """results: queue of check dicts; last one repeats."""
    state = {"idx": 0}

    def _check(self):
        i = min(state["idx"], len(results) - 1)
        state["idx"] += 1
        return dict(results[i])

    monkeypatch.setattr(AutoresearchRunner, "_run_monitor_check", _check)

    # _monitor_sleep is a module-level function in monitor.py since the
    # runner refactor — patch it there (the loop calls it unqualified).
    import strategy_research.core.study.monitor as monitor_mod

    async def _no_sleep(interval):
        return None

    monkeypatch.setattr(monitor_mod, "_monitor_sleep", _no_sleep)


def _check(meets_targets: bool, metrics=None) -> dict:
    return {
        "metrics": metrics or {"calmar": 0.6},
        "verdict": "monitor",
        "meets_targets": meets_targets,
        "reason": "" if meets_targets else "metric_targets no longer met",
        "now_iso": "2026-08-12T00:00:00+00:00",
    }


def test_monitor_check_targets_last_keep_run(env, monkeypatch):
    """Monitor re-backtests the last keep run's strategy.py (§15.2)."""
    gs, store, study = _make_runner(env)
    state = ss.load(env, study.study_id)
    state.last_keep_run_dir = "rounds/round_0002/run_0001"
    ss.save(env, study.study_id, state)
    keep_dir = env / "study" / study.study_id / "rounds" / "round_0002" / "run_0001"
    keep_dir.mkdir(parents=True)
    (keep_dir / "strategy.py").write_text("PARAMS = {}\n", encoding="utf-8")
    captured: dict = {}
    import strategy_research.apps.backtest as bt_mod

    def fake_backtest(**kw):
        captured.update(kw)
        return {"success": True, "metrics": {"calmar": 0.8}}

    monkeypatch.setattr(bt_mod, "run_backtest_script", fake_backtest)
    runner = AutoresearchRunner(study, store, control=ControlToken(), emitter=NullEmitter())
    check = runner._run_monitor_check()
    assert check["meets_targets"] is True
    assert captured["action"] == "monitor"
    assert captured["strategy_dir"] == (env / "study" / study.study_id / "rounds" / "round_0002" / "run_0001")


def test_monitor_phase_enters_monitoring_and_cancel(env, monkeypatch):
    """E2 completion with interval → MONITORING; checks pass; cancel stops."""
    _patch_round(monkeypatch)
    _patch_monitor_check(monkeypatch, [_check(True)])
    gs, store, study = _make_runner(env, monitor_interval=60)
    collector = _Collector()
    control = ControlToken()
    runner = AutoresearchRunner(study, store, control=control, emitter=collector)

    async def main():
        task = asyncio.create_task(runner.run())
        # wait until monitoring started + at least one check passed
        for _ in range(500):
            if "study_monitor_check" in collector.names():
                break
            await asyncio.sleep(0.01)
        assert store.get_study(study.study_id).execution_status == StudyStatus.MONITORING
        assert "study_monitoring_started" in collector.names()
        assert "study_monitor_check" in collector.names()
        control.cancelled = True
        reason = await task
        assert reason == ShutdownReason.CANCELLED

    asyncio.run(main())


def test_monitor_drift_repairs_back_to_monitoring(env, monkeypatch):
    """Drift → NEEDS_REFRESH + repair round passes → back to MONITORING."""
    rounds = {"n": 0}
    _patch_round(monkeypatch, e2_passed=True, rounds_counter=rounds)
    _patch_monitor_check(monkeypatch, [_check(False), _check(True)])
    gs, store, study = _make_runner(env, monitor_interval=60)
    collector = _Collector()
    control = ControlToken()
    runner = AutoresearchRunner(study, store, control=control, emitter=collector)

    async def main():
        task = asyncio.create_task(runner.run())
        for _ in range(1000):
            names = collector.names()
            if "study_monitoring_started" in names and names.count("study_monitoring_started") >= 2:
                break
            await asyncio.sleep(0.01)
        assert "study_drift_detected" in collector.names()
        assert rounds["n"] == 2  # 1 research round + 1 repair round, E2 pass
        assert store.get_study(study.study_id).execution_status == StudyStatus.MONITORING
        control.cancelled = True
        await task

    asyncio.run(main())


def test_monitor_drift_three_failed_repairs_stays_needs_refresh(env, monkeypatch):
    """3 failed repair rounds → stays NEEDS_REFRESH (design §15.2)."""
    rounds = {"n": 0}
    _patch_round(monkeypatch, e2_passed=lambda i: i == 1, rounds_counter=rounds)
    _patch_monitor_check(monkeypatch, [_check(False)])
    gs, store, study = _make_runner(env, monitor_interval=60)
    collector = _Collector()
    runner = AutoresearchRunner(study, store, control=ControlToken(), emitter=collector)

    async def main():
        reason = await runner.run()
        assert reason == ShutdownReason.NEEDS_REFRESH
        assert rounds["n"] == 4  # 1 research + 3 failed repairs
        assert store.get_study(study.study_id).execution_status == StudyStatus.NEEDS_REFRESH

    asyncio.run(main())


def test_monitor_recover_skips_research_rounds(env, monkeypatch):
    """A MONITORING study restarts directly into the monitor phase."""
    rounds = {"n": 0}
    _patch_round(monkeypatch, e2_passed=True, rounds_counter=rounds)
    _patch_monitor_check(monkeypatch, [_check(True)])
    gs, store, study = _make_runner(env, monitor_interval=60, status=StudyStatus.MONITORING)
    control = ControlToken()
    runner = AutoresearchRunner(study, store, control=control, emitter=NullEmitter())

    async def main():
        task = asyncio.create_task(runner.run())
        await asyncio.sleep(0.1)
        assert rounds["n"] == 0  # no research rounds ran
        control.cancelled = True
        reason = await task
        assert reason == ShutdownReason.CANCELLED

    asyncio.run(main())


def test_repair_budget_exhausted_stays_needs_refresh(env, monkeypatch):
    """Budget exhaustion during repair → needs_refresh + study_budget_limited."""
    rounds = {"n": 0}
    _patch_round(monkeypatch, e2_passed=lambda i: i == 1, rounds_counter=rounds)
    _patch_monitor_check(monkeypatch, [_check(False)])
    # budget_turn must be persisted (budget_exceeded reads the store
    # snapshot, not an in-memory dataclass replacement).
    gs, store, study = _make_runner(
        env, monitor_interval=60, status=StudyStatus.MONITORING,
        budget_turn=1,
    )
    collector = _Collector()
    runner = AutoresearchRunner(study, store, control=ControlToken(), emitter=collector)
    runner._total_used_turns = 1

    async def main():
        reason = await runner.run()
        assert reason == ShutdownReason.NEEDS_REFRESH
        assert rounds["n"] == 0
        assert "study_budget_limited" in collector.names()

    asyncio.run(main())


# ── v4: SSE events carry trace context ───────────────────────────────


def test_emitted_events_carry_trace_id(env, monkeypatch):
    """study_started / study_round / study_complete events must include
    a stable study-scoped trace_id + study_id + round_num (log correlation)."""
    _patch_round(monkeypatch)
    gs = GoalStore()
    store = StudyStore()
    goal = gs.replace_goal(
        session_id="sess-t", objective="x",
        criteria=default_goal_criteria(), supersede=False,
    )
    study = store.create_study(
        owner_session_id="sess-t", goal_id=goal.goal_id, objective="x",
        workspace_path=str(env), strategy_name="demo",
        metric_targets=[{"name": "calmar", "op": ">=", "value": 0.5}],
        cooldown_base=0.01, cooldown_jitter=0.01, min_cooldown=0.01,
        max_rounds=None, monitor_interval_seconds=None,
    )
    store.update_goal_id(study.study_id, goal.goal_id)
    from strategy_research.core.study.bootstrap import init_study_dir as _init_study_dir
    _init_study_dir(env, study.study_id, "demo", "x")

    collector = _Collector()
    runner = AutoresearchRunner(study, store, control=ControlToken(), emitter=collector)

    async def main():
        reason = await runner.run()
        assert reason == ShutdownReason.TARGETS_MET
        started = next(
            (d for s, e, d in collector.events if e == "study_started"), None
        )
        assert started is not None
        assert started["trace_id"], "study_started must carry trace_id"
        rounds = [d for s, e, d in collector.events if e == "study_round"]
        assert len(rounds) >= 1
        for d in rounds:
            assert d["trace_id"] == started["trace_id"], (
                "trace_id must be stable across rounds (study-scoped)"
            )
            assert d["study_id"] == study.study_id
            assert d["round_num"] is not None
            assert d["round"] == d["round_num"]

    asyncio.run(main())
