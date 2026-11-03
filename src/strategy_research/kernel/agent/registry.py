"""AgentPluginRegistry — unified agent registry.

Single lookup point for every consumer (study runner, SwarmRuntime,
SubAgentTool, WorkflowRunner). Provides dependency closure completion
for AI-planner output fixing and selection validation.
"""
from __future__ import annotations

from collections.abc import Iterable

from .builtin_plugins import BUILTIN_PLUGINS
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
    """Module-level registry pre-loaded with BUILTIN_PLUGINS."""
    global _default_registry
    if _default_registry is None:
        _default_registry = AgentPluginRegistry(BUILTIN_PLUGINS)
    return _default_registry


__all__ = ["AgentPluginRegistry", "get_default_registry"]
