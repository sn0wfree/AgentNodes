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
    # 后台任务管理（bg_tools.py）：action=start 时执行长命令——与 shell
    # 同属 R3 执行面。2026-11-11 前不在枚举里，_extract_target 拿不到
    # 命令串，H2 提权规则对它完全失效（asset-value-review §1.2 缺陷 3）。
    RUN_BG_COMMAND = "run_bg_command"

    # ── R4 destructive (deny by default) ───────────────────────
    DELETE_FILE = "delete_file"

    # ── Agent delegation ───────────────────────────────────────
    DELEGATE_TO_AGENT = "task"

    # Catch-all when a tool name does not map to a known category.
    UNKNOWN = "*"


# ── Hard deny（批次 6 · P1 修复）─────────────────────────────────────


@dataclass(frozen=True)
class HardDenyRule:
    """不可覆盖的拒绝规则——决策流水线**第 1 步**，先于一切规则匹配。

    **为什么独立于 ``PermissionRule``**：P1 缺陷（2026-11-07 调研实测）——
    ``PermissionGateway.respond(permanent=True)`` → ``save_rule()`` 把用户
    的 allow 规则**追加到 ruleset 末尾**，而 evaluator 是 last-match-wins，
    于是「在删除提示上点'永远允许'」会盖掉 ``DELETE_FILE=deny`` 的默认规则。
    hard_deny 是**独立层**：不在 ruleset 里、不进 YAML、任何来源都改不了。

    Attributes:
        permission: 工具名或 ``"*"``。
        pattern:    fnmatch，对 target 与规范化 target **双匹配**。
        reason:     审计用（进入 ``PermissionDecision.reason``）。
    """

    permission: str
    pattern: str
    reason: str


#: 三条任何级别都不放开的硬约束（phase1 doc §3.2）。
#:
#: **诚实边界**：H1/H3 对 **shell 重定向**（``echo x > .agentnodes/...``）
#: 拦不住——那是解析器差分（与 P3 同源）。文本 hard_deny 是纵深防御的
#: 一层；真正的强制靠 OS 沙箱（P4，本阶段不做）。
DEFAULT_HARD_DENY: tuple[HardDenyRule, ...] = (
    # ── H1 · 禁止 agent 改自己的权限/钩子配置（自我授权）─────────────
    HardDenyRule("*", ".agentnodes/**", "self-configuration is not permitted"),
    HardDenyRule("*", "**/permissions.yaml", "self-configuration is not permitted"),
    HardDenyRule("*", ".claude/**", "self-configuration is not permitted"),
    HardDenyRule("*", ".git/hooks/**", "git hooks are agent-writable code paths"),
    HardDenyRule("*", ".mcp.json", "MCP config is agent-writable code paths"),
    # ── H2 · 禁止提权 ────────────────────────────────────────────────
    # （run_bg_command 与 shell 同属执行面，提权规则必须镜像——缺陷 3：
    #   旧实现 H2 只对 "shell" 生效，run_bg_command 起后台 sudo 畅通。）
    HardDenyRule("shell", "sudo", "privilege escalation"),
    HardDenyRule("shell", "sudo *", "privilege escalation"),
    HardDenyRule("shell", "su", "privilege escalation"),
    HardDenyRule("shell", "su *", "privilege escalation"),
    HardDenyRule("shell", "doas*", "privilege escalation"),
    HardDenyRule("shell", "pkexec*", "privilege escalation"),
    *(HardDenyRule(Permission.RUN_BG_COMMAND.value, p, "privilege escalation")
      for p in ("sudo", "sudo *", "su", "su *", "doas*", "pkexec*")),
    # ── H3 · 凭据与云 metadata ───────────────────────────────────────
    HardDenyRule("*", "**/.ssh/**", "credential paths"),
    HardDenyRule("*", ".ssh/**", "credential paths"),
    HardDenyRule("*", "**/.aws/**", "credential paths"),
    HardDenyRule("*", "**/.kube/**", "credential paths"),
    HardDenyRule("*", "**/.env", "credential paths"),
    HardDenyRule("*", "**/.env.*", "credential paths"),
    HardDenyRule("web_fetch", "*169.254.169.254*", "cloud metadata endpoint"),
)


# ── 权限分级（批次 6 · L0-L4）────────────────────────────────────────


class PermissionMode(str, Enum):
    """权限分级模式（phase1 doc §3.1）。

    - **L1 是默认**：与 Claude Code ``default`` / Codex ``read-only`` 对齐——
      开箱能改工作区文件（checklist E3 前提），shell 需确认（能力爆炸）。
    - **不提供 L5/bypass**：它要求一次性 VM + egress 代理 + 审计日志三个
      前提，缺一即事故制造机。
    """

    L0_READONLY = "L0_readonly"
    L1_WORKSPACE_EDIT = "L1_workspace_edit"
    L2_SANDBOX_EXEC = "L2_sandbox_exec"
    L3_NETWORKED = "L3_networked"
    L4_CONFIRM_ALL = "L4_confirm_all"

    @classmethod
    def default(cls) -> "PermissionMode":
        return cls.L1_WORKSPACE_EDIT

    @classmethod
    def from_value(cls, v: str | "PermissionMode") -> "PermissionMode":
        if isinstance(v, cls):
            return v
        for m in cls:
            if m.value == str(v):
                return m
        raise ValueError(
            f"unknown permission mode: {v!r} (expected one of "
            f"{[m.value for m in cls]})"
        )


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


