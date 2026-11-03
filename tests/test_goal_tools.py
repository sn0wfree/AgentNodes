"""Tests for goal tools (LLM tool interface) and /goal chat intercept."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from strategy_research.kernel.agent.builtin_tools import build_default_registry
from strategy_research.kernel.agent.builtin_tools.goal_tools import (
    AddEvidenceTool,
    CompleteGoalTool,
    CreateGoalTool,
    GetGoalStatusTool,
    ListGoalsTool,
    register_goal_tools,
)
from strategy_research.core.agent.tools import ToolContext, ToolRegistry
from strategy_research.apps.goal import EvidenceInput, GoalStore

# ── Goal Tool Unit Tests ────────────────────────────────────────────


class TestGoalTools:
    """Test each goal tool in isolation with a temp DB."""

    @pytest.fixture
    def tmp_goal_db(self, tmp_path):
        """Create a GoalStore with a temp DB."""
        db_path = tmp_path / "test_goals.db"
        store = GoalStore(db_path=db_path)
        return store, db_path

    @pytest.fixture
    def workspace(self, tmp_path):
        """Create a minimal workspace."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "strategies").mkdir()
        return ws

    def test_create_goal_tool(self, workspace, tmp_goal_db):
        store, db_path = tmp_goal_db
        tool = CreateGoalTool()
        with patch("strategy_research.kernel.agent.builtin_tools.goal_tools._get_store", return_value=store):
            result = tool.execute(
                ctx=ToolContext(session_id="test-session"),
                objective="Analyze momentum factor IC",
                criteria=["IC > 0.05", "IR > 0.3"],
            )
        data = json.loads(result)
        assert data["status"] == "ok"
        assert "goal_id" in data
        assert data["objective"] == "Analyze momentum factor IC"

    def test_create_goal_tool_empty_objective(self, workspace, tmp_goal_db):
        store, db_path = tmp_goal_db
        tool = CreateGoalTool()
        with patch("strategy_research.kernel.agent.builtin_tools.goal_tools._get_store", return_value=store):
            result = tool.execute(
                ctx=ToolContext(session_id="test-session"),
                objective="",
            )
        data = json.loads(result)
        assert data["status"] == "error"
        assert "missing" in data["error"]

    def test_add_evidence_tool(self, workspace, tmp_goal_db):
        store, db_path = tmp_goal_db
        # Create a goal first
        store.replace_goal(
            session_id="test-session",
            objective="Test goal",
            criteria=["Criterion 1"],
        )
        tool = AddEvidenceTool()
        with patch("strategy_research.kernel.agent.builtin_tools.goal_tools._get_store", return_value=store):
            result = tool.execute(
                ctx=ToolContext(session_id="test-session"),
                text="IC mean = 0.06, IR = 0.5",
            )
        data = json.loads(result)
        assert data["status"] == "ok"
        assert "evidence_id" in data

    def test_add_evidence_tool_no_goal(self, workspace, tmp_goal_db):
        store, db_path = tmp_goal_db
        tool = AddEvidenceTool()
        with patch("strategy_research.kernel.agent.builtin_tools.goal_tools._get_store", return_value=store):
            result = tool.execute(
                ctx=ToolContext(session_id="test-session"),
                text="Some evidence",
            )
        data = json.loads(result)
        assert data["status"] == "error"
        assert "no active goal" in data["error"]

    def test_add_evidence_tool_empty_text(self, workspace, tmp_goal_db):
        store, db_path = tmp_goal_db
        store.replace_goal(
            session_id="test-session",
            objective="Test",
            criteria=["C1"],
        )
        tool = AddEvidenceTool()
        with patch("strategy_research.kernel.agent.builtin_tools.goal_tools._get_store", return_value=store):
            result = tool.execute(
                ctx=ToolContext(session_id="test-session"),
                text="",
            )
        data = json.loads(result)
        assert data["status"] == "error"
        assert "missing" in data["error"]

    def test_complete_goal_tool(self, workspace, tmp_goal_db):
        store, db_path = tmp_goal_db
        goal = store.replace_goal(
            session_id="test-session",
            objective="Test goal",
            criteria=["Criterion 1"],
        )
        # Get the criterion ID and add evidence linked to it
        criteria = store.list_criteria(goal.goal_id)
        assert len(criteria) == 1
        criterion_id = criteria[0].criterion_id
        store.append_evidence(
            session_id="test-session",
            goal_id=goal.goal_id,
            expected_goal_id=goal.goal_id,
            evidence=EvidenceInput(text="Evidence for criterion", criterion_id=criterion_id, source_type="test"),
        )
        tool = CompleteGoalTool()
        with patch("strategy_research.kernel.agent.builtin_tools.goal_tools._get_store", return_value=store):
            result = tool.execute(
                ctx=ToolContext(session_id="test-session"),
                recap="Research complete",
            )
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["goal_id"] == goal.goal_id

    def test_complete_goal_tool_no_goal(self, workspace, tmp_goal_db):
        store, db_path = tmp_goal_db
        tool = CompleteGoalTool()
        with patch("strategy_research.kernel.agent.builtin_tools.goal_tools._get_store", return_value=store):
            result = tool.execute(
                ctx=ToolContext(session_id="test-session"),
            )
        data = json.loads(result)
        assert data["status"] == "error"
        assert "no active goal" in data["error"]

    def test_add_evidence_auto_attaches_to_required_criteria(self, workspace, tmp_goal_db):
        store, db_path = tmp_goal_db
        goal = store.replace_goal(
            session_id="test-session",
            objective="Test goal",
            criteria=["Criterion 1", "Criterion 2"],
        )
        criteria = store.list_criteria(goal.goal_id)
        assert len(criteria) == 2
        tool = AddEvidenceTool()
        with patch("strategy_research.kernel.agent.builtin_tools.goal_tools._get_store", return_value=store):
            result = tool.execute(
                ctx=ToolContext(session_id="test-session"),
                text="IC mean = 0.06, IR = 0.5",
            )
        data = json.loads(result)
        assert data["status"] == "ok"
        attached = data.get("auto_attached_to") or []
        assert set(attached) == {c.criterion_id for c in criteria}
        evidence = store.list_evidence(goal.goal_id)
        assert len(evidence) == 2
        assert {ev.criterion_id for ev in evidence} == set(attached)

    def test_add_evidence_auto_attach_enables_complete(self, workspace, tmp_goal_db):
        store, db_path = tmp_goal_db
        store.replace_goal(
            session_id="test-session",
            objective="Test goal",
            criteria=["Criterion 1", "Criterion 2"],
        )
        with patch("strategy_research.kernel.agent.builtin_tools.goal_tools._get_store", return_value=store):
            result = AddEvidenceTool().execute(
                ctx=ToolContext(session_id="test-session"),
                text="All observations collected",
            )
            assert json.loads(result)["status"] == "ok"
            result = CompleteGoalTool().execute(
                ctx=ToolContext(session_id="test-session"),
                recap="Done",
            )
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["goal_status"] == "complete"

    def test_complete_goal_missing_evidence_fix_hint(self, workspace, tmp_goal_db):
        store, db_path = tmp_goal_db
        store.replace_goal(
            session_id="test-session",
            objective="Test goal",
            criteria=["Criterion 1"],
        )
        tool = CompleteGoalTool()
        with patch("strategy_research.kernel.agent.builtin_tools.goal_tools._get_store", return_value=store):
            result = tool.execute(
                ctx=ToolContext(session_id="test-session"),
            )
        data = json.loads(result)
        assert data["status"] == "error"
        assert "lacks evidence" in data["error"]
        assert "add_evidence" in data["fix"]
        assert "Criterion 1" in data["fix"]

    def test_get_goal_status_tool(self, workspace, tmp_goal_db):
        store, db_path = tmp_goal_db
        store.replace_goal(
            session_id="test-session",
            objective="Test goal",
            criteria=["C1", "C2"],
        )
        tool = GetGoalStatusTool()
        with patch("strategy_research.kernel.agent.builtin_tools.goal_tools._get_store", return_value=store):
            result = tool.execute(
                ctx=ToolContext(session_id="test-session"),
            )
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["has_goal"] is True
        assert data["goal_status"] == "active"
        assert data["objective"] == "Test goal"
        assert data["criteria_count"] == 2

    def test_get_goal_status_tool_no_goal(self, workspace, tmp_goal_db):
        store, db_path = tmp_goal_db
        tool = GetGoalStatusTool()
        with patch("strategy_research.kernel.agent.builtin_tools.goal_tools._get_store", return_value=store):
            result = tool.execute(
                ctx=ToolContext(session_id="test-session"),
            )
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["has_goal"] is False

    def test_add_evidence_single_criterion_auto_attach(self, workspace, tmp_goal_db):
        store, db_path = tmp_goal_db
        goal = store.replace_goal(
            session_id="test-session",
            objective="Test goal",
            criteria=["Only criterion"],
        )
        tool = AddEvidenceTool()
        with patch("strategy_research.kernel.agent.builtin_tools.goal_tools._get_store", return_value=store):
            result = tool.execute(
                ctx=ToolContext(session_id="test-session"),
                text="Free-form note",
            )
        data = json.loads(result)
        assert data["status"] == "ok"
        criteria = store.list_criteria(goal.goal_id)
        assert data["auto_attached_to"] == [criteria[0].criterion_id]
        evidence = store.list_evidence(goal.goal_id)
        assert len(evidence) == 1
        assert evidence[0].criterion_id == criteria[0].criterion_id

    def test_add_evidence_explicit_criterion_id_no_auto(self, workspace, tmp_goal_db):
        store, db_path = tmp_goal_db
        goal = store.replace_goal(
            session_id="test-session",
            objective="Test goal",
            criteria=["C1", "C2"],
        )
        criteria = store.list_criteria(goal.goal_id)
        target = criteria[0].criterion_id
        tool = AddEvidenceTool()
        with patch("strategy_research.kernel.agent.builtin_tools.goal_tools._get_store", return_value=store):
            result = tool.execute(
                ctx=ToolContext(session_id="test-session"),
                text="Only for C1",
                criterion_id=target,
            )
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data.get("auto_attached_to") is None
        evidence = store.list_evidence(goal.goal_id)
        assert len(evidence) == 1
        assert evidence[0].criterion_id == target

    def test_get_goal_status_progress_after_auto_attach(self, workspace, tmp_goal_db):
        store, db_path = tmp_goal_db
        store.replace_goal(
            session_id="test-session",
            objective="Test goal",
            criteria=["C1", "C2"],
        )
        with patch("strategy_research.kernel.agent.builtin_tools.goal_tools._get_store", return_value=store):
            result = AddEvidenceTool().execute(
                ctx=ToolContext(session_id="test-session"),
                text="Covers all",
            )
        data = json.loads(result)
        assert data["progress_percent"] == 100.0
        with patch("strategy_research.kernel.agent.builtin_tools.goal_tools._get_store", return_value=store):
            result = GetGoalStatusTool().execute(
                ctx=ToolContext(session_id="test-session"),
            )
        data = json.loads(result)
        assert data["progress_percent"] == 100.0

    def test_add_evidence_unknown_criterion_id_error(self, workspace, tmp_goal_db):
        store, db_path = tmp_goal_db
        store.replace_goal(
            session_id="test-session",
            objective="Test goal",
            criteria=["C1"],
        )
        tool = AddEvidenceTool()
        with patch("strategy_research.kernel.agent.builtin_tools.goal_tools._get_store", return_value=store):
            result = tool.execute(
                ctx=ToolContext(session_id="test-session"),
                text="Bad criterion",
                criterion_id="crit_nonexistent",
            )
        data = json.loads(result)
        assert data["status"] == "error"
        assert "criterion" in data["error"].lower()

    def test_list_goals_tool(self, workspace, tmp_goal_db):
        store, db_path = tmp_goal_db
        store.replace_goal(
            session_id="s1",
            objective="Goal 1",
            criteria=["C1"],
        )
        store.replace_goal(
            session_id="s2",
            objective="Goal 2",
            criteria=["C2"],
        )
        tool = ListGoalsTool()
        with patch("strategy_research.kernel.agent.builtin_tools.goal_tools._get_store", return_value=store):
            result = tool.execute(ctx=ToolContext())
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["count"] == 2

    def test_list_goals_tool_filter_by_session(self, workspace, tmp_goal_db):
        store, db_path = tmp_goal_db
        store.replace_goal(
            session_id="s1",
            objective="Goal 1",
            criteria=["C1"],
        )
        store.replace_goal(
            session_id="s2",
            objective="Goal 2",
            criteria=["C2"],
        )
        tool = ListGoalsTool()
        with patch("strategy_research.kernel.agent.builtin_tools.goal_tools._get_store", return_value=store):
            result = tool.execute(
                ctx=ToolContext(session_id="s1"),
            )
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["count"] == 1
        assert data["goals"][0]["objective"] == "Goal 1"


