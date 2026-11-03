"""Tests for ``cli.ui.rail.RailRunDashboard``."""

from __future__ import annotations

from rich.console import Console, Group

from strategy_research.interfaces.cli.ui.rail import RailRunDashboard, RailStep


def _render_to_text(dashboard) -> str:
    """Render the dashboard to plain text via a recording Console."""
    console = Console(record=True, force_terminal=False, width=120)
    console.print(dashboard.render())
    return console.export_text()


# ─── Step dataclass ─────────────────────────────────────────────────────


class TestRailStep:
    def test_default_status_active(self):
        step = RailStep(title="t", tool="tool")
        assert step.status == "active"

    def test_append_line_respects_limit(self):
        step = RailStep(title="t", tool="tool")
        step.append_line("a")
        step.append_line("b")
        assert step.lines == ["a", "b"]

    def test_append_line_caps(self):
        step = RailStep(title="t", tool="tool")
        for i in range(600):
            step.append_line(f"line-{i}")
        assert len(step.lines) == 500  # _TEXT_LINE_LIMIT


# ─── Tool call / result lifecycle ───────────────────────────────────────


class TestToolLifecycle:
    def test_tool_call_pushes_step(self):
        d = RailRunDashboard()
        d.handle_event("tool_call", {"tool": "skill", "args": {"name": "read_csv"}})
        assert len(d._steps) == 1
        assert d._steps[0].tool == "skill"
        assert d._steps[0].title == "Skill"  # strip 'load_' + capitalize

    def test_active_step_singular(self):
        d = RailRunDashboard()
        d.handle_event("tool_call", {"tool": "skill"})
        d.handle_event("tool_call", {"tool": "get_financials"})
        # Latest call becomes active; previous is still in steps
        assert d._active_step.tool == "get_financials"

    def test_tool_result_clears_active(self):
        d = RailRunDashboard()
        d.handle_event("tool_call", {"tool": "skill"})
        d.handle_event("tool_result", {"ok": True, "elapsed_ms": 100})
        assert d._active_step is None
        assert d._steps[-1].status == "done"
        assert d._steps[-1].duration_s == 0.1

    def test_tool_result_failure(self):
        d = RailRunDashboard()
        d.handle_event("tool_call", {"tool": "skill"})
        d.handle_event("tool_result", {"ok": False, "elapsed_ms": 50, "summary": "boom"})
        assert d._steps[-1].status == "error"
        assert d._steps[-1].result_summary == "boom"

    def test_steps_limit(self):
        d = RailRunDashboard(limit=3)
        for i in range(5):
            d.handle_event("tool_call", {"tool": f"tool_{i}"})
        assert len(d._steps) == 3  # oldest 2 dropped


# ─── Other events ───────────────────────────────────────────────────────


class TestOtherEvents:
    def test_text_delta_appends(self):
        d = RailRunDashboard()
        d.handle_event("tool_call", {"tool": "skill"})
        d.handle_event("text_delta", {"text": "loading…"})
        assert "loading…" in d._active_step.lines

    def test_thinking_done(self):
        d = RailRunDashboard()
        assert d._thinking_active is True
        d.handle_event("thinking_done", {})
        assert d._thinking_active is False

    def test_llm_usage_accumulates(self):
        d = RailRunDashboard()
        d.handle_event("llm_usage", {"input_tokens": 100, "output_tokens": 50})
        d.handle_event("llm_usage", {"input_tokens": 200, "output_tokens": 100})
        assert d._input_tokens == 300
        assert d._output_tokens == 150

    def test_tool_progress(self):
        d = RailRunDashboard()
        d.handle_event("tool_call", {"tool": "run_backtest"})
        d.handle_event("tool_progress", {"stage": "loading", "current": 1, "total": 4, "message": "ping"})
        assert d._active_step is not None
        assert any("loading" in line for line in d._active_step.lines)

    def test_compact_creates_step(self):
        d = RailRunDashboard()
        d.handle_event("compact", {"tokens": 5000})
        assert len(d._steps) == 1
        assert d._steps[0].status == "warning"
        assert "5000" in (d._steps[0].result_summary or "")


# ─── Render ────────────────────────────────────────────────────────────


class TestRender:
    def test_render_returns_group(self):
        d = RailRunDashboard()
        result = d.render()
        assert isinstance(result, Group)

    def test_render_text_contains_step_title(self):
        d = RailRunDashboard()
        d.handle_event("tool_call", {"tool": "skill"})
        d.handle_event("tool_result", {"ok": True, "elapsed_ms": 200})
        out = _render_to_text(d)
        assert "Skill" in out

    def test_render_shows_active(self):
        d = RailRunDashboard()
        d.handle_event("tool_call", {"tool": "skill"})  # active
        d.set_verb("Loading")
        out = _render_to_text(d)
        assert "Loading" in out

    def test_render_shows_done_when_finished(self):
        d = RailRunDashboard()
        d.handle_event("tool_call", {"tool": "skill"})
        d.handle_event("tool_result", {"ok": True})
        d.finish(result="Completed.", elapsed=1.5)
        out = _render_to_text(d)
        assert "Completed" in out or "Done" in out

    def test_render_returns_group_with_steps(self):
        d = RailRunDashboard()
        d.handle_event("tool_call", {"tool": "skill"})
        d.handle_event("tool_call", {"tool": "run_backtest"})
        d.handle_event("tool_result", {"ok": True})
        result = d.render()
        # Group of: 2 step rows + activity line
        children = list(result.renderables)
        assert len(children) >= 3
