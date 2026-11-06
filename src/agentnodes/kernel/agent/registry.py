"""AgentPluginRegistry — unified agent registry.

Single lookup point for every consumer (study runner, SwarmRuntime,
SubAgentTool, WorkflowRunner). Provides dependency closure completion
for AI-planner output fixing and selection validation.

**不持有任何具体插件**（2026-11-06 批次 4 · 三步走第一步）：插件定义是
业务内容（L2），本模块只是**注册机制**（L3）。默认注册表**为空**——
需要业务插件的调用方显式地 ``import agentnodes.apps.agent_plugins``
并调用 ``populate(registry)``。

理由：kernel 里写死 ``BUILTIN_PLUGINS`` 会让「平台」和「某个业务领域的
角色定义」绑死。量化域的角色（researcher / factor_analyst / strategist）
住在 kernel，换成别的业务域就得改 kernel——那不是平台。
"""
from __future__ import annotations

from collections.abc import Iterable

from .plugin import AgentPlugin


class AgentPluginRegistry:
    def __init__(self, plugins: Iterable[AgentPlugin] = ()):
        self._plugins: dict[str, AgentPlugin] = {}
        for p in plugins:
            self.register(p)

    def register(self, plugin: AgentPlugin) -> None:
        self._plugins[plugin.id] = plugin

    def get(self, plugin_id: str) -> AgentPlugin | None:
        return self._plugins.get(plugin_id)

    def has(self, plugin_id: str) -> bool:
        return plugin_id in self._plugins

    def list_plugins(self) -> list[AgentPlugin]:
        return sorted(self._plugins.values(), key=lambda p: p.id)

    def list_ids(self) -> list[str]:
        return sorted(self._plugins.keys())

    def by_category(self, category: str) -> list[AgentPlugin]:
        return [p for p in self.list_plugins() if p.category == category]

    def __len__(self) -> int:
        return len(self._plugins)

    def __contains__(self, plugin_id: object) -> bool:
        return plugin_id in self._plugins

    # ── Selection helpers ───────────────────────────────────────

    def complete_dependencies(
        self, selected: Iterable[str],
    ) -> list[str]:
        """Return ``selected`` plus the closure of hard ``requires``.

        Order is topologically valid (upstream first); unknown ids are
        dropped (use :meth:`validate_selection` to detect them).
        """
        selected_set = {s for s in selected if self.has(s)}
        visited: set[str] = set()
        order: list[str] = []

        def visit(pid: str) -> None:
            if pid in visited or not self.has(pid):
                return
            visited.add(pid)
            for dep in self._plugins[pid].requires:
                visit(dep)
            order.append(pid)

        for pid in sorted(selected_set):
            visit(pid)
        return order

    def validate_selection(self, selected: Iterable[str]) -> list[str]:
        """Return a list of error strings (empty == OK).

        Errors: unknown ids; missing required (``optional=False``)
        plugins. Hard dependencies are auto-completed by
        :meth:`complete_dependencies`, so their absence is not an error
        here — the caller should use the completed list.
        """
        errors: list[str] = []
        sel = list(selected)
        for pid in sel:
            if not self.has(pid):
                errors.append(f"unknown plugin id: {pid!r}")

        known = [pid for pid in sel if self.has(pid)]
        completed = set(self.complete_dependencies(known))
        for plugin in self.list_plugins():
            if not plugin.optional and plugin.id not in completed:
                errors.append(f"required plugin {plugin.id!r} not selected")
        return errors


_default_registry: AgentPluginRegistry | None = None


def get_default_registry() -> AgentPluginRegistry:
    """Module-level registry.

    **默认无插件**（第 4 批次起）：插件是 L2 内容，由调用方显式填充。
    需要业务插件时先 ``import agentnodes.apps.agent_plugins`` 再
    ``agent_plugins.populate(get_default_registry())``。

    ``reset_default_registry()`` 后重新拿到的仍是空表——这是有意的：
    空表是**基线状态**，不是「漏加载」。IDE/IDE 自动补全、纯 LLM 对话、
    工具调试都不需要业务插件。
    """
    global _default_registry
    if _default_registry is None:
        _default_registry = AgentPluginRegistry()
    return _default_registry


def reset_default_registry() -> None:
    """丢弃默认注册表（测试用）。下一次 ``get_default_registry()`` 重建空表。"""
    global _default_registry
    _default_registry = None


__all__ = [
    "AgentPluginRegistry",
    "get_default_registry",
    "reset_default_registry",
]
