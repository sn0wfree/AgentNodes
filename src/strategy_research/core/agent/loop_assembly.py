"""Shared AgentLoop assembly core (registry + prompt + strategy).

Phase 3 of docs/phase3-dual-factory-design.md: both
``role_factory.build_agent_loop`` and ``chat_loop.build_chat_agent_loop``
hand-assembled these three pieces — the real drift source between the
two factories. ``assemble_loop_parts`` is the single source; the two
factory signatures stay unchanged (they differ in research vs chat
semantics and are not being merged).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass
class LoopAssembly:
    """Resolved pieces shared by both AgentLoop factories."""

    registry: Any
    system_prompt: Optional[str]
    loop_strategy: Any = None


def assemble_loop_parts(
    *,
    role: str,
    registry: Any,
    extra_context: dict[str, Any] | None = None,
    system_prompt_override: str | None = None,
    workspace: Path | None = None,
    loop_strategy: Any | None = None,
) -> LoopAssembly:
    """Single source for registry defaulting + prompt rendering + strategy resolve.

    - ``registry``: when None, falls back to ``build_default_registry()``
      (safe to None if that import fails — chat callers pass explicit
      registries for the DAG-orchestrator mode).
    - ``system_prompt``: ``system_prompt_override`` wins; otherwise
      ``PromptBuilderFactory.get(role)`` renders with the merged context
      (``workspace``/``tool_list`` placeholders are filled when the
      caller passes them via ``extra_context`` or a live registry).
    - ``loop_strategy``: ``resolve_loop_strategy`` turns a spec
      (str / dict / LoopStrategy / None) into a resolved strategy.
    """
    from .builtin_tools import build_default_registry
    from .prompt_builder import PromptBuilderFactory

    if registry is None:
        try:
            registry = build_default_registry()
        except Exception:  # noqa: BLE001
            registry = None

    if system_prompt_override is not None:
        system_prompt: str | None = system_prompt_override
    else:
        ctx = dict(extra_context or {})
        if workspace is not None:
            ctx.setdefault("workspace", str(workspace))
        else:
            ctx.setdefault("workspace", "")
        if "tool_list" not in ctx and registry is not None:
            try:
                tool_names = sorted(registry._tools.keys())
                ctx["tool_list"] = "\n".join(f"- {n}" for n in tool_names)
            except Exception:  # noqa: BLE001
                ctx["tool_list"] = ""
        system_prompt = PromptBuilderFactory.get(role).build_system_prompt(role, ctx)

    resolved_strategy: Any = None
    if loop_strategy is not None:
        from strategy_research.kernel.agent.strategy.profile_resolver import resolve_loop_strategy

        resolved_strategy = resolve_loop_strategy(loop_strategy)

    return LoopAssembly(
        registry=registry,
        system_prompt=system_prompt,
        loop_strategy=resolved_strategy,
    )
