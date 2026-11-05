"""GoalPanel — compact goal status widget between ModeBar and Transcript.

Shows the active research goal's progress, criteria status, and
evidence count.  Collapsible via Ctrl+G (toggle_fold_panel).

Layout (expanded):
    🎯 研究动量因子在 A 股市场的有效性
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 66% (2/3)
    ✔ 1. 定义研究论点和标的池
    ✔ 2. 收集市场数据和基准证据
    ○ 3. 记录风险提示和非建议边界
    📎 4 evidence  │  ⏸ Ctrl+G 暂停续跑

Layout (collapsed):
    🎯 研究动量因子...  66% (2/3)  📎 4  │  Ctrl+G 展开
"""
from __future__ import annotations

from typing import Any

from textual.widgets import Static

# Status icon mapping (goal status → icon)
_STATUS_ICONS = {
    "active": "🟢",
    "paused": "⏸",
    "waiting_user": "⏳",
    "needs_refresh": "🔄",
    "insufficient_evidence": "⚠️",
    "compliance_blocked": "🚫",
    "blocked": "❌",
    "budget_limited": "💰",
    "usage_limited": "📊",
    "complete": "✅",
    "cancelled": "⛔",
    "superseded": "📋",
}

# Criterion status icons
_CRITERION_ICONS = {
    "covered": "✔",
    "complete": "✔",
    "satisfied": "✔",
    "pending": "○",
    "open": "○",
    "unsatisfied": "○",
    "missing": "○",
    "stale": "⚠️",
    "too_weak": "⚠️",
}


def _progress_bar(ratio: float, width: int = 20) -> str:
    """Render a Unicode progress bar."""
    filled = int(ratio * width)
    empty = width - filled
    return "━" * filled + "─" * empty


def _criterion_icon(status: str) -> str:
    """Return the icon for a criterion status."""
    return _CRITERION_ICONS.get(status.lower(), "○")


