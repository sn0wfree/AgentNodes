"""Phase 4 - v0.5 unit tests: SwarmRuntime + context helpers.

Covers:
  - SwarmPreset: dataclass fields, defaults
  - AgentResult: dataclass fields, defaults
  - SwarmResult: dataclass fields, defaults
  - SwarmRuntime: execute with stub controller, cancel, hook lifecycle
  - SwarmRuntime: _find_agent, _gather_upstream, _topological_layers
  - SwarmRuntime: _emit error safety, _any_hook_should_stop
  - context.default_goal_criteria, format_goal_context, criterion_is_covered
  - context.goal_progress_tuple, goal_needs_continuation
"""
from __future__ import annotations

from unittest.mock import MagicMock

from strategy_research.core.goal import context as goal_ctx
from strategy_research.kernel.swarm.runtime import (
    AgentResult,
    SwarmPreset,
    SwarmResult,
    SwarmRuntime,
)
from strategy_research.core.workflow.types import AgentCall, AgentStatus

# ═══════════════════════════════════════════════════════════════════════
# SwarmPreset / AgentResult / SwarmResult dataclasses
# ═══════════════════════════════════════════════════════════════════════


class TestSwarmPreset:
    def test_defaults(self):
        p = SwarmPreset(name="test")
        assert p.name == "test"
        assert p.description == ""
        assert p.agents == []
        assert p.dag == {}
        assert p.goal is None
        assert p.completion is None
        assert p.branches == []
        assert p.version == "1.0"

    def test_with_fields(self):
        p = SwarmPreset(
            name="wf",
            description="desc",
            agents=[AgentCall(agent_name="a", prompt=".prompts/a.md")],
            dag={"a": []},
            goal={"criteria": ["c1"]},
            completion={"mode": "auto"},
        )
        assert p.description == "desc"
        assert len(p.agents) == 1
        assert p.goal["criteria"] == ["c1"]


class TestAgentResult:
    def test_defaults(self):
        r = AgentResult(agent_id="a")
        assert r.agent_id == "a"
        assert r.status == AgentStatus.PENDING
        assert r.output == ""
        assert r.error is None
        assert r.elapsed_s == 0.0

    def test_with_error(self):
        r = AgentResult(agent_id="a", status=AgentStatus.ERROR, error="boom")
        assert r.status == AgentStatus.ERROR
        assert r.error == "boom"


class TestSwarmResult:
    def test_defaults(self):
        r = SwarmResult()
        assert r.run_id == ""
        assert r.preset_name == ""
        assert r.agent_results == {}
        assert r.final_output == ""
        assert r.success is False

    def test_with_results(self):
        r = SwarmResult(
            run_id="r1",
            preset_name="test",
            agent_results={"a": AgentResult(agent_id="a", status=AgentStatus.SUCCESS)},
            success=True,
        )
        assert r.run_id == "r1"
        assert "a" in r.agent_results
        assert r.success is True


# ═══════════════════════════════════════════════════════════════════════
# SwarmRuntime - internal helpers
# ═══════════════════════════════════════════════════════════════════════


class TestSwarmRuntimeFindAgent:
    def test_find_existing(self):
        rt = SwarmRuntime()
        call = AgentCall(agent_name="target", prompt="p")
        result = rt._find_agent([call], "target")
        assert result is call

    def test_find_missing(self):
        rt = SwarmRuntime()
        result = rt._find_agent([], "ghost")
        assert result is None

    def test_find_among_multiple(self):
        rt = SwarmRuntime()
        calls = [
            AgentCall(agent_name="a", prompt="p"),
            AgentCall(agent_name="b", prompt="p"),
            AgentCall(agent_name="c", prompt="p"),
        ]
        result = rt._find_agent(calls, "b")
        assert result.agent_name == "b"


