"""kernel.workflow —— DAG 执行/校验（包入口）。

**2026-11-13 包级副作用修正（重要）**：本文件原先在模块顶层
``from .types import AgentCall, AgentStatus, SwarmHook``，而 ``types`` 又
``from agentnodes.kernel.swarm.types import ...``——于是**任何**对
``agentnodes.kernel.workflow.*`` 的 import（哪怕只是取一个纯文本工具函数）
都会触发这条链：

    workflow.__init__ → types → swarm.__init__ → swarm.preset_loader
    → swarm.runtime → runtimes.__init__ → graph_engine
    → langgraph / langchain_core / uuid_utils（原生 DLL）

实测后果（跨平台 e2e，WSL）：
1. ``langgraph`` 是可选 extra（不在 ``dependencies``、不在 ``api``）——
   按文档装 ``[api]`` 的用户在 chat 第一条消息就炸 ``ModuleNotFoundError``；
2. 即使装了 langgraph，这条链还会拉起 ``uuid_utils`` 的原生 DLL，在
   Application Control 策略下被拦（``ImportError: DLL load failed``）。

**修法**：``types`` 的三个名字改为 **PEP 562 懒加载**（模块级 ``__getattr__``）
——包导入不再有副作用；只有真正取用 ``AgentCall`` / ``AgentStatus`` /
``SwarmHook`` 时才触发那条链。纯文本工具等无重依赖的模块（如
``orchestrate_guard``）从此可以安全地从包内 import。

详见 ``docs/design/langgraph-implicit-dep-fix-20261113.md``。
"""

from __future__ import annotations

from typing import Any

from agentnodes.kernel.workflow.agents import AgentExecutor, AgentRegistry
from agentnodes.kernel.workflow.dag import topological_layers, validate_dag
from agentnodes.kernel.workflow.executors import AgentLoopExecutor, CLIExecutor, PythonExecutor, StubExecutor
from agentnodes.kernel.workflow.validator import AgentValidator, ValidationResult

# ``types`` 的三个 re-export 改为懒加载（见模块 docstring）——**不要**改成
# 顶层 import：那会把 swarm/runtimes/langgraph/langchain_core/uuid_utils
# 整条重依赖链拉进任何 import workflow 的进程。
_LAZY_TYPES: frozenset[str] = frozenset({"AgentCall", "AgentStatus", "SwarmHook"})


def __getattr__(name: str) -> Any:
    """PEP 562：按需加载 ``types`` 的 re-export（零包级副作用）。"""
    if name in _LAZY_TYPES:
        from agentnodes.kernel.workflow import types as _types

        return getattr(_types, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | _LAZY_TYPES)


__all__ = [
    "AgentCall",
    "AgentExecutor",
    "AgentLoopExecutor",
    "AgentRegistry",
    "AgentStatus",
    "AgentValidator",
    "CLIExecutor",
    "PythonExecutor",
    "StubExecutor",
    "SwarmHook",
    "ValidationResult",
    "topological_layers",
    "validate_dag",
]
