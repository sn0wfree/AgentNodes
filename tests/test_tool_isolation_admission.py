# -*- coding: utf-8 -*-
"""4c-B2.1：isolatable 声明准入门槛的契约测试（纯声明面，零执行）。"""
from __future__ import annotations

from agentnodes.kernel.agent.tools.core import (
    TIMEOUT_CAPABILITY_ISOLATABLE,
    TIMEOUT_CAPABILITY_UNSTOPPABLE,
    BaseTool,
    ToolRegistry,
    check_isolation_admission,
)


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
