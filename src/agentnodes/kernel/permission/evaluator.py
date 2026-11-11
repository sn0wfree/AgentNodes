"""Permission evaluator — 决策流水线（批次 6 改造）。

**流水线**（任一步 deny 即终止）::

    1. hard_deny 双匹配（独立层，不可覆盖，P1 修复）
    2. 规则匹配：last-match-wins（用户 YAML / 模式预设）
       —— **在 R0 之前**：旧实现 R0 快路径让 read 的用户规则成为死代码
    3. R0 只读快路径（无规则命中时）
    4. 兜底 ASK
    5. 非交互降级：**一切** ASK → DENY（interactive=False）
    6. on_decision 审计回调（每次决策）

Ported from opencode ``packages/opencode/src/permission/index.ts:21-99``.
The evaluator's only job is to pick a verdict; persisting the user's
"always" choice is handled by ``rules_io.save_rule`` from the gateway
layer (see ``approvals.py``).
"""

from __future__ import annotations

import fnmatch
import logging
import os
from typing import Any, Callable

from .schema import (
    DEFAULT_HARD_DENY,
    MODE_PRESETS,
    HardDenyRule,
    Permission,
    PermissionAction,
    PermissionDecision,
    PermissionMode,
    PermissionRule,
)

logger = logging.getLogger(__name__)

#: registry 真实工具名 -> 枚举规范名（缺陷 2，2026-11-11）。
#: 事实源：``build_default_registry()`` 的工具名 vs ``Permission`` 枚举值。
#: 目前只有 web_fetch 一处分歧；新增工具时**两边名字必须对齐**，
#: 分歧就登记到这里（R-018：安全判定必须认真实调用形态）。
_TOOL_ALIASES: dict[str, str] = {
    "webfetch": Permission.WEB_FETCH.value,
}


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
#
# **批次 6**：默认规则集 = ``MODE_PRESETS[L1]``（工作区编辑模式）。
# write/edit 从 ask 变 allow 是**有意的行为变更**——checklist E3
# 「能写任意工作区文件」的前提；workspace 边界由 PathWhitelist 把守。
# 旧表（全 ask）已被 L0–L4 分级取代；用户 YAML 仍叠加在预设之后。


