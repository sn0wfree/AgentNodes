"""ToolGateway — single entry for tool schema + execution (A2).

Wraps a :class:`ToolRegistry` with a named toolset filter and optional
allow/deny overrides. AgentLoop / role_factory / chat_loop should talk
to the gateway rather than filtering registries ad hoc.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable

from .tools import ToolRegistry
from .toolsets import apply_toolset, resolve_toolset

logger = logging.getLogger(__name__)


class ToolGateway:
    """Filtered view over a ToolRegistry."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        toolset: str | Iterable[str] | None = None,
        allow: Iterable[str] | None = None,
        deny: Iterable[str] | None = None,
    ) -> None:
        self._source = registry
        base = apply_toolset(registry, toolset, deny=deny)
        if allow is not None:
            allow_set = {str(x) for x in allow}
            base = base.restricted(allow=sorted(allow_set))
        if deny is not None:
            deny_set = {str(x) for x in deny}
            base = base.restricted(deny=sorted(deny_set))
        self._registry = base
        self._toolset = toolset
        self._allow = list(allow) if allow is not None else None
        self._deny = list(deny) if deny is not None else None

    # ── LLM-facing surface ──────────────────────────────────────

    def definitions(self) -> list[dict[str, Any]]:
        """OpenAI function-calling schemas for the filtered tools."""
        return self._registry.get_definitions()

    @property
    def names(self) -> list[str]:
        return list(self._registry.tool_names)

    def __contains__(self, name: str) -> bool:
        return name in self._registry

    def __len__(self) -> int:
        return len(self._registry)

    # ── Execution ───────────────────────────────────────────────

    def execute(self, name: str, params: dict[str, Any]) -> str:
        """Execute a tool; always returns a JSON string envelope."""
        if name not in self._registry:
            return json.dumps({
                "status": "error",
                "error": f"Tool '{name}' not available in current toolset",
                "available": self.names[:30],
            }, ensure_ascii=False)
        return self._registry.execute(name, params)

    def get(self, name: str):
        return self._registry.get(name)

    # ── Introspection ───────────────────────────────────────────

    def describe(self) -> dict[str, Any]:
        return {
            "toolset": self._toolset if isinstance(self._toolset, str) else list(self._toolset or []),
            "allow": self._allow,
            "deny": self._deny,
            "tools": self.names,
        }


def build_gateway(
    workspace=None,  # noqa: ANN001
    *,
    toolset: str | Iterable[str] | None = None,
    allow: Iterable[str] | None = None,
    deny: Iterable[str] | None = None,
) -> ToolGateway:
    """Build the default registry and wrap it in a filtered gateway."""
    from strategy_research.kernel.agent.builtin_tools import build_default_registry

    registry = build_default_registry(workspace)
    return ToolGateway(registry, toolset=toolset, allow=allow, deny=deny)


def toolset_for_role(role: str | None) -> list[str] | None:
    """Role → toolset allow-list (falls back to minimal)."""
    return resolve_toolset(role or "minimal")


__all__ = [
    "ToolGateway",
    "build_gateway",
    "toolset_for_role",
]