class GoalPanel(Static):
    """Compact goal status widget with progress and criteria."""

    DEFAULT_CSS = """
    GoalPanel {
        height: auto;
        max-height: 8;
        dock: top;
        padding: 0 1;
        background: $surface;
        color: $text;
    }
    GoalPanel.no-goal {
        display: none;
    }
    GoalPanel.collapsed {
        height: 1;
        max-height: 1;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._objective: str = ""
        self._status: str = ""
        self._progress: float = 0.0
        self._criteria: list[dict[str, Any]] = []
        self._evidence_count: int = 0
        self._expanded: bool = True
        self._goal_id: str = ""
        self._continuation_paused: bool = False
        # Start hidden until a goal is active
        self.add_class("no-goal")

    def update_goal(
        self,
        objective: str = "",
        status: str = "",
        progress: float = 0.0,
        criteria: list[dict[str, Any]] | None = None,
        evidence_count: int = 0,
        goal_id: str = "",
        continuation_paused: bool = False,
    ) -> None:
        """Update goal data and re-render."""
        self._objective = objective
        self._status = status
        self._progress = progress
        self._criteria = criteria or []
        self._evidence_count = evidence_count
        self._goal_id = goal_id
        self._continuation_paused = continuation_paused
        # Show the panel when goal is active
        self.remove_class("no-goal")
        self._render()

    def clear_goal(self) -> None:
        """Clear goal data and hide the panel."""
        self._objective = ""
        self._status = ""
        self._progress = 0.0
        self._criteria = []
        self._evidence_count = 0
        self._goal_id = ""
        # Hide the panel when no goal
        self.add_class("no-goal")
        self.update(" ")

    def toggle_panel(self) -> None:
        """Toggle expanded/collapsed state."""
        self._expanded = not self._expanded
        if self._expanded:
            self.remove_class("collapsed")
        else:
            self.add_class("collapsed")
        self._render()

    def _render(self) -> None:
        """Re-render the panel content."""
        if not self._objective:
            self.update("")
            return

        if self._expanded:
            self._render_expanded()
        else:
            self._render_collapsed()

    def _render_expanded(self) -> None:
        """Render the full expanded view."""
        lines = []

        # Title line
        icon = _STATUS_ICONS.get(self._status, "🎯")
        title = self._objective
        if len(title) > 60:
            title = title[:57] + "..."
        lines.append(f"{icon} [bold]{title}[/bold]")

        # Progress bar
        ratio = self._progress / 100.0
        covered = sum(1 for c in self._criteria if c.get("status", "").lower() in {
            "covered", "complete", "satisfied"})
        total = len(self._criteria)
        bar = _progress_bar(ratio)
        pct = f"{self._progress:.0f}%"
        lines.append(f"{bar} {pct} ({covered}/{total})")

        # Criteria list
        for i, c in enumerate(self._criteria, 1):
            status = c.get("status", "pending")
            icon = _criterion_icon(status)
            text = c.get("text", "")
            if len(text) > 55:
                text = text[:52] + "..."
            req = "" if c.get("required", True) else " [dim](opt)[/dim]"
            lines.append(f" {icon} {i}. {text}{req}")

        # Footer: evidence count + hint
        ev_str = f"📎 {self._evidence_count} evidence"
        if self._continuation_paused:
            hint = "Ctrl+G 恢复续跑"
            hint_style = "warning"
        else:
            hint = "Ctrl+G 暂停续跑"
            hint_style = "dim"
        lines.append(f" {ev_str}  [dim]│[/dim]  [{hint_style}]{hint}[/{hint_style}]")

        self.update("\n".join(lines))

    def _render_collapsed(self) -> None:
        """Render the compact single-line view."""
        icon = _STATUS_ICONS.get(self._status, "🎯")
        title = self._objective
        if len(title) > 30:
            title = title[:27] + "..."
        covered = sum(1 for c in self._criteria if c.get("status", "").lower() in {
            "covered", "complete", "satisfied"})
        total = len(self._criteria)
        pct = f"{self._progress:.0f}% ({covered}/{total})"
        ev = f"📎 {self._evidence_count}"
        hint = "Ctrl+G 展开"
        self.update(f"{icon} {title}  {pct}  {ev}  [dim]│[/dim]  [dim]{hint}[/dim]")

    # ── P3.8: Workflow event handler ──────────────────────────

    def on_workflow_event(self, event: str, data: dict[str, Any]) -> None:
        """Handle workflow events from WorkflowEventBus (P3.8).

        Called by GoalPanelObserver when the runner emits events.
        Updates the panel display based on the event type.

        Supported events:
          - layer_start: show current layer progress
          - agent_start: flash agent as running
          - agent_complete: update agent status
          - agent_error: mark agent as error
          - workflow_completed: show completion status
          - workflow_failed: show error status
          - workflow_paused: show paused status
          - workflow_resumed: show resumed status
        """
        agent_id = data.get("agent_id", "")

        if event == "agent_start":
            # Mark agent as running in criteria (if we have criteria)
            for c in self._criteria:
                # Match by index or text containing agent_id
                if c.get("agent_id") == agent_id or not c.get("agent_id"):
                    c["_agent_status"] = "running"
                    break
            self._render()

        elif event == "agent_complete":
            for c in self._criteria:
                if c.get("agent_id") == agent_id:
                    c["_agent_status"] = "success"
                    c["status"] = "covered"
                    break
            self._evidence_count = data.get("evidence_count", self._evidence_count)
            self._render()

        elif event == "agent_error":
            for c in self._criteria:
                if c.get("agent_id") == agent_id:
                    c["_agent_status"] = "error"
                    break
            self._render()

        elif event == "workflow_completed":
            self._status = "complete"
            self._progress = 100.0
            self._render()

        elif event == "workflow_failed":
            self._status = "error"
            self._render()

        elif event in ("workflow_paused", "workflow_resumed"):
            # The continuation_paused flag is set by the caller
            self._render()

        elif event == "layer_start":
            # Could show layer progress in the footer
            self._render()


__all__ = ["GoalPanel"]
