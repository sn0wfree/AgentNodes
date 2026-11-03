"""Study v2 M1 tests — micro session binding + true parallel scheduling.

Covers:
- store: owner_session_id default/kept, update_session_id rebind, legacy
  migration (ALTER + backfill)
- scheduler: two studies run in parallel (create_task dispatch); global
  semaphore caps concurrency (SR_STUDY_MAX_CONCURRENT)
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from strategy_research.core.goal import GoalStore
from strategy_research.core.goal.context import default_goal_criteria
from strategy_research.apps.study import (
    StudyScheduler,
    StudyStatus,
    StudyStore,
)
from strategy_research.apps.study import runner as runner_mod
from strategy_research.apps.study import scheduler as scheduler_mod


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(
        "QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db")
    )
    monkeypatch.setenv(
        "QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json")
    )


@pytest.fixture
def store(tmp_path: Path):
    return StudyStore(db_path=tmp_path / "goals.db")


@pytest.fixture
def goal_store():
    return GoalStore()


class _FakeBus:
    def emit(self, sid, event, data):
        pass


class FakeSessionService:
    def __init__(self):
        self._processing: set[str] = set()
        self.event_bus = _FakeBus()

    def is_session_processing(self, sid: str) -> bool:
        return sid in self._processing

    def mark_session_processing(self, sid: str, *, processing: bool) -> None:
        if processing:
            self._processing.add(sid)
        else:
            self._processing.discard(sid)


def _setup(store, goal_store, session="sess-st", **overrides):
    goal = goal_store.replace_goal(
        session_id=session,
        objective="研究动量",
        criteria=default_goal_criteria(),
    )
    kw = dict(
        owner_session_id=session, goal_id=goal.goal_id,
        objective="研究动量", workspace_path="/tmp/ws",
        strategy_name="rot_alpha", behavior="improving",
        metric_targets=[{"name": "calmar", "op": ">=", "value": 0.5}],
        cooldown_base=0.01, cooldown_jitter=0.01, min_cooldown=0.01,
    )
    kw.update(overrides)
    study = store.create_study(**kw)
    return goal, study


# ── store: owner_session_id / micro-session rebind ────────────────────


def test_create_study_single_identity(store, goal_store):
    """v2 single identity: session_id column == study_id; owner = chat."""
    goal, study = _setup(store, goal_store)
    assert study.session_id == study.study_id
    assert study.owner_session_id == "sess-st"
    reloaded = store.get_study(study.study_id)
    assert reloaded.session_id == study.study_id


def test_update_goal_id_links_goal(store, goal_store):
    """Timing sequence: create_study(goal_id=None) → replace_goal →
    update_goal_id links the ledger row."""
    goal, study = _setup(store, goal_store)
    assert study.goal_id == goal.goal_id  # _setup passes goal_id directly
    # standalone: goal_id=None then link later
    g2, s2 = _setup(store, goal_store, goal_id=None)
    assert s2.goal_id is None
    linked = store.update_goal_id(s2.study_id, "goal_later")
    assert linked is not None and linked.goal_id == "goal_later"
    assert store.get_study(s2.study_id).goal_id == "goal_later"


def test_goal_binds_study_id_domain(store, goal_store):
    """A study goal is created under the study_id domain (supersede=False):
    a second study under the same owner does NOT supersede the first."""
    from strategy_research.core.goal.context import default_goal_criteria

    g1, s1 = _setup(store, goal_store, session="chat_abc", goal_id=None)
    goal1 = goal_store.replace_goal(
        session_id=s1.study_id, objective="研究A",
        criteria=default_goal_criteria(), supersede=False,
    )
    store.update_goal_id(s1.study_id, goal1.goal_id)

    g2, s2 = _setup(store, goal_store, session="chat_abc", goal_id=None)
    goal2 = goal_store.replace_goal(
        session_id=s2.study_id, objective="研究B",
        criteria=default_goal_criteria(), supersede=False,
    )
    store.update_goal_id(s2.study_id, goal2.goal_id)

    # Both goals stay ACTIVE — no cross-study supersede, no unique-index hit
    assert goal_store.get_goal(goal1.goal_id).status.value == "active"
    assert goal_store.get_goal(goal2.goal_id).status.value == "active"


def test_chat_goal_supersede_semantics_kept(store, goal_store):
    """chat semantics untouched: same-session replace_goal (default
    supersede=True) still invalidates the previous active goal."""
    from strategy_research.core.goal.context import default_goal_criteria

    g1 = goal_store.replace_goal(
        session_id="chat_abc", objective="旧目标",
        criteria=default_goal_criteria(),
    )
    g2 = goal_store.replace_goal(
        session_id="chat_abc", objective="新目标",
        criteria=default_goal_criteria(),
    )
    assert goal_store.get_goal(g1.goal_id).status.value == "superseded"
    assert goal_store.get_goal(g2.goal_id).status.value == "active"


def test_legacy_db_migration_backfills_owner(tmp_path):
    """A pre-v2 DB (no owner_session_id column) gains the column + backfill."""
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE studies (
            study_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            goal_id TEXT, objective TEXT NOT NULL,
            executor_type TEXT NOT NULL DEFAULT 'autoresearch',
            workspace_path TEXT NOT NULL, strategy_name TEXT NOT NULL,
            metric_targets TEXT, budget_token INTEGER, budget_turn INTEGER,
            budget_time_seconds INTEGER, cooldown_base REAL NOT NULL DEFAULT 30.0,
            cooldown_jitter REAL NOT NULL DEFAULT 10.0,
            min_cooldown REAL NOT NULL DEFAULT 1.0, max_rounds INTEGER,
            lazy_detection_interval INTEGER NOT NULL DEFAULT 10,
            keep_recent INTEGER NOT NULL DEFAULT 10, behavior TEXT,
            execution_status TEXT NOT NULL DEFAULT 'queued',
            current_round INTEGER NOT NULL DEFAULT 0,
            last_metrics TEXT, last_verdict TEXT, last_error TEXT,
            heartbeat TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            completed_at TEXT, monitor_interval_seconds INTEGER,
            last_monitor_check_at TEXT,
            monitor_drift_count INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        "INSERT INTO studies (study_id, session_id, objective, workspace_path, "
        "strategy_name, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("study-legacy", "chat-sess", "目标", "/ws", "s1", "t1", "t1"),
    )
    conn.commit()
    conn.close()

    store = StudyStore(db_path=db)
    row = store.get_study("study-legacy")
    assert row is not None
    assert row.session_id == "chat-sess"
    # backfilled from session_id
    assert row.owner_session_id == "chat-sess"


# ── v2 single identity: owner-session queries + study_id accounting ────


def test_get_active_study_matches_owner_not_study_id(store, goal_store):
    """'My active study' lookups use the owner chat session; the study's
    own session_id (== study_id) is the event channel, not a query key."""
    goal, study = _setup(store, goal_store, session="chat_abc")

    by_owner = store.get_active_study("chat_abc")
    assert by_owner is not None and by_owner.study_id == study.study_id
    # The study_id as a session key does not answer "my studies" queries.
    assert store.get_active_study(study.study_id) is None


def test_list_studies_matches_owner(store, goal_store):
    goal, study = _setup(store, goal_store, session="chat_abc")
    rows = store.list_studies(session_id="chat_abc")
    assert [r.study_id for r in rows] == [study.study_id]
    assert store.list_studies(session_id=study.study_id) == []


def test_study_id_goal_accounting_passes(store, goal_store):
    """Decision D: a study writing to its goal ledger with its own
    session_id (== study_id) succeeds; evidence persists under the goal's
    own session (the study's domain)."""
    from strategy_research.core.goal import EvidenceInput
    from strategy_research.core.goal.context import default_goal_criteria

    goal, study = _setup(store, goal_store, session="chat_abc")
    study_id = study.study_id
    # Build the goal under the study's own domain like study_start does.
    goal2 = goal_store.replace_goal(
        session_id=study_id, objective="研究动量",
        criteria=default_goal_criteria(), supersede=False,
    )
    store.update_goal_id(study_id, goal2.goal_id)

    for c in goal_store.list_criteria(goal2.goal_id):
        if not c.required:
            continue
        goal_store.append_evidence(
            session_id=study_id,               # study's own identity writes
            goal_id=goal2.goal_id,
            expected_goal_id=goal2.goal_id,
            evidence=EvidenceInput(
                text="达标", criterion_id=c.criterion_id,
                evidence_type="acceptance", run_id="run_0001",
                source_provider="study", source_type="metric_targets_met",
            ),
        )
    goal_store.complete_lite(
        session_id=study_id,
        goal_id=goal2.goal_id,
        expected_goal_id=goal2.goal_id,
        recap="研究达标",
    )
    completed = goal_store.get_goal(goal2.goal_id)
    assert completed.status.value == "complete"
    for ev in goal_store.list_evidence(goal2.goal_id):
        assert ev.session_id == study_id  # persisted under the goal's session


# ── scheduler: true parallelism + semaphore cap ───────────────────────


def _patch_slow_round(monkeypatch, entered: list, sleep: float, *, rounds_max: int = 2):
    """Stub runner round with a slow (sleep) implementation that never
    meets targets, so studies need a bounded number of rounds."""

    def _round(self, r, prev, directives_text=None):
        with _LOCK:
            entered.append((self.study.study_id, time.monotonic()))
        time.sleep(sleep)
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
            "summary": {"round": r, "agent_statuses": {}, "performance_change": None,
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


_LOCK = threading.Lock()


async def _await_status(store, study_id, target, *, timeout_steps=400, step=0.01):
    last = None
    for _ in range(timeout_steps):
        await asyncio.sleep(step)
        cur = store.get_study(study_id)
        last = cur
        if cur and cur.execution_status == target:
            return cur
    return last


def test_two_studies_run_in_parallel(store, goal_store, monkeypatch):
    entered: list = []
    _patch_slow_round(monkeypatch, entered, sleep=0.15)
    g1, s1 = _setup(store, goal_store, session="sess-a", max_rounds=2)
    g2, s2 = _setup(store, goal_store, session="sess-b", max_rounds=2)
    svc = FakeSessionService()
    sched = StudyScheduler(store, session_service=svc)

    async def main():
        await sched.submit(s1)
        await sched.submit(s2)
        await _await_status(store, s1.study_id, StudyStatus.ERROR)
        await _await_status(store, s2.study_id, StudyStatus.ERROR)
        await sched.shutdown()

    asyncio.run(main())
    ids = [sid for sid, _ in entered]
    assert set(ids) == {s1.study_id, s2.study_id}
    # Both studies entered their first round within a window much smaller
    # than the serial wall-time (2 rounds x 0.15s each) — proof of overlap.
    t1 = entered[0][1]
    t2 = next(t for sid, t in entered if sid != entered[0][0])
    assert abs(t1 - t2) < 0.05, f"first rounds not concurrent: {entered}"


def test_semaphore_caps_concurrency(store, goal_store, monkeypatch):
    entered: list = []
    _patch_slow_round(monkeypatch, entered, sleep=0.1)
    monkeypatch.setattr(scheduler_mod, "SR_STUDY_MAX_CONCURRENT", 1)
    g1, s1 = _setup(store, goal_store, session="sess-a", max_rounds=2)
    g2, s2 = _setup(store, goal_store, session="sess-b", max_rounds=2)
    svc = FakeSessionService()
    sched = StudyScheduler(store, session_service=svc)

    async def main():
        await sched.submit(s1)
        await sched.submit(s2)
        await _await_status(store, s1.study_id, StudyStatus.ERROR)
        await _await_status(store, s2.study_id, StudyStatus.ERROR)
        await sched.shutdown()

    asyncio.run(main())
    # Second study's first round must start strictly after the first
    # study's rounds finished (cap=1 → strictly serial).
    t_first = entered[0][1]
    t_second_first = next(t for sid, t in entered if sid != entered[0][0])
    assert t_second_first >= t_first + 0.15, f"not serial under cap=1: {entered}"
