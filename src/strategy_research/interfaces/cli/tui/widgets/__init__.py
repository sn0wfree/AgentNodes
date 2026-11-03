"""Textual widget package for the QuantNodes-Research TUI.

Each widget is a thin Textual-native wrapper over existing CLI building
blocks (banner renderer, tool event formatter, etc.). The goal is that
the widgets expose a small ``update_*`` / ``write_*`` API and rely on
Rich ``Text`` / ``Renderable`` instances so the legacy code path stays
unchanged.
"""
from __future__ import annotations

from strategy_research.interfaces.cli.tui.widgets.banner import Banner
from strategy_research.interfaces.cli.tui.widgets.dag_view import DAGView
from strategy_research.interfaces.cli.tui.widgets.goal_panel import GoalPanel
from strategy_research.interfaces.cli.tui.widgets.hint_footer import HintFooter
from strategy_research.interfaces.cli.tui.widgets.input_bar import ChatInput
from strategy_research.interfaces.cli.tui.widgets.mode_bar import ModeBar
from strategy_research.interfaces.cli.tui.widgets.rail import ActivityRail
from strategy_research.interfaces.cli.tui.widgets.resume_dialog import ResumeOrNewModal
from strategy_research.interfaces.cli.tui.widgets.sidebar import CommandSidebar
from strategy_research.interfaces.cli.tui.widgets.status_header import StatusHeader
from strategy_research.interfaces.cli.tui.widgets.streaming_text import StreamingText
from strategy_research.interfaces.cli.tui.widgets.thinking_spinner import ThinkingSpinner
from strategy_research.interfaces.cli.tui.widgets.tools_rail import Milestone, TimelineEntry, ToolsRail
from strategy_research.interfaces.cli.tui.widgets.transcript import TranscriptView

__all__ = [
    "Banner",
    "ChatInput",
    "CommandSidebar",
    "DAGView",
    "GoalPanel",
    "HintFooter",
    "ModeBar",
    "ActivityRail",
    "ResumeOrNewModal",
    "StatusHeader",
    "StreamingText",
    "ThinkingSpinner",
    "ToolsRail",
    "Milestone",
    "TimelineEntry",
    "TranscriptView",
]
