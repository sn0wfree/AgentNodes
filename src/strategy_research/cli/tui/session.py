"""ChatSession — orchestrates the per-turn input → dispatch loop.

The session is a thin layer over :func:`cli.interactive.main.process_turn`
that already implements:

* halt/resume intercept
* mandate pick intercept
* slash command dispatch (via :data:`_DISPATCH`)
* plain-text user turn append

The session wraps each of those effects with a thin Textual-aware
shell:

* user input is read from ``ctx.pending_input`` (set by :class:`ResumeOrNewModal`
  on accept) or arrived via :class:`SynthesizeInput` from
  :class:`ChatInput` / :class:`CommandSidebar`.
* the session calls ``process_turn(text, ctx)`` and inspects the rc.
* on ``rc == 2`` (the ``/quit`` sentinel from ``cmd_quit``), the
  session asks the app to exit.
* on the session observing ``ctx.pending_prompt`` (used by
  ``/journal`` / ``/shadow`` queue-a-prompt), it re-enters
  immediately with that prompt so the user does not have to
  round-trip through stdin.

Handler output capture (Commit 3)
* Handlers still write to the singleton via ``console.print(...)``. In
  TUI mode we wrap each turn in :func:`cli.theme.captured_console` so
  :func:`cli.theme.get_console` returns a recording console for the
  duration. Anything the handler prints accumulates inside that
  console and is then forwarded to the app's TranscriptView.

LLM streaming (Commit 5)
* Plain-text turns (``rc == 0`` and ``process_turn`` has appended the
  user turn to ``ctx.history``) are routed through
  :mod:`cli.llm_streaming.stream_chat_to_tui` which posts token deltas
  to the TranscriptView and appends the final assistant message back to
  ``ctx.history``. No-op when an LLM client has not been installed
  (e.g. in tests); the session falls back to plain chat history.
"""
from __future__ import annotations

from typing import Any, Optional

from strategy_research.cli.halt import clear_halt as _clear_halt
from strategy_research.cli.halt import is_halted as _is_halted
from strategy_research.cli.halt import trip_halt as _trip_halt
from strategy_research.cli.interactive.main import process_turn
from strategy_research.cli.theme import captured_console
from strategy_research.cli.tui.messages import SynthesizeInput, WriteTranscript

# Standard ``cmd_quit`` sentinel. See ``cli/commands/slash_chat.py``.
QUIT_RC: int = 2





# ── 阶段 3a：CLI 侧 Gateway（与 API/scheduler 共享唯一实例） ──


def _get_cli_gateway():
    """进程内唯一 Gateway（共享 mailbox，保证 session 串行域）。"""
    from strategy_research.kernel.gateway import get_gateway

    return get_gateway()


