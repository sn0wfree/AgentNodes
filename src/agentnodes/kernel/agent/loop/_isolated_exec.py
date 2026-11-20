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

#: 灰度工具 allowlist（逗号分隔，默认空 = 不启用任何工具——B2.4 口径：
#: 即使开关打开也不放行真实工具，留给 B2.5 试点单独裁决）。
ISOLATION_TOOLS_ENV = "AGENTNODES_ISOLATION_TOOLS"

# 决策原因（供 shadow 事件/测试断言，稳定词表）
_REASON_DISABLED = "disabled"
_REASON_PLATFORM = "unsupported_platform"
_REASON_CAPABILITY = "capability"
_REASON_ADMISSION = "admission"
_REASON_NOT_ALLOWLISTED = "not_allowlisted"
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


def isolation_tool_allowlist() -> frozenset[str]:
    """灰度 allowlist（默认空 = 不启用任何工具，B2.4 口径）。

    逗号分隔，空白去除；经 settings.get_str 读取（门禁纪律）。
    """
    from agentnodes.foundation.config import settings as _settings

    raw = _settings.get_str(ISOLATION_TOOLS_ENV, "") or ""
    return frozenset(x.strip() for x in raw.split(",") if x.strip())


def validate_isolation_config() -> None:
    """启动期配置校验（4d）：强制开启但平台不支持时显式报错，不静默降级。

    开关默认关时永远通过（零生产影响）。开关开但非 win32 → 抛
    ``RuntimeError``（"不得静默降级为 in-process 并声称隔离成功"）。
    """
    if not isolation_execution_enabled():
        return
    if sys.platform != "win32":
        raise RuntimeError(
            "AGENTNODES_ISOLATION_EXECUTION=1 but platform is "
            f"{sys.platform!r}: isolation is Windows-only; refusing to "
            "silently fall back to in-process execution"
        )


def should_isolate(
    tool: Any,
    registry: Any = None,
    *,
    enabled: bool | None = None,
    platform: str | None = None,
    allowlist: frozenset[str] | None = None,
) -> IsolationDecision:
    """是否对本次工具调用走隔离分支（4a 决策探针，纯函数、零副作用）。

    判定顺序（先便宜后昂贵、先硬约束后软条件）：
    1. 开关关闭 → 不隔离（**唯一决策输入**，默认关）；
    2. 平台不支持（非 win32）→ 不隔离（POSIX 永不启用，禁启用保护不变）；
    3. capability 非 isolatable → 不隔离；
    4. 声明 isolatable 但准入不通过 → 不隔离（B2.1 回落语义：
       ``ToolRegistry.timeout_capability`` 已把准入折叠进返回值）；
    5. 工具名不在 allowlist → 不隔离（4d 灰度；默认空 = 不启用任何工具）。

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

    if allowlist is None:
        allowlist = isolation_tool_allowlist()
    if name not in allowlist:
        return IsolationDecision(False, _REASON_NOT_ALLOWLISTED, capability, name)

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


@dataclass(frozen=True)
class IsolatedOutcome:
    """隔离执行的结构化结果（供 loop 映射 E5 outcome / tool_result）。

    ``payload`` 是工具原始输出字符串（与 in-process 路径同形）；
    ``actual_state`` 是实际观测（四值），**不由声明推断**；
    ``terminated/unknown`` 表明已执行但结果被拒收——loop 不得重试。
    """

    payload: str
    actual_state: str
    status: str
    call_id: str
    discarded_rows: int
    timed_out: bool

    @property
    def result_rejected(self) -> bool:
        """结果是否被拒收（截止后 returned / terminated / unknown）。"""
        return self.actual_state in ("returned", "unknown") or self.status == "terminated"


def run_isolated(
    tool_name: str,
    tool_args: dict[str, Any],
    *,
    remaining_budget_s: float | None,
    approval_decider: Any = None,
    ctx: Any = None,
) -> IsolatedOutcome:
    """在隔离 worker 内执行一次工具调用（**同步阻塞核心**，4c）。

    :param tool_args: 工具参数（``tc.arguments``，**不含** ctx/内部注入键）。
    :param remaining_budget_s: 剩余 attempt 预算（``max(0, deadline-now)``）；
        ``None`` = 无 deadline（不推荐——用 seam 默认 30s）。
    :param approval_decider: 父进程 ASK 裁决 callable（闭包捕获本调用的
        tool/args/ctx；未知裁决 fail-closed）——经 ApprovalProxy 注入。
    :param ctx: 工具上下文（ToolContext）——经 codec 编码，回调不入 wire。

    **纪律**（方案 §2.3/§2.5）：每调用独占 transient worker（默认，不复用
    句柄→无并发错配）；blocking 实现由调用方包 ``asyncio.to_thread``；
    确认先于返回（seam 内 kill_and_confirm 完成后才返回）；本函数**不**
    自动重试（terminated/unknown 不得重试——不使工具幂等）。
    """
    import json

    from agentnodes.kernel.agent.tools.isolation import (
        ApprovalProxy,
        isolate_tool_call,
    )

    timeout = remaining_budget_s if remaining_budget_s is not None else 30.0
    approval = ApprovalProxy(approval_decider) if approval_decider is not None else None
    result = isolate_tool_call(
        tool_name,
        dict(tool_args),
        ctx=ctx,
        approval=approval,
        timeout_s=timeout,
        # worker=None → transient（每次独占，安全优先；复用模式 4d 另批）
    )
    # 工具输出：result.payload["output"]（与 worker 回传结构一致）
    output = result.payload.get("output")
    if output is None:
        output = json.dumps(result.payload, ensure_ascii=False, default=str)
    return IsolatedOutcome(
        payload=output if isinstance(output, str) else json.dumps(
            output, ensure_ascii=False, default=str,
        ),
        actual_state=result.actual_state,
        status=result.status,
        call_id=result.call_id,
        discarded_rows=result.discarded_rows,
        timed_out=result.timed_out,
    )


async def arun_isolated(
    tool_name: str,
    tool_args: dict[str, Any],
    **kw: Any,
) -> IsolatedOutcome:
    """async 包装：blocking 隔离在线程里跑（不阻塞事件循环/不互饿）。

    上层若用 ``asyncio.wait_for`` 只作返回保护——不能替代 seam 内的
    kill+确认（4c 的 deadline 顺序由 ``remaining_budget_s`` + seam 保证）。
    """
    import asyncio

    return await asyncio.to_thread(run_isolated, tool_name, tool_args, **kw)


__all__ = [
    "ISOLATION_EXECUTION_ENV",
    "ISOLATION_TOOLS_ENV",
    "IsolatedOutcome",
    "IsolationDecision",
    "arun_isolated",
    "isolation_execution_enabled",
    "isolation_tool_allowlist",
    "run_isolated",
    "shadow_event",
    "should_isolate",
    "validate_isolation_config",
]
