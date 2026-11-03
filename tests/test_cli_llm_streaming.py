"""Tests for ``cli.llm_streaming`` — the LLM → Textual TUI bridge.

We don't hit the network in unit tests; instead we use a stub client
that yields :class:`StreamChunk` objects whose ``delta_content`` is
already-populated.
"""
from __future__ import annotations

from typing import Iterable
from unittest import mock

import pytest

from strategy_research.cli.llm_streaming import (
    _build_messages,
    _consume_sync_stream,
    stream_chat_to_tui,
)
from strategy_research.cli.tui.session import ChatSession
from strategy_research.foundation.llm.errors import LLMError, LLMTimeoutError
from strategy_research.foundation.llm.parser import StreamChunk


@pytest.fixture(autouse=True)
def _isolate_session_db(tmp_path, monkeypatch):
    """Isolate the unified session DB (TUI flow persists to cwd by default)."""
    monkeypatch.setenv("SR_WORKSPACE_PATH", str(tmp_path))


def _chunk(content: str, *, finish: str | None = None) -> StreamChunk:
    return StreamChunk(delta_content=content, finish_reason=finish)


class _FakeClient:
    """Mimics :class:`OpenAICompatClient.stream` yielding deltas.

    Supports both sync ``stream`` and async ``astream`` so the
    streaming bridge can exercise the real per-token path.
    """

    def __init__(self, chunks: Iterable[StreamChunk] | None = None, *, error: Exception | None = None):
        self._chunks = list(chunks or [])
        self._error = error

    def stream(self, messages, **kw):
        if self._error is not None:
            raise self._error
        for c in self._chunks:
            yield c

    async def astream(self, messages, **kw):
        if self._error is not None:
            raise self._error
        for c in self._chunks:
            yield c


# ─── _build_messages ────────────────────────────────────────────────


