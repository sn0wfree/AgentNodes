"""Permission gateway — async handshake between tool execution and the
frontend SSE response.

Lifecycle:
    tool.ainvoke(...)
      evaluator -> ASK
        -> gateway.request(tool_call_id, ...)  # creates Future
           -> SSE emit 'permission_request'
           -> await asyncio.Future (set by respond())
        <- gateway.respond(tool_call_id, action, permanent)
      evaluate permanent -> save_rule()
      continue (allow) / raise (deny) / raise (reject)

The gateway is per-process. Two pending requests for the same
``tool_call_id`` is treated as a programmer error (the second
``respond`` raises).
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Callable

from .evaluator import PermissionEvaluator
from .rules_io import DEFAULT_RULES_PATH, save_rule
from .schema import (
    PermissionAction,
    PermissionDecision,
    PermissionResponse,
    PermissionRule,
)

logger = logging.getLogger(__name__)


# ── Default handshake parameters ──
DEFAULT_TIMEOUT_S: float = 300.0  # 5 min — matches opencode defaults


# ── Event hook signature: gateway -> SSE / TUI ──
# (tool_call_id, decision, args) -> None
RequestHook = Callable[[str, PermissionDecision, dict[str, Any]], None]


class PermissionGateway:
    """Tracks pending permission requests keyed by ``tool_call_id``.

    The gateway owns the ``PermissionEvaluator`` so adding a new rule
    via ``respond(permanent=True)`` immediately affects subsequent
    calls in the same session.
    """

    def __init__(
        self,
        rules_path: Path = DEFAULT_RULES_PATH,
        evaluator: PermissionEvaluator | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        on_request: RequestHook | None = None,
    ) -> None:
        self._evaluator = evaluator or PermissionEvaluator()
        self._rules_path = rules_path
        self._timeout_s = timeout_s
        self._on_request = on_request
        # tool_call_id -> (Future[PermissionResponse], owner session_id)
        self._pending: dict[str, tuple[asyncio.Future[PermissionResponse], str]] = {}
        # Audit log: every ASK response is recorded for debugging.
        self._history: list[dict[str, Any]] = []

    # ── Read-only accessors ─────────────────────────────────────

    @property
    def evaluator(self) -> PermissionEvaluator:
        return self._evaluator

    @property
    def pending(self) -> dict[str, asyncio.Future[PermissionResponse]]:
        return dict(self._pending)

    @property
    def history(self) -> list[dict[str, Any]]:
        return list(self._history)

    # ── Lifecycle ──────────────────────────────────────────────

    def reload_rules(self, rules: list[PermissionRule]) -> None:
        """Replace the active ruleset (e.g. after the user edits the
        YAML file from settings)."""
        self._evaluator.reload(rules)

    async def request(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        args: dict[str, Any],
        decision: PermissionDecision,
    ) -> PermissionResponse:
        """Block until the user responds. Returns the response.

        On timeout, returns ``DENY`` with ``permanent=False`` — the
        agent loop surfaces this as a tool error and the user can
        retry manually.
        """
        if tool_call_id in self._pending:
            # Programmer error — the same tool_call_id should never
            # produce two pending requests.
            raise RuntimeError(
                f"duplicate permission request for tool_call_id={tool_call_id}",
            )

        loop = asyncio.get_event_loop()
        future: asyncio.Future[PermissionResponse] = loop.create_future()
        # P5-5①: record the owning session so the respond endpoint can
        # enforce ownership (the args payload carries __session_id__,
        # stashed by the tool executor for exactly this SSE-hook reason).
        session_id = str(args.get("__session_id__", "") or "")
        self._pending[tool_call_id] = (future, session_id)

        # Fire the SSE / UI hook so the user knows there's a prompt.
        if self._on_request is not None:
            try:
                self._on_request(tool_call_id, decision, args)
            except Exception as exc:  # noqa: BLE001
                logger.warning("permission on_request hook failed: %s", exc)

        started = time.monotonic()
        try:
            response = await asyncio.wait_for(
                future, timeout=self._timeout_s,
            )
        except asyncio.TimeoutError:
            response = PermissionResponse(
                action=PermissionAction.DENY,
                permanent=False,
                reason=f"timeout after {self._timeout_s:.0f}s",
            )
        finally:
            self._pending.pop(tool_call_id, None)
            self._history.append(
                {
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "args": _safe_args(args),
                    "decision_pattern": decision.pattern,
                    "response_action": (
                        response.action.value
                        if "response" in locals() else "timeout"
                    ),
                    "permanent": (
                        response.permanent
                        if "response" in locals() else False
                    ),
                    "elapsed_s": round(time.monotonic() - started, 3),
                },
            )

        if response.permanent:
            self._persist(response, tool_name, decision, args)

        return response

    def respond(
        self,
        tool_call_id: str,
        response: PermissionResponse,
    ) -> bool:
        """Front-end reply. Returns True if a pending request was
        resolved; False when the id is unknown (already timed out
        or answered)."""
        entry = self._pending.get(tool_call_id)
        if entry is None:
            return False
        future = entry[0] if isinstance(entry, tuple) else entry
        if future.done():
            return False
        future.set_result(response)
        return True

    def owner_session_of(self, tool_call_id: str) -> str | None:
        """P5-5①: the session that raised this pending request, or None."""
        entry = self._pending.get(tool_call_id)
        if entry is None:
            return None
        if isinstance(entry, tuple):
            return entry[1] or None
        return None

    # ── Internals ──────────────────────────────────────────────

    def _persist(
        self,
        response: PermissionResponse,
        tool_name: str,
        decision: PermissionDecision,
        args: dict[str, Any],
    ) -> None:
        rule = PermissionRule(
            permission=tool_name,
            pattern=decision.pattern,
            action=response.action,
        )
        try:
            save_rule(rule, self._rules_path)
            # Make the new rule effective immediately for sibling
            # tool calls in the same session.
            self._evaluator.add_rule(rule)
            logger.info(
                "permission: persisted permanent %s for %s pattern=%s",
                response.action.value, tool_name, decision.pattern,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("permission rule persist failed: %s", exc)


def _safe_args(args: dict[str, Any]) -> dict[str, Any]:
    """Truncate big fields before logging the request."""
    out: dict[str, Any] = {}
    for k, v in args.items():
        s = repr(v)
        out[k] = s[:120] + "…" if len(s) > 120 else v
    return out
