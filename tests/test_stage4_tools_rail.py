"""Tests for ToolsRail — Stage C updated.

After Stage C, tool events flow inline to TranscriptView; the rail
keeps only ``compact`` events and the iter counter.

This file supersedes the Stage 4 tool-timeline tests with Stage C
expectations:

* handle_event("tool_call" / "tool_result" / "tool_progress" /
  tool_heartbeat) are silent no-ops on the rail.
* handle_event("compact") still appends a TimelineEntry.
* set_iter and TimelineEntry defaults unchanged.
"""
from __future__ import annotations

from agentnodes.interfaces.cli.tui.widgets.tools_rail import (
    TimelineEntry,
    ToolsRail,
)


class TestTimelineEntry:
    def test_default_fields(self):
        e = TimelineEntry(kind="tool", label="read", status="running")
        assert e.duration_ms is None
        assert e.detail == ""
        assert e.iter is None


class TestToolsRailHandleEvent:
    """Stage C: tool_* events are silent; only compact + iter_start remain."""

    def _rail(self) -> ToolsRail:
        return ToolsRail()

    def test_tool_call_is_no_op(self):
        rail = self._rail()
        rail.handle_event("tool_call", {"tool": "read", "iter": 1})
        assert rail._timeline == []

    def test_tool_result_is_no_op(self):
        rail = self._rail()
        rail.handle_event("tool_result", {
            "tool": "read", "status": "ok", "elapsed_ms": 500,
        })
        assert rail._timeline == []

    def test_tool_progress_is_no_op(self):
        rail = self._rail()
        rail.handle_event("tool_progress", {"tool": "x", "message": "50% done"})
        assert rail._timeline == []

    def test_tool_heartbeat_is_no_op(self):
        rail = self._rail()
        rail.handle_event("tool_heartbeat", {"tool": "x", "detail": "still working"})
        assert rail._timeline == []

    def test_compact_adds_entry(self):
        rail = self._rail()
        rail.handle_event("compact", {
            "layer": "microcompact",
            "iteration": 1,
            "before_tokens": 12000,
            "after_tokens": 4000,
        })
        assert len(rail._timeline) == 1
        assert rail._timeline[0].kind == "compact"
        assert "compacted" in rail._timeline[0].label

    def test_iter_start_updates_iter_counter(self):
        rail = self._rail()
        rail.handle_event("iter_start", {"iteration": 3, "max_iterations": 10})
        assert rail._iter == 3
        assert rail._iter_max == 10

    def test_clear_timeline(self):
        rail = self._rail()
        rail.handle_event("compact", {"before_tokens": 1, "after_tokens": 1})
        rail.clear_timeline()
        assert len(rail._timeline) == 0


class TestToolsRailRender:
    """Stage C: empty timeline shows the inline-tool hint, not '(idle)'."""

    def _rail(self) -> ToolsRail:
        return ToolsRail()

    def test_render_empty_timeline_shows_inline_hint(self):
        rail = self._rail()
        content = rail._render_timeline()
        assert "(tool calls shown inline)" in content

    def test_render_compact_entry_appears(self):
        rail = self._rail()
        rail.handle_event("compact", {"before_tokens": 12, "after_tokens": 4})
        content = rail._render_timeline()
        assert "compacted" in content

    def test_render_footer_shows_iter(self):
        rail = self._rail()
        rail.set_iter(3, 10)
        content = rail._render_footer()
        assert "iter 3/10" in content
