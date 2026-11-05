"""Permission evaluator (last-match-wins glob matching).

Ported from opencode ``packages/opencode/src/permission/index.ts:21-99``.
The evaluator's only job is to pick a verdict; persisting the user's
"always" choice is handled by ``rules_io.save_rule`` from the gateway
layer (see ``approvals.py``).
"""

from __future__ import annotations

import fnmatch
import logging
from typing import Any

from .schema import (
    Permission,
    PermissionAction,
    PermissionDecision,
    PermissionRule,
)

logger = logging.getLogger(__name__)


class PermissionDeniedError(Exception):
    """Raised when the evaluator returns ``action == DENY`` or the
    user picked ``reject`` (one-shot or permanent).

    The error message is what the agent loop surfaces back to the
    LLM as a tool error (matching the rest of the codebase's
    err_actionable pattern).
    """

    def __init__(
        self,
        message: str,
        *,
        rule: PermissionRule | None = None,
        target: str = "",
    ) -> None:
        super().__init__(message)
        self.message = str(message)
        self.rule = rule
        self.target = target

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": "denied",
            "error": self.message,
        }
        if self.rule is not None:
            payload["rule"] = {
                "permission": self.rule.permission,
                "pattern": self.rule.pattern,
                "action": self.rule.action.value,
            }
        if self.target:
            payload["target"] = self.target
        return payload


# ── Default ruleset (used when the user file is empty / absent) ──
# Tier R0 tools (read-only) are auto-allowed at the evaluator level
# (see ``_is_auto_allowed``) — no need to list them in defaults.


DEFAULT_RULES: list[PermissionRule] = [
    # R1 network read — ask once
    PermissionRule(
        permission=Permission.WEB_FETCH.value, pattern="*",
        action=PermissionAction.ASK,
        comment="Network reads ask by default",
    ),
    PermissionRule(
        permission=Permission.WEB_SEARCH.value, pattern="*",
        action=PermissionAction.ASK,
    ),
    # R2 workspace write — ask every call
    PermissionRule(
        permission=Permission.WRITE_FILE.value, pattern="*",
        action=PermissionAction.ASK,
        comment="Workspace writes ask by default",
    ),
    PermissionRule(
        permission=Permission.EDIT.value, pattern="*",
        action=PermissionAction.ASK,
    ),
    # R3 side-effects — ask every call
    PermissionRule(
        permission=Permission.RUN_COMMAND.value, pattern="*",
        action=PermissionAction.ASK,
        comment="Shell commands ask by default",
    ),
    PermissionRule(
        permission=Permission.RUN_BACKTEST.value, pattern="*",
        action=PermissionAction.ASK,
    ),
    PermissionRule(
        permission=Permission.GET_MARKET_DATA.value, pattern="*",
        action=PermissionAction.ASK,
    ),
    PermissionRule(
        permission=Permission.IMPORT_DATA.value, pattern="*",
        action=PermissionAction.ASK,
    ),
    PermissionRule(
        permission=Permission.COMPUTE_FACTOR.value, pattern="*",
        action=PermissionAction.ASK,
    ),
    PermissionRule(
        permission=Permission.DELEGATE_TO_AGENT.value, pattern="*",
        action=PermissionAction.ASK,
    ),
    # R4 destructive — deny by default
    PermissionRule(
        permission=Permission.DELETE_FILE.value, pattern="*",
        action=PermissionAction.DENY,
        comment="Destructive ops deny by default",
    ),
]


def _is_auto_allowed(tool_name: str) -> bool:
    """R0 tools (pure read / list) never trigger a prompt."""
    return tool_name in {
        Permission.READ_FILE.value,
        Permission.LIST_FILES.value,
        Permission.LIST_HISTORY.value,
    }


