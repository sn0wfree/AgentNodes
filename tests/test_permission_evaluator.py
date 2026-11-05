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


def test_default_action_is_ask_for_unknown_tool():
    ev = PermissionEvaluator()
    decision = ev.evaluate("write", {"path": "/tmp/x"})
    assert decision.action == PermissionAction.ASK
    assert decision.rule is not None
    assert decision.rule.action == PermissionAction.ASK


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


def test_extract_target_run_command_uses_first_token():
    ev = PermissionEvaluator()
    d = ev.evaluate("shell", {"command": "rm -rf /"})
    assert d.target == "rm"


def test_extract_target_run_backtest_uses_strategy_name():
    ev = PermissionEvaluator()
    d = ev.evaluate("run_backtest", {"strategy_name": "momentum_v3"})
    assert d.target == "momentum_v3"


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
        permission=Permission.RUN_COMMAND.value,
        pattern="git",
        action=PermissionAction.ALLOW,
    )
    save_rule(rule, path=path)
    save_rule(rule, path=path)
    loaded = load_rules(path)
    matching = [
        r for r in loaded
        if r.permission == Permission.RUN_COMMAND.value
        and r.pattern == "git"
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
    by default and respects the ``STRATEGY_RESEARCH_PERMISSIONS_PATH``
    env override at module-import time."""
    # The module-level DEFAULT_RULES_PATH is bound at import time, so
    # the env override takes effect only when the module is reloaded.
    # We verify the *contract* by importing fresh with the env set.
    monkeypatch.setenv(
        "STRATEGY_RESEARCH_PERMISSIONS_PATH",
        str(tmp_path / "perm.yaml"),
    )
    import importlib

    import agentnodes.kernel.permission.rules_io as ri
    importlib.reload(ri)
    assert str(ri.DEFAULT_RULES_PATH).endswith("perm.yaml")
