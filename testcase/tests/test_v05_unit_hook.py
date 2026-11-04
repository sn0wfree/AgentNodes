"""Phase 4 - v0.5 unit tests: GoalWorkflowHook callbacks and internals.

Fills coverage gaps for:
  - Properties: name, completed, evidence_count
  - _extract_output: None, dict, object, string
  - on_layer_start: event emission
  - on_agent_complete: evidence collection, run_store, event bus
  - on_layer_complete: event emission, criteria check
  - _check_all_criteria_covered: various scenarios
  - should_stop: completed vs cancelled vs neither
"""
from __future__ import annotations

from unittest.mock import MagicMock

from strategy_research.apps.goal.workflow_hook import GoalWorkflowHook

# ═══════════════════════════════════════════════════════════════════════
# Properties
# ═══════════════════════════════════════════════════════════════════════


class TestHookProperties:
    def test_name(self):
        assert GoalWorkflowHook("s", "g", {}, None).name == "GoalWorkflowHook"

    def test_completed_default_false(self):
        assert GoalWorkflowHook("s", "g", {}, None).completed is False

    def test_evidence_count_default_zero(self):
        assert GoalWorkflowHook("s", "g", {}, None).evidence_count == 0


# ═══════════════════════════════════════════════════════════════════════
# _extract_output
# ═══════════════════════════════════════════════════════════════════════


class TestHookExtractOutput:
    def setup_method(self):
        self.hook = GoalWorkflowHook("s", "g", {}, None)

    def test_none(self):
        assert self.hook._extract_output(None) == ""

    def test_dict_answer(self):
        assert self.hook._extract_output({"answer": "out"}) == "out"

    def test_dict_output_key(self):
        assert self.hook._extract_output({"output": "out"}) == "out"

    def test_dict_answer_precedence(self):
        assert self.hook._extract_output({"answer": "a", "output": "b"}) == "a"

    def test_object_with_output(self):
        obj = MagicMock()
        obj.output = "obj_out"
        assert self.hook._extract_output(obj) == "obj_out"

    def test_object_with_none_output(self):
        obj = MagicMock()
        obj.output = None
        assert self.hook._extract_output(obj) == ""

    def test_raw_string(self):
        assert self.hook._extract_output("raw") == "raw"


# ═══════════════════════════════════════════════════════════════════════
# on_layer_start
# ═══════════════════════════════════════════════════════════════════════


class TestHookOnLayerStart:
    def test_emits_event(self):
        bus = MagicMock()
        hook = GoalWorkflowHook("s", "g", {}, None, event_bus=bus)
        hook.on_layer_start(2, ["a", "b"], {})
        bus.emit.assert_called_once_with("layer_start", layer=2, agents=["a", "b"])

    def test_no_bus_no_crash(self):
        GoalWorkflowHook("s", "g", {}, None).on_layer_start(0, ["a"], {})


# ═══════════════════════════════════════════════════════════════════════
# on_agent_complete
# ═══════════════════════════════════════════════════════════════════════


class TestHookOnAgentComplete:
    def _mock_store(self):
        store = MagicMock()
        store.get_current_snapshot.return_value = {
            "criteria": [{"criterion_id": "c0"}],
        }
        return store

    def test_no_evidence_map_entry(self):
        hook = GoalWorkflowHook("s", "g", {}, self._mock_store())
        hook.on_agent_complete("unknown", {"answer": "x" * 20}, {})
        assert hook.evidence_count == 0

    def test_collects_evidence(self):
        hook = GoalWorkflowHook("s", "g", {"a": 0}, self._mock_store())
        hook.on_agent_complete("a", {"answer": "x" * 20}, {})
        assert hook.evidence_count == 1

    def test_skips_empty_output(self):
        hook = GoalWorkflowHook("s", "g", {"a": 0}, self._mock_store())
        hook.on_agent_complete("a", {"answer": ""}, {})
        assert hook.evidence_count == 0

    def test_emits_agent_complete_event(self):
        bus = MagicMock()
        hook = GoalWorkflowHook("s", "g", {"a": 0}, self._mock_store(), event_bus=bus)
        hook.on_agent_complete("a", {"answer": "x" * 20}, {})
        bus.emit.assert_any_call("agent_complete", agent_id="a")

    def test_saves_to_run_store(self):
        rs = MagicMock()
        hook = GoalWorkflowHook("s", "g", {"a": 0}, self._mock_store(), run_store=rs, run_id="r1")
        hook.on_agent_complete("a", {"answer": "x" * 20}, {})
        rs.save_agent_output.assert_called_once()

    def test_run_store_exception_swallowed(self):
        rs = MagicMock()
        rs.save_agent_output.side_effect = RuntimeError("disk full")
        hook = GoalWorkflowHook("s", "g", {"a": 0}, self._mock_store(), run_store=rs, run_id="r1")
        hook.on_agent_complete("a", {"answer": "x" * 20}, {})
        # Should not raise

    def test_multiple_agents_increment_count(self):
        store = MagicMock()
        store.get_current_snapshot.return_value = {
            "criteria": [{"criterion_id": "c0"}, {"criterion_id": "c1"}],
        }
        hook = GoalWorkflowHook("s", "g", {"a": 0, "b": 1}, store)
        hook.on_agent_complete("a", {"answer": "x" * 20}, {})
        hook.on_agent_complete("b", {"answer": "y" * 20}, {})
        assert hook.evidence_count == 2


