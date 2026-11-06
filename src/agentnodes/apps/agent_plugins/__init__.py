"""L2 插件集（业务内容）。

**分层**：本包是 **L2 内容层**——插件定义（agent 角色、工具清单、依赖拓扑）
属于业务域，不是平台原语。kernel 只保留**注册机制**（`AgentPluginRegistry`）
与**插件数据模型**（`AgentPlugin`），不持有任何具体插件。

**加载约定**：kernel 侧 ``get_default_registry()`` 不再预加载业务插件；
需要业务插件的调用方（CLI / workflow / swarm）显式 import 本包并调用
``populate(registry)``。这是 ADR-003 端口化在插件层的一致应用：
方向仍是 apps → kernel，kernel 不知道插件的存在。

**当前可用的插件集**：无。
``quant.py`` 的角色定义完整保留（第三步素材），但它们引用的 21 个工具
实现已随批次 4/5 删除，``available_plugins()`` 因此返回空——**宁可空，
也不加载调用必败的壳**。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentnodes.kernel.agent.plugin import AgentPlugin
    from agentnodes.kernel.agent.registry import AgentPluginRegistry

#: 本包内提供插件集的模块名（不含本 ``__init__``）。
#:
#: **为什么要显式列**：``scripts/arch/check_tool_refs.py`` 需要扫遍所有
#: 插件模块做悬空引用检查。用 ``pkgutil.iter_modules`` 自动发现也能做到，
#: 但那会把任何放错位置的临时文件也当成插件集扫——显式清单让「本包有
#: 哪几个插件集」成为可审计的事实，新增插件集时必须在这里登记。
__all_plugin_modules__: tuple[str, ...] = ("quant",)


def all_available_plugins() -> tuple["AgentPlugin", ...]:
    """返回当前环境下**工具齐备**的全部插件（跨插件集汇总）。

    这是 kernel 侧唯一该问的入口：它不关心插件来自哪个域，
    只关心「能不能跑」。
    """
    from .quant import available_plugins as quant_available

    out: list[AgentPlugin] = []
    for provider in (quant_available,):
        out.extend(provider())
    return tuple(out)


def populate(registry: "AgentPluginRegistry") -> int:
    """把可用插件注册进 ``registry``，返回注册数量。

    幂等：已存在的 id 直接覆盖（``AgentPluginRegistry.register`` 语义）。
    """
    plugins = all_available_plugins()
    for p in plugins:
        registry.register(p)
    return len(plugins)


__all__ = ["__all_plugin_modules__", "all_available_plugins", "populate"]
