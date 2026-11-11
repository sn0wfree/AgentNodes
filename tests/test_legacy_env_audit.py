"""批 E · 旧名环境变量审计（只读，零行为变更）。

设计：docs/design/legacy-env-audit-command-20261110.md §5。

核心不变量：**审计命令不改变任何东西**——不改环境变量、不动 llm.json。
它只是"看一眼这台机器上还有哪些旧名在用"。

**批 E-2 之后的语义变化**：旧名兼容层已删除，所以审计报告要区分两类：

- **已失效**（``honored=False``）：值被**静默忽略**——读取层只认规范名，
  设了旧名等于没设。必须改名。
- **仍生效**（``honored=True``）：:data:`settings.LEGACY_LIVE_ENVS` 里的
  那几个——它们指向"用户数据在哪"，过渡期保留，但已废弃。

清单来源也从"读取层的 alias 索引"换成了"诊断层的 ``_LEGACY_CATALOG``"
——兼容层删了，审计照样能跑。
"""
from __future__ import annotations

import json

import pytest

from agentnodes.foundation import settings as S


# ── settings 层：legacy_usage ───────────────────────────────


def test_no_legacy_names_reports_empty():
    assert S.legacy_usage({}) == []


def test_legacy_name_is_detected_and_mapped():
    usages = S.legacy_usage({"AN_WORKSPACE_PATH": "/data/proj"})
    assert len(usages) == 1
    u = usages[0]
    assert u.old_name == "AN_WORKSPACE_PATH"
    assert u.canonical == "AGENTNODES_WORKSPACE"
    assert u.value == "/data/proj"


def test_canonical_name_is_not_reported_as_legacy():
    """canonical 本身不是旧名——不能误报（否则每次审计都"有旧名"）。"""
    assert S.legacy_usage({"AGENTNODES_WORKSPACE": "/data/proj"}) == []


def test_external_vars_are_not_reported():
    """外部变量（HOME / NO_COLOR …）不归本表管，不能报成旧名。"""
    env = {name: "x" for name in S.EXTERNAL_ENV_VARS}
    assert S.legacy_usage(env) == []


def test_risk_grading():
    """风险分档：高危 = 路径/密钥/安全开关。"""
    assert S.legacy_risk("AN_WORKSPACE_PATH") == "high"
    assert S.legacy_risk("AN_ADMIN_PASSWORD") == "high"
    assert S.legacy_risk("JWT_SECRET") == "high"
    assert S.legacy_risk("AN_CHAT_ATTEMPT_TIMEOUT") == "medium"
    assert S.legacy_risk("AN_CACHE_TTL") == "low"


def test_usage_sorted_by_risk_then_name():
    env = {
        "AN_CACHE_TTL": "60",
        "AN_WORKSPACE_PATH": "/w",
        "AN_CHAT_ATTEMPT_TIMEOUT": "600",
    }
    usages = S.legacy_usage(env)
    assert [u.old_name for u in usages] == [
        "AN_WORKSPACE_PATH",          # high
        "AN_CHAT_ATTEMPT_TIMEOUT",    # medium
        "AN_CACHE_TTL",               # low
    ]


def test_secret_values_are_masked_in_summary():
    """密钥类 canonical 的值必须脱敏——审计输出会进 CI 日志。"""
    summary = S.legacy_usage_summary({"AN_ADMIN_PASSWORD": "super-secret-pw"})
    item = summary["items"][0]
    assert item["canonical"] == "AGENTNODES_ADMIN_PASSWORD"
    assert "super-secret-pw" not in json.dumps(summary)
    assert item["value"] != "super-secret-pw"


def test_summary_counts_by_risk():
    summary = S.legacy_usage_summary({
        "AN_WORKSPACE_PATH": "/w",
        "AN_CHAT_ATTEMPT_TIMEOUT": "600",
        "AN_CACHE_TTL": "60",
    })
    assert summary["total"] == 3
    assert summary["counts"] == {"high": 1, "medium": 1, "low": 1}
    assert len(summary["items"]) == 3
    assert "high" in summary["notes"]


def test_audit_does_not_mutate_environ():
    """**零行为变更**：审计前后环境完全一致。"""
    import os

    before = dict(os.environ)
    S.legacy_usage()
    S.legacy_usage_summary()
    assert dict(os.environ) == before


def test_audit_does_not_emit_alias_warnings():
    """审计不该产生 settings 的弃用告警噪声。"""
    import logging

    S.reset_warnings()
    with caplog_at("agentnodes.foundation.settings") as cap:
        S.legacy_usage({"AN_WORKSPACE_PATH": "/w"})
    assert not [r for r in cap.records if "deprecated" in r.getMessage()]


