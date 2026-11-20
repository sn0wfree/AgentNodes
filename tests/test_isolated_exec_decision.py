# -*- coding: utf-8 -*-
"""B2.4 子批 4a：隔离接线决策探针测试（全平台无 skipif）。

开关默认关——关闭时现有 E5 与工具测试结果逐字不变（本文件用决策
纯函数 + loop 影子事件断言，不改任何执行分支）。生产路径 sync/async/
retry 共用 should_isolate 单一决策输入（方案 §4「不得保留部分接线」）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentnodes.kernel.agent.loop._isolated_exec import (
    ISOLATION_EXECUTION_ENV,
    ISOLATION_TOOLS_ENV,
    isolation_execution_enabled,
    isolation_tool_allowlist,
    shadow_event,
    should_isolate,
    terminal_event,
    validate_isolation_config,
)
from agentnodes.kernel.agent.tools.core import (
    TIMEOUT_CAPABILITY_ABANDONABLE,
    TIMEOUT_CAPABILITY_ISOLATABLE,
    TIMEOUT_CAPABILITY_UNSTOPPABLE,
)


def _tool(name="t1", capability=TIMEOUT_CAPABILITY_UNSTOPPABLE):
    return SimpleNamespace(name=name, timeout_capability=capability)


class TestSwitch:
    def test_default_is_off(self, monkeypatch):
        monkeypatch.delenv(ISOLATION_EXECUTION_ENV, raising=False)
        assert isolation_execution_enabled() is False

    @pytest.mark.parametrize("value", ["0", "", "false", "no", "off", "junk"])
    def test_off_values(self, monkeypatch, value):
        monkeypatch.setenv(ISOLATION_EXECUTION_ENV, value)
        assert isolation_execution_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
    def test_on_values(self, monkeypatch, value):
        monkeypatch.setenv(ISOLATION_EXECUTION_ENV, value)
        assert isolation_execution_enabled() is True


@pytest.fixture()
def isolation_on(monkeypatch):
    """Q10：以系统状态（settings/env）替换旧 enabled/allowlist 测试旋钮。"""
    monkeypatch.setenv(ISOLATION_EXECUTION_ENV, "1")
    monkeypatch.setenv(ISOLATION_TOOLS_ENV, "write,read")


class TestShouldIsolate:
    def test_disabled_short_circuits_everything(self, monkeypatch):
        """开关关时任何工具都不隔离（含 isolatable + win32）。"""
        monkeypatch.delenv(ISOLATION_EXECUTION_ENV, raising=False)
        d = should_isolate(
            _tool(capability=TIMEOUT_CAPABILITY_ISOLATABLE), platform="win32",
        )
        assert d.isolate is False
        assert d.reason == "disabled"

    def test_non_windows_never_isolates(self, isolation_on):
        d = should_isolate(
            _tool(capability=TIMEOUT_CAPABILITY_ISOLATABLE), platform="linux",
        )
        assert d.isolate is False
        assert d.reason == "unsupported_platform"

    def test_capability_gate(self, isolation_on):
        for cap in (TIMEOUT_CAPABILITY_UNSTOPPABLE, TIMEOUT_CAPABILITY_ABANDONABLE):
            d = should_isolate(_tool(capability=cap), platform="win32")
            assert d.isolate is False
            assert d.reason == "capability"
            assert d.capability == cap

    def test_isolatable_allowed_on_windows(self, isolation_on):
        d = should_isolate(
            _tool("write", TIMEOUT_CAPABILITY_ISOLATABLE), platform="win32",
        )
        assert d.isolate is True
        assert d.reason == "isolatable"
        assert d.tool_name == "write"

    def test_not_in_allowlist_rejected(self, monkeypatch):
        """4d：allowlist 空 → 即使开关开也不启用（灰度）。"""
        monkeypatch.setenv(ISOLATION_EXECUTION_ENV, "1")
        monkeypatch.setenv(ISOLATION_TOOLS_ENV, "")  # 空 allowlist
        d = should_isolate(
            _tool("write", TIMEOUT_CAPABILITY_ISOLATABLE), platform="win32",
        )
        assert d.isolate is False
        assert d.reason == "not_allowlisted"

    def test_registry_timeout_capability_used_for_admission(self, isolation_on):
        """生产必须传 registry——准入门槛折叠在 timeout_capability 里。

        声明 isolatable 但 registry 回落 unstoppable（DB/NET effects）→
        不隔离，reason=admission（区别于 capability）。
        """
        tool = _tool("risky", TIMEOUT_CAPABILITY_ISOLATABLE)
        registry = SimpleNamespace(
            timeout_capability=lambda name: TIMEOUT_CAPABILITY_UNSTOPPABLE,
        )
        d = should_isolate(tool, registry=registry, platform="win32")
        assert d.isolate is False
        assert d.reason == "admission"
        assert d.capability == TIMEOUT_CAPABILITY_UNSTOPPABLE


class TestShadowEvent:
    def test_shadow_event_shape(self, isolation_on):
        d = should_isolate(
            _tool("write", TIMEOUT_CAPABILITY_ISOLATABLE), platform="win32",
        )
        from agentnodes.kernel.events.event_v2 import (
            EventType,
            is_known_event_type,
        )

        ev = shadow_event(d)
        assert ev["type"] == EventType.ISOLATION_SHADOW_DECISION
        assert is_known_event_type(ev["type"]) is True  # Q11 注册表覆盖
        assert ev["tool"] == "write"
        assert ev["isolate"] is True
        assert ev["reason"] == "isolatable"
        assert ev["capability"] == TIMEOUT_CAPABILITY_ISOLATABLE

    def test_terminal_event_registered_and_shaped(self):
        """Q11：终态事件进注册表，命名法与 shadow 统一（type 键）。"""
        from agentnodes.kernel.agent.loop._isolated_exec import IsolatedOutcome
        from agentnodes.kernel.events.event_v2 import (
            EventType,
            is_known_event_type,
        )

        outcome = IsolatedOutcome(
            "{}", "stopped", "terminated", "call-1", 0, True,
        )
        ev = terminal_event(outcome)
        assert ev["type"] == EventType.ISOLATION_TERMINAL
        assert is_known_event_type(ev["type"]) is True
        assert ev["call_id"] == "call-1"
        assert ev["actual_state"] == "stopped"


class TestGrayRelease:
    """4d：灰度 allowlist + 启动期平台校验 + 单一决策输入。"""

    def test_allowlist_default_empty(self, monkeypatch):
        monkeypatch.delenv(ISOLATION_TOOLS_ENV, raising=False)
        assert isolation_tool_allowlist() == frozenset()

    def test_allowlist_parses_comma_separated(self, monkeypatch):
        monkeypatch.setenv(ISOLATION_TOOLS_ENV, "write, read ,")
        assert isolation_tool_allowlist() == frozenset({"write", "read"})

    def test_validate_disabled_always_passes(self, monkeypatch):
        monkeypatch.delenv(ISOLATION_EXECUTION_ENV, raising=False)
        validate_isolation_config()  # 不抛

    def test_validate_forced_on_posix_raises(self, monkeypatch):
        monkeypatch.setenv(ISOLATION_EXECUTION_ENV, "1")
        # 用平台参数无法直接注入 validate（它读 sys.platform）；在 Windows
        # 上此测试应通过（不抛），在 POSIX CI 上应抛——两种都对，故断言
        # 「 Windows 上不抛」并按平台分支。
        import sys

        if sys.platform == "win32":
            validate_isolation_config()  # win32 允许
        else:
            with pytest.raises(RuntimeError, match="Windows-only"):
                validate_isolation_config()

    def test_should_isolate_is_single_decision_input(self, monkeypatch):
        """不得保留部分接线：开关关时 sync/async 都走 in-process（同一决策）。"""
        monkeypatch.delenv(ISOLATION_EXECUTION_ENV, raising=False)
        monkeypatch.delenv(ISOLATION_EXECUTION_ENV, raising=False)
        tool = _tool("write", TIMEOUT_CAPABILITY_ISOLATABLE)
        registry = SimpleNamespace(
            timeout_capability=lambda n: TIMEOUT_CAPABILITY_ISOLATABLE,
        )
        # 开关关 → 无论 allowlist env 是否放行，都不隔离（开关是唯一输入）
        monkeypatch.setenv(ISOLATION_TOOLS_ENV, "write")
        d1 = should_isolate(tool, registry, platform="win32")
        monkeypatch.setenv(ISOLATION_TOOLS_ENV, "")
        d2 = should_isolate(tool, registry, platform="win32")
        assert d1.isolate is False and d2.isolate is False
        assert d1.reason == d2.reason == "disabled"