def _r(permission: str, pattern: str, action: PermissionAction,
       comment: str = "") -> PermissionRule:
    return PermissionRule(
        permission=permission, pattern=pattern, action=action, comment=comment
    )


#: 各模式的**基础规则**（用户 YAML 在其后叠加，last-match-wins 可覆盖；
#: hard_deny 独立本表之外，模式不可放松）。
#:
#: L1 下 write/edit **allow 是行为变更**（原 ask）——checklist E3
# 「能写任意工作区文件」的前提；workspace 边界由 PathWhitelist 把守。
MODE_PRESETS: dict[str, tuple[PermissionRule, ...]] = {
    PermissionMode.L0_READONLY.value: (
        # 只读观察：读组自动放行，其余一律拒绝（真正的 observation 模式）
        _r("write", "*", PermissionAction.DENY, "L0: read-only mode"),
        _r("edit", "*", PermissionAction.DENY, "L0: read-only mode"),
        _r("shell", "*", PermissionAction.DENY, "L0: read-only mode"),
        _r("task", "*", PermissionAction.DENY, "L0: read-only mode"),
        _r("websearch", "*", PermissionAction.DENY, "L0: read-only mode"),
        _r("web_fetch", "*", PermissionAction.DENY, "L0: read-only mode"),
        _r("delete_file", "*", PermissionAction.DENY, "L0: read-only mode"),
    ),
    PermissionMode.L1_WORKSPACE_EDIT.value: (
        # 工作区编辑：写允许（边界=PathWhitelist），执行/联网需确认
        _r("write", "*", PermissionAction.ALLOW,
           "L1: workspace edits auto-allowed (boundary enforced elsewhere)"),
        _r("edit", "*", PermissionAction.ALLOW, "L1: workspace edits"),
        _r("shell", "*", PermissionAction.ASK, "L1: command execution asks"),
        _r("task", "*", PermissionAction.ASK, "L1: delegation asks"),
        _r("websearch", "*", PermissionAction.ASK, "L1: network asks"),
        _r("web_fetch", "*", PermissionAction.ASK, "L1: network asks"),
        _r("delete_file", "*", PermissionAction.DENY,
           "destructive ops deny by default"),
    ),
    PermissionMode.L2_SANDBOX_EXEC.value: (
        # 沙箱执行：L1 + shell 自动（沙箱边界待 P4；显式 opt-in）
        _r("write", "*", PermissionAction.ALLOW, "L2: workspace edits"),
        _r("edit", "*", PermissionAction.ALLOW, "L2: workspace edits"),
        _r("shell", "*", PermissionAction.ALLOW,
           "L2: exec auto (sandbox boundary pending P4)"),
        _r("task", "*", PermissionAction.ASK, "L2: delegation asks"),
        _r("websearch", "*", PermissionAction.ASK, "L2: network asks"),
        _r("web_fetch", "*", PermissionAction.ASK, "L2: network asks"),
        _r("delete_file", "*", PermissionAction.DENY,
           "destructive ops deny by default"),
    ),
    PermissionMode.L3_NETWORKED.value: (
        # 受控联网：L2 + web 自动（域名白名单代理待 P4，显式 opt-in）
        _r("write", "*", PermissionAction.ALLOW, "L3: workspace edits"),
        _r("edit", "*", PermissionAction.ALLOW, "L3: workspace edits"),
        _r("shell", "*", PermissionAction.ALLOW, "L3: exec auto"),
        _r("task", "*", PermissionAction.ASK, "L3: delegation asks"),
        _r("websearch", "*", PermissionAction.ALLOW, "L3: networked"),
        _r("web_fetch", "*", PermissionAction.ALLOW, "L3: networked"),
        _r("delete_file", "*", PermissionAction.DENY,
           "destructive ops deny by default"),
    ),
    PermissionMode.L4_CONFIRM_ALL.value: (
        # 人工确认：读组之外一律 ask（含删除）
        _r("write", "*", PermissionAction.ASK, "L4: confirm everything"),
        _r("edit", "*", PermissionAction.ASK, "L4: confirm everything"),
        _r("shell", "*", PermissionAction.ASK, "L4: confirm everything"),
        _r("task", "*", PermissionAction.ASK, "L4: confirm everything"),
        _r("websearch", "*", PermissionAction.ASK, "L4: confirm everything"),
        _r("web_fetch", "*", PermissionAction.ASK, "L4: confirm everything"),
        _r("delete_file", "*", PermissionAction.ASK, "L4: confirm everything"),
    ),
}
