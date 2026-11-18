# -*- coding: utf-8 -*-
"""任务签名口径策略（顺序⑤ S1，K4）——"本应用如何算签名"的显式注入点。

## 为什么把它从 TicketStore 里拆出来

``TicketStore`` 是通用平台机制（Ticket/Assignment 的持久化与状态机编排），
而"用哪套 role / tool_set 口径算任务签名"是**应用策略**（RSI/goal 场景）。
两者混在一处，导致：

- 通用存储被迫知道 RSI 的 env 名与工具链探测口径；
- 换一套签名口径必须改通用存储代码。

本模块承载**口径策略**（解析 role/tool_set + 计算签名），``TicketStore``
通过 ``signature_policy`` 参数接收它——**默认行为完全不变**。

## 必须保留的不变量：签名计算点仍在 store

``TicketStore._signature_for`` 的 docstring 已给出理由（防奖励黑客，H5）：

    签名是 holdout 的匹配键，决定了"哪些历史轨迹能进验证集"。若发起方
    可以自填，验证集就能被挑选。store 是唯一的准入/收尾入口，所以它是
    唯一合法的计算点。

因此本模块只提供"**怎么算**"（算法 + 口径解析），**不提供"算给谁"**——
计算位置仍在 store（唯一合法点）。策略可注入 ≠ 调用方可自填。

## 依赖方向

- 本模块只依赖 foundation（settings）与 stdlib；
- 默认实现 ``compute_task_signature`` 走 ``kernel.evolution.holdout``
  （同层 L3，合法）——但**延迟到调用时**才 import，避免 kernel.orchestration
  在 import 期拉起 kernel.evolution；
- 应用侧（interfaces/apps）可注入自己的 policy，无需改通用存储。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from agentnodes.foundation.config import settings as _settings

logger = logging.getLogger(__name__)

#: 任务签名口径的 env 覆盖。**为什么放在 kernel 而不是组装根**：
#: ``TicketStore`` 是签名的唯一计算点，口径必须与它同源；把这些名字写在
#: L1 会创造"两处都知道签名怎么算"的第二事实源。
_SIG_ROLE_ENV = "AGENTNODES_RSI_ROLE"
_SIG_TOOLSET_ENV = "AGENTNODES_RSI_TOOLSET"

#: 解析不到任何工具集时的最小口径。**刻意很窄**：一个确定性的窄空间好过
#: 一个随运行时漂移的宽空间——后者会让"同一签名"逐渐收集到语义不同的
#: 轨迹，holdout 从"同任务历史"退化成"随便一批历史"。
_FALLBACK_ROLE = "chat"
_FALLBACK_TOOLSET: tuple[str, ...] = ("read",)

#: 解析缓存：``(role_env, toolset_env) -> scope``。
#:
#: ``build_default_registry()`` 首次调用约 200ms（要 import 全量工具模块）。
#: 装配根在同一个进程里可能多次构造 TicketStore（测试尤其如此），重复付
#: 这笔钱还带来一个隐蔽风险：两次构造拿到**不同的**工具集 → 同一份历史
#: 被算成两种签名。按 env 键缓存，既省时间又保证同进程内口径一致。
_SCOPE_CACHE: dict[tuple[str, str], tuple[str, tuple[str, ...]]] = {}


def _tool_names_from(raw: str) -> tuple[str, ...]:
    """``"read, write"`` → ``("read", "write")``（去空、排序、小写）。"""
    return tuple(sorted({t.strip().lower() for t in raw.split(",") if t.strip()}))


def _registry_kinds(registry: Any) -> list[str]:
    """尽力读出工具名列表（不同实现暴露不同名字）。

    已知形态：
    - ``CapabilityRegistry.list_kinds()`` / ``.kinds``（阶段二注册表）
    - ``ToolRegistry.tool_names()``（``build_default_registry`` 的真实返回）
    """
    for attr in ("list_kinds", "kinds", "registered_kinds", "tool_names"):
        candidate = getattr(registry, attr, None)
        if candidate is None:
            continue
        try:
            value = candidate() if callable(candidate) else candidate
            names = [str(v) for v in (value or ())]
            if names:
                return names
        except Exception:  # noqa: BLE001 —— 换下一个候选
            continue
    return []


def resolve_signature_scope() -> tuple[str, tuple[str, ...]]:
    """推出生效的 ``(role, tool_set)`` 签名口径（确定性、有缓存）。

    解析顺序：
    1. ``AGENTNODES_RSI_ROLE`` / ``AGENTNODES_RSI_TOOLSET``（逗号分隔）
       —— 运维可钉住，测试可隔离。
    2. 默认工具链的实际注册 kind（``build_default_registry``）—— 生产口径
       与真实可用的工具一致。
    3. ``("chat", ("read",))`` —— 工具链不可用时的最小可用口径。

    任一步的异常都被吞掉并继续下沉：签名是"证据归类"的辅助字段，不该
    因为工具链装配失败就把 turn 打挂。
    """
    role = _settings.get_str("AGENTNODES_RSI_ROLE").strip()
    raw_tools = _settings.get_str("AGENTNODES_RSI_TOOLSET").strip()
    cache_key = (role, raw_tools)
    cached = _SCOPE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    if raw_tools:
        resolved = (role or _FALLBACK_ROLE, _tool_names_from(raw_tools))
        _SCOPE_CACHE[cache_key] = resolved
        return resolved

    try:
        from agentnodes.kernel.agent.builtin_tools import build_default_registry

        registry = build_default_registry()
        names = _registry_kinds(registry)
        if names:
            resolved = (
                role or _FALLBACK_ROLE,
                tuple(sorted({n.split(".")[-1] for n in names if n})),
            )
            _SCOPE_CACHE[cache_key] = resolved
            return resolved
    except Exception:  # noqa: BLE001 —— 工具链不可用，退回最小口径
        logger.debug("signature tool_set resolution failed", exc_info=True)

    resolved = (role or _FALLBACK_ROLE, _FALLBACK_TOOLSET)
    _SCOPE_CACHE[cache_key] = resolved
    return resolved


def default_compute_signature(
    objective: str, role: str, tool_set: tuple[str, ...],
) -> str:
    """默认签名计算：``kernel.evolution.holdout.compute_task_signature``。

    延迟 import（模块级 import 会让 orchestration 在 import 期拉起
    evolution）。失败返回空串并记 debug——签名缺失只会让 holdout 抽不到
    样本，不会阻断 turn。
    """
    try:
        from agentnodes.kernel.evolution.holdout import compute_task_signature

        return compute_task_signature(objective, role, tool_set)
    except Exception:  # noqa: BLE001 —— 签名算不出不阻断准入/收尾
        logger.debug("task signature computation failed", exc_info=True)
        return ""


@dataclass(frozen=True)
class TaskSignaturePolicy:
    """任务签名口径策略（role + tool_set + 计算实现）。

    :param role: 签名口径里的 role。
    :param tool_set: 签名口径里的 tool_set（已排序）。
    :param compute: ``(objective, role, tool_set) -> signature``；缺省用
        :func:`default_compute_signature`（evolution.holdout 实现）。

    **注**：注入的是"怎么算"，不是"算给谁"——计算点仍在
    ``TicketStore``（防奖励黑客不变量，见本模块 docstring）。
    """

    role: str
    tool_set: tuple[str, ...]
    compute: Callable[[str, str, tuple[str, ...]], str] = default_compute_signature

    def compute_signature(self, objective: str) -> str:
        """按本策略计算 objective 的任务签名（失败由 compute 自行处理）。"""
        return self.compute(objective, self.role, self.tool_set)

    @classmethod
    def from_scope(cls) -> "TaskSignaturePolicy":
        """按当前进程口径解析（env → 默认工具链 → 最小口径）。"""
        role, tool_set = resolve_signature_scope()
        return cls(role=role, tool_set=tool_set)


__all__ = [
    "TaskSignaturePolicy",
    "default_compute_signature",
    "resolve_signature_scope",
]