class TestSwarmRuntimeGatherUpstream:
    def test_no_upstream(self):
        rt = SwarmRuntime()
        result = rt._gather_upstream("a", {"a": []}, {})
        assert result == {}

    def test_upstream_success(self):
        rt = SwarmRuntime()
        results = {
            "up1": AgentResult(agent_id="up1", status=AgentStatus.SUCCESS, output="out1"),
        }
        result = rt._gather_upstream("a", {"a": ["up1"]}, results)
        assert result == {"up1": "out1"}

    def test_upstream_error_excluded(self):
        rt = SwarmRuntime()
        results = {
            "up1": AgentResult(agent_id="up1", status=AgentStatus.ERROR, output="fail"),
        }
        result = rt._gather_upstream("a", {"a": ["up1"]}, results)
        assert result == {}

    def test_multiple_upstream(self):
        rt = SwarmRuntime()
        results = {
            "up1": AgentResult(agent_id="up1", status=AgentStatus.SUCCESS, output="o1"),
            "up2": AgentResult(agent_id="up2", status=AgentStatus.SUCCESS, output="o2"),
            "up3": AgentResult(agent_id="up3", status=AgentStatus.PENDING),
        }
        result = rt._gather_upstream("a", {"a": ["up1", "up2", "up3"]}, results)
        assert "up1" in result
        assert "up2" in result
        assert "up3" not in result