# ── Tool Registry Tests ─────────────────────────────────────────────


class TestGoalToolsRegistry:
    def test_goal_tools_registered(self):
        """Goal tools should be in the default registry."""
        reg = build_default_registry()
        goal_tool_names = [n for n in reg._tools if "goal" in n or "evidence" in n]
        assert "create_goal" in goal_tool_names
        assert "add_evidence" in goal_tool_names
        assert "complete_goal" in goal_tool_names
        assert "get_goal_status" in goal_tool_names
        assert "list_goals" in goal_tool_names

    def test_register_goal_tools_function(self):
        """register_goal_tools should add all 5 tools."""
        reg = ToolRegistry()
        register_goal_tools(reg)
        assert len(reg._tools) == 5
        assert "create_goal" in reg._tools
        assert "add_evidence" in reg._tools
        assert "complete_goal" in reg._tools
        assert "get_goal_status" in reg._tools
        assert "list_goals" in reg._tools

    def test_goal_tools_openai_schema(self):
        """Goal tools should produce valid OpenAI function schemas."""
        reg = build_default_registry()
        defs = reg.get_definitions()
        goal_defs = [d for d in defs if d["function"]["name"] in [
            "create_goal", "add_evidence", "complete_goal", "get_goal_status", "list_goals"
        ]]
        assert len(goal_defs) == 5
        for d in goal_defs:
            assert "type" in d
            assert d["type"] == "function"
            assert "function" in d
            assert "name" in d["function"]
            assert "description" in d["function"]
            assert "parameters" in d["function"]