DEFAULT_RULES: list[PermissionRule] = list(
    MODE_PRESETS[PermissionMode.L1_WORKSPACE_EDIT.value]
)


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

    def __init__(
        self,
        ruleset: list[PermissionRule] | None = None,
        *,
        hard_deny: tuple[HardDenyRule, ...] | list[HardDenyRule] | None = None,
        interactive: bool = True,
        on_decision: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        # Copy on construction so the caller can mutate their list
        # without disturbing the evaluator's state. None -> defaults.
        if ruleset is None:
            self._rules: list[PermissionRule] = list(DEFAULT_RULES)
        else:
            self._rules = list(ruleset)
        # hard_deny 独立于 ruleset（P1 修复）：用户 YAML / 永久允许都改不了它。
        self._hard_deny: tuple[HardDenyRule, ...] = (
            tuple(hard_deny) if hard_deny is not None
            else tuple(DEFAULT_HARD_DENY)
        )
        # 非交互模式（CI / -p 脚本）：一切 ASK → DENY（对齐 Gemini
        # "ask_user 在非交互模式按 deny 处理"）。
        self.interactive = interactive
        # 审计（流水线第 6 步）：每次决策发一条 dict。
        self.on_decision = on_decision

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
        # registry 真实工具名 -> 枚举规范名（2026-11-11，缺陷 2）：
        # Permission.WEB_FETCH.value 是 "web_fetch"，registry 里的真实名字
        # 是 "webfetch"——不归一则云 metadata 的 hard_deny 对真实名不生效。
        # 枚举里只有这一个拼法分歧（WEB_SEARCH = "websearch" 恰好一致）。
        tool_name = _TOOL_ALIASES.get(tool_name, tool_name)
        args = args or {}
        target = self._extract_target(tool_name, args)

        # 1. hard_deny（P1 修复）：独立层，先于一切规则，不可覆盖。
        #    对 target 与规范化 target 双匹配（P2）。
        hit = self._match_hard_deny(tool_name, target)
        if hit is not None:
            decision = PermissionDecision(
                action=PermissionAction.DENY,
                rule=None,
                target=target,
            )
            decision.hard = True          # type: ignore[attr-defined]
            decision.reason = hit.reason  # type: ignore[attr-defined]
            self._audit(decision, tool_name, target)
            return decision

        # 2. 规则匹配（last-match-wins，用户规则 / 模式预设）。
        #    **必须在 R0 之前**（批次 6 修）：旧实现 R0 快路径在规则前
        #    返回 ALLOW，用户对 read 的 deny/allow 规则是死代码。
        matched = self._matching_rules(tool_name, target)
        if matched:
            last = matched[-1]
            decision = PermissionDecision(
                action=last.action,
                rule=last,
                target=target,
            )
        elif _is_auto_allowed(tool_name):
            # 3. R0 快路径：无规则命中时只读工具自动放行
            decision = PermissionDecision(
                action=PermissionAction.ALLOW,
                rule=None,
                target=target,
            )
        else:
            # 4. 兜底 ASK
            decision = PermissionDecision(
                action=PermissionAction.ASK,
                rule=None,
                target=target,
            )

        # 5. 非交互降级：**一切** ASK → DENY（CI / -p 脚本语义，
        #    对齐 Gemini "ask_user 在非交互模式按 deny 处理"）。
        #    allow / deny 不受影响。
        if decision.action == PermissionAction.ASK and not self.interactive:
            decision = PermissionDecision(
                action=PermissionAction.DENY,
                rule=decision.rule,
                target=target,
            )

        self._audit(decision, tool_name, target)
        return decision

    # ── hard deny / 审计 ───────────────────────────────────────

    def _match_hard_deny(
        self, tool_name: str, target: str
    ) -> HardDenyRule | None:
        """hard_deny 双匹配：raw 与 normpath 形态，任一命中即拒。"""
        candidates = {target, _norm_target(target)}
        for hd in self._hard_deny:
            if hd.permission != tool_name and hd.permission != "*":
                continue
            for cand in candidates:
                if _glob_match(hd.pattern, cand):
                    return hd
        return None

    def _audit(
        self,
        decision: PermissionDecision,
        tool_name: str,
        target: str,
    ) -> None:
        if self.on_decision is None:
            return
        try:
            self.on_decision({
                "tool": tool_name,
                "target": target,
                "action": decision.action.value,
                "rule": (
                    f"{decision.rule.permission}:{decision.rule.pattern}"
                    if decision.rule is not None else None
                ),
                "hard": bool(getattr(decision, "hard", False)),
                "reason": getattr(decision, "reason", "") or "",
            })
        except Exception:  # noqa: BLE001 —— 审计失败不影响判定
            logger.warning("permission audit callback failed", exc_info=True)

    # ── Helpers ────────────────────────────────────────────────

    def _matching_rules(
        self,
        tool_name: str,
        target: str,
    ) -> list[PermissionRule]:
        """规则匹配（P2 双匹配语义）。

        - **deny**：raw 或 normpath 任一命中即算命中（变体不放过）
        - **allow/ask**：需两形态都命中（防"规范化后越界"的误放行）
        """
        raw_cand = target
        norm_cand = _norm_target(target)
        out: list[PermissionRule] = []
        for rule in self._rules:
            if rule.permission != tool_name and rule.permission != "*":
                continue
            raw_hit = _glob_match(rule.pattern, raw_cand)
            if rule.action == PermissionAction.DENY:
                if raw_hit or _glob_match(rule.pattern, norm_cand):
                    out.append(rule)
                continue
            if raw_hit and _glob_match(rule.pattern, norm_cand):
                out.append(rule)
        return out

    @staticmethod
    def _extract_target(tool_name: str, args: dict[str, Any]) -> str:
        """Derive the rule-pattern target from the call args.

        - file-path tools: ``args["path"]``
        - shell / run_command: **整条命令串**（批次 13 附录 P0 修）
        - web_fetch:       URL
        - everything else: tool name itself (so a rule keyed on
          ``run_backtest`` still matches when the path glob doesn't
          apply).

        When the relevant arg is missing or empty, fall back to the
        tool name so the rule still matches via a ``pattern="*"``.

        **为什么 shell 用整条命令串而不是首词**（批次 13 附录 §1 实测）：
        旧实现对 shell 取 ``cmd.split()[0]``，于是 hard_deny 的
        ``**/.ssh/**`` 对 ``cat ~/.ssh/id_rsa`` **永远不匹配**——两条路径
        都放行（原则七：检查信号必须看见真实操作对象）。

        代价：``sudo`` 这类**首词规则**需要 ``sudo *`` 形态在整串上匹配。
        ``_glob_match`` 已把 ``*`` 当任意非空串，因此
        ``fnmatch("sudo echo x", "sudo*")`` → True，
        ``DEFAULT_HARD_DENY`` 里的 ``sudo *`` / ``pkexec*`` 仍然命中。
        """
        if tool_name in {
            Permission.WRITE_FILE.value,
            Permission.EDIT.value,
            Permission.DELETE_FILE.value,
            Permission.READ_FILE.value,
        }:
            return str(args.get("path") or args.get("file_path") or "") or tool_name
        if tool_name in (Permission.RUN_COMMAND.value, Permission.RUN_BG_COMMAND.value):
            # run_bg_command 与 shell 同为执行面（缺陷 3）：旧实现不认识它，
            # target 落回工具名——H3 的 `*` 路径规则与 H2 提权规则全部失效。
            # command 仅 action=start 时有意义；status/wait/log/kill 无命令，
            # target 落回工具名（无命令 = 无路径/提权可判）。
            cmd = str(args.get("command") or "").strip()
            return cmd or tool_name
        # web_fetch：target = URL（批次 6）——H3 的云 metadata hard deny
        # （169.254.169.254）需要看到 URL 才能拦。
        if tool_name == Permission.WEB_FETCH.value:
            return str(args.get("url") or args.get("u") or "") or tool_name
        return tool_name


def _norm_target(target: str) -> str:
    """P2：target 的规范化形态（用于 deny 规则的变体匹配）。

    只做**文本层**规范化：反斜杠归一、``./`` 前缀去除、
    ``a/../b`` 折叠（``os.path.normpath`` 的跨平台语义）。
    **不做 symlink 解析**——evaluator 没有 workspace 根，无法 realpath；
    文件边界由 ``PathWhitelist``（sandbox/legacy.py，已有
    ``.resolve() + relative_to`` 双保险）把守，见批次 6 设计 §0。
    """
    if not target:
        return target
    try:
        normalized = os.path.normpath(target).replace("\\", "/")
    except (ValueError, TypeError):  # pragma: no cover — 防御
        return target
    return normalized or target


def _glob_match(pattern: str, target: str) -> bool:
    """``fnmatch`` with one extension: ``**`` matches any path
    segment including slashes. ``fnmatch`` already treats ``*`` as
    any-non-slash run, so we only need to translate ``**`` into
    ``*`` after collapsing separators.

    **裸相对形态必须同被匹配**（2026-11-11，缺陷 1）：``**/.env`` 替换后
    成为 ``*/.env``，仍要求一个 ``/``——于是 ``read .env``（模型最自然的
    写法）恰好是唯一被放过的形态，实测读出密钥。这里在主匹配失败时回退
    匹配「去掉 ``*/`` 前缀」的形态。只影响 hard_deny 与规则的 ``**/``
    模式（MODE_PRESETS 全是 ``"*"``），方向是拒得更宽 = fail-closed。
    """
    if not pattern:
        return True
    # ``**`` -> ``*`` (already non-segment in fnmatch); ``**/x`` -> ``x``.
    # For the modest rules our users actually write we don't need
    # full globstar; keep it simple.
    p = pattern.replace("**", "*")
    t = target or ""
    if fnmatch.fnmatchcase(t, p):
        return True
    if p.startswith("*/") and fnmatch.fnmatchcase(t, p[2:]):
        return True
    return False
