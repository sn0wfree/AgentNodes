# -*- coding: utf-8 -*-
"""隔离执行接线 seam（B2.4 子批 4a · 决策探针，零生产影响）。

单一决策函数 :func:`should_isolate` 是生产路径上**唯一**的隔离决策输入；
:data:`ISOLATION_EXECUTION_ENV` 开关默认关闭——关闭时现有 E5 与工具
测试结果逐字不变（B2.4 §3 4a 验收行）。

本批只提供决策探针 + shadow 模式（只计算决策并发事件，不执行隔离），
**不接实际隔离执行**（那是 4c）。约束（方案 §4）：

- 开关是唯一决策输入——sync/async/retry 共用它，禁止某条路径硬编码
  隔离或硬编码绕过（有测试钉住）；
- POSIX 永不启用（``supported()==False`` 的 adapter）——即使开关打开
  也不决策隔离。

设计：docs/design/b24-production-wiring-r014-20261120.md（R-014 已批准 4a）。
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: 生产开关（默认 0=关）。关闭即回到既有 in-process 执行（回滚开关）。
ISOLATION_EXECUTION_ENV = "AGENTNODES_ISOLATION_EXECUTION"

# 决策原因（供 shadow 事件/测试断言，稳定词表）
_REASON_DISABLED = "disabled"
_REASON_PLATFORM = "unsupported_platform"
_REASON_CAPABILITY = "capability"
_REASON_ADMISSION = "admission"
_REASON_OK = "isolatable"


@dataclass(frozen=True)
class IsolationDecision:
    """一次工具调用的隔离决策（shadow 模式发事件、测试断言用）。"""

    isolate: bool
    reason: str
    capability: str
    tool_name: str


def isolation_execution_enabled() -> bool:
    """开关是否打开——只有一个读取点（测试可 monkeypatch 本函数）。

    经 settings.get_bool 读取（登记 ENV_TABLE，门禁纪律）；值真才开、
    大小写不敏感（get_bool 口径）。
    """
    from agentnodes.foundation.config import settings as _settings

    return _settings.get_bool(ISOLATION_EXECUTION_ENV, False)


def should_isolate(
    tool: Any,
    registry: Any = None,
    *,
    enabled: bool | None = None,
    platform: str | None = None,
) -> IsolationDecision:
    """是否对本次工具调用走隔离分支（4a 决策探针，纯函数、零副作用）。

    判定顺序（先便宜后昂贵、先硬约束后软条件）：
    1. 开关关闭 → 不隔离（**唯一决策输入**，默认关）；
    2. 平台不支持（非 win32）→ 不隔离（POSIX 永不启用，禁启用保护不变）；
    3. capability 非 isolatable → 不隔离；
    4. 声明 isolatable 但准入不通过 → 不隔离（B2.1 回落语义：
       ``ToolRegistry.timeout_capability`` 已把准入折叠进返回值）。

    ``registry`` 给定时用 ``registry.timeout_capability(name)``（遵守
    B2.1 准入回落）；否则降级读裸 ``tool.timeout_capability``（测试 /
    合成工具场景）。**生产调用必须传 registry**——否则绕过准入。
    """
    name = getattr(tool, "name", "") or type(tool).__name__
    if enabled is None:
        enabled = isolation_execution_enabled()
    if not enabled:
        return IsolationDecision(False, _REASON_DISABLED, "", name)

    if platform is None:
        platform = sys.platform
    if platform != "win32":
        return IsolationDecision(False, _REASON_PLATFORM, "", name)

    # B2.1 纪律：声明 ≠ 事实；准入门槛折叠在 registry 里（回落 unstoppable）。
    if registry is not None:
        capability = registry.timeout_capability(name)
    else:
        capability = getattr(tool, "timeout_capability", "unstoppable")

    from agentnodes.kernel.agent.tools.core import TIMEOUT_CAPABILITY_ISOLATABLE

    if capability != TIMEOUT_CAPABILITY_ISOLATABLE:
        # 区分「声明就不是 isolatable」与「声明 isolatable 但准入拒绝回落」
        declared = getattr(tool, "timeout_capability", "")
        reason = (
            _REASON_ADMISSION
            if declared == TIMEOUT_CAPABILITY_ISOLATABLE
            else _REASON_CAPABILITY
        )
        return IsolationDecision(False, reason, capability, name)

    return IsolationDecision(True, _REASON_OK, capability, name)


def shadow_event(decision: IsolationDecision) -> dict[str, Any]:
    """4a shadow 模式事件体（只计算决策、发事件，不执行隔离）。"""
    return {
        "kind": "isolation.shadow_decision",
        "tool": decision.tool_name,
        "isolate": decision.isolate,
        "reason": decision.reason,
        "capability": decision.capability,
    }


__all__ = [
    "ISOLATION_EXECUTION_ENV",
    "IsolationDecision",
    "isolation_execution_enabled",
    "shadow_event",
    "should_isolate",
]
