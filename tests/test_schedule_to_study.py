"""Scheduled-research × study 全链路测试。

Covers:
- dispatch 桥：job → bootstrap.create_study_record → scheduler.submit →
  AutoresearchRunner 跑（stub 轮）→ 终态；job.last_run_id = study_id
- API server lifespan：启动定时守护（迁移 legacy JSON + executor 接线）
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from strategy_research.core.goal import GoalStore
from strategy_research.core.scheduled_research.executor import ScheduledResearchExecutor
from strategy_research.core.scheduled_research.models import JobStatus, ScheduledResearchJob
from strategy_research.core.scheduled_research.store import ScheduledResearchStore
from strategy_research.apps.study import StudyScheduler, StudyStatus, StudyStore
from strategy_research.apps.study import runner as runner_mod


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(
        "QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db")
    )
    monkeypatch.setenv(
        "QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json")
    )
    monkeypatch.setenv("SR_SESSIONS_DB", str(tmp_path / "sessions.db"))


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    base = tmp_path / "ws"
    strat_dir = base / "strategies" / "demo"
    strat_dir.mkdir(parents=True)
    (strat_dir / "strategy.py").write_text(
        "PARAMS = {}\nFACTOR_EXPRS = []\nFACTOR_WEIGHT_METHOD = 'equal'\n",
        encoding="utf-8",
    )
    (base / "acceptance.yaml").write_text("llm_enabled: false\n", encoding="utf-8")
    return base


class _FakeBus:
    def emit(self, sid, event, data):
        pass


class FakeSessionService:
    def __init__(self):
        self._processing: set[str] = set()
        self.event_bus = _FakeBus()

    def is_session_processing(self, session_id):
        return session_id in self._processing

    def mark_session_processing(self, session_id, *, processing):
        if processing:
            self._processing.add(session_id)
        else:
            self._processing.discard(session_id)


def _stub_round(monkeypatch):
    """Fast discard-round stub: study never meets targets, stops at max_rounds."""

    def _round(self, r, prev, directives_text=None):
        return {
            "round": r, "run_name": f"run_{r:04d}", "run_dir": Path("/tmp/fake"),
            "metrics": {"calmar": 0.1, "sharpe": 0.0, "max_dd": -0.2},
            "verdict": "discard",
            "decision": {"stagnation_triggered": False, "reason": "",
                         "to_dict": lambda: {"stagnation_triggered": False}},
            "agent_outputs": {k: {"ok": True} for k in (
                "researcher", "data_quality", "factor_analyst", "strategist",
                "portfolio_construction", "risk_controller",
                "attribution_analyst", "anti_overfit_analyst",
                "backtest_diagnostics")},
            "summary": {"round": r, "agent_statuses": {},
                        "performance_change": None,
                        "acceptance_decision": {"stagnation_triggered": False}},
            "backtest_error": None,
        }

    monkeypatch.setattr(runner_mod.AutoresearchRunner, "_run_one_round", _round)
    monkeypatch.setattr(
        runner_mod.AutoresearchRunner, "_round_cooldown", lambda self: 0.0,
    )
    monkeypatch.setattr(
        runner_mod.AutoresearchRunner, "_maybe_load_previous_summary",
        lambda self, study: None,
    )


async def _await_study_terminal(store, study_id, *, timeout_steps=400, step=0.01):
    terminal = {
        s for s in StudyStatus
        if s.value in ("complete", "error", "cancelled", "budget_limited",
                       "early_stopped", "monitoring")
    }
    for _ in range(timeout_steps):
        await asyncio.sleep(step)
        cur = store.get_study(study_id)
        if cur and cur.execution_status in terminal:
            return cur
    return store.get_study(study_id)


@pytest.mark.asyncio
async def test_dispatch_creates_and_runs_study(tmp_path, ws, monkeypatch):
    """job 到点 → study 创建 → scheduler 执行（stub）→ 终态 + last_run_id."""
    _stub_round(monkeypatch)

    db = tmp_path / "goals.db"
    sched_store = ScheduledResearchStore(path=db)
    study_store = StudyStore(db_path=db)
    scheduler = StudyScheduler(study_store, session_service=FakeSessionService())
    executor = ScheduledResearchExecutor(sched_store, scheduler=scheduler)

    job = ScheduledResearchJob(
        id="e2e_job", workspace=str(ws), strategy_name="demo",
        prompt="研究动量因子", cron="0 2 * * *",
        next_run_at=time.time() - 1, max_rounds=1, target="study",
        owner_session_id="sess-x",
    )
    sched_store.add(job)

    await executor._tick()  # 到点 dispatch → create + submit

    job_after = sched_store.get("e2e_job")
    # cron 周期性 job：dispatch 成功后重置为 PENDING 并重排下次触发
    assert job_after.status == JobStatus.PENDING
    assert job_after.last_run_id.startswith("study_")
    assert job_after.next_run_at > time.time() - 1
    study_id = job_after.last_run_id

    # study 应在 scheduler 中执行至终态（max_rounds=1 → complete 或 early_stopped）
    final = await _await_study_terminal(study_store, study_id)
    assert final is not None
    assert final.execution_status in (
        StudyStatus.COMPLETE, StudyStatus.EARLY_STOPPED, StudyStatus.ERROR,
    )
    # objective 映射自 job.prompt
    assert final.objective == "研究动量因子"

    # goal 账本在 study 隔离域下建立
    goal = GoalStore().get_goal(final.goal_id)
    assert goal is not None
    assert goal.session_id == study_id

    await scheduler.shutdown()


@pytest.mark.asyncio
async def test_dispatch_with_config_params(tmp_path, ws, monkeypatch):
    """config 透传：metric_targets/budget/monitor → study 记录."""
    _stub_round(monkeypatch)

    db = tmp_path / "goals.db"
    sched_store = ScheduledResearchStore(path=db)
    study_store = StudyStore(db_path=db)
    scheduler = StudyScheduler(study_store, session_service=FakeSessionService())
    executor = ScheduledResearchExecutor(sched_store, scheduler=scheduler)

    job = ScheduledResearchJob(
        id="cfg_job", workspace=str(ws), strategy_name="demo",
        prompt="动量+低波", interval_ms=60_000,
        next_run_at=time.time() - 1, max_rounds=1, target="study",
        config={
            "metric_targets": [{"name": "calmar", "op": ">=", "value": 1.0}],
            "budget_turn": 5,
            "monitor_interval_seconds": 600,
        },
    )
    sched_store.add(job)

    await executor._tick()

    job_after = sched_store.get("cfg_job")
    study_id = job_after.last_run_id
    final = await _await_study_terminal(study_store, study_id)
    assert final is not None
    assert final.metric_targets == [{"name": "calmar", "op": ">=", "value": 1.0}]
    assert final.budget_turn == 5
    assert final.monitor_interval_seconds == 600
    assert job_after.next_run_at > time.time()  # interval 已重排

    await scheduler.shutdown()


def test_app_lifespan_starts_daemon_and_migrates(tmp_path, monkeypatch, ws):
    """create_app lifespan：迁移 legacy JSON + 启动定时守护（接线验证）."""
    from unittest.mock import patch

    legacy = tmp_path / "scheduled_jobs.json"
    legacy.write_text(json.dumps({
        "schema_version": 1,
        "jobs": [{
            "id": "legacy_1", "workspace": str(ws), "strategy_name": "demo",
            "prompt": "旧任务", "cron": "0 2 * * *", "next_run_at": 1.0,
            "created_at": 1.0, "status": "pending", "max_rounds": 1,
        }],
    }), encoding="utf-8")

    # 指向 legacy JSON：monkeypatch 模块级常量（模块已 import）
    import strategy_research.core.scheduled_research.store as sched_store_mod
    monkeypatch.setattr(sched_store_mod, "LEGACY_JSON_PATH", legacy)

    from fastapi.testclient import TestClient

    from strategy_research.api.app import create_app

    started = {}

    def _fake_start(self, loop=None):
        started["executor"] = True
        return _FakeBus() if False else None  # noqa: F841

    with patch.object(ScheduledResearchExecutor, "start", _fake_start):
        app = create_app(workspace_path=ws, goal_db_path=tmp_path / "goals.db")
        with TestClient(app):
            pass  # lifespan 进出

    assert started["executor"] is True, "lifespan 未启动定时守护"
    assert not legacy.exists(), "legacy JSON 应被 rename"
    assert Path(f"{legacy}.migrated").exists()
    store = ScheduledResearchStore(path=tmp_path / "goals.db")
    jobs = store.load()
    store.close()
    assert len(jobs) == 1
    assert jobs[0].id == "legacy_1"
    assert jobs[0].target == "study"