# ── /goal Chat Intercept Tests ──────────────────────────────────────


class TestGoalChatIntercept:
    """Test the /goal command parsing logic."""

    def test_parse_goal_start(self):
        content = "/goal start Analyze momentum factor"
        parts = content.split(None, 2)
        subcmd = parts[1].lower() if len(parts) > 1 else "status"
        args = parts[2] if len(parts) > 2 else ""
        assert subcmd == "start"
        assert args == "Analyze momentum factor"

    def test_parse_goal_status(self):
        content = "/goal status"
        parts = content.split(None, 2)
        subcmd = parts[1].lower() if len(parts) > 1 else "status"
        args = parts[2] if len(parts) > 2 else ""
        assert subcmd == "status"
        assert args == ""

    def test_parse_goal_evidence(self):
        content = "/goal evidence IC mean = 0.06"
        parts = content.split(None, 2)
        subcmd = parts[1].lower() if len(parts) > 1 else "status"
        args = parts[2] if len(parts) > 2 else ""
        assert subcmd == "evidence"
        assert args == "IC mean = 0.06"

    def test_parse_goal_complete(self):
        content = "/goal complete"
        parts = content.split(None, 2)
        subcmd = parts[1].lower() if len(parts) > 1 else "status"
        args = parts[2] if len(parts) > 2 else ""
        assert subcmd == "complete"
        assert args == ""

    def test_parse_goal_complete_with_recap(self):
        content = "/goal complete Research done successfully"
        parts = content.split(None, 2)
        subcmd = parts[1].lower() if len(parts) > 1 else "status"
        args = parts[2] if len(parts) > 2 else ""
        assert subcmd == "complete"
        assert args == "Research done successfully"

    def test_parse_goal_cancel(self):
        content = "/goal cancel"
        parts = content.split(None, 2)
        subcmd = parts[1].lower() if len(parts) > 1 else "status"
        args = parts[2] if len(parts) > 2 else ""
        assert subcmd == "cancel"
        assert args == ""

    def test_parse_goal_help(self):
        content = "/goal help"
        parts = content.split(None, 2)
        subcmd = parts[1].lower() if len(parts) > 1 else "status"
        args = parts[2] if len(parts) > 2 else ""
        assert subcmd == "help"
        assert args == ""

    def test_parse_goal_bare(self):
        content = "/goal"
        parts = content.split(None, 2)
        subcmd = parts[1].lower() if len(parts) > 1 else "status"
        args = parts[2] if len(parts) > 2 else ""
        assert subcmd == "status"
        assert args == ""

    def test_goal_prefix_detection(self):
        assert "/goal start test".strip().startswith("/goal")
        assert "/goal".strip().startswith("/goal")
        assert "/goal status".strip().startswith("/goal")
        assert "not a goal command".strip().startswith("/goal") is False


