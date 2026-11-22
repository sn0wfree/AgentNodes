# -*- coding: utf-8 -*-
"""会话 key 编解码单点（第四轮 C6）——kind 前缀 ↔ 物理 session id 的唯一约定。

**两个 id 命名空间，刻意不同**：

- **full session_key**（``chat:{X}`` / ``mcp:{X}`` / ``automation:{X}`` …）：
  mailbox 路由 / pause-resume / session_id_for / fold 恢复的键——
  ``chat:X`` 与 ``automation:X`` 是**不同 mailbox**；
- **fence domain = 物理 session id**（``X``）：串行域——chat 与 automation
  的活跃 run 同域互斥（R3），lease 域 / cancel scope / SSE
  ``business_session_id`` 都用它。

此前同一约定以三种拼法散在 session_controller / entry_point / gateway
（各带 docstring 承诺别处同步）——漂移会静默分裂 mailbox 或漏 fence
（并发 run 互斥失效）。本模块是**唯一事实源**：所有构造经
:func:`make_session_key`，所有剥离经 :func:`physical_session_id`。

文法（``<kind>:<name>`` 两侧非空）的提交期校验归
``orchestration.contracts.validate_work_request``——本模块只做编解码，
不做语义校验。owner 标签（``goal:<goal_id>`` / ``chat:<run_id>``，
lease 持有者观测标识）与 ``study:{sid}:round:{n}`` SSE 频道命名不属于
本模块的 mailbox session_key 命名空间。

落点（kernel/orchestration）：contracts 已拥有同款文法，且 gateway →
orchestration 是既有边——gateway 与 orchestration 双方零新边可达。
"""
from __future__ import annotations

__all__ = [
    "make_session_key",
    "physical_session_id",
]


def make_session_key(kind: str, session_id: str) -> str:
    """物理 session id → full session_key（``kind:id``）。

    编码侧单点：11 处历史 ``f"chat:{id}"`` / ``f"automation:{id}"`` 等内联
    构造全部收此。``kind`` 不含冒号（与 contracts 文法一致——kind 段必须
    是单段）；``session_id`` 原样嵌入（含冒号的物理 id 仍可 round-trip，
    因为 decode 只剥第一个 kind 段）。
    """
    return f"{kind}:{session_id}"


def physical_session_id(session_key: str) -> str:
    """full session_key → 物理 session id（fence/lease/串行域的键）。

    剥掉第一个 kind 段（maxsplit=1）：``chat:x`` / ``automation:x`` /
    ``goal:x`` 都得 ``x``；多冒号 ``a:b:c`` 得 ``b:c``；无前缀键原样返回
    （scheduler 直接持物理 id 的既有形态）。
    """
    return session_key.split(":", 1)[1] if ":" in session_key else session_key