class TestBuildMessages:
    def test_empty_history_returns_empty(self):
        from dataclasses import dataclass, field

        @dataclass
        class _Ctx:
            history: list = field(default_factory=list)

        assert _build_messages(_Ctx()) == []

    def test_drops_empty_strings(self):
        from dataclasses import dataclass, field

        @dataclass
        class _Ctx:
            history: list = field(default_factory=lambda: [
                {"role": "user", "content": ""},
                {"role": "user", "content": "ok"},
            ])

        msgs = _build_messages(_Ctx())
        assert msgs == [{"role": "user", "content": "ok"}]

    def test_filters_unknown_roles(self):
        from dataclasses import dataclass, field

        @dataclass
        class _Ctx:
            history: list = field(default_factory=lambda: [
                {"role": "user", "content": "hi"},
                {"role": "system", "content": "you are a bot"},
                {"role": "tool", "content": "data"},
                {"role": "unknown_role", "content": "skip"},
                {"role": "assistant", "content": "hello"},
            ])

        msgs = _build_messages(_Ctx())
        assert msgs == [
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "you are a bot"},
            {"role": "assistant", "content": "hello"},
        ]

    def test_truncates_to_recent_12_turns(self):
        from dataclasses import dataclass, field

        @dataclass
        class _Ctx:
            history: list = field(default_factory=lambda: [
                {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg{i}"}
                for i in range(40)
            ])

        msgs = _build_messages(_Ctx())
        # The function takes ``[-12:]`` from history → last 12 (which is
        # still well above the off-screen noise floor; we test for "no
        # more than 12").
        assert len(msgs) <= 12
        # And the most recent turn survives.
        assert msgs[-1]["content"] == "msg39"


# ─── _consume_sync_stream ───────────────────────────────────────────


class TestConsumeSyncStream:
    def test_folds_deltas_into_one_string(self):
        client = _FakeClient([
            _chunk("Hello"),
            _chunk(", "),
            _chunk("world!"),
        ])
        text, char_count = _consume_sync_stream(client, [])
        assert text == "Hello, world!"
        assert char_count == 13

    def test_empty_stream_returns_empty(self):
        client = _FakeClient([])
        text, char_count = _consume_sync_stream(client, [])
        assert text == ""
        assert char_count == 0

    def test_propagates_llm_error(self):
        client = _FakeClient(error=LLMTimeoutError("nope"))
        with pytest.raises(LLMTimeoutError):
            _consume_sync_stream(client, [])


# ─── stream_chat_to_tui ─────────────────────────────────────────────


class _FakeApp:
    """Minimal Textual app stub that collects WriteTranscript posts.

    Delegates streaming lifecycle to the TranscriptView stub so the
    bridge exercises the full begin/update/end flow.  The
    ``_FakeTranscriptViewSink`` simulates the RichLog ``lines`` list
    and ``write()`` so fold/truncate logic can be tested.
    """

    def __init__(self) -> None:
        self.writes: list = []
        self.exit_count = 0
        self._tv = _FakeTranscriptViewSink(self)
        self.thinking_started = False

    def query_one(self, *_args, **_kw):
        return self._tv

    def post_message(self, message) -> None:
        self.writes.append(message)

    def start_thinking(self) -> None:
        self.thinking_started = True

    def stop_thinking(self) -> None:
        self.thinking_started = False

    def start_streaming(self) -> None:
        self._tv.begin_streaming()

    def update_streaming(self, full_text: str) -> None:
        self._tv.update_streaming(full_text)

    def update_streaming_delta(self, delta: str) -> None:
        pass

    def end_streaming(self, suffix: str = "") -> str:
        return self._tv.end_streaming(suffix=suffix)


class _FakeTranscriptViewSink:
    """Stand-in for the :class:`TranscriptView` widget.

    Mirrors the multi-folder + cursor logic of the real TranscriptView
    so streaming/fold/cursor tests can run without a Textual mount cycle.
    """

    def __init__(self, app: _FakeApp) -> None:
        self._app = app
        self.lines: list = []
        self._stream_baseline: int | None = None
        self._streamer = None
        self._folders: list = []
        self._fold_baselines: list = []
        self._fold_line_counts: list = []
        self._active_folder_idx: int | None = None

    def post_message(self, message) -> None:
        self._app.writes.append(message)

    def write(self, content) -> None:
        self.lines.append(content)
        self._app.writes.append(
            type("M", (), {"content": content})()
        )

    def begin_streaming(self) -> None:
        if self._active_folder_idx is not None:
            idx = self._active_folder_idx
            if self._folders[idx].expanded:
                self._re_render_folder(idx, expand=False)
        self._active_folder_idx = None
        self._stream_baseline = len(self.lines)
        from strategy_research.cli.tui.widgets.streaming_text import StreamingText
        self._streamer = StreamingText()
        self._streamer.start()

    def update_streaming(self, text: str) -> None:
        if self._streamer is None:
            return
        self._streamer.update_streaming(text)
        self._truncate_to(self._stream_baseline)
        rendered = self._streamer.render()
        if rendered:
            self.write(rendered)

    def end_streaming(self, suffix: str = "") -> str:
        if self._streamer is None:
            return ""
        full_text = self._streamer.full_text
        self._truncate_to(self._stream_baseline)
        start = self._stream_baseline
        self._folders.append(self._streamer)
        self._fold_baselines.append(start)
        self._fold_line_counts.append(len(self.lines) - start)
        self._streamer = None
        self._stream_baseline = None
        rendered = self._folders[-1].render()
        if rendered:
            if suffix:
                self.write(f"{rendered}  {suffix}")
            else:
                self.write(rendered)
        elif suffix:
            self.write(suffix)
        return full_text

    def toggle_fold(self) -> None:
        if not self._folders:
            return
        if self._active_folder_idx is None:
            self._active_folder_idx = len(self._folders) - 1
            self._re_render_folder(self._active_folder_idx, expand=True)
        else:
            idx = self._active_folder_idx
            folder = self._folders[idx]
            if folder.expanded:
                self._re_render_folder(idx, expand=False)
                next_idx = (idx - 1) % len(self._folders)
                if next_idx != idx:
                    self._active_folder_idx = next_idx
                    self._re_render_folder(next_idx, expand=True)
            else:
                self._re_render_folder(idx, expand=True)

    def _re_render_folder(self, idx: int, expand: bool) -> None:
        start = self._fold_baselines[idx]
        line_count = self._fold_line_counts[idx]
        folder_end = min(start + line_count, len(self.lines))
        after = self.lines[folder_end:]
        self._truncate_to(start)
        folder = self._folders[idx]
        if expand:
            folder.expand()
        else:
            folder.collapse()
        rendered = folder.render()
        if rendered:
            self.write(rendered)
        old_end = folder_end
        new_end = len(self.lines)
        self.lines.extend(after)
        delta = new_end - old_end
        for i in range(idx + 1, len(self._fold_baselines)):
            self._fold_baselines[i] += delta

    def _truncate_to(self, baseline) -> None:
        if baseline is None:
            return
        if len(self.lines) > baseline:
            self.lines = self.lines[:baseline]


@pytest.mark.asyncio
async def test_stream_chat_to_tui_writes_thinking_and_final():
    """Streamed text lands in the transcript as one complete record.

    The 'thinking' phase is now handled by ThinkingSpinner (process
    layer) and does NOT write to the transcript. Only the final
    complete text + stats line should appear.
    """
    app = _FakeApp()
    client = _FakeClient([
        _chunk("Hello"),
        _chunk(", world"),
    ])
    rc = await stream_chat_to_tui(client, [], app=app)
    assert rc == 0
    contents = [str(m.content) for m in app.writes]
    joined = "".join(contents)
    # Full text in transcript (record layer)
    assert "Hello, world" in joined
    # Stats line
    assert any("chars" in c for c in contents)


@pytest.mark.asyncio
async def test_stream_chat_to_tui_renders_error_line_on_llm_failure():
    app = _FakeApp()
    client = _FakeClient(error=LLMError("rate limit exceeded"))
    rc = await stream_chat_to_tui(client, [], app=app)
    assert rc == 1
    assert any("rate limit exceeded" in str(m.content) for m in app.writes)


@pytest.mark.asyncio
async def test_stream_chat_to_tui_appends_to_ctx_history():
    app = _FakeApp()
    from dataclasses import dataclass
    from dataclasses import field as dc_field

    @dataclass
    class _Ctx:
        history: list = dc_field(default_factory=list)

    ctx = _Ctx()
    client = _FakeClient([_chunk("model reply")])
    rc = await stream_chat_to_tui(client, [], app=app, ctx=ctx)
    assert rc == 0
    # The assistant turn was appended.
    assert ctx.history == [{"role": "assistant", "content": "model reply"}]


@pytest.mark.asyncio
async def test_stream_chat_to_tui_empty_response_does_not_crash():
    app = _FakeApp()
    client = _FakeClient([])  # no chunks
    rc = await stream_chat_to_tui(client, [], app=app)
    assert rc == 0
    # Should have written an "(empty response)" hint.
    contents = [str(m.content) for m in app.writes]
    assert any("empty response" in c for c in contents)


@pytest.mark.asyncio
async def test_stream_chat_to_tui_uses_astream_when_available():
    """When the client has ``astream``, the async streaming path is used.

    The streaming lifecycle (begin_streaming -> update_streaming ->
    end_streaming) should be exercised, and the full text should end
    up in the transcript as a folded record.
    """
    app = _FakeApp()
    client = _FakeClient([
        _chunk("line1\n"),
        _chunk("line2\n"),
        _chunk("line3"),
    ])
    assert hasattr(client, "astream")
    rc = await stream_chat_to_tui(client, [], app=app)
    assert rc == 0
    # Full text in transcript record (folded, but short enough to be visible)
    contents = [str(m.content) for m in app.writes]
    joined = "".join(contents)
    assert "line1" in joined
    assert "line3" in joined


@pytest.mark.asyncio
async def test_stream_chat_to_tui_falls_back_to_sync_without_astream():
    """When the client has no ``astream``, the sync ``stream`` path is used."""
    app = _FakeApp()

    class _SyncOnlyClient:
        def __init__(self, chunks):
            self._chunks = chunks

        def stream(self, messages, **kw):
            for c in self._chunks:
                yield c

    client = _SyncOnlyClient([_chunk("sync reply")])
    rc = await stream_chat_to_tui(client, [], app=app)
    assert rc == 0
    contents = [str(m.content) for m in app.writes]
    joined = "".join(contents)
    assert "sync reply" in joined


@pytest.mark.asyncio
async def test_stream_chat_to_tui_long_text_shows_fold_indicator():
    """Long streamed text (>200 chars) gets a fold indicator + summary."""
    app = _FakeApp()
    long_text = "A" * 300 + " tail"
    client = _FakeClient([_chunk(long_text)])
    rc = await stream_chat_to_tui(client, [], app=app)
    assert rc == 0
    contents = [str(m.content) for m in app.writes]
    joined = "".join(contents)
    # Fold indicator present
    assert "ctrl+e to expand" in joined
    # Tail visible
    assert "tail" in joined
    # ctx.history gets the FULL text (zero data loss)
    from dataclasses import dataclass
    from dataclasses import field as dc_field

    @dataclass
    class _Ctx:
        history: list = dc_field(default_factory=list)

    ctx2 = _Ctx()
    app2 = _FakeApp()
    client2 = _FakeClient([_chunk(long_text)])
    await stream_chat_to_tui(client2, [], app=app2, ctx=ctx2)
    assert ctx2.history[-1]["content"] == long_text


@pytest.mark.asyncio
async def test_stream_chat_to_tui_long_text_shows_summary():
    """Folded long text includes a one-sentence summary."""
    app = _FakeApp()
    long_text = "A股低回撤量化策略的核心思路是通过多因子模型筛选低波动股票。然后进行回测验证。" + "B" * 300
    client = _FakeClient([_chunk(long_text)])
    rc = await stream_chat_to_tui(client, [], app=app)
    assert rc == 0
    contents = [str(m.content) for m in app.writes]
    joined = "".join(contents)
    # Summary (first sentence) present
    assert "A股低回撤量化策略的核心思路是通过多因子模型筛选低波动股票" in joined


@pytest.mark.asyncio
async def test_toggle_fold_cycles_through_multiple_folders():
    """Ctrl+E cycles: expand last -> fold + expand prev -> cycle."""

    app = _FakeApp()

    # Turn 1: long text -> folder[0]
    long1 = "X" * 300
    await stream_chat_to_tui(_FakeClient([_chunk(long1)]), [], app=app)
    tv = app._tv
    assert len(tv._folders) == 1

    # Turn 2: long text -> folder[1]
    long2 = "Y" * 300
    app2 = app
    await stream_chat_to_tui(_FakeClient([_chunk(long2)]), [], app=app2)
    assert len(tv._folders) == 2
    assert tv._active_folder_idx is None

    # Ctrl+E #1: activate last (idx=1), expand
    tv.toggle_fold()
    assert tv._active_folder_idx == 1
    assert tv._folders[1].expanded

    # Ctrl+E #2: fold idx=1, activate idx=0, expand
    tv.toggle_fold()
    assert not tv._folders[1].expanded
    assert tv._active_folder_idx == 0
    assert tv._folders[0].expanded

    # Ctrl+E #3: fold idx=0, cycle back to idx=1, expand
    tv.toggle_fold()
    assert not tv._folders[0].expanded
    assert tv._active_folder_idx == 1
    assert tv._folders[1].expanded


@pytest.mark.asyncio
async def test_begin_streaming_auto_folds_expanded_folder():
    """New input auto-folds the currently expanded folder."""
    app = _FakeApp()
    long_text = "Z" * 300
    await stream_chat_to_tui(_FakeClient([_chunk(long_text)]), [], app=app)
    tv = app._tv

    # Expand the folder
    tv.toggle_fold()
    assert tv._folders[0].expanded

    # New streaming session: should auto-fold
    tv.begin_streaming()
    assert not tv._folders[0].expanded
    assert tv._active_folder_idx is None


@pytest.mark.asyncio
async def test_user_messages_preserved_across_turns():
    """Bug A: user messages written between folders survive auto-fold."""
    app = _FakeApp()

    # Turn 1: long text -> folder[0]
    await stream_chat_to_tui(_FakeClient([_chunk("X" * 300)]), [], app=app)
    tv = app._tv

    # Simulate Turn 2 user message written after folder[0]
    tv.write("")
    tv.write("USER MSG TURN 2")

    # Expand folder[0] so auto-fold has work to do
    tv.toggle_fold()
    assert tv._folders[0].expanded

    # Auto-fold via begin_streaming
    tv.begin_streaming()

    # User message must survive
    assert any("USER MSG TURN 2" in str(l) for l in tv.lines), \
        f"User message lost! Lines: {tv.lines}"


@pytest.mark.asyncio
async def test_blank_lines_preserved_across_turns():
    """Bug B: blank lines between folders survive auto-fold."""
    app = _FakeApp()
    await stream_chat_to_tui(_FakeClient([_chunk("Y" * 300)]), [], app=app)
    tv = app._tv

    tv.write("")
    tv.write("MARKER LINE")

    tv.toggle_fold()
    tv.begin_streaming()

    # Marker line preserved
    assert any("MARKER LINE" in str(l) for l in tv.lines)
    # Blank line preserved
    assert "" in tv.lines


@pytest.mark.asyncio
async def test_re_render_preserves_inter_folder_content():
    """Content between folders is preserved across re-renders."""
    app = _FakeApp()
    # Turn 1
    await stream_chat_to_tui(_FakeClient([_chunk("A" * 300)]), [], app=app)
    # User msg between turns
    app._tv.write("")
    app._tv.write("USER PROMPT")
    # Turn 2
    await stream_chat_to_tui(_FakeClient([_chunk("B" * 300)]), [], app=app)

    tv = app._tv
    assert len(tv._folders) == 2

    # Re-render folder[0] (expand then fold)
    tv.toggle_fold()  # expand last (folder[1])
    tv.toggle_fold()  # fold folder[1], expand folder[0]
    tv.toggle_fold()  # fold folder[0], expand folder[1]
    tv.toggle_fold()  # fold folder[1], expand folder[0]

    # User prompt still preserved
    assert any("USER PROMPT" in str(l) for l in tv.lines)


@pytest.mark.asyncio
async def test_fake_app_write_transcript_helper():
    """Bug C: ResearchApp._write_transcript delegates to write_transcript."""
    from strategy_research.cli.tui.app import ResearchApp

    app = ResearchApp(skip_resume=True)
    # Should not raise AttributeError.
    app._write_transcript("[muted]Started fresh session.[/muted]")


# ─── ChatSession LLM integration ────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_session_dispatches_plain_text_to_llm():
    """When ``llm_client`` is bound, plain-text turns go through AgentLoop."""
    from dataclasses import dataclass
    from dataclasses import field as dc_field

    @dataclass
    class _App:
        writes: list = dc_field(default_factory=list)

        def post_message(self, message) -> None:
            self.writes.append(message)

        def write_transcript(self, content) -> None:
            self.writes.append(content)

        def query_one(self, *_args, **_kw):
            return type("TV", (), {
                "post_message": lambda self, msg: None,
            })()

        def start_thinking(self) -> None:
            pass

        def stop_thinking(self) -> None:
            pass

        def start_streaming(self) -> None:
            pass

        def update_streaming(self, full_text: str) -> None:
            pass

        def update_streaming_delta(self, delta: str) -> None:
            pass

        def end_streaming(self, suffix: str = "") -> str:
            return ""

        def update_header(self, **kw) -> None:
            pass

        def route_agent_event(self, event_type: str, data) -> None:
            pass

    @dataclass
    class _Ctx:
        history: list = dc_field(default_factory=list)

    ctx = _Ctx()
    app = _App()
    client = _FakeClient([_chunk("model reply here")])
    # _run_agent_loop reads llm_client.config as a fallback
    client.config = mock.MagicMock()
    s = ChatSession(ctx, app=app, llm_client=client)

    # Stub AgentLoop to return a fake result
    fake_result = mock.MagicMock()
    fake_result.answer = "model reply here"
    fake_result.error = None

    # Patch the AgentLoop symbol bound in chat_loop (module-level
    # ``from .loop import AgentLoop``) — patching loop.AgentLoop is a no-op
    # once chat_loop has already been imported by earlier tests.
    with mock.patch("strategy_research.core.agent.chat_loop.AgentLoop") as MockLoop, \
         mock.patch("strategy_research.core.agent.builtin_tools.build_default_registry", return_value=None), \
         mock.patch(
             # Force the degraded in-memory path: with the shared session
             # DB unavailable, the assistant reply lands in ctx.history
             # (the legacy behavior this test pins).
             "strategy_research.core.agent.memory_manager.get_default_memory_manager",
             side_effect=RuntimeError("no mm in test"),
         ):
            instance = mock.MagicMock()
            instance.arun = mock.AsyncMock(return_value=fake_result)
            MockLoop.return_value = instance
            rc = await s.dispatch("hi there")

    assert rc == 0
    # Assistant reply appended.
    assert any(t.get("role") == "assistant" for t in ctx.history)


@pytest.mark.asyncio
async def test_chat_session_skips_llm_for_slash_commands():
    """Slash commands go through _DISPATCH only, not the LLM bridge."""
    from dataclasses import dataclass
    from dataclasses import field as dc_field

    @dataclass
    class _App:
        writes: list = dc_field(default_factory=list)
        exit_called: bool = False

        def post_message(self, message) -> None:
            self.writes.append(message)

        def exit(self) -> None:
            self.exit_called = True

        def write_transcript(self, content) -> None:
            self.writes.append(content)

    @dataclass
    class _Ctx:
        history: list = dc_field(default_factory=list)
        debug: bool = False
        pending_prompt: str = ""
        session_id: str = "cli"

    ctx = _Ctx()
    app = _App()
    # A client that would explode if invoked.
    client = mock.MagicMock()
    client.stream.side_effect = AssertionError("stream should NOT be invoked for slash commands")
    s = ChatSession(ctx, app=app, llm_client=client)
    rc = await s.dispatch("/help")
    assert rc == 0
    # Mock client.stream was never called.
    client.stream.assert_not_called()
    # App exited (rc == 0 means /help ran — it does NOT exit). It only
    # exits on /quit with rc=2. So app.exit_called must be False.
    assert not app.exit_called


@pytest.mark.asyncio
async def test_chat_session_no_client_no_llm_call():
    """When ``llm_client is None`` plain text still appends to history only."""
    from dataclasses import dataclass
    from dataclasses import field as dc_field

    @dataclass
    class _App:
        writes: list = dc_field(default_factory=list)

        def post_message(self, message) -> None:
            self.writes.append(message)

    @dataclass
    class _Ctx:
        history: list = dc_field(default_factory=list)

    ctx = _Ctx()
    app = _App()
    s = ChatSession(ctx, app=app, llm_client=None)  # no LLM
    rc = await s.dispatch("hi")
    assert rc == 0
    # No LLM call, but user turn landed in history.
    assert ctx.history == [{"role": "user", "content": "hi"}]
    # No transcripts were posted.
    assert app.writes == []