# ── Integration: Goal Lifecycle via Tools ────────────────────────────


class TestGoalLifecycle:
    """Test full goal lifecycle using tools."""

    @pytest.fixture
    def workspace(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "strategies").mkdir()
        return ws

    def test_full_lifecycle(self, workspace, tmp_path):
        """Create goal → add evidence → complete."""
        db_path = tmp_path / "lifecycle_goals.db"
        store = GoalStore(db_path=db_path)

        with patch("strategy_research.kernel.agent.builtin_tools.goal_tools._get_store", return_value=store):
            # 1. Create goal
            create_tool = CreateGoalTool()
            result = json.loads(create_tool.execute(
                ctx=ToolContext(session_id="lifecycle-test"),
                objective="Test full lifecycle",
                criteria=["Criterion A", "Criterion B"],
            ))
            assert result["status"] == "ok"
            goal_id = result["goal_id"]

            # 2. Check status
            status_tool = GetGoalStatusTool()
            result = json.loads(status_tool.execute(
                ctx=ToolContext(session_id="lifecycle-test"),
            ))
            assert result["has_goal"] is True
            assert result["progress_percent"] == 0

            # 3. Add evidence for each criterion
            criteria = store.list_criteria(goal_id)
            criterion_ids = {c.text: c.criterion_id for c in criteria}
            ev_tool = AddEvidenceTool()
            result = json.loads(ev_tool.execute(
                ctx=ToolContext(session_id="lifecycle-test"),
                text="Evidence for criterion A",
                criterion_id=criterion_ids.get("Criterion A"),
            ))
            assert result["status"] == "ok"
            result = json.loads(ev_tool.execute(
                ctx=ToolContext(session_id="lifecycle-test"),
                text="Evidence for criterion B",
                criterion_id=criterion_ids.get("Criterion B"),
            ))
            assert result["status"] == "ok"

            # 4. Complete goal
            complete_tool = CompleteGoalTool()
            result = json.loads(complete_tool.execute(
                ctx=ToolContext(session_id="lifecycle-test"),
                recap="All criteria covered",
            ))
            assert result["status"] == "ok"

            # 5. Verify completed - after complete, get_current_goal returns None
            #    but the goal should still be in the list
            list_result = json.loads(ListGoalsTool().execute(
                ctx=ToolContext(session_id="lifecycle-test"),
            ))
            assert list_result["count"] == 1
            assert list_result["goals"][0]["goal_status"] == "complete"
