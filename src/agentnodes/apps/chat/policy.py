# -*- coding: utf-8 -*-
"""对话运行期策略 —— 环境开关 + 模式门禁（产品决策，L2）。

**为什么在 L2**：「是否向用户暴露 shell 工具」是产品安全策略，不是平台
机制。平台侧（L3）只提供 ``run_command`` 工具本身与 ``allow_shell_tools``
开关位；**决定什么时候打开**属于业务侧。

**为什么不留在 L1**：L1 是哑适配器，不该读环境变量做安全决策。而且
CLI、API、MCP 三个渠道都要用同一套判据——放在某个 router 里会导致
其余渠道各写一份，这正是历史上「chat 走 gateway、CLI 走另一种判断」
这类不一致的来源。
"""
from __future__ import annotations

from dataclasses import dataclass

from agentnodes.foundation.config import settings as _settings

#: 环境变量名（``1`` / ``true`` / ``yes`` 视为开启）。
#:
#: **批次 E-2（2026-11-10）**：原为 ``AN_ALLOW_SHELL_TOOLS``（旧名）。
ENV_ALLOW_SHELL = "AGENTNODES_ALLOW_SHELL_TOOLS"

#: 这些模式下永不暴露 shell 工具（analysis-only，且单轮迭代）。
SHELL_BLOCKED_MODES = frozenset({"plan"})


def shell_tools_enabled(mode: str | None = None) -> bool:
    """Whether the ``run_command`` shell tool should be offered to the agent.

    Shell tools are opt-in: set ``AGENTNODES_ALLOW_SHELL_TOOLS=1`` in the
    server environment. Plan mode never exposes shell tools (analysis-only,
    single iteration), regardless of the env var.

    :param mode: 当前对话模式（``"plan"`` / ``"build"`` / None）
    """
    if mode in SHELL_BLOCKED_MODES:
        return False
    return _settings.get_str("AGENTNODES_ALLOW_SHELL_TOOLS").strip().lower() in (
        "1", "true", "yes",
    )


# ── ChatPolicy（顺序③，裁决 3：2026-11-18）──────────────────────────


@dataclass(frozen=True)
class ChatPolicy:
    """聊天产品策略（显式化）——apps/chat 拥有编排与产品策略（裁决 3）。

    聊天默认值由 apps/chat 显式声明；``kernel/agent/factory/chat_loop.py``
    逐步变成机械装配器（L3 不 import apps——L1/L2 调用方把本对象传给
    ``build_chat_agent_loop(policy=...)``，builder 按鸭子类型读取）。

    **先保行为再移默认值（裁决 3 原文）**：两条 chat 路径的既有默认值
    **互不一致**，本类把它们逐字段钉住，统一默认值是后续显式裁决：

    ================  ============  ========================  ==========
    字段              builder 默认  send_async legacy 默认     说明
    ================  ============  ========================  ==========
    max_iterations    1（单轮）     50（SessionService 队列）  路径语义不同
    allow_shell_tools True          False                     文档曾误写 False
    ================  ============  ========================  ==========

    drift-guard：``tests/test_chat_policy.py`` 用 ``inspect.signature``
    把上述对应关系钉住——任何一侧改默认值都会让测试红，迫使显式裁决。
    """

    max_iterations: int = 1
    allow_shell_tools: bool = True
    enable_goal_injection: bool = False
    enable_claim_validation: bool = False
    strict_claim_validation: bool = False

    @classmethod
    def for_builder(cls) -> "ChatPolicy":
        """``build_chat_agent_loop`` 签名默认对应的策略（单轮 chat）。"""
        return cls()

    @classmethod
    def for_send_async(cls) -> "ChatPolicy":
        """``SessionService.send_message`` legacy 队列路径对应的策略。"""
        return cls(max_iterations=50, allow_shell_tools=False)


__all__ = ["ChatPolicy", "ENV_ALLOW_SHELL", "SHELL_BLOCKED_MODES", "shell_tools_enabled"]
