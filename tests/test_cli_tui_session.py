"""Tests for ``cli.tui.session.ChatSession`` and the dispatch surface.

These tests verify that the session correctly wraps the existing
``process_turn`` dispatcher in a Textual-aware async shell. They
``mock.patch.object`` the chat subcommands so the textual layer is
exercised without any of the legacy ``rich.console.Console.print``
side-effecting.

Scenarios:
* User types ``/quit`` — session asks app to exit.
* User types ``/help`` — handler is invoked and rc flows back.
* User types plain text — appended to ``ctx.history``.
* Halt keyword — trips ``cli.halt.HALT`` via the session.
* ``/resume`` keyword — clears ``cli.halt.HALT``.
* Bare digit on a pending proposal — consumed by ``process_turn``,
  not appended to ``ctx.history``.
* Slash handler queues a follow-up prompt (``ctx.pending_prompt``)
  — drained automatically by the session.
* ResumeOrNewModal callback wires ``pending_input`` correctly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest import mock

import pytest

from agentnodes.interfaces.cli.halt import is_halted, trip_halt
from agentnodes.interfaces.cli.interactive.main import InteractiveContext
from agentnodes.interfaces.cli.tui.session import (
    QUIT_RC,
    ChatSession,
)


@dataclass
class _FakeApp:
    """Minimal stand-in for ``ResearchApp`` to receive write/exit calls."""

    exit_calls: int = 0

    def exit(self) -> None:
        self.exit_calls += 1

    def write_transcript(self, content: Any) -> None:
        # Tests can inspect ``app.write_calls`` if needed; for now store.
        if not hasattr(self, "write_calls"):
            self.write_calls = []  # type: ignore[attr-defined]
        self.write_calls.append(content)  # type: ignore[attr-defined]


# ─── session.dispatch() basic routing ───────────────────────────────


@pytest.mark.asyncio
async def test_session_quit_returns_2_and_calls_app_exit():
    """``/quit`` returns rc=2 (sentinel) and triggers app.exit()."""
    ctx = InteractiveContext()
    app = _FakeApp()
    s = ChatSession(ctx, app=app)
    rc = await s.dispatch("/quit")
    assert rc == QUIT_RC
    assert app.exit_calls == 1


@pytest.mark.asyncio
async def test_session_plain_text_appended_to_history():
    """Plain text hits ``process_turn`` and lands in ``ctx.history``."""
    ctx = InteractiveContext()
    s = ChatSession(ctx)
    rc = await s.dispatch("hello world")
    assert rc == 0
    assert len(ctx.history) == 1
    assert ctx.history[0] == {"role": "user", "content": "hello world"}


@pytest.mark.asyncio
async def test_session_empty_input_returns_0():
    """Empty / whitespace input is a no-op."""
    ctx = InteractiveContext()
    s = ChatSession(ctx)
    rc = await s.dispatch("   ")
    assert rc == 0
    assert ctx.history == []


@pytest.mark.asyncio
async def test_session_halt_keyword_trips_halt():
    """Bare Chinese "停" trips the kill switch and writes a transcript line."""
    ctx = InteractiveContext()
    app = _FakeApp()
    s = ChatSession(ctx, app=app)
    rc = await s.dispatch("停")
    assert rc == 0
    assert is_halted() is True


@pytest.mark.asyncio
async def test_session_resume_keyword_clears_halt():
    """``/resume`` clears the kill switch."""
    trip_halt()
    ctx = InteractiveContext()
    s = ChatSession(ctx)
    rc = await s.dispatch("/resume")
    assert rc == 0
    assert is_halted() is False


@pytest.mark.asyncio
async def test_session_pick_consumed_for_pending_proposal():
    """Bare digit on a pending proposal is consumed without going to history."""
    from agentnodes.interfaces.cli.mandate import make_proposal

    ctx = InteractiveContext()
    ctx.pending_proposal = make_proposal(
        "Pick",
        [{"label": "Alpha", "payload": None}, {"label": "Beta", "payload": None}],
    )
    s = ChatSession(ctx)
    rc = await s.dispatch("1")
    assert rc == 0
    # The digit does NOT land in history — the proposal was consumed.
    assert ctx.history == []
    # And the proposal was cleared by the dispatcher.
    assert ctx.pending_proposal is None


@pytest.mark.asyncio
async def test_session_drains_pending_prompt_after_slash():
    """``/journal <path>`` (or /shadow) writes ``ctx.pending_prompt``; the
    session re-dispatches it automatically so the user doesn't have to
    re-press Enter.
    """
    ctx = InteractiveContext()
    s = ChatSession(ctx)

    def fake_journal(ctx_arg, *args):
        ctx_arg.pending_prompt = "echo from /journal"
        return 0

    # Stub ``/journal`` inside ``_DISPATCH`` to queue a follow-up prompt.
    with mock.patch.dict(
        "agentnodes.interfaces.cli.interactive.main._DISPATCH",
        {"journal": fake_journal},
        clear=False,
    ):
        await s.dispatch("/journal some/path")

    # The journal handler ran, queued the prompt, the session drained it.
    assert ctx.history == [
        {"role": "user", "content": "echo from /journal"},
    ]


# ─── ResumeOrNewModal callback wiring ────────────────────────────────


def test_resume_modal_callback_resume_branch(monkeypatch):
    """``_on_resume_choice((True, None))`` triggers session resume."""
    from agentnodes.interfaces.cli.tui.app import ResearchApp

    # Build a fresh app context, skip_resume=True to avoid the real prompt.
    app = ResearchApp(model="m", version="0.4.2", skip_resume=True)
    app.session = ChatSession(app.ctx, app=app)

    # Stub the resume-loading helper so we don't touch the real DB.
    captured = {"calls": 0}

    def fake_resume(app_self):
        captured["calls"] += 1

    monkeypatch.setattr(
        "agentnodes.interfaces.cli.tui.app.ResearchApp._resume_most_recent_session",
        fake_resume,
        raising=False,
    )
    monkeypatch.setattr(
        "agentnodes.interfaces.cli.tui.app.ResearchApp._write_transcript",
        lambda self, content: None,
        raising=False,
    )

    app._on_resume_choice((True, None))
    assert captured["calls"] == 1


def test_resume_modal_callback_new_branch(monkeypatch):
    """``_on_resume_choice((False, None))`` writes a "fresh session" line."""
    from agentnodes.interfaces.cli.tui.app import ResearchApp

    app = ResearchApp(model="m", version="0.4.2", skip_resume=True)
    app.session = ChatSession(app.ctx, app=app)

    write_calls = []

    monkeypatch.setattr(
        "agentnodes.interfaces.cli.tui.app.ResearchApp._write_transcript",
        lambda self, c: write_calls.append(c),
        raising=False,
    )
    app._on_resume_choice((False, None))
    assert any("Started fresh" in str(c) for c in write_calls)


def test_resume_modal_callback_pending_input_posts_synthesize_input(monkeypatch):
    """批1 #4：弹窗首条 prompt 经 post_message(SynthesizeInput) 走键入同链。

    此前 enqueue 进 ``_pending_input`` 后全仓零消费——弹窗里输入的
    首条消息被静默丢弃。修复后与 ChatInput 提交完全同路。
    """
    from agentnodes.interfaces.cli.tui.app import ResearchApp
    from agentnodes.interfaces.cli.tui.messages import SynthesizeInput

    app = ResearchApp(model="m", version="0.4.2", skip_resume=True)
    app.session = ChatSession(app.ctx, app=app)

    monkeypatch.setattr(
        "agentnodes.interfaces.cli.tui.app.ResearchApp._write_transcript",
        lambda self, c: None,
        raising=False,
    )
    posted = []
    monkeypatch.setattr(
        "agentnodes.interfaces.cli.tui.app.ResearchApp.post_message",
        lambda self, m: posted.append(m),
        raising=False,
    )
    app._on_resume_choice((False, "hi there"))
    assert len(posted) == 1
    assert isinstance(posted[0], SynthesizeInput)
    assert posted[0].text == "hi there"
    # enqueue/_pending_input 缓冲已随修复删除
    assert not hasattr(app.session, "_pending_input")


# ─── TUI integration smoke ────────────────────────────────────────


@pytest.mark.asyncio
async def test_tui_input_submitted_routes_through_session(monkeypatch):
    """``ChatInput``-style SynthesizeInput message reaches ``process_turn``."""
    from agentnodes.interfaces.cli.tui.app import ResearchApp
    from agentnodes.interfaces.cli.tui.messages import SynthesizeInput

    app = ResearchApp(model="m", version="0.4.2", skip_resume=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Skip banner render-time: instantiate the session eagerly.
        if app.session is None:
            app.session = ChatSession(app.ctx, app=app)
        await pilot.pause()
        await app.on_synthesize_input(SynthesizeInput(text="hi"))
        await pilot.pause()
        # Plain text lands in history.
        assert any(
            m.get("content") == "hi" for m in app.ctx.history
        ), f"history does not contain 'hi': {app.ctx.history!r}"


@pytest.mark.asyncio
async def test_tui_ctrl_d_quits_app():
    """``Ctrl+D`` (``action_quit_app``) exits the TUI cleanly."""
    from agentnodes.interfaces.cli.tui.app import ResearchApp

    app = ResearchApp(model="m", version="0.4.2", skip_resume=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_quit_app()
        # Pilot will eventually exit the test.


@pytest.mark.asyncio
async def test_tui_ctrl_c_trips_halt(monkeypatch):
    """``Ctrl+C`` (``action_halt``) trips the kill switch via the session.

    **注意分支**：``action_halt`` 是"智能 Ctrl+C"——有选中文本时复制、
    否则 trip halt。``run_test()`` 的虚拟终端可能残留选中态，导致测试
    偶发走到"复制"分支而 is_halted() 为 False（全量顺序下出现过）。
    这里显式确保无选中，让测试钉住 trip 这条路径。
    """
    from agentnodes.interfaces.cli.tui.app import ResearchApp

    app = ResearchApp(model="m", version="0.4.2", skip_resume=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.session = ChatSession(app.ctx, app=app)
        # 清掉可能残留的选中，确保走 trip 分支
        try:
            app.screen.clear_selection()
        except Exception:  # noqa: BLE001 — 老版本 Textual 无此 API
            pass
        app.action_halt()
        await pilot.pause()
        assert is_halted() is True
