"""批次 6 · 权限加固测试（hard_deny 层 / L0-L4 分级 / P2 双匹配）。

对应设计：docs/design/batch6-permission-hardening-20261107.md
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agentnodes.kernel.permission.evaluator import PermissionEvaluator
from agentnodes.kernel.permission.rules_io import load_rules, save_rule
from agentnodes.kernel.permission.schema import (
    DEFAULT_HARD_DENY,
    MODE_PRESETS,
    HardDenyRule,
    Permission,
    PermissionAction,
    PermissionMode,
    PermissionRule,
)


# ── P1 · hard_deny 层 ─────────────────────────────────────────────────


class TestHardDeny:
    def test_p1_permanent_allow_cannot_override_hard_deny(self):
        """P1 回归：用户「永远允许」(追加 allow 规则) 不能盖掉 hard_deny。

        场景：write 到 .agentnodes/permissions.yaml 是 H1；
        即使用户先允许过一次（save_rule 追加 allow）→ 仍 DENY。
        """
        user_allow = PermissionRule(
            permission="write", pattern="*", action=PermissionAction.ALLOW,
        )
        ev = PermissionEvaluator(ruleset=[user_allow])
        d = ev.evaluate("write", {"path": ".agentnodes/permissions.yaml"})
        assert d.action == PermissionAction.DENY
        assert d.hard is True
        assert d.reason == "self-configuration is not permitted"

    def test_h1_self_config_paths(self):
        ev = PermissionEvaluator()
        for tool, path in [
            ("write", ".agentnodes/permissions.yaml"),
            ("read", ".agentnodes/permissions.yaml"),
            ("edit", ".claude/settings.json"),
            ("delete_file", ".git/hooks/pre-commit"),
            ("write", ".mcp.json"),
        ]:
            d = ev.evaluate(tool, {"path": path})
            assert d.action == PermissionAction.DENY, f"{tool} {path}"
            assert d.hard is True

    def test_h2_privilege_escalation(self):
        ev = PermissionEvaluator()
        for cmd in ("sudo rm -rf /", "su root", "doas x", "pkexec y"):
            d = ev.evaluate("shell", {"command": cmd})
            assert d.action == PermissionAction.DENY, cmd
            assert d.hard is True

    def test_h2_even_in_L2_shell_allow(self):
        """L2 下 shell 自动放行，但提权仍拦（分级不可放松 hard_deny）。"""
        ev = PermissionEvaluator(
            ruleset=list(MODE_PRESETS[PermissionMode.L2_SANDBOX_EXEC.value])
        )
        d = ev.evaluate("shell", {"command": "sudo ls"})
        assert d.action == PermissionAction.DENY
        assert d.hard is True

    def test_h3_credentials_and_metadata(self):
        ev = PermissionEvaluator()
        for tool, args in [
            ("read", {"path": ".ssh/id_rsa"}),
            ("read", {"path": "home/user/.aws/credentials"}),
            ("write", {"path": "configs/.env"}),
            ("web_fetch", {"url": "http://169.254.169.254/latest/meta-data/"}),
        ]:
            d = ev.evaluate(tool, args)
            assert d.action == PermissionAction.DENY, f"{tool} {args}"
            assert d.hard is True

    def test_hard_deny_not_disabled_by_empty_ruleset(self):
        """ruleset 为空也拦——hard 层独立于 ruleset。"""
        ev = PermissionEvaluator(ruleset=[])
        d = ev.evaluate("shell", {"command": "sudo x"})
        assert d.action == PermissionAction.DENY

    def test_custom_hard_deny_replaces_default(self):
        """显式传 hard_deny 覆盖默认（测试/受控环境用）。"""
        ev = PermissionEvaluator(
            ruleset=[],
            hard_deny=[HardDenyRule("shell", "forbidden*", "test")],
        )
        assert ev.evaluate("shell", {"command": "forbidden x"}).action == (
            PermissionAction.DENY
        )
        # 默认的 sudo 不再拦（已替换）
        assert ev.evaluate("shell", {"command": "sudo x"}).action == (
            PermissionAction.ASK
        )


# ── P2 · 双匹配 ───────────────────────────────────────────────────────


class TestDualMatching:
    def test_deny_hits_path_variants(self):
        """deny secret/** 时，路径变体不放过。"""
        ev = PermissionEvaluator(ruleset=[
            PermissionRule(
                permission="read", pattern="secret/**",
                action=PermissionAction.DENY,
            ),
        ])
        for variant in (
            "secret/x",
            "./secret/x",
            "a/../secret/x",
            "secret\\x",
        ):
            d = ev.evaluate("read", {"path": variant})
            assert d.action == PermissionAction.DENY, variant

    def test_allow_needs_both_forms(self):
        """allow 需两形态都命中——规范化后越界的形态不误放行。"""
        ev = PermissionEvaluator(ruleset=[
            PermissionRule(
                permission="read", pattern="src/**",
                action=PermissionAction.ALLOW,
            ),
        ])
        assert ev.evaluate("read", {"path": "src/a.py"}).action == (
            PermissionAction.ALLOW
        )
        # ./src/a.py 规范化后相同 → 仍 allow
        assert ev.evaluate("read", {"path": "./src/a.py"}).action == (
            PermissionAction.ALLOW
        )
        # 规范化后不在 src/ 的形态 → 无规则命中 → R0 只读自动放行
        # （read 的默认语义；deny 规则的变体拦截在上一条用例已验证）
        d = ev.evaluate("read", {"path": "other/../secret/a.py"})
        assert d.action == PermissionAction.ALLOW
        assert d.rule is None


# ── L0-L4 模式矩阵 ────────────────────────────────────────────────────


class TestModeMatrix:
    CASES: list[tuple[str, str, dict, str]] = [
        # (mode, tool, args, expected)
        ("L0_readonly", "write", {"path": "a.py"}, "deny"),
        ("L0_readonly", "shell", {}, "deny"),
        ("L0_readonly", "read", {"path": "a.py"}, "allow"),
        ("L1_workspace_edit", "write", {"path": "a.py"}, "allow"),
        ("L1_workspace_edit", "edit", {"path": "a.py"}, "allow"),
        ("L1_workspace_edit", "shell", {}, "ask"),
        ("L1_workspace_edit", "web_fetch", {"url": "https://x"}, "ask"),
        ("L1_workspace_edit", "delete_file", {"path": "a"}, "deny"),
        ("L2_sandbox_exec", "shell", {}, "allow"),
        ("L2_sandbox_exec", "web_fetch", {"url": "https://x"}, "ask"),
        ("L3_networked", "shell", {}, "allow"),
        ("L3_networked", "web_fetch", {"url": "https://x"}, "allow"),
        ("L3_networked", "delete_file", {"path": "a"}, "deny"),
        ("L4_confirm_all", "write", {"path": "a.py"}, "ask"),
        ("L4_confirm_all", "shell", {}, "ask"),
        ("L4_confirm_all", "delete_file", {"path": "a"}, "ask"),
        ("L4_confirm_all", "read", {"path": "a.py"}, "allow"),
    ]

    @pytest.mark.parametrize("mode,tool,args,expected", CASES)
    def test_mode_matrix(self, mode, tool, args, expected):
        ev = PermissionEvaluator(ruleset=list(MODE_PRESETS[mode]))
        assert ev.evaluate(tool, args).action.value == expected, (
            f"{mode}/{tool}"
        )

    def test_mode_enum_roundtrip(self):
        assert PermissionMode.from_value("L1_workspace_edit") == (
            PermissionMode.L1_WORKSPACE_EDIT
        )
        with pytest.raises(ValueError):
            PermissionMode.from_value("L9_yolo")

    def test_default_mode_is_L1(self):
        assert PermissionMode.default() == PermissionMode.L1_WORKSPACE_EDIT
        assert PermissionMode.default().value in MODE_PRESETS


# ── P3 · save_rule 校验 ───────────────────────────────────────────────


class TestSaveRuleValidation:
    def test_shell_allow_with_pattern_rejected(self, tmp_path: Path):
        rule = PermissionRule(
            permission=Permission.RUN_COMMAND.value,
            pattern="git *",
            action=PermissionAction.ALLOW,
        )
        with pytest.raises(ValueError, match="pattern"):
            save_rule(rule, path=tmp_path / "p.yaml")

    def test_shell_allow_wildcard_ok(self, tmp_path: Path):
        rule = PermissionRule(
            permission=Permission.RUN_COMMAND.value,
            pattern="*",
            action=PermissionAction.ALLOW,
        )
        save_rule(rule, path=tmp_path / "p.yaml")
        assert load_rules(tmp_path / "p.yaml")[-1].pattern == "*"

    def test_shell_deny_with_pattern_ok(self, tmp_path: Path):
        """deny 的 pattern 化规则不受限——deny 语义保守，不制造错觉。"""
        rule = PermissionRule(
            permission=Permission.RUN_COMMAND.value,
            pattern="curl*",
            action=PermissionAction.DENY,
        )
        save_rule(rule, path=tmp_path / "p.yaml")
        assert load_rules(tmp_path / "p.yaml")[-1].pattern == "curl*"


# ── 非交互 + 审计 ─────────────────────────────────────────────────────


class TestNonInteractiveAndAudit:
    def test_non_interactive_ask_becomes_deny(self):
        ev = PermissionEvaluator(interactive=False)
        # shell 在 L1 默认 ask → 非交互降级 deny
        assert ev.evaluate("shell", {}).action == PermissionAction.DENY
        # 未建模工具兜底 ask → deny
        assert ev.evaluate("custom", {}).action == PermissionAction.DENY
        # allow 规则不受影响
        assert ev.evaluate("write", {"path": "a.py"}).action == (
            PermissionAction.ALLOW
        )

    def test_audit_callback_receives_decisions(self):
        records: list[dict] = []
        ev = PermissionEvaluator(on_decision=records.append)
        ev.evaluate("shell", {"command": "sudo x"})       # hard deny
        ev.evaluate("write", {"path": "a.py"})            # rule allow
        assert len(records) == 2
        hard = records[0]
        assert hard["hard"] is True
        assert hard["action"] == "deny"
        rule_hit = records[1]
        assert rule_hit["hard"] is False
        assert rule_hit["rule"] == "write:*"

    def test_audit_failure_does_not_break_decision(self):
        def boom(_d):
            raise RuntimeError("audit down")

        ev = PermissionEvaluator(on_decision=boom)
        # 审计崩了判定照常
        assert ev.evaluate("shell", {"command": "sudo x"}).action == (
            PermissionAction.DENY
        )
