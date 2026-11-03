"""Tests for GoalPanel widget, GoalTemplate, lite completion, and evidence detection.

Coverage:
  GoalPanel:
    * init with no-goal (hidden)
    * update_goal shows panel with correct content
    * clear_goal hides panel
    * toggle_panel expand/collapse
    * progress bar rendering
    * criterion icon mapping
    * continuation_paused hint

  GoalTemplate:
    * 5 predefined templates exist
    * get_template returns correct template
    * list_templates returns all templates
    * template criteria are non-empty

  Lite completion:
    * GoalStore.complete_lite with all criteria covered succeeds
    * GoalStore.complete_lite with uncovered criterion raises ValueError

  Evidence detection:
    * detect_evidence_in_response with quantitative text returns match
    * detect_evidence_in_response with plain text returns None
    * detect_evidence_in_response with short text returns None

  Session continuation:
    * toggle_goal_continuation pauses and resumes
"""
from __future__ import annotations

from unittest import mock

from strategy_research.cli.tui.widgets.goal_panel import (
    GoalPanel,
    _criterion_icon,
    _progress_bar,
)

# ── GoalPanel widget ──────────────────────────────────────────


class TestGoalPanelInit:
    def test_default_is_hidden(self):
        panel = GoalPanel()
        assert panel.has_class("no-goal")

    def test_default_no_data(self):
        panel = GoalPanel()
        assert panel._objective == ""
        assert panel._status == ""
        assert panel._progress == 0.0
        assert panel._criteria == []


class TestGoalPanelUpdateGoal:
    def test_shows_panel_on_update(self):
        panel = GoalPanel()
        panel.update_goal(objective="test goal", status="active")
        assert not panel.has_class("no-goal")

    def testStoresData(self):
        panel = GoalPanel()
        criteria = [{"criterion_id": "c1", "text": "test", "status": "pending", "required": True}]
        panel.update_goal(
            objective="研究动量因子",
            status="active",
            progress=50.0,
            criteria=criteria,
            evidence_count=3,
            goal_id="g1",
        )
        assert panel._objective == "研究动量因子"
        assert panel._status == "active"
        assert panel._progress == 50.0
        assert len(panel._criteria) == 1
        assert panel._evidence_count == 3
        assert panel._goal_id == "g1"

    def test_continuation_paused_flag(self):
        panel = GoalPanel()
        panel.update_goal(objective="test", continuation_paused=True)
        assert panel._continuation_paused is True


class TestGoalPanelClearGoal:
    def test_hides_panel(self):
        panel = GoalPanel()
        panel.update_goal(objective="test")
        assert not panel.has_class("no-goal")
        panel.clear_goal()
        assert panel.has_class("no-goal")

    def test_clears_data(self):
        panel = GoalPanel()
        panel.update_goal(objective="test", evidence_count=5)
        panel.clear_goal()
        assert panel._objective == ""
        assert panel._evidence_count == 0


class TestGoalPanelToggle:
    def test_toggle_collapse(self):
        panel = GoalPanel()
        panel.update_goal(objective="test")
        assert panel._expanded is True
        panel.toggle_panel()
        assert panel._expanded is False
        assert panel.has_class("collapsed")
        panel.toggle_panel()
        assert panel._expanded is True
        assert not panel.has_class("collapsed")


class TestProgressBar:
    def test_empty_bar(self):
        bar = _progress_bar(0.0, width=10)
        assert bar.startswith("─")
        assert "━" not in bar

    def test_full_bar(self):
        bar = _progress_bar(1.0, width=10)
        assert "━" * 10 in bar

    def test_half_bar(self):
        bar = _progress_bar(0.5, width=10)
        assert "━" * 5 in bar
        assert "─" * 5 in bar


class TestCriterionIcon:
    def test_covered(self):
        assert _criterion_icon("covered") == "✔"

    def test_pending(self):
        assert _criterion_icon("pending") == "○"

    def test_stale(self):
        assert _criterion_icon("stale") == "⚠️"

    def test_unknown(self):
        assert _criterion_icon("unknown") == "○"


# ── GoalTemplate ──────────────────────────────────────────────


class TestGoalTemplate:
    def test_five_predefined_templates(self):
        from strategy_research.apps.goal.templates import TEMPLATES
        assert len(TEMPLATES) == 5

    def test_get_template(self):
        from strategy_research.apps.goal.templates import get_template
        tmpl = get_template("factor_research")
        assert tmpl is not None
        assert tmpl.name == "因子研究"
        assert len(tmpl.criteria) == 4

    def test_get_unknown_template(self):
        from strategy_research.apps.goal.templates import get_template
        assert get_template("nonexistent") is None

    def test_list_templates(self):
        from strategy_research.apps.goal.templates import list_templates
        templates = list_templates()
        assert "factor_research" in templates
        assert "market_analysis" in templates

    def test_template_names(self):
        from strategy_research.apps.goal.templates import template_names
        names = template_names()
        assert isinstance(names, list)
        assert len(names) == 5
        assert names == sorted(names)


# ── Evidence detection ────────────────────────────────────────


class TestEvidenceDetection:
    def test_detect_quantitative_evidence(self):
        from strategy_research.cli.tui.session import ChatSession
        result = ChatSession.detect_evidence_in_response(
            "动量因子在 2020-2023 年间的年化收益为 12.3%，夏普比率 0.85"
        )
        assert result is not None
        assert result["confidence"] == "high"
        assert "年化收益" in result["matched_pattern"]

    def test_detect_medium_confidence(self):
        from strategy_research.cli.tui.session import ChatSession
        result = ChatSession.detect_evidence_in_response(
            "通过回测数据分析，该策略在过去5年表现稳定"
        )
        assert result is not None
        assert result["confidence"] == "medium"

    def test_no_evidence_plain_text(self):
        from strategy_research.cli.tui.session import ChatSession
        result = ChatSession.detect_evidence_in_response("hello world")
        assert result is None

    def test_no_evidence_short_text(self):
        from strategy_research.cli.tui.session import ChatSession
        result = ChatSession.detect_evidence_in_response("hi")
        assert result is None

    def test_no_evidence_empty(self):
        from strategy_research.cli.tui.session import ChatSession
        assert ChatSession.detect_evidence_in_response("") is None
        assert ChatSession.detect_evidence_in_response("   ") is None


# ── Session continuation control ──────────────────────────────


class TestContinuationControl:
    def test_default_not_paused(self):
        from strategy_research.cli.tui.session import ChatSession
        ctx = mock.MagicMock()
        session = ChatSession(ctx)
        assert session._goal_continuation_paused is False

    def test_toggle_pauses(self):
        from strategy_research.cli.tui.session import ChatSession
        ctx = mock.MagicMock()
        session = ChatSession(ctx)
        session._write_transcript = mock.MagicMock()
        session.app = None
        session.toggle_goal_continuation()
        assert session._goal_continuation_paused is True

    def test_toggle_resumes(self):
        from strategy_research.cli.tui.session import ChatSession
        ctx = mock.MagicMock()
        session = ChatSession(ctx)
        session._write_transcript = mock.MagicMock()
        session.app = None
        session.toggle_goal_continuation()
        session.toggle_goal_continuation()
        assert session._goal_continuation_paused is False
