# -*- coding: utf-8 -*-
"""4c-B2.1：isolatable 声明准入门槛的契约测试（纯声明面，零执行）。"""
from __future__ import annotations

from agentnodes.kernel.agent.tools.isolation_admission import (
    TIMEOUT_CAPABILITY_ISOLATABLE,
    TIMEOUT_CAPABILITY_UNSTOPPABLE,
    check_isolation_admission,
    effective_capability,
)
from agentnodes.kernel.agent.tools.core import BaseTool, ToolRegistry


class _Tool(BaseTool):
    name = "admission_probe"
    description = "admission probe"
    parameters: dict = {}

    def execute(self, **kwargs):
        return "ok"


def test_isolatable_fs_only_admitted():
    tool = _Tool()
    tool.timeout_capability = TIMEOUT_CAPABILITY_ISOLATABLE
    tool.effects = frozenset({"fs"})
    assert check_isolation_admission(tool) is None


def test_isolatable_readonly_admitted():
    tool = _Tool()
    tool.timeout_capability = TIMEOUT_CAPABILITY_ISOLATABLE
    tool.effects = frozenset()
    assert check_isolation_admission(tool) is None


def test_isolatable_net_rejected():
    """远端操作不能被进程终止撤销——需服务端确认取消（尚未实现）。"""
    tool = _Tool()
    tool.timeout_capability = TIMEOUT_CAPABILITY_ISOLATABLE
    tool.effects = frozenset({"net"})
    reason = check_isolation_admission(tool)
    assert reason and "net" in reason


def test_isolatable_db_rejected():
    tool = _Tool()
    tool.timeout_capability = TIMEOUT_CAPABILITY_ISOLATABLE
    tool.effects = frozenset({"db"})
    reason = check_isolation_admission(tool)
    assert reason and "db" in reason


def test_non_isolatable_not_subject_to_admission():
    """门槛只管 isolatable 声明；其他三级不受影响。"""
    tool = _Tool()  # 默认 unstoppable
    assert check_isolation_admission(tool) is None


class TestEffectiveCapability:
    """Q3/Q6：有效能力收口三件事——未注册/非法值/准入回落。"""

    def test_none_tool_is_unstoppable(self):
        assert effective_capability(None) == TIMEOUT_CAPABILITY_UNSTOPPABLE

    def test_invalid_declaration_falls_back(self):
        tool = _Tool()
        tool.timeout_capability = "bogus"
        assert effective_capability(tool) == TIMEOUT_CAPABILITY_UNSTOPPABLE

    def test_isolatable_admission_failure_falls_back(self):
        tool = _Tool()
        tool.timeout_capability = TIMEOUT_CAPABILITY_ISOLATABLE
        tool.effects = frozenset({"net"})
        assert effective_capability(tool) == TIMEOUT_CAPABILITY_UNSTOPPABLE

    def test_valid_isolatable_preserved(self):
        tool = _Tool()
        tool.timeout_capability = TIMEOUT_CAPABILITY_ISOLATABLE
        tool.effects = frozenset({"fs"})
        assert effective_capability(tool) == TIMEOUT_CAPABILITY_ISOLATABLE


def test_registry_falls_back_for_invalid_isolatable():
    """registry 声明面对不合规的 isolatable 回落 unstoppable（诚实默认）。"""
    tool = _Tool()
    tool.timeout_capability = TIMEOUT_CAPABILITY_ISOLATABLE
    tool.effects = frozenset({"net"})
    registry = ToolRegistry()
    registry.register(tool)
    assert registry.timeout_capability("admission_probe") == TIMEOUT_CAPABILITY_UNSTOPPABLE

    # FS-only 声明保留 isolatable
    tool.effects = frozenset({"fs"})
    assert registry.timeout_capability("admission_probe") == TIMEOUT_CAPABILITY_ISOLATABLE


# ── B2.5 5a：C2/C3 分叉面判定 + C4 allowlist + C5 风险清单 ──────────


