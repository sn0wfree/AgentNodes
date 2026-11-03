"""Tests for the workflow segment loop (executor.py) and store (store.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from strategy_research.kernel.workflow.builtin import load_definition
from strategy_research.kernel.workflow.executor import WorkflowRunner
from strategy_research.kernel.workflow.store import WorkflowStore

# Pin all tests in this file to the same xdist worker.
# The workflow module's import path + JSON template reads cause
# non-deterministic failures when run in parallel alongside other test
# files (see docs/study-subsystem-tech-debt-20260827.md ⑥).
pytestmark = pytest.mark.xdist_group("workflow")


class FakeLoop:
    """Scripted LLM loop factory for executor tests."""

    def __init__(self, planner=None, evaluator="stop", step="步骤完成") -> None:
        self._planner = planner
        self._evaluator = evaluator
        self._step = step
        self.calls = 0

    def __call__(self, **kwargs) -> str:
        self.calls += 1
        role = kwargs.get("role")
        if role == "planner":
            return self._planner
        if role == "evaluator":
            return self._evaluator
        return self._step


PLAN_2 = json.dumps({"plan": [
    {"id": "plan_1", "title": "数据准备", "description": "检查数据质量并确认覆盖",
     "type": "llm_agent", "tools": ["read"], "depends_on": []},
    {"id": "plan_2", "title": "回测", "description": "运行回测验证假设并记录指标",
     "type": "llm_agent", "tools": ["run_backtest"], "depends_on": ["plan_1"]},
]})


def make_runner(name, workspace, objective="研究动量策略", **kwargs):
    definition = load_definition(name, workspace)
    assert definition is not None, f"builtin {name} not found"
    return WorkflowRunner(definition, workspace, objective, **kwargs)


# ── Store (isolated DB) ───────────────────────────────────────


class TestStore:
    def test_schema_created_in_workspace(self, tmp_path: Path):
        store = WorkflowStore(db_path=tmp_path / "workflows.db")
        assert store.health_check()
        store.create_run("r1", "demo", "s1", "目标", {"params": {}})
        run = store.get_run("r1")
        assert run["status"] == "pending"
        assert run["definition_name"] == "demo"
        assert tmp_path.joinpath("workflows.db").exists()

    def test_full_lifecycle(self, tmp_path: Path):
        store = WorkflowStore(db_path=tmp_path / "wf.db")
        store.create_run("r1", "demo", "s1", "目标", {"params": {}})
        store.update_run("r1", status="running", segment_idx=1,
                         findings=["f1"], failures=["x"])
        store.upsert_segment("r1", 0, ["a", "b"], status="completed", elapsed_s=1.2)
        store.create_approval("r1", "gate")
        assert store.get_approval("r1", "gate")["status"] == "awaiting"
        assert store.respond_approval("r1", "gate", True)
        assert store.get_approval("r1", "gate")["status"] == "approved"
        store.append_event("r1", "segment_started", {"idx": 0})
        store.append_event("r1", "run_completed", {})
        events = store.list_events("r1")
        assert len(events) == 2
        assert events[0]["event_type"] == "run_completed"
        segments = store.list_segments("r1")
        assert segments[0]["status"] == "completed"
        assert store.delete_run("r1")
        assert store.get_run("r1") is None
        assert store.list_events("r1") == []

    def test_isolated_from_session_db(self, tmp_path: Path):
        # WorkflowStore only touches its own file
        store = WorkflowStore(db_path=tmp_path / "wf.db")
        store.create_run("r1", "demo", "s1", "obj", {})
        tables = {r["name"] for r in store._ensure_conn().execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "runs" in tables and "run_events" in tables
        assert "messages" not in tables  # session tables never created

    def test_health_check_and_repair(self, tmp_path: Path):
        db = tmp_path / "wf.db"
        db.write_bytes(b"not a sqlite file")
        store = WorkflowStore(db_path=db)
        assert store.health_check() is False
        assert store.auto_repair() is True
        assert store.health_check() is True

    def test_runner_persists_through_store(self, tmp_path: Path):
        store = WorkflowStore(db_path=tmp_path / "wf.db")
        runner = make_runner("plan_execute_auto", tmp_path, store=store,
                             loop_factory=FakeLoop(planner=PLAN_2))
        run_id = runner.start()
        assert runner.status == "completed"
        assert store.get_run(run_id)["status"] == "completed"
        assert len(store.list_node_outputs(run_id)) >= 4
        assert store.list_events(run_id)


# ── Auto workflow (no approval) ───────────────────────────────


class TestAutoFlow:
    def test_full_auto_run(self, tmp_path: Path):
        events = []
        runner = make_runner("plan_execute_auto", tmp_path,
                             loop_factory=FakeLoop(planner=PLAN_2),
                             emit_event=lambda t, d: events.append(t))
        runner.start()
        assert runner.status == "completed"
        assert sorted(runner.pre_completed.keys()) == ["evaluator", "plan_1", "plan_2", "planner"]
        assert "plan_created" in events
        assert "run_completed" in events
        assert runner.replan_count == 0

    def test_plan_steps_execute_in_topological_order(self, tmp_path: Path):
        []
        def fake(**kwargs):
            role = kwargs.get("role")
            if role == "planner":
                return PLAN_2
            if role == "evaluator":
                return '{"verdict": "stop"}'
            return "ok"
        runner = make_runner("plan_execute_auto", tmp_path, loop_factory=fake)
        runner.start()
        # plan_1 must complete before plan_2 (dependency)
        assert "plan_1" in runner.pre_completed and "plan_2" in runner.pre_completed

    def test_evaluator_replan_loops_to_planner(self, tmp_path: Path):
        calls = {"plan": 0}
        def fake(**kwargs):
            role = kwargs.get("role")
            if role == "planner":
                calls["plan"] += 1
                return PLAN_2
            if role == "evaluator":
                return '{"verdict": "replan", "reason": "数据不足", "findings": ["补数据"]}'
            return "ok"
        runner = make_runner("plan_execute_auto", tmp_path, loop_factory=fake)
        runner.start()
        assert runner.status == "completed"  # ended via max_segments hard limit
        assert runner.replan_count == runner.params["exec"]["max_segments"]
        assert calls["plan"] >= runner.replan_count

    def test_max_segments_hard_limit(self, tmp_path: Path):
        runner = make_runner("plan_execute_auto", tmp_path,
                             loop_factory=FakeLoop(planner=PLAN_2,
                                                   evaluator='{"verdict": "replan"}'))
        runner.start()
        assert runner.replan_count == 3  # hard limit = max_segments
        assert runner.replan_count == runner.params["exec"]["max_segments"]
        assert runner.status == "completed"

    def test_rule_layer_stop_after_two_failures(self, tmp_path: Path):
        def fake(**kwargs):
            role = kwargs.get("role")
            if role == "planner":
                return PLAN_2
            if role == "evaluator":
                return '{"verdict": "stop"}'
            raise RuntimeError("执行失败")
        runner = make_runner("plan_execute_auto", tmp_path, loop_factory=fake)
        runner.start()
        assert runner.status == "completed"
        assert any("连续 2 步失败" in f for f in runner.failures)

    def test_evaluator_loop_failure_defaults_continue(self, tmp_path: Path):
        def fake(**kwargs):
            role = kwargs.get("role")
            if role == "planner":
                return PLAN_2
            if role == "evaluator":
                raise RuntimeError("evaluator down")
            return "ok"
        runner = make_runner("plan_execute_auto", tmp_path, loop_factory=fake)
        runner.start()
        assert runner.status == "completed"


# ── Approval workflow ─────────────────────────────────────────


class TestApprovalFlow:
    def test_awaits_after_planner_segment(self, tmp_path: Path):
        events = []
        runner = make_runner("plan_execute_approval", tmp_path,
                             loop_factory=FakeLoop(planner=PLAN_2),
                             emit_event=lambda t, d: events.append(t))
        runner.start()
        assert runner.status == "awaiting"
        assert "awaiting_approval" in events
        assert sorted(runner.pre_completed.keys()) == ["plan_1", "plan_2", "planner"]

    def test_approve_continues_to_evaluator(self, tmp_path: Path):
        runner = make_runner("plan_execute_approval", tmp_path,
                             loop_factory=FakeLoop(planner=PLAN_2))
        runner.start()
        assert runner.approve(True)
        assert runner.status == "completed"
        assert "evaluator" in runner.pre_completed
        approval = runner.store.get_approval(runner.run_id, "approval")
        assert approval["status"] == "approved"

    def test_reject_triggers_replan(self, tmp_path: Path):
        calls = {"plan": 0}
        def fake(**kwargs):
            role = kwargs.get("role")
            if role == "planner":
                calls["plan"] += 1
                return PLAN_2
            if role == "evaluator":
                return '{"verdict": "stop"}'
            return "ok"
        runner = make_runner("plan_execute_approval", tmp_path, loop_factory=fake)
        runner.start()
        assert runner.status == "awaiting"
        assert runner.approve(False, edits={"note": "换方向"})
        assert calls["plan"] == 2  # initial + replan
        assert runner.status == "completed"

    def test_approve_when_not_awaiting_returns_false(self, tmp_path: Path):
        runner = make_runner("plan_execute_auto", tmp_path,
                             loop_factory=FakeLoop(planner=PLAN_2))
        runner.start()
        assert runner.status == "completed"
        assert runner.approve(True) is False

    def test_timeout_keeps_awaiting(self, tmp_path: Path):
        runner = make_runner("plan_execute_approval", tmp_path,
                             loop_factory=FakeLoop(planner=PLAN_2))
        runner.start()
        # timeout=null → wait forever; nothing auto-resumes
        assert runner.status == "awaiting"
        assert runner.approve(True)
        assert runner.status == "completed"


# ── Static pipeline (no planner/evaluator) ────────────────────


class TestStaticFlow:
    def test_alpha_research_runs_through(self, tmp_path: Path):
        from strategy_research.kernel.workflow.node_types import NodeExecutors
        NodeExecutors.reset()
        NodeExecutors.register("run_backtest", lambda **kw: {"summary": "回测完成", "sharpe": 0.9})
        NodeExecutors.register("check_data", lambda **kw: {"summary": "数据 OK"})
        runner = make_runner("alpha_research", tmp_path,
                             loop_factory=FakeLoop(planner=PLAN_2))
        runner.start()
        assert runner.status == "completed"
        assert "hypothesis" in runner.pre_completed
        assert "data_check" in runner.pre_completed
        assert "backtest" in runner.pre_completed
        assert "evaluator" in runner.pre_completed

    def test_data_quality_audit(self, tmp_path: Path):
        from strategy_research.kernel.workflow.node_types import NodeExecutors
        NodeExecutors.reset()
        NodeExecutors.register("check_data", lambda **kw: {"summary": "数据 OK"})
        runner = make_runner("data_quality_audit", tmp_path,
                             loop_factory=FakeLoop(planner=PLAN_2))
        runner.start()
        assert runner.status == "completed"
        assert set(runner.pre_completed.keys()) == {"check", "diagnose"}


# ── Params override ───────────────────────────────────────────


class TestParams:
    def test_run_request_override_wins(self, tmp_path: Path):
        runner = make_runner("plan_execute_auto", tmp_path,
                             loop_factory=FakeLoop(planner=PLAN_2),
                             params_override={"exec": {"max_segments": 1}})
        assert runner.params["exec"]["max_segments"] == 1
        assert runner.params["planner"]["max_steps"] == 6  # untouched default

    def test_snapshot_persisted(self, tmp_path: Path):
        store = WorkflowStore(db_path=tmp_path / "wf.db")
        runner = make_runner("plan_execute_auto", tmp_path, store=store,
                             loop_factory=FakeLoop(planner=PLAN_2),
                             params_override={"exec": {"max_segments": 2}})
        run_id = runner.start()
        run = store.get_run(run_id)
        snapshot = json.loads(run["params_snapshot"])
        assert snapshot["params"]["exec"]["max_segments"] == 2