class PermissionEvaluator:
    """Resolve a tool call to one of ``allow | ask | deny``.

    Resolution order (mirrors opencode's behavior):
      1. R0 tools auto-allow — no rule lookup.
      2. Walk the ruleset in order; collect every rule whose
         ``permission`` matches ``tool_name`` (or ``"*"``) AND whose
         ``pattern`` matches the call's target via ``fnmatch``.
      3. The LAST matching rule wins (allows narrow exceptions
         after broad defaults, e.g. deny *.env after allow *).
      4. If no rule matched, fall back to ``ASK`` (safe default).
    """

    def __init__(self, ruleset: list[PermissionRule] | None = None) -> None:
        # Copy on construction so the caller can mutate their list
        # without disturbing the evaluator's state. None -> defaults.
        if ruleset is None:
            self._rules: list[PermissionRule] = list(DEFAULT_RULES)
        else:
            self._rules = list(ruleset)

    @property
    def rules(self) -> list[PermissionRule]:
        """Read-only view of the active ruleset (for UI display)."""
        return list(self._rules)

    def add_rule(self, rule: PermissionRule) -> None:
        """Append a rule at evaluation-priority-last (overrides
        earlier entries)."""
        self._rules.append(rule)

    def reload(self, ruleset: list[PermissionRule]) -> None:
        """Replace the entire ruleset (used after the user edits
        ``permissions.yaml``)."""
        self._rules = list(ruleset)

    def evaluate(
        self,
        tool_name: str,
        args: dict[str, Any] | None = None,
    ) -> PermissionDecision:
        """Resolve the verdict for ``tool_name(**args)``.

        ``args`` may be empty when the caller only knows the tool
        name (e.g. UI preflight before a user submits).
        """
        args = args or {}
        target = self._extract_target(tool_name, args)

        # 1. R0 fast path
        if _is_auto_allowed(tool_name):
            return PermissionDecision(
                action=PermissionAction.ALLOW,
                rule=None,
                target=target,
            )

        # 2 + 3. last-match-wins glob match
        matched = self._matching_rules(tool_name, target)

        if matched:
            last = matched[-1]
            return PermissionDecision(
                action=last.action,
                rule=last,
                target=target,
            )

        # 4. Safe default
        return PermissionDecision(
            action=PermissionAction.ASK,
            rule=None,
            target=target,
        )

    # ── Helpers ────────────────────────────────────────────────

    def _matching_rules(
        self,
        tool_name: str,
        target: str,
    ) -> list[PermissionRule]:
        out: list[PermissionRule] = []
        for rule in self._rules:
            if rule.permission != tool_name and rule.permission != "*":
                continue
            if not _glob_match(rule.pattern, target):
                continue
            out.append(rule)
        return out

    @staticmethod
    def _extract_target(tool_name: str, args: dict[str, Any]) -> str:
        """Derive the rule-pattern target from the call args.

        - file-path tools: ``args["path"]``
        - run_command:    first token of ``args["command"]``
        - everything else: tool name itself (so a rule keyed on
          ``run_backtest`` still matches when the path glob doesn't
          apply).

        When the relevant arg is missing or empty, fall back to the
        tool name so the rule still matches via a ``pattern="*"``.
        """
        if tool_name in {
            Permission.WRITE_FILE.value,
            Permission.EDIT.value,
            Permission.DELETE_FILE.value,
            Permission.READ_FILE.value,
        }:
            return str(args.get("path") or args.get("file_path") or "") or tool_name
        if tool_name == Permission.RUN_COMMAND.value:
            cmd = str(args.get("command") or "").strip()
            return cmd.split()[0] if cmd else tool_name
        if tool_name == Permission.RUN_BACKTEST.value:
            return str(args.get("strategy_name") or "") or tool_name
        return tool_name


def _glob_match(pattern: str, target: str) -> bool:
    """``fnmatch`` with one extension: ``**`` matches any path
    segment including slashes. ``fnmatch`` already treats ``*`` as
    any-non-slash run, so we only need to translate ``**`` into
    ``*`` after collapsing separators.
    """
    if not pattern:
        return True
    # ``**`` -> ``*`` (already non-segment in fnmatch); ``**/x`` -> ``x``.
    # For the modest rules our users actually write we don't need
    # full globstar; keep it simple.
    p = pattern.replace("**", "*")
    return fnmatch.fnmatchcase(target or "", p)