class _CtxCallbackTool(BaseTool):
    """函数体读 ctx.emit_event——C3 应拒。"""

    name = "ctx_cb_probe"
    description = "probe"
    parameters: dict = {}
    effects = frozenset({"fs"})
    timeout_capability = TIMEOUT_CAPABILITY_ISOLATABLE

    def execute(self, ctx, **kwargs):
        if ctx.emit_event:
            ctx.emit_event("x", {})
        return "ok"


class _KwargsInjectTool(BaseTool):
    """**kwargs 读 emit_event——C3 应拒（**kwargs 不因未显式声明豁免）。"""

    name = "kwargs_probe"
    description = "probe"
    parameters: dict = {}
    effects = frozenset({"fs"})
    timeout_capability = TIMEOUT_CAPABILITY_ISOLATABLE

    def execute(self, **kwargs):
        cb = kwargs.get("emit_event")
        return "ok" if cb else "no"


class _ExplicitInjectTool(BaseTool):
    """显式声明注入参数——C2 应拒。"""

    name = "explicit_probe"
    description = "probe"
    parameters: dict = {}
    effects = frozenset({"fs"})
    timeout_capability = TIMEOUT_CAPABILITY_ISOLATABLE

    def execute(self, ctx, emit_event=None):
        return "ok"


def test_c3_ctx_callback_rejected():
    reason = check_isolation_admission(_CtxCallbackTool())
    assert reason and "ctx.emit_event" in reason


def test_c3_kwargs_inject_rejected():
    reason = check_isolation_admission(_KwargsInjectTool())
    assert reason and "kwargs.emit_event" in reason


def test_c2_explicit_inject_rejected():
    reason = check_isolation_admission(_ExplicitInjectTool())
    assert reason and "emit_event" in reason


def test_c3_unparsable_source_fail_closed(monkeypatch):
    """源码不可解析 → fail-closed 拒（不静默放行）。"""
    import inspect

    tool = _Tool()
    tool.timeout_capability = TIMEOUT_CAPABILITY_ISOLATABLE
    tool.effects = frozenset({"fs"})
    monkeypatch.setattr(inspect, "getsource", lambda fn: "def broken(:" )
    reason = check_isolation_admission(tool)
    assert reason and "fail-closed" in reason


def test_write_tool_passes_admission():
    """试点入选工具 write 通过 C1–C3（不读回调、不读 kwargs 注入）。"""
    from agentnodes.kernel.agent.builtin_tools import build_default_registry

    registry = build_default_registry()
    write = registry.get("write")
    assert write is not None
    # write 默认 unstoppable——临时按 isolatable 测判定（用完恢复）
    original = write.timeout_capability
    try:
        write.timeout_capability = TIMEOUT_CAPABILITY_ISOLATABLE
        assert check_isolation_admission(write) is None
    finally:
        write.timeout_capability = original


def test_c_risk_inventory_catches_real_tools():
    """风险清单：E3 三类工具（show_chart/task/todowrite）被 C2/C3 抓到。"""
    from agentnodes.kernel.agent.builtin_tools import build_default_registry

    registry = build_default_registry()
    flagged = {}
    original = {t.name: t.timeout_capability for t in registry.all_tools()}
    try:
        for tool in registry.all_tools():
            tool.timeout_capability = TIMEOUT_CAPABILITY_ISOLATABLE
            reason = check_isolation_admission(tool)
            if reason and ("ctx.emit_event" in reason or "kwargs." in reason):
                flagged[tool.name] = reason
    finally:
        for tool in registry.all_tools():
            tool.timeout_capability = original[tool.name]
    assert "show_chart" in flagged  # 函数体读 ctx.emit_event
    assert "task" in flagged          # **kwargs 读 emit_event
    # todowrite 也有 kwargs.emit_event，但它 effects 含 db → C1 先拒（正确顺序），
    # 不因分叉进 worker，故不在 C2/C3 风险清单里——这不是漏检。
    assert "write" not in flagged     # 试点工具无分叉
