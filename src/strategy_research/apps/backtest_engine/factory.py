"""BacktestEngine factory (P0-3).

Mirrors the LLM provider / DataStore registry pattern: a default
provider is registered at import time; ``register(name, cls)`` adds
new providers without touching core; ``get_engine(name=None)``
returns an instance.

P0-3 ships one adapter (``strategy_engine``) wired by default; the
callback-engine adapter is also wired so the standard import works
across both legacy paths.
"""

from __future__ import annotations

from typing import Optional

from .protocol import BacktestEngine

__all__ = ["BacktestEngineRegistry", "register_engine", "get_engine"]

_REGISTRY: dict[str, type[BacktestEngine]] = {}
_INSTANCES: dict[str, BacktestEngine] = {}


class BacktestEngineRegistry:
    """Holder for the process-global registry."""

    @classmethod
    def register(cls, name: str, provider_cls: type[BacktestEngine]) -> None:
        _REGISTRY[name] = provider_cls
        _INSTANCES.pop(name, None)  # invalidate cached instance

    @classmethod
    def unregister(cls, name: str) -> None:
        _REGISTRY.pop(name, None)
        _INSTANCES.pop(name, None)

    @classmethod
    def available(cls) -> list[str]:
        return list(_REGISTRY.keys())


def register_engine(name: str, provider_cls: type[BacktestEngine]) -> None:
    """Module-level convenience for ``BacktestEngineRegistry.register``."""
    BacktestEngineRegistry.register(name, provider_cls)


def get_engine(name: Optional[str] = None) -> BacktestEngine:
    """Return a BacktestEngine instance.

    - ``name=None`` (default) returns ``"strategy"`` (the YAML-path
      default; matches the production wiring via ``config_runner``).
    - ``name="strategy"`` / ``"callback"`` look up the registry.
    - Unknown names raise ``KeyError`` with the available list.
    """
    if name is None:
        name = "strategy"
    if name in _INSTANCES:
        return _INSTANCES[name]
    if name not in _REGISTRY:
        raise KeyError(
            f"unknown BacktestEngine provider {name!r}; "
            f"available: {sorted(_REGISTRY.keys())}"
        )
    instance = _REGISTRY[name]()
    _INSTANCES[name] = instance
    return instance


# ── Default registration at import time ──────────────────────────
# Delayed to avoid circular import: adapters import this module to
# call ``register_engine`` from their own __init__.
def _register_defaults() -> None:
    from .callback_engine_adapter import CallbackEngineAdapter
    from .strategy_engine_adapter import StrategyEngineAdapter

    register_engine("strategy", StrategyEngineAdapter)
    register_engine("callback", CallbackEngineAdapter)


_register_defaults()
