"""TranscriptView - scrolling log of assistant turns + system messages.

Wraps Textual's :class:`textual.widgets.RichLog` so we can feed it Rich
``RenderableType`` objects (the output of ``cli.ui.transcript.render_answer``)
directly. The widget auto-scrolls to the latest line; consumers post a
``WriteTranscript`` message which the parent app forwards to ``write()``.

Streaming + fold (TUI display philosophy):
    1. **Streaming**: ``begin_streaming()`` records a line baseline,
       ``update_streaming(text)`` truncates back to that baseline and
       re-writes the folded tail.  No new lines accumulate.

    2. **Folded record**: ``end_streaming(suffix)`` converts the
       streamer into a *folder* appended to a list.  Each folder is
       independently toggleable via ``toggle_fold()`` (Ctrl+E).

    3. **Auto-fold**: ``begin_streaming()`` auto-folds the currently
       active (expanded) folder so the screen stays compact.

Multi-folder cursor (Ctrl+E):
    Ctrl+E cycles through all folders.  The first press activates the
    last folder (expand).  Subsequent presses fold the current one and
    expand the previous (cyclic).  This lets the user inspect any past
    turn without losing context.

Inline tool calls (Stage C):
    Tool calls live as 1-2 lines parallel to streaming text:

        ⏳ read_file · {"path": "..."}      ← append_tool_call
        ✔ read_file · 0.3s                  ← update_tool_result

    State is tracked in ``_tool_lines: {call_id: line_index}`` so the
    result line can be replaced in-place via ``_truncate_to``.

Body content as Markdown (streaming v2):
    The assistant's final answer is rendered as Rich ``Markdown`` via
    ``write_assistant_message`` so headers / bold / lists / code blocks
    / tables display correctly. Body content is **not** folded — the
    RichLog handles overflow natively via wrap + scroll.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Iterable

from rich.console import RenderableType
from textual.widgets import RichLog

from strategy_research.interfaces.cli.tui.messages import WriteTranscript
from strategy_research.interfaces.cli.tui.widgets.streaming_text import StreamingText

if TYPE_CHECKING:
    from textual.geometry import Strip

_ARGS_PREVIEW_MAX = 80


class TranscriptView(RichLog):
    """Auto-scrolling chat log widget with streaming + fold support.

    Public API:
        write(content): append a Renderable on a new line.
        begin_streaming(): start an in-place streaming session.
        update_streaming(text): replace streaming content in-place.
        end_streaming(suffix) -> str: finalize, return full text.
        toggle_fold(): cycle fold/expand through folders (Ctrl+E).
    """

    DEFAULT_CSS = """
    TranscriptView {
        height: 1fr;
        scrollbar-size: 1 1;
    }
    """

    BORDER_TITLE = "Transcript"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(wrap=True, markup=True, highlight=False, **kwargs)
        self._stream_baseline: int | None = None
        self._streamer: StreamingText | None = None
        self._folders: list[StreamingText] = []
        self._fold_baselines: list[int] = []
        self._fold_line_counts: list[int] = []
        self._active_folder_idx: int | None = None
        # Inline tool-call state (Stage C):
        # _tool_lines:  call_id → line index of the in-flight "⏳ tool · args" line
        # _tool_names:  call_id → tool name (cached so update_tool_result can render it)
        self._tool_lines: dict[str, int] = {}
        self._tool_names: dict[str, str] = {}
        # Mouse selection support: plain-text mirror for get_selection()
        self._plain_lines: list[str] = []

    def append(self, content: RenderableType | str) -> None:
        """Append ``content`` on a new line (alias for ``write``)."""
        self.write(content)

    def write_lines(self, lines: Iterable[RenderableType | str]) -> None:
        """Append a batch of lines."""
        for ln in lines:
            self.write(ln)

    def clear_log(self) -> None:
        """Drop all lines (used by ``ctrl+l`` action)."""
        self.clear()
        self._stream_baseline = None
        self._streamer = None
        self._folders = []
        self._fold_baselines = []
        self._fold_line_counts = []
        self._active_folder_idx = None
        self._tool_lines = {}
        self._tool_names = {}
        self._plain_lines = []

    def append_done(self) -> None:
        """Write a ``Done.`` marker line to close a turn."""
        self.write("[dim]\u2022 Done.[/dim]")

    # ------------------------------------------------------------------ mouse selection

    def write(
        self,
        content: RenderableType | str,
        *args: Any,
        **kwargs: Any,
    ) -> "TranscriptView":
        """Override write to also store plain text for mouse selection.

        ``*args`` is required: ``RichLog.on_resize`` replays deferred
        writes via ``self.write(*deferred_render)`` which unpacks
        ``DeferredRender(content, width, expand, shrink, scroll_end)``
        as 5 positional args. Without ``*args`` this raises
        ``TypeError: takes 2 positional arguments but 6 were given``
        at resize time.
        """
        if isinstance(content, str):
            self._plain_lines.append(content)
        else:
            try:
                import io

                from rich.console import Console as RichConsole
                buf = io.StringIO()
                console = RichConsole(file=buf, force_terminal=False, width=200)
                console.print(content, end="")
                self._plain_lines.append(buf.getvalue().rstrip("\n"))
            except Exception:
                self._plain_lines.append(str(content))
        return super().write(content, *args, **kwargs)

    def render_line(self, y: int) -> "Strip":
        """Render a line with offset metadata for mouse selection.

        Calls ``Strip.apply_offsets()`` so the Textual compositor can
        map screen coordinates to content positions. Also applies
        selection highlighting when a drag-select is active.
        """
        scroll_x, scroll_y = self.scroll_offset
        strip = self._render_line(scroll_y + y, scroll_x, self.size.width)

        # Embed offset metadata — without this the compositor cannot
        # determine which content position the user clicked on.
        strip = strip.apply_offsets(scroll_x, y)

        # Apply selection highlight if a selection is active.
        selection = self.text_selection
        if selection is not None:
            span = selection.get_span(y)
            if span is not None:
                start, end = span
                if end == -1:
                    end = len(strip.text)
                try:
                    sel_style = self.screen.get_component_rich_style(
                        "screen--selection"
                    )
                    strip.stylize_before(sel_style, start, end)
                except Exception:
                    pass

        return strip

    def get_selection(self, selection: Any) -> tuple[str, str] | None:
        """Extract plain text under the selection range.

        Uses the ``_plain_lines`` mirror maintained by ``write()`` to
        return the selected text without needing to re-render Rich
        renderables.
        """
        text = "\n".join(self._plain_lines)
        return selection.extract(text), "\n"

    def selection_updated(self, selection: Any) -> None:
        """Called when the selection changes — invalidate render cache."""
        self._line_cache.clear()
        self.refresh()

    # ------------------------------------------------------------------ markdown body

    def write_markdown(self, content: str) -> None:
        """Render ``content`` as Rich Markdown and append (no fold).

        Markdown features supported out of the box:
            * ATX / Setext headers (# ## ###)
            * **bold**, *italic*, ~~strike~~
            * `inline code` (Python lexer fallback)
            * ``` fenced code blocks ``` with monokai syntax highlighting
            * bullet + numbered lists
            * GFM tables
            * blockquotes
            * links

        Empty input renders a ``(empty response)`` muted hint so the
        user gets explicit feedback rather than silent failure.
        """
        from rich.markdown import Markdown

        if not content or not content.strip():
            self.write("[muted](empty response)[/muted]")
            return
        # Strip trailing whitespace; Rich Markdown renders trailing
        # blank lines as extra vertical space.
        cleaned = content.rstrip()
        md = Markdown(
            cleaned,
            code_theme="monokai",
            inline_code_lexer="python",
            justify="left",
        )
        self.write(md)

    def write_assistant_message(self, content: str) -> None:
        """Final assistant message: replace streaming area with Markdown.

        Three steps:

        1. End any active streamer (text_delta preview has been
           accumulating plain text into the streaming session).
        2. Truncate the streaming baseline — those raw preview lines
           are superseded by the formatted Markdown renderable.
        3. Append the Markdown-rendered content.

        The net effect: during generation the user sees a typewriter
        preview; on completion the preview is replaced by the same
        text rendered with headers / bold / code formatting intact.
        No fold is applied to body content — overflow is handled by
        RichLog's native wrapping + scroll.
        """
        if self._streamer is not None:
            self._truncate_to(self._stream_baseline)
            self._streamer = None
            self._stream_baseline = None
        self.write_markdown(content)

    # ------------------------------------------------------------------ thinking folder

    def append_thinking(self, content: str) -> None:
        """Render think content as a foldable section (collapsed by default).

        Pushes a :class:`StreamingText` folder into ``_folders`` so the
        existing :meth:`toggle_fold` (Ctrl+E) can expand/collapse it like
        any other turn record. While folded, only the header line is
        visible — the actual reasoning content is hidden until the user
        explicitly opts in.

        Args:
            content: Raw think content extracted from ``<think>...</think>``
                or unclosed tag variants. Should already be stripped of
                tag delimiters. Empty / whitespace-only input is a no-op.

        Side effects:
            * Appends one header line to the RichLog.
            * Registers the folder in ``_folders`` / ``_fold_baselines``
              / ``_fold_line_counts`` with ``_active_folder_idx`` left at
              ``None`` (the think folder does not steal focus from the
              currently active streaming folder).
            * Existing :meth:`toggle_fold` will pick up the new folder on
              next invocation (it activates the last folder when no
              folder is active).
        """
        if not content or not content.strip():
            return

        # Build a StreamingText folder with the think content.
        folder = StreamingText()
        folder.start()
        folder.append_delta(content)
        self._folders.append(folder)

        # Record the line boundary for in-place re-rendering on
        # Ctrl+E. Initial line_count = 1 (just the header line).
        start = len(self.lines)
        self._fold_baselines.append(start)
        self._fold_line_counts.append(1)

        # Header: 💭 thinking (N chars · ctrl+e to expand)
        n_chars = len(content)
        n_lines = content.count("\n") + 1
        self.write(
            f"[dim]\U0001f4ad thinking "
            f"({n_chars} chars \u00b7 {n_lines} lines \u00b7 ctrl+e to expand)[/dim]"
        )

    # ------------------------------------------------------------------ inline tools (Stage C)

    def append_tool_call(self, call_id: str, tool: str, args: dict) -> None:
        """Write an inline ``⏳ tool · {args preview}`` line for a new tool call.

        Args:
            call_id: Unique tool-call identifier (used by ``update_tool_result``
                to locate the line and replace it in-place).
            tool: Tool name (e.g. ``"read"``).
            args: Tool arguments dict (JSON-serialised for display).

        Side effects:
            * Appends one styled line to the RichLog.
            * Stores ``call_id → line_index`` in ``_tool_lines``.
            * Caches ``call_id → tool`` in ``_tool_names`` for the result.
        """
        args_str = self._format_args_preview(args)
        line = f"[muted]\u23f3 [bold]{tool}[/bold] \u00b7 {args_str}[/muted]"
        self.write(line)
        self._tool_lines[call_id] = len(self.lines) - 1
        self._tool_names[call_id] = tool

    def update_tool_result(self, call_id: str, ok: bool, elapsed_ms: int) -> None:
        """Replace the ``⏳`` line for ``call_id`` with a ``✔/✘`` result line.

        If ``call_id`` is unknown (e.g. event arrived after a ``clear_log``),
        the call is silently ignored — no crash, no orphan write.

        Args:
            call_id: Same id passed to ``append_tool_call``.
            ok: True for success (green check), False for error (red cross).
            elapsed_ms: Execution duration; rendered as ``0.3s`` / ``320ms``.

        Side effects:
            * Truncates to the line index recorded by ``append_tool_call``.
            * Writes the styled result line.
            * Removes the call from ``_tool_lines`` / ``_tool_names``.
        """
        if call_id not in self._tool_lines:
            return
        line_idx = self._tool_lines.pop(call_id)
        tool = self._tool_names.pop(call_id, "?")
        elapsed_str = self._format_elapsed(elapsed_ms)
        symbol = "\u2714" if ok else "\u2718"
        style = "success" if ok else "error"
        new_line = f"[{style}]{symbol} [bold]{tool}[/bold] \u00b7 {elapsed_str}[/{style}]"
        self._truncate_to(line_idx)
        self.write(new_line)

    @staticmethod
    def _format_args_preview(args: dict) -> str:
        """JSON-encode args with truncation at ``_ARGS_PREVIEW_MAX`` chars."""
        try:
            args_str = json.dumps(args, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            args_str = repr(args)
        if len(args_str) > _ARGS_PREVIEW_MAX:
            args_str = args_str[:_ARGS_PREVIEW_MAX - 3] + "..."
        return args_str

    @staticmethod
    def _format_elapsed(elapsed_ms: int) -> str:
        """Format milliseconds as ``320ms`` (<1s) or ``1.2s`` (>=1s)."""
        if elapsed_ms < 1000:
            return f"{elapsed_ms}ms"
        return f"{elapsed_ms / 1000:.1f}s"

    # ------------------------------------------------------------------ streaming

    def begin_streaming(self) -> None:
        """Start an in-place streaming session.

        Auto-folds the currently active (expanded) folder so the screen
        stays compact before the new turn begins.
        """
        if self._active_folder_idx is not None:
            idx = self._active_folder_idx
            if self._folders[idx].expanded:
                self._re_render_folder(idx, expand=False)
        self._active_folder_idx = None
        self._stream_baseline = len(self.lines)
        self._streamer = StreamingText()
        self._streamer.start()

    def update_streaming(self, text: str) -> None:
        """Replace streaming content in-place (no new lines accumulated)."""
        if self._streamer is None:
            return
        self._streamer.update_streaming(text)
        self._truncate_to(self._stream_baseline)
        rendered = self._streamer.render()
        if rendered:
            self.write(rendered)

    def end_streaming(self, suffix: str = "") -> str:
        """Finalize streaming session.

        Appends the streamer as a new folder to the list.
        ``suffix`` (e.g. stats line) is appended to the rendered text.

        Returns the full accumulated text.
        """
        if self._streamer is None:
            return ""
        full_text = self._streamer.full_text
        self._truncate_to(self._stream_baseline)
        start = self._stream_baseline or 0
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

    # ------------------------------------------------------------------ fold

    def toggle_fold(self) -> None:
        """Cycle fold/expand through folders (Ctrl+E).

        * No active folder: activate the last folder, expand it.
        * Active folder expanded: fold it, move cursor to the previous
          folder (cyclic), expand that one.  If the cursor lands on the
          same folder (only one folder), leave it folded.
        * Active folder folded: expand it.
        """
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

    # ------------------------------------------------------------------ internal

    def _re_render_folder(self, idx: int, expand: bool) -> None:
        """Re-render a single folder in-place using precise line boundaries.

        Truncates only the folder's own lines (``baseline[i]`` through
        ``baseline[i] + line_count[i]``), writes the new content, then
        restores any non-folder lines that came after.  This preserves
        blank lines, user messages, and other content that lives
        between folders.
        """
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
        self._line_cache.clear()
        self.virtual_size = self.virtual_size.__class__(
            self._widest_line_width, len(self.lines)
        )
        self.refresh()

    def _truncate_to(self, baseline: int | None) -> None:
        """Truncate ``self.lines`` back to ``baseline``."""
        if baseline is None:
            return
        if len(self.lines) > baseline:
            self.lines = self.lines[:baseline]
            self._line_cache.clear()
            self.virtual_size = (
                self.virtual_size.__class__(
                    self._widest_line_width, len(self.lines)
                )
            )
            self.refresh()

    # ------------------------------------------------------------------ messages

    def on_write_transcript(self, message: WriteTranscript) -> None:
        """Textual message handler: dispatch ``WriteTranscript`` to ``write``."""
        self.write(message.content)