class TestSwarmRuntimeTopologicalLayers:
    def test_empty(self):
        from strategy_research.core.workflow.dag import topological_layers
        assert topological_layers({}) == []

    def test_single(self):
        from strategy_research.core.workflow.dag import topological_layers
        assert topological_layers({"a": []}) == [["a"]]

    def test_chain(self):
        from strategy_research.core.workflow.dag import topological_layers
        layers = topological_layers({"a": [], "b": ["a"], "c": ["b"]})
        assert layers == [["a"], ["b"], ["c"]]

    def test_diamond(self):
        from strategy_research.core.workflow.dag import topological_layers
        layers = topological_layers(
            {"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]}
        )
        assert layers[0] == ["a"]
        assert set(layers[1]) == {"b", "c"}
        assert layers[2] == ["d"]


class TestSwarmRuntimeEmit:
    def test_calls_method(self):
        rt = SwarmRuntime()
        hook = MagicMock()
        rt._emit([hook], "on_layer_start", 0, ["a"], {})
        hook.on_layer_start.assert_called_once_with(0, ["a"], {})

    def test_missing_method_silent(self):
        rt = SwarmRuntime()
        hook = MagicMock()
        hook.on_layer_start = None
        rt._emit([hook], "on_layer_start", 0, ["a"], {})
        # Should not raise

    def test_exception_swallowed(self):
        rt = SwarmRuntime()
        hook = MagicMock()
        hook.on_layer_start.side_effect = RuntimeError("crash")
        rt._emit([hook], "on_layer_start", 0, ["a"], {})
        # Should not raise

    def test_multiple_hooks(self):
        rt = SwarmRuntime()
        h1 = MagicMock()
        h2 = MagicMock()
        rt._emit([h1, h2], "on_layer_start", 0, ["a"], {})
        h1.on_layer_start.assert_called_once()
        h2.on_layer_start.assert_called_once()


class TestSwarmRuntimeShouldStop:
    def test_no_hooks(self):
        rt = SwarmRuntime()
        assert rt._any_hook_should_stop([]) is False

    def test_hook_returns_true(self):
        rt = SwarmRuntime()
        hook = MagicMock()
        hook.should_stop.return_value = True
        assert rt._any_hook_should_stop([hook]) is True

    def test_hook_returns_false(self):
        rt = SwarmRuntime()
        hook = MagicMock()
        hook.should_stop.return_value = False
        assert rt._any_hook_should_stop([hook]) is False

    def test_hook_exception_swallowed(self):
        rt = SwarmRuntime()
        hook = MagicMock()
        hook.should_stop.side_effect = RuntimeError("crash")
        assert rt._any_hook_should_stop([hook]) is False

    def test_multiple_hooks_first_true(self):
        rt = SwarmRuntime()
        h1 = MagicMock()
        h1.should_stop.return_value = True
        h2 = MagicMock()
        h2.should_stop.return_value = False
        assert rt._any_hook_should_stop([h1, h2]) is True


# ═══════════════════════════════════════════════════════════════════════
# SwarmRuntime.execute
# ═══════════════════════════════════════════════════════════════════════


class TestSwarmRuntimeExecute:
    def test_execute_simple_dag(self, tmp_path):
        rt = SwarmRuntime()
        preset = SwarmPreset(
            name="test",
            agents=[AgentCall(agent_name="a", prompt=".prompts/researcher.md", context={"allow_stub": True})],
            dag={"a": []},
        )
        result = rt.execute(preset, tmp_path, "test task")
        assert result.run_id.startswith("swarm_")
        assert "a" in result.agent_results
        assert result.elapsed_s >= 0.0

    def test_execute_with_hooks(self, tmp_path):
        rt = SwarmRuntime()
        hook = MagicMock()
        hook.should_stop.return_value = False
        preset = SwarmPreset(
            name="test",
            agents=[AgentCall(agent_name="a", prompt=".prompts/researcher.md", context={"allow_stub": True})],
            dag={"a": []},
        )
        rt.execute(preset, tmp_path, 'task', hooks=[hook])
        hook.on_layer_start.assert_called()
        hook.on_agent_complete.assert_called()
        hook.on_layer_complete.assert_called()

    def test_execute_hook_should_stop(self, tmp_path):
        rt = SwarmRuntime()
        hook = MagicMock()
        hook.should_stop.return_value = True
        preset = SwarmPreset(
            name="test",
            agents=[
                AgentCall(agent_name="a", prompt=".prompts/researcher.md", context={"allow_stub": True}),
                AgentCall(agent_name="b", prompt=".prompts/researcher.md", context={"allow_stub": True}),
            ],
            dag={"a": [], "b": ["a"]},
        )
        rt.execute(preset, tmp_path, 'task', hooks=[hook])
        # Should stop after first layer
        hook.on_layer_start.assert_called_once()

    def test_execute_two_layer_dag(self, tmp_path):
        rt = SwarmRuntime()
        preset = SwarmPreset(
            name="test",
            agents=[
                AgentCall(agent_name="a", prompt=".prompts/researcher.md", context={"allow_stub": True}),
                AgentCall(agent_name="b", prompt=".prompts/researcher.md", context={"allow_stub": True}),
            ],
            dag={"a": [], "b": ["a"]},
        )
        result = rt.execute(preset, tmp_path, "task")
        assert "a" in result.agent_results
        assert "b" in result.agent_results


class TestSwarmRuntimeCancel:
    def test_cancel_nonexistent(self):
        rt = SwarmRuntime()
        assert rt.cancel("nonexistent") is False

    def test_cancel_after_execute(self, tmp_path):
        rt = SwarmRuntime()
        preset = SwarmPreset(name="t", dag={"a": []})
        result = rt.execute(preset, tmp_path, "task")
        # Run is already done, should be removed from active
        assert rt.cancel(result.run_id) is False


# ═══════════════════════════════════════════════════════════════════════
# Goal context helpers
# ═══════════════════════════════════════════════════════════════════════


class TestDefaultGoalCriteria:
    def test_returns_list(self):
        criteria = goal_ctx.default_goal_criteria()
        assert isinstance(criteria, list)
        assert len(criteria) >= 3

    def test_returns_copy(self):
        c1 = goal_ctx.default_goal_criteria()
        c2 = goal_ctx.default_goal_criteria()
        assert c1 == c2
        assert c1 is not c2


class TestCriterionIsCovered:
    def test_covered_by_status(self):
        snapshot = {"evidence": []}
        criterion = {"criterion_id": "c0", "status": "satisfied"}
        assert goal_ctx.criterion_is_covered(snapshot, criterion) is True

    def test_covered_by_evidence(self):
        snapshot = {"evidence": [{"criterion_id": "c0"}]}
        criterion = {"criterion_id": "c0", "status": "pending"}
        assert goal_ctx.criterion_is_covered(snapshot, criterion) is True

    def test_not_covered(self):
        snapshot = {"evidence": [{"criterion_id": "c1"}]}
        criterion = {"criterion_id": "c0", "status": "pending"}
        assert goal_ctx.criterion_is_covered(snapshot, criterion) is False

    def test_empty_status_treated_as_open(self):
        snapshot = {"evidence": []}
        criterion = {"criterion_id": "c0", "status": ""}
        assert goal_ctx.criterion_is_covered(snapshot, criterion) is False


class TestGoalProgressTuple:
    def test_all_covered(self):
        snapshot = {
            "criteria": [
                {"criterion_id": "c0", "status": "satisfied"},
                {"criterion_id": "c1", "status": "satisfied"},
            ],
            "evidence": [],
            "evidence_count": 0,
        }
        covered, total = goal_ctx.goal_progress_tuple(snapshot)
        assert covered == 2
        assert total == 0

    def test_partial_coverage(self):
        snapshot = {
            "criteria": [
                {"criterion_id": "c0", "status": "pending"},
                {"criterion_id": "c1", "status": "satisfied"},
            ],
            "evidence": [{"criterion_id": "c0"}],
            "evidence_count": 1,
        }
        covered, total = goal_ctx.goal_progress_tuple(snapshot)
        assert covered == 2  # c0 has evidence, c1 has satisfied status
        assert total == 1

    def test_empty(self):
        snapshot = {"criteria": [], "evidence": []}
        covered, total = goal_ctx.goal_progress_tuple(snapshot)
        assert covered == 0
        assert total == 0


class TestGoalNeedsContinuation:
    def test_active_status(self):
        snapshot = {"goal": {"status": "active"}, "criteria": [{"criterion_id": "c0"}]}
        assert goal_ctx.goal_needs_continuation(snapshot) is True

    def test_complete_status(self):
        snapshot = {"goal": {"status": "complete"}, "criteria": []}
        assert goal_ctx.goal_needs_continuation(snapshot) is False

    def test_no_criteria(self):
        """No criteria → nothing left to drive → continuation stops
        (4bf5e7a: goal_needs_continuation returns False when nothing
        to drive)."""
        snapshot = {"goal": {"status": "active"}, "criteria": []}
        assert goal_ctx.goal_needs_continuation(snapshot) is False

    def test_blocked_status(self):
        snapshot = {"goal": {"status": "blocked"}, "criteria": []}
        assert goal_ctx.goal_needs_continuation(snapshot) is False

    def test_empty_goal(self):
        snapshot = {"goal": {}, "criteria": []}
        assert goal_ctx.goal_needs_continuation(snapshot) is False


class TestFormatGoalContext:
    def test_returns_string(self):
        snapshot = {
            "goal": {"goal_id": "g1", "status": "active", "objective": "test"},
            "criteria": [{"criterion_id": "c0", "text": "criterion text", "status": "pending"}],
            "evidence": [],
        }
        result = goal_ctx.format_goal_context(snapshot)
        assert isinstance(result, str)
        assert "g1" in result
        assert "current-research-goal" in result

    def test_includes_criteria(self):
        snapshot = {
            "goal": {"goal_id": "g1", "status": "active", "objective": "obj"},
            "criteria": [
                {"criterion_id": "c0", "text": "first criterion", "status": "pending"},
                {"criterion_id": "c1", "text": "second criterion", "status": "satisfied"},
            ],
            "evidence": [],
        }
        result = goal_ctx.format_goal_context(snapshot)
        assert "first criterion" in result
        assert "second criterion" in result

    def test_includes_evidence_count(self):
        snapshot = {
            "goal": {"goal_id": "g1", "status": "active", "objective": "obj"},
            "criteria": [],
            "evidence": [{"criterion_id": "c0"}, {"criterion_id": "c0"}],
            "evidence_count": 2,
        }
        result = goal_ctx.format_goal_context(snapshot)
        assert "evidence_count" in result
