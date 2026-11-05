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

import os

#: 环境变量名（``1`` / ``true`` / ``yes`` 视为开启）。
ENV_ALLOW_SHELL = "AN_ALLOW_SHELL_TOOLS"

#: 这些模式下永不暴露 shell 工具（analysis-only，且单轮迭代）。
SHELL_BLOCKED_MODES = frozenset({"plan"})


def shell_tools_enabled(mode: str | None = None) -> bool:
    """Whether the ``run_command`` shell tool should be offered to the agent.

    Shell tools are opt-in: set ``AN_ALLOW_SHELL_TOOLS=1`` in the server
    environment. Plan mode never exposes shell tools (analysis-only, single
    iteration), regardless of the env var.

    :param mode: 当前对话模式（``"plan"`` / ``"build"`` / None）
    """
    if mode in SHELL_BLOCKED_MODES:
        return False
    return os.environ.get(ENV_ALLOW_SHELL, "").strip().lower() in (
        "1", "true", "yes",
    )


__all__ = ["ENV_ALLOW_SHELL", "SHELL_BLOCKED_MODES", "shell_tools_enabled"]
