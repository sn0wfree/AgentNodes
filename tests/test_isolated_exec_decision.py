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
    isolation_execution_enabled,
    shadow_event,
    should_isolate,
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


class TestShouldIsolate:
    def test_disabled_short_circuits_everything(self, monkeypatch):
        """开关关时任何工具都不隔离（含 isolatable + win32）。"""
        monkeypatch.delenv(ISOLATION_EXECUTION_ENV, raising=False)
        d = should_isolate(
            _tool(capability=TIMEOUT_CAPABILITY_ISOLATABLE),
            enabled=False, platform="win32",
        )
        assert d.isolate is False
        assert d.reason == "disabled"

    def test_non_windows_never_isolates(self):
        d = should_isolate(
            _tool(capability=TIMEOUT_CAPABILITY_ISOLATABLE),
            enabled=True, platform="linux",
        )
        assert d.isolate is False
        assert d.reason == "unsupported_platform"

    def test_capability_gate(self):
        for cap in (TIMEOUT_CAPABILITY_UNSTOPPABLE, TIMEOUT_CAPABILITY_ABANDONABLE):
            d = should_isolate(_tool(capability=cap), enabled=True, platform="win32")
            assert d.isolate is False
            assert d.reason == "capability"
            assert d.capability == cap

    def test_isolatable_allowed_on_windows(self):
        d = should_isolate(
            _tool("write", TIMEOUT_CAPABILITY_ISOLATABLE),
            enabled=True, platform="win32",
        )
        assert d.isolate is True
        assert d.reason == "isolatable"
        assert d.tool_name == "write"

    def test_registry_timeout_capability_used_for_admission(self):
        """生产必须传 registry——准入门槛折叠在 timeout_capability 里。

        声明 isolatable 但 registry 回落 unstoppable（DB/NET effects）→
        不隔离，reason=admission（区别于 capability）。
        """
        tool = _tool("risky", TIMEOUT_CAPABILITY_ISOLATABLE)
        registry = SimpleNamespace(
            timeout_capability=lambda name: TIMEOUT_CAPABILITY_UNSTOPPABLE,
        )
        d = should_isolate(
            tool, registry=registry, enabled=True, platform="win32",
        )
        assert d.isolate is False
        assert d.reason == "admission"
        assert d.capability == TIMEOUT_CAPABILITY_UNSTOPPABLE


class TestShadowEvent:
    def test_shadow_event_shape(self):
        d = should_isolate(
            _tool("write", TIMEOUT_CAPABILITY_ISOLATABLE),
            enabled=True, platform="win32",
        )
        ev = shadow_event(d)
        assert ev["kind"] == "isolation.shadow_decision"
        assert ev["tool"] == "write"
        assert ev["isolate"] is True
        assert ev["reason"] == "isolatable"
        assert ev["capability"] == TIMEOUT_CAPABILITY_ISOLATABLE