# ── 批 E-2：兼容层已删除，审计清单仍在 ────────────────────────


def test_legacy_catalog_covers_every_old_name():
    """诊断清单必须完整（批 E-3 后 49 组 / 54 个旧名）。"""
    assert len(S._LEGACY_CATALOG) == 54
    assert len(S._LEGACY_NAMES) == 49
    for canonical, olds in S._LEGACY_NAMES.items():
        assert canonical in S.ENV_TABLE, canonical
        for old in olds:
            assert S._LEGACY_CATALOG[old] == canonical


def test_legacy_names_never_collide_with_canonical():
    """旧名不得同时是规范名——否则兼容层被偷偷加回来且无告警。"""
    assert not (set(S._LEGACY_CATALOG) & set(S.ENV_TABLE))


def test_audit_still_works_after_compat_layer_removed():
    """**审计命令不能随兼容层一起死**——它是删兼容层的安全网。"""
    usages = S.legacy_usage({"AN_ADMIN_TOKEN": "tok"})
    assert len(usages) == 1
    assert usages[0].canonical == "AGENTNODES_ADMIN_TOKEN"


# ── 批 E-2：读取层只认规范名 ─────────────────────────────────


def test_old_name_is_not_read_by_the_settings_layer(monkeypatch):
    """**硬切换**：旧名设了也不生效——读取层只认规范名。"""
    monkeypatch.setenv("AN_WORKSPACE_PATH", "/data/proj")
    monkeypatch.delenv("AGENTNODES_WORKSPACE", raising=False)
    assert S.get_str("AGENTNODES_WORKSPACE") is None


def test_canonical_wins_over_old_name(monkeypatch):
    monkeypatch.setenv("AN_WORKSPACE_PATH", "/old")
    monkeypatch.setenv("AGENTNODES_WORKSPACE", "/new")
    assert S.get_str("AGENTNODES_WORKSPACE") == "/new"


def test_reading_canonical_warns_when_old_name_lurks(monkeypatch):
    """**不留静默失效**：环境里还留着旧名时，读规范名要明确喊出来。"""
    monkeypatch.setenv("AN_WORKSPACE_PATH", "/data/proj")
    monkeypatch.delenv("AGENTNODES_WORKSPACE", raising=False)
    S.reset_warnings()
    with caplog_at("agentnodes.foundation.settings") as cap:
        S.get_str("AGENTNODES_WORKSPACE")
    msgs = [r.getMessage() for r in cap.records]
    assert any("AN_WORKSPACE_PATH" in m for m in msgs), msgs
    assert any("no longer read" in m for m in msgs), msgs


def test_live_legacy_env_is_still_honored(monkeypatch):
    """``LEGACY_LIVE_ENVS`` 里的旧名仍生效（指向用户数据在哪）。"""
    monkeypatch.setenv("SR_WORKSPACE_PATH", "/legacy/ws")
    assert S.get_legacy("SR_WORKSPACE_PATH") == "/legacy/ws"


def test_get_legacy_rejects_names_that_are_not_live():
    """只有显式登记的"仍然生效"旧名能走这个入口——防止兼容层回流。"""
    with pytest.raises(KeyError):
        S.get_legacy("AN_WORKSPACE_PATH")


def test_live_legacy_env_is_flagged_as_honored():
    usages = S.legacy_usage({"SR_WORKSPACE_PATH": "/legacy/ws"})
    assert len(usages) == 1
    assert usages[0].honored is True
    ignored = S.legacy_usage({"AN_WORKSPACE_PATH": "/w"})
    assert ignored[0].honored is False


def test_summary_separates_honored_from_ignored():
    summary = S.legacy_usage_summary({
        "SR_WORKSPACE_PATH": "/legacy/ws",
        "AN_WORKSPACE_PATH": "/w",
    })
    assert summary["total"] == 2
    assert summary["honored"] == 1
    assert summary["ignored"] == 1


class caplog_at:
    """最小 caplog 上下文（只抓指定 logger）。"""

    def __init__(self, logger_name: str) -> None:
        self._name = logger_name
        self.records: list = []

    def __enter__(self):
        import logging

        self._logger = logging.getLogger(self._name)
        self._old_level = self._logger.level
        self._logger.setLevel(logging.WARNING)
        handler = logging.Handler()
        handler.emit = self.records.append  # type: ignore[method-assign]
        self._handler = handler
        self._logger.addHandler(handler)
        return self

    def __exit__(self, *exc):
        self._logger.removeHandler(self._handler)
        self._logger.setLevel(self._old_level)
        return False


# ── CLI 层：config audit-legacy ─────────────────────────────


