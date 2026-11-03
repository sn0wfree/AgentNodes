"""ToolsRail - right-side panel showing Goal progress + ITER + COMPACT.

Stage C: tool-call events now flow inline to TranscriptView (see
``TranscriptView.append_tool_call`` / ``update_tool_result``).
The rail no longer renders the tool timeline; it keeps only the
higher-level state — GOAL milestones, current ITER, and CONTEXT
COMPACTION events.

    ┌──────────────────────────────────────┐
    │ GOAL                                 │
    │ 研究A股低回撤量化策略                │
    │ [======------] 55%  3/5              │
    ├──────────────────────────────────────┤
    │ (tool calls shown inline)            │
    ├──────────────────────────────────────┤
    │ iter 2/10                            │
    └──────────────────────────────────────┘

A residual ``TIMELINE`` section is still rendered for ``compact``
events (and any future non-tool timeline entries), so the rail keeps
its structural integrity while delegating the per-tool line of
detail to the main transcript.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional

from textual.widgets import Static


@dataclass
class Milestone:
    """A single goal milestone/criteria."""
    name: str
    status: str  # "pending" | "running" | "done" | "failed" | "skipped"


@dataclass
class TimelineEntry:
    """A single entry in the unified timeline."""
    kind: str  # "tool" | "compact" | "info"
    label: str  # tool name or compact summary
    status: str  # "running" | "done" | "error" | "info"
    duration_ms: Optional[int] = None
    detail: str = ""  # last progress detail (shown as └ sub-line)
    iter: Optional[int] = None


def _format_duration(ms: Optional[int]) -> str:
    """Format duration: 1234 -> '1.2s', 500 -> '500ms'."""
    if ms is None:
        return "..."
    if ms >= 1000:
        return f"{ms / 1000:.1f}s"
    return f"{ms}ms"


def _milestone_icon(status: str) -> str:
    """Return icon for milestone status."""
    return {
        "pending": "○",
        "running": "⏳",
        "done": "✔",
        "failed": "✘",
        "skipped": "○",
    }.get(status, "○")


def _entry_icon(status: str) -> str:
    """Return icon for timeline entry status."""
    return {
        "running": "⏳",
        "done": "•",
        "error": "✘",
        "info": "•",
    }.get(status, "•")


class ToolsRail(Static):
    """Right-panel showing Goal progress + unified Timeline."""

    DEFAULT_CSS = """
    ToolsRail {
        width: 36;
        height: 1fr;
        border: round $primary;
        border-title-align: left;
        background: $primary 8%;
        padding: 0 1;
    }
    """

    BORDER_TITLE = "Tools"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._goal_title: str = ""
        self._goal_description: str = ""
        self._goal_progress: int = 0
        self._goal_criteria_total: int = 0
        self._goal_criteria_done: int = 0
        self._milestones: List[Milestone] = []
        self._timeline: List[TimelineEntry] = []
        self._max_milestones: int = 5
        self._max_timeline: int = 12
        self._iter: int = 0
        self._iter_max: int = 0

    def update_goal(
        self,
        *,
        title: str = "",
        description: str = "",
        progress: int = 0,
        criteria_total: int = 0,
        criteria_done: int = 0,
        milestones: Optional[List[Milestone]] = None,
    ) -> None:
        """Update goal progress information."""
        if title:
            self._goal_title = title
        if description:
            self._goal_description = description
        self._goal_progress = progress
        self._goal_criteria_total = criteria_total
        self._goal_criteria_done = criteria_done
        if milestones is not None:
            self._milestones = milestones
        self._refresh()

    def set_iter(self, iteration: int, max_iterations: int = 0) -> None:
        """Update the current iteration counter."""
        self._iter = iteration
        if max_iterations:
            self._iter_max = max_iterations
        self._refresh()

    def handle_event(self, event_type: str, data: dict) -> None:
        """Handle events from AgentLoop (Stage C: tools moved to TranscriptView).

        Only ``compact`` and ``iter_start`` are processed here. Tool
        events (``tool_call``, ``tool_result``, ``tool_progress``,
        ``tool_heartbeat``) are routed to ``TranscriptView`` for inline
        rendering — see ``ResearchApp.route_agent_event``.
        """
        if event_type == "compact":
            before = data.get("before_tokens", "?")
            after = data.get("after_tokens", "?")
            self._timeline.append(TimelineEntry(
                kind="compact",
                label=f"compacted ({before}k→{after}k)",
                status="info",
                iter=data.get("iter"),
            ))
            self._trim_timeline()
            self._refresh()
        elif event_type == "iter_start":
            self.set_iter(data.get("iteration", 0), data.get("max_iterations", 0))

    def clear_timeline(self) -> None:
        """Clear all timeline entries."""
        self._timeline.clear()
        self._refresh()

    def _trim_timeline(self) -> None:
        if len(self._timeline) > self._max_timeline:
            self._timeline = self._timeline[-self._max_timeline:]

    def _refresh(self) -> None:
        """Re-render the entire rail content."""
        parts: List[str] = []
        parts.append(self._render_goal())
        parts.append("[dim]──────────────────────────────[/dim]")
        parts.append(self._render_timeline())
        parts.append(self._render_footer())
        content = "\n".join(parts)
        self.update(content)

    def _render_goal(self) -> str:
        """Render the goal section."""
        lines: List[str] = []
        lines.append("[bold]GOAL[/bold]")

        if self._goal_description:
            desc = self._goal_description[:20]
            if len(self._goal_description) > 20:
                desc += "..."
            lines.append(desc)

        if self._goal_criteria_total > 0:
            bar = _progress_bar(self._goal_progress, 100, width=12)
            lines.append(
                f"{bar} {self._goal_progress}%  "
                f"{self._goal_criteria_done}/{self._goal_criteria_total}"
            )

        visible = self._milestones[:self._max_milestones]
        for m in visible:
            icon = _milestone_icon(m.status)
            name = m.name[:15] + "..." if len(m.name) > 15 else m.name
            lines.append(f"{icon} {name}  {m.status}")

        if len(self._milestones) > self._max_milestones:
            lines.append(
                f"[dim]↑↓ scroll  {self._max_milestones}/{len(self._milestones)} shown[/dim]"
            )

        return "\n".join(lines)

    def _render_timeline(self) -> str:
        """Render the non-tool timeline (currently just ``compact`` events).

        Tool calls themselves are rendered inline in TranscriptView; this
        section now shows only meta-events (e.g. context compression).
        """
        lines: List[str] = []
        lines.append("[bold]TIMELINE[/bold]")

        for entry in self._timeline[-self._max_timeline:]:
            icon = _entry_icon(entry.status)
            if entry.status == "running":
                lines.append(f"{icon} {entry.label} ...")
            else:
                duration = _format_duration(entry.duration_ms)
                lines.append(f"{icon} {entry.label} {duration}")
            if entry.detail:
                lines.append(f"  [dim]└ {entry.detail}[/dim]")

        if not self._timeline:
            lines.append("[dim](tool calls shown inline)[/dim]")

        return "\n".join(lines)

    def _render_footer(self) -> str:
        """Render the footer with iter counter + (optional) timeline summary."""
        parts: List[str] = []
        if self._iter_max > 0:
            parts.append(f"iter {self._iter}/{self._iter_max}")
        # Compact-event count (only non-tool entries; tool totals come
        # from TranscriptView / App._tool_total via StatusHeader).
        non_tool = [t for t in self._timeline if t.kind != "tool"]
        if non_tool:
            parts.append(f"{len(non_tool)} meta")
        return "  ".join(parts) if parts else ""


def _progress_bar(value: int, max_value: int, width: int = 10) -> str:
    """Render a progress bar."""
    if max_value <= 0:
        return "[" + "-" * width + "]"
    ratio = min(value / max_value, 1.0)
    filled = int(ratio * width)
    return "[" + "=" * filled + "-" * (width - filled) + "]"


__all__ = ["ToolsRail", "Milestone", "TimelineEntry"]
