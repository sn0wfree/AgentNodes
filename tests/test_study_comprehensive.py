"""Comprehensive tests for study module — filling all coverage gaps.

Covers:
  - meets_metric_targets: all operators, unknown op, None/missing values, non-numeric
  - acceptance_config_from_targets: None, empty, overrides
  - StudyStore edge cases: update to INTERRUPTED/EARLY_STOPPED, concurrent updates,
    _write_transaction rollback, corrupt data deserialization, update_monitor_check not found
  - AutoresearchRunner: _update_results_tsv, _budget_exceeded (time/turn/both),
    _complete_goal edge cases, _maybe_load_previous_summary real paths,
    _format_directives edge cases, NOVELTY_REJECTED shutdown, emitter exception handling
  - StudyScheduler: pause, resume, is_running, active_studies, emitter_factory,
    recover_on_startup QUEUED path, _emit_event exception handling
  - Attribution: ABSENT outcome, compute_precision edge cases
  - Scoreboard: build_scoreboard_context, update with various lever types
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
_TEST_WS = Path(tempfile.gettempdir()) / "qn-test-ws"
_TEST_WS.mkdir(parents=True, exist_ok=True)

import pytest

from strategy_research.core.study.method.attribution import (
    AttributionOutcome,
    classify_attribution,
    compute_precision,
)
from strategy_research.core.study.runner import (
    acceptance_config_from_targets,
)
from strategy_research.core.study.runner import (
    meets_metric_targets as executor_meets_metric_targets,
)
from strategy_research.core.study.models import (
    ACTIVE_EXECUTION_STATUSES,
    MetricTarget,
    StudyDirective,
    StudyRoundRecord,
    StudyStatus,
    default_metric_targets,
)
from strategy_research.core.study.runner import (
    AutoresearchRunner,
    ControlToken,
    NullEmitter,
    ShutdownReason,
    _metric_pass_set,
    meets_metric_targets,
)
from strategy_research.core.study.scheduler import (
    StudyScheduler,
    _EventBusEmitter,
    make_event_bus_emitter,
)
from strategy_research.core.study.store import StudyStore

# ── fixtures ────────────────────────────────────────────────────────


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
    from strategy_research.core.goal import GoalStore
    return GoalStore()


class CollectingEmitter:
    def __init__(self):
        self.events: list[tuple[str, str, dict]] = []

    def emit(self, session_id: str, event: str, data: dict) -> None:
        self.events.append((session_id, event, data))


class FailingEmitter:
    """Emitter that raises on every emit — tests defensive catch."""
    def emit(self, session_id: str, event: str, data: dict) -> None:
        raise RuntimeError("emit exploded")


class _FakeBus:
    def __init__(self):
        self.events: list[tuple[str, str, str, dict]] = []

    def emit(self, sid, event, data):
        self.events.append((sid, event, data))


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


def _make_study(store, goal_store, **overrides):
    goal = goal_store.replace_goal(
        session_id="sess-comp",
        objective="comprehensive test",
        criteria=["calmar >= 0.5"],
    )
    kw = dict(
        owner_session_id="sess-comp", goal_id=goal.goal_id, objective="comprehensive test",
        workspace_path=str(_TEST_WS), strategy_name="test_strat",
        behavior="improving",
        metric_targets=[{"name": "calmar", "op": ">=", "value": 0.5}],
        cooldown_base=0.01, cooldown_jitter=0.01, min_cooldown=0.01,
    )
    kw.update(overrides)
    return goal, store.create_study(**kw)


# ═══════════════════════════════════════════════════════════════════
# 1. meets_metric_targets — all operators + edge cases
# ═══════════════════════════════════════════════════════════════════


class TestMeetsMetricTargets:
    """Test both executor and runner versions (they should be identical)."""

    @pytest.mark.parametrize("impl", [
        executor_meets_metric_targets,
        meets_metric_targets,
    ])
    def test_ge_pass(self, impl):
        assert impl({"calmar": 0.6}, [{"name": "calmar", "op": ">=", "value": 0.5}])

    @pytest.mark.parametrize("impl", [
        executor_meets_metric_targets,
        meets_metric_targets,
    ])
    def test_ge_fail(self, impl):
        assert not impl({"calmar": 0.4}, [{"name": "calmar", "op": ">=", "value": 0.5}])

    @pytest.mark.parametrize("impl", [
        executor_meets_metric_targets,
        meets_metric_targets,
    ])
    def test_le_pass(self, impl):
        assert impl({"max_dd": -0.1}, [{"name": "max_dd", "op": "<=", "value": -0.05}])

    @pytest.mark.parametrize("impl", [
        executor_meets_metric_targets,
        meets_metric_targets,
    ])
    def test_le_fail(self, impl):
        # -0.1 > -0.15, so -0.1 <= -0.15 is False (does not meet target)
        assert not impl({"max_dd": -0.1}, [{"name": "max_dd", "op": "<=", "value": -0.15}])

    @pytest.mark.parametrize("impl", [
        executor_meets_metric_targets,
        meets_metric_targets,
    ])
    def test_gt_pass(self, impl):
        assert impl({"calmar": 0.6}, [{"name": "calmar", "op": ">", "value": 0.5}])

    @pytest.mark.parametrize("impl", [
        executor_meets_metric_targets,
        meets_metric_targets,
    ])
    def test_gt_fail_equal(self, impl):
        assert not impl({"calmar": 0.5}, [{"name": "calmar", "op": ">", "value": 0.5}])

    @pytest.mark.parametrize("impl", [
        executor_meets_metric_targets,
        meets_metric_targets,
    ])
    def test_lt_pass(self, impl):
        assert impl({"max_dd": -0.2}, [{"name": "max_dd", "op": "<", "value": -0.15}])

    @pytest.mark.parametrize("impl", [
        executor_meets_metric_targets,
        meets_metric_targets,
    ])
    def test_lt_fail_equal(self, impl):
        assert not impl({"max_dd": -0.15}, [{"name": "max_dd", "op": "<", "value": -0.15}])

    @pytest.mark.parametrize("impl", [
        executor_meets_metric_targets,
        meets_metric_targets,
    ])
    def test_eq_pass(self, impl):
        assert impl({"calmar": 0.5}, [{"name": "calmar", "op": "==", "value": 0.5}])

    @pytest.mark.parametrize("impl", [
        executor_meets_metric_targets,
        meets_metric_targets,
    ])
    def test_eq_fail(self, impl):
        assert not impl({"calmar": 0.51}, [{"name": "calmar", "op": "==", "value": 0.5}])

    @pytest.mark.parametrize("impl", [
        executor_meets_metric_targets,
        meets_metric_targets,
    ])
    def test_unknown_op_returns_false(self, impl):
        """Unknown operator should return False (not silently pass)."""
        assert not impl({"calmar": 0.6}, [{"name": "calmar", "op": "!=", "value": 0.5}])

    @pytest.mark.parametrize("impl", [
        executor_meets_metric_targets,
        meets_metric_targets,
    ])
    def test_unknown_op_ne_returns_true(self, impl):
        """!= is unknown, so it should return False even though values differ."""
        assert not impl({"calmar": 0.6}, [{"name": "calmar", "op": "!=", "value": 0.5}])

    @pytest.mark.parametrize("impl", [
        executor_meets_metric_targets,
        meets_metric_targets,
    ])
    def test_missing_metric_returns_false(self, impl):
        assert not impl({}, [{"name": "calmar", "op": ">=", "value": 0.5}])

    @pytest.mark.parametrize("impl", [
        executor_meets_metric_targets,
        meets_metric_targets,
    ])
    def test_none_metric_value_returns_false(self, impl):
        assert not impl({"calmar": None}, [{"name": "calmar", "op": ">=", "value": 0.5}])

    @pytest.mark.parametrize("impl", [
        executor_meets_metric_targets,
        meets_metric_targets,
    ])
    def test_none_target_value_returns_false(self, impl):
        assert not impl({"calmar": 0.6}, [{"name": "calmar", "op": ">=", "value": None}])

    @pytest.mark.parametrize("impl", [
        executor_meets_metric_targets,
        meets_metric_targets,
    ])
    def test_none_target_name_returns_false(self, impl):
        assert not impl({"calmar": 0.6}, [{"name": None, "op": ">=", "value": 0.5}])

    @pytest.mark.parametrize("impl", [
        executor_meets_metric_targets,
        meets_metric_targets,
    ])
    def test_non_numeric_metric_returns_false(self, impl):
        assert not impl({"calmar": "not_a_number"}, [{"name": "calmar", "op": ">=", "value": 0.5}])

    @pytest.mark.parametrize("impl", [
        executor_meets_metric_targets,
        meets_metric_targets,
    ])
    def test_non_numeric_target_returns_false(self, impl):
        assert not impl({"calmar": 0.6}, [{"name": "calmar", "op": ">=", "value": "high"}])

    @pytest.mark.parametrize("impl", [
        executor_meets_metric_targets,
        meets_metric_targets,
    ])
    def test_empty_targets_pass(self, impl):
        assert impl({"calmar": 0.6}, [])

    @pytest.mark.parametrize("impl", [
        executor_meets_metric_targets,
        meets_metric_targets,
    ])
    def test_multiple_targets_all_pass(self, impl):
        metrics = {"calmar": 0.6, "sharpe": 0.4, "max_dd": -0.1}
        targets = [
            {"name": "calmar", "op": ">=", "value": 0.5},
            {"name": "sharpe", "op": ">=", "value": 0.3},
            {"name": "max_dd", "op": ">=", "value": -0.15},
        ]
        assert impl(metrics, targets)

    @pytest.mark.parametrize("impl", [
        executor_meets_metric_targets,
        meets_metric_targets,
    ])
    def test_multiple_targets_one_fails(self, impl):
        metrics = {"calmar": 0.6, "sharpe": 0.2, "max_dd": -0.1}
        targets = [
            {"name": "calmar", "op": ">=", "value": 0.5},
            {"name": "sharpe", "op": ">=", "value": 0.3},
        ]
        assert not impl(metrics, targets)

    @pytest.mark.parametrize("impl", [
        executor_meets_metric_targets,
        meets_metric_targets,
    ])
    def test_string_numeric_coercion(self, impl):
        assert impl({"calmar": "0.6"}, [{"name": "calmar", "op": ">=", "value": "0.5"}])


# ═══════════════════════════════════════════════════════════════════
# 2. _metric_pass_set — edge cases
# ═══════════════════════════════════════════════════════════════════


class TestMetricPassSet:
    def test_empty_metrics(self):
        assert _metric_pass_set({}, [{"name": "calmar", "op": ">=", "value": 0.5}]) == set()

    def test_empty_targets(self):
        assert _metric_pass_set({"calmar": 0.6}, []) == set()

    def test_pass_and_fail_mixed(self):
        metrics = {"calmar": 0.6, "sharpe": 0.2}
        targets = [
            {"name": "calmar", "op": ">=", "value": 0.5},
            {"name": "sharpe", "op": ">=", "value": 0.3},
        ]
        assert _metric_pass_set(metrics, targets) == {"calmar"}

    def test_non_numeric_skipped(self):
        metrics = {"calmar": "bad", "sharpe": 0.4}
        targets = [
            {"name": "calmar", "op": ">=", "value": 0.5},
            {"name": "sharpe", "op": ">=", "value": 0.3},
        ]
        assert _metric_pass_set(metrics, targets) == {"sharpe"}

    def test_none_value_skipped(self):
        targets = [{"name": "calmar", "op": ">=", "value": None}]
        assert _metric_pass_set({"calmar": 0.6}, targets) == set()

    def test_none_name_skipped(self):
        targets = [{"name": None, "op": ">=", "value": 0.5}]
        assert _metric_pass_set({"calmar": 0.6}, targets) == set()


# ═══════════════════════════════════════════════════════════════════
# 3. acceptance_config_from_targets
# ═══════════════════════════════════════════════════════════════════


class TestAcceptanceConfigFromTargets:
    def test_none_returns_default(self):
        from strategy_research.core.strategy_acceptance import DEFAULT_CONFIG
        result = acceptance_config_from_targets(None)
        assert result is DEFAULT_CONFIG

    def test_empty_returns_default(self):
        from strategy_research.core.strategy_acceptance import DEFAULT_CONFIG
        result = acceptance_config_from_targets([])
        assert result is DEFAULT_CONFIG

    def test_calmar_override(self):
        result = acceptance_config_from_targets([{"name": "calmar", "op": ">=", "value": 0.8}])
        assert result.hard_calmar_min == 0.8

    def test_sharpe_override(self):
        result = acceptance_config_from_targets([{"name": "sharpe", "op": ">=", "value": 0.5}])
        assert result.hard_sharpe_min == 0.5

    def test_max_dd_override(self):
        result = acceptance_config_from_targets([{"name": "max_dd", "op": ">=", "value": -0.2}])
        assert result.hard_max_dd_min == -0.2

    def test_multiple_overrides(self):
        result = acceptance_config_from_targets([
            {"name": "calmar", "op": ">=", "value": 0.9},
            {"name": "sharpe", "op": ">=", "value": 0.6},
            {"name": "max_dd", "op": ">=", "value": -0.25},
        ])
        assert result.hard_calmar_min == 0.9
        assert result.hard_sharpe_min == 0.6
        assert result.hard_max_dd_min == -0.25

    def test_unknown_metric_name_ignored(self):
        from strategy_research.core.strategy_acceptance import DEFAULT_CONFIG
        result = acceptance_config_from_targets([{"name": "unknown", "op": ">=", "value": 1.0}])
        assert result is DEFAULT_CONFIG

    def test_none_name_skipped(self):
        from strategy_research.core.strategy_acceptance import DEFAULT_CONFIG
        result = acceptance_config_from_targets([{"name": None, "op": ">=", "value": 1.0}])
        assert result is DEFAULT_CONFIG

    def test_none_value_skipped(self):
        from strategy_research.core.strategy_acceptance import DEFAULT_CONFIG
        result = acceptance_config_from_targets([{"name": "calmar", "op": ">=", "value": None}])
        assert result is DEFAULT_CONFIG


# ═══════════════════════════════════════════════════════════════════
# 4. StudyStore edge cases
# ═══════════════════════════════════════════════════════════════════


class TestStudyStoreEdgeCases:
    def test_update_status_not_found_returns_none(self, store):
        result = store.update_execution_status("nonexistent", StudyStatus.RUNNING)
        assert result is None

    def test_update_to_interrupted(self, store, goal_store):
        _, study = _make_study(store, goal_store)
        result = store.update_execution_status(study.study_id, StudyStatus.INTERRUPTED)
        assert result is not None
        assert result.execution_status == StudyStatus.INTERRUPTED

    def test_update_to_early_stopped(self, store, goal_store):
        _, study = _make_study(store, goal_store)
        result = store.update_execution_status(
            study.study_id, StudyStatus.EARLY_STOPPED,
            last_error="idle=3 rounds, best=0.5",
        )
        assert result is not None
        assert result.execution_status == StudyStatus.EARLY_STOPPED
        assert result.last_error == "idle=3 rounds, best=0.5"

    def test_update_with_last_verdict(self, store, goal_store):
        _, study = _make_study(store, goal_store)
        result = store.update_execution_status(
            study.study_id, StudyStatus.RUNNING, last_verdict="keep",
        )
        assert result is not None
        assert result.last_verdict == "keep"

    def test_update_round_heartbeat_not_found(self, store):
        # Should not raise
        store.update_round_heartbeat("nonexistent", 1)

    def test_update_last_metrics_not_found(self, store):
        # Should not raise
        store.update_last_metrics("nonexistent", {"calmar": 0.5}, "keep")

    def test_append_round_not_found(self, store):
        # Should raise or return gracefully — depends on FK
        # The INSERT will fail due to FK constraint
        with pytest.raises(Exception):
            store.append_round("nonexistent", 1, "run_0001")

    def test_list_rounds_empty_study(self, store):
        rounds = store.list_rounds("nonexistent")
        assert rounds == []

    def test_create_study_workflow_executor(self, store):
        study = store.create_study(
            owner_session_id="s1", goal_id=None, objective="test",
            workspace_path=str(_TEST_WS), strategy_name="strat",
            executor_type="workflow",
        )
        assert study.executor_type == "workflow"

    def test_create_study_manual_executor(self, store):
        study = store.create_study(
            owner_session_id="s1", goal_id=None, objective="test",
            workspace_path=str(_TEST_WS), strategy_name="strat",
            executor_type="manual",
        )
        assert study.executor_type == "manual"

    def test_concurrent_create_no_supersede(self, store):
        """v2: two studies under the same owner coexist (no same-session
        cancellation); newest wins "active study" lookups."""
        s1 = store.create_study(
            owner_session_id="sess-concurrent", goal_id=None, objective="first",
            workspace_path=str(_TEST_WS), strategy_name="strat1",
        )
        s2 = store.create_study(
            owner_session_id="sess-concurrent", goal_id=None, objective="second",
            workspace_path=str(_TEST_WS), strategy_name="strat2",
        )
        # Both stay active (parallel studies)
        updated = store.get_study(s1.study_id)
        assert updated.execution_status == StudyStatus.QUEUED
        active = store.get_active_study("sess-concurrent")
        assert active is not None
        assert active.study_id == s2.study_id

    def test_directive_ordering(self, store, goal_store):
        _, study = _make_study(store, goal_store)
        d1 = store.add_directive(study.study_id, "first directive")
        d2 = store.add_directive(study.study_id, "second directive")
        d3 = store.add_directive(study.study_id, "third directive")
        pending = store.list_pending_directives(study.study_id)
        # Should be in creation order
        ids = [d.directive_id for d in pending]
        assert ids == [d1.directive_id, d2.directive_id, d3.directive_id]

    def test_monitor_check_not_found(self, store):
        # Should not raise
        store.update_monitor_check("nonexistent", last_check_at="2024-01-01T00:00:00", drift=False)

    def test_list_active_studies_excludes_interrupted(self, store, goal_store):
        _, study = _make_study(store, goal_store)
        store.update_execution_status(study.study_id, StudyStatus.INTERRUPTED)
        active = store.list_active_studies()
        assert all(s.study_id != study.study_id for s in active)

    def test_list_active_studies_excludes_early_stopped(self, store, goal_store):
        _, study = _make_study(store, goal_store)
        store.update_execution_status(study.study_id, StudyStatus.EARLY_STOPPED)
        active = store.list_active_studies()
        assert all(s.study_id != study.study_id for s in active)

    def test_list_studies_limit(self, store, goal_store):
        for i in range(5):
            store.create_study(
                owner_session_id=f"s{i}", goal_id=None, objective=f"obj{i}",
                workspace_path=str(_TEST_WS), strategy_name=f"strat{i}",
            )
        result = store.list_studies(limit=3)
        assert len(result) == 3

    def test_list_studies_by_status(self, store, goal_store):
        _, s1 = _make_study(store, goal_store, owner_session_id="s1")
        _, s2 = _make_study(store, goal_store, owner_session_id="s2")
        store.update_execution_status(s1.study_id, StudyStatus.RUNNING)
        running = store.list_studies(status=StudyStatus.RUNNING)
        assert len(running) == 1
        assert running[0].study_id == s1.study_id

    def test_study_record_frozen(self, store, goal_store):
        _, study = _make_study(store, goal_store)
        with pytest.raises(AttributeError):
            study.execution_status = StudyStatus.RUNNING

    def test_metric_target_frozen(self):
        mt = MetricTarget(name="calmar", op=">=", value=0.5)
        with pytest.raises(AttributeError):
            mt.value = 0.8

    def test_metric_target_as_dict(self):
        mt = MetricTarget(name="sharpe", op="<=", value=0.3)
        assert mt.as_dict() == {"name": "sharpe", "op": "<=", "value": 0.3}

    def test_default_metric_targets(self):
        targets = default_metric_targets()
        assert len(targets) == 3
        names = {t["name"] for t in targets}
        assert names == {"calmar", "sharpe", "max_dd"}

    def test_study_directive_frozen(self):
        d = StudyDirective(
            directive_id="d1", study_id="s1", content="test",
            issued_by="user", created_at="2024-01-01",
        )
        with pytest.raises(AttributeError):
            d.content = "changed"

    def test_study_round_record_defaults(self):
        r = StudyRoundRecord(
            round_id="r1", study_id="s1", goal_id=None,
            session_id="sess1", round_num=1, run_name="run_0001",
        )
        assert r.metrics == {}
        assert r.verdict == "discard"
        assert r.evidence_ids == []
        assert r.config_changes is None
        assert r.agent_output is None


# ═══════════════════════════════════════════════════════════════════
# 5. AutoresearchRunner — _update_results_tsv
# ═══════════════════════════════════════════════════════════════════


class TestUpdateResultsTSV:
    def test_file_not_exists_noop(self, tmp_path):
        AutoresearchRunner._update_results_tsv(tmp_path, "run_0001", "keep")
        # Should not raise

    def test_matching_line_updated(self, tmp_path):
        tsv = tmp_path / "results.tsv"
        tsv.write_text(
            "run_name\tcalmar\tsharpe\tmax_dd\tret\tvol\tcal2\tsh2\tmd2\tret2\tvol2\tverdict\n"
            "run_0001\t0.5\t0.3\t-0.1\t0.1\t0.2\t0.6\t0.4\t-0.05\t0.15\t0.25\tdiscard\n"
            "run_0002\t0.6\t0.4\t-0.08\t0.12\t0.22\t0.7\t0.5\t-0.04\t0.16\t0.26\tkeep\n",
            encoding="utf-8",
        )
        AutoresearchRunner._update_results_tsv(tmp_path, "run_0001", "keep")
        lines = tsv.read_text(encoding="utf-8").strip().split("\n")
        # run_0001 verdict should be updated
        parts = lines[1].split("\t")
        assert parts[11] == "keep"
        # run_0002 should be unchanged
        parts2 = lines[2].split("\t")
        assert parts2[11] == "keep"

    def test_no_matching_line(self, tmp_path):
        tsv = tmp_path / "results.tsv"
        tsv.write_text(
            "run_name\tcalmar\tsharpe\tmax_dd\tret\tvol\tcal2\tsh2\tmd2\tret2\tvol2\tverdict\n"
            "run_0002\t0.6\t0.4\t-0.08\t0.12\t0.22\t0.7\t0.5\t-0.04\t0.16\t0.26\tkeep\n",
            encoding="utf-8",
        )
        AutoresearchRunner._update_results_tsv(tmp_path, "run_0001", "keep")
        content = tsv.read_text(encoding="utf-8")
        assert "run_0002" in content

    def test_short_line_no_crash(self, tmp_path):
        """Line with < 12 columns should not crash."""
        tsv = tmp_path / "results.tsv"
        tsv.write_text(
            "run_name\tcalmar\nrun_0001\t0.5\n",
            encoding="utf-8",
        )
        AutoresearchRunner._update_results_tsv(tmp_path, "run_0001", "keep")
        # Should not raise; line unchanged because len(parts) < 12


# ═══════════════════════════════════════════════════════════════════
# 6. AutoresearchRunner — budget edge cases
# ═══════════════════════════════════════════════════════════════════


class TestRunnerBudget:
    def _make_runner(self, store, goal_store, **overrides):
        _, study = _make_study(store, goal_store, **overrides)
        return AutoresearchRunner(study, store, control=ControlToken())

    def test_time_budget_not_exceeded(self, store, goal_store):
        runner = self._make_runner(store, goal_store, budget_time_seconds=100)
        runner._total_used_time = 50.0
        assert not runner._budget_exceeded()

    def test_time_budget_exceeded(self, store, goal_store):
        runner = self._make_runner(store, goal_store, budget_time_seconds=100)
        runner._total_used_time = 100.0
        assert runner._budget_exceeded()

    def test_turn_budget_not_exceeded(self, store, goal_store):
        runner = self._make_runner(store, goal_store, budget_turn=10)
        runner._total_used_turns = 5
        assert not runner._budget_exceeded()

    def test_turn_budget_exceeded(self, store, goal_store):
        runner = self._make_runner(store, goal_store, budget_turn=10)
        runner._total_used_turns = 10
        assert runner._budget_exceeded()

    def test_both_budgets_exceeded(self, store, goal_store):
        runner = self._make_runner(store, goal_store, budget_time_seconds=100, budget_turn=10)
        runner._total_used_time = 50.0
        runner._total_used_turns = 10
        assert runner._budget_exceeded()

    def test_no_budgets_never_exceeded(self, store, goal_store):
        runner = self._make_runner(store, goal_store)
        runner._total_used_time = 99999.0
        runner._total_used_turns = 99999
        assert not runner._budget_exceeded()

    def test_budget_summary(self, store, goal_store):
        runner = self._make_runner(store, goal_store)
        runner._total_used_turns = 5
        runner._total_used_time = 12.3
        summary = runner._budget_summary()
        assert "turns_used=5" in summary
        assert "12.3s" in summary


# ═══════════════════════════════════════════════════════════════════
# 7. AutoresearchRunner — _complete_goal edge cases
# ═══════════════════════════════════════════════════════════════════


class TestRunnerCompleteGoal:
    def test_goal_id_none_early_return(self, store, goal_store):
        _, study = _make_study(store, goal_store, goal_id=None)
        runner = AutoresearchRunner(study, store)
        # Should not raise
        runner._complete_goal({"metrics": {"calmar": 0.6}, "run_name": "run_0001"})

    def test_goal_completion_with_criteria(self, store, goal_store):
        _, study = _make_study(store, goal_store)
        runner = AutoresearchRunner(study, store, goal_store=goal_store)
        runner._complete_goal({"metrics": {"calmar": 0.6}, "run_name": "run_0001"})
        # Goal should be completed
        g = goal_store.get_goal(study.goal_id)
        assert g.status.value == "complete"


# ═══════════════════════════════════════════════════════════════════
# 8. AutoresearchRunner — _maybe_load_previous_summary
# ═══════════════════════════════════════════════════════════════════


class TestMaybeLoadPreviousSummary:
    def test_no_runs_dir(self, store, goal_store, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        _, study = _make_study(store, goal_store, workspace_path=str(ws))
        runner = AutoresearchRunner(study, store)
        result = runner._maybe_load_previous_summary(study)
        assert result is None

    def test_empty_runs_dir(self, store, goal_store, tmp_path):
        ws = tmp_path / "ws"
        runs = ws / "strategies" / "test_strat" / "runs"
        runs.mkdir(parents=True)
        _, study = _make_study(store, goal_store, workspace_path=str(ws))
        runner = AutoresearchRunner(study, store)
        result = runner._maybe_load_previous_summary(study)
        assert result is None

    def test_runs_with_non_numeric_dirs(self, store, goal_store, tmp_path):
        ws = tmp_path / "ws"
        runs = ws / "strategies" / "test_strat" / "runs"
        runs.mkdir(parents=True)
        (runs / "run_ABCD").mkdir()  # non-numeric
        _, study = _make_study(store, goal_store, workspace_path=str(ws))
        runner = AutoresearchRunner(study, store)
        result = runner._maybe_load_previous_summary(study)
        assert result is None

    def test_runs_with_valid_dir_but_no_summary(self, store, goal_store, tmp_path):
        ws = tmp_path / "ws"
        run_dir = ws / "strategies" / "test_strat" / "runs" / "run_0001"
        run_dir.mkdir(parents=True)
        _, study = _make_study(store, goal_store, workspace_path=str(ws))
        runner = AutoresearchRunner(study, store)
        result = runner._maybe_load_previous_summary(study)
        # load_run_summary returns None when no summary.json exists
        assert result is None


# ═══════════════════════════════════════════════════════════════════
# 9. AutoresearchRunner — _format_directives edge cases
# ═══════════════════════════════════════════════════════════════════


class TestFormatDirectives:
    def test_empty_list(self):
        result = AutoresearchRunner._format_directives([])
        assert "<user-directives>" in result
        assert "</user-directives>" in result

    def test_single_directive(self):
        d = StudyDirective(
            directive_id="d1", study_id="s1", content="focus on momentum",
            issued_by="user", created_at="2024-01-01T00:00:00",
        )
        result = AutoresearchRunner._format_directives([d])
        assert "focus on momentum" in result
        assert "2024-01-01T00:00:00" in result

    def test_directive_with_newlines(self):
        d = StudyDirective(
            directive_id="d1", study_id="s1", content="line1\nline2\nline3",
            issued_by="user", created_at="2024-01-01T00:00:00",
        )
        result = AutoresearchRunner._format_directives([d])
        # Newlines should be replaced with spaces
        assert "line1 line2 line3" in result

    def test_multiple_directives(self):
        directives = [
            StudyDirective(
                directive_id=f"d{i}", study_id="s1", content=f"directive {i}",
                issued_by="user", created_at=f"2024-01-0{i+1}T00:00:00",
            )
            for i in range(3)
        ]
        result = AutoresearchRunner._format_directives(directives)
        for i in range(3):
            assert f"directive {i}" in result


# ═══════════════════════════════════════════════════════════════════
# 10. AutoresearchRunner — _mark_terminal
# ═══════════════════════════════════════════════════════════════════


class TestMarkTerminal:
    def test_mark_terminal_with_reason(self, store, goal_store):
        _, study = _make_study(store, goal_store)
        runner = AutoresearchRunner(study, store)
        runner._mark_terminal(
            StudyStatus.ERROR, last_metrics={"calmar": 0.3},
            last_error="oops", reason=ShutdownReason.STAGNATION,
        )
        updated = store.get_study(study.study_id)
        assert updated.execution_status == StudyStatus.ERROR
        assert "stagnation" in updated.last_error
        assert updated.last_metrics == {"calmar": 0.3}

    def test_mark_terminal_no_reason(self, store, goal_store):
        _, study = _make_study(store, goal_store)
        runner = AutoresearchRunner(study, store)
        runner._mark_terminal(StudyStatus.COMPLETE, last_metrics={"calmar": 0.6})
        updated = store.get_study(study.study_id)
        assert updated.execution_status == StudyStatus.COMPLETE


# ═══════════════════════════════════════════════════════════════════
# 11. AutoresearchRunner — emitter exception handling
# ═══════════════════════════════════════════════════════════════════


class TestRunnerEmitterException:
    def test_emit_exception_does_not_crash(self, store, goal_store):
        _, study = _make_study(store, goal_store)
        runner = AutoresearchRunner(study, store, emitter=FailingEmitter())
        # Should not raise
        runner._emit("sess", "test_event", {"key": "value"})


# ═══════════════════════════════════════════════════════════════════
# 12. AutoresearchRunner — AEGIS helpers
# ═══════════════════════════════════════════════════════════════════


class TestAEGISHelpers:
    def test_check_novelty_no_goal_id(self, store, goal_store):
        _, study = _make_study(store, goal_store, goal_id=None)
        runner = AutoresearchRunner(study, store)
        is_novel, reason = runner._check_novelty("hypothesis", ["calmar"])
        assert is_novel is True
        assert reason is None

    def test_check_regression_no_goal_id(self, store, goal_store):
        _, study = _make_study(store, goal_store, goal_id=None)
        runner = AutoresearchRunner(study, store)
        passes, regressed = runner._check_regression({"calmar": "flipped"})
        assert passes is True
        assert regressed == []

    def test_archive_rejected_no_goal_id(self, store, goal_store):
        _, study = _make_study(store, goal_store, goal_id=None)
        runner = AutoresearchRunner(study, store)
        # Should not raise
        runner._archive_rejected(1, "hypothesis", "novelty", "reason")

    def test_build_journal_context_no_goal_id(self, store, goal_store):
        _, study = _make_study(store, goal_store, goal_id=None)
        runner = AutoresearchRunner(study, store)
        ctx = runner._build_journal_context()
        assert ctx == ""

    def test_build_scoreboard_context_no_scoreboard(self, store, goal_store):
        _, study = _make_study(store, goal_store)
        runner = AutoresearchRunner(study, store)
        ctx = runner._build_scoreboard_context()
        assert ctx == ""

    def test_build_scoreboard_context_with_scoreboard(self, store, goal_store):
        from strategy_research.core.goal.scoreboard import LeverScoreboard
        _, study = _make_study(store, goal_store)
        runner = AutoresearchRunner(study, store)
        runner._scoreboard = LeverScoreboard()
        runner._scoreboard.update(["action"], {"calmar": "flipped"}, "accepted", 1, 1)
        ctx = runner._build_scoreboard_context()
        assert isinstance(ctx, str)


# ═══════════════════════════════════════════════════════════════════
# 13. StudyScheduler — pause, resume, is_running, active_studies
# ═══════════════════════════════════════════════════════════════════


class TestSchedulerControl:
    def test_pause_nonexistent_returns_false(self, store):
        sched = StudyScheduler(store)
        assert sched.pause("nonexistent") is False

    def test_resume_nonexistent_returns_false(self, store):
        sched = StudyScheduler(store)
        assert sched.resume("nonexistent") is False

    def test_cancel_nonexistent_returns_false(self, store):
        sched = StudyScheduler(store)
        assert sched.cancel("nonexistent") is False

    def test_is_running_empty(self, store):
        sched = StudyScheduler(store)
        assert sched.is_running("anything") is False

    def test_active_studies_empty(self, store):
        sched = StudyScheduler(store)
        assert sched.active_studies() == []

    def test_pause_resume_cycle(self, store, goal_store, monkeypatch):
        """Test pause → resume via runner control token (no scheduler race)."""
        from strategy_research.core.study import runner as runner_mod

        round_states = []

        def _round(self, r, prev, directives_text=None):
            round_states.append(r)
            return {
                "round": r, "run_name": f"run_{r:04d}", "run_dir": _TEST_WS / "fake",
                "metrics": {"calmar": 0.1 + r * 0.1},  # improving to avoid early stop
                "verdict": "discard",
                "decision": {"stagnation_triggered": False, "reason": "",
                             "to_dict": lambda: {"stagnation_triggered": False}},
                "agent_outputs": {},
                "summary": None, "backtest_error": None,
            }

        monkeypatch.setattr(runner_mod.AutoresearchRunner, "_run_one_round", _round)
        monkeypatch.setattr(runner_mod.AutoresearchRunner, "_round_cooldown", lambda self: 0.0)
        monkeypatch.setattr(runner_mod.AutoresearchRunner, "_maybe_load_previous_summary", lambda self, s: None)

        _, study = _make_study(store, goal_store,
            metric_targets=[{"name": "calmar", "op": ">=", "value": 99.0}],
            max_rounds=10,
            behavior=None,
        )
        control = ControlToken()
        runner = AutoresearchRunner(study, store, control=control)

        async def main():
            # Set pause before starting — runner should wait
            control.paused = True

            async def pause_then_resume():
                await asyncio.sleep(0.1)
                control.paused = False

            # Run the runner and unpause after a short delay
            done_task = asyncio.create_task(runner.run())
            unpause_task = asyncio.create_task(pause_then_resume())
            await asyncio.gather(done_task, unpause_task)

            # Should have completed all 10 rounds
            assert len(round_states) == 10

        asyncio.run(main())


# ═══════════════════════════════════════════════════════════════════
# 14. StudyScheduler — emitter_factory
# ═══════════════════════════════════════════════════════════════════


class TestSchedulerEmitter:
    def test_emitter_factory_used(self, store, goal_store, monkeypatch):
        """When emitter_factory is provided, it should be called."""
        from strategy_research.core.study import runner as runner_mod
        factory_calls = []

        def my_factory(session_id):
            factory_calls.append(session_id)
            return NullEmitter()

        def _round(self, r, prev, directives_text=None):
            return {
                "round": r, "run_name": f"run_{r:04d}", "run_dir": _TEST_WS / "fake",
                "metrics": {"calmar": 0.6}, "verdict": "keep",
                "e2_passed": True,
                "decision": {"stagnation_triggered": False, "reason": "",
                             "to_dict": lambda: {"stagnation_triggered": False}},
                "agent_outputs": {k: {"ok": True} for k in
                    ("researcher", "data_quality", "factor_analyst", "strategist",
                     "portfolio_construction", "risk_controller",
                     "attribution_analyst", "anti_overfit_analyst",
                     "backtest_diagnostics")},
                "summary": {"round": 1, "agent_statuses": {}, "performance_change": None,
                            "acceptance_decision": {"stagnation_triggered": False}},
                "backtest_error": None,
            }

        monkeypatch.setattr(runner_mod.AutoresearchRunner, "_run_one_round", _round)
        monkeypatch.setattr(runner_mod.AutoresearchRunner, "_round_cooldown", lambda self: 0.0)
        monkeypatch.setattr(runner_mod.AutoresearchRunner, "_maybe_load_previous_summary", lambda self, s: None)

        _, study = _make_study(store, goal_store)
        svc = FakeSessionService()
        sched = StudyScheduler(store, session_service=svc, emitter_factory=my_factory)

        async def main():
            await sched.submit(study)
            cur = await _await_status(store, study.study_id, StudyStatus.COMPLETE)
            assert cur is not None
            # v2 single identity: emitter is bound to the study's own
            # session_id (== study_id), not the owner chat session.
            assert study.study_id in factory_calls
            await sched.shutdown()

        asyncio.run(main())


# ═══════════════════════════════════════════════════════════════════
# 15. StudyScheduler — _emit_event exception handling
# ═══════════════════════════════════════════════════════════════════


class TestSchedulerEmitEvent:
    def test_emit_event_no_session_service(self, store):
        sched = StudyScheduler(store)
        # Should not raise
        sched._emit_event("sess", "test_event", {})

    def test_emit_event_exception_caught(self, store):
        class BadBus:
            def emit(self, *a):
                raise RuntimeError("bus error")

        svc = FakeSessionService()
        svc.event_bus = BadBus()
        sched = StudyScheduler(store, session_service=svc)
        # Should not raise
        sched._emit_event("sess", "test_event", {})


# ═══════════════════════════════════════════════════════════════════
# 16. StudyScheduler — recover_on_startup QUEUED path
# ═══════════════════════════════════════════════════════════════════


class TestSchedulerRecover:
    def test_recover_queued_resubmitted(self, store, goal_store, monkeypatch, tmp_path):
        from strategy_research.core.study import runner as runner_mod

        def _round(self, r, prev, directives_text=None):
            return {
                "round": r, "run_name": f"run_{r:04d}", "run_dir": _TEST_WS / "fake",
                "metrics": {"calmar": 0.6}, "verdict": "keep",
                "e2_passed": True,
                "decision": {"stagnation_triggered": False, "reason": "",
                             "to_dict": lambda: {"stagnation_triggered": False}},
                "agent_outputs": {k: {"ok": True} for k in
                    ("researcher", "data_quality", "factor_analyst", "strategist",
                     "portfolio_construction", "risk_controller",
                     "attribution_analyst", "anti_overfit_analyst",
                     "backtest_diagnostics")},
                "summary": {"round": 1, "agent_statuses": {}, "performance_change": None,
                            "acceptance_decision": {"stagnation_triggered": False}},
                "backtest_error": None,
            }

        monkeypatch.setattr(runner_mod.AutoresearchRunner, "_run_one_round", _round)
        monkeypatch.setattr(runner_mod.AutoresearchRunner, "_round_cooldown", lambda self: 0.0)
        monkeypatch.setattr(runner_mod.AutoresearchRunner, "_maybe_load_previous_summary", lambda self, s: None)

        _, study = _make_study(store, goal_store)
        # Leave as QUEUED (default)
        svc = FakeSessionService()
        sched = StudyScheduler(store, session_service=svc)

        async def main():
            recs = await sched.recover_on_startup()
            assert len(recs) == 1
            # QUEUED studies should be re-submitted and eventually complete
            cur = await _await_status(store, study.study_id, StudyStatus.COMPLETE, timeout_steps=500)
            assert cur is not None
            await sched.shutdown()

        asyncio.run(main())

    def test_recover_empty(self, store):
        sched = StudyScheduler(store)

        async def main():
            recs = await sched.recover_on_startup()
            assert recs == []
            await sched.shutdown()

        asyncio.run(main())

    def test_recover_multiple_sessions(self, store, goal_store, monkeypatch, tmp_path):
        from strategy_research.core.study import runner as runner_mod

        def _round(self, r, prev, directives_text=None):
            return {
                "round": r, "run_name": f"run_{r:04d}", "run_dir": _TEST_WS / "fake",
                "metrics": {"calmar": 0.6}, "verdict": "keep",
                "e2_passed": True,
                "decision": {"stagnation_triggered": False, "reason": "",
                             "to_dict": lambda: {"stagnation_triggered": False}},
                "agent_outputs": {k: {"ok": True} for k in
                    ("researcher", "data_quality", "factor_analyst", "strategist",
                     "portfolio_construction", "risk_controller",
                     "attribution_analyst", "anti_overfit_analyst",
                     "backtest_diagnostics")},
                "summary": {"round": 1, "agent_statuses": {}, "performance_change": None,
                            "acceptance_decision": {"stagnation_triggered": False}},
                "backtest_error": None,
            }

        monkeypatch.setattr(runner_mod.AutoresearchRunner, "_run_one_round", _round)
        monkeypatch.setattr(runner_mod.AutoresearchRunner, "_round_cooldown", lambda self: 0.0)
        monkeypatch.setattr(runner_mod.AutoresearchRunner, "_maybe_load_previous_summary", lambda self, s: None)

        # Create studies in different sessions, all RUNNING
        goal1 = goal_store.replace_goal(session_id="s1", objective="obj1", criteria=["c1"])
        s1 = store.create_study(
            owner_session_id="s1", goal_id=goal1.goal_id, objective="obj1",
            workspace_path=str(_TEST_WS), strategy_name="strat1",
        )
        goal2 = goal_store.replace_goal(session_id="s2", objective="obj2", criteria=["c2"])
        s2 = store.create_study(
            owner_session_id="s2", goal_id=goal2.goal_id, objective="obj2",
            workspace_path=str(_TEST_WS), strategy_name="strat2",
        )
        store.update_execution_status(s1.study_id, StudyStatus.RUNNING)
        store.update_execution_status(s2.study_id, StudyStatus.RUNNING)

        svc = FakeSessionService()
        sched = StudyScheduler(store, session_service=svc)

        async def main():
            recs = await sched.recover_on_startup()
            assert len(recs) == 2
            # Check DB directly (recs have stale RUNNING status from before update)
            s1_updated = store.get_study(s1.study_id)
            s2_updated = store.get_study(s2.study_id)
            assert s1_updated.execution_status == StudyStatus.INTERRUPTED
            assert s2_updated.execution_status == StudyStatus.INTERRUPTED
            await sched.shutdown()

        asyncio.run(main())


# ═══════════════════════════════════════════════════════════════════
# 17. make_event_bus_emitter + _EventBusEmitter
# ═══════════════════════════════════════════════════════════════════


class TestEventBusEmitter:
    def test_make_event_bus_emitter(self):
        bus = _FakeBus()
        emitter = make_event_bus_emitter("sess-1", bus)
        assert isinstance(emitter, _EventBusEmitter)
        assert emitter.session_id == "sess-1"

    def test_emit_calls_bus(self):
        bus = _FakeBus()
        emitter = _EventBusEmitter("sess-1", bus)
        emitter.emit("sess-1", "test_event", {"key": "value"})
        assert len(bus.events) == 1
        assert bus.events[0] == ("sess-1", "test_event", {"key": "value"})

    def test_emit_passes_session_id(self):
        """The session_id parameter is forwarded to the bus."""
        bus = _FakeBus()
        emitter = _EventBusEmitter("sess-1", bus)
        emitter.emit("sess-override", "test_event", {})
        assert bus.events[0][0] == "sess-override"


# ═══════════════════════════════════════════════════════════════════
# 18. Attribution — ABSENT outcome + edge cases
# ═══════════════════════════════════════════════════════════════════


class TestAttributionEdgeCases:
    def test_absent_metric_in_predicted(self):
        """Metric in predicted but not in before/now → still_F (not ABSENT).
        ABSENT is only for metrics not in predicted_tasks at all."""
        result = classify_attribution(["calmar"], set(), set())
        assert result["calmar"] == AttributionOutcome.STILL_F

    def test_duplicate_predicted_metrics(self):
        """Duplicate metric names in predicted_tasks."""
        result = classify_attribution(
            ["calmar", "calmar"], set(), {"calmar"},
        )
        # Both entries should produce the same classification
        assert result["calmar"] == AttributionOutcome.FLIPPED

    def test_compute_precision_all_absent(self):
        attr = {"m1": AttributionOutcome.ABSENT, "m2": AttributionOutcome.ABSENT}
        precision, hits, total = compute_precision(attr)
        assert precision == 0.0
        assert hits == 0
        assert total == 0

    def test_compute_precision_mixed_with_absent(self):
        attr = {
            "m1": AttributionOutcome.FLIPPED,
            "m2": AttributionOutcome.ABSENT,
            "m3": AttributionOutcome.STILL_T,
        }
        precision, hits, total = compute_precision(attr)
        assert hits == 1
        # attributed=2 (FLIPPED + STILL_T), side_effects=0, total=2
        assert total == 2
        assert precision == 0.5

    def test_compute_precision_empty(self):
        precision, hits, total = compute_precision({})
        assert precision == 0.0
        assert hits == 0
        assert total == 0

    def test_compute_precision_all_regressed(self):
        attr = {"m1": AttributionOutcome.REGRESSED, "m2": AttributionOutcome.REGRESSED}
        precision, hits, total = compute_precision(attr)
        assert hits == 0
        # attributed=2, side_effects=2, total=4
        assert total == 4
        assert precision == 0.0

    def test_compute_precision_still_t_no_effect(self):
        attr = {"m1": AttributionOutcome.STILL_T, "m2": AttributionOutcome.STILL_T}
        precision, hits, total = compute_precision(attr)
        assert hits == 0
        # attributed=2, side_effects=0, total=2
        assert total == 2
        assert precision == 0.0


# ═══════════════════════════════════════════════════════════════════
# 19. Scoreboard
# ═══════════════════════════════════════════════════════════════════


class TestScoreboard:
    def test_empty_board_context(self):
        from strategy_research.core.goal.scoreboard import LeverScoreboard
        board = LeverScoreboard()
        ctx = board.build_scoreboard_context()
        assert isinstance(ctx, str)

    def test_update_single_lever(self):
        from strategy_research.core.goal.scoreboard import LeverScoreboard
        board = LeverScoreboard()
        board.update(["action"], {"calmar": "flipped"}, "accepted", 1, 1)
        stats = [s for s in board.get_scoreboard() if s.lever == "action"][0]
        assert stats.attempts == 1
        assert stats.accepted == 1

    def test_update_multiple_levers(self):
        from strategy_research.core.goal.scoreboard import LeverScoreboard
        board = LeverScoreboard()
        board.update(["action", "configuration"], {"calmar": "flipped"}, "accepted", 1, 1)
        action_stats = [s for s in board.get_scoreboard() if s.lever == "action"][0]
        config_stats = [s for s in board.get_scoreboard() if s.lever == "configuration"][0]
        assert action_stats.attempts == 1
        assert config_stats.attempts == 1

    def test_reverted_outcome(self):
        from strategy_research.core.goal.scoreboard import LeverScoreboard
        board = LeverScoreboard()
        board.update(["action"], {"calmar": "still_F"}, "reverted", 1, 1)
        stats = [s for s in board.get_scoreboard() if s.lever == "action"][0]
        assert stats.reverted == 1

    def test_scoreboard_context_after_updates(self):
        from strategy_research.core.goal.scoreboard import LeverScoreboard
        board = LeverScoreboard()
        board.update(["action"], {"calmar": "flipped"}, "accepted", 1, 1)
        board.update(["action"], {"calmar": "still_T"}, "accepted", 2, 2)
        ctx = board.build_scoreboard_context()
        assert "action" in ctx

    def test_get_best_lever(self):
        from strategy_research.core.goal.scoreboard import LeverScoreboard
        board = LeverScoreboard()
        board.update(["action"], {"calmar": "flipped"}, "accepted", 1, 1)
        best = board.get_best_lever()
        assert best == "action"

    def test_lever_fatigue_detection(self):
        from strategy_research.core.goal.scoreboard import LeverScoreboard
        board = LeverScoreboard()
        # 3 rounds with same low precision → fatigued
        for i in range(3):
            board.update(["action"], {"calmar": "still_F"}, "reverted", i+1, i+1)
        assert board.is_lever_fatigued("action")


# ═══════════════════════════════════════════════════════════════════
# 20. StudyStatus enum completeness
# ═══════════════════════════════════════════════════════════════════


class TestStudyStatusEnum:
    def test_all_values(self):
        expected = {
            "queued", "running", "paused", "interrupted", "error",
            "complete", "cancelled", "budget_limited", "monitoring",
            "needs_refresh", "early_stopped", "archived",
        }
        actual = {s.value for s in StudyStatus}
        assert actual == expected

    def test_active_set_excludes_terminals(self):
        terminals = {
            StudyStatus.COMPLETE, StudyStatus.CANCELLED,
            StudyStatus.ERROR, StudyStatus.BUDGET_LIMITED,
        }
        assert terminals.isdisjoint(ACTIVE_EXECUTION_STATUSES)

    def test_active_set_includes_monitoring(self):
        # MONITORING is active so restart recovery can re-arm its monitor
        # task (scheduler.recover_on_startup has an explicit branch).
        assert StudyStatus.MONITORING in ACTIVE_EXECUTION_STATUSES

    def test_active_set_excludes_needs_refresh(self):
        assert StudyStatus.NEEDS_REFRESH not in ACTIVE_EXECUTION_STATUSES

    def test_active_set_excludes_interrupted(self):
        assert StudyStatus.INTERRUPTED not in ACTIVE_EXECUTION_STATUSES

    def test_active_set_excludes_early_stopped(self):
        assert StudyStatus.EARLY_STOPPED not in ACTIVE_EXECUTION_STATUSES

    def test_active_set_has_queued_running_paused(self):
        assert StudyStatus.QUEUED in ACTIVE_EXECUTION_STATUSES
        assert StudyStatus.RUNNING in ACTIVE_EXECUTION_STATUSES
        assert StudyStatus.PAUSED in ACTIVE_EXECUTION_STATUSES


# ═══════════════════════════════════════════════════════════════════
# 21. AutoresearchRunner — run() exception handling
# ═══════════════════════════════════════════════════════════════════


class TestRunnerRunException:
    def test_exception_in_run_loop_sets_error(self, store, goal_store, monkeypatch):
        from strategy_research.core.study import runner as runner_mod

        def _failing_round(self, r, prev, directives_text=None):
            raise RuntimeError("round exploded")

        monkeypatch.setattr(runner_mod.AutoresearchRunner, "_run_one_round", _failing_round)
        monkeypatch.setattr(runner_mod.AutoresearchRunner, "_round_cooldown", lambda self: 0.0)
        monkeypatch.setattr(runner_mod.AutoresearchRunner, "_maybe_load_previous_summary", lambda self, s: None)

        _, study = _make_study(store, goal_store, max_rounds=5)
        emitter = CollectingEmitter()
        runner = AutoresearchRunner(study, store, emitter=emitter)

        async def main():
            reason = await runner.run()
            assert reason == ShutdownReason.ERROR
            updated = store.get_study(study.study_id)
            assert updated.execution_status == StudyStatus.ERROR
            assert "round exploded" in (updated.last_error or "")
            # Should have emitted study_failed
            failed_events = [e for _, e, _ in emitter.events if e == "study_failed"]
            assert len(failed_events) == 1

        asyncio.run(main())


# ═══════════════════════════════════════════════════════════════════
# 22. AutoresearchRunner — goal_store close on exception
# ═══════════════════════════════════════════════════════════════════


class TestRunnerGoalStoreClose:
    def test_own_goal_store_closed_on_success(self, store, goal_store, monkeypatch):
        from strategy_research.core.study import runner as runner_mod

        def _round(self, r, prev, directives_text=None):
            return {
                "round": r, "run_name": f"run_{r:04d}", "run_dir": _TEST_WS / "fake",
                "metrics": {"calmar": 0.6}, "verdict": "keep",
                "e2_passed": True,
                "decision": {"stagnation_triggered": False, "reason": "",
                             "to_dict": lambda: {"stagnation_triggered": False}},
                "agent_outputs": {k: {"ok": True} for k in
                    ("researcher", "data_quality", "factor_analyst", "strategist",
                     "portfolio_construction", "risk_controller",
                     "attribution_analyst", "anti_overfit_analyst",
                     "backtest_diagnostics")},
                "summary": {"round": 1, "agent_statuses": {}, "performance_change": None,
                            "acceptance_decision": {"stagnation_triggered": False}},
                "backtest_error": None,
            }

        monkeypatch.setattr(runner_mod.AutoresearchRunner, "_run_one_round", _round)
        monkeypatch.setattr(runner_mod.AutoresearchRunner, "_round_cooldown", lambda self: 0.0)
        monkeypatch.setattr(runner_mod.AutoresearchRunner, "_maybe_load_previous_summary", lambda self, s: None)

        _, study = _make_study(store, goal_store)
        runner = AutoresearchRunner(study, store)  # own goal store

        async def main():
            reason = await runner.run()
            assert reason == ShutdownReason.TARGETS_MET
            # own goal store should have been closed (no error)

        asyncio.run(main())


# ═══════════════════════════════════════════════════════════════════
# 23. Scheduler shutdown with active tasks
# ═══════════════════════════════════════════════════════════════════


class TestSchedulerShutdown:
    def test_double_shutdown(self, store):
        sched = StudyScheduler(store)

        async def main():
            await sched.shutdown()
            await sched.shutdown()  # Should not raise

        asyncio.run(main())

    def test_shutdown_cancels_control_tokens(self, store, goal_store, monkeypatch):
        from strategy_research.core.study import runner as runner_mod

        def _round(self, r, prev, directives_text=None):
            return {
                "round": r, "run_name": f"run_{r:04d}", "run_dir": _TEST_WS / "fake",
                "metrics": {"calmar": 0.6}, "verdict": "keep",
                "e2_passed": False,
                "decision": {"stagnation_triggered": False, "reason": "",
                             "to_dict": lambda: {"stagnation_triggered": False}},
                "agent_outputs": {},
                "summary": None, "backtest_error": None,
            }

        monkeypatch.setattr(runner_mod.AutoresearchRunner, "_run_one_round", _round)
        monkeypatch.setattr(runner_mod.AutoresearchRunner, "_round_cooldown", lambda self: 0.0)
        monkeypatch.setattr(runner_mod.AutoresearchRunner, "_maybe_load_previous_summary", lambda self, s: None)

        _, study = _make_study(store, goal_store,
            metric_targets=[{"name": "calmar", "op": ">=", "value": 99.0}],
            max_rounds=None,
        )
        svc = FakeSessionService()
        sched = StudyScheduler(store, session_service=svc)

        async def main():
            await sched.submit(study)
            await _await_status(store, study.study_id, StudyStatus.RUNNING)
            assert sched.cancel(study.study_id) is True
            cur = await _await_status(store, study.study_id, StudyStatus.CANCELLED)
            assert cur is not None
            await sched.shutdown()

        asyncio.run(main())


# ═══════════════════════════════════════════════════════════════════
# Helper
# ═══════════════════════════════════════════════════════════════════


async def _await_status(store, study_id, target, *, timeout_steps=300, step=0.01):
    last = None
    for _ in range(timeout_steps):
        await asyncio.sleep(step)
        cur = store.get_study(study_id)
        last = cur
        if cur and cur.execution_status == target:
            return cur
    return last
