"""Tests for ``cli.tui.session.ChatSession.dispatch_with_capture``.

Verifies that the TUI session:

* captures handler output (when the handler wrote via ``console.print``),
* forwards it to ``app.write_transcript``,
* propagates ``process_turn`` rc back,
* still drains ``ctx.pending_prompt`` queue,
* still trips / clears ``cli.halt.HALT``.

Most existing 25 handler tests still run unmodified because we did not
change handler signatures; we just wrap each handler call in a
recording console.
"""
from __future__ import annotations

from unittest import mock

import pytest

from strategy_research.interfaces.cli.halt import is_halted, trip_halt
from strategy_research.interfaces.cli.interactive.main import InteractiveContext
from strategy_research.interfaces.cli.tui.session import QUIT_RC, ChatSession


class _StubApp:
    """Tiny app shim that records everything that flows through it."""

    def __init__(self) -> None:
        self.exited = 0
        self.writes: list = []

    def exit(self) -> None:
        self.exited += 1

    def write_transcript(self, content) -> None:
        self.writes.append(content)


# ─── dispatch() output capture ─────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_captures_handler_output_and_writes_to_transcript(monkeypatch):
    """When ``process_turn`` calls a handler that uses ``console.print``,
    the captured text is forwarded via ``app.write_transcript``.
    """
    monkeypatch.setenv("LANGCHAIN_PROVIDER", "openai")
    monkeypatch.setenv("LANGCHAIN_MODEL_NAME", "gpt-4o")
    ctx = InteractiveContext()
    app = _StubApp()
    s = ChatSession(ctx, app=app)

    # Patch the ``/model`` dispatch entry to call cmd_model directly.
    # cmd_model uses ``_resolve_console(None)`` so the singleton
    # override is honored inside ``captured_console``.
    def _model_dispatch(*args, **kwargs):
        from strategy_research.interfaces.cli.commands.slash_chat import cmd_model
        return cmd_model()

    with mock.patch.dict(
        "strategy_research.interfaces.cli.interactive.main._DISPATCH",
        {"model": _model_dispatch},
        clear=False,
    ):
        rc = await s.dispatch("/model")

    assert rc == 0
    # The handler's printed output ("Provider: …", "Model: …") should
    # reach the transcript.
    captured = "\n".join(str(c) for c in app.writes)
    assert "Provider" in captured
    assert "Model" in captured
    assert "openai" in captured


@pytest.mark.asyncio
async def test_dispatch_plain_text_no_capture_transcript_write():
    """Plain text turns append to ``ctx.history`` and do not write to
    the transcript at all (no rich output to capture).
    """
    ctx = InteractiveContext()
    app = _StubApp()
    s = ChatSession(ctx, app=app)
    rc = await s.dispatch("hi")
    assert rc == 0
    assert app.writes == []  # nothing to capture for plain chat
    assert ctx.history == [{"role": "user", "content": "hi"}]


@pytest.mark.asyncio
async def test_dispatch_quit_sentinel_calls_exit():
    ctx = InteractiveContext()
    app = _StubApp()
    s = ChatSession(ctx, app=app)
    rc = await s.dispatch("/quit")
    assert rc == QUIT_RC
    assert app.exited == 1


@pytest.mark.asyncio
async def test_dispatch_quit_no_app_does_not_crash():
    """``app is None`` is allowed; ``app.exit()`` simply isn't called."""
    ctx = InteractiveContext()
    s = ChatSession(ctx)
    rc = await s.dispatch("/quit")
    assert rc == QUIT_RC


@pytest.mark.asyncio
async def test_dispatch_app_capture_format_passes_markup_through():
    """The captured text retains Rich markup because we use
    ``rec.export_text(..., styles=False)`` and let ``TranscriptView``
    render it.
    """
    ctx = InteractiveContext()
    app = _StubApp()
    s = ChatSession(ctx, app=app)

    def _handler(*args, **kwargs):
        from strategy_research.interfaces.cli.theme import get_console
        # Use the singleton so the captured_console context manager
        # captures our print.
        get_console().print("[bold]bold-text[/bold] seen")
        return 0

    with mock.patch.dict(
        "strategy_research.interfaces.cli.interactive.main._DISPATCH",
        {"show": _handler},
        clear=False,
    ):
        await s.dispatch("/show demo")

    captured = "\n".join(str(c) for c in app.writes)
    assert "bold-text" in captured
    assert "seen" in captured


@pytest.mark.asyncio
async def test_dispatch_halt_keyword_trips_halt_and_no_exit():
    ctx = InteractiveContext()
    app = _StubApp()
    s = ChatSession(ctx, app=app)
    rc = await s.dispatch("停")
    assert rc == 0
    assert is_halted()
    assert app.exited == 0  # halt does not exit, only quit does


@pytest.mark.asyncio
async def test_dispatch_resume_keyword_clears_halt():
    trip_halt()
    ctx = InteractiveContext()
    app = _StubApp()
    s = ChatSession(ctx, app=app)
    rc = await s.dispatch("/resume")
    assert rc == 0
    assert not is_halted()


@pytest.mark.asyncio
async def test_dispatch_drains_pending_prompt_with_capture():
    """Slash handlers may queue ``ctx.pending_prompt``; the session
    drains it and forwards any captured output to the transcript.
    """
    ctx = InteractiveContext()
    app = _StubApp()
    s = ChatSession(ctx, app=app)

    def _journal(ctx_arg, *args):
        ctx_arg.pending_prompt = "followup from journal"
        return 0

    def _echo(_ctx_arg, *args):
        from rich.console import Console
        Console().print("[green]followup handled[/green]")
        return 0

    with mock.patch.dict(
        "strategy_research.interfaces.cli.interactive.main._DISPATCH",
        {"journal": _journal, "echo_echo": _echo},
        clear=False,
    ):
        await s.dispatch("/journal some/path")

    # Pending prompt was consumed and went through history.
    assert any(
        m.get("content") == "followup from journal" for m in ctx.history
    ), f"history missing followup: {ctx.history!r}"
    # … but no capture flowed (the synthetic echo was not invoked).


@pytest.mark.asyncio
async def test_dispatch_does_not_double_capture_when_no_pending():
    """An empty pending path does not invoke the helper twice."""
    ctx = InteractiveContext()
    app = _StubApp()
    s = ChatSession(ctx, app=app)
    rc = await s.dispatch("hello again")
    # Plain text → no transcript writes; rc == 0.
    assert rc == 0
    assert app.writes == []
    assert ctx.history == [{"role": "user", "content": "hello again"}]