# ═══════════════════════════════════════════════════════════════════════
# on_layer_complete
# ═══════════════════════════════════════════════════════════════════════


class TestHookOnLayerComplete:
    def _store_with(self, criteria, evidence):
        store = MagicMock()
        store.get_current_snapshot.return_value = {
            "criteria": criteria,
            "evidence": evidence,
        }
        return store

    def test_emits_event(self):
        bus = MagicMock()
        store = self._store_with([{"criterion_id": "c0"}], [])
        hook = GoalWorkflowHook("s", "g", {}, store, event_bus=bus)
        hook.on_layer_complete(0, ["a"], {})
        bus.emit.assert_any_call("layer_complete", layer=0, agents=["a"])

    def test_no_bus_no_crash(self):
        store = self._store_with([{"criterion_id": "c0"}], [])
        GoalWorkflowHook("s", "g", {}, store).on_layer_complete(0, ["a"], {})


# ═══════════════════════════════════════════════════════════════════════
# _check_all_criteria_covered
# ═══════════════════════════════════════════════════════════════════════


class TestHookCheckAllCriteriaCovered:
    def _hook_with(self, criteria, evidence):
        store = MagicMock()
        store.get_current_snapshot.return_value = {
            "criteria": criteria,
            "evidence": evidence,
        }
        return GoalWorkflowHook("s", "g", {}, store)

    def test_all_covered(self):
        hook = self._hook_with(
            [{"criterion_id": "c0", "required": True}, {"criterion_id": "c1", "required": True}],
            [{"criterion_id": "c0"}, {"criterion_id": "c1"}],
        )
        assert hook._check_all_criteria_covered() is True

    def test_not_all_covered(self):
        hook = self._hook_with(
            [{"criterion_id": "c0", "required": True}, {"criterion_id": "c1", "required": True}],
            [{"criterion_id": "c0"}],
        )
        assert hook._check_all_criteria_covered() is False

    def test_optional_criteria_skipped(self):
        hook = self._hook_with(
            [{"criterion_id": "c0", "required": True}, {"criterion_id": "c1", "required": False}],
            [{"criterion_id": "c0"}],
        )
        assert hook._check_all_criteria_covered() is True

    def test_no_snapshot(self):
        store = MagicMock()
        store.get_current_snapshot.return_value = None
        hook = GoalWorkflowHook("s", "g", {}, store)
        assert hook._check_all_criteria_covered() is False

    def test_no_criteria(self):
        hook = self._hook_with([], [])
        assert hook._check_all_criteria_covered() is True


# ═══════════════════════════════════════════════════════════════════════
# should_stop
# ═══════════════════════════════════════════════════════════════════════


class TestHookShouldStop:
    def _make_hook(self, completed, cancelled):
        hook = GoalWorkflowHook.__new__(GoalWorkflowHook)
        hook._completed = completed
        hook._runner = MagicMock()
        hook._runner._state = MagicMock()
        hook._runner._state.cancelled = cancelled
        return hook

    def test_stop_when_completed(self):
        assert self._make_hook(completed=True, cancelled=False).should_stop() is True

    def test_stop_when_cancelled(self):
        assert self._make_hook(completed=False, cancelled=True).should_stop() is True

    def test_no_stop_when_neither(self):
        assert self._make_hook(completed=False, cancelled=False).should_stop() is False

    def test_stop_when_both(self):
        assert self._make_hook(completed=True, cancelled=True).should_stop() is True

    def test_no_runner(self):
        hook = GoalWorkflowHook.__new__(GoalWorkflowHook)
        hook._completed = False
        hook._runner = None
        assert hook.should_stop() is False
