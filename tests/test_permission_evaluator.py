"""Unit tests for the permission ruleset evaluator.

Tier 1 A1 — ported from opencode's ``packages/opencode/src/permission/index.ts``.
Behaviour covered:
    - R0 tools auto-allow
    - last-match-wins across the ruleset
    - default ASK when nothing matches
    - glob pattern matching via fnmatch
    - extract_target for file / shell / backtest tools
    - permanent rule append-once (no duplicate)
    - YAML round-trip preserves ordering
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentnodes.kernel.permission import (
    Permission,
    PermissionAction,
    PermissionEvaluator,
    PermissionRule,
    load_rules,
    save_rule,
)
from agentnodes.kernel.permission.evaluator import PermissionDeniedError

# ── Evaluator basics ──────────────────────────────────────────────


def test_r0_tools_auto_allow():
    ev = PermissionEvaluator()
    decision = ev.evaluate("read", {"path": "/etc/passwd"})
    assert decision.action == PermissionAction.ALLOW
    assert decision.rule is None


def test_default_action_for_workspace_write_is_allow_in_L1():
    """批次 6：默认模式 = L1（工作区编辑）——write 自动放行。

    旧行为（write=ask）被 L0-L4 分级取代：E3「能写任意工作区文件」
    是第一阶段 checklist 的前提；workspace 边界由 PathWhitelist 把守。
    """
    ev = PermissionEvaluator()
    decision = ev.evaluate("write", {"path": "hello.py"})
    assert decision.action == PermissionAction.ALLOW
    assert decision.rule is not None
    assert decision.rule.action == PermissionAction.ALLOW


def test_default_action_is_ask_for_unmodeled_tool():
    """预设没覆盖的工具（如自定义工具）→ 兜底 ASK。"""
    ev = PermissionEvaluator()
    decision = ev.evaluate("some_custom_tool", {})
    assert decision.action == PermissionAction.ASK
    assert decision.rule is None


def test_destructive_tool_denied_by_default():
    ev = PermissionEvaluator()
    decision = ev.evaluate("delete_file", {"path": "anything"})
    assert decision.action == PermissionAction.DENY
    assert decision.rule is not None


def test_last_match_wins():
    """A narrow exception appended later overrides an earlier broad rule."""
    ev = PermissionEvaluator(ruleset=[
        PermissionRule(
            permission=Permission.WRITE_FILE.value,
            pattern="*",
            action=PermissionAction.ASK,
        ),
        PermissionRule(
            permission=Permission.WRITE_FILE.value,
            pattern="strategies/*",
            action=PermissionAction.ALLOW,
        ),
        PermissionRule(
            permission=Permission.WRITE_FILE.value,
            pattern="*.env",
            action=PermissionAction.DENY,
        ),
    ])
    assert ev.evaluate(
        "write", {"path": "strategies/foo.py"},
    ).action == PermissionAction.ALLOW
    assert ev.evaluate(
        "write", {"path": "README.md"},
    ).action == PermissionAction.ASK
    assert ev.evaluate(
        "write", {"path": ".env"},
    ).action == PermissionAction.DENY


def test_wildcard_permission_matches_any_tool():
    ev = PermissionEvaluator(ruleset=[
        PermissionRule(
            permission="*", pattern="*",
            action=PermissionAction.ALLOW,
        ),
    ])
    assert ev.evaluate("write", {"path": "x"}).action == PermissionAction.ALLOW
    assert ev.evaluate("shell", {"command": "ls"}).action == PermissionAction.ALLOW
    # R0 still auto-allow (fast path), but the wildcard rule also
    # matches — verdict is allow either way.


# ── Target extraction ──────────────────────────────────────────────


def test_extract_target_write_file_uses_path():
    ev = PermissionEvaluator()
    d = ev.evaluate("write", {"path": "strategies/x.py"})
    assert d.target == "strategies/x.py"


def test_extract_target_run_command_uses_whole_command():
    """shell 的 target 是**整条命令串**，不是首词。

    **批次 13 附录 §1（P0 修复）**：旧实现取 ``cmd.split()[0]``，于是
    ``hard_deny`` 的路径模式（``**/.ssh/**`` / ``**/.aws/**``）对
    ``cat ~/.ssh/id_rsa`` **永远匹配不上**——实测两条路径都放行。
    原则七：检查信号必须看见真实操作对象，而不是它的第一个词。

    副作用（可接受）：首词规则需写成 ``sudo *`` 形态在整串上匹配；
    ``DEFAULT_HARD_DENY`` 里 ``sudo`` / ``su`` / ``doas*`` / ``pkexec*``
    都已兼容（走 ``_glob_match``）。
    """
    ev = PermissionEvaluator()
    d = ev.evaluate("shell", {"command": "rm -rf /"})
    assert d.target == "rm -rf /"


def test_run_command_still_denied_via_hard_deny():
    """整串 target 之后，``rm -rf /`` 仍必须被拒绝（行为不回退）。"""
    from agentnodes.kernel.permission.schema import PermissionAction

    ev = PermissionEvaluator(hard_deny=(
        # 与 DEFAULT_HARD_DENY 的 shell_escape 同形：危险命令直接 deny
        # （此处用 hard_deny 构造，避免依赖默认表里是否恰有该条）
        __import__(
            "agentnodes.kernel.permission.schema", fromlist=["HardDenyRule"],
        ).HardDenyRule("shell", "rm -rf *", "destructive command"),
    ))
    d = ev.evaluate("shell", {"command": "rm -rf /"})
    assert d.action == PermissionAction.DENY
    assert getattr(d, "hard", False) is True


def test_extract_target_unknown_business_tool_falls_back_to_name():
    """批次 4d：kernel 不再为业务工具做 target 提取。

    原测试 ``test_extract_target_run_backtest_uses_strategy_name`` 断言
    ``run_backtest`` 的 target 取 ``strategy_name``——那是量化域专属知识。
    删掉后 target 退化为工具名本身，业务工具的规则匹配依赖
    ``pattern="*"``（或用户直接写工具名规则）。

    **这是有意的行为变更**，代价已记入设计文档 §2.2：
    用户若写 ``permission: run_backtest, pattern: "my_strat"``，
    该命名 pattern 不再生效（退化成工具名匹配）。
    """
    ev = PermissionEvaluator()
    d = ev.evaluate("run_backtest", {"strategy_name": "momentum_v3"})
    assert d.target == "run_backtest"
    # 兜底 ASK（安全默认），不是放行
    assert d.action.value == "ask"


def test_business_tool_rule_with_wildcard_still_works():
    """用户写的业务工具规则（pattern="*"）仍然生效——字符串匹配不查枚举。"""
    from agentnodes.kernel.permission.schema import (
        PermissionAction,
        PermissionRule,
    )

    ev = PermissionEvaluator([
        PermissionRule(
            permission="run_backtest",
            pattern="*",
            action=PermissionAction.DENY,
        ),
    ])
    d = ev.evaluate("run_backtest", {"strategy_name": "x"})
    assert d.action is PermissionAction.DENY


def test_extract_target_read_file_falls_back_to_file_path():
    ev = PermissionEvaluator()
    d = ev.evaluate("read", {"file_path": "docs/x.md"})
    assert d.target == "docs/x.md"


def test_extract_target_empty_args_returns_tool_name():
    """When the call args lack the expected field, fall back to the
    tool name so the rule still matches."""
    ev = PermissionEvaluator()
    d = ev.evaluate("write", {})
    assert d.target == "write"


# ── Glob patterns ──────────────────────────────────────────────────


def test_glob_simple_star():
    ev = PermissionEvaluator(ruleset=[
        PermissionRule(
            permission=Permission.WRITE_FILE.value,
            pattern="strategies/*",
            action=PermissionAction.ALLOW,
        ),
    ])
    assert ev.evaluate(
        "write", {"path": "strategies/momentum.py"},
    ).action == PermissionAction.ALLOW
    assert ev.evaluate(
        "write", {"path": "scripts/build.py"},
    ).action == PermissionAction.ASK


def test_glob_double_star_treated_as_single():
    """fnmatch treats `*` and `**` identically. We accept that."""
    ev = PermissionEvaluator(ruleset=[
        PermissionRule(
            permission=Permission.WRITE_FILE.value,
            pattern="strategies/**",
            action=PermissionAction.ALLOW,
        ),
    ])
    assert ev.evaluate(
        "write", {"path": "strategies/x.py"},
    ).action == PermissionAction.ALLOW


def test_decision_pattern_property_for_display():
    """The UI shows `decision.pattern`; it should be the rule's
    pattern, not the raw target."""
    ev = PermissionEvaluator(ruleset=[
        PermissionRule(
            permission=Permission.WRITE_FILE.value,
            pattern="strategies/*",
            action=PermissionAction.ASK,
        ),
    ])
    d = ev.evaluate("write", {"path": "strategies/foo.py"})
    assert d.pattern == "strategies/*"
    # No rule matched -> falls back to target.
    d2 = ev.evaluate("shell", {"command": "ls"})
    assert d2.pattern == "ls"


# ── PermissionDeniedError payload ──────────────────────────────────


def test_deny_raises_with_payload():
    """The error message travels back to the agent loop as a tool
    error; the rule metadata helps the LLM explain itself."""
    rule = PermissionRule(
        permission=Permission.DELETE_FILE.value,
        pattern="*",
        action=PermissionAction.DENY,
    )
    err = PermissionDeniedError(
        "Permission denied: delete_file pattern=*",
        rule=rule,
        target="strategies/momentum.py",
    )
    payload = err.to_payload()
    assert payload["status"] == "denied"
    assert payload["target"] == "strategies/momentum.py"
    assert payload["rule"]["action"] == "deny"


# ── YAML persistence ───────────────────────────────────────────────


def test_load_rules_returns_defaults_when_file_missing(tmp_path: Path):
    path = tmp_path / "permissions.yaml"
    rules = load_rules(path)
    # Default rules include the destructive deny entry.
    assert any(
        r.permission == Permission.DELETE_FILE.value
        and r.action == PermissionAction.DENY
        for r in rules
    )


def test_save_and_load_roundtrip(tmp_path: Path):
    path = tmp_path / "permissions.yaml"
    save_rule(PermissionRule(
        permission=Permission.WRITE_FILE.value,
        pattern="strategies/*",
        action=PermissionAction.ALLOW,
        comment="auto: user picked always-allow",
    ), path=path)
    loaded = load_rules(path)
    assert any(
        r.permission == Permission.WRITE_FILE.value
        and r.pattern == "strategies/*"
        and r.action == PermissionAction.ALLOW
        for r in loaded
    )


def test_save_rule_idempotent_for_duplicate_tail(tmp_path: Path):
    """Saving the same rule twice does NOT produce two appends."""
    path = tmp_path / "permissions.yaml"
    rule = PermissionRule(
        permission=Permission.WEB_FETCH.value,
        pattern="*",
        action=PermissionAction.ALLOW,
    )
    save_rule(rule, path=path)
    save_rule(rule, path=path)
    loaded = load_rules(path)
    matching = [
        r for r in loaded
        if r.permission == Permission.WEB_FETCH.value
        and r.pattern == "*"
        and r.action == PermissionAction.ALLOW
    ]
    assert len(matching) == 1


def test_load_rules_falls_back_to_defaults_on_garbage_yaml(tmp_path: Path):
    path = tmp_path / "permissions.yaml"
    path.write_text("not: valid: yaml: :::\n  - broken", encoding="utf-8")
    rules = load_rules(path)
    # Falls back to defaults — caller gets the safe baseline.
    assert any(r.permission == Permission.DELETE_FILE.value for r in rules)


def test_default_rules_path_resolves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """``DEFAULT_RULES_PATH`` resolves ``~/.agentnodes/permissions.yaml``
    by default and respects the ``AGENTNODES_PERMISSIONS_PATH``
    env override at module-import time."""
    # The module-level DEFAULT_RULES_PATH is bound at import time, so
    # the env override takes effect only when the module is reloaded.
    # We verify the *contract* by importing fresh with the env set.
    monkeypatch.setenv(
        "AGENTNODES_PERMISSIONS_PATH",
        str(tmp_path / "perm.yaml"),
    )
    import importlib

    import agentnodes.kernel.permission.rules_io as ri
    importlib.reload(ri)
    assert str(ri.DEFAULT_RULES_PATH).endswith("perm.yaml")
