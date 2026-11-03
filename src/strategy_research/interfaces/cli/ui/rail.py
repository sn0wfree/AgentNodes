"""RailRunDashboard — Codex-style activity rail.

Mirrors ``vibe-trading/cli/ui/rail.py``. Tracks a sequence of
:class:`RailStep` records (one per agent tool call), each with title / tool
name / args / status / lines / elapsed. ``handle_event`` is the dispatcher
that the agent loop calls; ``render()`` produces a Rich ``Group`` with the
current state.

Public API:

* :class:`RailStep` — single step dataclass.
* :class:`RailRunDashboard` — owns the steps and produces renderables.

Supported ``handle_event`` types:

* ``text_delta`` — append to active step's lines.
* ``thinking_done`` — flip ``thinking_active=False``.
* ``llm_usage`` — accumulate input/output tokens.
* ``tool_call`` — push a new step.
* ``tool_progress`` — append to active step.
* ``tool_heartbeat`` — register active step if missing.
* ``tool_result`` — mark done/error + duration.
* ``compact`` — append a "context" warning step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from rich.console import Group, RenderResult
from rich.text import Text

from strategy_research.interfaces.cli.components.tool_event import beautify_tool_name

# Cap on the number of recent steps kept in memory.
_DEFAULT_LIMIT = 10
_TEXT_LINE_LIMIT = 500

_STATUS_LABEL = {
    "active": "⏵",
    "done": "✓",
    "error": "×",
    "warning": "⚠",
}
_STATUS_STYLE = {
    "active": "warning",
    "done": "success",
    "error": "danger",
    "warning": "warning",
}


@dataclass
class RailStep:
    title: str
    tool: str
    args: dict = field(default_factory=dict)
    status: str = "active"
    lines: list[str] = field(default_factory=list)
    started_at: Optional[float] = None
    duration_s: Optional[float] = None
    result_summary: Optional[str] = None

    def append_line(self, line: str) -> None:
        if len(self.lines) < _TEXT_LINE_LIMIT:
            self.lines.append(line)


def _action_label(tool: str) -> str:
    """Render a pretty action verb for the given tool name."""
    pretty = beautify_tool_name(tool)
    if not pretty:
        return tool
    return pretty


class RailRunDashboard:
    """Activity rail."""

    def __init__(self, *, limit: int = _DEFAULT_LIMIT) -> None:
        self._steps: list[RailStep] = []
        self._limit = limit
        self._active_step: Optional[RailStep] = None
        self._thinking_active: bool = True
        self._input_tokens: int = 0
        self._output_tokens: int = 0
        self._verb: str = "Analyzing"
        self._elapsed: float = 0.0

    # ── Event handlers ─────────────────────────────────────────────

    def handle_event(self, event_type: str, data: dict[str, Any]) -> None:
        if event_type == "text_delta":
            text = data.get("text", "")
            if self._active_step is not None and text:
                self._active_step.append_line(text)
        elif event_type == "thinking_done":
            self._thinking_active = False
        elif event_type == "llm_usage":
            self._input_tokens += int(data.get("input_tokens", 0))
            self._output_tokens += int(data.get("output_tokens", 0))
        elif event_type in ("tool_call", "tool_heartbeat"):
            self._tool_call(data)
        elif event_type == "tool_progress":
            stage = data.get("stage", "")
            current = data.get("current", "")
            total = data.get("total", "")
            message = data.get("message", "")
            line = " · ".join(
                x for x in (stage, f"{current}/{total}" if current else "", message)
                if x
            )
            if self._active_step is not None and line:
                self._active_step.append_line(line)
        elif event_type == "tool_result":
            self._tool_result(data)
        elif event_type == "compact":
            tokens = data.get("tokens")
            step = RailStep(
                title="Context compacted",
                tool="compact",
                status="warning",
                result_summary=f"{tokens} tokens" if tokens else None,
            )
            self._push_step(step)

    def _tool_call(self, data: dict[str, Any]) -> None:
        tool = data.get("tool", "")
        title = _action_label(tool)
        args = data.get("args", {})
        step = RailStep(title=title, tool=tool, args=args)
        self._push_step(step)

    def _push_step(self, step: RailStep) -> None:
        self._steps.append(step)
        self._active_step = step
        if len(self._steps) > self._limit:
            self._steps = self._steps[-self._limit :]

    def _tool_result(self, data: dict[str, Any]) -> None:
        if self._active_step is None:
            return
        ok = data.get("ok", True)
        elapsed_ms = data.get("elapsed_ms")
        if elapsed_ms is not None:
            self._active_step.duration_s = float(elapsed_ms) / 1000
        self._active_step.status = "done" if ok else "error"
        summary = data.get("summary")
        if summary:
            self._active_step.result_summary = str(summary)
        self._active_step = None

    # ── Verb / completion ──────────────────────────────────────────

    def set_verb(self, verb: str) -> None:
        """Update the spinner verb (e.g. 'Synthesizing…')."""
        self._verb = verb.rstrip("…")

    def finish(self, result: str, elapsed: float) -> None:
        """Finalize the dashboard — mark all steps done, set completion text."""
        for step in self._steps:
            if step.status == "active":
                step.status = "done"
        self._elapsed = elapsed
        self._thinking_active = False
        self._completion = result
        self._active_step = None

    def _ensure_completion(self) -> str:
        if not hasattr(self, "_completion") or not self._completion:
            return f"Done {self._verb}."
        return self._completion

    # ── Render ─────────────────────────────────────────────────────

    def render(self) -> RenderResult:
        """Yield a Rich Group containing the per-step rows + activity line."""
        children: list[Any] = []
        for step in self._steps:
            children.append(self._render_step(step))

        # Activity line at the bottom.
        activity = Text()
        if self._thinking_active or self._active_step is not None:
            activity.append(f"· {self._verb} ({self._elapsed:.0f}s)", style="muted")
        else:
            activity.append(f"• {self._ensure_completion()}", style="success")
        children.append(activity)
        return Group(*children)

    def _render_step(self, step: RailStep) -> Text:
        marker = _STATUS_LABEL.get(step.status, "·")
        style = _STATUS_STYLE.get(step.status, "muted")
        text = Text()
        text.append(f"{marker} ", style=style)
        text.append(step.title, style="bold")
        if step.duration_s is not None:
            from strategy_research.interfaces.cli.utils.format import format_duration
            text.append(f"  {format_duration(step.duration_s * 1000)}", style="muted")
        if step.result_summary:
            text.append(f"  · {step.result_summary}", style="muted")
        text.append("\n")
        return text


__all__ = ["RailStep", "RailRunDashboard"]
