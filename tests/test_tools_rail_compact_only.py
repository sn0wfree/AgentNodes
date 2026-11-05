"""Tests for Stage C3: ToolsRail only handles compact + iter_start.

After Stage C, tool events flow inline to TranscriptView; the rail
keeps only ``compact`` (context-compression) events and the iter
counter. Verify:

* handle_event silently ignores tool_call/tool_result/tool_progress/
  tool_heartbeat (no exception, no timeline entry).
* handle_event still appends TimelineEntry for compact events.
* set_iter still updates the iter counter.
* Empty timeline shows the "(tool calls shown inline)" hint.
"""
from __future__ import annotations

from agentnodes.interfaces.cli.tui.widgets.tools_rail import (
    TimelineEntry,
    ToolsRail,
)


class TestRailIgnoresToolEvents:
    """Tool events are no-ops on the rail (Stage C)."""

    def _rail(self) -> ToolsRail:
        # Bypass mount — we only exercise handle_event() state mutation.
        rail = ToolsRail.__new__(ToolsRail)
        rail._goal_title = ""
        rail._goal_description = ""
        rail._goal_progress = 0
        rail._goal_criteria_total = 0
        rail._goal_criteria_done = 0
        rail._milestones = []
        rail._timeline = []
        rail._max_milestones = 5
        rail._max_timeline = 12
        rail._iter = 0
        rail._iter_max = 0
        # Skip _refresh (Static.update requires mount)
        rail._refresh = lambda: None
        return rail

    def test_tool_call_ignored(self):
        rail = self._rail()
        rail.handle_event("tool_call", {
            "tool": "read", "args": {"path": "x"}, "call_id": "c1",
        })
        assert rail._timeline == []

    def test_tool_result_ignored(self):
        rail = self._rail()
        rail.handle_event("tool_result", {
            "tool": "read", "call_id": "c1",
            "ok": True, "elapsed_ms": 320,
        })
        assert rail._timeline == []

    def test_tool_progress_ignored(self):
        rail = self._rail()
        rail.handle_event("tool_progress", {"detail": "50% done"})
        assert rail._timeline == []

    def test_tool_heartbeat_ignored(self):
        rail = self._rail()
        rail.handle_event("tool_heartbeat", {"detail": "still running"})
        assert rail._timeline == []

    def test_unknown_event_ignored(self):
        rail = self._rail()
        rail.handle_event("made_up_event", {})
        assert rail._timeline == []


class TestRailKeepsCompact:
    """compact events still produce TimelineEntry rows."""

    def test_compact_event_appends_entry(self):
        rail = ToolsRail.__new__(ToolsRail)
        rail._goal_title = ""
        rail._goal_description = ""
        rail._goal_progress = 0
        rail._goal_criteria_total = 0
        rail._goal_criteria_done = 0
        rail._milestones = []
        rail._timeline = []
        rail._max_milestones = 5
        rail._max_timeline = 12
        rail._iter = 0
        rail._iter_max = 0
        rail._refresh = lambda: None

        rail.handle_event("compact", {
            "before_tokens": 12, "after_tokens": 4, "iter": 2,
        })
        assert len(rail._timeline) == 1
        entry = rail._timeline[0]
        assert entry.kind == "compact"
        assert entry.status == "info"
        assert "12k" in entry.label and "4k" in entry.label
        assert entry.iter == 2

    def test_compact_then_tool_call_does_not_mix(self):
        rail = ToolsRail.__new__(ToolsRail)
        rail._goal_title = ""
        rail._goal_description = ""
        rail._goal_progress = 0
        rail._goal_criteria_total = 0
        rail._goal_criteria_done = 0
        rail._milestones = []
        rail._timeline = []
        rail._max_milestones = 5
        rail._max_timeline = 12
        rail._iter = 0
        rail._iter_max = 0
        rail._refresh = lambda: None

        rail.handle_event("compact", {"before_tokens": 10, "after_tokens": 4})
        rail.handle_event("tool_call", {"tool": "x"})
        rail.handle_event("tool_result", {"tool": "x"})
        # Only the compact event is recorded
        assert len(rail._timeline) == 1
        assert rail._timeline[0].kind == "compact"


class TestIterStillUpdates:
    def test_iter_start_updates_iter_counter(self):
        rail = ToolsRail.__new__(ToolsRail)
        rail._goal_title = ""
        rail._goal_description = ""
        rail._goal_progress = 0
        rail._goal_criteria_total = 0
        rail._goal_criteria_done = 0
        rail._milestones = []
        rail._timeline = []
        rail._max_milestones = 5
        rail._max_timeline = 12
        rail._iter = 0
        rail._iter_max = 0
        rail._refresh = lambda: None

        rail.handle_event("iter_start", {"iteration": 3, "max_iterations": 10})
        assert rail._iter == 3
        assert rail._iter_max == 10


class TestTimelineRendering:
    """Render-text contains expected strings (Stage C hint + iter)."""

    def _rail_with_iter(self) -> ToolsRail:
        rail = ToolsRail.__new__(ToolsRail)
        rail._goal_title = ""
        rail._goal_description = ""
        rail._goal_progress = 0
        rail._goal_criteria_total = 0
        rail._goal_criteria_done = 0
        rail._milestones = []
        rail._timeline = []
        rail._max_milestones = 5
        rail._max_timeline = 12
        rail._iter = 2
        rail._iter_max = 10
        return rail

    def test_empty_timeline_shows_inline_hint(self):
        rail = self._rail_with_iter()
        text = rail._render_timeline()
        assert "(tool calls shown inline)" in text

    def test_compact_entry_renders(self):
        rail = self._rail_with_iter()
        rail._timeline.append(TimelineEntry(
            kind="compact", label="compacted (12k→4k)", status="info",
        ))
        text = rail._render_timeline()
        assert "compacted (12k→4k)" in text
        assert "(tool calls shown inline)" not in text

    def test_footer_shows_iter(self):
        rail = self._rail_with_iter()
        text = rail._render_footer()
        assert "iter 2/10" in text

    def test_footer_no_tool_total(self):
        """Tool totals come from App._tool_total, not rail."""
        rail = self._rail_with_iter()
        # Even with entries (compact) the footer should not say "tools"
        rail._timeline.append(TimelineEntry(
            kind="compact", label="compacted (12k→4k)", status="info",
        ))
        text = rail._render_footer()
        assert "tools" not in text
        assert "1 meta" in text  # non-tool entry counter
