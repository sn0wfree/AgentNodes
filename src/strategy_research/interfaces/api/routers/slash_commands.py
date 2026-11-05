"""Slash command handlers for the chat API (/study, /compact, /clear, /help).

Extracted from ``chat.py`` (P3) — the business logic of the slash
commands lives here; ``chat.py`` routes into these handlers.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

from ._task_utils import log_task_exception


def _build_llm_config():
    """LLMConfig from env (shared helper, see chat.py)."""
    from .chat import _build_llm_config as _cfg

    return _cfg()


def _get_session_service():
    """Container-backed SessionService (imported lazily to avoid a cycle)."""
    from .chat import _get_session_service as _svc

    return _svc()


from ..schemas.chat import ChatMessageRequest as ChatMessage
from ..schemas.chat import SendMessageResponse


# ── /goal 命令已随 apps/goal 移除（2026-11-04 做减法批次 2）──────


def _emit_response(
    event_bus: Any, session_id: str, assistant_msg_id: str, response_text: str,
) -> None:
    """Emit a slash-command response as the 3-step text protocol.

    原名 ``_emit_goal_response``；goal 命令移除后由 /study 段继续使用，
    改成中性的名字——它发射的是通用 SSE 文本协议，与 goal 无关。
    """
    text_id = str(uuid.uuid4())
    event_bus.emit(session_id, "text.started", {
        "message_id": assistant_msg_id, "text_id": text_id,
    })
    event_bus.emit(session_id, "text_delta", {
        "message_id": assistant_msg_id, "text_id": text_id, "text": response_text,
    })
    event_bus.emit(session_id, "text.ended", {
        "message_id": assistant_msg_id, "text_id": text_id, "text": response_text,
    })
    event_bus.emit(session_id, "assistant_message", {
        "message_id": assistant_msg_id, "content": response_text,
        "message_type": "assistant", "metadata": {"model": "slash-handler"},
    })
    event_bus.emit(session_id, "agent_done", {
        "message_id": assistant_msg_id, "status": "success",
    })



async def _handle_study_command(body: ChatMessage) -> SendMessageResponse:
    """Handle ``/study`` slash commands (light wrapper around the API).

    Supported:
        ``/study start "objective" [--workspace W] [--strategy S]
                       [--metric calmar>=0.5,sharpe>=0.3]
                       [--budget-turn N] [--budget-time S]
                       [--max-rounds N] [--behavior static|varying|improving]``
        ``/study status``
        ``/study list``
        ``/study pause <study_id>``
        ``/study resume <study_id>``
        ``/study cancel <study_id>``
        ``/study help``

    Uses the same TXT-style response protocol as /goal handlers (text
    started / delta / ended). State changes happen via the study router
    helpers so the scheduler emits study_* events upstream too.
    """
    import uuid

    session_id = body.session_id
    content = body.content.strip()

    user_msg_id = str(uuid.uuid4())
    assistant_msg_id = str(uuid.uuid4())
    service = _get_session_service()
    event_bus = service.event_bus

    # Persist the user message before running the command (same triple as
    # chat: EventStore → projector.flush → messages table).
    event_bus.emit(session_id, "message_received", {
        "message_id": user_msg_id,
        "user_message_id": user_msg_id,
        "assistant_message_id": assistant_msg_id,
        "content": content,
        "role": "user",
    })

    try:
        response_text = _dispatch_study_command(content, session_id)
    except Exception as exc:
        logger.exception("study command failed")
        response_text = f"Study command failed: {exc}"
    logger.info("study chat command response: %s", response_text[:100])

    # Flush any pending workflow submits (created by ``/study start``)
    # on this loop before the response round-trips to the user.
    if _study_pending_submits:
        for study, config, goal_id, objective, ws in _study_pending_submits:
            # AEGIS: autoresearch → scheduler（workflow 执行器已移除）
            from .study import _get_study_scheduler

            sched = _get_study_scheduler()
            import asyncio as _asyncio
            task = _asyncio.create_task(sched.submit(study))
            task.add_done_callback(log_task_exception)
        _study_pending_submits.clear()

    _emit_response(event_bus, session_id, assistant_msg_id, response_text)

    return SendMessageResponse(
        message_id=user_msg_id, user_message_id=user_msg_id,
        assistant_message_id=assistant_msg_id,
        event_id="", status="done",
    )


def _dispatch_study_command(content: str, session_id: str) -> str:
    """Parse and run a /study subcommand. ``content`` is the raw user text."""

    import shlex

    # Strip leading "/study"
    body = content[len("/study"):].strip()
    if not body:
        return _study_help()

    # Split into subcommand + rest (shlex to keep quoted objective intact).
    try:
        tokens = shlex.split(body)
    except ValueError as exc:
        return f"Parse error: {exc}"
    subcmd = tokens[0].lower()
    rest = tokens[1:]

    if subcmd in ("help", "?"):
        return _study_help()
    if subcmd == "start":
        return _study_start_cmd(rest, session_id)
    if subcmd == "status":
        return _study_status_cmd(session_id)
    if subcmd == "list":
        return _study_list_cmd(rest)
    if subcmd in ("pause", "resume", "cancel"):
        if not rest:
            return f"/study {subcmd} requires a study_id"
        return _study_control_cmd(subcmd, rest[0])

    if subcmd in ("redirect", "directive"):
        # /study redirect <study_id> "<directive content>"
        if not rest:
            return "/study redirect requires a study_id and quoted content"
        target_study = rest[0]
        # Re-join remaining tokens so multi-word directives work without
        # having to escape every space.
        directive_text = " ".join(rest[1:]).strip().strip('"\'')
        if not directive_text:
            return "/study redirect requires quoted content after study_id"
        return _study_redirect_cmd(target_study, directive_text, session_id)

    # Else: unknown — show help. (Allows the user to say "/study foo bar".)
    return f"Unknown subcommand: {subcmd}\n" + _study_help()


def _study_help() -> str:
    return (
        "/study start \"<objective>\" [--workspace W] [--strategy S]\n"
        "            [--metric calmar>=0.5,sharpe>=0.3]\n"
        "            [--budget-turn N] [--budget-time S] [--max-rounds N]\n"
        "            [--monitor-interval S]   (Phase 3: post-completion drift check)\n"
        "            [--behavior static|varying|improving]\n"
        "            [--guidance-file REL]   workspace-relative markdown body\n"
        "            [--gates-file REL]      workspace-relative YAML frontmatter\n"
        "  Create a study. The active session's goal ledger is created.\n"
        "/study status   — current study for this session\n"
        "/study list [status=queued|running|monitoring|complete|cancelled]\n"
        "/study pause <study_id>\n"
        "/study resume <study_id>\n"
        "/study cancel <study_id>\n"
        "/study redirect <study_id> \"<directive>\" — mid-exec redirect\n"
        "/study help     — this message"
    )


def _parse_study_flags(rest: list[str]) -> dict:
    """Tokenize free-form ``--flag value`` style flags."""
    flags = {
        "workspace_path": None, "strategy_name": None,
        "metric_targets": None, "executor_type": "autoresearch",
        "budget_turn": None, "budget_time_seconds": None, "max_rounds": None,
        "behavior": None, "monitor_interval_seconds": None,
        "guidance_file": None, "gates_file": None,
    }
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok.startswith("--") and i + 1 < len(rest):
            _apply_study_flag(flags, tok[2:], rest[i + 1])
            i += 2
        else:
            i += 1
    return flags


def _apply_study_flag(flags: dict, key: str, val: str) -> None:
    """Apply a single ``--key value`` pair to the flags dict."""
    if key in ("workspace", "workspace_path"):
        flags["workspace_path"] = val
    elif key in ("strategy", "strategy_name"):
        flags["strategy_name"] = val
    elif key == "metric":
        flags["metric_targets"] = _parse_metric_targets(val)
    elif key in ("budget-turn", "budget-time", "max-rounds", "monitor-interval"):
        _set_int_flag(flags, {
            "budget-turn": "budget_turn",
            "budget-time": "budget_time_seconds",
            "max-rounds": "max_rounds",
            "monitor-interval": "monitor_interval_seconds",
        }[key], val)
    elif key == "behavior":
        flags["behavior"] = val
    elif key in ("executor", "executor_type"):
        if val in ("autoresearch", "workflow"):
            flags["executor_type"] = val
    elif key in ("guidance-file",):
        flags["guidance_file"] = val
    elif key in ("gates-file",):
        flags["gates_file"] = val


def _set_int_flag(flags: dict, dst: str, val: str) -> None:
    """Set an int flag; ignore unparseable values."""
    try:
        flags[dst] = int(val)
    except ValueError:
        pass


def _parse_metric_targets(spec: str) -> list[dict] | None:
    """Parse a comma-separated ``calmar>=0.5`` spec → list of target dicts."""
    import re
    targets: list[dict] = []
    for chunk in spec.split(","):
        m = re.match(r"\s*([A-Za-z_]+)\s*(>=|<=|>|<|==)\s*(-?\d+(\.\d+)?)\s*$", chunk)
        if not m:
            continue
        targets.append({
            "name": m.group(1), "op": m.group(2), "value": float(m.group(3)),
        })
    return targets or None


def _default_workspace() -> str:
    """Return the process-default workspace path for /study defaults."""
    import os
    from pathlib import Path

    return os.environ.get("SR_WORKSPACE_PATH") or str(
        Path.home() / ".quantnodes-research"
    )


def _study_start_cmd(rest: list[str], session_id: str) -> str:
    from strategy_research.apps.study import StudyStatus, StudyStore, default_metric_targets

    # Check for active study first (one task per session)
    with StudyStore() as _chk:
        active = _chk.get_active_study(session_id)
        if active is not None:
            return (
                f"Session already has an active study: {active.study_id[:12]}...\n"
                f"Status: {active.execution_status.value}\n"
                f"Cancel it first with /study cancel or wait for it to complete."
            )

    flags = _parse_study_flags(rest)
    # Objective = remaining positional tail (everything not consumed by flags)
    positional = [t for t in rest
                  if not (t.startswith("--") or _is_flag_value(rest, t))]
    objective = " ".join(positional).strip(' "\'') or "Research goal"
    ws = flags["workspace_path"] or _default_workspace()
    strategy = flags["strategy_name"]
    if not strategy:
        return (
            "/study start requires --strategy <name>. "
            "Use ``/study list strategies`` once we expose preset discovery."
        )
    targets = flags["metric_targets"] or default_metric_targets()

    try:
        # goal 台账已随 apps/goal 移除（2026-11-04 做减法批次 2）：
        # study 不再创建/关联 goal，goal_id 置空。
        with StudyStore() as store:
            study = store.create_study(
                owner_session_id=session_id, goal_id="",
                objective=objective, workspace_path=ws, strategy_name=strategy,
                metric_targets=targets,
                budget_token=None, budget_turn=flags["budget_turn"],
                budget_time_seconds=flags["budget_time_seconds"],
                cooldown_base=30.0, cooldown_jitter=10.0, min_cooldown=1.0,
                max_rounds=flags["max_rounds"], behavior=flags["behavior"],
                monitor_interval_seconds=flags["monitor_interval_seconds"],
            )

        # v2 §17.1: --guidance-file / --gates-file → single guidance.md
        from pathlib import Path

        from strategy_research.apps.study.method import guidance as gd
        guidance_text = gd.compose_guidance_text(
            Path(ws),
            guidance_file=flags["guidance_file"],
            gates_file=flags["gates_file"],
        )
        if guidance_text:
            gdir = Path(ws) / "study" / study.study_id
            gdir.mkdir(parents=True, exist_ok=True)
            (gdir / "guidance.md").write_text(guidance_text, encoding="utf-8")

        # Queue for async submission by _handle_study_command
        # （workflow 执行器已随 apps/goal.workflow 移除；只剩 autoresearch
        #   → scheduler → AutoresearchRunner 一条路。）
        if flags["executor_type"] != "autoresearch":
            return (
                "Cannot create study: workflow executor 已移除"
                "（apps/goal.workflow，2026-11-04）。请使用 "
                "--executor autoresearch。"
            )
        _study_pending_submits.append((study, None, "", objective, ws))

    except (ValueError, FileNotFoundError) as e:
        return f"Cannot create study: {e}"
    return (
        f"Study created: {study.study_id[:12]}...\n"
        f"Objective: {study.objective}\n"
        f"Strategy: {study.strategy_name} @ {study.workspace_path}\n"
        f"Targets: {targets}\n"
        f"Status: {StudyStatus.QUEUED.value}"
    )


# Studies created by /study start need to be submitted to the scheduler on
# the FastAPI event loop. _handle_study_command awaits these after the
# dispatcher returns so the response text + the queued task both happen.
_study_pending_submits: list = []


def _is_flag_value(tokens, t) -> bool:
    """Return True if ``t`` follows a ``--flag`` token in ``tokens``."""
    i = tokens.index(t)
    return i > 0 and tokens[i - 1].startswith("--")


def _study_status_cmd(session_id: str) -> str:
    from strategy_research.apps.study import StudyStore
    with StudyStore() as store:
        study = store.get_active_study(session_id)
    if study is None:
        return "No active study for this session. Use /study start ..."
    mon = (
        f"Monitor interval: {study.monitor_interval_seconds}s "
        f"last_check={study.last_monitor_check_at} "
        f"drift_count={study.monitor_drift_count}\n"
        if study.monitor_interval_seconds else ""
    )
    return (
        f"Study: {study.study_id[:12]}...\n"
        f"Objective: {study.objective}\n"
        f"Executor: {study.executor_type}\n"
        f"Status: {study.execution_status.value}\n"
        f"Round: {study.current_round}\n"
        f"Last metrics: {study.last_metrics}\n"
        f"Last verdict: {study.last_verdict}\n"
        f"Last error: {study.last_error}\n"
        f"{mon}"
    )


def _study_list_cmd(rest: list[str]) -> str:
    from strategy_research.apps.study import StudyStatus, StudyStore
    status = None
    for tok in rest:
        if tok.startswith("status=") or tok.startswith("s="):
            val = tok.split("=", 1)[1]
            try:
                status = StudyStatus(val)
            except ValueError:
                return f"Invalid status: {val}"
    with StudyStore() as store:
        rows = store.list_studies(status=status, limit=20)
    if not rows:
        return "No studies found."
    out = [f"Found {len(rows)} study/studies (newest first):"]
    for r in rows:
        out.append(
            f"- {r.study_id[:12]}... [{r.execution_status.value}] "
            f"obj={r.objective[:40]} round={r.current_round}"
        )
    return "\n".join(out)


def _study_control_cmd(action: str, study_id: str) -> str:
    from .study import _get_study_scheduler
    sched = _get_study_scheduler()
    fn = {"pause": sched.pause, "resume": sched.resume,
          "cancel": sched.cancel}[action]
    if not fn(study_id):
        return f"Study {study_id} not found or not active — cannot {action}."
    return f"Study {study_id}: {action} requested."


def _study_redirect_cmd(study_id: str, directive: str, session_id: str) -> str:
    """Append a mid-execution directive to a study."""
    from strategy_research.apps.study import StudyStore
    issued_by = f"chat:{session_id}"
    try:
        with StudyStore() as store:
            d = store.add_directive(
                study_id=study_id, content=directive,
                issued_by=issued_by,
            )
    except ValueError as e:
        return f"Cannot redirect: {e}"
    return (
        f"Directive recorded: {d.directive_id[:12]}...\n"
        f"Will apply to the next research round.\n"
        f"Content: {directive}"
    )


# ── /compact command handler ──────────────────────────────────────


async def _handle_compact_command(body: ChatMessage) -> SendMessageResponse:
    """Handle /compact command — compress session history in-place."""
    import uuid

    service = _get_session_service()
    cfg = _build_llm_config()

    # B5: user message persisted via EventStore → projector.flush()
    user_msg_id = str(uuid.uuid4())

    # Execute compaction
    try:
        result = await service.compact_history(
            session_id=body.session_id,
            config=cfg.compact_config if cfg else None,
        )
        layers = result.get("layers", [])
        before = result.get("before_tokens", 0)
        after = result.get("after_tokens", 0)
        summary = result.get("summary", "")

        if layers:
            response_text = f"✅ 上下文已压缩: {', '.join(layers)}（{before} → {after} tokens）"
            if summary:
                response_text += f"\n\n{summary}"
        else:
            response_text = "ℹ️ 上下文无需压缩，当前 token 使用量在阈值以下。"
    except Exception as exc:
        logger.exception("compact_history failed")
        response_text = f"❌ 压缩失败: {exc}"

    # B5: assistant message persisted via EventStore → projector.flush()
    assistant_msg_id = str(uuid.uuid4())

    # Emit SSE events (3-step text protocol) — also flushes to messages table
    event_bus = service.event_bus
    compact_text_id = str(uuid.uuid4())
    event_bus.emit(body.session_id, "message_received", {
        "message_id": user_msg_id,
        "user_message_id": user_msg_id,
        "assistant_message_id": assistant_msg_id,
        "content": body.content,
        "role": "user",
        "status": "done",
    })
    event_bus.emit(body.session_id, "text.started", {
        "text_id": compact_text_id,
        "message_id": assistant_msg_id,
    })
    event_bus.emit(body.session_id, "text_delta", {
        "text": response_text,
        "text_id": compact_text_id,
        "message_id": assistant_msg_id,
    })
    event_bus.emit(body.session_id, "text.ended", {
        "text_id": compact_text_id,
        "text": response_text,
        "message_id": assistant_msg_id,
    })
    event_bus.emit(body.session_id, "assistant_message", {
        "message_id": assistant_msg_id,
        "content": response_text,
        "message_type": "assistant",
    })
    event_bus.emit(body.session_id, "agent_done", {
        "message_id": assistant_msg_id,
        "status": "completed",
    })

    return SendMessageResponse(
        message_id=user_msg_id,
        user_message_id=user_msg_id,
        assistant_message_id=assistant_msg_id,
        event_id="",
        status="done",
    )


# ── /clear command handler (webui only) ─────────────────────────────


_HELP_TEXT = (
    "## 可用命令\n"
    "\n"
    "- `/study <目标描述>` — 启动一个研究任务（多轮迭代）\n"
    "- `/compact` — 压缩当前会话的上下文\n"
    "- `/clear` — 清空当前会话的 LLM 上下文（保留历史消息）\n"
    "- `/help` — 显示本帮助\n"
    "\n"
    "## 快捷键\n"
    "\n"
    "- ⌘K — 搜索会话\n"
    "- ⌘P — 打开命令面板\n"
    "- ⌘T — 新建会话\n"
    "- ⌘B — 切换右栏\n"
    "- ⌘1–9 — 切换会话 tab\n"
    "- Enter — 发送 · Shift+Enter — 换行\n"
)


async def _handle_clear_command(body: ChatMessage) -> SendMessageResponse:
    """Handle ``/clear`` — drop the LLM-visible history for this session.

    Implementation: calls ``MemoryManager.clear`` which truncates the
    session's memory backend rows (the buffer the AgentLoop reads at
    attempt start). The persisted message log (the ``messages`` table
    populated by the projector) is intentionally NOT touched so the
    user can still scroll their conversation. The UI sees a synthetic
    assistant acknowledgement via the same text-event flow as
    ``/compact``.
    """
    import uuid

    try:
        from strategy_research.kernel.agent.memory_manager import (
            get_default_memory_manager,
        )
        mm = get_default_memory_manager()
        await mm.clear(body.session_id)
        response_text = "✅ 已清空当前会话的上下文。历史消息保留可见。"
    except Exception as exc:
        logger.exception("clear failed")
        response_text = f"❌ 清空失败: {exc}"

    user_msg_id = str(uuid.uuid4())
    assistant_msg_id = str(uuid.uuid4())
    service = _get_session_service()
    event_bus = service.event_bus
    text_id = str(uuid.uuid4())

    event_bus.emit(body.session_id, "message_received", {
        "message_id": user_msg_id,
        "user_message_id": user_msg_id,
        "assistant_message_id": assistant_msg_id,
        "content": body.content,
        "role": "user",
        "status": "done",
    })
    event_bus.emit(body.session_id, "text.started", {
        "text_id": text_id,
        "message_id": assistant_msg_id,
    })
    event_bus.emit(body.session_id, "text_delta", {
        "text": response_text,
        "text_id": text_id,
        "message_id": assistant_msg_id,
    })
    event_bus.emit(body.session_id, "text.ended", {
        "text_id": text_id,
        "text": response_text,
        "message_id": assistant_msg_id,
    })
    event_bus.emit(body.session_id, "assistant_message", {
        "message_id": assistant_msg_id,
        "content": response_text,
        "message_type": "assistant",
    })
    event_bus.emit(body.session_id, "agent_done", {
        "message_id": assistant_msg_id,
        "status": "completed",
    })
    return SendMessageResponse(
        message_id=user_msg_id,
        user_message_id=user_msg_id,
        assistant_message_id=assistant_msg_id,
        event_id="",
        status="done",
    )


async def _handle_help_command(body: ChatMessage) -> SendMessageResponse:
    """Handle ``/help`` — return the static cheat-sheet as an assistant message."""
    import uuid

    user_msg_id = str(uuid.uuid4())
    assistant_msg_id = str(uuid.uuid4())
    service = _get_session_service()
    event_bus = service.event_bus
    text_id = str(uuid.uuid4())

    event_bus.emit(body.session_id, "message_received", {
        "message_id": user_msg_id,
        "user_message_id": user_msg_id,
        "assistant_message_id": assistant_msg_id,
        "content": body.content,
        "role": "user",
        "status": "done",
    })
    event_bus.emit(body.session_id, "text.started", {
        "text_id": text_id,
        "message_id": assistant_msg_id,
    })
    event_bus.emit(body.session_id, "text_delta", {
        "text": _HELP_TEXT,
        "text_id": text_id,
        "message_id": assistant_msg_id,
    })
    event_bus.emit(body.session_id, "text.ended", {
        "text_id": text_id,
        "text": _HELP_TEXT,
        "message_id": assistant_msg_id,
    })
    event_bus.emit(body.session_id, "assistant_message", {
        "message_id": assistant_msg_id,
        "content": _HELP_TEXT,
        "message_type": "assistant",
    })
    event_bus.emit(body.session_id, "agent_done", {
        "message_id": assistant_msg_id,
        "status": "completed",
    })
    return SendMessageResponse(
        message_id=user_msg_id,
        user_message_id=user_msg_id,
        assistant_message_id=assistant_msg_id,
        event_id="",
        status="done",
    )