def _run_cli(monkeypatch, env: dict, argv: list[str]) -> tuple[int, str]:
    """跑 CLI 的 config audit-legacy（monkeypatch 环境），返回 (退出码, 输出)。"""
    import argparse

    from agentnodes.interfaces.cli.commands import config_cmd

    for k in list(S.ENV_TABLE):
        monkeypatch.delenv(k, raising=False)
    for old in S._LEGACY_CATALOG:
        monkeypatch.delenv(old, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    args = argparse.Namespace(action="audit-legacy", json=False)
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = config_cmd.cmd_config(args)
    return code, buf.getvalue()


def test_cli_exit_0_when_no_legacy(monkeypatch):
    code, out = _run_cli(monkeypatch, {}, ["audit-legacy"])
    assert code == 0
    assert "未发现旧名环境变量" in out


def test_cli_exit_1_when_legacy_present(monkeypatch):
    code, out = _run_cli(
        monkeypatch, {"AN_WORKSPACE_PATH": "/data/proj"}, ["audit-legacy"])
    assert code == 1
    assert "AN_WORKSPACE_PATH" in out
    assert "AGENTNODES_WORKSPACE" in out
    assert "高危" in out


def test_cli_lists_all_sections(monkeypatch):
    _code, out = _run_cli(
        monkeypatch, {"AN_WORKSPACE_PATH": "/w"}, ["audit-legacy"])
    assert "=== 1." in out
    assert "=== 2." in out
    assert "=== 3." in out
    assert "=== 结论 ===" in out


def test_cli_marks_ignored_legacy_as_failed(monkeypatch):
    """已失效的旧名必须标出来——它设了但不生效。"""
    _code, out = _run_cli(
        monkeypatch, {"AN_WORKSPACE_PATH": "/w"}, ["audit-legacy"])
    assert "已失效" in out
    assert "必须改名" in out


def test_cli_marks_live_legacy_as_deprecated(monkeypatch):
    """仍生效的旧名标成"已废弃"，不是"已失效"。"""
    _code, out = _run_cli(
        monkeypatch, {"SR_WORKSPACE_PATH": "/w"}, ["audit-legacy"])
    assert "仍生效" in out
    assert "已废弃" in out


def test_cli_masks_secret_values(monkeypatch):
    _code, out = _run_cli(
        monkeypatch, {"AN_ADMIN_PASSWORD": "top-secret-value"}, ["audit-legacy"])
    assert "top-secret-value" not in out


def test_cli_json_output(monkeypatch):
    """--json 输出可被解析，字段齐全。"""
    import argparse
    import io
    import contextlib

    from agentnodes.interfaces.cli.commands import config_cmd

    for k in list(S.ENV_TABLE):
        monkeypatch.delenv(k, raising=False)
    for old in S._LEGACY_CATALOG:
        monkeypatch.delenv(old, raising=False)
    monkeypatch.setenv("AN_WORKSPACE_PATH", "/data/proj")

    args = argparse.Namespace(action="audit-legacy", json=True)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = config_cmd.cmd_config(args)
    payload = json.loads(buf.getvalue())
    assert code == 1
    assert payload["total"] == 1
    assert payload["items"][0]["old_name"] == "AN_WORKSPACE_PATH"
    assert payload["items"][0]["risk"] == "high"
    assert payload["counts"]["high"] == 1
    assert payload["ignored"] == 1


def test_cli_audit_is_read_only(monkeypatch):
    """跑完命令，环境**不新增任何键**（审计只读）。

    **不断言整个 env 逐字节相等**：同文件的 session 级 autouse fixture
    （``_default_session_workspace``）已 setenv ``AGENTNODES_WORKSPACE``，
    ``_run_cli`` 的 monkeypatch 也会 setenv 它——两者都在断言时仍生效
    （teardown 才 undo）。所以先把 monkeypatch 的 setenv 做掉、拍下
    快照，再断言"审计调用本身不新增键"。
    """
    import os

    monkeypatch.delenv("AN_ADMIN_PASSWORD", raising=False)
    # 先让 _run_cli 的 setenv 生效（/w），再拍快照
    _run_cli(monkeypatch, {"AN_WORKSPACE_PATH": "/w"}, ["audit-legacy"])
    before = set(os.environ)
    # 再跑一次审计（此时 monkeypatch 的 setenv 已生效且稳定）
    _run_cli(monkeypatch, {"AN_WORKSPACE_PATH": "/w"}, ["audit-legacy"])
    added = set(os.environ) - before
    assert not added, f"audit added env keys: {sorted(added)}"


def test_cli_registered_in_builtin_commands():
    """命令已注册进内置命令表（否则用户敲不到）。"""
    from agentnodes.interfaces.cli.commands.registry import (
        ensure_builtin_commands,
        registered_commands,
    )

    ensure_builtin_commands()
    assert "config" in registered_commands()
