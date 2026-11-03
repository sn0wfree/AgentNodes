"""Profile → LoopStrategy resolution (P1-5).

A "profile" in this codebase is the parameter bundle used to construct
an ``AgentLoop``: the role's tool whitelist, the system prompt, the
LLM config, etc. Profiles are typically built from YAML configs
(see ``docs/toolset-convention.md``) but can also be plain dicts.

This module adds the ``loop_strategy`` field to the profile shape —
a separate concern from the role/registry/prompt. The field accepts
multiple spec shapes so YAML authors don't have to memorise one form:

- ``None`` / missing → ``ReActStrategyFactory.create()``
- string ``"explorer"`` → ``create_strategy("explorer")``
- string ``"minimal"`` → ``create_strategy("minimal")``
- dict ``{"name": "explorer", "config": {"max_iterations": 100}}`` →
  ``create_strategy("explorer", config=LoopConfig(**cfg_dict))``
- ``LoopStrategy`` instance → returned as-is (caller pre-built)

The resolver is a pure function: no AgentLoop / no DB / no LLM
client — only the strategy registry and the ``LoopConfig`` dataclass.
This keeps the unit-test surface small and avoids any implicit
singleton state.

P1-5 v0.1 scope: helper + integration with ``AgentLoop.__init__``.
The actual rewrite of ``AgentLoop._run_loop_core`` to drive the
strategy is L7 (post v0.1).
"""

from __future__ import annotations

from typing import Any

from .factory import create_strategy
from .loop_strategy import LoopConfig, LoopStrategy

__all__ = ["resolve_loop_strategy"]


def _build_config_from_dict(cfg_dict: dict[str, Any]) -> LoopConfig:
    """Build a ``LoopConfig`` from a dict of kwargs (ignores unknowns)."""
    fields = {f for f in LoopConfig.__dataclass_fields__}  # type: ignore[attr-defined]
    return LoopConfig(**{k: v for k, v in cfg_dict.items() if k in fields})


def resolve_loop_strategy(spec: Any) -> LoopStrategy:
    """Build / return a ``LoopStrategy`` from a profile spec.

    Accepts:
    - ``None`` / missing → default ``react`` strategy.
    - ``str`` → ``create_strategy(name)``.
    - ``dict`` → ``create_strategy(spec["name"], config=_build_config_from_dict(spec["config"]))``
      when ``"config"`` is present, else just ``create_strategy(spec["name"])``.
    - ``LoopStrategy`` instance → returned unchanged.
    - Anything else raises ``ValueError`` with a helpful message.
    """
    if spec is None:
        return create_strategy("react")
    if isinstance(spec, LoopStrategy):
        return spec
    if isinstance(spec, str):
        return create_strategy(spec)
    if isinstance(spec, dict):
        name = spec.get("name", "react")
        if not isinstance(name, str):
            raise ValueError(
                f"loop_strategy dict 'name' must be a string, got {type(name).__name__}"
            )
        cfg_dict = spec.get("config")
        if cfg_dict is None:
            return create_strategy(name)
        if not isinstance(cfg_dict, dict):
            raise ValueError(
                f"loop_strategy 'config' must be a dict, got {type(cfg_dict).__name__}"
            )
        return create_strategy(name, config=_build_config_from_dict(cfg_dict))
    raise ValueError(
        f"loop_strategy spec must be None / str / dict / LoopStrategy; "
        f"got {type(spec).__name__}"
    )
