"""ResearchApp — top-level Textual application.

Composes:
* Header (title)
* CommandSidebar (left) — clickable slash command list
* TranscriptView (centre) — banner Renderable is the first row
* ActivityRail (right) — event ticker
* ChatInput (bottom) — submit posts ``SynthesizeInput``
* HintFooter (very bottom)

Session orchestration (Commit 2):
* :class:`ChatSession` wraps :func:`cli.interactive.main.process_turn`
  for input dispatch (halt/resume/mandate/slash/quit sentinel).
* Key bindings (Ctrl+C halt, Ctrl+D quit, F1 help, Ctrl+L clear)
  delegate to the session.

Public API:
* :func:`run_tui` (in ``cli/tui/__init__``) — entrypoint for tests
  and the dispatcher.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from textual.app import App
from textual.containers import Horizontal

from strategy_research.cli.interactive.main import InteractiveContext
from strategy_research.cli.tui.keybindings import TUI_BINDINGS
from strategy_research.cli.tui.messages import (
    SynthesizeInput,
    WriteTranscript,
)
from strategy_research.cli.tui.session import ChatSession
from strategy_research.cli.tui.widgets import (
    Banner,
    ChatInput,
    CommandSidebar,
    GoalPanel,
    HintFooter,
    ModeBar,
    ResumeOrNewModal,
    StatusHeader,
    ThinkingSpinner,
    ToolsRail,
    TranscriptView,
)
from strategy_research.cli.tui.workers.workflow_worker import (
    WorkflowWorker,
)

# CSS_PATH is resolved relative to the file defining the App — Textual
# looks for a sibling ``.tcss`` at import time.
_HERE = os.path.dirname(os.path.abspath(__file__))


class ResearchApp(App):
    """Top-level Textual app for QuantNodes-Research."""

    CSS_PATH = os.path.join(_HERE, "styles.tcss")
    TITLE = "QuantNodes Strategy-Research"

    BINDINGS = list(TUI_BINDINGS)

    def __init__(
        self,
        *,
        model: str = "unknown",
        version: str = "0.4.2",
        session_db_path: Optional[str] = None,
        skip_resume: bool = False,
        llm_client: Optional[Any] = None,
    ) -> None:
        super().__init__()
        self._model = model
        self._version = version
        self._session_db_path = session_db_path
        # Per-session state shared with the legacy REPL (process_turn dispatcher).
        self.ctx = InteractiveContext()
        self.banner: Optional[Banner] = None
        self.session: Optional[ChatSession] = None
        # When True, skip the resume-or-new modal. Used by tests and
        # by the ``--repl`` legacy escape hatch when resuming a specific
        # session by id (handled in a later commit).
        self._skip_resume = skip_resume
        # Optional OpenAI-compat client for streaming plain-text turns.
        self._llm_client = llm_client
        # Tool-call totals (used by StatusHeader). Updated in
        # ``_bump_tool_count`` when ``tool_call`` / ``tool_result`` events
        # arrive via ``route_agent_event``.
        self._tool_total: int = 0
        self._tool_ok: int = 0
        # Active workflow worker (Phase 4 v0.5.2). Created by
        # ``start_workflow()`` when the user runs ``/goal start --workflow``.
        self._workflow_worker: Optional[WorkflowWorker] = None

        # PR2 (tui-text-routing): tracks the active text segment so we
        # can avoid double-writing when an ``assistant_message`` falls
        # back event arrives after ``text.ended`` already finalized.
        # ``_active_text_id`` is set by text.started, cleared by
        # text.ended (or when a new segment starts).
        self._active_text_id: Optional[str] = None
        self._finalized_text_ids: set[str] = set()

    def compose(self):
        yield StatusHeader(id="status-header")
        yield ModeBar(id="mode-bar")
        yield GoalPanel(id="goal-panel")
        with Horizontal(id="main-row"):
            yield CommandSidebar(id="sidebar", classes="hidden")
            yield TranscriptView(id="transcript")
            yield ToolsRail(id="rail")
        yield ThinkingSpinner(id="thinking-spinner")
        yield ChatInput(id="input")
        yield HintFooter()

    # ------------------------------------------------------------------ on_mount

    async def on_mount(self) -> None:
        # 0) Resolve model + session id eagerly so the banner + header
        #    show the real provider (e.g. "minimax-M3") instead of the
        #    "unknown" default, and "sid:cli-xxxxxxxx" instead of the
        #    bare "cli" placeholder. The pure logic lives in
        #    ``_resolve_session_identity`` so it is unit-testable in
        #    isolation (see tests/test_session_id_init.py).
        model, sid = self._resolve_session_identity()

        # 1) Banner Renderable sits as the first row inside the transcript.
        from strategy_research.cli.ui.banner import render_banner
        transcript = self.query_one(TranscriptView)
        self.banner = Banner(model=model, version=self._version, mode="tui")
        banner_text = render_banner(
            model=model, version=self._version, mode="tui"
        )
        transcript.write(banner_text)

        # 1a) Push the resolved model + session id into the header so it
        #     is correct from the very first frame (not only after the
        #     first user input which would lazily trigger
        #     ``_update_header_stats``).
        try:
            self.update_header(
                model=model,
                session_id=sid,
                connection_status="idle",
            )
        except Exception:
            pass

        # 2) Construct the session and bind it to the dispatch surface.
        self.session = ChatSession(
            self.ctx, app=self, llm_client=self._llm_client,
        )

        # 3) Decide whether to push the resume modal.
        if self._skip_resume:
            return

        latest_title = self._probe_latest_session_title()
        # Only ask if a prior session exists. Otherwise auto-new.
        if latest_title is None:
            return

        # Push the modal; the callback wires the user's choice into the session.
        modal = ResumeOrNewModal(latest_session=latest_title)
        self.push_screen(modal, self._on_resume_choice)

    # ------------------------------------------------------------------ identity

    def _resolve_session_identity(self) -> tuple[str, str]:
        """Resolve ``(model, session_id)`` at startup.

        Pure side-effects: only mutates ``self._model`` and
        ``self.ctx.session_id``. Returns the resolved values so the
        caller (``on_mount``) can apply them to Banner / StatusHeader
        widgets in one place.

        Resolution rules:

        1. **model** — read from ``LLMConfig.load().model``. If the
           config has no model or the load fails, keep whatever was
           passed to ``__init__`` (typically ``"unknown"``).
        2. **session_id** — only generate a fresh ``cli-xxxxxxxx``
           when the current value is the bare ``"cli"`` default. We
           never overwrite a pre-existing id (set by a test fixture,
           the resume-from-disk path, or a future ``--sid=...`` CLI
           flag). If UUID generation fails, fall back to the literal
           ``"cli-fallback"`` so the header still has *something*
           to display.

        Returns:
            ``(model, session_id)`` — what was actually used after
            resolution (so the caller can reuse the values without
            re-reading ``self._model`` / ``self.ctx.session_id``).
        """
        # 1) Resolve model
        model = self._model
        try:
            from strategy_research.core.llm.config import LLMConfig
            cfg = LLMConfig.load()
            if cfg.model:
                model = cfg.model
                self._model = model
        except Exception:
            pass

        # 2) Generate fresh session id if still the bare "cli"
        sid = self.ctx.session_id
        if sid == "cli":
            try:
                import uuid
                sid = f"cli-{uuid.uuid4().hex[:8]}"
            except Exception:
                sid = "cli-fallback"
            self.ctx.session_id = sid

        return model, sid

    # ------------------------------------------------------------------ resume

    def _probe_latest_session_title(self) -> Optional[str]:
        """Return the most-recent persisted session's title, or None.

        B4 Step 3: reads the unified DB. The legacy ``SessionDB`` was queried
        here before, and its dataclass had no ``title`` field, so this always
        returned "(untitled)".
        """
        try:
            from strategy_research.core.session.unified import list_sessions
            sessions = list_sessions(limit=1)
        except Exception:
            return None
        if not sessions:
            return None
        return sessions[0].get("title") or "(untitled)"

    def _on_resume_choice(self, choice: tuple[bool, Optional[str]]) -> None:
        """Callback: take the (resume, pending_input) tuple."""
        if choice is None:
            return
        is_resume, pending_input = choice
        if self.session is None:
            return
        if is_resume:
            self._resume_most_recent_session()
        else:
            self._write_transcript("[muted]Started fresh session.[/muted]")
        if pending_input and self.session is not None:
            self.session.enqueue(pending_input)

    def _resume_most_recent_session(self) -> None:
        """Restore history + session_id from the most-recent persisted session.

        B4 Step 3: this never actually worked. It read ``session.session_id``
        and ``session.title`` off the legacy dataclass, neither of which
        existed -- ``session_id`` was always None and the method returned
        early. The unified row has a real ``id``, so resume now functions.
        """
        try:
            from strategy_research.core.session.unified import (
                get_messages,
                list_sessions,
            )
            sessions = list_sessions(limit=1)
        except Exception:
            self._write_transcript("[muted]Could not load prior session.[/muted]")
            return
        if not sessions:
            return
        sid = sessions[0].get("id")
        title = sessions[0].get("title") or "(untitled)"
        if not sid:
            return
        self.ctx.session_id = sid
        try:
            messages = get_messages(sid, limit=20)
            history = [
                {"role": m["role"], "content": m["content"]}
                for m in messages
                if m["role"] in {"user", "assistant"}
                and (m["content"] or "").strip()
                # Skip compaction events (opencode-aligned, hidden from UI)
                and (m.get("message_type") or m["role"]) != "compaction"
            ][-6:]
        except Exception:
            history = []
        self.ctx.history = history
        self._write_transcript(
            f"[muted]Resumed session: {title} ({len(history)} prior turns)[/muted]"
        )

    # ------------------------------------------------------------------ inbound

    def write_transcript(self, content: Any) -> None:
        """Forward a Renderable into the mounted TranscriptView."""
        try:
            transcript = self.query_one(TranscriptView)
        except Exception:
            return
        transcript.post_message(WriteTranscript(content=content))

    def _write_transcript(self, content: Any) -> None:
        """Internal alias used by ``on_mount`` resume helpers.

        Delegates to the public :meth:`write_transcript` so internal
        callers (``_on_resume_choice``, ``_resume_most_recent_session``)
        can post session-info lines without hitting ``AttributeError``.
        """
        self.write_transcript(content)

    def write_rail(self, event_type: str, data: dict) -> None:
        """Forward an agent event into the mounted ToolsRail."""
        try:
            rail = self.query_one(ToolsRail)
        except Exception:
            return
        rail.handle_event(event_type, data)

    def update_header(self, **kwargs: Any) -> None:
        """Update the StatusHeader with new values."""
        try:
            header = self.query_one(StatusHeader)
        except Exception:
            return
        header.update_status(**kwargs)

    def update_tools_rail_goal(self, **kwargs: Any) -> None:
        """Update the ToolsRail goal section."""
        try:
            rail = self.query_one(ToolsRail)
        except Exception:
            return
        rail.update_goal(**kwargs)

    def route_agent_event(self, event_type: str, data: dict) -> None:
        """Route AgentLoop events to the appropriate widget.

        PR2 (tui-text-routing): subscribes to the opencode-style 3-step
        text protocol (text.started -> text_delta -> text.ended). The
        legacy text_delta-only path is kept as a fallback for old
        backends (no text.started ever arrives).
        """
        if event_type == "text.started":
            # Begin a new streaming segment. Each LLM iteration (or
            # one-shot final answer) gets its own streamer so multi-
            # iteration flows don't merge text into one big block.
            text_id = data.get("text_id") if isinstance(data, dict) else None
            self.begin_streaming_session(text_id=text_id)
        elif event_type == "text_delta":
            # Strip  THINK / <reasoning> / <thinking> / <|reasoning|>
            # etc. before forwarding to the streamer so the user never
            # sees the model's internal monologue during typing.
            from strategy_research.cli.tui.text_filters import strip_thinking_tags
            self.update_streaming_delta(strip_thinking_tags(data.get("text", "")))
        elif event_type == "text.ended":
            # Finalize the current streaming segment (keep visible as a
            # folder). On the next text.started a fresh segment begins.
            self.end_streaming_session()
        elif event_type == "thinking_done":
            # Transition marker: thinking → text. Streaming already started.
            pass
        elif event_type == "assistant_message":
            self._route_assistant_message(data)
        elif event_type in ("tool_call", "tool_result", "tool_progress", "tool_heartbeat"):
            # Stage C: tool calls are rendered inline in the transcript,
            # not the side rail. See TranscriptView.append_tool_call /
            # update_tool_result.
            self._route_tool_event(event_type, data)
        elif event_type == "compact":
            self.write_rail("compact", data)
        elif event_type == "llm_usage":
            self.update_header(token_used=data.get("output_tokens", 0))
        elif event_type == "iter_start":
            self._route_iter_start(data)
        elif event_type == "iter_end":
            self.stop_thinking()
            try:
                tv = self.query_one(TranscriptView)
                tv.append_done()
            except Exception:
                pass
        elif event_type == "thinking_start":
            self.start_thinking()
        elif event_type == "thinking_end":
            self.stop_thinking()
        elif event_type == "error":
            self._route_error_event(data)

    def _route_assistant_message(self, data: dict) -> None:
        """Render a final assistant message (one-shot fallback path)."""
        from strategy_research.cli.tui.content_formatter import reformat_body_content
        from strategy_research.cli.tui.text_filters import extract_thinking_tags

        raw_content = data.get("content", "") or ""
        think_content, body_content = extract_thinking_tags(raw_content)
        try:
            tv = self.query_one(TranscriptView)
            # Dedup: if we've already finalized a segment for this
            # turn, the assistant_message is a duplicate from a
            # non-streaming fallback path. Skip the write.
            if self._finalized_text_ids and not tv._streamer:
                return
            if think_content:
                tv.append_thinking(think_content)
            if tv._streamer is not None:
                tv._truncate_to(tv._stream_baseline)
                tv._streamer = None
                tv._stream_baseline = None
            tv.write_assistant_message(reformat_body_content(body_content))
            self._finalized_text_ids.clear()
            self._active_text_id = None
        except Exception:
            pass

    def _route_iter_start(self, data: dict) -> None:
        """Handle the iter_start event (thinking + header + rail)."""
        self.start_thinking()
        self.update_header(
            iter_count=data.get("iteration", 0),
            iter_max=data.get("max_iterations", 0),
        )
        try:
            rail = self.query_one(ToolsRail)
            rail.set_iter(data.get("iteration", 0), data.get("max_iterations", 0))
        except Exception:
            pass

    def _route_error_event(self, data: dict) -> None:
        """Render an error event into the transcript."""
        try:
            tv = self.query_one(TranscriptView)
            from strategy_research.cli.tui.messages import WriteTranscript
            message = data.get("message", "unknown")
            if "quota" in message.lower():
                friendly = "[yellow]\u26a0 MiniMax \u914d\u989d\u5df2\u7528\u5b8c\uff085\u5c0f\u65f6\u9650\u989d\uff09\u3002\u8bf7\u7a0d\u540e\u91cd\u8bd5\u6216\u5207\u6362 provider\u3002[/yellow]"
            else:
                friendly = f"[red]Agent error:[/red] {message}"
            tv.post_message(WriteTranscript(content=friendly))
        except Exception:
            pass

    def _route_tool_event(self, event_type: str, data: dict) -> None:
        """Inline tool events into TranscriptView (stage C)."""
        try:
            tv = self.query_one(TranscriptView)
        except Exception:
            return
        if event_type == "tool_call":
            call_id = data.get("call_id", "")
            tool = data.get("tool", "?")
            args = data.get("args", {}) or {}
            tv.append_tool_call(call_id, tool, args)
            self._record_tool_start()
        elif event_type == "tool_result":
            call_id = data.get("call_id", "")
            ok = bool(data.get("ok", True))
            elapsed_ms = int(data.get("elapsed_ms", 0))
            tv.update_tool_result(call_id, ok, elapsed_ms)
            self._record_tool_result(ok=ok)

    def _record_tool_start(self) -> None:
        """Increment the tool-call counter and refresh the header."""
        try:
            self._tool_total += 1
            self.update_header(
                tool_count=self._tool_total,
                tool_ok=self._tool_ok,
            )
        except Exception:
            pass

    def _record_tool_result(self, *, ok: bool) -> None:
        """Increment the tool-success counter (only on success)."""
        try:
            if ok:
                self._tool_ok += 1
            self.update_header(
                tool_count=self._tool_total,
                tool_ok=self._tool_ok,
            )
        except Exception:
            pass

    def start_thinking(self) -> None:
        try:
            spinner = self.query_one(ThinkingSpinner)
            spinner.start()
        except Exception:
            pass

    def stop_thinking(self) -> None:
        try:
            spinner = self.query_one(ThinkingSpinner)
            spinner.stop()
        except Exception:
            pass

    def start_streaming(self) -> None:
        try:
            spinner = self.query_one(ThinkingSpinner)
            spinner.stop()
        except Exception:
            pass
        try:
            tv = self.query_one(TranscriptView)
            tv.begin_streaming()
        except Exception:
            pass

    def update_streaming(self, full_text: str) -> None:
        try:
            tv = self.query_one(TranscriptView)
            tv.update_streaming(full_text)
        except Exception:
            pass

    def update_streaming_delta(self, delta: str) -> None:
        """Accumulate a text_delta into the active streaming session."""
        try:
            tv = self.query_one(TranscriptView)
            if tv._streamer is None:
                # No active streaming session — start one for this delta
                tv.begin_streaming()
            streamer = tv._streamer
            if streamer is None:
                return
            streamer.append_delta(delta)
            tv._truncate_to(tv._stream_baseline)
            rendered = streamer.render()
            if rendered:
                tv.write(rendered)
        except Exception:
            pass

    def end_streaming(self, suffix: str = "") -> str:
        try:
            tv = self.query_one(TranscriptView)
            return tv.end_streaming(suffix=suffix)
        except Exception:
            return ""

    # ------------------------------------------------------------------ PR2 (tui-text-routing)

    def begin_streaming_session(self, text_id: str | None = None) -> None:
        """Start a new streaming segment (PR2: text.started).

        Args:
            text_id: Identifier for this segment. Used by dedup logic
                to skip ``assistant_message`` events that arrive after
                we already finalized via ``text.ended``.

        Idempotent: if a streamer is already active, finalize it first
        so the new segment starts fresh. This handles back-to-back
        text.started events (e.g. when the LLM resumes streaming
        after a tool call).
        """
        try:
            tv = self.query_one(TranscriptView)
            if tv._streamer is not None:
                # Auto-finalize any previous segment that hasn't ended
                # (e.g. duplicate text.started, or backend skipped
                # text.ended). Otherwise deltas would bleed over.
                tv.end_streaming()
            tv.begin_streaming()
            self._active_text_id = text_id
        except Exception:
            pass

    def end_streaming_session(self) -> None:
        """Finalize the current streaming segment (PR2: text.ended).

        Safe to call when no segment is active (no-op). The final
        content is kept as a folder (Ctrl+E to expand) so users can
        revisit past turns.
        """
        try:
            tv = self.query_one(TranscriptView)
            if tv._streamer is None:
                return
            tv.end_streaming()
            # Track the active id as finalized so a subsequent
            # assistant_message fallback knows to skip.
            if self._active_text_id is not None:
                self._finalized_text_ids.add(self._active_text_id)
            self._active_text_id = None
        except Exception:
            pass

    async def on_synthesize_input(self, message: SynthesizeInput) -> None:
        """Route ``ChatInput.Submitted`` / sidebar clicks to the session."""
        if self.session is None:
            return
        await self.session.on_synthesize_input(message)

    # ------------------------------------------------------------------ keybindings

    def action_halt(self) -> None:
        """Ctrl+C — copy selected text, or trip the kill switch.

        Smart behaviour: if the user has text selected in the
        TranscriptView, Ctrl+C copies it to the clipboard (via OSC 52)
        and clears the selection.  Only when *nothing* is selected does
        it trigger the kill switch — matching standard terminal / editor
        muscle memory.
        """
        try:
            selected = self.screen.get_selected_text()
        except Exception:
            selected = None
        if selected:
            self.copy_to_clipboard(selected)
            self.screen.clear_selection()
            n = len(selected)
            self.write_transcript(f"[dim]已复制 {n} 字符[/dim]")
            return
        if self.session is None:
            return
        self.session.trip_halt(reason="ctrl+c")

    def action_quit_app(self) -> None:
        """Ctrl+D — leave the TUI cleanly."""
        self.exit()

    def action_resume(self) -> None:
        """``/resume``-style recovery — clear the kill switch."""
        if self.session is None:
            return
        self.session.clear_halt()

    def action_show_help(self) -> None:
        """F1 — render the help table into the transcript."""
        try:
            import io

            from rich.console import Console as RichConsole

            from strategy_research.cli.commands.help import render_help_table
            buf = io.StringIO()
            render_help_table(console=RichConsole(file=buf, force_terminal=False))
            if buf.getvalue():
                self.write_transcript(buf.getvalue())
        except Exception:
            self.write_transcript("[muted]/help not yet rendered in TUI v1.[/muted]")

    def action_clear_transcript(self) -> None:
        """Ctrl+L — wipe the chat log."""
        try:
            tv = self.query_one(TranscriptView)
            tv.clear_log()
        except Exception:
            pass
        # Also wipe memory for the session so re-runs start clean.
        if self.session is not None:
            self.ctx.history = []
            # Phase 7+8: also clear in MemoryManager (SQLite)
            try:
                import asyncio

                from strategy_research.core.agent.memory_manager import (
                    get_default_memory_manager,
                )
                mm = get_default_memory_manager()
                asyncio.get_event_loop().run_until_complete(
                    mm.clear(getattr(self.ctx, "session_id", ""))
                )
            except Exception:
                pass

    def action_toggle_sidebar(self) -> None:
        """Ctrl+1 — toggle the Commands sidebar visibility."""
        try:
            sidebar = self.query_one("#sidebar")
            if "hidden" in sidebar.classes:
                sidebar.remove_class("hidden")
            else:
                sidebar.add_class("hidden")
        except Exception:
            pass

    def action_toggle_tools_rail(self) -> None:
        """Ctrl+2 - toggle the ToolsRail visibility."""
        try:
            rail = self.query_one("#rail")
            if "hidden" in rail.classes:
                rail.remove_class("hidden")
            else:
                rail.add_class("hidden")
        except Exception:
            pass

    def action_toggle_fold(self) -> None:
        """Ctrl+E - expand/collapse the most recent folded record."""
        try:
            tv = self.query_one(TranscriptView)
            tv.toggle_fold()
        except Exception:
            pass

    def action_toggle_mode(self) -> None:
        """Ctrl+M — toggle chat/goal interactive mode."""
        if self.session is None:
            return
        ctx = self.session.ctx
        new_mode = "goal" if ctx.interactive_mode == "chat" else "chat"
        ctx.interactive_mode = new_mode
        # Update the mode bar
        try:
            bar = self.query_one("#mode-bar", ModeBar)
            bar.update_mode(new_mode)
        except Exception:
            pass
        # Show brief notification in transcript
        label = "策略研究（JSON 输出）" if new_mode == "goal" else "普通聊天（自然语言）"
        self.write_transcript(f"[dim]已切换到 {label} 模式[/dim]")

    def action_toggle_goal_continuation(self) -> None:
        """Ctrl+G — pause/resume goal auto-continuation.

        Phase 4 v0.5.2: If a workflow worker is active, Ctrl+G pauses it
        first (immediate=True). Falls back to the legacy continuation
        toggle when no workflow is running.
        """
        # 1) Pause active workflow (highest priority)
        worker = self._workflow_worker
        if worker is not None and worker.is_running:
            worker.cancel(immediate=True)
            return

        # 2) Legacy continuation toggle
        if self.session is None:
            return
        self.session.toggle_goal_continuation()

    # ── Phase 4 v0.5.2 — workflow integration ──────────────────

    def start_workflow(self, runner: Any) -> WorkflowWorker:
        """Attach a GoalWorkflowRunner to a fresh WorkflowWorker.

        Subscribes the GoalPanel widget as an event observer so the
        panel updates in real time as the workflow executes. Replaces
        any previously attached worker.

        Args:
            runner: A GoalWorkflowRunner with a populated config and
                event_bus.

        Returns:
            The newly created WorkflowWorker.
        """
        # Unsubscribe from previous worker's runner to avoid leaks.
        prev_worker = self._workflow_worker
        if prev_worker is not None:
            prev_worker._unsubscribe_panel_observer()

        worker = WorkflowWorker(runner, self)
        self._workflow_worker = worker
        # Subscribe the panel observer immediately so events emitted
        # during the very first layer are captured.
        worker._subscribe_panel_observer()
        return worker

    def update_goal_panel(self) -> None:
        """Refresh GoalPanel from the current goal snapshot.

        Called by _sync_interactive_mode in ChatSession after each turn.
        """
        try:
            panel = self.query_one("#goal-panel", GoalPanel)
        except Exception:
            return
        if self.session is None:
            panel.clear_goal()
            return
        try:
            from strategy_research.core.goal import GoalStore
            store = GoalStore()
            snapshot = store.get_current_snapshot(self.session.ctx.session_id)
            if snapshot is None:
                panel.clear_goal()
                return
            goal = snapshot["goal"]
            panel.update_goal(
                objective=goal.get("objective", ""),
                status=goal.get("status", ""),
                progress=goal.get("progress_percent", 0.0),
                criteria=snapshot.get("criteria", []),
                evidence_count=snapshot.get("evidence_count", 0),
                goal_id=goal.get("goal_id", ""),
                continuation_paused=getattr(self.session, "_goal_continuation_paused", False),
            )
        except Exception:
            panel.clear_goal()


__all__ = ["ResearchApp"]
