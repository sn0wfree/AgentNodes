"""兼容壳（2026-11-13）：实现已下沉到 ``kernel/agent/text_guard.py``。

**为什么下沉**：chat 主路径（``service.py::_run_with_guard``）只需要三个纯文本
函数，却因本模块在 ``kernel.workflow`` 包内而触发整条重依赖链
（``workflow.__init__`` → ``types`` → ``swarm`` → ``runtimes`` →
``langgraph`` / ``langchain_core`` / ``uuid_utils``）。实测后果：按文档装
``[api]`` 的用户在 chat 第一条消息就炸 ``ModuleNotFoundError``。详见
``docs/design/langgraph-implicit-dep-fix-20261113.md``。

**包级副作用已同时修掉**（``workflow/__init__.py`` 的 ``types`` 改为 PEP 562
懒加载），所以本文件现在可以安全地做纯 re-export——从本模块 import 不再
拉起重依赖。

**调用方应改用** ``from agentnodes.kernel.agent.text_guard import ...``。
本文件择期删除（随 U6 之后的清理批，需满足 R-015 四项）。

---

原始说明（历史保留）：Continuation guard for DAG-orchestrator chat sessions.
"""

from __future__ import annotations

from agentnodes.kernel.agent.text_guard import (  # noqa: F401
    DEFAULT_MAX_CONTINUES,
    continue_instruction,
    looks_like_question,
    max_continues,
)
from agentnodes.kernel.agent.text_guard import __all__ as __all__  # noqa: F401
