"""Permission schema definitions.

Maps to opencode's ``packages/opencode/src/permission/index.ts:21-32``
Rule / Ruleset. Kept intentionally small: any new tool must opt-in
by adding a member to ``Permission``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class PermissionAction(str, Enum):
    """Rule action — matches opencode's three-way gate."""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class Permission(str, Enum):
    """Tool categories that the ruleset can gate.

    **这不是白名单**（2026-11-06 批次 4d 修正旧 docstring）：本枚举只是
    ``DEFAULT_RULES`` 的构造常量。判定走 ``PermissionEvaluator`` 的
    「last-match-wins 字符串 glob」，**不查枚举**。所以：

    - 任意工具名都能被 ``permissions.yaml`` 里的字符串规则匹配
    - 本枚举里没有的工具 → 走兜底 ``ASK``（安全默认），不是自动放行
    - 只有 ``_is_auto_allowed()`` 里那三个只读工具是自动放行的

    **为什么去掉四个业务成员**（RUN_BACKTEST / GET_MARKET_DATA /
    IMPORT_DATA / COMPUTE_FACTOR）：kernel 只声明**通用工具类别**，
    业务工具由 L2 插件自带权限配置。删掉后这四个工具走兜底 ASK，
    与删除前显式 ASK 规则**行为一致**；用户已写的
    ``permission: run_backtest`` 规则仍然有效（字符串匹配）。
    """

    # ── R0 read-only (auto-allowed) ────────────────────────────
    READ_FILE = "read"
    LIST_FILES = "list"
    LIST_HISTORY = "list_history"

    # ── R1 network read (ask once default) ────────────────────
    WEB_FETCH = "web_fetch"
    WEB_SEARCH = "websearch"

    # ── R2 workspace write (ask every call default) ───────────
    WRITE_FILE = "write"
    EDIT = "edit"

    # ── R3 execute side-effects (ask every call default) ──────
    RUN_COMMAND = "shell"

    # ── R4 destructive (deny by default) ───────────────────────
    DELETE_FILE = "delete_file"

    # ── Agent delegation ───────────────────────────────────────
    DELEGATE_TO_AGENT = "task"

    # Catch-all when a tool name does not map to a known category.
    UNKNOWN = "*"


@dataclass
class PermissionRule:
    """One row in the ruleset.

    Attributes:
        permission: tool name or ``"*"`` for wildcard.
        pattern:    glob applied to the tool's target (path / command).
        action:     allow / ask / deny.
        comment:    optional human-readable annotation (kept in the
                    YAML but not consulted by the evaluator).
    """

    permission: str
    pattern: str
    action: PermissionAction
    comment: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "permission": self.permission,
            "pattern": self.pattern,
            "action": self.action.value,
        }
        if self.comment:
            d["comment"] = self.comment
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PermissionRule":
        return cls(
            permission=str(d.get("permission", "*")),
            pattern=str(d.get("pattern", "*")),
            action=PermissionAction(str(d.get("action", "ask"))),
            comment=str(d.get("comment", "")),
        )


@dataclass
class PermissionDecision:
    """The verdict returned by the evaluator.

    ``rule`` is the rule that produced the verdict (None when no
    rule matched and the evaluator fell back to the default).
    """

    action: PermissionAction
    rule: Optional[PermissionRule] = None
    target: str = ""

    @property
    def pattern(self) -> str:
        """The pattern string the user-facing UI should display."""
        if self.rule is not None:
            return self.rule.pattern
        return self.target or "*"


@dataclass
class PermissionResponse:
    """User's reply to a permission_request SSE event.

    Attributes:
        action:  allow | deny.
        permanent: True if the user picked "always" — the gateway
                   should persist a rule and the evaluator will
                   remember it across calls.
        reason:  free-form text (used when ``action == "deny"``).
    """

    action: PermissionAction
    permanent: bool = False
    reason: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