class ChatSession:
    """Single-turn dispatcher that wraps ``process_turn``.

    Holds a reference to the running :class:`ResearchApp` so it can
    request ``app.exit()`` on ``/quit`` and post messages back into
    the widget tree. Optionally holds an :class:`OpenAICompatClient` so
    plain-text turns can stream from the configured LLM into the
    TranscriptView.
    """

    def __init__(
        self,
        ctx: Any,
        *,
        app: Any = None,
        llm_client: Any = None,
        session_logger: Any = None,
        transcript_width: int = 120,
    ) -> None:
        self.ctx = ctx
        self.app = app
        self.transcript_width = transcript_width
        # Optional OpenAICompatClient. When set, plain-text turns are
        # streamed via ``stream_chat_to_tui``. When None, plain-text
        # turns append to history only.
        self.llm_client = llm_client
        # ``session_logger`` is an optional callable taking
        # ``(session_id, role, content)``. Reserved for Commit 5 (TTY
        # guard integration); unused here.
        self.session_logger = session_logger
        # Pending raw input buffer used by :meth:`ResumeOrNewModal`.
        self._pending_input: Optional[str] = None
        # Goal continuation control: when True, _check_goal_continuation
        # is suppressed so the agent stops after each LLM response.
        self._goal_continuation_paused: bool = False

    # ------------------------------------------------------------------ API

    def enqueue(self, text: str) -> None:
        """Queue text for the next :meth:`dispatch` cycle."""
        self._pending_input = text

    async def dispatch(self, text: str) -> int:
        """Run a single turn: ``process_turn(text, ctx)`` → return rc.

        On ``rc == QUIT_RC`` (=2 from ``/quit``), the session asks
        the app to exit (if bound). On the session observing
        ``ctx.pending_prompt`` after a slash turn (``/journal``,
        ``/shadow``), the queued prompt is dispatched recursively.
        On a successful plain-text turn (``rc == 0`` with ``llm_client``
        bound), the session routes the messages payload through the
        streaming bridge so the LLM reply reaches the TranscriptView.
        """
        rc, captured_text = self._dispatch_with_capture(text)

        # Forward captured handler output to the TUI transcript.
        stripped = captured_text.strip("\n").strip()
        if stripped:
            self._write_captured(stripped)

        # Update header stats after each turn
        self._update_header_stats()

        # Sync interactive mode (chat/goal) based on active GoalStore state.
        # Must run AFTER process_turn (which may create/complete a goal)
        # and BEFORE the gateway dispatch (which reads the mode).
        self._sync_interactive_mode()

        # Refresh the GoalPanel widget with current goal data.
        if self.app is not None:
            try:
                self.app.update_goal_panel()
            except Exception:
                pass

        # If plain text and an LLM client is bound, route the user
        # turn through AgentLoop (full event-driven flow).
        if (
            rc == 0
            and self.llm_client is not None
            and self.app is not None
            and text.strip()
            and not text.strip().startswith("/")
            and not text.strip().startswith("停")
        ):
            from rich.text import Text

            self._write_transcript("")
            self._write_transcript(
                Text(f"\u276f {text.strip()}", style="bold cyan")
            )
            try:
                # P5-1c ④：唯一入口 = Gateway（WorkRequest → mailbox →
                # 准入 → 引擎 → 事件流）。旧的直连 AgentLoop 分支已删除。
                await self._run_via_gateway(text.strip())
            except Exception as exc:  # noqa: BLE001
                self._write_transcript(f"[red]Agent error:[/red] {exc}")
            # Refresh header stats now that the assistant message has
            # been appended to ctx.history (Stage D fix: previously the
            # header only reflected pre-arun state, leaving "0 msg").
            self._update_header_stats()

        # Drain ``ctx.pending_prompt`` queued by slash handlers.
        queued = getattr(self.ctx, "pending_prompt", None)
        if queued:
            self.ctx.pending_prompt = ""
            return await self._drain(queued, depth=0, accumulator=rc)

        if rc == QUIT_RC and self.app is not None:
            self.app.exit()
        return rc

    async def on_synthesize_input(self, message: SynthesizeInput) -> None:
        """Textual message handler: forward widget submissions here."""
        await self.dispatch(message.text)

    # ------------------------------------------------------------------ helpers

    def _dispatch_with_capture(self, text: str) -> tuple[int, str]:
        """Run ``process_turn`` inside a captured console.

        Returns ``(rc, captured_text)``. ``captured_text`` is the raw
        text the handler emitted through ``get_console()`` (empty for
        plain text turns that only append to history).
        """
        text_str = (text or "").strip()
        with captured_console(width=self.transcript_width) as rec:
            rc = process_turn(text_str, self.ctx)
            captured = rec.export_text(clear=False, styles=False)
        return rc, captured

    async def _drain(self, prompt: str, *, depth: int, accumulator: int) -> int:
        """Run a queued prompt, allowing up to 8 levels of re-queueing."""
        if depth >= 8:
            self._write_transcript(
                "[warning]Prompt queue depth exceeded — discarding remaining.[/]"
            )
            return accumulator
        rc, captured = self._dispatch_with_capture(prompt)
        stripped = captured.strip("\n").strip()
        if stripped:
            self._write_captured(stripped)
        next_queued = getattr(self.ctx, "pending_prompt", None)
        if next_queued:
            self.ctx.pending_prompt = ""
            return await self._drain(next_queued, depth=depth + 1, accumulator=rc)
        if rc == QUIT_RC and self.app is not None:
            self.app.exit()
        return rc

    def _write_transcript(self, content: Any) -> None:
        """Post a transcript line via the app's widget, if available."""
        if self.app is not None:
            self.app.write_transcript(content)

    def _write_captured(self, captured_text: str) -> None:
        """Render the captured ANSI/marked-up text into the transcript."""
        self._write_transcript(captured_text)

    async def _run_via_gateway(self, task: str) -> None:
        """阶段 3a：CLI 走新架构（Gateway 引擎 + MemoryManager 历史）。"""
        import asyncio

        session_id = getattr(self.ctx, "session_id", "cli")
        mm = None
        history: list[dict] | None = None
        try:
            from strategy_research.core.agent.memory_manager import (
                get_default_memory_manager,
            )

            mm = get_default_memory_manager()
            full = await mm.get(session_id)
            history = full[:-1] if len(full) > 1 else None
        except Exception:  # noqa: BLE001 —— 无 MemoryManager 时退化为内存历史
            history = list(self.ctx.history[:-1]) if len(self.ctx.history) > 1 else None

        gw = _get_cli_gateway()
        messages = list(history or []) + [{"role": "user", "content": task}]

        receipt = gw.entry.submit(
            __import__(
                "strategy_research.kernel.gateway", fromlist=["cli_request"]
            ).cli_request(session_id, task)
        )
        if not receipt.accepted:
            self.app.write_transcript(f"[入口拒绝: {receipt.reason}]")
            return

        # 同步驱动（A2 的后台消费者会替换）；用 executor 避免阻塞 TUI loop。
        # kind 过滤：CLI 只领 CHAT 工单（AUTOMATION 归 scheduler 的消费者）。
        from strategy_research.kernel.orchestration import WorkKind

        runs = await asyncio.get_running_loop().run_in_executor(
            None, lambda: gw.run_once({WorkKind.CHAT}),
        )
        run = runs[-1] if runs else None
        if run is None or not run.ok:
            self.app.write_transcript(
                f"[运行失败: {run.error if run else 'no run'}]"
            )
            return

        self.app.write_transcript(run.answer)
        # 持久化历史（与旧路径同一 MemoryManager）
        if mm is not None and run.answer:
            try:
                await mm.append(session_id, "user", task)
                await mm.append(session_id, "assistant", run.answer)
            except Exception:  # noqa: BLE001
                self.ctx.history.append({"role": "assistant", "content": run.answer})
        else:
            self.ctx.history.append({"role": "assistant", "content": run.answer})

    def _sync_interactive_mode(self) -> None:
        """Sync ``ctx.interactive_mode`` with GoalStore.

        Called after each turn (post-``process_turn``, pre-gateway-dispatch)
        so the mode reflects the current goal state.

        - No active goal → ``"chat"`` (conversational prompt)
        - Active goal   → ``"goal"``   (researcher prompt, JSON output)

        Also updates the ModeBar widget so the user always sees the
        current mode.

        Failures default to ``"chat"`` (safe fallback — the user always
        gets *some* response, even if the goal DB is temporarily
        unreachable).
        """
        try:
            from strategy_research.apps.goal import GoalStore
            store = GoalStore()
            goal = store.get_current_goal(self.ctx.session_id)
            self.ctx.interactive_mode = "goal" if goal is not None else "chat"
        except Exception:
            self.ctx.interactive_mode = "chat"
        # Update the mode bar widget if available
        if self.app is not None:
            try:
                from strategy_research.cli.tui.widgets.mode_bar import ModeBar
                bar = self.app.query_one("#mode-bar", ModeBar)
                bar.update_mode(self.ctx.interactive_mode)
            except Exception:
                pass

    def _update_header_stats(self) -> None:
        """Update the StatusHeader with current session stats.

        Stage C: tool totals come from ``app._tool_total`` / ``_tool_ok``
        (incremented in ``ResearchApp._route_tool_event``) rather than
        from the ToolsRail timeline — tools are now rendered inline in
        TranscriptView and the rail no longer tracks them.

        Failures are logged at WARNING (not silently swallowed as
        before) so we don't ship another "header stuck on unknown" bug
        without a trace.
        """
        if self.app is None:
            return
        import logging
        _log = logging.getLogger(__name__)
        try:
            # Count messages in history
            msg_count = len(self.ctx.history)
            # Tool totals now live on App (Stage C)
            tool_count = getattr(self.app, "_tool_total", 0)
            tool_ok = getattr(self.app, "_tool_ok", 0)
            # Estimate tokens (rough: 1 token per 4 chars)
            token_used = sum(
                len(str(turn.get("content", ""))) // 4
                for turn in self.ctx.history
            )
            # Get model from LLM config
            model = "unknown"
            try:
                from strategy_research.foundation.llm.config import LLMConfig
                cfg = LLMConfig.load()
                model = cfg.model
            except Exception as exc:  # noqa: BLE001
                _log.debug("LLMConfig.load failed: %s", exc)
            self.app.update_header(
                connection_status="live",
                model=model,
                message_count=msg_count,
                tool_count=tool_count,
                tool_ok=tool_ok,
                token_used=token_used,
                session_id=getattr(self.ctx, "session_id", "cli"),
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("_update_header_stats failed: %s", exc, exc_info=True)

    # ------------------------------------------------------------------ halt API

    def trip_halt(self, *, reason: str = "user explicit halt") -> None:
        """Trip the kill switch (Ctrl+C or Ctrl+C typed into chat)."""
        _trip_halt(reason=reason)
        self._write_transcript(
            f"[warning]halt tripped ({reason}) — long-running loops will exit on next checkpoint.[/]"
        )

    def clear_halt(self) -> None:
        """Clear the kill switch (via /resume keyword)."""
        was = _is_halted()
        _clear_halt()
        if was:
            self._write_transcript(
                "[success]halt cleared — long-running loops may now proceed.[/]"
            )

    def toggle_goal_continuation(self) -> None:
        """Ctrl+G — pause/resume goal auto-continuation.

        When paused, the agent stops after each LLM response without
        automatically injecting a <goal-continuation> prompt.  The user
        can still chat freely; resuming re-enables the auto-continue
        behaviour on the next turn.
        """
        self._goal_continuation_paused = not self._goal_continuation_paused
        if self._goal_continuation_paused:
            self._write_transcript(
                "[dim]Goal 自动续跑已暂停 — 按 Ctrl+G 恢复[/dim]"
            )
        else:
            self._write_transcript(
                "[dim]Goal 自动续跑已恢复[/dim]"
            )
        # Update GoalPanel hint
        if self.app is not None:
            try:
                self.app.update_goal_panel()
            except Exception:
                pass

    # ------------------------------------------------------------------ evidence detection

    @staticmethod
    def detect_evidence_in_response(content: str) -> dict | None:
        """Heuristic: detect research evidence in agent response text.

        Returns a dict with ``text``, ``confidence`` ("high"/"medium"/"low"),
        and ``matched_pattern`` if evidence is detected, or None.

        This is a passive detection — the caller decides whether to prompt
        the user for confirmation.
        """
        import re

        if not content or len(content.strip()) < 20:
            return None

        # Patterns indicating quantitative research evidence
        patterns = [
            (r"(年化收益[率]?|annualized\s+return)", "high"),
            (r"(夏普比率?|sharpe\s+ratio)", "high"),
            (r"(最大回撤|max(?:imum)?\s+drawdown)", "high"),
            (r"(收益率|return\s+rate)", "medium"),
            (r"(波动率|volatility)", "medium"),
            (r"(回测|backtest|back-test)", "medium"),
            (r"(数据来源|data\s+source|来源[：:])", "medium"),
            (r"(统计[显著性]|statistical\s+significance|p[\s-]*value)", "high"),
            (r"(置信区间|confidence\s+interval)", "high"),
            (r"(因子[收益表现]|factor\s+(?:return|performance))", "medium"),
        ]

        for pattern, confidence in patterns:
            if re.search(pattern, content, re.IGNORECASE):
                return {
                    "text": content[:500],
                    "confidence": confidence,
                    "matched_pattern": pattern,
                }

        return None


__all__ = [
    "ChatSession",
    "QUIT_RC",
    "SynthesizeInput",
    "WriteTranscript",
]
